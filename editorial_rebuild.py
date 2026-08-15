from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, Protocol, TypedDict
import uuid

import cfcrawl
from cfcrawl import EditorialBuildResult, TutorialBatch, build_editorial_document
from editorial_cache import (
    ContestStatus,
    GenerationStore,
    RebuildLock,
    activate_generation,
    load_active_generation,
)
from content_codecs import EDITORIAL_CODEC  # pyright: ignore[reportMissingImports]
from editorial_model import EditorialDocument, Node, canonical_json, validate_document
from editorial_parser import ParseError, parse_blog_html  # pyright: ignore[reportAttributeAccessIssue]
from editorial_render import render_editorial_html


PARSER_VERSION = "editorial-parser-v2"
FIXTURE_VERSION = "editorial-fixtures-v2"
BATCH_SIZE = 8
DEFAULT_DELAY = 1.5
LIVE_1700_SENTINELS = {
    "1700A": "Let's notice that the optimal path",
    "1700B": "Let X be the number in input",
    "1700C": "Consider the difference array",
    "1700D": "To begin with, we note",
    "1700E": "We need to find a simple criteria",
    "1700F": "We are asked to find a minimum cost perfect matching",
}


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


class GenerationReport(TypedDict):
    generationId: str
    counts: dict[str, int]
    activated: bool
    finalized: bool


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

    def localize_assets(self, document: EditorialDocument) -> EditorialBuildResult:
        raise NotImplementedError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _generation_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:12]}"


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
    def __init__(self, *, image_dir: str | None = None) -> None:
        self.image_dir = image_dir
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

    def localize_assets(self, document: EditorialDocument) -> EditorialBuildResult:
        return cfcrawl.localize_editorial_assets(document, image_dir=self.image_dir)


def _source_or_live(source: EditorialSource | None) -> EditorialSource:
    return source if source is not None else _LiveEditorialSource()


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
        and (
            receipt.status_code is None
            or 200 <= receipt.status_code < 300
        )
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
    return EditorialBuildResult(ContestStatus.TRANSIENT_FAILURE, None, evidence)


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
        return EditorialBuildResult(ContestStatus.KNOWN_ABSENT, None, evidence)

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
            ContestStatus.INVALID_STRUCTURE,
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
        source.localize_assets,
    )
    evidence = dict(result.evidence)
    evidence.setdefault("sourceUrl", source_url)
    if result.status is ContestStatus.READY:
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
    """Build, render, and structurally validate one editorial without cache mutation."""
    contest_id = str(contest_id)
    if source is None:
        with tempfile.TemporaryDirectory(prefix="cfdb-editorial-validation-") as directory:
            result = _build_contest(
                contest_id,
                _LiveEditorialSource(image_dir=directory),
                delay=DEFAULT_DELAY,
                sleep_fn=time.sleep,
            )
    else:
        result = _build_contest(
            contest_id,
            source,
            delay=DEFAULT_DELAY,
            sleep_fn=time.sleep,
        )
    if result.status is not ContestStatus.READY or result.document is None:
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

    # Rendering is itself a validation gate; raw source HTML is never returned.
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
    store: GenerationStore,
    contest_id: str,
    result: EditorialBuildResult,
    lock: RebuildLock,
) -> None:
    document_path = None
    if result.status is ContestStatus.READY:
        if result.document is None:
            raise ValueError("ready build result lacks a document")
        document_path = store.write_document(result.document, lock=lock)
    store.set_status(
        contest_id,
        result.status,
        evidence=result.evidence,
        document_path=document_path,
        lock=lock,
    )
    store.write_manifest(lock=lock)




def _resumable_generation_id(
    root: Path,
    prefix: str,
    expected_contests: list[str],
) -> str | None:
    generations = root / "generations"
    if not generations.is_dir():
        return None
    candidates: list[tuple[str, str]] = []
    for path in generations.iterdir():
        if not path.is_dir() or not path.name.startswith(f"{prefix}-"):
            continue
        store = GenerationStore.open(root, path.name)
        if (
            store.manifest["finalizedAt"] is None
            and store.manifest["parserVersion"] == PARSER_VERSION
            and store.manifest["expectedIds"] == sorted(expected_contests)
        ):
            candidates.append((store.manifest["createdAt"], path.name))
    return max(candidates)[1] if candidates else None


def _report(store: GenerationStore, activated: bool) -> GenerationReport:
    return {
        "generationId": store.generation_id,
        "counts": dict(store.manifest["counts"]),
        "activated": activated,
        "finalized": store.manifest["finalizedAt"] is not None,
    }


def _same_generation_snapshot(
    current: GenerationStore | None,
    expected: GenerationStore,
) -> bool:
    return (
        current is not None
        and current.generation_id == expected.generation_id
        and current.manifest == expected.manifest
    )


