import io
import json
import threading
import unittest
import copy
from pathlib import Path
from unittest.mock import Mock, patch
from typing import Any, cast

import server
from content_cache import ContentStatus


def report(content_kind: str) -> dict:
    return {
        "contentKind": content_kind,
        "expectedCount": 1,
        "attemptedCount": 1,
        "publishedCount": 1,
        "knownAbsentCount": 0,
        "failedCount": 0,
        "statusCounts": {
            "ready": 1,
            "known_absent": 0,
            "transient_failure": 0,
            "invalid_structure": 0,
            "pending": 0,
        },
        "completed": True,
        "assetGc": {"removedFiles": 0, "removedBytes": 0},
    }


def handler_for(body: bytes, *, content_type: str = "application/json") -> Any:
    handler = cast(Any, server.Handler.__new__(server.Handler))
    handler.path = "/api/rebuild"
    handler.headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
    }
    handler.rfile = io.BytesIO(body)
    handler._send = Mock()
    return handler


def sent_response(handler) -> tuple[int, dict]:
    code, body, content_type = handler._send.call_args.args[:3]
    assert content_type == "application/json"
    return code, json.loads(body)


class ServerRebuildTests(unittest.TestCase):
    def test_post_rebuild_accepts_started_and_active_incremental_crawls(self):
        for operation in ("started", "already_running"):
            with self.subTest(operation=operation):
                handler = handler_for(b'{"confirm":true}')
                with patch.object(server, "start_rebuild", return_value=operation) as start:
                    handler.do_POST()
                start.assert_called_once_with()
                code, payload = sent_response(handler)
                self.assertEqual(code, 202)
                self.assertEqual(
                    payload,
                    {"ok": True, "status": "accepted", "operation": operation},
                )

    def test_post_rebuild_requires_small_json_confirmation(self):
        cases = (
            (b'{"confirm":false}', "application/json", 400),
            (b"{}", "application/json", 400),
            (b"not-json", "application/json", 400),
            (b'{"confirm":1}', "application/json", 400),
            (b'{"confirm":1.0}', "application/json", 400),
            (b'{"confirm":false,"confirm":true}', "application/json", 400),
            (b'{"confirm":true}', "text/plain", 415),
            (b"x" * 257, "application/json", 413),
        )
        for body, content_type, expected_code in cases:
            with self.subTest(content_type=content_type, expected_code=expected_code):
                handler = handler_for(body, content_type=content_type)
                with patch.object(server, "start_rebuild") as start:
                    handler.do_POST()
                start.assert_not_called()
                code, payload = sent_response(handler)
                self.assertEqual(code, expected_code)
                self.assertFalse(payload["ok"])

    def test_get_rebuild_never_starts_mutation(self):
        handler = handler_for(b"")
        with patch.object(server, "start_rebuild") as start:
            handler.do_GET()
        start.assert_not_called()
        self.assertEqual(handler._send.call_args.args[0], 404)

    def test_rebuild_content_runs_both_kinds_concurrently(self):
        barrier = threading.Barrier(2)
        calls: list[tuple[str, Path]] = []

        def updater(content_kind: str):
            def run(*, cache_root: Path, progress_callback, priority_selector):
                calls.append((content_kind, cache_root))
                barrier.wait(timeout=2)
                progress_callback("1000A" if content_kind == "statement" else "1000", ContentStatus.READY, 1, 1)
                return report(content_kind)

            return run

        statement_root = Path("/tmp/cfdb-statement-rebuild-test")
        editorial_root = Path("/tmp/cfdb-editorial-rebuild-test")
        with patch.object(server, "STATEMENT_V2_ROOT", statement_root), patch.object(
            server, "EDITORIAL_V2_ROOT", editorial_root
        ), patch.object(server, "update_statements", updater("statement")), patch.object(
            server, "update_editorials", updater("editorial")
        ):
            getattr(server, "_rebuild_content")()

        self.assertCountEqual(
            calls,
            [("statement", statement_root), ("editorial", editorial_root)],
        )
        self.assertEqual(server.crawl_state["stage"], "done")
        self.assertEqual(
            server.crawl_state["contentStatus"],
            {"statement": "complete", "editorial": "complete"},
        )

    def test_rebuild_content_exposes_crawler_exception(self):
        def fail(**_kwargs):
            raise RuntimeError("statement failed")

        def succeed(*, cache_root: Path, progress_callback, priority_selector):
            progress_callback("1000", ContentStatus.READY, 1, 1)
            return report("editorial")

        with patch.object(server, "update_statements", fail), patch.object(
            server, "update_editorials", succeed
        ):
            getattr(server, "_rebuild_content")()

        self.assertEqual(server.crawl_state["stage"], "error")
        self.assertGreaterEqual(server.crawl_state["failed"], 1)
        self.assertEqual(server.crawl_state["contentStatus"]["statement"], "error")
        self.assertIn("statement failed", server.crawl_state["content"]["statement"]["error"])

    def test_progress_snapshot_is_detached_from_live_state(self):
        with server._crawl_state_lock:
            original = copy.deepcopy(server.crawl_state)
            server.crawl_state["content"] = {"statement": {"done": 1}}
        try:
            snapshot = getattr(server, "_progress_snapshot")()
            snapshot["content"]["statement"]["done"] = 99
            self.assertEqual(server.crawl_state["content"]["statement"]["done"], 1)
        finally:
            with server._crawl_state_lock:
                server.crawl_state.clear()
                server.crawl_state.update(original)

    def test_start_rebuild_coalesces_active_operation_and_starts_daemon(self):
        busy_lock = Mock()
        busy_lock.acquire.return_value = False
        with patch.object(server, "_crawl_operation_lock", busy_lock), patch.object(
            server.threading, "Thread"
        ) as thread:
            self.assertEqual(getattr(server, "start_rebuild")(), "already_running")
        thread.assert_not_called()

        available_lock = Mock()
        available_lock.acquire.return_value = True
        worker = Mock()
        with patch.object(server, "_crawl_operation_lock", available_lock), patch.object(
            server.threading, "Thread", return_value=worker
        ) as thread:
            self.assertEqual(getattr(server, "start_rebuild")(), "started")
        thread.assert_called_once_with(target=getattr(server, "_rebuild_worker"), daemon=True)
        worker.start.assert_called_once_with()

    def test_start_rebuild_marks_progress_busy_before_worker_starts(self):
        lock = Mock()
        lock.acquire.return_value = True
        worker = Mock()
        with server._crawl_state_lock:
            original = copy.deepcopy(server.crawl_state)
            server.crawl_state.clear()
            server.crawl_state.update(stage="idle")

        def observe_start():
            self.assertEqual(server._progress_snapshot()["stage"], "content")

        worker.start.side_effect = observe_start
        try:
            with patch.object(server, "_crawl_operation_lock", lock), patch.object(
                server.threading, "Thread", return_value=worker
            ):
                self.assertEqual(getattr(server, "start_rebuild")(), "started")
        finally:
            with server._crawl_state_lock:
                server.crawl_state.clear()
                server.crawl_state.update(original)

    def test_start_rebuild_releases_lock_when_thread_start_fails(self):
        lock = Mock()
        lock.acquire.return_value = True
        worker = Mock()
        worker.start.side_effect = RuntimeError("thread start failed")
        with server._crawl_state_lock:
            original = copy.deepcopy(server.crawl_state)
            server.crawl_state.clear()
            server.crawl_state.update(stage="idle")
        try:
            with patch.object(server, "_crawl_operation_lock", lock), patch.object(
                server.threading, "Thread", return_value=worker
            ):
                with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                    getattr(server, "start_rebuild")()
            self.assertEqual(server._progress_snapshot()["stage"], "error")
            self.assertEqual(
                server._progress_snapshot()["error"], "thread start failed"
            )
        finally:
            with server._crawl_state_lock:
                server.crawl_state.clear()
                server.crawl_state.update(original)
        lock.release.assert_called_once_with()

    def test_rebuild_worker_releases_lock_on_success_and_failure(self):
        for error in (None, RuntimeError("worker failed")):
            with self.subTest(error=error):
                lock = Mock()
                with patch.object(server, "_crawl_operation_lock", lock), patch.object(
                    server, "_rebuild_content", side_effect=error
                ):
                    if error is None:
                        getattr(server, "_rebuild_worker")()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "worker failed"):
                            getattr(server, "_rebuild_worker")()
                lock.release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
