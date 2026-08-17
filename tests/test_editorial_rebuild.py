import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import editorial_rebuild
from cfcrawl import EditorialBuildResult, TutorialBatch
from content_cache import ContentStatus, ContentStore  # pyright: ignore[reportMissingImports]
from crawl_priority import CrawlPriorityQueue  # pyright: ignore[reportMissingImports]
from content_codecs import EDITORIAL_CODEC  # pyright: ignore[reportMissingImports]
from editorial_model import EditorialDocument, Node
from editorial_rebuild import (
    LIVE_1700_SENTINELS,
    FetchReceipt,
    rebuild_editorials,
    update_editorials,
    validate_editorial,
)


FIXTURES = Path(__file__).parent / "fixtures" / "editorials" / "1700"
SOURCE_URL = "https://codeforces.com/blog/entry/103978"
TIMES = [
    "2026-08-14T10:00:00Z",
    "2026-08-14T10:00:10Z",
    "2026-08-14T10:00:20Z",
    "2026-08-14T10:00:30Z",
]
PNG_PAYLOAD = b"\x89PNG\r\n\x1a\neditorial-image"


class FixtureEditorialSource:
    def __init__(self, contest_ids=("1700", "9999")) -> None:
        self.contest_ids = list(contest_ids)
        self.transient_contests: set[str] = set()
        self.blocked_contests: dict[str, threading.Event] = {}
        self.started_contests: dict[str, threading.Event] = {}
        self.fetches: list[str] = []
        self.absence_receipts = {
            contest_id: [
                self._receipt("<html>recognized contest</html>", TIMES[0]),
                self._receipt("<html>recognized contest</html>", TIMES[1]),
            ]
            for contest_id in self.contest_ids
            if contest_id != "1700"
        }
        self._receipt_indexes: dict[str, int] = {}

    @staticmethod
    def _receipt(
        body: str,
        fetched_at: str,
        *,
        ok: bool = True,
        blocked: bool = False,
        recognized: bool = True,
    ) -> FetchReceipt:
        return FetchReceipt(ok, body, 200 if ok else None, blocked, recognized, fetched_at)

    def problem_contest_ids(self) -> list[str]:
        return list(self.contest_ids)

    def fetch_contest_page(self, contest_id: str) -> FetchReceipt:
        contest_id = str(contest_id)
        self.fetches.append(contest_id)
        started = self.started_contests.get(contest_id)
        if started is not None:
            started.set()
        blocker = self.blocked_contests.get(contest_id)
        if blocker is not None and not blocker.wait(timeout=5):
            raise TimeoutError("fixture blocker was not released")
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
        return self._receipt(
            (FIXTURES / "base.html").read_text(encoding="utf-8"),
            TIMES[1],
        )

    def fetch_tutorial_batch(self, contest_id: str, codes: list[str]) -> TutorialBatch:
        html_by_code = {}
        for code in codes:
            letter = code.removeprefix(contest_id)
            html = (FIXTURES / f"tutorial-{letter}.html").read_text(encoding="utf-8")
            html_by_code[code] = html.replace(
                f"{letter}_BODY_SENTINEL",
                LIVE_1700_SENTINELS[code],
            )
        return TutorialBatch(html_by_code, set(), [])

    def localize_assets(
        self,
        document: EditorialDocument,
        *,
        asset_root: Path,
    ) -> EditorialBuildResult:
        return EditorialBuildResult(ContentStatus.READY, document, {})


