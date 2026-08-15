from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Callable, Protocol, cast
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from content_assets import (  # pyright: ignore[reportMissingImports]
    AssetError,
    AssetFetchResult,
    AssetPolicy,
    localize_content_assets,
)
from content_cache import ContentStatus  # pyright: ignore[reportMissingImports]
from content_model import ContentNode
from content_parser import ParseError  # pyright: ignore[reportMissingImports]
from statement_model import StatementDocument, validate_statement_document
from statement_parser import parse_statement_html  # pyright: ignore[reportMissingImports]

_MAX_LIVE_BYTES = 20 * 1024 * 1024
_USER_AGENT = "cfdb-content-ir-v2"
_PROBLEM_CODE_RE = re.compile(r"^(\d+)([A-Za-z][A-Za-z0-9]*)$")
_SOURCE_PATHS = (
    re.compile(r"^/contest/(\d+)/problem/([A-Za-z0-9]+)/?$"),
    re.compile(r"^/problemset/problem/(\d+)/([A-Za-z0-9]+)/?$"),
)


@dataclass(frozen=True, slots=True)
class ProblemIdentity:
    problem_code: str
    contest_id: str
    index: str


@dataclass(frozen=True, slots=True)
class SourceFetch:
    source_url: str
    source_kind: str
    body: str | bytes
    content_type: str


@dataclass(slots=True)
class StatementBuildResult:
    status: ContentStatus
    document: StatementDocument | None
    evidence: dict[str, object]


class StatementSource(Protocol):
    def problem_codes(self) -> list[str]:
        raise NotImplementedError

    def fetch_problem(self, problem_code: str) -> SourceFetch:
        raise NotImplementedError

    def fetch_asset(self, url: str) -> AssetFetchResult:
        raise NotImplementedError


class TransientStatementError(RuntimeError):
    pass


def _problem_identity(problem_code: str) -> ProblemIdentity:
    match = _PROBLEM_CODE_RE.fullmatch(str(problem_code))
    if match is None:
        raise ValueError("invalid-problem-code")
    contest_id, index = match.groups()
    return ProblemIdentity(contest_id + index, contest_id, index)


def _validate_problem_identity(problem: ProblemIdentity) -> ProblemIdentity:
    contest_id = str(problem.contest_id)
    index = str(problem.index)
    problem_code = str(problem.problem_code)
    if (
        not contest_id.isdigit()
        or not index
        or not index.isalnum()
        or problem_code != contest_id + index
    ):
        raise ValueError("invalid-problem-identity")
    return ProblemIdentity(problem_code, contest_id, index)


def source_problem_identities(source: StatementSource) -> list[ProblemIdentity]:
    identity_loader = getattr(source, "problem_identities", None)
    if callable(identity_loader):
        load_identities = cast(Callable[[], list[ProblemIdentity]], identity_loader)
        identities = [_validate_problem_identity(item) for item in load_identities()]
    else:
        identities = [_problem_identity(str(code)) for code in source.problem_codes()]
    codes = [item.problem_code for item in identities]
    if not codes:
        raise ValueError("problem metadata contains no statement identities")
    if len(codes) != len(set(codes)):
        raise ValueError("problem metadata contains duplicate statement identities")
    return identities


