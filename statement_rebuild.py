from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Callable

from content_cache import (  # pyright: ignore[reportMissingImports]
    ContentStatus,
    ContentStore,
    ContentWriterLock,
)
from content_codecs import STATEMENT_CODEC  # pyright: ignore[reportMissingImports]
from editorial_model import canonical_json
from statement_crawl import (  # pyright: ignore[reportMissingImports]
    LiveStatementSource,
    ProblemIdentity,
    StatementBuildResult,
    StatementSource,
    fetch_statement_v2,
    source_problem_identities,
)
from statement_render import render_statement_html  # pyright: ignore[reportMissingImports]
from statement_model import StatementDocument  # pyright: ignore[reportMissingImports]

PARSER_VERSION = "statement-parser-v2"
FIXTURE_VERSION = "statement-fixtures-v2"
DEFAULT_DELAY = 1.5
DEFAULT_CACHE_ROOT = Path(__file__).resolve().parent / "statements" / "v2"
ProgressCallback = Callable[[str, ContentStatus, int, int], None]


def _expected_problem_identities(source: StatementSource) -> list[ProblemIdentity]:
    return sorted(source_problem_identities(source), key=lambda item: item.problem_code)


def _persist_result(
    store: ContentStore,
    identity: ProblemIdentity,
    result: StatementBuildResult,
    lock: ContentWriterLock,
) -> None:
    evidence = dict(result.evidence)
    if not evidence:
        evidence["error"] = "statement build produced no evidence"
    if result.status is ContentStatus.READY:
        if result.document is None:
            raise ValueError("ready statement result lacks a document")
        if result.document.content_id != identity.problem_code:
            raise ValueError("statement build returned the wrong problem")
        store.publish(result.document, lock=lock)
        return
    store.record_status(
        identity.problem_code,
        result.status,
        evidence=evidence,
        lock=lock,
    )


def _requested_identities(
    store: ContentStore,
    identities: list[ProblemIdentity],
    *,
    force: bool,
    requested_ids: set[str],
) -> list[ProblemIdentity]:
    expected = {item.problem_code for item in identities}
    if not requested_ids.issubset(expected):
        raise ValueError("requested problem is absent from metadata")
    ready = store.ready_ids()
    selected: list[ProblemIdentity] = []
    for identity in identities:
        content_id = identity.problem_code
        if force or content_id in requested_ids:
            selected.append(identity)
            continue
        try:
            marker = store.recorded_status(content_id)
        except (OSError, ValueError, TypeError, KeyError):
            selected.append(identity)
            continue
        marker_status = marker.get("status") if isinstance(marker, dict) else None
        if marker_status in {
            ContentStatus.TRANSIENT_FAILURE.value,
            ContentStatus.INVALID_STRUCTURE.value,
        }:
            selected.append(identity)
            continue
        if content_id in ready:
            continue
        if marker_status == ContentStatus.KNOWN_ABSENT.value:
            continue
        selected.append(identity)
    return selected


def _report(
    store: ContentStore,
    expected_ids: list[str],
    results: list[StatementBuildResult],
    garbage_collection: dict[str, int],
) -> dict[str, object]:
    counts = store.status_counts(expected_ids)
    published = sum(result.status is ContentStatus.READY for result in results)
    known_absent = sum(
        result.status is ContentStatus.KNOWN_ABSENT for result in results
    )
    failed = sum(
        result.status
        in {ContentStatus.TRANSIENT_FAILURE, ContentStatus.INVALID_STRUCTURE}
        for result in results
    )
    completed = (
        failed == 0
        and counts["pending"] == 0
        and counts[ContentStatus.TRANSIENT_FAILURE.value] == 0
        and counts[ContentStatus.INVALID_STRUCTURE.value] == 0
    )
    return {
        "contentKind": "statement",
        "expectedCount": len(expected_ids),
        "attemptedCount": len(results),
        "publishedCount": published,
        "knownAbsentCount": known_absent,
        "failedCount": failed,
        "statusCounts": counts,
        "completed": completed,
        "assetGc": garbage_collection,
    }


