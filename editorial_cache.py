from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
from typing import Any, Iterable, Mapping
import uuid

from editorial_model import SCHEMA_VERSION, EditorialDocument, canonical_json, validate_document


MANIFEST_SCHEMA = 2
_TERMINAL_STATUSES = {"ready", "known_absent"}
_ALLOWED_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


class ContestStatus(str, Enum):
    READY = "ready"
    KNOWN_ABSENT = "known_absent"
    TRANSIENT_FAILURE = "transient_failure"
    INVALID_STRUCTURE = "invalid_structure"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_component(value: str, label: str) -> str:
    value = str(value)
    if (
        not value
        or value in {".", ".."}
        or any(character not in _ALLOWED_NAME_CHARS for character in value)
    ):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(directory, flags)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EINVAL, errno.ENOTSUP}:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in {errno.EBADF, errno.EINVAL, errno.ENOTSUP}:
                raise
    finally:
        os.close(descriptor)


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(target: str | os.PathLike[str], value: Any) -> None:
    """Atomically replace *target* with canonical UTF-8 JSON."""
    _atomic_write_bytes(Path(target), _json_bytes(value))


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def _absence_evidence_is_complete(evidence: Any) -> bool:
    if not isinstance(evidence, dict):
        return False
    timestamps = evidence.get("successfulCheckTimestamps")
    receipts = evidence.get("contestPageReceipts")
    if not isinstance(timestamps, list) or not isinstance(receipts, list):
        return False
    if len(timestamps) < 2 or len(receipts) < 2:
        return False
    if any(not isinstance(item, str) or not item for item in timestamps):
        return False
    if len(set(timestamps)) < 2:
        return False

    receipt_timestamps: set[str] = set()
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("recognized") is not True:
            return False
        fetched_at = receipt.get("fetchedAt")
        if not isinstance(fetched_at, str) or not fetched_at:
            return False
        if receipt.get("editorialFound") is not False:
            return False
        if receipt.get("tutorialFound") is not False:
            return False
        receipt_timestamps.add(fetched_at)
    return set(timestamps).issubset(receipt_timestamps)


