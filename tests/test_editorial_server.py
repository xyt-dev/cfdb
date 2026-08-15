import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from content_cache import ContentStatus, ContentStore  # pyright: ignore[reportMissingImports]
from content_codecs import EDITORIAL_CODEC  # pyright: ignore[reportMissingImports]
from editorial_model import EditorialDocument, Node
import server


CHECKED_ABSENCE = {
    "successfulCheckTimestamps": [
        "2026-08-14T10:00:00Z",
        "2026-08-14T10:00:10Z",
    ],
    "contestPageReceipts": [
        {
            "fetchedAt": "2026-08-14T10:00:00Z",
            "recognized": True,
            "editorialFound": False,
            "tutorialFound": False,
        },
        {
            "fetchedAt": "2026-08-14T10:00:10Z",
            "recognized": True,
            "editorialFound": False,
            "tutorialFound": False,
        },
    ],
}


def make_document(contest_id: str = "1700") -> EditorialDocument:
    return EditorialDocument(
        contest_id=contest_id,
        source_url=f"https://codeforces.com/blog/entry/{contest_id}",
        root=Node(
            kind="document",
            children=[
                Node(
                    kind="paragraph",
                    children=[
                        Node(kind="text", text="<safe>"),
                        Node(
                            kind="link",
                            attrs={"href": "javascript:alert(1)"},
                            children=[Node(kind="text", text="click")],
                        ),
                    ],
                )
            ],
        ),
    )