def _crawl_statements(
    *,
    source: StatementSource,
    cache_root: str | os.PathLike[str],
    delay: float,
    sleep_fn: Callable[[float], None],
    force: bool,
    requested_ids: set[str],
    progress_callback: ProgressCallback | None,
) -> dict[str, object]:
    identities = _expected_problem_identities(source)
    expected_ids = [item.problem_code for item in identities]
    root = Path(cache_root)
    with ContentWriterLock(root) as lock:
        store = ContentStore.initialize(root, STATEMENT_CODEC, lock=lock)
        todo = _requested_identities(
            store,
            identities,
            force=force,
            requested_ids=requested_ids,
        )
        results: list[StatementBuildResult] = []
        for offset, identity in enumerate(todo):
            result = fetch_statement_v2(
                identity.problem_code,
                source=source,
                asset_root=store.assets_path,
                identity=identity,
            )
            try:
                _persist_result(store, identity, result, lock)
            except (OSError, ValueError, TypeError, KeyError) as error:
                result = StatementBuildResult(
                    ContentStatus.INVALID_STRUCTURE,
                    None,
                    {"error": f"publication-failed:{error}"},
                )
                store.record_status(
                    identity.problem_code,
                    result.status,
                    evidence=result.evidence,
                    lock=lock,
                )
            results.append(result)
            if progress_callback is not None:
                progress_callback(
                    identity.problem_code,
                    result.status,
                    offset + 1,
                    len(todo),
                )
            if offset + 1 < len(todo):
                sleep_fn(delay)
        garbage_collection = store.garbage_collect_assets(lock=lock)
        return _report(store, expected_ids, results, garbage_collection)


def rebuild_statements(
    *,
    source: StatementSource | None = None,
    cache_root: str | os.PathLike[str] | None = None,
    delay: float = DEFAULT_DELAY,
    sleep_fn: Callable[[float], None] = time.sleep,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, object]:
    active_source = source or LiveStatementSource()
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    return _crawl_statements(
        source=active_source,
        cache_root=root,
        delay=delay,
        sleep_fn=sleep_fn,
        force=True,
        requested_ids=set(),
        progress_callback=progress_callback,
    )


def update_statements(
    *,
    source: StatementSource | None = None,
    cache_root: str | os.PathLike[str] | None = None,
    requested_problems: list[str] | None = None,
    delay: float = DEFAULT_DELAY,
    sleep_fn: Callable[[float], None] = time.sleep,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, object]:
    active_source = source or LiveStatementSource()
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    requested = {str(problem_code) for problem_code in requested_problems or ()}
    return _crawl_statements(
        source=active_source,
        cache_root=root,
        delay=delay,
        sleep_fn=sleep_fn,
        force=False,
        requested_ids=requested,
        progress_callback=progress_callback,
    )


def validate_statement(
    problem_code: str,
    *,
    source: StatementSource | None = None,
) -> dict[str, object]:
    active_source = source or LiveStatementSource()
    try:
        identities = {
            item.problem_code: item
            for item in _expected_problem_identities(active_source)
        }
        identity = identities[str(problem_code)]
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {
            "ok": False,
            "problemCode": str(problem_code),
            "status": ContentStatus.KNOWN_ABSENT.value,
            "error": str(error),
        }
    with tempfile.TemporaryDirectory() as directory:
        store = ContentStore.initialize(directory, STATEMENT_CODEC)
        result = fetch_statement_v2(
            identity.problem_code,
            source=active_source,
            asset_root=store.assets_path,
            identity=identity,
        )
        if result.status is not ContentStatus.READY or result.document is None:
            return {
                "ok": False,
                "problemCode": identity.problem_code,
                "status": result.status.value,
                "evidence": dict(result.evidence),
            }
        store.publish(result.document)
        document = store.load_document(identity.problem_code)
        if not isinstance(document, StatementDocument):
            raise ValueError("statement store returned the wrong document type")
        html = render_statement_html(document)
        payload = canonical_json(document).encode("utf-8")
        return {
            "ok": True,
            "problemCode": identity.problem_code,
            "status": ContentStatus.READY.value,
            "documentSha256": hashlib.sha256(payload).hexdigest(),
            "htmlBytes": len(html.encode("utf-8")),
        }


__all__ = [
    "DEFAULT_CACHE_ROOT",
    "FIXTURE_VERSION",
    "PARSER_VERSION",
    "rebuild_statements",
    "update_statements",
    "validate_statement",
]
