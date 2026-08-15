import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cfcrawl
from cfcrawl import EditorialBuildResult, TutorialBatch
from editorial_cache import (
    ContestStatus,
    GenerationStore,
    activate_generation,
    atomic_write_json,
    load_active_document,
)
from editorial_model import EditorialDocument, Node
from content_codecs import EDITORIAL_CODEC  # pyright: ignore[reportMissingImports]
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


def checked_absence(first=TIMES[0], second=TIMES[1]):
    return {
        "successfulCheckTimestamps": [first, second],
        "contestPageReceipts": [
            {
                "fetchedAt": first,
                "recognized": True,
                "editorialFound": False,
                "tutorialFound": False,
            },
            {
                "fetchedAt": second,
                "recognized": True,
                "editorialFound": False,
                "tutorialFound": False,
            },
        ],
    }


def create_ready_generation(
    root: Path,
    generation_id: str,
    body: str,
    *,
    contests=("1700", "9999"),
    activate=False,
):
    store = GenerationStore.create(
        root,
        generation_id,
        contests,
        EDITORIAL_CODEC,
        parser_version=PARSER_VERSION,
        fixture_version=FIXTURE_VERSION,
    )
    document_path = store.write_document(make_document("1700", body))
    store.set_status(
        "1700",
        ContestStatus.READY,
        evidence={"validatedAt": TIMES[0]},
        document_path=document_path,
    )
    for contest_id in contests:
        if contest_id != "1700":
            store.set_status(
                contest_id,
                ContestStatus.KNOWN_ABSENT,
                evidence=checked_absence(),
            )
    store.write_manifest()
    if activate:
        activate_generation(root, generation_id)
    return store


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
            digest = report["canonicalJsonSha256"]
            self.assertIsNotNone(digest)
            assert digest is not None
            self.assertEqual(len(digest), 64)
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
            self.assertEqual(store.manifest["entries"]["9999"]["status"], "transient_failure")
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
            evidence = store.manifest["entries"]["9999"]["evidence"]
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
                self.assertEqual(store.manifest["entries"]["9999"]["status"], "transient_failure")
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
            self.assertEqual(successor.manifest["entries"]["9999"]["evidence"]["successfulCheckTimestamps"], TIMES[2:4])
            self.assertEqual(successor.manifest["entries"]["2000"]["status"], "known_absent")
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
            self.assertEqual(store.manifest["entries"]["1700"]["status"], "transient_failure")
            self.assertEqual(json.loads((root / "current.json").read_text())["generationId"], "base")

    def test_stale_incremental_is_never_resumed_after_newer_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            create_ready_generation(root, "base", "ACTIVE_A", activate=True)

            stale_source = FixtureEditorialSource()
            stale_source.transient_contests.add("9999")
            stale = update_editorials(
                source=stale_source,
                cache_root=root,
                generation_id="update-stale",
                sleep_fn=lambda _delay: None,
            )
            self.assertFalse(stale["activated"])
            self.assertEqual(
                GenerationStore.open(root, "update-stale").load_document("1700"),
                make_document("1700", "ACTIVE_A"),
            )

            create_ready_generation(root, "newer", "ACTIVE_B", activate=True)
            source = FixtureEditorialSource()
            with self.assertRaisesRegex(ValueError, "incremental generation already exists"):
                update_editorials(
                    source=source,
                    cache_root=root,
                    generation_id="update-stale",
                    sleep_fn=lambda _delay: None,
                )
            self.assertEqual(
                load_active_document(root, "1700"),
                make_document("1700", "ACTIVE_B"),
            )

            successor = update_editorials(
                source=source,
                cache_root=root,
                sleep_fn=lambda _delay: None,
            )
            self.assertNotEqual(successor["generationId"], "update-stale")
            self.assertTrue(successor["activated"])
            self.assertEqual(
                load_active_document(root, "1700"),
                make_document("1700", "ACTIVE_B"),
            )

    def test_incremental_fails_closed_on_noncanonical_or_digest_mismatched_active(self):
        for corruption in ("noncanonical-pointer", "manifest-digest"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "v2"
                store = create_ready_generation(root, "active", "ACTIVE", activate=True)
                pointer_path = root / "current.json"
                if corruption == "noncanonical-pointer":
                    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                    pointer_path.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
                else:
                    manifest_path = store.path / "manifest.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["fixtureVersion"] = "changed-but-valid"
                    atomic_write_json(manifest_path, manifest)

                before_generations = sorted(path.name for path in (root / "generations").iterdir())
                with self.assertRaises(ValueError):
                    update_editorials(
                        source=FixtureEditorialSource(),
                        cache_root=root,
                        sleep_fn=lambda _delay: None,
                    )
                self.assertEqual(
                    sorted(path.name for path in (root / "generations").iterdir()),
                    before_generations,
                )

    def test_incremental_fails_closed_if_active_parent_drifts_before_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2"
            create_ready_generation(
                root,
                "parent",
                "ACTIVE_A",
                contests=("1700",),
                activate=True,
            )
            other = create_ready_generation(
                root,
                "unexpected",
                "ACTIVE_B",
                contests=("1700",),
            )
            manifest_bytes = (other.path / "manifest.json").read_bytes()
            unexpected_pointer = {
                "schema": 2,
                "generationId": "unexpected",
                "activatedAt": TIMES[2],
                "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
            }
            pointer_path = root / "current.json"

            class DriftingSource(FixtureEditorialSource):
                def __init__(self):
                    super().__init__(("1700",))
                    self.changed = False

                def fetch_contest_page(self, contest_id):
                    if not self.changed:
                        atomic_write_json(pointer_path, unexpected_pointer)
                        self.changed = True
                    return super().fetch_contest_page(contest_id)

            with self.assertRaisesRegex(RuntimeError, "active generation changed"):
                update_editorials(
                    source=DriftingSource(),
                    cache_root=root,
                    generation_id="update-drift",
                    requested_contests=["1700"],
                    sleep_fn=lambda _delay: None,
                )
            self.assertEqual(
                load_active_document(root, "1700"),
                make_document("1700", "ACTIVE_B"),
            )

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
            self.assertEqual(store.manifest["entries"]["1700"]["status"], "ready")
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

    def test_cli_dispatches_explicit_modes_and_rejects_ambiguous_rebuilds(self):
        with patch("update.update_metadata", return_value=0) as metadata:
            self.assertEqual(update.main([]), 0)
        metadata.assert_called_once_with()

        with patch(
            "update.rebuild_editorials",
            return_value={"activated": True},
        ) as rebuild, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(update.main(["--editorials", "--rebuild"]), 0)
        rebuild.assert_called_once_with()

        with patch("update.validate_editorial", return_value={"ok": True}) as validate:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(update.main(["--validate-editorial", "1700"]), 0)
        validate.assert_called_once_with("1700")

        for argv in (["--rebuild"], ["--statements", "--editorials"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    update.main(list(argv))
                self.assertEqual(raised.exception.code, 2)

    def test_plain_editorial_update_without_pointer_does_not_crawl(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            cfcrawl,
            "EDITORIAL_DIR",
            directory,
        ), patch("update.update_editorials") as incremental, patch(
            "update.rebuild_editorials"
        ) as rebuild, patch("update.cfcrawl.fetch_all_editorials") as legacy, contextlib.redirect_stderr(
            io.StringIO()
        ):
            self.assertNotEqual(update.main(["--editorials"]), 0)

        incremental.assert_not_called()
        rebuild.assert_not_called()
        legacy.assert_not_called()

    def test_plain_editorial_update_with_pointer_dispatches_incremental(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            cfcrawl,
            "EDITORIAL_DIR",
            directory,
        ):
            root = Path(directory) / "v2"
            root.mkdir()
            (root / "current.json").write_text("{}", encoding="utf-8")
            with patch(
                "update.update_editorials",
                return_value={"activated": True},
            ) as incremental, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(update.main(["--editorials"]), 0)

        incremental.assert_called_once_with()

    def test_help_is_argparse_help(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                update.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--validate-editorial", output.getvalue())
        self.assertIn("--validate-statement", output.getvalue())
        self.assertIn("--editorials", output.getvalue())
        self.assertIn("--statements", output.getvalue())

    def test_server_background_skips_uninitialized_content_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            statement_root = Path(directory) / "statements-v2"
            editorial_root = Path(directory) / "editorials-v2"
            with patch.object(server, "STATEMENT_V2_ROOT", statement_root), patch.object(
                server,
                "EDITORIAL_V2_ROOT",
                editorial_root,
            ), patch("server.subprocess.run") as run, patch(
                "server.update_statements",
                create=True,
            ) as statements, patch("server.update_editorials") as editorials, patch(
                "server.cfcrawl.fetch_all_statements",
                side_effect=AssertionError("legacy statement crawl attempted"),
            ), patch(
                "server.cfcrawl.fetch_all_editorials",
                side_effect=AssertionError("legacy editorial crawl attempted"),
            ):
                run.return_value.returncode = 0
                run.return_value.stdout = b"ok"
                run.return_value.stderr = b""
                server.auto_update()

            statements.assert_not_called()
            editorials.assert_not_called()
            self.assertEqual(
                server.crawl_state["contentStatus"],
                {
                    "statement": "v2_not_initialized",
                    "editorial": "v2_not_initialized",
                },
            )
            self.assertEqual(server.crawl_state["stage"], "done")

    def test_server_background_updates_active_roots_independently(self):
        counts = {
            "ready": 2,
            "known_absent": 1,
            "transient_failure": 0,
            "invalid_structure": 0,
            "pending": 0,
        }
        for statement_active, editorial_active in (
            (True, False),
            (False, True),
            (True, True),
        ):
            with self.subTest(
                statement_active=statement_active,
                editorial_active=editorial_active,
            ), tempfile.TemporaryDirectory() as directory:
                statement_root = Path(directory) / "statements-v2"
                editorial_root = Path(directory) / "editorials-v2"
                if statement_active:
                    statement_root.mkdir()
                    (statement_root / "current.json").write_text("{}", encoding="utf-8")
                if editorial_active:
                    editorial_root.mkdir()
                    (editorial_root / "current.json").write_text("{}", encoding="utf-8")

                with patch.object(server, "STATEMENT_V2_ROOT", statement_root), patch.object(
                    server,
                    "EDITORIAL_V2_ROOT",
                    editorial_root,
                ), patch("server.subprocess.run") as run, patch(
                    "server.update_statements",
                    create=True,
                    return_value={
                        "generationId": "s-next",
                        "counts": counts,
                        "activated": True,
                    },
                ) as statements, patch(
                    "server.update_editorials",
                    return_value={
                        "generationId": "e-next",
                        "counts": counts,
                        "activated": True,
                    },
                ) as editorials, patch(
                    "server.cfcrawl.fetch_all_statements",
                    side_effect=AssertionError("legacy statement crawl attempted"),
                ), patch(
                    "server.cfcrawl.fetch_all_editorials",
                    side_effect=AssertionError("legacy editorial crawl attempted"),
                ):
                    run.return_value.returncode = 0
                    run.return_value.stdout = b"ok"
                    run.return_value.stderr = b""
                    server.auto_update()

                self.assertEqual(statements.call_count, int(statement_active))
                self.assertEqual(editorials.call_count, int(editorial_active))
                expected_status = {
                    "statement": "updated" if statement_active else "v2_not_initialized",
                    "editorial": "updated" if editorial_active else "v2_not_initialized",
                }
                self.assertEqual(server.crawl_state["contentStatus"], expected_status)
                self.assertEqual(
                    set(server.crawl_state["generations"]),
                    {
                        kind
                        for kind, active in (
                            ("statement", statement_active),
                            ("editorial", editorial_active),
                        )
                        if active
                    },
                )


    def test_server_background_continues_after_statement_update_error(self):
        counts = {
            "ready": 1,
            "known_absent": 0,
            "transient_failure": 0,
            "invalid_structure": 0,
            "pending": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            statement_root = Path(directory) / "statements-v2"
            editorial_root = Path(directory) / "editorials-v2"
            for root in (statement_root, editorial_root):
                root.mkdir()
                (root / "current.json").write_text("{}", encoding="utf-8")

            with patch.object(server, "STATEMENT_V2_ROOT", statement_root), patch.object(
                server,
                "EDITORIAL_V2_ROOT",
                editorial_root,
            ), patch("server.subprocess.run") as run, patch(
                "server.update_statements",
                side_effect=RuntimeError("statement update failed"),
            ) as statements, patch(
                "server.update_editorials",
                return_value={
                    "generationId": "e-next",
                    "counts": counts,
                    "activated": True,
                },
            ) as editorials, patch(
                "server.cfcrawl.fetch_all_statements",
                side_effect=AssertionError("legacy statement crawl attempted"),
            ), patch(
                "server.cfcrawl.fetch_all_editorials",
                side_effect=AssertionError("legacy editorial crawl attempted"),
            ):
                run.return_value.returncode = 0
                run.return_value.stdout = b"ok"
                run.return_value.stderr = b""
                server.auto_update()

            statements.assert_called_once_with()
            editorials.assert_called_once_with()
            self.assertEqual(
                server.crawl_state["contentStatus"],
                {"statement": "error", "editorial": "updated"},
            )
            self.assertEqual(
                server.crawl_state["generations"]["statement"],
                {"error": "statement update failed"},
            )
            self.assertEqual(
                server.crawl_state["generations"]["editorial"]["generationId"],
                "e-next",
            )
            self.assertEqual(server.crawl_state["stage"], "done")


if __name__ == "__main__":
    unittest.main()
