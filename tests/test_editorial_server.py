import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import cfcrawl
from content_cache import ContentStatus as ContestStatus, GenerationStore, activate_generation  # pyright: ignore[reportMissingImports]
from editorial_model import EditorialDocument, Node
from content_codecs import EDITORIAL_CODEC  # pyright: ignore[reportMissingImports]
from server import Handler, build_editorial_payload


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


def make_document(contest_id="1700"):
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
    def create_active_generation(self, root, entries, *, assets=None):
        store = GenerationStore.create(
            root,
            "g1",
            entries,
            EDITORIAL_CODEC,
            parser_version="parser-1",
            fixture_version="fixtures-1",
        )
        if assets:
            assets_path = store.path / "assets"
            assets_path.mkdir()
            for name, payload in assets.items():
                (assets_path / name).write_bytes(payload)
        for contest_id, status in entries.items():
            if status is ContestStatus.READY:
                document = make_document(contest_id)
                document_path = store.write_document(document)
                store.set_status(
                    contest_id,
                    status,
                    evidence={"validatedAt": "2026-08-14T10:00:00Z"},
                    document_path=document_path,
                )
            else:
                store.set_status(contest_id, status, evidence=CHECKED_ABSENCE)
        store.write_manifest()
        activate_generation(root, "g1")
        return store

    def request(self, path, headers=None):
        import http.server

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{httpd.server_address[1]}{path}"
            request = Request(url, headers=headers or {})
            with patch(
                "server.EDITORIAL_V2_ROOT",
                Path(cfcrawl.EDITORIAL_DIR) / "v2",
            ):
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

    def test_ready_v2_payload_contains_sanitized_html_and_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            self.create_active_generation(root, {"1700": ContestStatus.READY})
            expected = {
                "format": "html",
                "contentKind": "editorial",
                "schema": 2,
                "html": "<p>&lt;safe&gt;click</p>",
                "url": "https://codeforces.com/blog/entry/1700",
                "known": True,
                "status": "ready",
            }

            self.assertEqual(build_editorial_payload("1700", cache_root=root), expected)
            with patch.object(cfcrawl, "EDITORIAL_DIR", directory):
                status, headers, body = self.request("/api/editorial?contestId=1700")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(json.loads(body), expected)

    def test_malformed_cache_valid_document_fails_closed_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editorial_directory = root / "editorials"
            cache_root = editorial_directory / "v2"
            store = self.create_active_generation(
                cache_root,
                {"1700": ContestStatus.READY},
            )
            document_path = store.path / "documents" / "1700.json"
            value = json.loads(document_path.read_text(encoding="utf-8"))
            value["document"]["children"][0]["children"][0]["text"] = 42
            document_path.write_text(
                json.dumps(value, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )

            def snapshot():
                return {
                    path.relative_to(root).as_posix(): (
                        path.stat().st_size,
                        path.stat().st_mtime_ns,
                    )
                    for path in root.rglob("*")
                    if path.is_file()
                }

            expected = {
                "format": None,
                "contentKind": "editorial",
                "html": None,
                "status": "invalid_structure",
                "known": False,
                "error": "ready document digest mismatch",
            }
            before_files = snapshot()
            with patch.object(cfcrawl, "EDITORIAL_DIR", str(editorial_directory)):
                self.assertEqual(build_editorial_payload("1700", cache_root=cache_root), expected)
                status, headers, body = self.request("/api/editorial?contestId=1700")

            self.assertEqual(status, 500)
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(json.loads(body), expected)
            self.assertEqual(snapshot(), before_files)

    def test_known_absent_v2_payload_has_null_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            self.create_active_generation(root, {"9999": ContestStatus.KNOWN_ABSENT})
            expected = {
                "format": None,
                "contentKind": "editorial",
                "html": None,
                "status": "known_absent",
                "known": True,
            }

            self.assertEqual(build_editorial_payload("9999", cache_root=root), expected)
            with patch.object(cfcrawl, "EDITORIAL_DIR", directory):
                status, headers, body = self.request("/api/editorial?contestId=9999")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(json.loads(body), expected)

    def test_editorial_without_pointer_is_503_and_ignores_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editorial_directory = root / "editorials"
            editorial_directory.mkdir()
            (editorial_directory / "1700.md").write_text(
                "legacy must not leak",
                encoding="utf-8",
            )
            expected = {
                "format": None,
                "contentKind": "editorial",
                "html": None,
                "status": "v2_not_initialized",
                "known": False,
                "error": "editorial v2 is not initialized",
            }

            with patch.object(cfcrawl, "EDITORIAL_DIR", str(editorial_directory)):
                self.assertEqual(
                    build_editorial_payload("1700", cache_root=root / "v2"),
                    expected,
                )
                status, headers, body = self.request("/api/editorial?contestId=1700")

            self.assertEqual(status, 503)
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(json.loads(body), expected)
            self.assertNotIn("md", json.loads(body))

    def test_after_v2_activation_missing_contest_does_not_fall_back_to_v1(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editorial_directory = root / "editorials"
            editorial_directory.mkdir()
            (editorial_directory / "2000.md").write_text(
                "legacy must not leak",
                encoding="utf-8",
            )
            cache_root = editorial_directory / "v2"
            self.create_active_generation(cache_root, {"1700": ContestStatus.READY})

            with patch.object(cfcrawl, "EDITORIAL_DIR", str(editorial_directory)):
                status, headers, body = self.request("/api/editorial?contestId=2000")

            self.assertEqual(status, 500)
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(
                json.loads(body),
                {
                    "format": None,
                    "contentKind": "editorial",
                    "html": None,
                    "status": "invalid_structure",
                    "known": False,
                    "error": "active manifest missing editorial",
                },
            )

    def test_payload_read_does_not_change_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editorial_directory = root / "editorials"
            cache_root = editorial_directory / "v2"
            self.create_active_generation(
                cache_root,
                {
                    "1700": ContestStatus.READY,
                    "9999": ContestStatus.KNOWN_ABSENT,
                },
            )

            def snapshot():
                return {
                    path.relative_to(root).as_posix(): (
                        path.stat().st_size,
                        path.stat().st_mtime_ns,
                    )
                    for path in root.rglob("*")
                    if path.is_file()
                }

            before_files = snapshot()
            build_editorial_payload("1700", cache_root=cache_root)
            build_editorial_payload("9999", cache_root=cache_root)

            self.assertEqual(snapshot(), before_files)

    def test_invalid_contest_reference_returns_invalid_ref_payload(self):
        expected = {
            "format": None,
            "contentKind": "editorial",
            "html": None,
            "status": "invalid_ref",
            "known": False,
            "error": "invalid ref",
        }
        self.assertEqual(build_editorial_payload("../1700"), expected)

        status, headers, body = self.request("/api/editorial?contestId=../1700")
        self.assertEqual(status, 400)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), expected)

    def test_reader_payload_static_route_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = b"window.CFDBReaderPayload = {};\n"
            (root / "reader_payload.js").write_bytes(source)
            with patch("server.ROOT", str(root)):
                status, headers, body = self.request("/reader_payload.js")
                nested_status, _, _ = self.request("/nested/reader_payload.js")

            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/javascript; charset=utf-8")
            self.assertEqual(body, source)
            self.assertEqual(nested_status, 404)

    def test_opaque_origin_font_response_has_narrow_cors_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vendor = root / "vendor"
            vendor.mkdir()
            font = b"local-font-bytes"
            (vendor / "ReaderFont.otf").write_bytes(font)
            with patch("server.ROOT", str(root)):
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
        self.assertEqual(api_headers["Content-Type"], "application/json")
        self.assertEqual(api_headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", api_headers)


    def test_editorial_asset_route_serves_digest_validated_image(self):
        import hashlib

        payload = b"\x89PNG\r\n\x1a\neditorial-server-fixture"
        digest = hashlib.sha256(payload).hexdigest()
        pdf_payload = b"%PDF-1.7\neditorial-pdf-must-not-serve"
        pdf_digest = hashlib.sha256(pdf_payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            self.create_active_generation(
                root,
                {"1700": ContestStatus.READY},
                assets={
                    f"{digest}.png": payload,
                    f"{pdf_digest}.pdf": pdf_payload,
                },
            )

            with patch.object(cfcrawl, "EDITORIAL_DIR", directory):
                status, headers, body = self.request(
                    f"/editorial-assets/{digest}.png"
                )
                pdf_status, _, _ = self.request(
                    f"/editorial-assets/{pdf_digest}.pdf"
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
