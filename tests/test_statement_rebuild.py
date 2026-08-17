from importlib import import_module
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from content_assets import AssetFetchResult  # pyright: ignore[reportMissingImports]
from crawl_priority import CrawlPriorityQueue  # pyright: ignore[reportMissingImports]
from content_cache import ContentStatus, ContentStore  # pyright: ignore[reportMissingImports]
from content_codecs import STATEMENT_CODEC  # pyright: ignore[reportMissingImports]
from statement_crawl import ProblemIdentity, SourceFetch  # pyright: ignore[reportMissingImports]
from statement_model import StatementDocument  # pyright: ignore[reportMissingImports]


statement_rebuild = import_module("statement_rebuild")
rebuild_statements = statement_rebuild.rebuild_statements
update_statements = statement_rebuild.update_statements
validate_statement = statement_rebuild.validate_statement


def statement_html(index: str, text: str) -> str:
    return (
        '<div class="problem-statement">'
        '<div class="header"><div class="title">'
        + index
        + ". Synthetic</div></div>"
        '<div class="problem-description"><p>'
        + text
        + "</p></div></div>"
    )


class FixtureStatementSource:
    def __init__(
        self,
        documents: dict[tuple[str, str], str | bytes],
        *,
        failing: set[str] | None = None,
        pdf: set[str] | None = None,
    ) -> None:
        self.documents = dict(documents)
        self.failing = set(failing or ())
        self.pdf = set(pdf or ())
        self.fetches: list[str] = []
        self.identities = [
            ProblemIdentity(contest_id + index, contest_id, index)
            for contest_id, index in self.documents
        ]

    def problem_identities(self) -> list[ProblemIdentity]:
        return list(self.identities)

    def problem_codes(self) -> list[str]:
        return [item.problem_code for item in self.identities]

    def fetch_problem(self, problem_code: str) -> SourceFetch:
        self.fetches.append(problem_code)
        if problem_code in self.failing:
            raise OSError("synthetic fetch failure")
        identity = next(item for item in self.identities if item.problem_code == problem_code)
        body = self.documents[(identity.contest_id, identity.index)]
        if problem_code in self.pdf:
            return SourceFetch(
                source_url=(
                    f"https://codeforces.com/contest/{identity.contest_id}/problem/"
                    f"{identity.index}"
                ),
                source_kind="pdf",
                body=body,
                content_type="application/pdf",
            )
        return SourceFetch(
            source_url=(
                f"https://codeforces.com/contest/{identity.contest_id}/problem/"
                f"{identity.index}"
            ),
            source_kind="html",
            body=str(body),
            content_type="text/html",
        )

    def fetch_asset(self, url: str) -> AssetFetchResult:
        raise AssertionError(f"unexpected asset fetch: {url}")


