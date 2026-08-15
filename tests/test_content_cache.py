from importlib import import_module
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from editorial_model import EditorialDocument, Node


content_cache = import_module("content_cache")
content_codecs = import_module("content_codecs")
ContentStatus = content_cache.ContentStatus
ContentStore = content_cache.ContentStore
ContentWriterLock = content_cache.ContentWriterLock
STATEMENT_CODEC = content_codecs.STATEMENT_CODEC
EDITORIAL_CODEC = content_codecs.EDITORIAL_CODEC
StatementDocument = import_module("statement_model").StatementDocument


def make_statement(
    *,
    problem_code: str = "1700A",
    contest_id: str = "1700",
    index: str = "A",
    text: str = "statement",
    assets: list[str] | None = None,
    root: Node | None = None,
) -> object:
    return StatementDocument(
        problem_code=problem_code,
        contest_id=contest_id,
        index=index,
        source_url=f"https://codeforces.com/contest/{contest_id}/problem/{index}",
        source_kind="html",
        root=root
        or Node(
            kind="document",
            children=[Node(kind="paragraph", children=[Node(kind="text", text=text)])],
        ),
        assets=list(assets or []),
    )


def make_editorial() -> EditorialDocument:
    return EditorialDocument(
        contest_id="1700",
        source_url="https://codeforces.com/blog/entry/1",
        root=Node(
            kind="document",
            children=[Node(kind="paragraph", children=[Node(kind="text", text="editorial")])],
        ),
    )


class ContentCacheTests(unittest.TestCase):
    def test_published_document_is_immediately_readable_without_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "statements"
            store = ContentStore.initialize(root, STATEMENT_CODEC)

            relative_path = store.publish(make_statement())

            self.assertEqual(relative_path, "documents/1700A.json")
            self.assertEqual(store.load_document("1700A").content_id, "1700A")
            self.assertEqual(store.item_status("1700A")["status"], "ready")
            self.assertFalse((root / "current.json").exists())
            self.assertFalse((root / "generations").exists())
            self.assertFalse((root / "manifest.json").exists())

    def test_statement_and_editorial_roots_publish_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            statement_store = ContentStore.initialize(
                Path(directory) / "statements",
                STATEMENT_CODEC,
            )
            editorial_store = ContentStore.initialize(
                Path(directory) / "editorials",
                EDITORIAL_CODEC,
            )

            statement_store.publish(make_statement())

            self.assertEqual(statement_store.ready_ids(), {"1700A"})
            self.assertEqual(editorial_store.ready_ids(), set())

    def test_store_rejects_wrong_document_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContentStore.initialize(directory, STATEMENT_CODEC)

            with self.assertRaisesRegex(ValueError, "content kind"):
                store.publish(make_editorial())

    def test_numeric_problem_index_is_a_valid_exact_content_id(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContentStore.initialize(directory, STATEMENT_CODEC)

            store.publish(
                make_statement(problem_code="92101", contest_id="921", index="01")
            )

            document = store.load_document("92101")
            self.assertEqual(document.content_id, "92101")
            self.assertEqual(document.index, "01")

    def test_item_status_does_not_gate_a_valid_document(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContentStore.initialize(directory, STATEMENT_CODEC)
            store.record_status(
                "1700A",
                ContentStatus.TRANSIENT_FAILURE,
                evidence={"error": "temporary"},
            )
            self.assertEqual(store.item_status("1700A")["status"], "transient_failure")

            store.publish(make_statement())

            self.assertEqual(store.item_status("1700A")["status"], "ready")
            self.assertEqual(store.load_document("1700A").content_id, "1700A")

    def test_known_absent_removes_stale_document(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContentStore.initialize(directory, STATEMENT_CODEC)
            store.publish(make_statement())

            store.record_status(
                "1700A",
                ContentStatus.KNOWN_ABSENT,
                evidence={"recognized": False},
            )

            self.assertEqual(store.item_status("1700A")["status"], "known_absent")
            with self.assertRaises(FileNotFoundError):
                store.load_document("1700A")

    def test_missing_or_tampered_asset_prevents_publication_and_reads(self):
        payload = b"\x89PNG\r\n\x1a\ncontent"
        digest = hashlib.sha256(payload).hexdigest()
        route = f"/statement-assets/{digest}.png"
        root_node = Node(
            kind="document",
            children=[Node(kind="image", attrs={"src": route, "alt": "diagram"})],
        )
        document = make_statement(assets=[route], root=root_node)

        with tempfile.TemporaryDirectory() as directory:
            store = ContentStore.initialize(directory, STATEMENT_CODEC)
            with self.assertRaisesRegex(ValueError, "asset is missing"):
                store.publish(document)

            asset_path = store.assets_path / f"{digest}.png"
            asset_path.write_bytes(payload)
            store.publish(document)
            self.assertEqual(store.load_document("1700A").content_id, "1700A")

            asset_path.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "asset digest mismatch"):
                store.load_document("1700A")

    def test_failed_atomic_replacement_keeps_previous_document(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContentStore.initialize(directory, STATEMENT_CODEC)
            store.publish(make_statement(text="old"))
            target = store.document_path("1700A")
            previous = target.read_bytes()

            with patch("content_cache.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    store.publish(make_statement(text="new"))

            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(store.load_document("1700A").content_id, "1700A")

    def test_tampered_document_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContentStore.initialize(directory, STATEMENT_CODEC)
            store.publish(make_statement())
            store.document_path("1700A").write_text("{}", encoding="utf-8")

            self.assertEqual(store.ready_ids(), set())
            self.assertEqual(store.item_status("1700A")["status"], "invalid_structure")
            with self.assertRaises(ValueError):
                store.load_document("1700A")

    def test_writer_lock_excludes_another_writer_for_the_same_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "statements"
            with ContentWriterLock(root) as lock:
                store = ContentStore.initialize(root, STATEMENT_CODEC, lock=lock)
                store.publish(make_statement(), lock=lock)
                with self.assertRaisesRegex(RuntimeError, "writer lock is unavailable"):
                    ContentStore.initialize(root, STATEMENT_CODEC)

            self.assertEqual(
                ContentStore(root, STATEMENT_CODEC).load_document("1700A").content_id,
                "1700A",
            )

    def test_caller_held_lock_is_rejected_for_another_root(self):
        with tempfile.TemporaryDirectory() as directory:
            first_root = Path(directory) / "first"
            second_root = Path(directory) / "second"
            second_store = ContentStore.initialize(second_root, STATEMENT_CODEC)
            with ContentWriterLock(first_root) as lock:
                with self.assertRaisesRegex(RuntimeError, "caller-held writer lock"):
                    second_store.publish(make_statement(), lock=lock)


if __name__ == "__main__":
    unittest.main()
