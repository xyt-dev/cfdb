from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeAlias

from content_cache import (  # pyright: ignore[reportMissingImports]
    MANIFEST_SCHEMA as _MANIFEST_SCHEMA,
    ContentStatus as _ContentStatus,
    GenerationStore as _GenerationStore,
    RebuildLock as _RebuildLock,
    _fsync_directory as _content_fsync_directory,
    activate_generation as _activate_generation,
    atomic_write_json as _atomic_write_json,
    load_active_document as _load_active_document,
    load_active_generation as _load_active_generation,
)
from content_codecs import EDITORIAL_CODEC  # pyright: ignore[reportMissingImports]
from editorial_model import EditorialDocument

ContestStatus: TypeAlias = _ContentStatus
GenerationStore: TypeAlias = _GenerationStore
RebuildLock: TypeAlias = _RebuildLock
MANIFEST_SCHEMA = _MANIFEST_SCHEMA


def activate_generation(
    root: str | os.PathLike[str],
    generation_id: str,
    *,
    lock: RebuildLock | None = None,
) -> dict[str, Any]:
    return _activate_generation(root, generation_id, lock=lock)


def load_active_generation(
    root: str | os.PathLike[str],
) -> GenerationStore | None:
    return _load_active_generation(root)


def load_active_document(
    root: str | os.PathLike[str],
    contest_id: str,
) -> EditorialDocument | None:
    document = _load_active_document(root, contest_id)
    if document is None:
        return None
    if not isinstance(document, EditorialDocument):
        raise ValueError("active editorial document has the wrong content kind")
    return document


def atomic_write_json(target: str | os.PathLike[str], value: Any) -> None:
    _atomic_write_json(target, value)


def _fsync_directory(directory: Path) -> None:
    _content_fsync_directory(directory)


__all__ = [
    "ContestStatus",
    "EDITORIAL_CODEC",
    "GenerationStore",
    "MANIFEST_SCHEMA",
    "RebuildLock",
    "activate_generation",
    "atomic_write_json",
    "load_active_document",
    "load_active_generation",
]
