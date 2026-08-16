import io
import json
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, patch

import server


def handler_for(
    path: str,
    body: bytes,
    *,
    content_type: str = "application/json",
) -> Any:
    handler = cast(Any, server.Handler.__new__(server.Handler))
    handler.path = path
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


class ServerPriorityTests(unittest.TestCase):
    def test_priority_post_enqueues_exact_item_and_starts_or_joins_crawl(self):
        metadata = [
            {"contestId": 1605, "index": "E"},
            {"contestId": 1605, "index": "A"},
        ]
        for operation in ("started", "already_running"):
            for kind, content_id in (("statement", "1605E"), ("editorial", "1605")):
                with self.subTest(operation=operation, kind=kind):
                    queue = Mock()
                    handler = handler_for(
                        "/api/prioritize",
                        json.dumps(
                            {"kind": kind, "contentId": content_id}
                        ).encode(),
                    )
                    with patch.object(server, "PROBLEMS", metadata), patch.object(
                        server, "_crawl_priority", queue, create=True
                    ), patch.object(
                        server, "start_rebuild", return_value=operation
                    ) as start:
                        handler.do_POST()

                    queue.prioritize.assert_called_once_with(kind, content_id)
                    start.assert_called_once_with()
                    code, payload = sent_response(handler)
                    self.assertEqual(code, 202)
                    self.assertEqual(
                        payload,
                        {
                            "ok": True,
                            "status": "accepted",
                            "operation": operation,
                        },
                    )

    def test_priority_post_rejects_invalid_requests_without_mutation(self):
        cases = (
            (b'{"kind":"solution","contentId":"1605E"}', "invalid_priority"),
            (b'{"kind":"statement","contentId":"1605F"}', "invalid_priority"),
            (b'{"kind":"editorial","contentId":"9999"}', "invalid_priority"),
            (b'{"kind":"statement","contentId":"1605E","kind":"editorial"}', "invalid_priority"),
            (b"not-json", "invalid_priority"),
            (b'{"kind":"statement","contentId":"1605E"}', "unsupported_media_type"),
        )
        for body, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                content_type = "text/plain" if expected_status == "unsupported_media_type" else "application/json"
                handler = handler_for(
                    "/api/prioritize", body, content_type=content_type
                )
                queue = Mock()
                with patch.object(
                    server, "PROBLEMS", [{"contestId": 1605, "index": "E"}]
                ), patch.object(
                    server, "_crawl_priority", queue, create=True
                ), patch.object(server, "start_rebuild") as start:
                    handler.do_POST()

                queue.prioritize.assert_not_called()
                start.assert_not_called()
                code, payload = sent_response(handler)
                self.assertEqual(code, 415 if expected_status == "unsupported_media_type" else 400)
                self.assertEqual(payload["status"], expected_status)
                self.assertFalse(payload["ok"])

    def test_priority_get_never_mutates_or_starts_crawl(self):
        handler = handler_for("/api/prioritize", b"")
        queue = Mock()
        with patch.object(server, "_crawl_priority", queue, create=True), patch.object(
            server, "start_rebuild"
        ) as start:
            handler.do_GET()

        queue.prioritize.assert_not_called()
        start.assert_not_called()
        self.assertEqual(handler._send.call_args.args[0], 404)

    def test_content_update_receives_kind_priority_selector(self):
        queue = Mock()
        queue.pop_next.return_value = "1605E"
        calls: list[tuple[str, str | None]] = []

        def updater(*, cache_root: Path, progress_callback, priority_selector):
            calls.append((str(cache_root), priority_selector({"1605E"})))
            return {
                "contentKind": "statement",
                "statusCounts": {},
                "failedCount": 0,
                "completed": True,
            }

        root = Path("/tmp/cfdb-priority-test")
        with patch.object(server, "_crawl_priority", queue, create=True), patch.object(
            server, "_record_content_update"
        ):
            server._update_content("statement", root, updater)

        self.assertEqual(calls, [(str(root), "1605E")])
        queue.pop_next.assert_called_once_with("statement", {"1605E"})


    def test_rebuild_preview_labels_items_and_uses_queue_order(self):
        queue = Mock()
        queue.snapshot.side_effect = lambda kind: (
            ["1605D"] if kind == "statement" else []
        )
        problems = [
            {"id": "1605E", "contestId": 1605, "index": "E", "name": "Array Equalizer"},
            {"id": "1605D", "contestId": 1605, "index": "D", "name": "Another Problem"},
        ]
        with patch.object(server, "PROBLEMS", problems), patch.object(
            server, "pending_statement_ids", return_value=["1605E", "1605D"]
        ), patch.object(
            server, "pending_editorial_ids", return_value=["1605"]
        ), patch.object(server, "_crawl_priority", queue):
            preview = server._build_rebuild_preview()

        self.assertEqual(
            preview["statements"],
            {
                "count": 2,
                "items": [
                    {"id": "1605D", "label": "1605D — Another Problem"},
                    {"id": "1605E", "label": "1605E — Array Equalizer"},
                ],
            },
        )
        self.assertEqual(
            preview["editorials"],
            {
                "count": 1,
                "items": [{"id": "1605", "label": "Contest 1605"}],
            },
        )


    def test_rebuild_preview_is_read_only_and_returns_two_groups(self):
        preview = {
            "ok": True,
            "status": "ready",
            "statements": {
                "count": 2,
                "items": [{"id": "1605E", "label": "1605E — Array Equalizer"}],
            },
            "editorials": {
                "count": 1,
                "items": [{"id": "1605", "label": "Contest 1605"}],
            },
        }
        handler = handler_for("/api/rebuild/preview", b"")
        queue = Mock()
        with patch.object(server, "_build_rebuild_preview", return_value=preview), patch.object(
            server, "_crawl_priority", queue, create=True
        ), patch.object(server, "start_rebuild") as start:
            handler.do_GET()

        code, payload = sent_response(handler)
        self.assertEqual(code, 200)
        self.assertEqual(payload, preview)
        queue.prioritize.assert_not_called()
        start.assert_not_called()

    def test_confirmed_rebuild_enqueues_complete_preview_snapshot(self):
        preview = {
            "ok": True,
            "status": "ready",
            "statements": {
                "count": 2,
                "items": [
                    {"id": "1605E", "label": "1605E — Array Equalizer"},
                    {"id": "1605D", "label": "1605D — Another Problem"},
                ],
            },
            "editorials": {
                "count": 1,
                "items": [{"id": "1605", "label": "Contest 1605"}],
            },
        }
        handler = handler_for("/api/rebuild", b'{"confirm":true}')
        queue = Mock()
        with patch.object(server, "_build_rebuild_preview", return_value=preview), patch.object(
            server, "_crawl_priority", queue, create=True
        ), patch.object(server, "start_rebuild", return_value="started"):
            handler.do_POST()

        queue.enqueue_many.assert_any_call("statement", ["1605E", "1605D"])
        queue.enqueue_many.assert_any_call("editorial", ["1605"])
        code, payload = sent_response(handler)
        self.assertEqual(code, 202)
        self.assertEqual(payload["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
