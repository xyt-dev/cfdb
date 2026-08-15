import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import content_cache  # pyright: ignore[reportMissingImports]

from content_cache import (  # pyright: ignore[reportMissingImports]
    ContentStatus as ContestStatus,
    GenerationStore,
    RebuildLock,
    activate_generation,
    atomic_write_json,
    load_active_document,
)
from editorial_model import EditorialDocument, Node, canonical_json
from content_codecs import EDITORIAL_CODEC  # pyright: ignore[reportMissingImports]


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


def make_document(contest_id="1700", text="ready", asset_route=None):
    children = [Node(kind="paragraph", children=[Node(kind="text", text=text)])]
    assets = []
    if asset_route is not None:
        children.append(Node(kind="image", attrs={"src": asset_route, "alt": "diagram"}))
        assets.append(asset_route)
    return EditorialDocument(
        contest_id=contest_id,
        source_url=f"https://codeforces.com/contest/{contest_id}",
        root=Node(kind="document", children=children),
        assets=assets,
    )


class EditorialCacheTests(unittest.TestCase):
    def create_store(self, root, generation_id, contests):
        return GenerationStore.create(
            root,
            generation_id,
            contests,
            EDITORIAL_CODEC,
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

    def test_create_fsyncs_root_after_adding_generations_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            original = content_cache._fsync_directory

            with RebuildLock(root) as lock, patch(
                "content_cache._fsync_directory",
                side_effect=lambda path: (calls.append(Path(path)), original(Path(path)))[1],
            ):
                GenerationStore.create(
                    root,
                    "g1",
                    ["1700"],
                    EDITORIAL_CODEC,
                    parser_version="parser-1",
                    fixture_version="fixtures-1",
                    lock=lock,
                )

            self.assertIn(root, calls)

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

    def test_validated_active_generation_reuses_pointer_and_manifest_digest_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700"])
            self.ready_contest(store, make_document())
            store.write_manifest()
            activate_generation(root, "g1")

            snapshot = content_cache.load_active_generation(root)
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot.generation_id, "g1")

            pointer_path = root / "current.json"
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            pointer_path.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "canonical"):
                content_cache.load_active_generation(root)

            atomic_write_json(pointer_path, pointer)
            manifest_path = store.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["fixtureVersion"] = "changed-but-valid"
            atomic_write_json(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, "does not match"):
                content_cache.load_active_generation(root)


    def test_loading_active_snapshot_does_not_repeat_full_content_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700"])
            self.ready_contest(store, make_document())
            store.write_manifest()
            activate_generation(root, "g1")

            with patch.object(
                GenerationStore,
                "is_activation_ready",
                side_effect=AssertionError("full validation repeated"),
            ):
                snapshot = content_cache.load_active_generation(root)

            assert snapshot is not None
            self.assertEqual(snapshot.generation_id, "g1")

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
                    "from content_cache import RebuildLock\n"
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

    def test_document_digest_detects_tampering_on_load_and_reactivation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700"])
            self.ready_contest(store, make_document(text="original"))
            store.write_manifest()
            activate_generation(root, "g1")
            manifest_path = store.path / "manifest.json"
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
            document_bytes = (store.path / "documents" / "1700.json").read_bytes()
            pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["finalizedAt"])
            self.assertEqual(
                manifest["entries"]["1700"]["documentSha256"],
                hashlib.sha256(document_bytes).hexdigest(),
            )
            self.assertEqual(
                pointer["manifestSha256"],
                hashlib.sha256(manifest_bytes).hexdigest(),
            )

            atomic_write_json(
                store.path / "documents" / "1700.json",
                make_document(text="tampered").to_dict(),
            )
            with self.assertRaises(ValueError):
                load_active_document(root, "1700")
            with self.assertRaisesRegex(ValueError, "activation-ready"):
                activate_generation(root, "g1")

    def test_finalized_and_retained_generations_reject_all_mutators(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            g1 = self.create_store(root, "g1", ["1700"])
            self.ready_contest(g1, make_document(text="one"))
            g1.write_manifest()
            activate_generation(root, "g1")

            g2 = self.create_store(root, "g2", ["1700"])
            self.ready_contest(g2, make_document(text="two"))
            g2.write_manifest()
            activate_generation(root, "g2")

            operations = [
                lambda: g1.write_document(make_document(text="changed")),
                lambda: g1.set_status(
                    "1700",
                    ContestStatus.TRANSIENT_FAILURE,
                    evidence={"error": "changed"},
                ),
                lambda: g1.seed_from(g2),
                lambda: g1.write_manifest(),
            ]
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(RuntimeError, "finalized"):
                        operation()

            never_activated = self.create_store(root, "g3", ["1700"])
            self.ready_contest(never_activated, make_document(text="three"))
            never_activated.write_manifest()
            with self.assertRaisesRegex(RuntimeError, "finalized"):
                never_activated.write_document(make_document(text="changed"))

    def test_generation_mutations_share_writer_lock_without_nested_deadlock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with RebuildLock(root) as lock:
                store = GenerationStore.create(
                    root,
                    "g1",
                    ["1700"],
                    EDITORIAL_CODEC,
                    parser_version="parser-1",
                    fixture_version="fixtures-1",
                    lock=lock,
                )
                path = store.write_document(make_document(), lock=lock)
                store.set_status(
                    "1700",
                    ContestStatus.READY,
                    evidence={"validatedAt": "2026-08-14T10:00:00Z"},
                    document_path=path,
                    lock=lock,
                )
                store.write_manifest(lock=lock)
                successor = GenerationStore.create(
                    root,
                    "seeded",
                    ["1700", "2000"],
                    EDITORIAL_CODEC,
                    parser_version="parser-1",
                    fixture_version="fixtures-1",
                    lock=lock,
                )
                successor.seed_from(store, lock=lock)
                self.assertEqual(successor.load_document("1700"), make_document())
                activate_generation(root, "g1", lock=lock)

            draft = self.create_store(root, "g2", ["1700"])
            with RebuildLock(root):
                with self.assertRaisesRegex(RuntimeError, "writer lock is unavailable"):
                    draft.write_document(make_document())

    def test_manifest_requires_canonical_bytes_and_exact_top_level_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700"])
            manifest_path = store.path / "manifest.json"
            original = json.loads(manifest_path.read_text(encoding="utf-8"))

            manifest_path.write_text(json.dumps(original, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "canonical"):
                GenerationStore.open(root, "g1")

            variants = []
            missing = dict(original)
            del missing["createdAt"]
            variants.append(missing)
            extra = dict(original)
            extra["unexpected"] = True
            variants.append(extra)
            wrong_type = dict(original)
            wrong_type["parserVersion"] = 1
            variants.append(wrong_type)
            for manifest in variants:
                with self.subTest(manifest=manifest):
                    atomic_write_json(manifest_path, manifest)
                    with self.assertRaises(ValueError):
                        GenerationStore.open(root, "g1")

    def test_manifest_rejects_incomplete_status_fields_evidence_and_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700", "9999"])
            self.ready_contest(store, make_document())
            store.set_status(
                "9999",
                ContestStatus.KNOWN_ABSENT,
                evidence=CHECKED_ABSENCE,
            )
            store.write_manifest()
            manifest_path = store.path / "manifest.json"
            original = json.loads(manifest_path.read_text(encoding="utf-8"))
            ready = original["entries"]["1700"]
            self.assertIn("documentSha256", ready)

            variants = []
            for field in ("documentSha256", "updatedAt", "evidence"):
                variant = json.loads(json.dumps(original))
                del variant["entries"]["1700"][field]
                variants.append(variant)
            invalid_timestamp = json.loads(json.dumps(original))
            invalid_timestamp["entries"]["1700"]["updatedAt"] = "not-a-timestamp"
            variants.append(invalid_timestamp)
            incomplete_evidence = json.loads(json.dumps(original))
            del incomplete_evidence["entries"]["9999"]["evidence"]["contestPageReceipts"]
            variants.append(incomplete_evidence)
            extra_status_field = json.loads(json.dumps(original))
            extra_status_field["entries"]["1700"]["unexpected"] = True
            variants.append(extra_status_field)

            for manifest in variants:
                with self.subTest(manifest=manifest):
                    atomic_write_json(manifest_path, manifest)
                    with self.assertRaises(ValueError):
                        activate_generation(root, "g1")

    def test_pointer_requires_canonical_bytes_schema_hash_timestamp_and_exact_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.create_store(root, "g1", ["1700"])
            self.ready_contest(store, make_document())
            store.write_manifest()
            activate_generation(root, "g1")
            pointer_path = root / "current.json"
            original = json.loads(pointer_path.read_text(encoding="utf-8"))

            pointer_path.write_text(json.dumps(original, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "canonical"):
                load_active_document(root, "1700")

            variants = []
            for field in ("schema", "manifestSha256", "activatedAt"):
                variant = dict(original)
                del variant[field]
                variants.append(variant)
            wrong_schema = dict(original)
            wrong_schema["schema"] = "2"
            variants.append(wrong_schema)
            wrong_hash = dict(original)
            wrong_hash["manifestSha256"] = "not-a-hash"
            variants.append(wrong_hash)
            wrong_timestamp = dict(original)
            wrong_timestamp["activatedAt"] = "not-a-timestamp"
            variants.append(wrong_timestamp)
            extra = dict(original)
            extra["unexpected"] = True
            variants.append(extra)

            for pointer in variants:
                with self.subTest(pointer=pointer):
                    atomic_write_json(pointer_path, pointer)
                    with self.assertRaises(ValueError):
                        load_active_document(root, "1700")

    def test_seed_successor_hardlinks_or_copies_ready_documents_and_assets(self):
        payload = b"\x89PNG\r\n\x1a\nSEEDED_ASSET"
        name = hashlib.sha256(payload).hexdigest() + ".png"
        route = f"/editorial-assets/{name}"
        document = make_document(asset_route=route)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = self.create_store(root, "g1", ["1700", "9999"])
            asset_directory = active.path / "assets"
            asset_directory.mkdir()
            (asset_directory / name).write_bytes(payload)
            self.ready_contest(active, document)
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
            source_asset = active.path / "assets" / name
            seeded_asset = successor.path / "assets" / name
            self.assertEqual(seeded.read_bytes(), source.read_bytes())
            self.assertTrue(
                os.stat(seeded).st_ino == os.stat(source).st_ino
                or seeded.read_bytes() == source.read_bytes()
            )
            self.assertEqual(seeded_asset.read_bytes(), source_asset.read_bytes())
            self.assertTrue(
                os.stat(seeded_asset).st_ino == os.stat(source_asset).st_ino
                or seeded_asset.read_bytes() == source_asset.read_bytes()
            )
            self.assertEqual(successor.load_document("1700"), document)
            manifest = json.loads((successor.path / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["entries"]["1700"]["status"], "ready")
            self.assertNotIn("9999", manifest["entries"])
            self.assertFalse(successor.is_activation_ready())

    def test_activation_rejects_missing_digest_mismatched_or_invalid_magic_assets(self):
        good_payload = b"\x89PNG\r\n\x1a\nGOOD_ASSET"
        cases = (
            ("missing", good_payload, None),
            ("digest-mismatch", good_payload, b"\x89PNG\r\n\x1a\nOTHER_ASSET"),
            ("invalid-magic", b"not-a-png", b"not-a-png"),
        )
        for label, named_payload, stored_payload in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                name = hashlib.sha256(named_payload).hexdigest() + ".png"
                route = f"/editorial-assets/{name}"
                store = self.create_store(root, "g1", ["1700"])
                if stored_payload is not None:
                    asset_directory = store.path / "assets"
                    asset_directory.mkdir()
                    (asset_directory / name).write_bytes(stored_payload)
                self.ready_contest(store, make_document(asset_route=route))
                store.write_manifest()

                self.assertIsNone(store.manifest["finalizedAt"])
                with self.assertRaisesRegex(ValueError, "activation-ready"):
                    activate_generation(root, "g1")


if __name__ == "__main__":
    unittest.main()
