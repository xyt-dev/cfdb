from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, Protocol, TypedDict
from collections.abc import Collection

import cfcrawl
from cfcrawl import EditorialBuildResult, TutorialBatch, build_editorial_document
from content_cache import (  # pyright: ignore[reportMissingImports]
    ContentStatus,
    ContentStore,
    ContentWriterLock,
)
from content_codecs import EDITORIAL_CODEC  # pyright: ignore[reportMissingImports]
from editorial_model import EditorialDocument, Node, canonical_json, validate_document
from editorial_parser import ParseError, parse_blog_html  # pyright: ignore[reportAttributeAccessIssue]
from editorial_render import render_editorial_html


PARSER_VERSION = "editorial-parser-v2"
FIXTURE_VERSION = "editorial-fixtures-v2"
BATCH_SIZE = 8
DEFAULT_DELAY = 1.5
DEFAULT_CACHE_ROOT = Path(__file__).resolve().parent / "editorials" / "v2"
LIVE_1700_SENTINELS = {
    "1700A": "Let's notice that the optimal path",
    "1700B": "Let X be the number in input",
    "1700C": "Consider the difference array",
    "1700D": "To begin with, we note",
    "1700E": "We need to find a simple criteria",
    "1700F": "We are asked to find a minimum cost perfect matching",
}
ProgressCallback = Callable[[str, ContentStatus, int, int], None]
PrioritySelector = Callable[[Collection[str]], str | None]


@dataclass(frozen=True, slots=True)
class FetchReceipt:
    ok: bool
    body: str
    status_code: int | None
    blocked: bool
    recognized: bool
    fetched_at: str
    error: str | None = None


class ValidationReport(TypedDict):
    ok: bool
    contestId: str
    status: str
    problemCodes: list[str]
    matchedSentinels: dict[str, str]
    unresolvedSlots: list[str]
    validationErrors: list[object]
    canonicalJsonSha256: str | None


class EditorialSource(Protocol):
    def problem_contest_ids(self) -> list[str]:
        raise NotImplementedError

    def fetch_contest_page(self, contest_id: str) -> FetchReceipt:
        raise NotImplementedError

    def find_editorial_url(self, contest_html: str) -> str | None:
        raise NotImplementedError

    def fetch_editorial_page(self, url: str) -> FetchReceipt:
        raise NotImplementedError

    def fetch_tutorial_batch(self, contest_id: str, codes: list[str]) -> TutorialBatch:
        raise NotImplementedError

    def localize_assets(
        self,
        document: EditorialDocument,
        *,
        asset_root: Path,
    ) -> EditorialBuildResult:
        raise NotImplementedError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_recognized(body: str) -> bool:
    if not body.strip():
        return False
    lowered = body.lower()
    return not any(
        marker in lowered
        for marker in (
            "403 forbidden",
            "just a moment",
            "contest not found",
            "does not exist",
            "nginx/",
        )
    )


class _LiveEditorialSource:
    def problem_contest_ids(self) -> list[str]:
        return sorted({str(problem["contestId"]) for problem in cfcrawl._load_problems()})

    def _fetch(self, url: str) -> FetchReceipt:
        fetched_at = _utc_now()
        try:
            body = cfcrawl.fetch_url(url)
        except Exception as error:
            return FetchReceipt(False, "", None, False, False, fetched_at, str(error))
        if not isinstance(body, str) or not body:
            return FetchReceipt(False, "", None, False, False, fetched_at, "fetch-failed")
        blocked = any(marker in body.lower() for marker in ("403 forbidden", "just a moment"))
        return FetchReceipt(
            True,
            body,
            200,
            blocked,
            _is_recognized(body),
            fetched_at,
            None,
        )

    def fetch_contest_page(self, contest_id: str) -> FetchReceipt:
        return self._fetch(f"https://codeforces.com/contest/{contest_id}")

    def find_editorial_url(self, contest_html: str) -> str | None:
        return cfcrawl._find_editorial_link(contest_html)

    def fetch_editorial_page(self, url: str) -> FetchReceipt:
        return self._fetch(url)

    def fetch_tutorial_batch(self, contest_id: str, codes: list[str]) -> TutorialBatch:
        return cfcrawl._fetch_problem_tutorial_fragments(contest_id, codes)

    def localize_assets(
        self,
        document: EditorialDocument,
        *,
        asset_root: Path,
    ) -> EditorialBuildResult:
        return cfcrawl.localize_editorial_assets(document, image_dir=os.fspath(asset_root))