def _run_generation(
    *,
    source: EditorialSource,
    cache_root: str | os.PathLike[str],
    generation_id: str,
    expected_contests: list[str],
    delay: float,
    sleep_fn: Callable[[float], None],
    seed: GenerationStore | None = None,
    requested_contests: set[str] | None = None,
    allow_resume: bool = False,
) -> GenerationReport:
    root = Path(cache_root)
    requested = requested_contests or set()
    with RebuildLock(root) as lock:
        if seed is not None:
            active_snapshot = load_active_generation(root)
            if not _same_generation_snapshot(active_snapshot, seed):
                raise RuntimeError("active generation changed before successor creation")
        generation_path = root / "generations" / generation_id
        if generation_path.exists():
            if not allow_resume:
                raise ValueError(f"incremental generation already exists: {generation_id}")
            store = GenerationStore.open(root, generation_id)
            if store.manifest["expectedIds"] != sorted(expected_contests):
                raise ValueError("resumed generation contest set differs from problem metadata")
            if store.manifest["parserVersion"] != PARSER_VERSION:
                raise ValueError("resumed generation parser version differs")
        else:
            store = GenerationStore.create(
                root,
                generation_id,
                expected_contests,
                EDITORIAL_CODEC,
                parser_version=PARSER_VERSION,
                fixture_version=FIXTURE_VERSION,
                lock=lock,
            )
            if seed is not None and seed.manifest["parserVersion"] == PARSER_VERSION:
                store.seed_from(seed, lock=lock)
                for contest_id in requested:
                    store.manifest["entries"].pop(contest_id, None)

        if store.manifest["finalizedAt"] is not None:
            active_snapshot = load_active_generation(root)
            if active_snapshot is None or active_snapshot.generation_id != generation_id:
                activate_generation(root, generation_id, lock=lock)
            return _report(store, True)

        todo = []
        for contest_id in expected_contests:
            entry = store.manifest["entries"].get(contest_id)
            status = entry.get("status") if isinstance(entry, dict) else None
            if status in {ContestStatus.READY.value, ContestStatus.KNOWN_ABSENT.value}:
                if contest_id not in requested:
                    continue
            todo.append(contest_id)

        for offset in range(0, len(todo), BATCH_SIZE):
            batch = todo[offset:offset + BATCH_SIZE]
            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                results = list(
                    executor.map(
                        lambda contest_id: _build_contest(
                            contest_id,
                            source,
                            delay=delay,
                            sleep_fn=sleep_fn,
                        ),
                        batch,
                    )
                )
            for contest_id, result in zip(batch, results):
                _persist_result(store, contest_id, result, lock)
            if offset + BATCH_SIZE < len(todo):
                sleep_fn(delay)
        if not todo:
            store.write_manifest(lock=lock)

        store = GenerationStore.open(root, generation_id)
        activated = False
        if store.manifest["finalizedAt"] is not None and store.is_activation_ready():
            if seed is not None:
                active_snapshot = load_active_generation(root)
                if not _same_generation_snapshot(active_snapshot, seed):
                    raise RuntimeError("active generation changed before successor activation")
            activate_generation(root, generation_id, lock=lock)
            activated = True
        return _report(store, activated)


def rebuild_editorials(
    *,
    source: EditorialSource | None = None,
    cache_root: str | os.PathLike[str] | None = None,
    generation_id: str | None = None,
    delay: float = DEFAULT_DELAY,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> GenerationReport:
    """Create or resume a full inactive v2 generation and activate only if terminal."""
    editorial_source = _source_or_live(source)
    root = Path(cache_root) if cache_root is not None else Path(cfcrawl.EDITORIAL_DIR) / "v2"
    expected = _contest_ids(editorial_source)
    selected_generation = generation_id or _resumable_generation_id(root, "full", expected)
    return _run_generation(
        source=editorial_source,
        cache_root=root,
        generation_id=selected_generation or _generation_id("full"),
        expected_contests=expected,
        delay=delay,
        sleep_fn=sleep_fn,
        allow_resume=True,
    )


def update_editorials(
    *,
    source: EditorialSource | None = None,
    cache_root: str | os.PathLike[str] | None = None,
    generation_id: str | None = None,
    requested_contests: list[str] | None = None,
    delay: float = DEFAULT_DELAY,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> GenerationReport:
    """Build an incremental successor without mutating the active generation."""
    editorial_source = _source_or_live(source)
    root = Path(cache_root) if cache_root is not None else Path(cfcrawl.EDITORIAL_DIR) / "v2"
    active = load_active_generation(root)
    if active is None:
        raise RuntimeError("incremental editorial update requires an active v2 generation")
    metadata_contests = _contest_ids(editorial_source)
    expected = sorted(
        set(active.manifest["expectedIds"]) | set(metadata_contests),
        key=_contest_sort_key,
    )
    requested = {str(item) for item in requested_contests or []}
    if not requested.issubset(expected):
        raise ValueError("requested contest is absent from problem metadata and active generation")
    known_absent = {
        contest_id
        for contest_id, entry in active.manifest["entries"].items()
        if entry.get("status") == ContestStatus.KNOWN_ABSENT.value
    }
    new_contests = set(expected) - set(active.manifest["expectedIds"])
    return _run_generation(
        source=editorial_source,
        cache_root=root,
        generation_id=generation_id or _generation_id("update"),
        expected_contests=expected,
        delay=delay,
        sleep_fn=sleep_fn,
        seed=active,
        requested_contests=requested | known_absent | new_contests,
    )
