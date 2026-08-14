import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cfcrawl
from cfcrawl import EditorialBuildResult, TutorialBatch
from editorial_cache import ContestStatus, GenerationStore, activate_generation
from editorial_model import EditorialDocument, Node
from editorial_rebuild import (
    FIXTURE_VERSION,
    LIVE_1700_SENTINELS,
    PARSER_VERSION,
    FetchReceipt,
    rebuild_editorials,
    update_editorials,
    validate_editorial,
)
import server
import update


FIXTURES = Path(__file__).parent / "fixtures" / "editorials" / "1700"
SOURCE_URL = "https://codeforces.com/blog/entry/103978"
TIMES = [
    "2026-08-14T10:00:00Z",
    "2026-08-14T10:00:10Z",
    "2026-08-14T10:00:20Z",
    "2026-08-14T10:00:30Z",
]


def make_document(contest_id: str, body: str = "ready") -> EditorialDocument:
    return EditorialDocument(
        contest_id=contest_id,
        source_url=f"https://codeforces.com/blog/entry/{contest_id}",
        root=Node(
            kind="document",
            children=[Node(kind="paragraph", children=[Node(kind="text", text=body)])],
        ),
    )


class FixtureEditorialSource:
    def __init__(self, contest_ids=("1700", "9999")):
        self.contest_ids = list(contest_ids)
        self.transient_contests = set()
        self.absence_receipts = {
            "9999": [self._receipt("<html>recognized contest</html>", TIMES[0]),
                     self._receipt("<html>recognized contest</html>", TIMES[1])]
        }
        self._receipt_indexes = {}

    @staticmethod
    def _receipt(body: str, fetched_at: str, *, ok=True, blocked=False, recognized=True):
        return FetchReceipt(ok, body, 200 if ok else None, blocked, recognized, fetched_at)

    def problem_contest_ids(self) -> list[str]:
        return list(self.contest_ids)

    def fetch_contest_page(self, contest_id: str) -> FetchReceipt:
        contest_id = str(contest_id)
        if contest_id in self.transient_contests:
            return FetchReceipt(False, "", None, False, False, TIMES[0], "temporary")
        if contest_id == "1700":
            return self._receipt(
                '<a href="/blog/entry/103978" title="Editorial">Editorial</a>',
                TIMES[0],
            )
        receipts = self.absence_receipts[contest_id]
        index = self._receipt_indexes.get(contest_id, 0)
        self._receipt_indexes[contest_id] = index + 1
        return receipts[min(index, len(receipts) - 1)]

    def find_editorial_url(self, contest_html: str) -> str | None:
        return SOURCE_URL if "/blog/entry/103978" in contest_html else None

    def fetch_editorial_page(self, url: str) -> FetchReceipt:
        if url != SOURCE_URL:
            raise AssertionError(url)
        return self._receipt((FIXTURES / "base.html").read_text(encoding="utf-8"), TIMES[1])

    def fetch_tutorial_batch(self, contest_id: str, codes: list[str]) -> TutorialBatch:
        html_by_code = {}
        for code in codes:
            letter = code.removeprefix(contest_id)
            html = (FIXTURES / f"tutorial-{letter}.html").read_text(encoding="utf-8")
            html_by_code[code] = html.replace(f"{letter}_BODY_SENTINEL", LIVE_1700_SENTINELS[code])
        return TutorialBatch(html_by_code, set(), [])

    def localize_assets(self, document: EditorialDocument) -> EditorialBuildResult:
        return EditorialBuildResult(ContestStatus.READY, document, {})