def _valid_timestamp(value: str) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _receipt_valid(receipt: FetchReceipt) -> bool:
    return (
        isinstance(receipt, FetchReceipt)
        and _valid_timestamp(receipt.fetched_at)
        and receipt.ok
        and (receipt.status_code is None or 200 <= receipt.status_code < 300)
        and not receipt.blocked
        and receipt.recognized
        and bool(receipt.body)
    )


def _receipt_dict(receipt: FetchReceipt, *, editorial_found: bool) -> dict[str, object]:
    return {
        "fetchedAt": receipt.fetched_at,
        "recognized": receipt.recognized,
        "editorialFound": editorial_found,
        "tutorialFound": editorial_found,
    }


def _failure(error: str, *receipts: FetchReceipt) -> EditorialBuildResult:
    evidence: dict[str, object] = {"errors": [error]}
    if receipts:
        evidence["receipts"] = [
            {
                "ok": item.ok,
                "statusCode": item.status_code,
                "blocked": item.blocked,
                "recognized": item.recognized,
                "fetchedAt": item.fetched_at,
                "error": item.error,
            }
            for item in receipts
        ]
    return EditorialBuildResult(ContentStatus.TRANSIENT_FAILURE, None, evidence)


def _collect_tutorial_codes(root: Node) -> list[str]:
    codes: list[str] = []

    def visit(node: Node) -> None:
        if node.kind == "tutorial_slot":
            codes.append(str(node.attrs.get("problemCode", "")))
        for child in node.children:
            visit(child)

    visit(root)
    return codes


def _build_contest(
    contest_id: str,
    source: EditorialSource,
    *,
    asset_root: Path,
    delay: float,
    sleep_fn: Callable[[float], None],
) -> EditorialBuildResult:
    first = source.fetch_contest_page(contest_id)
    if not _receipt_valid(first):
        return _failure("contest-page-fetch-failed", first)
    source_url = source.find_editorial_url(first.body)
    if source_url is None:
        sleep_fn(delay)
        second = source.fetch_contest_page(contest_id)
        if not _receipt_valid(second):
            return _failure("absence-recheck-failed", first, second)
        second_url = source.find_editorial_url(second.body)
        if second_url is not None or second.fetched_at == first.fetched_at:
            return _failure("absence-check-mismatch", first, second)
        evidence = {
            "successfulCheckTimestamps": [first.fetched_at, second.fetched_at],
            "contestPageReceipts": [
                _receipt_dict(first, editorial_found=False),
                _receipt_dict(second, editorial_found=False),
            ],
        }
        return EditorialBuildResult(ContentStatus.KNOWN_ABSENT, None, evidence)

    editorial = source.fetch_editorial_page(source_url)
    if not _receipt_valid(editorial):
        return _failure("editorial-page-fetch-failed", first, editorial)
    try:
        parsed = parse_blog_html(
            editorial.body,
            contest_id=contest_id,
            source_url=source_url,
        )
    except ParseError as error:
        return EditorialBuildResult(
            ContentStatus.INVALID_STRUCTURE,
            None,
            {"error": str(error), "sourceUrl": source_url},
        )
    codes = _collect_tutorial_codes(parsed.root)
    try:
        tutorial_batch = source.fetch_tutorial_batch(contest_id, codes)
    except Exception:
        return _failure("tutorial-batch-fetch-failed", editorial)
    result = build_editorial_document(
        contest_id,
        source_url,
        editorial.body,
        tutorial_batch,
        lambda document: source.localize_assets(document, asset_root=asset_root),
    )
    evidence = dict(result.evidence)
    evidence.setdefault("sourceUrl", source_url)
    if result.status is ContentStatus.READY:
        evidence["validatedAt"] = editorial.fetched_at
    return EditorialBuildResult(result.status, result.document, evidence)


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _plain_text(node: Node) -> str:
    return (node.text or "") + "".join(_plain_text(child) for child in node.children)


