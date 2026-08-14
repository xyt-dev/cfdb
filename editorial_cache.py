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
from typing import Any, Callable, Iterable, Mapping, TypeVar
import uuid

from editorial_model import SCHEMA_VERSION, EditorialDocument, canonical_json, validate_document


MANIFEST_SCHEMA = 2
_TERMINAL_STATUSES = {"ready", "known_absent"}
_ALLOWED_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_HEX_CHARS = frozenset("0123456789abcdef")
_MANIFEST_KEYS = {
    "schema",
    "generationId",
    "createdAt",
    "parserVersion",
    "fixtureVersion",
    "expectedContests",
    "contests",
    "counts",
    "finalizedAt",
}
_COUNT_KEYS = {
    "ready",
    "known_absent",
    "transient_failure",
    "invalid_structure",
    "pending",
}
_POINTER_KEYS = {"schema", "generationId", "activatedAt", "manifestSha256"}
_T = TypeVar("_T")


class ContestStatus(str, Enum):
    READY = "ready"
    KNOWN_ABSENT = "known_absent"
    TRANSIENT_FAILURE = "transient_failure"
    INVALID_STRUCTURE = "invalid_structure"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_CHARS for character in value)
    )


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


def _read_canonical_json(path: Path, label: str) -> tuple[Any, bytes]:
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {label} JSON") from error
    if payload != _json_bytes(value):
        raise ValueError(f"{label} JSON is not canonical")
    return value, payload


def _absence_evidence_is_complete(evidence: Any) -> bool:
    required_evidence = {"successfulCheckTimestamps", "contestPageReceipts"}
    if not isinstance(evidence, dict) or not required_evidence.issubset(evidence):
        return False
    timestamps = evidence["successfulCheckTimestamps"]
    receipts = evidence["contestPageReceipts"]
    if not isinstance(timestamps, list) or not isinstance(receipts, list):
        return False
    if len(timestamps) < 2 or len(receipts) < 2:
        return False
    if any(not _is_timestamp(item) for item in timestamps) or len(set(timestamps)) < 2:
        return False

    receipt_timestamps: set[str] = set()
    receipt_keys = {"fetchedAt", "recognized", "editorialFound", "tutorialFound"}
    for receipt in receipts:
        if not isinstance(receipt, dict) or not receipt_keys.issubset(receipt):
            return False
        if receipt["recognized"] is not True:
            return False
        if receipt["editorialFound"] is not False or receipt["tutorialFound"] is not False:
            return False
        fetched_at = receipt["fetchedAt"]
        if not _is_timestamp(fetched_at):
            return False
        receipt_timestamps.add(fetched_at)
    return set(timestamps).issubset(receipt_timestamps)