class EditorialRebuildTests(unittest.TestCase):
    def test_pending_preview_can_skip_ready_document_validation(self):
        source = FixtureEditorialSource(("1700", "9999"))
        with tempfile.TemporaryDirectory() as directory:
            store = ContentStore.initialize(directory, EDITORIAL_CODEC)
            store.document_path("1700").write_text("{}", encoding="utf-8")
            with patch.object(
                ContentStore,
                "ready_ids",
                side_effect=AssertionError("preview validated ready documents"),
            ):
                pending = editorial_rebuild.pending_editorial_ids(
                    source=source,
                    cache_root=directory,
                    validate_documents=False,
                )
            validated_pending = editorial_rebuild.pending_editorial_ids(
                source=source,
                cache_root=directory,
            )

        self.assertEqual(pending, ["9999"])
        self.assertEqual(validated_pending, ["1700", "9999"])
    def test_recognition_ignores_error_phrase_inside_editorial_content(self):
        body = """
            <html>
              <head><title>Round editorial - Codeforces</title></head>
              <body><div class="ttypography">The target does not exist.</div></body>
            </html>
        """

        self.assertTrue(editorial_rebuild._is_recognized(body))

    def test_recognition_rejects_explicit_missing_page_title(self):
        body = """
            <html>
              <head><title>Blog entry does not exist - Codeforces</title></head>
              <body><div class="error">Missing entry</div></body>
            </html>
        """

        self.assertFalse(editorial_rebuild._is_recognized(body))


    def test_recognition_ignores_body_prose_when_head_end_is_omitted(self):
        body = """
            <html>
              <head>
                <title>Round editorial - Codeforces</title>
                <meta name="description" content="The target does not exist">
              <body>
                <div class="ttypography">The target does not exist.</div>
              </body>
            </html>
        """

        self.assertTrue(editorial_rebuild._is_recognized(body))

    def test_recognition_rejects_plain_block_page(self):
        self.assertFalse(editorial_rebuild._is_recognized("403 Forbidden\nnginx/1.18"))
    def test_completed_contest_is_readable_while_another_fetch_is_blocked(self):
        source = FixtureEditorialSource(
            ("1700", "9999")
        )
        release_second = threading.Event()
        source.blocked_contests["9999"] = release_second
        source.started_contests["9999"] = threading.Event()
        first_published = threading.Event()
        result: dict[str, object] = {}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "editorials"

            def progress(
                contest_id: str,
                status: ContentStatus,
                completed: int,
                total: int,
            ) -> None:
                if contest_id == "1700" and status is ContentStatus.READY:
                    first_published.set()

            def crawl() -> None:
                result.update(
                    rebuild_editorials(
                        source=source,
                        cache_root=root,
                        delay=0,
                        sleep_fn=lambda _delay: None,
                        progress_callback=progress,
                    )
                )

            thread = threading.Thread(target=crawl)
            thread.start()
            self.assertTrue(source.started_contests["9999"].wait(timeout=5))
            self.assertTrue(first_published.wait(timeout=5))
            store = ContentStore(root, EDITORIAL_CODEC)
            self.assertEqual(store.load_document("1700").content_id, "1700")
            with self.assertRaises(FileNotFoundError):
                store.load_document("9999")
            self.assertFalse((root / "current.json").exists())
            self.assertFalse((root / "generations").exists())
            release_second.set()
            thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertTrue(result["completed"])
            self.assertEqual(store.item_status("9999")["status"], "known_absent")

    def test_incremental_update_bootstraps_empty_store_and_skips_known_items(self):
        initial = FixtureEditorialSource(("1700",))
        successor = FixtureEditorialSource(("1700", "9999"))
        with tempfile.TemporaryDirectory() as directory:
            first = update_editorials(
                source=initial,
                cache_root=directory,
                delay=0,
                sleep_fn=lambda _delay: None,
            )
            second = update_editorials(
                source=successor,
                cache_root=directory,
                delay=0,
                sleep_fn=lambda _delay: None,
            )

            self.assertTrue(first["completed"])
            self.assertTrue(second["completed"])
            self.assertNotIn("1700", successor.fetches)
            self.assertEqual(successor.fetches, ["9999", "9999"])
            store = ContentStore(directory, EDITORIAL_CODEC)
            self.assertEqual(store.load_document("1700").content_id, "1700")
            self.assertEqual(store.item_status("9999")["status"], "known_absent")


    def test_incremental_update_promotes_clicked_contest_into_next_batch(self):
        source = FixtureEditorialSource(tuple(str(contest_id) for contest_id in range(1701, 1710)))
        priority = CrawlPriorityQueue()

        def prioritize_after_first_batch(
            _contest_id: str,
            _status: ContentStatus,
            completed: int,
            _total: int,
        ) -> None:
            if completed == 2:
                priority.prioritize("editorial", "1709")

        with tempfile.TemporaryDirectory() as directory, patch.object(
            editorial_rebuild, "BATCH_SIZE", 2
        ):
            report = update_editorials(
                source=source,
                cache_root=directory,
                delay=0,
                sleep_fn=lambda _delay: None,
                progress_callback=prioritize_after_first_batch,
                priority_selector=lambda remaining: priority.pop_next(
                    "editorial", remaining
                ),
            )

        self.assertTrue(report["completed"])
        self.assertEqual(set(source.fetches[:4]), {"1701", "1702"})
        self.assertEqual(set(source.fetches[4:8]), {"1703", "1709"})

    def test_failed_contest_is_recorded_and_retried(self):
        failing = FixtureEditorialSource(("1700", "9999"))
        failing.transient_contests.add("9999")
        recovered = FixtureEditorialSource(("1700", "9999"))
        with tempfile.TemporaryDirectory() as directory:
            first = update_editorials(
                source=failing,
                cache_root=directory,
                delay=0,
                sleep_fn=lambda _delay: None,
            )
            store = ContentStore(directory, EDITORIAL_CODEC)

            self.assertFalse(first["completed"])
            self.assertEqual(store.load_document("1700").content_id, "1700")
            self.assertEqual(store.item_status("9999")["status"], "transient_failure")

            second = update_editorials(
                source=recovered,
                cache_root=directory,
                delay=0,
                sleep_fn=lambda _delay: None,
            )

            self.assertTrue(second["completed"])
            self.assertEqual(recovered.fetches, ["9999", "9999"])
            self.assertEqual(store.item_status("9999")["status"], "known_absent")

    def test_requested_ready_contest_is_recrawled(self):
        initial = FixtureEditorialSource(("1700",))
        refreshed = FixtureEditorialSource(("1700",))
        with tempfile.TemporaryDirectory() as directory:
            update_editorials(
                source=initial,
                cache_root=directory,
                delay=0,
                sleep_fn=lambda _delay: None,
            )
            update_editorials(
                source=refreshed,
                cache_root=directory,
                requested_contests=["1700"],
                delay=0,
                sleep_fn=lambda _delay: None,
            )

            self.assertEqual(refreshed.fetches, ["1700"])

    def test_referenced_image_survives_incremental_asset_collection(self):
        class ImageSource(FixtureEditorialSource):
            def localize_assets(
                self,
                document: EditorialDocument,
                *,
                asset_root: Path,
            ) -> EditorialBuildResult:
                digest = hashlib.sha256(PNG_PAYLOAD).hexdigest()
                route = f"/editorial-assets/{digest}.png"
                asset_root.mkdir(parents=True, exist_ok=True)
                (asset_root / f"{digest}.png").write_bytes(PNG_PAYLOAD)
                document.root.children.append(
                    Node(kind="image", attrs={"src": route, "alt": "diagram"})
                )
                document.assets.append(route)
                return EditorialBuildResult(ContentStatus.READY, document, {})

        with tempfile.TemporaryDirectory() as directory:
            update_editorials(
                source=ImageSource(("1700",)),
                cache_root=directory,
                delay=0,
                sleep_fn=lambda _delay: None,
            )
            update_editorials(
                source=FixtureEditorialSource(("1700", "9999")),
                cache_root=directory,
                delay=0,
                sleep_fn=lambda _delay: None,
            )
            store = ContentStore(directory, EDITORIAL_CODEC)
            document = store.load_document("1700")
            assert isinstance(document, EditorialDocument)
            name = Path(document.assets[0]).name

            self.assertEqual((store.assets_path / name).read_bytes(), PNG_PAYLOAD)

    def test_validate_1700_preserves_exact_a_through_f_composition(self):
        report = validate_editorial(
            "1700",
            source=FixtureEditorialSource(("1700",)),
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["problemCodes"], list(LIVE_1700_SENTINELS))
        self.assertEqual(report["matchedSentinels"], LIVE_1700_SENTINELS)
        self.assertEqual(report["unresolvedSlots"], [])
        self.assertEqual(report["validationErrors"], [])

    def test_live_source_forwards_direct_asset_root(self):
        document = EditorialDocument(
            contest_id="1700",
            source_url=SOURCE_URL,
            root=Node(kind="document"),
        )
        expected = EditorialBuildResult(ContentStatus.READY, document, {})
        with tempfile.TemporaryDirectory() as directory:
            asset_root = Path(directory) / "assets"
            with patch(
                "editorial_rebuild.cfcrawl.localize_editorial_assets",
                return_value=expected,
            ) as localize:
                actual = editorial_rebuild._LiveEditorialSource().localize_assets(
                    document,
                    asset_root=asset_root,
                )

        self.assertIs(actual, expected)
        localize.assert_called_once_with(document, image_dir=str(asset_root))


if __name__ == "__main__":
    unittest.main()