class EditorialRebuildTests(unittest.TestCase):
    def test_validate_1700_reports_a_through_f_adjacency_without_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            report = validate_editorial("1700", source=FixtureEditorialSource())

            self.assertTrue(report["ok"])
            self.assertEqual(report["problemCodes"], list(LIVE_1700_SENTINELS))
            self.assertEqual(report["matchedSentinels"], LIVE_1700_SENTINELS)
            self.assertEqual(report["unresolvedSlots"], [])
            self.assertEqual(report["validationErrors"], [])
            self.assertEqual(len(report["canonicalJsonSha256"]), 64)
            self.assertFalse((root / "current.json").exists())
            self.assertFalse(root.exists())

    def test_validate_1700_rejects_extra_problem_section(self):
        class ExtraSectionSource(FixtureEditorialSource):
            def localize_assets(self, document):
                document.root.children.append(
                    Node(
                        kind="problem_section",
                        attrs={"problemCode": "1700G"},
                        children=[Node(kind="paragraph", children=[Node(kind="text", text="extra")])],
                    )
                )
                return super().localize_assets(document)

        report = validate_editorial("1700", source=ExtraSectionSource(("1700",)))
        self.assertFalse(report["ok"])
        self.assertIn(
            "unexpected-problem-sections",
            [error.get("code") for error in report["validationErrors"] if isinstance(error, dict)],
        )

    def test_full_rebuild_ignores_v1_markdown_and_failure_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            editorial_dir = Path(directory) / "editorials"
            editorial_dir.mkdir()
            (editorial_dir / "1700.md").write_text("poison v1", encoding="utf-8")
            (Path(directory) / "failed_editorials.json").write_text('["1700", "9999"]', encoding="utf-8")
            root = editorial_dir / "v2"

            report = rebuild_editorials(
                source=FixtureEditorialSource(("1700",)),
                cache_root=root,
                generation_id="full",
                sleep_fn=lambda _delay: None,
            )

            document = GenerationStore.open(root, "full").load_document("1700")
            self.assertTrue(report["activated"])
            self.assertNotIn("poison v1", json.dumps(document.to_dict()))
            self.assertEqual((editorial_dir / "1700.md").read_text(), "poison v1")
            self.assertEqual((Path(directory) / "failed_editorials.json").read_text(), '["1700", "9999"]')

    def test_full_rebuild_does_not_activate_with_transient_failure(self):
        source = FixtureEditorialSource()
        source.transient_contests.add("9999")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            report = rebuild_editorials(
                source=source,
                cache_root=root,
                generation_id="blocked",
                sleep_fn=lambda _delay: None,
            )
            store = GenerationStore.open(root, "blocked")

            self.assertFalse(report["activated"])
            self.assertEqual(store.manifest["contests"]["9999"]["status"], "transient_failure")
            self.assertIsNone(store.manifest["finalizedAt"])
            self.assertFalse((root / "current.json").exists())

    def test_full_rebuild_refuses_empty_problem_index_without_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            with self.assertRaises(ValueError):
                rebuild_editorials(
                    source=FixtureEditorialSource(()),
                    cache_root=root,
                    sleep_fn=lambda _delay: None,
                )
            self.assertFalse((root / "current.json").exists())
            self.assertFalse((root / "generations").exists())

    def test_complete_rebuild_activates_ready_and_known_absent_contests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            report = rebuild_editorials(
                source=FixtureEditorialSource(),
                cache_root=root,
                generation_id="complete",
                sleep_fn=lambda _delay: None,
            )
            store = GenerationStore.open(root, "complete")
            pointer = json.loads((root / "current.json").read_text())

            self.assertTrue(report["activated"])
            self.assertEqual(pointer["generationId"], "complete")
            self.assertEqual(store.manifest["counts"]["ready"], 1)
            self.assertEqual(store.manifest["counts"]["known_absent"], 1)
            evidence = store.manifest["contests"]["9999"]["evidence"]
            self.assertEqual(evidence["successfulCheckTimestamps"], TIMES[:2])
            self.assertEqual(len(evidence["contestPageReceipts"]), 2)

    def test_known_absent_requires_two_matching_valid_page_checks(self):
        cases = {
            "blocked": FetchReceipt(False, "403", 403, True, False, TIMES[1], "blocked"),
            "unrecognized": FetchReceipt(True, "challenge", 200, False, False, TIMES[1]),
            "server-error": FetchReceipt(True, "error", 500, False, True, TIMES[1]),
            "editorial-on-recheck": FixtureEditorialSource._receipt(
                '<a href="/blog/entry/103978" title="Tutorial">Tutorial</a>', TIMES[1]
            ),
            "same-timestamp": FixtureEditorialSource._receipt("<html>recognized contest</html>", TIMES[0]),
            "invalid-timestamp": FixtureEditorialSource._receipt(
                "<html>recognized contest</html>", "not-a-timestamp"
            ),
        }
        for label, second in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                source = FixtureEditorialSource(("9999",))
                source.absence_receipts["9999"] = [
                    source._receipt("<html>recognized contest</html>", TIMES[0]), second
                ]
                root = Path(directory) / "v2"
                report = rebuild_editorials(
                    source=source,
                    cache_root=root,
                    generation_id=label,
                    sleep_fn=lambda _delay: None,
                )
                store = GenerationStore.open(root, label)
                self.assertFalse(report["activated"])
                self.assertEqual(store.manifest["contests"]["9999"]["status"], "transient_failure")
                self.assertFalse((root / "current.json").exists())

    def test_incremental_successor_seeds_ready_documents_and_rechecks_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            rebuild_editorials(
                source=FixtureEditorialSource(), cache_root=root,
                generation_id="base", sleep_fn=lambda _delay: None,
            )
            source = FixtureEditorialSource(("1700", "9999", "2000"))
            source.absence_receipts["2000"] = [
                source._receipt("<html>recognized contest</html>", TIMES[2]),
                source._receipt("<html>recognized contest</html>", TIMES[3]),
            ]
            source.absence_receipts["9999"] = [
                source._receipt("<html>recognized contest</html>", TIMES[2]),
                source._receipt("<html>recognized contest</html>", TIMES[3]),
            ]
            report = update_editorials(
                source=source, cache_root=root, generation_id="successor",
                sleep_fn=lambda _delay: None,
            )
            successor = GenerationStore.open(root, "successor")

            self.assertTrue(report["activated"])
            self.assertEqual(successor.load_document("1700").to_dict(), GenerationStore.open(root, "base").load_document("1700").to_dict())
            self.assertEqual(successor.manifest["contests"]["9999"]["evidence"]["successfulCheckTimestamps"], TIMES[2:4])
            self.assertEqual(successor.manifest["contests"]["2000"]["status"], "known_absent")
            self.assertEqual(json.loads((root / "current.json").read_text())["generationId"], "successor")

    def test_incremental_recrawls_explicitly_requested_ready_contest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            rebuild_editorials(source=FixtureEditorialSource(("1700",)), cache_root=root,
                               generation_id="base", sleep_fn=lambda _delay: None)
            source = FixtureEditorialSource(("1700",))
            source.transient_contests.add("1700")
            report = update_editorials(
                source=source, cache_root=root, generation_id="requested",
                requested_contests=["1700"], sleep_fn=lambda _delay: None,
            )
            store = GenerationStore.open(root, "requested")
            self.assertFalse(report["activated"])
            self.assertEqual(store.manifest["contests"]["1700"]["status"], "transient_failure")
            self.assertEqual(json.loads((root / "current.json").read_text())["generationId"], "base")

    def test_resume_skips_ready_documents_in_same_generation(self):
        source = FixtureEditorialSource(("1700", "9999"))
        source.transient_contests.add("9999")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            rebuild_editorials(source=source, cache_root=root, generation_id="resume",
                               sleep_fn=lambda _delay: None)
            before = (root / "generations" / "resume" / "documents" / "1700.json").read_bytes()
            source.transient_contests = {"1700"}
            source.absence_receipts["9999"] = [
                source._receipt("<html>recognized contest</html>", TIMES[2]),
                source._receipt("<html>recognized contest</html>", TIMES[3]),
            ]
            source._receipt_indexes["9999"] = 0
            report = rebuild_editorials(source=source, cache_root=root, generation_id="resume",
                                        sleep_fn=lambda _delay: None)
            store = GenerationStore.open(root, "resume")
            self.assertTrue(report["activated"])
            self.assertEqual(store.manifest["contests"]["1700"]["status"], "ready")
            self.assertEqual((root / "generations" / "resume" / "documents" / "1700.json").read_bytes(), before)

    def test_default_full_rebuild_retries_same_inactive_generation(self):
        source = FixtureEditorialSource()
        source.transient_contests.add("9999")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            first = rebuild_editorials(
                source=source,
                cache_root=root,
                sleep_fn=lambda _delay: None,
            )
            source.transient_contests.clear()
            second = rebuild_editorials(
                source=source,
                cache_root=root,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(second["generationId"], first["generationId"])
            self.assertTrue(second["activated"])

    def test_cli_parses_validate_editorial_1700(self):
        args = update.build_argument_parser().parse_args(["--validate-editorial", "1700"])
        self.assertEqual(args.validate_editorial, "1700")
        self.assertFalse(args.editorials)

    def test_cli_validate_1700_does_not_change_active_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            editorial_dir = Path(directory) / "editorials"
            v2 = editorial_dir / "v2"
            v2.mkdir(parents=True)
            pointer = v2 / "current.json"
            pointer.write_bytes(b'{"generationId":"active-sentinel"}')
            before = (pointer.read_bytes(), pointer.stat().st_mtime_ns)
            with patch.object(cfcrawl, "EDITORIAL_DIR", str(editorial_dir)), patch(
                "update.validate_editorial",
                side_effect=lambda contest_id: validate_editorial(
                    contest_id,
                    source=FixtureEditorialSource(("1700",)),
                ),
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(update.main(["--validate-editorial", "1700"]), 0)
            self.assertEqual((pointer.read_bytes(), pointer.stat().st_mtime_ns), before)
            self.assertFalse((v2 / "generations").exists())

    def test_cli_parses_editorials_rebuild(self):
        args = update.build_argument_parser().parse_args(["--editorials", "--rebuild"])
        self.assertTrue(args.editorials)
        self.assertTrue(args.rebuild)

    def test_cli_dispatches_all_modes_and_rejects_bare_rebuild(self):
        with patch("update.update_metadata", return_value=0) as metadata:
            self.assertEqual(update.main([]), 0)
            metadata.assert_called_once_with()
        with patch("update.main_crawl", return_value=0) as statements:
            self.assertEqual(update.main(["--statements"]), 0)
            statements.assert_called_once_with()
        with patch("update.rebuild_editorials", return_value={"activated": True}) as rebuild:
            self.assertEqual(update.main(["--editorials", "--rebuild"]), 0)
            rebuild.assert_called_once_with()
        with patch("update.validate_editorial", return_value={"ok": True}) as validate:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(update.main(["--validate-editorial", "1700"]), 0)
            validate.assert_called_once_with("1700")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                update.main(["--rebuild"])
        self.assertEqual(raised.exception.code, 2)

    def test_cli_editorials_uses_legacy_before_activation_and_incremental_after(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cfcrawl, "EDITORIAL_DIR", directory):
            with patch("update.cfcrawl.fetch_all_editorials", return_value=(2, 1, 1)) as legacy:
                self.assertEqual(update.main(["--editorials"]), 0)
                legacy.assert_called_once()
            v2 = Path(directory) / "v2"
            v2.mkdir()
            (v2 / "current.json").write_text("{}", encoding="utf-8")
            with patch("update.update_editorials", return_value={"activated": True}) as incremental:
                self.assertEqual(update.main(["--editorials"]), 0)
                incremental.assert_called_once_with()

    def test_help_is_argparse_help(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                update.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--validate-editorial", output.getvalue())
        self.assertIn("--editorials", output.getvalue())

    def test_server_background_uses_legacy_then_incremental_and_reports_counts(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cfcrawl, "EDITORIAL_DIR", directory), patch(
            "server.subprocess.run"
        ) as run, patch("server.cfcrawl.fetch_all_statements", return_value=(3, 2, 1)), patch(
            "server.cfcrawl.fetch_all_editorials", return_value=(2, 1, 1)
        ) as legacy:
            run.return_value.returncode = 0
            run.return_value.stdout = b"ok"
            run.return_value.stderr = b""
            server.auto_update()
            legacy.assert_called_once()
            self.assertEqual(server.crawl_state["stage"], "done")

            v2 = Path(directory) / "v2"
            v2.mkdir()
            (v2 / "current.json").write_text("{}", encoding="utf-8")
            with patch("server.update_editorials", return_value={
                "generationId": "next", "counts": {
                    "ready": 2, "known_absent": 1, "transient_failure": 0,
                    "invalid_structure": 0, "pending": 0,
                }, "activated": True,
            }) as incremental:
                server.auto_update()
            incremental.assert_called_once_with()
            self.assertEqual(server.crawl_state["generationId"], "next")
            self.assertEqual(server.crawl_state["statusCounts"]["ready"], 2)


if __name__ == "__main__":
    unittest.main()
