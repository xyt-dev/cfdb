import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from editorial_cache import (
    ContestStatus,
    GenerationStore,
    RebuildLock,
    activate_generation,
    atomic_write_json,
    load_active_document,
)
from editorial_model import EditorialDocument, Node, canonical_json


CHECKED_ABSENCE = {
    "successfulCheckTimestamps": [
        "2026-08-14T10:00:00Z",
        "2026-08-14T10:00:10Z",
    ],
    "contestPageReceipts": [
        {
            "fetchedAt": "2026-08-14T10:00:00Z",
            "recognized": True,
            "editorialFound": False,
            "tutorialFound": False,
        },
        {
            "fetchedAt": "2026-08-14T10:00:10Z",
            "recognized": True,
            "editorialFound": False,
            "tutorialFound": False,
        },
    ],
}


def make_document(contest_id="1700", text="ready"):
    return EditorialDocument(
        contest_id=contest_id,
        source_url=f"https://codeforces.com/contest/{contest_id}",
        root=Node(
            kind="document",
            children=[Node(kind="paragraph", children=[Node(kind="text", text=text)])],
        ),
    )


class EditorialCacheTests(unittest.TestCase):
    def create_store(self, root, generation_id, contests):
        return GenerationStore.create(
            root,
            generation_id,
            contests,
            parser_version="parser-1",
            fixture_version="fixtures-1",
        )

    def ready_contest(self, store, document):
        path = store.write_document(document)
        store.set_status(
            document.contest_id,
            ContestStatus.READY,
            evidence={"validatedAt": "2026-08-14T10:00:00Z"},
            document_path=path,
        )

    def test_document_write_round_trips_canonical_json(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.create_store(Path(directory), "g1", ["1700"])
            document = make_document(text="Zażółć gęślą jaźń")

            document_path = store.write_document(document)
            self.assertEqual(document_path, "documents/1700.json")
            self.assertEqual(
                (store.path / document_path).read_text(encoding="utf-8"),
                canonical_json(document),
            )
            self.assertEqual(store.load_document("1700"), document)

            unsupported = make_document()
            unsupported.schema = 999
            with self.assertRaisesRegex(ValueError, "unsupported document schema"):
                store.write_document(unsupported)

    def test_incomplete_generation_cannot_activate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700", "9999"])
            self.ready_contest(store, make_document())
            store.write_manifest()

            self.assertFalse(store.is_activation_ready())
            with self.assertRaises(ValueError):
                activate_generation(root, "g1")
            self.assertFalse((root / "current.json").exists())

    def test_only_ready_and_known_absent_are_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700", "9999"])
            self.ready_contest(store, make_document())

            store.set_status(
                "9999",
                ContestStatus.TRANSIENT_FAILURE,
                evidence={"error": "timeout"},
            )
            self.assertFalse(store.is_activation_ready())
            store.set_status(
                "9999",
                ContestStatus.INVALID_STRUCTURE,
                evidence={"diagnostic": "no-island"},
            )
            self.assertFalse(store.is_activation_ready())
            store.set_status(
                "9999",
                ContestStatus.KNOWN_ABSENT,
                evidence=CHECKED_ABSENCE,
            )
            self.assertTrue(store.is_activation_ready())

    def test_activation_pointer_switch_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            g1 = self.create_store(root, "g1", ["1700", "9999"])
            self.ready_contest(g1, make_document(text="generation one"))
            g1.set_status(
                "9999",
                ContestStatus.KNOWN_ABSENT,
                evidence=CHECKED_ABSENCE,
            )
            g1.write_manifest()
            activate_generation(root, "g1")
            self.assertEqual(
                json.loads((root / "current.json").read_text(encoding="utf-8"))["generationId"],
                "g1",
            )

            g2 = self.create_store(root, "g2", ["1700", "9999"])
            self.ready_contest(g2, make_document(text="generation two"))
            g2.set_status(
                "9999",
                ContestStatus.KNOWN_ABSENT,
                evidence=CHECKED_ABSENCE,
            )
            g2.write_manifest()
            activate_generation(root, "g2")
            self.assertEqual(load_active_document(root, "1700"), make_document(text="generation two"))

            activate_generation(root, "g1")
            pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
            self.assertEqual(pointer["generationId"], "g1")
            self.assertEqual(pointer["previousGenerationId"], "g2")
            self.assertEqual(load_active_document(root, "1700"), make_document(text="generation one"))

    def test_activation_rejects_inconsistent_manifest_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700"])
            self.ready_contest(store, make_document())
            store.write_manifest()
            manifest_path = store.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["counts"]["ready"] = 0
            atomic_write_json(manifest_path, manifest)

            with self.assertRaises(ValueError):
                activate_generation(root, "g1")
            self.assertFalse((root / "current.json").exists())

    def test_active_pointer_detects_manifest_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700"])
            self.ready_contest(store, make_document())
            store.write_manifest()
            activate_generation(root, "g1")
            manifest_path = store.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["unexpectedRewrite"] = True
            atomic_write_json(manifest_path, manifest)

            with self.assertRaises(ValueError):
                load_active_document(root, "1700")

    def test_failed_atomic_write_leaves_previous_document_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "document.json"
            atomic_write_json(target, {"generation": 1})
            previous = target.read_bytes()

            with self.assertRaises(TypeError):
                atomic_write_json(target, {"generation": object()})

            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_rebuild_lock_excludes_second_process_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with RebuildLock(root):
                marker = json.loads((root / "rebuild.lock").read_text(encoding="utf-8"))
                self.assertEqual(marker["pid"], os.getpid())
                self.assertTrue(marker["processStart"])
                code = (
                    "from editorial_cache import RebuildLock\n"
                    "from pathlib import Path\n"
                    "import sys\n"
                    "try:\n"
                    "    RebuildLock(Path(sys.argv[1])).acquire()\n"
                    "except FileExistsError:\n"
                    "    raise SystemExit(0)\n"
                    "raise SystemExit(1)\n"
                )
                result = subprocess.run(
                    [sys.executable, "-c", code, str(root)],
                    cwd=Path(__file__).resolve().parents[1],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "rebuild.lock").exists())

    def test_activation_auto_lock_excludes_another_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700"])
            self.ready_contest(store, make_document())
            store.write_manifest()

            with RebuildLock(root):
                with self.assertRaisesRegex(RuntimeError, "writer lock is unavailable"):
                    activate_generation(root, "g1")
            self.assertFalse((root / "current.json").exists())

    def test_activation_accepts_valid_caller_held_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700"])
            self.ready_contest(store, make_document())
            store.write_manifest()

            with RebuildLock(root) as lock:
                activate_generation(root, "g1", lock=lock)
            self.assertEqual(
                json.loads((root / "current.json").read_text(encoding="utf-8"))["generationId"],
                "g1",
            )

    def test_activation_rejects_unheld_or_wrong_root_lock(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700"])
            self.ready_contest(store, make_document())
            store.write_manifest()

            with self.assertRaisesRegex(RuntimeError, "caller-held writer lock is invalid"):
                activate_generation(root, "g1", lock=RebuildLock(root))
            with RebuildLock(Path(other)) as wrong_lock:
                with self.assertRaisesRegex(RuntimeError, "caller-held writer lock is invalid"):
                    activate_generation(root, "g1", lock=wrong_lock)
            self.assertFalse((root / "current.json").exists())

    def test_seed_successor_hardlinks_or_copies_ready_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = self.create_store(root, "g1", ["1700", "9999"])
            self.ready_contest(active, make_document())
            active.set_status(
                "9999",
                ContestStatus.KNOWN_ABSENT,
                evidence=CHECKED_ABSENCE,
            )
            active.write_manifest()

            successor = self.create_store(root, "g2", ["1700", "9999", "2000"])
            successor.seed_from(active)
            successor.write_manifest()

            source = active.path / "documents" / "1700.json"
            seeded = successor.path / "documents" / "1700.json"
            self.assertEqual(seeded.read_bytes(), source.read_bytes())
            self.assertTrue(
                os.stat(seeded).st_ino == os.stat(source).st_ino
                or seeded.read_bytes() == source.read_bytes()
            )
            self.assertEqual(successor.load_document("1700"), make_document())
            manifest = json.loads((successor.path / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["contests"]["1700"]["status"], "ready")
            self.assertNotIn("9999", manifest["contests"])
            self.assertFalse(successor.is_activation_ready())


if __name__ == "__main__":
    unittest.main()
