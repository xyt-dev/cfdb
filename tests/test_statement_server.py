import hashlib
from importlib import import_module
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import cfcrawl
from content_cache import ContentStatus, GenerationStore, activate_generation  # pyright: ignore[reportMissingImports]
from content_codecs import STATEMENT_CODEC  # pyright: ignore[reportMissingImports]
from editorial_model import Node
from statement_model import StatementDocument


server = import_module("server")
Handler = server.Handler
build_statement_payload = server.build_statement_payload


def make_statement(problem_code: str = "1700A") -> StatementDocument:
    split_at = next(
        index
        for index, character in enumerate(problem_code)
        if not character.isdigit()
    )
    contest_id = problem_code[:split_at]
    index = problem_code[split_at:]
    return StatementDocument(
        problem_code=problem_code,
        contest_id=contest_id,
        index=index,
        source_url=f"https://codeforces.com/contest/{contest_id}/problem/{index}",
        source_kind="html",
        root=Node(
            kind="document",
            children=[
                Node(
                    kind="heading",
                    attrs={"level": 1, "role": "title"},
                    children=[Node(kind="text", text="A < B")],
                ),
                Node(
                    kind="section",
                    attrs={"role": "body"},
                    children=[Node(kind="paragraph", children=[Node(kind="text", text="body")])],
                ),
            ],
        ),
    )


class StatementServerTests(unittest.TestCase):
    def create_active_generation(
        self,
        root: Path,
        document: StatementDocument,
        *,
        assets: dict[str, bytes] | None = None,
    ):
        store = GenerationStore.create(
            root,
            "s1",
            [document.problem_code],
            STATEMENT_CODEC,
            "parser-v2",
            "fixtures-v2",
        )
        if assets:
            assets_path = store.path / "assets"
            assets_path.mkdir()
            for name, payload in assets.items():
                (assets_path / name).write_bytes(payload)
        document_path = store.write_document(document)
        store.set_status(
            document.problem_code,
            ContentStatus.READY,
            evidence={"validatedAt": "2026-08-15T00:00:00Z"},
            document_path=document_path,
        )
        store.write_manifest()
        activate_generation(root, "s1")
        return store

    def request(self, path: str, *, statement_root: Path):
        import http.server

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{httpd.server_address[1]}{path}"
            request = Request(url)
            with patch.object(server, "STATEMENT_V2_ROOT", statement_root):
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

    def test_statement_without_pointer_is_503_and_ignores_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            legacy = Path(directory) / "1700A.md"
            legacy.write_text("must not leak", encoding="utf-8")
            expected = {
                "format": None,
                "contentKind": "statement",
                "html": None,
                "status": "v2_not_initialized",
                "known": False,
                "error": "statement v2 is not initialized",
            }

            with patch.object(
                cfcrawl,
                "read_statement_md",
                side_effect=AssertionError("legacy statement read attempted"),
            ):
                self.assertEqual(build_statement_payload("1700A", cache_root=root), expected)
                status, headers, body = self.request(
                    "/api/statement?contestId=1700&index=A",
                    statement_root=root,
                )

            self.assertEqual(status, 503)
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(json.loads(body), expected)
            self.assertNotIn("md", json.loads(body))

    def test_ready_statement_payload_contains_sanitized_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            document = make_statement()
            self.create_active_generation(root, document)

            payload = build_statement_payload("1700A", cache_root=root)

            self.assertEqual(payload["format"], "html")
            self.assertEqual(payload["contentKind"], "statement")
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["schema"], 2)
            self.assertEqual(payload["sourceKind"], "html")
            self.assertEqual(payload["url"], document.source_url)
            self.assertIn("A &lt; B", payload["html"])

    def test_pdf_asset_route_is_digest_only_and_nosniff(self):
        payload = b"%PDF-1.7\nserver-fixture"
        digest = hashlib.sha256(payload).hexdigest()
        route = f"/statement-assets/{digest}.pdf"
        wrong_digest_name = f"{'0' * 64}.pdf"
        wrong_magic_payload = b"not-a-pdf"
        wrong_magic_name = f"{hashlib.sha256(wrong_magic_payload).hexdigest()}.pdf"
        missing_name = f"{'1' * 64}.pdf"
        document = make_statement("1000A")
        document.source_kind = "pdf"
        document.root.children.append(
            Node(
                kind="attachment",
                attrs={
                    "href": route,
                    "mediaType": "application/pdf",
                    "label": "Open PDF",
                },
            )
        )
        document.assets = [route]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            self.create_active_generation(
                root,
                document,
                assets={
                    f"{digest}.pdf": payload,
                    wrong_digest_name: payload,
                    wrong_magic_name: wrong_magic_payload,
                },
            )

            status, headers, body = self.request(route, statement_root=root)
            traversal_status, _, _ = self.request(
                "/statement-assets/%2e%2e%2fsecret.pdf",
                statement_root=root,
            )
            wrong_digest_status, _, _ = self.request(
                f"/statement-assets/{wrong_digest_name}",
                statement_root=root,
            )
            wrong_magic_status, _, _ = self.request(
                f"/statement-assets/{wrong_magic_name}",
                statement_root=root,
            )
            missing_status, _, _ = self.request(
                f"/statement-assets/{missing_name}",
                statement_root=root,
            )
            extension_status, _, _ = self.request(
                f"/statement-assets/{digest}.svg",
                statement_root=root,
            )

            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/pdf")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(
                headers["Content-Disposition"],
                f'attachment; filename="{digest}.pdf"',
            )
            self.assertEqual(body, payload)
            self.assertEqual(traversal_status, 400)
            self.assertEqual(wrong_digest_status, 404)
            self.assertEqual(wrong_magic_status, 404)
            self.assertEqual(missing_status, 404)
            self.assertEqual(extension_status, 400)

    def test_invalid_statement_reference_returns_400(self):
        with tempfile.TemporaryDirectory() as directory:
            status, _, body = self.request(
                "/api/statement?contestId=../1700&index=A",
                statement_root=Path(directory),
            )

        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["status"], "invalid_ref")


    def test_statement_query_fields_must_match_document_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            self.create_active_generation(root, make_statement("1700A"))

            status, _, body = self.request(
                "/api/statement?contestId=17&index=00A",
                statement_root=root,
            )

        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["status"], "invalid_ref")

    def test_compound_statement_index_preserves_exact_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            document = make_statement("1970A1")
            self.create_active_generation(root, document)

            status, _, body = self.request(
                "/api/statement?contestId=1970&index=A1",
                statement_root=root,
            )

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["url"], document.source_url)

    def test_problem_metadata_uses_v2_manifest_not_legacy_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "v2"
            self.create_active_generation(root, make_statement("1700A"))
            legacy = base / "legacy-statements"
            legacy.mkdir()
            (legacy / "9999A.md").write_text("must be ignored", encoding="utf-8")
            solutions = base / "solutions"
            solutions.mkdir()
            problems = [{"id": "1700A"}, {"id": "9999A"}]

            with patch.object(cfcrawl, "STATEMENT_DIR", str(legacy)), patch.object(
                cfcrawl,
                "SOLUTION_DIR",
                str(solutions),
            ), patch.object(server, "PROBLEMS", problems):
                status, _, body = self.request(
                    "/api/problems",
                    statement_root=root,
                )

        self.assertEqual(status, 200)
        payload = {item["id"]: item for item in json.loads(body)}
        self.assertTrue(payload["1700A"]["hasFile"])
        self.assertFalse(payload["9999A"]["hasFile"])


if __name__ == "__main__":
    unittest.main()
