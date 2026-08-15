from __future__ import annotations

from datetime import datetime, timezone
from contextlib import suppress
from enum import Enum
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
from typing import Any, Callable, Mapping, TypeVar
import uuid
from dataclasses import dataclass

from content_asset_policy import (  # pyright: ignore[reportMissingImports]
    asset_identity_from_route,
    asset_magic_is_valid,
)
from content_model import ContentNode, SemanticDocument


STATUS_SCHEMA = 1
_ALLOWED_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_T = TypeVar("_T")


class ContentStatus(str, Enum):
    READY = "ready"
    KNOWN_ABSENT = "known_absent"
    TRANSIENT_FAILURE = "transient_failure"
    INVALID_STRUCTURE = "invalid_structure"


@dataclass(frozen=True, slots=True)
class DocumentCodec:
    content_kind: str
    from_dict: Callable[[dict[str, Any]], SemanticDocument]
    validate: Callable[[SemanticDocument, bool], list[Any]]

    def __post_init__(self) -> None:
        _validate_component(self.content_kind, "content kind")

    def validate_document(self, document: SemanticDocument, *, ready: bool) -> None:
        if document.content_kind != self.content_kind:
            raise ValueError("document content kind does not match store")
        errors = self.validate(document, ready)
        if errors:
            raise ValueError(errors[0].code)


def _codec_for_kind(content_kind: str) -> DocumentCodec:
    from content_codecs import codec_for_kind  # pyright: ignore[reportMissingImports]

    return codec_for_kind(content_kind)


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
        with suppress(FileNotFoundError):
            temporary.unlink()
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


def _process_start_metadata(pid: int) -> str | None:
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        stat = stat_path.read_text(encoding="ascii")
        after_name = stat.rsplit(")", 1)[1].split()
        return f"linux-proc-ticks:{after_name[19]}"
    except (OSError, IndexError, UnicodeError):
        return None


def _boot_id() -> str | None:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return value or None


def _lock_owner_is_live(marker: Any) -> bool:
    if not isinstance(marker, dict):
        return True
    pid = marker.get("pid")
    if type(pid) is not int or pid <= 0:
        return True
    marker_boot_id = marker.get("bootId")
    current_boot_id = _boot_id()
    if marker_boot_id is not None and current_boot_id is not None:
        if marker_boot_id != current_boot_id:
            return False
    expected_start = marker.get("processStart")
    actual_start = _process_start_metadata(pid)
    if isinstance(expected_start, str) and actual_start is not None:
        return expected_start == actual_start
    try:
        os.kill(pid, 0)
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