class StatementRebuildTests(unittest.TestCase):
    def test_pending_preview_can_skip_ready_document_validation(self):
        source = FixtureStatementSource(
            {
                ("1700", "A"): statement_html("A", "A body"),
                ("1700", "B"): statement_html("B", "B body"),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ContentStore.initialize(directory, STATEMENT_CODEC)
            store.document_path("1700A").write_text("{}", encoding="utf-8")
            with patch.object(
                ContentStore,
                "ready_ids",
                side_effect=AssertionError("preview validated ready documents"),
            ):
                pending = statement_rebuild.pending_statement_ids(
                    source=source,
                    cache_root=directory,
                    validate_documents=False,
                )
            validated_pending = statement_rebuild.pending_statement_ids(
                source=source,
                cache_root=directory,
            )

        self.assertEqual(pending, ["1700B"])
        self.assertEqual(validated_pending, ["1700A", "1700B"])
    def test_first_problem_is_readable_before_later_problem_is_crawled(self):
        source = FixtureStatementSource(
            {
                ("1700", "A"): statement_html("A", "A body"),
                ("1700", "B"): statement_html("B", "B body"),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "statements"

            def observe_after_first(_delay: float) -> None:
                store = ContentStore(root, STATEMENT_CODEC)
                self.assertEqual(store.load_document("1700A").content_id, "1700A")
                with self.assertRaises(FileNotFoundError):
                    store.load_document("1700B")

            report = rebuild_statements(
                source=source,
                cache_root=root,
                delay=0,
                sleep_fn=observe_after_first,
            )

            self.assertTrue(report["completed"])
            self.assertEqual(report["attemptedCount"], 2)
            self.assertEqual(ContentStore(root, STATEMENT_CODEC).ready_ids(), {"1700A", "1700B"})
            self.assertFalse((root / "current.json").exists())
            self.assertFalse((root / "generations").exists())

    def test_incremental_update_bootstraps_empty_store(self):
        source = FixtureStatementSource(
            {("1700", "A"): statement_html("A", "A body")}
        )
        with tempfile.TemporaryDirectory() as directory:
            report = update_statements(
                source=source,
                cache_root=directory,
                sleep_fn=lambda _delay: None,
            )

            self.assertTrue(report["completed"])
            self.assertEqual(source.fetches, ["1700A"])
            self.assertEqual(
                ContentStore(directory, STATEMENT_CODEC).load_document("1700A").content_id,
                "1700A",
            )

    def test_incremental_update_skips_valid_document_and_fetches_new_problem(self):
        initial = FixtureStatementSource(
            {("1700", "A"): statement_html("A", "A body")}
        )
        successor = FixtureStatementSource(
            {
                ("1700", "A"): statement_html("A", "A changed but not requested"),
                ("1700", "B"): statement_html("B", "B new"),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            rebuild_statements(
                source=initial,
                cache_root=directory,
                sleep_fn=lambda _delay: None,
            )
            report = update_statements(
                source=successor,
                cache_root=directory,
                sleep_fn=lambda _delay: None,
            )

            self.assertTrue(report["completed"])
            self.assertEqual(successor.fetches, ["1700B"])
            self.assertEqual(
                ContentStore(directory, STATEMENT_CODEC).ready_ids(),
                {"1700A", "1700B"},
            )


    def test_incremental_update_promotes_clicked_problem_before_next_fetch(self):
        source = FixtureStatementSource(
            {
                ("1700", "A"): statement_html("A", "A body"),
                ("1700", "B"): statement_html("B", "B body"),
                ("1700", "C"): statement_html("C", "C body"),
            }
        )
        priority = CrawlPriorityQueue()

        def prioritize_after_first(_delay: float) -> None:
            if source.fetches == ["1700A"]:
                priority.prioritize("statement", "1700C")

        with tempfile.TemporaryDirectory() as directory:
            report = update_statements(
                source=source,
                cache_root=directory,
                delay=0,
                sleep_fn=prioritize_after_first,
                priority_selector=lambda remaining: priority.pop_next(
                    "statement", remaining
                ),
            )

        self.assertTrue(report["completed"])
        self.assertEqual(source.fetches, ["1700A", "1700C", "1700B"])

    def test_failed_item_is_recorded_and_retried_without_hiding_ready_items(self):
        failing = FixtureStatementSource(
            {
                ("1700", "A"): statement_html("A", "A body"),
                ("1700", "B"): statement_html("B", "B body"),
            },
            failing={"1700B"},
        )
        recovered = FixtureStatementSource(
            {
                ("1700", "A"): statement_html("A", "A body"),
                ("1700", "B"): statement_html("B", "B recovered"),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            first = update_statements(
                source=failing,
                cache_root=directory,
                sleep_fn=lambda _delay: None,
            )
            store = ContentStore(directory, STATEMENT_CODEC)

            self.assertFalse(first["completed"])
            self.assertEqual(store.load_document("1700A").content_id, "1700A")
            self.assertEqual(store.item_status("1700B")["status"], "transient_failure")

            second = update_statements(
                source=recovered,
                cache_root=directory,
                sleep_fn=lambda _delay: None,
            )

            self.assertTrue(second["completed"])
            self.assertEqual(recovered.fetches, ["1700B"])
            self.assertEqual(store.load_document("1700B").content_id, "1700B")

    def test_requested_problem_replaces_only_that_document(self):
        initial = FixtureStatementSource(
            {
                ("1700", "A"): statement_html("A", "old A"),
                ("1700", "B"): statement_html("B", "old B"),
            }
        )
        refreshed = FixtureStatementSource(
            {
                ("1700", "A"): statement_html("A", "new A"),
                ("1700", "B"): statement_html("B", "new B"),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            rebuild_statements(
                source=initial,
                cache_root=directory,
                sleep_fn=lambda _delay: None,
            )
            before_b = ContentStore(directory, STATEMENT_CODEC).document_path("1700B").read_bytes()

            update_statements(
                source=refreshed,
                cache_root=directory,
                requested_problems=["1700A"],
                sleep_fn=lambda _delay: None,
            )

            self.assertEqual(refreshed.fetches, ["1700A"])
            store = ContentStore(directory, STATEMENT_CODEC)
            self.assertIn(b"new A", store.document_path("1700A").read_bytes())
            self.assertEqual(store.document_path("1700B").read_bytes(), before_b)

    def test_numeric_index_is_published_from_exact_identity(self):
        source = FixtureStatementSource(
            {("921", "01"): statement_html("01", "numeric index")}
        )
        with tempfile.TemporaryDirectory() as directory:
            report = update_statements(
                source=source,
                cache_root=directory,
                sleep_fn=lambda _delay: None,
            )
            document = ContentStore(directory, STATEMENT_CODEC).load_document("92101")
            assert isinstance(document, StatementDocument)

            self.assertTrue(report["completed"])
            self.assertEqual(document.contest_id, "921")
            self.assertEqual(document.index, "01")

    def test_incremental_update_preserves_pdf_attachment(self):
        payload = b"%PDF-1.7\nSYNTHETIC_STATEMENT\n"
        initial = FixtureStatementSource({("1700", "A"): payload}, pdf={"1700A"})
        successor = FixtureStatementSource(
            {
                ("1700", "A"): statement_html("A", "must remain PDF"),
                ("1700", "B"): statement_html("B", "new HTML"),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            rebuild_statements(
                source=initial,
                cache_root=directory,
                sleep_fn=lambda _delay: None,
            )
            update_statements(
                source=successor,
                cache_root=directory,
                sleep_fn=lambda _delay: None,
            )
            store = ContentStore(directory, STATEMENT_CODEC)
            document = store.load_document("1700A")
            assert isinstance(document, StatementDocument)

            self.assertIsInstance(document, StatementDocument)
            self.assertEqual(document.source_kind, "pdf")
            self.assertEqual(len(document.assets), 1)
            self.assertTrue((store.assets_path / Path(document.assets[0]).name).is_file())

    def test_validate_statement_uses_temporary_store_only(self):
        source = FixtureStatementSource(
            {("1700", "A"): statement_html("A", "ok")}
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            statement_rebuild,
            "DEFAULT_CACHE_ROOT",
            Path(directory) / "production",
        ):
            report = validate_statement("1700A", source=source)

            self.assertTrue(report["ok"])
            self.assertFalse((Path(directory) / "production").exists())


if __name__ == "__main__":
    unittest.main()