class GenerationStore:
    def __init__(self, root: Path, generation_id: str, manifest: dict[str, Any]):
        self.root = Path(root)
        self.generation_id = _validate_component(generation_id, "generation ID")
        self.path = self.root / "generations" / self.generation_id
        self.documents_path = self.path / "documents"
        self.manifest = manifest

    @classmethod
    def create(
        cls,
        root: str | os.PathLike[str],
        generation_id: str,
        expected_contests: Iterable[str],
        parser_version: str,
        fixture_version: str,
    ) -> "GenerationStore":
        root_path = Path(root)
        generation_id = _validate_component(generation_id, "generation ID")
        contests = [_validate_component(item, "contest ID") for item in expected_contests]
        if len(contests) != len(set(contests)):
            raise ValueError("expected contest IDs must be unique")
        contests.sort()

        generations_path = root_path / "generations"
        generations_path.mkdir(parents=True, exist_ok=True)
        generation_path = generations_path / generation_id
        generation_path.mkdir()
        documents_path = generation_path / "documents"
        documents_path.mkdir()
        _fsync_directory(generation_path)
        _fsync_directory(generations_path)

        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "generationId": generation_id,
            "createdAt": _utc_now(),
            "parserVersion": str(parser_version),
            "fixtureVersion": str(fixture_version),
            "expectedContests": contests,
            "contests": {},
            "counts": {},
        }
        store = cls(root_path, generation_id, manifest)
        store.write_manifest()
        return store

    @classmethod
    def open(
        cls,
        root: str | os.PathLike[str],
        generation_id: str,
    ) -> "GenerationStore":
        root_path = Path(root)
        generation_id = _validate_component(generation_id, "generation ID")
        manifest_path = root_path / "generations" / generation_id / "manifest.json"
        return cls._from_manifest(root_path, generation_id, _read_json(manifest_path))

    @classmethod
    def _from_manifest(
        cls,
        root: Path,
        generation_id: str,
        manifest: Any,
    ) -> "GenerationStore":
        if not isinstance(manifest, dict):
            raise ValueError("generation manifest must be an object")
        if manifest.get("schema") != MANIFEST_SCHEMA:
            raise ValueError("unsupported generation manifest schema")
        if manifest.get("generationId") != generation_id:
            raise ValueError("generation manifest ID mismatch")
        expected = manifest.get("expectedContests")
        entries = manifest.get("contests")
        if not isinstance(expected, list) or not isinstance(entries, dict):
            raise ValueError("invalid generation manifest")
        normalized = [_validate_component(item, "contest ID") for item in expected]
        if normalized != sorted(set(normalized)):
            raise ValueError("expected contest IDs must be sorted and unique")
        if any(contest_id not in normalized for contest_id in entries):
            raise ValueError("manifest contains an unexpected contest")
        return cls(root, generation_id, manifest)

    def _expected_contests(self) -> list[str]:
        return list(self.manifest["expectedContests"])

    def _document_relative_path(self, contest_id: str) -> str:
        return f"documents/{contest_id}.json"

    def write_document(self, document: EditorialDocument) -> str:
        contest_id = _validate_component(document.contest_id, "contest ID")
        if contest_id not in self._expected_contests():
            raise ValueError(f"unexpected contest ID: {contest_id}")
        if document.schema != SCHEMA_VERSION:
            raise ValueError("unsupported document schema")
        errors = validate_document(document, ready=True)
        if errors:
            raise ValueError(errors[0].code)
        relative_path = self._document_relative_path(contest_id)
        _atomic_write_bytes(self.path / relative_path, canonical_json(document).encode("utf-8"))
        return relative_path

    def set_status(
        self,
        contest_id: str,
        status: ContestStatus | str,
        *,
        evidence: Mapping[str, Any],
        document_path: str | None = None,
    ) -> None:
        contest_id = _validate_component(contest_id, "contest ID")
        if contest_id not in self._expected_contests():
            raise ValueError(f"unexpected contest ID: {contest_id}")
        status = ContestStatus(status)
        expected_path = self._document_relative_path(contest_id)
        if status is ContestStatus.READY:
            if document_path is None:
                document_path = expected_path
            if document_path != expected_path:
                raise ValueError("ready document path must match its contest ID")
        elif document_path is not None:
            raise ValueError("only ready contests may name a document")

        entry: dict[str, Any] = {
            "status": status.value,
            "evidence": deepcopy(dict(evidence)),
            "updatedAt": _utc_now(),
        }
        if document_path is not None:
            entry["documentPath"] = document_path
        _json_bytes(entry)
        self.manifest["contests"][contest_id] = entry

    def _ready_entry_is_valid(self, contest_id: str, entry: dict[str, Any]) -> bool:
        relative_path = entry.get("documentPath")
        if (
            not isinstance(relative_path, str)
            or relative_path != self._document_relative_path(contest_id)
        ):
            return False
        path = self.path / relative_path
        if path.is_symlink() or not path.is_file():
            return False
        try:
            payload = path.read_text(encoding="utf-8")
            document = EditorialDocument.from_dict(json.loads(payload))
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False
        return (
            document.contest_id == contest_id
            and document.schema == SCHEMA_VERSION
            and not validate_document(document, ready=True)
            and payload == canonical_json(document)
        )

    def is_activation_ready(self) -> bool:
        expected = self._expected_contests()
        entries = self.manifest.get("contests")
        if not isinstance(entries, dict) or set(entries) != set(expected):
            return False
        for contest_id in expected:
            entry = entries.get(contest_id)
            if not isinstance(entry, dict):
                return False
            status = entry.get("status")
            if status not in _TERMINAL_STATUSES:
                return False
            if status == ContestStatus.READY.value:
                if not self._ready_entry_is_valid(contest_id, entry):
                    return False
            elif not _absence_evidence_is_complete(entry.get("evidence")):
                return False
        return True

    def _counts_are_valid(self) -> bool:
        entries = self.manifest.get("contests", {})
        actual = {status.value: 0 for status in ContestStatus}
        for entry in entries.values():
            status = entry.get("status") if isinstance(entry, dict) else None
            if status in actual:
                actual[status] += 1
        actual["pending"] = len(self._expected_contests()) - len(entries)
        return self.manifest.get("counts") == actual

    def write_manifest(self) -> None:
        entries = self.manifest.get("contests", {})
        counts = {status.value: 0 for status in ContestStatus}
        for entry in entries.values():
            status = entry.get("status") if isinstance(entry, dict) else None
            if status in counts:
                counts[status] += 1
        counts["pending"] = len(self._expected_contests()) - len(entries)
        self.manifest["counts"] = counts
        atomic_write_json(self.path / "manifest.json", self.manifest)

    def seed_from(self, active_generation: "GenerationStore") -> None:
        if active_generation.root.resolve() != self.root.resolve():
            raise ValueError("generations must share a root")
        source_entries = active_generation.manifest.get("contests", {})
        for contest_id in self._expected_contests():
            entry = source_entries.get(contest_id)
            if not isinstance(entry, dict) or entry.get("status") != ContestStatus.READY.value:
                continue
            if not active_generation._ready_entry_is_valid(contest_id, entry):
                raise ValueError(f"active ready document is invalid: {contest_id}")
            source = active_generation.path / active_generation._document_relative_path(contest_id)
            target = self.path / self._document_relative_path(contest_id)
            _atomic_link_or_copy(source, target)
            self.set_status(
                contest_id,
                ContestStatus.READY,
                evidence=entry.get("evidence", {}),
                document_path=self._document_relative_path(contest_id),
            )

    def load_document(self, contest_id: str) -> EditorialDocument:
        contest_id = _validate_component(contest_id, "contest ID")
        if contest_id not in self._expected_contests():
            raise KeyError(contest_id)
        path = self.documents_path / f"{contest_id}.json"
        payload = _read_json(path)
        document = EditorialDocument.from_dict(payload)
        if document.contest_id != contest_id:
            raise ValueError("document contest ID mismatch")
        return document


