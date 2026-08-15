from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Callable
import uuid

from content_cache import (  # pyright: ignore[reportMissingImports]
    ContentStatus,
    GenerationStore,
    RebuildLock,
    activate_generation,
    load_active_generation,
)
from content_codecs import STATEMENT_CODEC  # pyright: ignore[reportMissingImports]
from editorial_model import canonical_json
from statement_crawl import (  # pyright: ignore[reportMissingImports]
    LiveStatementSource,
    StatementBuildResult,
    StatementSource,
    fetch_statement_v2,
)
from statement_render import render_statement_html  # pyright: ignore[reportMissingImports]

PARSER_VERSION = "statement-parser-v2"
FIXTURE_VERSION = "statement-fixtures-v2"
DEFAULT_DELAY = 1.5
DEFAULT_CACHE_ROOT = Path(__file__).resolve().parent / "statements" / "v2"
_PROBLEM_CODE_RE = re.compile(r"^\d+[A-Za-z][A-Za-z0-9]*$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _generation_id(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}"


def _expected_problem_codes(source: StatementSource) -> list[str]:
    codes = [str(code) for code in source.problem_codes()]
    if not codes:
        raise ValueError("problem metadata contains no statement IDs")
    if any(_PROBLEM_CODE_RE.fullmatch(code) is None for code in codes):
        raise ValueError("problem metadata contains an invalid statement ID")
    if len(codes) != len(set(codes)):
        raise ValueError("problem metadata contains duplicate statement IDs")
    return sorted(codes)


def _same_generation_snapshot(
    current: GenerationStore | None,
    expected: GenerationStore,
) -> bool:
    return current is not None and current.generation_id == expected.generation_id


def _report(store: GenerationStore, activated: bool) -> dict[str, object]:
    return {
        "generationId": store.generation_id,
        "contentKind": store.manifest["contentKind"],
        "expectedIds": list(store.manifest["expectedIds"]),
        "counts": dict(store.manifest["counts"]),
        "activated": activated,
        "finalizedAt": store.manifest["finalizedAt"],
    }


def _persist_result(
    store: GenerationStore,
    problem_code: str,
    result: StatementBuildResult,
    lock: RebuildLock,
) -> None:
    document_path = None
    evidence = dict(result.evidence)
    if result.status is ContentStatus.READY:
        if result.document is None:
            raise ValueError("ready statement result lacks a document")
        document_path = store.write_document(result.document, lock=lock)
        evidence["validatedAt"] = _utc_now()
    elif not evidence:
        evidence["error"] = "statement build produced no evidence"
    store.set_status(
        problem_code,
        result.status,
        evidence=evidence,
        document_path=document_path,
        lock=lock,
    )
    store.write_manifest(lock=lock)


def _run_statement_generation(
    *,
    source: StatementSource,
    cache_root: str | os.PathLike[str],
    generation_id: str,
    expected_ids: list[str],
    delay: float,
    sleep_fn: Callable[[float], None],
    seed: GenerationStore | None,
    requested_ids: set[str],
    allow_resume: bool,
) -> dict[str, object]:
    root = Path(cache_root)
    with RebuildLock(root) as lock:
        if seed is not None:
            active_snapshot = load_active_generation(root)
            if not _same_generation_snapshot(active_snapshot, seed):
                raise RuntimeError("active statement generation changed before successor creation")
        generation_path = root / "generations" / generation_id
        if generation_path.exists():
            if not allow_resume:
                raise ValueError(f"statement generation already exists: {generation_id}")
            store = GenerationStore.open(root, generation_id)
            if store.manifest["contentKind"] != "statement":
                raise ValueError("resumed generation has the wrong content kind")
            if store.manifest["expectedIds"] != expected_ids:
                raise ValueError("resumed statement generation ID set differs from metadata")
            if store.manifest["parserVersion"] != PARSER_VERSION:
                raise ValueError("resumed statement generation parser version differs")
        else:
            store = GenerationStore.create(
                root,
                generation_id,
                expected_ids,
                STATEMENT_CODEC,
                PARSER_VERSION,
                FIXTURE_VERSION,
                lock=lock,
            )
            if seed is not None and seed.manifest["parserVersion"] == PARSER_VERSION:
                store.seed_from(seed, lock=lock)
                for problem_code in requested_ids:
                    store.manifest["entries"].pop(problem_code, None)

        if store.manifest["finalizedAt"] is not None:
            active_snapshot = load_active_generation(root)
            if active_snapshot is None or active_snapshot.generation_id != generation_id:
                activate_generation(root, generation_id, lock=lock)
            return _report(store, True)

        todo: list[str] = []
        for problem_code in expected_ids:
            entry = store.manifest["entries"].get(problem_code)
            status = entry.get("status") if isinstance(entry, dict) else None
            if status in {ContentStatus.READY.value, ContentStatus.KNOWN_ABSENT.value}:
                if problem_code not in requested_ids:
                    continue
            todo.append(problem_code)

        for index, problem_code in enumerate(todo):
            result = fetch_statement_v2(
                problem_code,
                source=source,
                asset_root=store.path / "assets",
            )
            _persist_result(store, problem_code, result, lock)
            if index + 1 < len(todo):
                sleep_fn(delay)
        if not todo:
            store.write_manifest(lock=lock)

        store = GenerationStore.open(root, generation_id)
        activated = False
        if store.manifest["finalizedAt"] is not None and store.is_activation_ready():
            if seed is not None:
                active_snapshot = load_active_generation(root)
                if not _same_generation_snapshot(active_snapshot, seed):
                    raise RuntimeError("active statement generation changed before successor activation")
            activate_generation(root, generation_id, lock=lock)
            activated = True
        return _report(store, activated)


def validate_statement(
    problem_code: str,
    *,
    source: StatementSource | None = None,
) -> dict[str, object]:
    active_source = source or LiveStatementSource()
    with tempfile.TemporaryDirectory(prefix="cfdb-statement-validation-") as directory:
        result = fetch_statement_v2(
            problem_code,
            source=active_source,
            asset_root=Path(directory) / "assets",
        )
        if result.status is not ContentStatus.READY or result.document is None:
            return {
                "ok": False,
                "problemCode": str(problem_code),
                "status": result.status.value,
                "errors": [dict(result.evidence)],
                "canonicalJsonSha256": None,
            }
        try:
            render_statement_html(result.document)
        except Exception as error:
            return {
                "ok": False,
                "problemCode": str(problem_code),
                "status": ContentStatus.INVALID_STRUCTURE.value,
                "errors": [{"error": f"render-failed:{error}"}],
                "canonicalJsonSha256": None,
            }
        canonical = canonical_json(result.document).encode("utf-8")
        return {
            "ok": True,
            "problemCode": result.document.problem_code,
            "status": result.status.value,
            "sourceKind": result.document.source_kind,
            "assets": list(result.document.assets),
            "errors": [],
            "canonicalJsonSha256": hashlib.sha256(canonical).hexdigest(),
        }


def rebuild_statements(
    *,
    source: StatementSource | None = None,
    cache_root: str | os.PathLike[str] | None = None,
    generation_id: str | None = None,
    delay: float = DEFAULT_DELAY,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    active_source = source or LiveStatementSource()
    expected = _expected_problem_codes(active_source)
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    return _run_statement_generation(
        source=active_source,
        cache_root=root,
        generation_id=generation_id or _generation_id("rebuild"),
        expected_ids=expected,
        delay=delay,
        sleep_fn=sleep_fn,
        seed=None,
        requested_ids=set(),
        allow_resume=True,
    )


def update_statements(
    *,
    source: StatementSource | None = None,
    cache_root: str | os.PathLike[str] | None = None,
    generation_id: str | None = None,
    requested_problems: list[str] | None = None,
    delay: float = DEFAULT_DELAY,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    active_source = source or LiveStatementSource()
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    active = load_active_generation(root)
    if active is None:
        raise ValueError("statement v2 is not initialized")
    if active.manifest["contentKind"] != "statement":
        raise ValueError("active generation is not a statement generation")
    metadata_ids = _expected_problem_codes(active_source)
    expected = sorted(set(active.manifest["expectedIds"]) | set(metadata_ids))
    requested = {str(problem_code) for problem_code in requested_problems or ()}
    if not requested.issubset(expected):
        raise ValueError("requested problem is absent from metadata and active generation")
    known_absent = {
        problem_code
        for problem_code, entry in active.manifest["entries"].items()
        if isinstance(entry, dict)
        and entry.get("status") == ContentStatus.KNOWN_ABSENT.value
    }
    new_ids = set(expected) - set(active.manifest["expectedIds"])
    return _run_statement_generation(
        source=active_source,
        cache_root=root,
        generation_id=generation_id or _generation_id("update"),
        expected_ids=expected,
        delay=delay,
        sleep_fn=sleep_fn,
        seed=active,
        requested_ids=requested | known_absent | new_ids,
        allow_resume=False,
    )


__all__ = [
    "DEFAULT_CACHE_ROOT",
    "rebuild_statements",
    "update_statements",
    "validate_statement",
]