def _status_entry_is_well_formed(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    status = entry.get("status")
    if status not in {item.value for item in ContestStatus}:
        return False
    evidence = entry.get("evidence")
    if not _is_timestamp(entry.get("updatedAt")) or not isinstance(evidence, dict):
        return False

    common_keys = {"status", "evidence", "updatedAt"}
    if status == ContestStatus.READY.value:
        if set(entry) != common_keys | {"documentPath", "documentSha256"}:
            return False
        return (
            _is_timestamp(evidence.get("validatedAt"))
            and isinstance(entry["documentPath"], str)
            and _is_sha256(entry["documentSha256"])
        )
    if set(entry) != common_keys:
        return False
    if status == ContestStatus.KNOWN_ABSENT.value:
        return _absence_evidence_is_complete(entry["evidence"])
    return bool(entry["evidence"])


def _run_with_writer_lock(
    root: Path,
    lock: RebuildLock | None,
    operation: Callable[[RebuildLock], _T],
) -> _T:
    if lock is None:
        try:
            with RebuildLock(root) as acquired:
                return operation(acquired)
        except FileExistsError as error:
            raise RuntimeError("writer lock is unavailable") from error
    if not isinstance(lock, RebuildLock) or not lock._is_held_for(root):
        raise RuntimeError("caller-held writer lock is invalid")
    return operation(lock)


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
        *,
        lock: RebuildLock | None = None,
    ) -> "GenerationStore":
        root_path = Path(root)
        generation_id = _validate_component(generation_id, "generation ID")
        contests = [_validate_component(item, "contest ID") for item in expected_contests]
        if len(contests) != len(set(contests)):
            raise ValueError("expected contest IDs must be unique")
        contests.sort()
        return _run_with_writer_lock(
            root_path,
            lock,
            lambda held: cls._create_locked(
                root_path,
                generation_id,
                contests,
                parser_version,
                fixture_version,
            ),
        )

    @classmethod
    def _create_locked(
        cls,
        root: Path,
        generation_id: str,
        contests: list[str],
        parser_version: str,
        fixture_version: str,
    ) -> "GenerationStore":
        generations_path = root / "generations"
        generations_created = not generations_path.exists()
        generations_path.mkdir(parents=True, exist_ok=True)
        if generations_created:
            _fsync_directory(root)
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
            "counts": {
                "ready": 0,
                "known_absent": 0,
                "transient_failure": 0,
                "invalid_structure": 0,
                "pending": len(contests),
            },
            "finalizedAt": None,
        }
        store = cls(root, generation_id, manifest)
        atomic_write_json(store.path / "manifest.json", manifest)
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
        manifest, _ = _read_canonical_json(manifest_path, "generation manifest")
        return cls._from_manifest(root_path, generation_id, manifest)

    @classmethod
    def _from_manifest(
        cls,
        root: Path,
        generation_id: str,
        manifest: Any,
    ) -> "GenerationStore":
        if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
            raise ValueError("generation manifest fields are invalid")
        if type(manifest["schema"]) is not int or manifest["schema"] != MANIFEST_SCHEMA:
            raise ValueError("unsupported generation manifest schema")
        if manifest["generationId"] != generation_id:
            raise ValueError("generation manifest ID mismatch")
        if not _is_timestamp(manifest["createdAt"]):
            raise ValueError("invalid generation creation timestamp")
        if not isinstance(manifest["parserVersion"], str) or not isinstance(
            manifest["fixtureVersion"], str
        ):
            raise ValueError("invalid generation version fields")
        expected = manifest["expectedContests"]
        entries = manifest["contests"]
        counts = manifest["counts"]
        finalized_at = manifest["finalizedAt"]
        if not isinstance(expected, list) or not isinstance(entries, dict):
            raise ValueError("invalid generation manifest")
        normalized = [_validate_component(item, "contest ID") for item in expected]
        if normalized != sorted(set(normalized)):
            raise ValueError("expected contest IDs must be sorted and unique")
        if any(contest_id not in normalized for contest_id in entries):
            raise ValueError("manifest contains an unexpected contest")
        if any(not _status_entry_is_well_formed(entry) for entry in entries.values()):
            raise ValueError("manifest contains an invalid contest status")
        if (
            not isinstance(counts, dict)
            or set(counts) != _COUNT_KEYS
            or any(type(value) is not int or value < 0 for value in counts.values())
        ):
            raise ValueError("manifest counts are invalid")
        if finalized_at is not None and not _is_timestamp(finalized_at):
            raise ValueError("invalid generation finalization timestamp")
        store = cls(root, generation_id, manifest)
        if not store._counts_are_valid():
            raise ValueError("manifest counts are inconsistent")
        if finalized_at is not None and not store.is_activation_ready():
            raise ValueError("finalized generation is not activation-ready")
        return store

    def _expected_contests(self) -> list[str]:
        return list(self.manifest["expectedContests"])

    def _document_relative_path(self, contest_id: str) -> str:
        return f"documents/{contest_id}.json"

    def _ensure_mutable(self) -> None:
        persisted, _ = _read_canonical_json(self.path / "manifest.json", "generation manifest")
        persisted_store = self._from_manifest(self.root, self.generation_id, persisted)
        if persisted_store.manifest["finalizedAt"] is not None or self.manifest["finalizedAt"] is not None:
            raise RuntimeError(f"generation is finalized: {self.generation_id}")

    def write_document(
        self,
        document: EditorialDocument,
        *,
        lock: RebuildLock | None = None,
    ) -> str:
        return _run_with_writer_lock(
            self.root,
            lock,
            lambda held: self._write_document_locked(document),
        )

    def _write_document_locked(self, document: EditorialDocument) -> str:
        self._ensure_mutable()
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
        lock: RebuildLock | None = None,
    ) -> None:
        _run_with_writer_lock(
            self.root,
            lock,
            lambda held: self._set_status_locked(
                contest_id,
                status,
                evidence=evidence,
                document_path=document_path,
            ),
        )

    def _set_status_locked(
        self,
        contest_id: str,
        status: ContestStatus | str,
        *,
        evidence: Mapping[str, Any],
        document_path: str | None = None,
    ) -> None:
        self._ensure_mutable()
        contest_id = _validate_component(contest_id, "contest ID")
        if contest_id not in self._expected_contests():
            raise ValueError(f"unexpected contest ID: {contest_id}")
        status = ContestStatus(status)
        expected_path = self._document_relative_path(contest_id)
        document_digest: str | None = None
        if status is ContestStatus.READY:
            if document_path is None:
                document_path = expected_path
            if document_path != expected_path:
                raise ValueError("ready document path must match its contest ID")
            document_bytes = (self.path / document_path).read_bytes()
            document_digest = hashlib.sha256(document_bytes).hexdigest()
        elif document_path is not None:
            raise ValueError("only ready contests may name a document")

        entry: dict[str, Any] = {
            "status": status.value,
            "evidence": deepcopy(dict(evidence)),
            "updatedAt": _utc_now(),
        }
        if document_path is not None:
            entry["documentPath"] = document_path
            entry["documentSha256"] = document_digest
        if not _status_entry_is_well_formed(entry):
            raise ValueError("contest status fields or evidence are incomplete")
        self.manifest["contests"][contest_id] = entry

    def _load_ready_document(
        self,
        contest_id: str,
        entry: dict[str, Any],
    ) -> EditorialDocument:
        relative_path = entry["documentPath"]
        if relative_path != self._document_relative_path(contest_id):
            raise ValueError("ready document path mismatch")
        path = self.path / relative_path
        if path.is_symlink() or not path.is_file():
            raise ValueError("ready document is missing")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry["documentSha256"]:
            raise ValueError("ready document digest mismatch")
        try:
            value = json.loads(payload)
            document = EditorialDocument.from_dict(value)
        except (UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise ValueError("ready document JSON is invalid") from error
        if payload != canonical_json(document).encode("utf-8"):
            raise ValueError("ready document JSON is not canonical")
        if document.contest_id != contest_id or document.schema != SCHEMA_VERSION:
            raise ValueError("ready document identity or schema mismatch")
        errors = validate_document(document, ready=True)
        if errors:
            raise ValueError(errors[0].code)
        return document

    def _ready_entry_is_valid(self, contest_id: str, entry: dict[str, Any]) -> bool:
        try:
            self._load_ready_document(contest_id, entry)
        except (OSError, ValueError, TypeError, KeyError):
            return False
        return True

    def is_activation_ready(self) -> bool:
        expected = self._expected_contests()
        entries = self.manifest.get("contests")
        if not isinstance(entries, dict) or set(entries) != set(expected):
            return False
        for contest_id in expected:
            entry = entries.get(contest_id)
            if not _status_entry_is_well_formed(entry):
                return False
            if not isinstance(entry, dict):
                return False
            status = entry["status"]
            if status not in _TERMINAL_STATUSES:
                return False
            if status == ContestStatus.READY.value and not self._ready_entry_is_valid(
                contest_id, entry
            ):
                return False
        return True

    def _computed_counts(self) -> dict[str, int]:
        entries = self.manifest.get("contests", {})
        counts = {status.value: 0 for status in ContestStatus}
        for entry in entries.values():
            status = entry.get("status") if isinstance(entry, dict) else None
            if status in counts:
                counts[status] += 1
        counts["pending"] = len(self._expected_contests()) - len(entries)
        return counts

    def _counts_are_valid(self) -> bool:
        return self.manifest.get("counts") == self._computed_counts()

    def write_manifest(self, *, lock: RebuildLock | None = None) -> None:
        _run_with_writer_lock(
            self.root,
            lock,
            lambda held: self._write_manifest_locked(),
        )

    def _write_manifest_locked(self) -> None:
        self._ensure_mutable()
        candidate = deepcopy(self.manifest)
        candidate["counts"] = self._computed_counts()
        candidate_store = self.__class__(self.root, self.generation_id, candidate)
        if candidate_store.is_activation_ready():
            candidate["finalizedAt"] = _utc_now()
        self._from_manifest(self.root, self.generation_id, candidate)
        atomic_write_json(self.path / "manifest.json", candidate)
        self.manifest = candidate

    def seed_from(
        self,
        active_generation: "GenerationStore",
        *,
        lock: RebuildLock | None = None,
    ) -> None:
        _run_with_writer_lock(
            self.root,
            lock,
            lambda held: self._seed_from_locked(active_generation),
        )

    def _seed_from_locked(self, active_generation: "GenerationStore") -> None:
        self._ensure_mutable()
        if active_generation.root.resolve() != self.root.resolve():
            raise ValueError("generations must share a root")
        source_entries = active_generation.manifest.get("contests", {})
        for contest_id in self._expected_contests():
            entry = source_entries.get(contest_id)
            if not isinstance(entry, dict) or entry.get("status") != ContestStatus.READY.value:
                continue
            active_generation._load_ready_document(contest_id, entry)
            source = active_generation.path / active_generation._document_relative_path(contest_id)
            target = self.path / self._document_relative_path(contest_id)
            _atomic_link_or_copy(source, target)
            self._set_status_locked(
                contest_id,
                ContestStatus.READY,
                evidence=entry["evidence"],
                document_path=self._document_relative_path(contest_id),
            )

    def load_document(self, contest_id: str) -> EditorialDocument:
        contest_id = _validate_component(contest_id, "contest ID")
        if contest_id not in self._expected_contests():
            raise KeyError(contest_id)
        entry = self.manifest["contests"].get(contest_id)
        if isinstance(entry, dict) and entry.get("status") == ContestStatus.READY.value:
            return self._load_ready_document(contest_id, entry)
        value, _ = _read_canonical_json(
            self.documents_path / f"{contest_id}.json",
            "editorial document",
        )
        document = EditorialDocument.from_dict(value)
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
        pointer, _ = _read_canonical_json(pointer_path, "activation pointer")
    except FileNotFoundError:
        return None
    if not isinstance(pointer, dict):
        raise ValueError("activation pointer must be an object")
    keys = set(pointer)
    if keys != _POINTER_KEYS and keys != _POINTER_KEYS | {"previousGenerationId"}:
        raise ValueError("activation pointer fields are invalid")
    if type(pointer["schema"]) is not int or pointer["schema"] != MANIFEST_SCHEMA:
        raise ValueError("activation pointer schema is invalid")
    generation_id = pointer["generationId"]
    if not isinstance(generation_id, str):
        raise ValueError("activation pointer lacks a generation ID")
    _validate_component(generation_id, "generation ID")
    if not _is_timestamp(pointer["activatedAt"]):
        raise ValueError("activation pointer timestamp is invalid")
    if not _is_sha256(pointer["manifestSha256"]):
        raise ValueError("activation pointer manifest hash is invalid")
    if "previousGenerationId" in pointer:
        previous = pointer["previousGenerationId"]
        if not isinstance(previous, str):
            raise ValueError("activation pointer previous generation is invalid")
        _validate_component(previous, "generation ID")
    return pointer


def activate_generation(
    root: str | os.PathLike[str],
    generation_id: str,
    *,
    lock: RebuildLock | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    return _run_with_writer_lock(
        root_path,
        lock,
        lambda held: _activate_generation_locked(root_path, generation_id),
    )


def _activate_generation_locked(root_path: Path, generation_id: str) -> dict[str, Any]:
    generation_id = _validate_component(generation_id, "generation ID")
    manifest_path = root_path / "generations" / generation_id / "manifest.json"
    manifest, manifest_bytes = _read_canonical_json(manifest_path, "generation manifest")
    store = GenerationStore._from_manifest(root_path, generation_id, manifest)
    if store.manifest["finalizedAt"] is None or not store.is_activation_ready():
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
    manifest, manifest_bytes = _read_canonical_json(manifest_path, "generation manifest")
    if hashlib.sha256(manifest_bytes).hexdigest() != pointer["manifestSha256"]:
        raise ValueError("active generation manifest does not match its pointer")
    store = GenerationStore._from_manifest(root_path, generation_id, manifest)
    if store.manifest["finalizedAt"] is None:
        raise ValueError("active generation is not finalized")
    entry = store.manifest["contests"].get(str(contest_id))
    if not isinstance(entry, dict) or entry.get("status") != ContestStatus.READY.value:
        return None
    try:
        return store._load_ready_document(str(contest_id), entry)
    except ValueError as error:
        raise ValueError(f"active document {error}") from error


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
            marker, _ = _read_canonical_json(self.path, "writer lock")
        except (OSError, ValueError, TypeError):
            return False
        return isinstance(marker, dict) and marker.get("token") == self._token

    def release(self) -> None:
        if self._token is None:
            raise RuntimeError("lock is not held by this object")
        try:
            marker, _ = _read_canonical_json(self.path, "writer lock")
        except (OSError, ValueError, TypeError) as error:
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