def _atomic_link_or_copy(source: Path, target: Path) -> None:
    temporary = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        try:
            os.link(source, temporary)
        except OSError as error:
            if error.errno not in {
                errno.EXDEV,
                errno.EPERM,
                errno.EACCES,
                errno.ENOSYS,
                errno.EOPNOTSUPP,
            }:
                raise
            _atomic_write_bytes(target, source.read_bytes())
            return
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_pointer(root: Path) -> dict[str, Any] | None:
    pointer_path = root / "current.json"
    try:
        pointer = _read_json(pointer_path)
    except FileNotFoundError:
        return None
    if not isinstance(pointer, dict):
        raise ValueError("activation pointer must be an object")
    generation_id = pointer.get("generationId")
    if not isinstance(generation_id, str):
        raise ValueError("activation pointer lacks a generation ID")
    _validate_component(generation_id, "generation ID")
    return pointer


def activate_generation(
    root: str | os.PathLike[str],
    generation_id: str,
    *,
    lock: RebuildLock | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    if lock is None:
        try:
            with RebuildLock(root_path):
                return _activate_generation_locked(root_path, generation_id)
        except FileExistsError as error:
            raise RuntimeError("writer lock is unavailable") from error
    if not isinstance(lock, RebuildLock) or not lock._is_held_for(root_path):
        raise RuntimeError("caller-held writer lock is invalid")
    return _activate_generation_locked(root_path, generation_id)


def _activate_generation_locked(root_path: Path, generation_id: str) -> dict[str, Any]:
    generation_id = _validate_component(generation_id, "generation ID")
    manifest_path = root_path / "generations" / generation_id / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid generation manifest JSON") from error
    store = GenerationStore._from_manifest(root_path, generation_id, manifest)
    if not store.is_activation_ready() or not store._counts_are_valid():
        raise ValueError(f"generation is not activation-ready: {generation_id}")
    previous_pointer = _read_pointer(root_path)
    pointer: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "generationId": generation_id,
        "activatedAt": _utc_now(),
        "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }
    if previous_pointer is not None:
        pointer["previousGenerationId"] = previous_pointer["generationId"]
    atomic_write_json(root_path / "current.json", pointer)
    return pointer


def load_active_document(
    root: str | os.PathLike[str],
    contest_id: str,
) -> EditorialDocument | None:
    root_path = Path(root)
    pointer = _read_pointer(root_path)
    if pointer is None:
        return None
    generation_id = pointer["generationId"]
    manifest_path = root_path / "generations" / generation_id / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    expected_hash = pointer.get("manifestSha256")
    if (
        not isinstance(expected_hash, str)
        or hashlib.sha256(manifest_bytes).hexdigest() != expected_hash
    ):
        raise ValueError("active generation manifest does not match its pointer")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid active generation manifest JSON") from error
    store = GenerationStore._from_manifest(root_path, generation_id, manifest)
    entry = store.manifest["contests"].get(str(contest_id))
    if not isinstance(entry, dict) or entry.get("status") != ContestStatus.READY.value:
        return None
    if not store._ready_entry_is_valid(str(contest_id), entry):
        raise ValueError(f"active document is invalid: {contest_id}")
    return store.load_document(str(contest_id))


def _process_start_metadata(pid: int) -> str:
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        stat = stat_path.read_text(encoding="ascii")
        after_name = stat.rsplit(")", 1)[1].split()
        return f"linux-proc-ticks:{after_name[19]}"
    except (OSError, IndexError, UnicodeError):
        return f"acquired-at:{_utc_now()}"


def _boot_id() -> str | None:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return value or None


class RebuildLock:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.path = self.root / "rebuild.lock"
        self._token: str | None = None

    def acquire(self) -> "RebuildLock":
        if self._token is not None:
            raise RuntimeError("lock is already held by this object")
        self.root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        marker = {
            "pid": os.getpid(),
            "processStart": _process_start_metadata(os.getpid()),
            "acquiredAt": _utc_now(),
            "hostname": socket.gethostname(),
            "bootId": _boot_id(),
            "token": token,
        }
        reservation = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(reservation)
        try:
            # The exclusive empty reservation blocks contenders while the complete
            # marker follows the same temporary-file replacement protocol as JSON.
            _atomic_write_bytes(self.path, _json_bytes(marker))
        except BaseException:
            try:
                self.path.unlink()
                _fsync_directory(self.root)
            except FileNotFoundError:
                pass
            raise
        self._token = token
        return self

    def _is_held_for(self, root: Path) -> bool:
        if self._token is None or self.root.resolve() != root.resolve():
            return False
        try:
            marker = _read_json(self.path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        return isinstance(marker, dict) and marker.get("token") == self._token

    def release(self) -> None:
        if self._token is None:
            raise RuntimeError("lock is not held by this object")
        try:
            marker = _read_json(self.path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("lock ownership is ambiguous; refusing recovery") from error
        if not isinstance(marker, dict) or marker.get("token") != self._token:
            raise RuntimeError("lock ownership is ambiguous; refusing recovery")
        self.path.unlink()
        _fsync_directory(self.root)
        self._token = None

    def __enter__(self) -> "RebuildLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.release()
