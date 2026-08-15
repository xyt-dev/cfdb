#!/usr/bin/env python3
"""Shared Codeforces HTTP, metadata, solution, and typed editorial crawl helpers."""
from dataclasses import dataclass
import json
import os
import re
import subprocess
import sys
import tempfile
import time

from content_assets import (  # pyright: ignore[reportMissingImports]
    AssetError,
    AssetFetchResult,
    AssetPolicy,
    localize_content_assets,
)
from content_cache import ContentStatus  # pyright: ignore[reportMissingImports]
from editorial_model import Diagnostic, EditorialDocument, Node, validate_document
from editorial_parser import ParseError, compose_tutorials, parse_blog_html, parse_tutorial_fragment  # pyright: ignore[reportAttributeAccessIssue]

ROOT = os.path.dirname(os.path.abspath(__file__))
STATEMENT_DIR = os.path.join(ROOT, "statements")
SOLUTION_DIR = os.path.join(ROOT, "solutions")
EDITORIAL_DIR = os.path.join(ROOT, "editorials")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
CURL = os.environ.get("CFGEN_CURL") or "curl"


@dataclass(slots=True)
class TutorialBatch:
    html_by_code: dict[str, str]
    missing_codes: set[str]
    transient_errors: list[str]


@dataclass(slots=True)
class EditorialBuildResult:
    status: ContentStatus
    document: EditorialDocument | None
    evidence: dict[str, object]


def _pick_curl():
    for name in ("curl-impersonate", "curl-impersonate-chrome", "curl_chrome116"):
        for d in os.environ.get("PATH", "").split(":"):
            if d and os.path.isfile(os.path.join(d, name)):
                return name
    return "curl"


CURL = os.environ.get("CFGEN_CURL") or _pick_curl()


def fetch_url(url: str, timeout: int = 30, retries: int = 3) -> str | None:
    """curl 抓取 HTML，带重试；失败返回 None"""
    for attempt in range(retries):
        try:
            r = subprocess.run(
                [CURL, "-sL", "--compressed", "--max-time", str(timeout),
                 "-H", f"User-Agent: {UA}",
                 "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                 "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
                 url],
                capture_output=True, timeout=timeout + 10)
            if r.returncode == 0 and r.stdout:
                return r.stdout.decode("utf-8", "replace")
        except Exception:
            pass
        time.sleep(3 * (attempt + 1))
    return None