class EditorialServerTests(unittest.TestCase):
    def publish(self, root: Path, document: EditorialDocument) -> ContentStore:
        store = ContentStore.initialize(root, EDITORIAL_CODEC)
        store.publish(document)
        return store

    def request(
        self,
        path: str,
        *,
        editorial_root: Path | None = None,
        headers: dict[str, str] | None = None,
    ):
        import http.server

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{httpd.server_address[1]}{path}"
            request = Request(url, headers=headers or {})
            root = editorial_root or server.EDITORIAL_V2_ROOT
            with patch.object(server, "EDITORIAL_V2_ROOT", root):
                try:
                    with urlopen(request, timeout=5) as response:
                        return response.status, dict(response.headers), response.read()
                except HTTPError as error:
                    try:
                        return error.code, dict(error.headers), error.read()
                    finally:
                        error.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_published_editorial_is_immediately_available_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            self.publish(root, make_document())
            expected = {
                "format": "html",
                "contentKind": "editorial",
                "schema": 2,
                "html": "<p>&lt;safe&gt;click</p>",
                "url": "https://codeforces.com/blog/entry/1700",
                "known": True,
                "status": "ready",
            }

            self.assertEqual(server.build_editorial_payload("1700", cache_root=root), expected)
            status, headers, body = self.request(
                "/api/editorial?contestId=1700",
                editorial_root=root,
            )

            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(json.loads(body), expected)

    def test_missing_editorial_is_pending_without_initializing_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            expected = {
                "format": None,
                "contentKind": "editorial",
                "html": None,
                "status": "pending",
                "known": False,
                "error": "editorial has not been crawled yet",
            }

            self.assertEqual(server.build_editorial_payload("1700", cache_root=root), expected)
            status, headers, body = self.request(
                "/api/editorial?contestId=1700",
                editorial_root=root,
            )

            self.assertEqual(status, 202)
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(json.loads(body), expected)
            self.assertFalse(root.exists())

    def test_known_absent_editorial_has_null_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            store = ContentStore.initialize(root, EDITORIAL_CODEC)
            store.record_status(
                "9999",
                ContentStatus.KNOWN_ABSENT,
                evidence=CHECKED_ABSENCE,
            )
            expected = {
                "format": None,
                "contentKind": "editorial",
                "html": None,
                "status": "known_absent",
                "known": True,
            }

            self.assertEqual(server.build_editorial_payload("9999", cache_root=root), expected)
            status, _, body = self.request(
                "/api/editorial?contestId=9999",
                editorial_root=root,
            )

            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), expected)

    def test_malformed_document_fails_closed_without_request_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            store = self.publish(root, make_document())
            document_path = store.document_path("1700")
            value = json.loads(document_path.read_text(encoding="utf-8"))
            value["document"]["children"][0]["children"][0]["text"] = 42
            document_path.write_text(
                json.dumps(value, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            before = document_path.read_bytes()

            payload = server.build_editorial_payload("1700", cache_root=root)
            status, _, body = self.request(
                "/api/editorial?contestId=1700",
                editorial_root=root,
            )

            self.assertEqual(payload["status"], "invalid_structure")
            self.assertEqual(status, 500)
            self.assertEqual(json.loads(body)["status"], "invalid_structure")
            self.assertEqual(document_path.read_bytes(), before)

    def test_missing_contest_never_falls_back_to_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "v2"
            self.publish(root, make_document("1700"))
            (base / "2000.md").write_text("legacy must not leak", encoding="utf-8")

            payload = server.build_editorial_payload("2000", cache_root=root)

            self.assertEqual(payload["status"], "pending")
            self.assertNotIn("legacy", json.dumps(payload))

    def test_payload_reads_do_not_change_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            store = self.publish(root, make_document())
            store.record_status(
                "9999",
                ContentStatus.KNOWN_ABSENT,
                evidence=CHECKED_ABSENCE,
            )

            def snapshot() -> dict[str, tuple[int, int]]:
                return {
                    path.relative_to(root).as_posix(): (
                        path.stat().st_size,
                        path.stat().st_mtime_ns,
                    )
                    for path in root.rglob("*")
                    if path.is_file()
                }

            before = snapshot()
            server.build_editorial_payload("1700", cache_root=root)
            server.build_editorial_payload("9999", cache_root=root)

            self.assertEqual(snapshot(), before)

    def test_invalid_contest_reference_returns_400(self):
        status, headers, body = self.request("/api/editorial?contestId=../1700")

        self.assertEqual(status, 400)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body)["status"], "invalid_ref")

    def test_reader_payload_static_route_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = b"window.CFDBReaderPayload = {};\n"
            (root / "reader_payload.js").write_bytes(source)
            with patch.object(server, "ROOT", str(root)):
                status, headers, body = self.request("/reader_payload.js")
                nested_status, _, _ = self.request("/nested/reader_payload.js")

            self.assertEqual(status, 200)
            self.assertEqual(
                headers["Content-Type"],
                "application/javascript; charset=utf-8",
            )
            self.assertEqual(body, source)
            self.assertEqual(nested_status, 404)

    def test_opaque_origin_font_response_has_narrow_cors_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vendor = root / "vendor"
            vendor.mkdir()
            font = b"local-font-bytes"
            (vendor / "ReaderFont.otf").write_bytes(font)
            with patch.object(server, "ROOT", str(root)):
                status, headers, body = self.request(
                    "/vendor/ReaderFont.otf",
                    headers={"Origin": "null"},
                )
                api_status, api_headers, _ = self.request(
                    "/api/editorial?contestId=../1700",
                    headers={"Origin": "null"},
                )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "font/otf")
        self.assertEqual(headers["Access-Control-Allow-Origin"], "null")
        self.assertEqual(body, font)
        self.assertEqual(api_status, 400)
        self.assertEqual(api_headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", api_headers)

    def test_editorial_asset_route_serves_direct_digest_validated_image(self):
        payload = b"\x89PNG\r\n\x1a\neditorial-server-fixture"
        digest = hashlib.sha256(payload).hexdigest()
        pdf_payload = b"%PDF-1.7\neditorial-pdf-must-not-serve"
        pdf_digest = hashlib.sha256(pdf_payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            store = ContentStore.initialize(root, EDITORIAL_CODEC)
            (store.assets_path / f"{digest}.png").write_bytes(payload)
            (store.assets_path / f"{pdf_digest}.pdf").write_bytes(pdf_payload)

            status, headers, body = self.request(
                f"/editorial-assets/{digest}.png",
                editorial_root=root,
            )
            pdf_status, _, _ = self.request(
                f"/editorial-assets/{pdf_digest}.pdf",
                editorial_root=root,
            )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            headers["Cache-Control"],
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(body, payload)
        self.assertEqual(pdf_status, 404)


if __name__ == "__main__":
    unittest.main()