def validate_editorial(
    contest_id: str,
    *,
    source: EditorialSource | None = None,
) -> ValidationReport:
    """Build, render, and structurally validate one editorial without store mutation."""
    contest_id = str(contest_id)
    with tempfile.TemporaryDirectory(prefix="cfdb-editorial-validation-") as directory:
        result = _build_contest(
            contest_id,
            source or _LiveEditorialSource(),
            asset_root=Path(directory),
            delay=DEFAULT_DELAY,
            sleep_fn=time.sleep,
        )
    if result.status is not ContentStatus.READY or result.document is None:
        return {
            "ok": False,
            "contestId": contest_id,
            "status": result.status.value,
            "problemCodes": [],
            "matchedSentinels": {},
            "unresolvedSlots": [],
            "validationErrors": [dict(result.evidence)],
            "canonicalJsonSha256": None,
        }

    document = result.document
    validation_errors: list[object] = [
        item.to_dict() for item in validate_document(document, ready=True)
    ]
    sections = [node for node in _walk(document.root) if node.kind == "problem_section"]
    problem_codes = [str(section.attrs.get("problemCode", "")) for section in sections]
    unresolved = [
        str(node.attrs.get("problemCode", ""))
        for node in _walk(document.root)
        if node.kind == "tutorial_slot"
    ]
    if len(problem_codes) != len(set(problem_codes)):
        validation_errors.append({"code": "duplicate-problem-section"})
    if contest_id == "1700" and problem_codes != list(LIVE_1700_SENTINELS):
        validation_errors.append(
            {
                "code": "unexpected-problem-sections",
                "expected": list(LIVE_1700_SENTINELS),
                "actual": problem_codes,
            }
        )

    matched: dict[str, str] = {}
    for code, sentinel in LIVE_1700_SENTINELS.items() if contest_id == "1700" else ():
        matching_sections = [
            section for section in sections if section.attrs.get("problemCode") == code
        ]
        if len(matching_sections) == 1 and sentinel in _plain_text(matching_sections[0]):
            matched[code] = sentinel
        else:
            validation_errors.append({"code": "missing-problem-sentinel", "problemCode": code})
    if unresolved:
        validation_errors.append({"code": "unresolved-tutorial-slots"})
    try:
        render_editorial_html(document)
    except Exception as error:
        validation_errors.append({"code": "render-failed", "message": str(error)})
    canonical = canonical_json(document).encode("utf-8")
    return {
        "ok": not validation_errors,
        "contestId": contest_id,
        "status": result.status.value,
        "problemCodes": problem_codes,
        "matchedSentinels": matched,
        "unresolvedSlots": unresolved,
        "validationErrors": validation_errors,
        "canonicalJsonSha256": hashlib.sha256(canonical).hexdigest(),
    }


def _contest_sort_key(contest_id: str) -> tuple[int, str]:
    try:
        numeric_id = int(contest_id)
    except ValueError as error:
        raise ValueError("invalid contest ID") from error
    return numeric_id, contest_id


def _contest_ids(source: EditorialSource) -> list[str]:
    contests = [str(item) for item in source.problem_contest_ids()]
    if not contests:
        raise ValueError("problem metadata contains no contests")
    if any(not item.isdigit() for item in contests):
        raise ValueError("problem metadata contains an invalid contest ID")
    return sorted(set(contests), key=_contest_sort_key)


def _persist_result(
    store: ContentStore,
    contest_id: str,
    result: EditorialBuildResult,
    lock: ContentWriterLock,
) -> None:
    evidence = dict(result.evidence)
    if not evidence:
        evidence["error"] = "editorial build produced no evidence"
    if result.status is ContentStatus.READY:
        if result.document is None:
            raise ValueError("ready editorial result lacks a document")
        if result.document.content_id != contest_id:
            raise ValueError("editorial build returned the wrong contest")
        store.publish(result.document, lock=lock)
        return
    store.record_status(
        contest_id,
        result.status,
        evidence=evidence,
        lock=lock,
    )


def _requested_contests(
    store: ContentStore,
    contest_ids: list[str],
    *,
    force: bool,
    requested_ids: set[str],
) -> list[str]:
    expected = set(contest_ids)
    if not requested_ids.issubset(expected):
        raise ValueError("requested contest is absent from metadata")
    ready = store.ready_ids()
    selected: list[str] = []
    for contest_id in contest_ids:
        if force or contest_id in requested_ids:
            selected.append(contest_id)
            continue
        try:
            marker = store.recorded_status(contest_id)
        except (OSError, ValueError, TypeError, KeyError):
            selected.append(contest_id)
            continue
        marker_status = marker.get("status") if isinstance(marker, dict) else None
        if marker_status in {
            ContentStatus.TRANSIENT_FAILURE.value,
            ContentStatus.INVALID_STRUCTURE.value,
        }:
            selected.append(contest_id)
            continue
        if contest_id in ready:
            continue
        if marker_status == ContentStatus.KNOWN_ABSENT.value:
            continue
        selected.append(contest_id)
    return selected



def pending_editorial_ids(
    *,
    source: EditorialSource | None = None,
    cache_root: str | os.PathLike[str] | None = None,
) -> list[str]:
    active_source = source or _LiveEditorialSource()
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    contest_ids = _contest_ids(active_source)
    return _requested_contests(
        ContentStore(root, EDITORIAL_CODEC),
        contest_ids,
        force=False,
        requested_ids=set(),
    )


