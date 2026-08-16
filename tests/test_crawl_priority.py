from importlib import import_module
import unittest


def priority_queue():
    try:
        module = import_module("crawl_priority")
    except ModuleNotFoundError:
        raise AssertionError("crawl priority scheduler is missing") from None
    return module.CrawlPriorityQueue()


class CrawlPriorityQueueTests(unittest.TestCase):
    def test_latest_click_moves_to_front_without_duplication(self):
        queue = priority_queue()
        queue.prioritize("statement", "1000A")
        queue.prioritize("statement", "1000B")
        queue.prioritize("statement", "1000A")

        remaining = {"1000A", "1000B"}
        self.assertEqual(queue.pop_next("statement", remaining), "1000A")
        remaining.remove("1000A")
        self.assertEqual(queue.pop_next("statement", remaining), "1000B")
        self.assertIsNone(queue.pop_next("statement", set()))

    def test_content_kinds_are_independent_and_stale_items_are_discarded(self):
        queue = priority_queue()
        queue.prioritize("statement", "1000A")
        queue.prioritize("editorial", "1000")

        self.assertIsNone(queue.pop_next("statement", {"1000B"}))
        self.assertEqual(queue.pop_next("editorial", {"1000"}), "1000")

    def test_unknown_content_kind_is_rejected(self):
        queue = priority_queue()

        with self.assertRaisesRegex(ValueError, "invalid-content-kind"):
            queue.prioritize("solution", "1000A")
        with self.assertRaisesRegex(ValueError, "invalid-content-kind"):
            queue.pop_next("solution", {"1000A"})


    def test_enqueue_many_puts_complete_snapshot_at_front_and_exposes_snapshot(self):
        queue = priority_queue()
        queue.prioritize("statement", "old")
        queue.enqueue_many("statement", ["A", "B", "A"])

        self.assertEqual(queue.snapshot("statement"), ["A", "B", "old"])
        self.assertEqual(queue.pop_next("statement", {"A", "B", "old"}), "A")
        self.assertEqual(queue.snapshot("statement"), ["B", "old"])


if __name__ == "__main__":
    unittest.main()