class ContentWriterLock:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.path = self.root / "crawl.lock"
        self._token: str | None = None

    def acquire(self) -> "ContentWriterLock":
        if self._token is not None:
            raise RuntimeError("lock is already held by this object")
        if self.root.is_symlink():
            raise ValueError("content root must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ValueError("content root is not a directory")
        try:
            reservation = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            try:
                marker, _ = _read_canonical_json(self.path, "writer lock")
            except (OSError, ValueError, TypeError):
                raise
            if _lock_owner_is_live(marker):
                raise
            self.path.unlink()
            _fsync_directory(self.root)
            reservation = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        os.close(reservation)
        token = uuid.uuid4().hex
        marker = {
            "pid": os.getpid(),
            "processStart": _process_start_metadata(os.getpid()),
            "acquiredAt": _utc_now(),
            "hostname": socket.gethostname(),
            "bootId": _boot_id(),
            "token": token,
        }
        try:
            _atomic_write_bytes(self.path, _json_bytes(marker))
        except BaseException:
            with suppress(FileNotFoundError):
                self.path.unlink()
                _fsync_directory(self.root)
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

    def __enter__(self) -> "ContentWriterLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.release()


def _run_with_writer_lock(
    root: Path,
    lock: ContentWriterLock | None,
    operation: Callable[[ContentWriterLock], _T],
) -> _T:
    if lock is None:
        try:
            with ContentWriterLock(root) as acquired:
                return operation(acquired)
        except FileExistsError as error:
            raise RuntimeError("writer lock is unavailable") from error
    if not isinstance(lock, ContentWriterLock) or not lock._is_held_for(root):
        raise RuntimeError("caller-held writer lock is invalid")
    return operation(lock)


class ContentStore:
    def __init__(
        self,
        root: str | os.PathLike[str],
        codec: DocumentCodec | str,
    ):
        self.root = Path(root)
        self.codec = _codec_for_kind(codec) if isinstance(codec, str) else codec
        self.documents_path = self.root / "documents"
        self.assets_path = self.root / "assets"
        self.status_path = self.root / "status"

    @classmethod
    def initialize(
        cls,
        root: str | os.PathLike[str],
        codec: DocumentCodec | str,
        *,
        lock: ContentWriterLock | None = None,
    ) -> "ContentStore":
        store = cls(root, codec)
        _run_with_writer_lock(store.root, lock, lambda held: store._ensure_layout())
        return store

    @property
    def content_kind(self) -> str:
        return self.codec.content_kind

    def _ensure_layout(self) -> None:
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("content root is invalid")
        for directory in (self.documents_path, self.assets_path, self.status_path):
            if directory.is_symlink():
                raise ValueError("content store directory must not be a symlink")
            directory.mkdir(exist_ok=True)
            if not directory.is_dir():
                raise ValueError("content store path is not a directory")
        _fsync_directory(self.root)

    def document_path(self, content_id: str) -> Path:
        content_id = _validate_component(content_id, "content ID")
        return self.documents_path / f"{content_id}.json"

    def status_file(self, content_id: str) -> Path:
        content_id = _validate_component(content_id, "content ID")
        return self.status_path / f"{content_id}.json"

    def _validated_document_asset_names(self, document: SemanticDocument) -> list[str]:
        references: dict[str, tuple[str, str, str]] = {}

        def visit(node: ContentNode) -> None:
            if node.kind in {"image", "attachment"}:
                attribute = "src" if node.kind == "image" else "href"
                route = node.attrs.get(attribute)
                if not isinstance(route, str):
                    raise ValueError("document asset route is invalid")
                identity = asset_identity_from_route(
                    route,
                    content_kind=document.content_kind,
                    resource_kind=node.kind,
                )
                if identity is None:
                    raise ValueError("document asset route is invalid")
                references[route] = (
                    identity.name,
                    identity.digest,
                    identity.extension,
                )
            if node.kind == "spoiler":
                title = node.attrs.get("title")
                if isinstance(title, list):
                    for value in title:
                        if isinstance(value, dict):
                            visit(ContentNode.from_dict(value))
            for child in node.children:
                visit(child)

        visit(document.root)
        if len(document.assets) != len(set(document.assets)):
            raise ValueError("document asset list contains duplicates")
        if set(document.assets) != set(references):
            raise ValueError("document asset list does not match its references")
        if not references:
            return []
        if self.assets_path.is_symlink() or not self.assets_path.is_dir():
            raise ValueError("document asset directory is missing")

        names: list[str] = []
        for route in document.assets:
            name, digest, extension = references[route]
            asset_path = self.assets_path / name
            if asset_path.is_symlink() or not asset_path.is_file():
                raise ValueError("document asset is missing")
            payload = asset_path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError("document asset digest mismatch")
            if not asset_magic_is_valid(extension, payload):
                raise ValueError("document asset magic mismatch")
            names.append(name)
        return names

    def publish(
        self,
        document: SemanticDocument,
        *,
        lock: ContentWriterLock | None = None,
    ) -> str:
        self.codec.validate_document(document, ready=True)
        content_id = _validate_component(document.content_id, "content ID")
        self._validated_document_asset_names(document)

        def write(held: ContentWriterLock) -> str:
            self._ensure_layout()
            self._validated_document_asset_names(document)
            target = self.document_path(content_id)
            _atomic_write_bytes(target, _json_bytes(document.to_dict()))
            marker = self.status_file(content_id)
            with suppress(FileNotFoundError):
                marker.unlink()
                _fsync_directory(self.status_path)
            return f"documents/{content_id}.json"

        return _run_with_writer_lock(self.root, lock, write)

    def record_status(
        self,
        content_id: str,
        status: ContentStatus | str,
        *,
        evidence: Mapping[str, Any],
        lock: ContentWriterLock | None = None,
    ) -> None:
        content_id = _validate_component(content_id, "content ID")
        status = ContentStatus(status)
        if status is ContentStatus.READY:
            raise ValueError("ready content must be published as a document")
        evidence_value = dict(evidence)
        if not evidence_value:
            raise ValueError("content status evidence is required")
        marker = {
            "schema": STATUS_SCHEMA,
            "contentKind": self.content_kind,
            "contentId": content_id,
            "status": status.value,
            "evidence": evidence_value,
            "updatedAt": _utc_now(),
        }

        def write(held: ContentWriterLock) -> None:
            self._ensure_layout()
            atomic_write_json(self.status_file(content_id), marker)
            if status is ContentStatus.KNOWN_ABSENT:
                with suppress(FileNotFoundError):
                    self.document_path(content_id).unlink()
                    _fsync_directory(self.documents_path)

        _run_with_writer_lock(self.root, lock, write)

    def _read_status(self, content_id: str) -> dict[str, Any] | None:
        path = self.status_file(content_id)
        if path.is_symlink():
            raise ValueError("content status must not be a symlink")
        if not path.is_file():
            return None
        value, _ = _read_canonical_json(path, "content status")
        required = {
            "schema",
            "contentKind",
            "contentId",
            "status",
            "evidence",
            "updatedAt",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("content status fields are invalid")
        if value["schema"] != STATUS_SCHEMA:
            raise ValueError("unsupported content status schema")
        if value["contentKind"] != self.content_kind or value["contentId"] != content_id:
            raise ValueError("content status identity mismatch")
        try:
            status = ContentStatus(value["status"])
        except (TypeError, ValueError) as error:
            raise ValueError("invalid content status") from error
        if status is ContentStatus.READY:
            raise ValueError("ready status must be represented by a document")
        if not isinstance(value["evidence"], dict) or not value["evidence"]:
            raise ValueError("content status evidence is invalid")
        if not _is_timestamp(value["updatedAt"]):
            raise ValueError("content status timestamp is invalid")
        return value

    def recorded_status(self, content_id: str) -> dict[str, Any] | None:
        content_id = _validate_component(content_id, "content ID")
        return self._read_status(content_id)

    def load_document(self, content_id: str) -> SemanticDocument:
        content_id = _validate_component(content_id, "content ID")
        path = self.document_path(content_id)
        if path.is_symlink():
            raise ValueError("content document must not be a symlink")
        if not path.is_file():
            raise FileNotFoundError(content_id)
        value, _ = _read_canonical_json(path, "content document")
        if not isinstance(value, dict):
            raise ValueError("content document JSON must be an object")
        document = self.codec.from_dict(value)
        if document.content_id != content_id or document.content_kind != self.content_kind:
            raise ValueError("document content identity mismatch")
        self.codec.validate_document(document, ready=True)
        self._validated_document_asset_names(document)
        return document

    def item_status(self, content_id: str) -> dict[str, Any]:
        content_id = _validate_component(content_id, "content ID")
        path = self.document_path(content_id)
        if path.exists() or path.is_symlink():
            try:
                self.load_document(content_id)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                return {
                    "status": ContentStatus.INVALID_STRUCTURE.value,
                    "evidence": {"error": str(error)},
                }
            return {"status": ContentStatus.READY.value, "evidence": {}}
        try:
            marker = self._read_status(content_id)
        except (OSError, ValueError, TypeError, KeyError) as error:
            return {
                "status": ContentStatus.INVALID_STRUCTURE.value,
                "evidence": {"error": str(error)},
            }
        if marker is not None:
            return marker
        return {"status": "pending", "evidence": {}}

    def document_ids(self) -> set[str]:
        if not self.documents_path.exists():
            return set()
        if self.documents_path.is_symlink() or not self.documents_path.is_dir():
            raise ValueError("content document directory is invalid")
        content_ids: set[str] = set()
        for path in self.documents_path.iterdir():
            if path.suffix != ".json" or path.is_symlink() or not path.is_file():
                continue
            try:
                content_ids.add(_validate_component(path.stem, "content ID"))
            except ValueError:
                continue
        return content_ids

    def ready_ids(self) -> set[str]:
        ready: set[str] = set()
        for content_id in self.document_ids():
            try:
                self.load_document(content_id)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                continue
            ready.add(content_id)
        return ready

    def status_counts(self, expected_ids: list[str] | set[str]) -> dict[str, int]:
        counts = {
            ContentStatus.READY.value: 0,
            ContentStatus.KNOWN_ABSENT.value: 0,
            ContentStatus.TRANSIENT_FAILURE.value: 0,
            ContentStatus.INVALID_STRUCTURE.value: 0,
            "pending": 0,
        }
        for content_id in expected_ids:
            status = self.item_status(content_id)["status"]
            counts[status] = counts.get(status, 0) + 1
        return counts

    def garbage_collect_assets(
        self,
        *,
        lock: ContentWriterLock | None = None,
    ) -> dict[str, int]:
        def collect(held: ContentWriterLock) -> dict[str, int]:
            self._ensure_layout()
            referenced: set[str] = set()
            for content_id in self.ready_ids():
                document = self.load_document(content_id)
                referenced.update(self._validated_document_asset_names(document))
            removed_files = 0
            removed_bytes = 0
            for path in self.assets_path.iterdir():
                if path.name in referenced:
                    continue
                if path.is_symlink() or path.is_file():
                    try:
                        removed_size = path.lstat().st_size
                    except OSError:
                        removed_size = 0
                    removed_bytes += removed_size
                    path.unlink()
                    removed_files += 1
            if removed_files:
                _fsync_directory(self.assets_path)
            return {"removedFiles": removed_files, "removedBytes": removed_bytes}

        return _run_with_writer_lock(self.root, lock, collect)


__all__ = [
    "ContentStatus",
    "ContentStore",
    "ContentWriterLock",
    "DocumentCodec",
    "atomic_write_json",
]
