from importlib import import_module
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from content_assets import AssetFetchResult  # pyright: ignore[reportMissingImports]
from content_cache import load_active_generation  # pyright: ignore[reportMissingImports]
from statement_crawl import SourceFetch  # pyright: ignore[reportMissingImports]


statement_rebuild = import_module("statement_rebuild")
rebuild_statements = statement_rebuild.rebuild_statements
update_statements = statement_rebuild.update_statements
validate_statement = statement_rebuild.validate_statement


def statement_html(problem_code: str, text: str) -> str:
    index = problem_code[len(problem_code.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")) :]
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
    def __init__(self, documents: dict[str, str], *, failing: set[str] | None = None) -> None:
        self.documents = dict(documents)
        self.failing = set(failing or ())
        self.fetches: list[str] = []

    def problem_codes(self) -> list[str]:
        return sorted(self.documents)

    def fetch_problem(self, problem_code: str) -> SourceFetch:
        self.fetches.append(problem_code)
        if problem_code in self.failing:
            raise OSError("synthetic fetch failure")
        contest_id = "".join(character for character in problem_code if character.isdigit())
        index = problem_code[len(contest_id) :]
        return SourceFetch(
            source_url=f"https://codeforces.com/contest/{contest_id}/problem/{index}",
            source_kind="html",
            body=self.documents[problem_code],
            content_type="text/html",
        )

    def fetch_asset(self, url: str) -> AssetFetchResult:
        raise AssertionError(f"unexpected asset fetch: {url}")


class StatementRebuildTests(unittest.TestCase):
    def test_validate_statement_uses_temporary_root_without_activation(self):
        source = FixtureStatementSource({"1700A": statement_html("1700A", "ok")})
        with tempfile.TemporaryDirectory() as directory, patch.object(
            statement_rebuild,
            "DEFAULT_CACHE_ROOT",
            Path(directory) / "production",
        ):
            report = validate_statement("1700A", source=source)

            self.assertTrue(report["ok"])
            self.assertFalse((Path(directory) / "production" / "current.json").exists())

    def test_complete_statement_rebuild_activates_independently(self):
        source = FixtureStatementSource(
            {
                "1700A": statement_html("1700A", "A body"),
                "1700B": statement_html("1700B", "B body"),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "statements"
            report = rebuild_statements(
                source=source,
                cache_root=root,
                generation_id="s1",
                sleep_fn=lambda _delay: None,
            )
            active = load_active_generation(root)

            self.assertTrue(report["activated"])
            assert active is not None
            self.assertEqual(active.generation_id, "s1")
            self.assertEqual(active.manifest["contentKind"], "statement")
            self.assertEqual(active.manifest["expectedIds"], ["1700A", "1700B"])

    def test_incremental_statement_update_requires_active_parent(self):
        source = FixtureStatementSource({"1700A": statement_html("1700A", "A")})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "statement v2 is not initialized"):
                update_statements(source=source, cache_root=directory)

    def test_incremental_successor_seeds_ready_and_fetches_only_new_problem(self):
        initial = FixtureStatementSource({"1700A": statement_html("1700A", "A")})
        successor = FixtureStatementSource(
            {
                "1700A": statement_html("1700A", "A should stay seeded"),
                "1700B": statement_html("1700B", "B new"),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            rebuild_statements(
                source=initial,
                cache_root=directory,
                generation_id="s1",
                sleep_fn=lambda _delay: None,
            )
            report = update_statements(
                source=successor,
                cache_root=directory,
                generation_id="s2",
                sleep_fn=lambda _delay: None,
            )
            active = load_active_generation(directory)

            self.assertTrue(report["activated"])
            self.assertEqual(successor.fetches, ["1700B"])
            assert active is not None
            self.assertEqual(active.generation_id, "s2")
            self.assertEqual(active.manifest["expectedIds"], ["1700A", "1700B"])

    def test_failed_successor_does_not_replace_active_generation(self):
        initial = FixtureStatementSource({"1700A": statement_html("1700A", "A")})
        failing = FixtureStatementSource(
            {
                "1700A": statement_html("1700A", "A"),
                "1700B": statement_html("1700B", "B"),
            },
            failing={"1700B"},
        )
        with tempfile.TemporaryDirectory() as directory:
            rebuild_statements(
                source=initial,
                cache_root=directory,
                generation_id="s1",
                sleep_fn=lambda _delay: None,
            )
            report = update_statements(
                source=failing,
                cache_root=directory,
                generation_id="s2",
                sleep_fn=lambda _delay: None,
            )
            active = load_active_generation(directory)

            self.assertFalse(report["activated"])
            assert active is not None
            self.assertEqual(active.generation_id, "s1")


if __name__ == "__main__":
    unittest.main()
