from importlib import import_module
from pathlib import Path
import tempfile
import unittest

from editorial_model import canonical_json


statement_crawl = import_module("statement_crawl")
ContentStatus = import_module("content_cache").ContentStatus
from content_assets import AssetFetchResult  # pyright: ignore[reportMissingImports]
SourceFetch = statement_crawl.SourceFetch
ProblemIdentity = statement_crawl.ProblemIdentity
fetch_statement_v2 = statement_crawl.fetch_statement_v2

FIXTURES = Path(__file__).parent / "fixtures" / "statements"
PNG_PAYLOAD = b"\x89PNG\r\n\x1a\nstatement-image"
PDF_PAYLOAD = b"%PDF-1.7\nstatement-fixture"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


class FixtureStatementSource:
    def __init__(
        self,
        *,
        problem_code: str,
        source_fetch: SourceFetch,
        asset_result: AssetFetchResult | None = None,
    ) -> None:
        self.problem_code = problem_code
        self.source_fetch = source_fetch
        self.asset_result = asset_result or AssetFetchResult(PNG_PAYLOAD, "image/png")
        self.asset_urls: list[str] = []

    def problem_codes(self) -> list[str]:
        return [self.problem_code]

    def fetch_problem(self, problem_code: str) -> SourceFetch:
        if problem_code != self.problem_code:
            raise AssertionError("unexpected problem fetch")
        return self.source_fetch

    def fetch_asset(self, url: str) -> AssetFetchResult:
        self.asset_urls.append(url)
        return self.asset_result


class StatementCrawlerTests(unittest.TestCase):
    def test_fetch_statement_v2_returns_ready_ir_without_markdown(self):
        source = FixtureStatementSource(
            problem_code="1700A",
            source_fetch=SourceFetch(
                source_url="https://codeforces.com/contest/1700/problem/A",
                source_kind="html",
                body=fixture("normal.html"),
                content_type="text/html; charset=utf-8",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = fetch_statement_v2("1700A", source=source, asset_root=directory)

            self.assertIs(result.status, ContentStatus.READY)
            self.assertIsNotNone(result.document)
            self.assertEqual(result.document.content_kind, "statement")
            self.assertEqual(result.document.source_kind, "html")
            self.assertNotIn("md", result.evidence)
            images = [node for node in walk(result.document.root) if node.kind == "image"]
            self.assertEqual(len(images), 1)
            self.assertTrue(images[0].attrs["src"].startswith("/statement-assets/"))
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            self.assertEqual(source.asset_urls, ["https://codeforces.com/synthetic/diagram.png"])

    def test_numeric_index_uses_exact_metadata_identity(self):
        class NumericStatementSource(FixtureStatementSource):
            def problem_identities(self) -> list[object]:
                return [ProblemIdentity("92101", "921", "01")]

        source = NumericStatementSource(
            problem_code="92101",
            source_fetch=SourceFetch(
                source_url="https://codeforces.com/contest/921/problem/01",
                source_kind="html",
                body=fixture("normal.html"),
                content_type="text/html",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = fetch_statement_v2("92101", source=source, asset_root=directory)

        self.assertIs(result.status, ContentStatus.READY)
        self.assertEqual(result.document.problem_code, "92101")
        self.assertEqual(result.document.contest_id, "921")
        self.assertEqual(result.document.index, "01")

    def test_pdf_source_becomes_local_attachment_not_text(self):
        source = FixtureStatementSource(
            problem_code="1000A",
            source_fetch=SourceFetch(
                source_url="https://codeforces.com/contest/1000/problem/A",
                source_kind="pdf",
                body=PDF_PAYLOAD,
                content_type="application/pdf",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = fetch_statement_v2("1000A", source=source, asset_root=directory)

            self.assertIs(result.status, ContentStatus.READY)
            self.assertIsNotNone(result.document)
            self.assertEqual(result.document.source_kind, "pdf")
            attachments = [
                node for node in walk(result.document.root) if node.kind == "attachment"
            ]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0].attrs["mediaType"], "application/pdf")
            self.assertTrue(attachments[0].attrs["href"].startswith("/statement-assets/"))
            self.assertNotIn("pdf text", canonical_json(result.document).lower())
            self.assertEqual(source.asset_urls, [])

    def test_source_url_identity_must_match_full_problem_code(self):
        source = FixtureStatementSource(
            problem_code="1700A",
            source_fetch=SourceFetch(
                source_url="https://codeforces.com/contest/1700/problem/A1",
                source_kind="html",
                body=fixture("normal.html"),
                content_type="text/html",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = fetch_statement_v2("1700A", source=source, asset_root=directory)

            self.assertIs(result.status, ContentStatus.INVALID_STRUCTURE)
            self.assertIsNone(result.document)
            self.assertIn("source-identity-mismatch", str(result.evidence))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_interstitial_and_invalid_pdf_are_typed_failures(self):
        cases = [
            SourceFetch(
                source_url="https://codeforces.com/contest/1000/problem/A",
                source_kind="html",
                body="<html><title>Just a moment</title></html>",
                content_type="text/html",
            ),
            SourceFetch(
                source_url="https://codeforces.com/contest/1000/problem/A",
                source_kind="pdf",
                body=b"<html>challenge</html>",
                content_type="application/pdf",
            ),
        ]
        for source_fetch in cases:
            with self.subTest(kind=source_fetch.source_kind), tempfile.TemporaryDirectory() as directory:
                result = fetch_statement_v2(
                    "1000A",
                    source=FixtureStatementSource(
                        problem_code="1000A",
                        source_fetch=source_fetch,
                    ),
                    asset_root=directory,
                )

                self.assertIs(result.status, ContentStatus.INVALID_STRUCTURE)
                self.assertIsNone(result.document)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_unknown_exact_problem_code_is_known_absent_without_fetch(self):
        source = FixtureStatementSource(
            problem_code="1000A1",
            source_fetch=SourceFetch(
                source_url="https://codeforces.com/contest/1000/problem/A1",
                source_kind="html",
                body=fixture("localized.html"),
                content_type="text/html",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = fetch_statement_v2("1000A2", source=source, asset_root=directory)

            self.assertIs(result.status, ContentStatus.KNOWN_ABSENT)
            self.assertIsNone(result.document)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
