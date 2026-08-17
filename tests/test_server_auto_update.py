from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from content_cache import ContentStatus
import server


def report(content_kind: str, *, completed: bool = True) -> dict[str, object]:
    failed = 0 if completed else 1
    return {
        "contentKind": content_kind,
        "expectedCount": 1,
        "attemptedCount": 1,
        "publishedCount": 1 if completed else 0,
        "knownAbsentCount": 0,
        "failedCount": failed,
        "statusCounts": {
            "ready": 1 if completed else 0,
            "known_absent": 0,
            "transient_failure": failed,
            "invalid_structure": 0,
            "pending": 0,
        },
        "completed": completed,
        "assetGc": {"removedFiles": 0, "removedBytes": 0},
    }


class ServerAutoUpdateTests(unittest.TestCase):
    def test_empty_statement_and_editorial_roots_start_concurrently(self):
        barrier = threading.Barrier(2)
        calls: list[tuple[str, Path]] = []

        def updater(content_kind: str):
            def crawl(*, cache_root: Path, progress_callback, priority_selector):
                calls.append((content_kind, Path(cache_root)))
                barrier.wait(timeout=5)
                progress_callback(
                    "1700A" if content_kind == "statement" else "1700",
                    ContentStatus.READY,
                    1,
                    1,
                )
                return report(content_kind)

            return crawl

        with tempfile.TemporaryDirectory() as directory:
            statement_root = Path(directory) / "statements"
            editorial_root = Path(directory) / "editorials"
            completed_process = subprocess.CompletedProcess([], 0, b"ok", b"")
            with patch.object(server, "STATEMENT_V2_ROOT", statement_root), patch.object(
                server,
                "EDITORIAL_V2_ROOT",
                editorial_root,
            ), patch.object(
                server,
                "update_statements",
                updater("statement"),
            ), patch.object(
                server,
                "update_editorials",
                updater("editorial"),
            ), patch.object(
                server.subprocess,
                "run",
                return_value=completed_process,
            ), patch.object(server, "_reload_problems"):
                server.auto_update()

            self.assertCountEqual(
                calls,
                [
                    ("statement", statement_root),
                    ("editorial", editorial_root),
                ],
            )
            self.assertEqual(server.crawl_state["stage"], "done")
            self.assertEqual(
                server.crawl_state["contentStatus"],
                {"statement": "complete", "editorial": "complete"},
            )
            self.assertEqual(server.crawl_state["done"], 2)
            self.assertEqual(server.crawl_state["total"], 2)
            self.assertFalse((statement_root / "current.json").exists())
            self.assertFalse((editorial_root / "current.json").exists())

    def test_one_crawler_failure_does_not_stop_the_other(self):
        def fail(**_kwargs):
            raise RuntimeError("statement failed")

        def succeed(*, cache_root: Path, progress_callback, priority_selector):
            progress_callback("1700", ContentStatus.READY, 1, 1)
            return report("editorial")

        with tempfile.TemporaryDirectory() as directory:
            completed_process = subprocess.CompletedProcess([], 0, b"ok", b"")
            with patch.object(server, "STATEMENT_V2_ROOT", Path(directory) / "s"), patch.object(
                server,
                "EDITORIAL_V2_ROOT",
                Path(directory) / "e",
            ), patch.object(server, "update_statements", fail), patch.object(
                server,
                "update_editorials",
                succeed,
            ), patch.object(
                server.subprocess,
                "run",
                return_value=completed_process,
            ), patch.object(server, "_reload_problems"):
                server.auto_update()

        self.assertEqual(server.crawl_state["stage"], "error")
        self.assertEqual(server.crawl_state["error"], "auto-update failed: statement")
        self.assertEqual(server.crawl_state["contentStatus"]["statement"], "error")
        self.assertEqual(server.crawl_state["contentStatus"]["editorial"], "complete")
        self.assertIn("statement failed", server.crawl_state["content"]["statement"]["error"])

    def test_startup_waits_for_active_manual_operation(self):
        with patch.object(
            server,
            "_acquire_crawl_operation",
            side_effect=[False, True],
        ) as acquire, patch("server.time.sleep") as sleep, patch.object(
            server, "_auto_update"
        ) as update, patch.object(server, "_finish_crawl_operation") as finish:
            self.assertTrue(server.auto_update())

        self.assertEqual(acquire.call_count, 2)
        acquire.assert_called_with(defer_rebuild=False)
        sleep.assert_called_once_with(0.1)
        update.assert_called_once_with()
        finish.assert_called_once_with()

    def test_metadata_failure_stops_content_crawls_without_creating_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            statement_root = Path(directory) / "s"
            editorial_root = Path(directory) / "e"
            failed_process = subprocess.CompletedProcess([], 1, b"", b"metadata failed")
            with patch.object(server, "STATEMENT_V2_ROOT", statement_root), patch.object(
                server,
                "EDITORIAL_V2_ROOT",
                editorial_root,
            ), patch.object(server, "update_statements") as statements, patch.object(
                server,
                "update_editorials",
            ) as editorials, patch.object(
                server.subprocess,
                "run",
                return_value=failed_process,
            ):
                server.auto_update()

            statements.assert_not_called()
            editorials.assert_not_called()
            self.assertEqual(server.crawl_state["stage"], "error")
            self.assertFalse(statement_root.exists())
            self.assertFalse(editorial_root.exists())


if __name__ == "__main__":
    unittest.main()
