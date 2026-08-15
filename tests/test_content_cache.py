from importlib import import_module
import json
from pathlib import Path
import tempfile
import unittest

from editorial_model import EditorialDocument, Node


content_cache = import_module("content_cache")
content_codecs = import_module("content_codecs")
ContentStatus = content_cache.ContentStatus
GenerationStore = content_cache.GenerationStore
activate_generation = content_cache.activate_generation
load_active_document = content_cache.load_active_document
STATEMENT_CODEC = content_codecs.STATEMENT_CODEC
EDITORIAL_CODEC = content_codecs.EDITORIAL_CODEC
StatementDocument = import_module("statement_model").StatementDocument

VALIDATED_AT = "2026-08-15T00:00:00.000000Z"


def make_statement() -> object:
    return StatementDocument(
        problem_code="1700A",
        contest_id="1700",
        index="A",
        source_url="https://codeforces.com/contest/1700/problem/A",
        source_kind="html",
        root=Node(
            kind="document",
            children=[Node(kind="paragraph", children=[Node(kind="text", text="statement")])],
        ),
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


def mark_ready(store, document) -> None:
    document_path = store.write_document(document)
    store.set_status(
        document.content_id,
        ContentStatus.READY,
        evidence={"validatedAt": VALIDATED_AT},
        document_path=document_path,
    )
    store.write_manifest()


class ContentCacheTests(unittest.TestCase):
    def test_statement_and_editorial_roots_activate_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            statement_root = Path(directory) / "statements"
            editorial_root = Path(directory) / "editorials"
            statement_store = GenerationStore.create(
                statement_root,
                "s1",
                ["1700A"],
                STATEMENT_CODEC,
                "parser-v2",
                "fixtures-v2",
            )
            editorial_store = GenerationStore.create(
                editorial_root,
                "e1",
                ["1700"],
                EDITORIAL_CODEC,
                "parser-v2",
                "fixtures-v2",
            )

            mark_ready(statement_store, make_statement())

            self.assertTrue(statement_store.is_activation_ready())
            self.assertFalse(editorial_store.is_activation_ready())
            activate_generation(statement_root, "s1")
            self.assertTrue((statement_root / "current.json").is_file())
            self.assertFalse((editorial_root / "current.json").exists())

    def test_store_rejects_wrong_document_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GenerationStore.create(
                directory,
                "s1",
                ["1700A"],
                STATEMENT_CODEC,
                "parser-v2",
                "fixtures-v2",
            )

            with self.assertRaisesRegex(ValueError, "content kind"):
                store.write_document(make_editorial())

    def test_manifest_uses_generic_content_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GenerationStore.create(
                directory,
                "s1",
                ["1700A"],
                STATEMENT_CODEC,
                "parser-v2",
                "fixtures-v2",
            )

            manifest = json.loads((store.path / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(manifest["contentKind"], "statement")
            self.assertEqual(manifest["expectedIds"], ["1700A"])
            self.assertEqual(manifest["entries"], {})
            self.assertNotIn("expectedContests", manifest)
            self.assertNotIn("contests", manifest)

    def test_statement_document_round_trip_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GenerationStore.create(
                directory,
                "s1",
                ["1700A"],
                STATEMENT_CODEC,
                "parser-v2",
                "fixtures-v2",
            )
            mark_ready(store, make_statement())
            activate_generation(directory, "s1")

            loaded = load_active_document(directory, "1700A")

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.content_kind, "statement")
            self.assertEqual(loaded.content_id, "1700A")
            document_path = store.path / "documents" / "1700A.json"
            document_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_active_document(directory, "1700A")


if __name__ == "__main__":
    unittest.main()