def _source_identity(source_url: str) -> str:
    try:
        parsed = urlsplit(source_url)
    except ValueError as error:
        raise ValueError("source-identity-mismatch") from error
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.netloc.lower() not in {"codeforces.com", "www.codeforces.com"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("source-identity-mismatch")
    for pattern in _SOURCE_PATHS:
        match = pattern.fullmatch(parsed.path)
        if match is not None:
            return match.group(1) + match.group(2)
    raise ValueError("source-identity-mismatch")


def _require_source_identity(problem: ProblemIdentity, source_fetch: SourceFetch) -> None:
    if _source_identity(source_fetch.source_url) != problem.problem_code:
        raise ValueError("source-identity-mismatch")


def _make_pdf_document(
    problem: ProblemIdentity,
    source_fetch: SourceFetch,
) -> StatementDocument:
    if not isinstance(source_fetch.body, bytes):
        raise ValueError("invalid-pdf-body")
    return StatementDocument(
        problem_code=problem.problem_code,
        contest_id=problem.contest_id,
        index=problem.index,
        source_url=source_fetch.source_url,
        source_kind="pdf",
        root=ContentNode(
            kind="document",
            children=[
                ContentNode(
                    kind="heading",
                    attrs={"level": 1, "role": "title"},
                    children=[
                        ContentNode(
                            kind="text",
                            text=f"{problem.problem_code} statement",
                        )
                    ],
                ),
                ContentNode(
                    kind="attachment",
                    attrs={
                        "href": source_fetch.source_url,
                        "mediaType": "application/pdf",
                        "label": "Open PDF statement",
                    },
                ),
            ],
        ),
    )


def build_statement_document(
    problem: ProblemIdentity,
    source_fetch: SourceFetch,
    *,
    asset_root: str | Path,
    source: StatementSource,
) -> StatementDocument:
    _require_source_identity(problem, source_fetch)
    normalized_content_type = source_fetch.content_type.split(";", 1)[0].strip().lower()
    if source_fetch.source_kind == "html":
        if not isinstance(source_fetch.body, str) or normalized_content_type != "text/html":
            raise ValueError("invalid-html-source")
        document = parse_statement_html(
            source_fetch.body,
            problem_code=problem.problem_code,
            contest_id=problem.contest_id,
            index=problem.index,
            source_url=source_fetch.source_url,
        )
    elif source_fetch.source_kind == "pdf":
        if normalized_content_type != "application/pdf":
            raise ValueError("invalid-pdf-media-type")
        document = _make_pdf_document(problem, source_fetch)
    else:
        raise ValueError("unsupported-statement-source-kind")

    def fetch_asset(url: str) -> AssetFetchResult:
        if source_fetch.source_kind == "pdf" and url == source_fetch.source_url:
            if not isinstance(source_fetch.body, bytes):
                raise AssetError("invalid-pdf-body")
            return AssetFetchResult(source_fetch.body, source_fetch.content_type)
        return source.fetch_asset(url)

    localized = localize_content_assets(
        document,
        generation_asset_dir=asset_root,
        route_prefix="/statement-assets",
        fetcher=fetch_asset,
        policy=AssetPolicy(
            allow_raster=True,
            allow_pdf_attachment=True,
            max_bytes=_MAX_LIVE_BYTES,
        ),
    )
    if not isinstance(localized, StatementDocument):
        raise ValueError("localized-statement-content-kind-mismatch")
    errors = validate_statement_document(localized, ready=True)
    if errors:
        raise ValueError(errors[0].code)
    return localized


def fetch_statement_v2(
    problem_code: str,
    *,
    source: StatementSource | None = None,
    asset_root: str | Path | None = None,
    identity: ProblemIdentity | None = None,
) -> StatementBuildResult:
    active_source = source or LiveStatementSource()
    requested_code = str(problem_code)
    if identity is not None:
        try:
            problem = _validate_problem_identity(identity)
        except ValueError as error:
            return StatementBuildResult(
                ContentStatus.INVALID_STRUCTURE,
                None,
                {"error": str(error)},
            )
        if problem.problem_code != requested_code:
            return StatementBuildResult(
                ContentStatus.INVALID_STRUCTURE,
                None,
                {"error": "problem-identity-mismatch"},
            )
    else:
        try:
            identities = {
                item.problem_code: item
                for item in source_problem_identities(active_source)
            }
        except Exception as error:
            return StatementBuildResult(
                ContentStatus.TRANSIENT_FAILURE,
                None,
                {"error": f"metadata-unavailable:{error}"},
            )
        problem = identities.get(requested_code)
        if problem is None:
            return StatementBuildResult(
                ContentStatus.KNOWN_ABSENT,
                None,
                {"problemCode": requested_code, "recognized": False},
            )
    if asset_root is None:
        return StatementBuildResult(
            ContentStatus.INVALID_STRUCTURE,
            None,
            {"error": "asset-root-required"},
        )
    try:
        source_fetch = active_source.fetch_problem(problem.problem_code)
    except Exception as error:
        return StatementBuildResult(
            ContentStatus.TRANSIENT_FAILURE,
            None,
            {"error": f"statement-fetch-failed:{error}"},
        )
    try:
        document = build_statement_document(
            problem,
            source_fetch,
            asset_root=asset_root,
            source=active_source,
        )
    except AssetError as error:
        error_code = str(error)
        status = (
            ContentStatus.TRANSIENT_FAILURE
            if error_code in {"asset-fetch-failed", "asset-write-failed"}
            else ContentStatus.INVALID_STRUCTURE
        )
        return StatementBuildResult(status, None, {"error": error_code})
    except (ParseError, TypeError, ValueError) as error:
        return StatementBuildResult(
            ContentStatus.INVALID_STRUCTURE,
            None,
            {"error": str(error)},
        )
    return StatementBuildResult(
        ContentStatus.READY,
        document,
        {
            "sourceUrl": document.source_url,
            "sourceKind": document.source_kind,
            "assets": list(document.assets),
        },
    )


class LiveStatementSource:
    def __init__(self, *, timeout: float = 30.0, max_bytes: int = _MAX_LIVE_BYTES) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._problem_identities: tuple[ProblemIdentity, ...] | None = None

    def problem_identities(self) -> list[ProblemIdentity]:
        from cfcrawl import _load_problems

        if self._problem_identities is None:
            result: list[ProblemIdentity] = []
            for problem in _load_problems():
                if not isinstance(problem, dict):
                    continue
                contest_id = problem.get("contestId")
                index = problem.get("index")
                if isinstance(contest_id, int) and isinstance(index, str):
                    result.append(
                        _validate_problem_identity(
                            ProblemIdentity(
                                str(contest_id) + index,
                                str(contest_id),
                                index,
                            )
                        )
                    )
            self._problem_identities = tuple(result)
        return list(self._problem_identities)

    def problem_codes(self) -> list[str]:
        return [item.problem_code for item in self.problem_identities()]

    def fetch_problem(self, problem_code: str) -> SourceFetch:
        identities = {
            item.problem_code: item for item in self.problem_identities()
        }
        try:
            problem = identities[str(problem_code)]
        except KeyError as error:
            raise ValueError("invalid-problem-code") from error
        url = (
            f"https://codeforces.com/contest/{problem.contest_id}/problem/{problem.index}"
        )
        payload, content_type = self._fetch_bytes(url)
        if content_type.split(";", 1)[0].strip().lower() == "application/pdf" or payload.startswith(
            b"%PDF-"
        ):
            return SourceFetch(url, "pdf", payload, "application/pdf")
        try:
            html_text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise TransientStatementError("statement-html-is-not-utf8") from error
        return SourceFetch(url, "html", html_text, "text/html")

    def fetch_asset(self, url: str) -> AssetFetchResult:
        payload, content_type = self._fetch_bytes(url)
        return AssetFetchResult(payload, content_type)

    def _fetch_bytes(self, url: str) -> tuple[bytes, str]:
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read(self.max_bytes + 1)
                content_type = response.headers.get("Content-Type", "application/octet-stream")
        except OSError as error:
            raise TransientStatementError("statement-request-failed") from error
        if len(payload) > self.max_bytes:
            raise TransientStatementError("statement-response-too-large")
        if not payload:
            raise TransientStatementError("statement-response-empty")
        return payload, content_type


__all__ = [
    "LiveStatementSource",
    "ProblemIdentity",
    "SourceFetch",
    "StatementBuildResult",
    "StatementSource",
    "build_statement_document",
    "fetch_statement_v2",
    "source_problem_identities",
]