def _load_problems() -> list:
    """加载 problems.json（失败返回空列表）"""
    path = os.path.join(ROOT, "problems.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    print(f"⚠️ 无法加载 {path}（先运行 update.py 生成数据）", file=sys.stderr)
    return []


# ═══ 题面 ═══










# ═══ 自己的解题代码（solutions 目录）═══
SOLUTION_EXTS = {".rs", ".cpp", ".cc", ".cxx", ".py", ".c", ".java", ".go", ".ts", ".js", ".kt", ".txt"}


def list_solutions(cid, idx) -> list[dict]:
    """列出该题的用户解题代码文件（按修改时间倒序）"""
    prefix = f"{cid}{idx}."
    out = []
    try:
        names = os.listdir(SOLUTION_DIR)
    except OSError:
        return out
    for n in names:
        if n.startswith(prefix):
            ext = os.path.splitext(n)[1].lower()
            if ext in SOLUTION_EXTS:
                pth = os.path.join(SOLUTION_DIR, n)
                try:
                    st = os.stat(pth)
                    out.append({"name": n, "ext": ext, "mtime": st.st_mtime})
                except OSError:
                    pass
    out.sort(key=lambda x: -x["mtime"])
    return out


def read_solution(name) -> str | None:
    """读取解题代码内容"""
    pth = os.path.join(SOLUTION_DIR, name)
    try:
        with open(pth, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


# ═══ CF 官方题解（editorial 博客）═══
_ENTRY_HREF = r'<a[^>]*href="(?:https?://codeforces\.com)?(/blog/entry/\d+)[^"]*"[^>]*'


def _find_editorial_link(contest_html: str) -> str | None:
    """在比赛页面找 editorial 博客链接。
    ① title 属性匹配（常规页面）；② 链接文本匹配（老页面 title 是数字/绝对 URL/带参数）"""
    for m in re.finditer(_ENTRY_HREF + r'title="([^"]*)"[^>]*>', contest_html, re.I):
        if re.search(r"editorial|tutorial", m.group(2), re.I):
            return "https://codeforces.com" + m.group(1)
    # fallback：链接文本含 Tutorial/Editorial（排除 Announcement）
    for m in re.finditer(_ENTRY_HREF + r'>([\s\S]*?)</a>', contest_html, re.I):
        text = m.group(2)
        if re.search(r"tutorial|editorial", text, re.I) and not re.search(r"announcement", text, re.I):
            return "https://codeforces.com" + m.group(1)
    return None


EDITORIAL_IMAGE_DIR = os.path.join(EDITORIAL_DIR, "images")




def _tutorial_batch_from_responses(codes, fetch_tutorial) -> TutorialBatch:
    html_by_code: dict[str, str] = {}
    missing_codes: set[str] = set()
    transient_errors: list[str] = []
    for code in codes:
        try:
            response = fetch_tutorial(code)
        except Exception:
            transient_errors.append(f"{code}:tutorial-request-failed")
            continue
        if not isinstance(response, dict):
            transient_errors.append(f"{code}:invalid-tutorial-response")
            continue
        success = response.get("success")
        is_boolean = isinstance(success, bool)
        if (is_boolean and success) or success == "true":
            fragment = response.get("html")
            if isinstance(fragment, str) and fragment.strip():
                html_by_code[code] = fragment
            else:
                transient_errors.append(f"{code}:missing-tutorial-html")
        elif (is_boolean and not success) or success == "false":
            missing_codes.add(code)
        else:
            transient_errors.append(f"{code}:invalid-tutorial-success")
    return TutorialBatch(html_by_code, missing_codes, transient_errors)


def _fetch_problem_tutorial_fragments(cid: str, codes: list[str]) -> TutorialBatch:
    if not codes:
        return TutorialBatch({}, set(), [])

    descriptor, jar = tempfile.mkstemp(prefix=f"cfdb-{cid}-", suffix=".cookies")
    os.close(descriptor)
    try:
        idx0 = codes[0][len(cid):]
        try:
            page_result = subprocess.run(
                [CURL, "-s", "-c", jar, "--max-time", "20",
                 "-H", f"User-Agent: {UA}",
                 f"https://codeforces.com/contest/{cid}/problem/{idx0}"],
                capture_output=True,
                timeout=30,
            )
            page = page_result.stdout.decode("utf-8", "replace")
        except Exception:
            return TutorialBatch({}, set(), ["csrf-page-fetch-failed"])
        match = re.search(r"data-csrf='([a-f0-9]+)'", page)
        if match is None:
            return TutorialBatch({}, set(), ["csrf-token-unavailable"])
        token = match.group(1)

        def fetch_tutorial(code: str):
            result = subprocess.run(
                [CURL, "-s", "-b", jar, "-c", jar, "-X", "POST",
                 "--max-time", "20", "-H", f"User-Agent: {UA}",
                 "-H", "X-Requested-With: XMLHttpRequest",
                 "-H", f"X-Csrf-Token: {token}",
                 "--data", f"problemCode={code}",
                 "https://codeforces.com/data/problemTutorial"],
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0 or not result.stdout:
                raise OSError("tutorial request failed")
            try:
                return json.loads(result.stdout)
            finally:
                time.sleep(0.3)

        return _tutorial_batch_from_responses(codes, fetch_tutorial)
    finally:
        try:
            os.remove(jar)
        except OSError:
            pass


def build_editorial_document(
    contest_id,
    source_url,
    base_html,
    tutorial_batch,
    asset_localizer,
) -> EditorialBuildResult:
    contest_id = str(contest_id)
    if tutorial_batch.transient_errors:
        return EditorialBuildResult(
            ContentStatus.TRANSIENT_FAILURE,
            None,
            {"errors": list(tutorial_batch.transient_errors)},
        )
    try:
        base = parse_blog_html(
            base_html,
            contest_id=contest_id,
            source_url=source_url,
        )
        parsed = {
            code: parse_tutorial_fragment(fragment, expected_code=code)
            for code, fragment in tutorial_batch.html_by_code.items()
        }
        composed = compose_tutorials(
            base,
            tutorials=parsed,
            missing_codes=tutorial_batch.missing_codes,
        )
    except ParseError as error:
        return EditorialBuildResult(
            ContentStatus.INVALID_STRUCTURE,
            None,
            {"error": str(error)},
        )
    localized = asset_localizer(composed)
    if localized.status is not ContentStatus.READY or localized.document is None:
        return localized
    validation_errors = validate_document(localized.document, ready=True)
    if validation_errors:
        return EditorialBuildResult(
            ContentStatus.INVALID_STRUCTURE,
            None,
            {"errors": [item.to_dict() for item in validation_errors]},
        )
    return EditorialBuildResult(
        ContentStatus.READY,
        localized.document,
        {"sourceUrl": source_url},
    )


def _fetch_editorial_asset(url: str) -> bytes | None:
    try:
        result = subprocess.run(
            [CURL, "-sL", "--max-time", "20", "-H", f"User-Agent: {UA}", url],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        return None
    payload = result.stdout.lstrip()
    if (
        result.returncode != 0
        or not payload
        or payload.lower().startswith((b"<html", b"<!doctype html"))
    ):
        return None
    return result.stdout




def localize_editorial_assets(
    document: EditorialDocument,
    *,
    image_dir: str | None = None,
    image_fetcher=None,
) -> EditorialBuildResult:
    image_dir = image_dir or EDITORIAL_IMAGE_DIR
    image_fetcher = image_fetcher or _fetch_editorial_asset

    def fetch_asset(source: str) -> AssetFetchResult:
        try:
            fetched = image_fetcher(source)
        except Exception as error:
            raise AssetError("asset-fetch-failed") from error
        if isinstance(fetched, AssetFetchResult):
            return fetched
        if not isinstance(fetched, bytes) or not fetched:
            raise AssetError("asset-fetch-failed")
        if fetched.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        elif fetched.startswith(b"\xff\xd8\xff"):
            media_type = "image/jpeg"
        elif fetched.startswith((b"GIF87a", b"GIF89a")):
            media_type = "image/gif"
        elif len(fetched) >= 12 and fetched.startswith(b"RIFF") and fetched[8:12] == b"WEBP":
            media_type = "image/webp"
        else:
            media_type = "application/octet-stream"
        return AssetFetchResult(fetched, media_type)

    try:
        localized = localize_content_assets(
            document,
            generation_asset_dir=image_dir,
            route_prefix="/editorial-assets",
            fetcher=fetch_asset,
            policy=AssetPolicy(
                allow_raster=True,
                allow_pdf_attachment=False,
                max_bytes=20 * 1024 * 1024,
            ),
        )
    except AssetError as error:
        diagnostic = Diagnostic(
            "error",
            "editorial-asset-transient-failure",
            str(error),
            "document",
        )
        return EditorialBuildResult(
            ContentStatus.TRANSIENT_FAILURE,
            None,
            {"errors": [diagnostic.to_dict()]},
        )
    if not isinstance(localized, EditorialDocument):
        return EditorialBuildResult(
            ContentStatus.INVALID_STRUCTURE,
            None,
            {"error": "localized editorial has the wrong content kind"},
        )
    return EditorialBuildResult(
        ContentStatus.READY,
        localized,
        {"assets": list(localized.assets)},
    )


def fetch_editorial_v2(
    cid,
    retries: int = 3,
    timeout: int = 30,
    *,
    fetch_page=None,
    fetch_tutorial=None,
    asset_localizer=None,
) -> EditorialBuildResult:
    cid = str(cid)
    if not cid.isdigit():
        return EditorialBuildResult(
            ContentStatus.INVALID_STRUCTURE,
            None,
            {"error": "invalid-contest-id"},
        )
    page_fetcher = fetch_page or (
        lambda url: fetch_url(url, timeout=timeout, retries=retries)
    )

    def transient(error: str) -> EditorialBuildResult:
        return EditorialBuildResult(
            ContentStatus.TRANSIENT_FAILURE,
            None,
            {"errors": [error]},
        )

    contest_url = f"https://codeforces.com/contest/{cid}"
    try:
        contest_html = page_fetcher(contest_url)
    except Exception:
        return transient("contest-page-fetch-failed")
    if not isinstance(contest_html, str) or not contest_html:
        return transient("contest-page-fetch-failed")
    if any(marker in contest_html for marker in (
        "Contest not found", "does not exist", "Just a moment", "403 Forbidden",
    )):
        return transient("contest-page-unrecognized")
    source_url = _find_editorial_link(contest_html)
    if source_url is None:
        return transient("editorial-link-not-confirmed")
    try:
        base_html = page_fetcher(source_url)
    except Exception:
        return transient("editorial-page-fetch-failed")
    if not isinstance(base_html, str) or not base_html:
        return transient("editorial-page-fetch-failed")
    if any(marker in base_html for marker in (
        "403 Forbidden", "Just a moment", "nginx/", "Announcement of Codeforces Round",
    )):
        return transient("editorial-page-unrecognized")

    try:
        parsed_base = parse_blog_html(
            base_html,
            contest_id=cid,
            source_url=source_url,
        )
    except ParseError as error:
        return EditorialBuildResult(
            ContentStatus.INVALID_STRUCTURE,
            None,
            {"error": str(error)},
        )

    codes: list[str] = []

    def collect_slots(node: Node) -> None:
        if node.kind == "tutorial_slot":
            codes.append(str(node.attrs.get("problemCode", "")))
        for child in node.children:
            collect_slots(child)

    collect_slots(parsed_base.root)
    if fetch_tutorial is None:
        batch = _fetch_problem_tutorial_fragments(cid, codes)
    else:
        batch = _tutorial_batch_from_responses(codes, fetch_tutorial)
    localizer = asset_localizer or localize_editorial_assets
    return build_editorial_document(
        cid,
        source_url,
        base_html,
        batch,
        localizer,
    )