def _report(
    store: ContentStore,
    expected_ids: list[str],
    results: list[EditorialBuildResult],
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
        "contentKind": "editorial",
        "expectedCount": len(expected_ids),
        "attemptedCount": len(results),
        "publishedCount": published,
        "knownAbsentCount": known_absent,
        "failedCount": failed,
        "statusCounts": counts,
        "completed": completed,
        "assetGc": garbage_collection,
    }


def _crawl_editorials(
    *,
    source: EditorialSource,
    cache_root: str | os.PathLike[str],
    delay: float,
    sleep_fn: Callable[[float], None],
    force: bool,
    requested_ids: set[str],
    progress_callback: ProgressCallback | None,
    priority_selector: PrioritySelector | None,
) -> dict[str, object]:
    expected_ids = _contest_ids(source)
    root = Path(cache_root)
    with ContentWriterLock(root) as lock:
        store = ContentStore.initialize(root, EDITORIAL_CODEC, lock=lock)
        todo = _requested_contests(
            store,
            expected_ids,
            force=force,
            requested_ids=requested_ids,
        )
        total = len(todo)
        remaining = dict.fromkeys(todo)
        results: list[EditorialBuildResult] = []
        completed_count = 0
        while remaining:
            batch: list[str] = []
            while remaining and len(batch) < BATCH_SIZE:
                prioritized_id = (
                    priority_selector(remaining.keys())
                    if priority_selector is not None
                    else None
                )
                selected_id = (
                    prioritized_id
                    if prioritized_id is not None and prioritized_id in remaining
                    else next(iter(remaining))
                )
                del remaining[selected_id]
                batch.append(selected_id)
            with ThreadPoolExecutor(max_workers=max(1, len(batch))) as executor:
                futures = {
                    executor.submit(
                        _build_contest,
                        contest_id,
                        source,
                        asset_root=store.assets_path,
                        delay=delay,
                        sleep_fn=sleep_fn,
                    ): contest_id
                    for contest_id in batch
                }
                for future in as_completed(futures):
                    contest_id = futures[future]
                    try:
                        result = future.result()
                    except Exception as error:
                        result = EditorialBuildResult(
                            ContentStatus.TRANSIENT_FAILURE,
                            None,
                            {"errors": [f"contest-build-failed:{error}"]},
                        )
                    try:
                        _persist_result(store, contest_id, result, lock)
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        result = EditorialBuildResult(
                            ContentStatus.INVALID_STRUCTURE,
                            None,
                            {"error": f"publication-failed:{error}"},
                        )
                        store.record_status(
                            contest_id,
                            result.status,
                            evidence=result.evidence,
                            lock=lock,
                        )
                    results.append(result)
                    completed_count += 1
                    if progress_callback is not None:
                        progress_callback(
                            contest_id,
                            result.status,
                            completed_count,
                            total,
                        )
            if remaining:
                sleep_fn(delay)
        garbage_collection = store.garbage_collect_assets(lock=lock)
        return _report(store, expected_ids, results, garbage_collection)


def rebuild_editorials(
    *,
    source: EditorialSource | None = None,
    cache_root: str | os.PathLike[str] | None = None,
    delay: float = DEFAULT_DELAY,
    sleep_fn: Callable[[float], None] = time.sleep,
    progress_callback: ProgressCallback | None = None,
    priority_selector: PrioritySelector | None = None,
) -> dict[str, object]:
    active_source = source or _LiveEditorialSource()
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    return _crawl_editorials(
        source=active_source,
        cache_root=root,
        delay=delay,
        sleep_fn=sleep_fn,
        force=True,
        requested_ids=set(),
        progress_callback=progress_callback,
        priority_selector=priority_selector,
    )


def update_editorials(
    *,
    source: EditorialSource | None = None,
    cache_root: str | os.PathLike[str] | None = None,
    requested_contests: list[str] | None = None,
    delay: float = DEFAULT_DELAY,
    sleep_fn: Callable[[float], None] = time.sleep,
    progress_callback: ProgressCallback | None = None,
    priority_selector: PrioritySelector | None = None,
) -> dict[str, object]:
    active_source = source or _LiveEditorialSource()
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    requested = {str(contest_id) for contest_id in requested_contests or ()}
    return _crawl_editorials(
        source=active_source,
        cache_root=root,
        delay=delay,
        sleep_fn=sleep_fn,
        force=False,
        requested_ids=requested,
        progress_callback=progress_callback,
        priority_selector=priority_selector,
    )


__all__ = [
    "BATCH_SIZE",
    "DEFAULT_CACHE_ROOT",
    "FIXTURE_VERSION",
    "FetchReceipt",
    "LIVE_1700_SENTINELS",
    "PARSER_VERSION",
    "pending_editorial_ids",
    "rebuild_editorials",
    "update_editorials",
    "validate_editorial",
]
