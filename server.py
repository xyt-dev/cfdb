#!/usr/bin/env python3
"""Serve static UI, metadata, solutions, and read-only v2 content APIs."""
from concurrent.futures import ThreadPoolExecutor
import http.server
import hashlib
from pathlib import Path
import json
import os
import subprocess
import sys
import threading
import urllib.parse

import cfcrawl
from content_asset_policy import (  # pyright: ignore[reportMissingImports]
    ASSET_CONTENT_TYPES,
    asset_magic_is_valid,
    parse_asset_name,
)
from content_cache import ContentStatus, ContentStore  # pyright: ignore[reportMissingImports]
from editorial_rebuild import update_editorials
from statement_rebuild import update_statements  # pyright: ignore[reportMissingImports]
from editorial_render import render_editorial_html
from statement_render import render_statement_html  # pyright: ignore[reportMissingImports]

ROOT = os.path.dirname(os.path.abspath(__file__))
STATEMENT_V2_ROOT = Path(cfcrawl.STATEMENT_DIR) / "v2"
EDITORIAL_V2_ROOT = Path(cfcrawl.EDITORIAL_DIR) / "v2"
try:
    with open(os.path.join(ROOT, "problems.json"), encoding="utf-8") as f:
        PROBLEMS = json.load(f)
except (OSError, json.JSONDecodeError) as e:
    print(f"❌ 无法加载 problems.json: {e}")
    PROBLEMS = []
try:
    PORT = int(os.environ.get("CFDB_PORT", "8765"))
except ValueError:
    PORT = 8765


# Background crawl progress exposed by /api/progress.
crawl_state = {
    "stage": "idle",
    "done": 0,
    "total": 0,
    "cached": 0,
    "fetched": 0,
    "failed": 0,
    "contentStatus": {"statement": "idle", "editorial": "idle"},
    "content": {},
}
_crawl_state_lock = threading.Lock()


def _content_progress_callback(content_kind: str):
    def update(content_id: str, status: ContentStatus, done: int, total: int) -> None:
        with _crawl_state_lock:
            detail = crawl_state["content"].setdefault(
                content_kind,
                {"done": 0, "total": 0, "failed": 0},
            )
            detail.update(
                currentId=content_id,
                currentStatus=status.value,
                done=done,
                total=total,
            )
            if status in {
                ContentStatus.TRANSIENT_FAILURE,
                ContentStatus.INVALID_STRUCTURE,
            }:
                detail["failed"] = detail.get("failed", 0) + 1
            crawl_state["stage"] = "content"
            crawl_state["done"] = sum(
                item.get("done", 0) for item in crawl_state["content"].values()
            )
            crawl_state["total"] = sum(
                item.get("total", 0) for item in crawl_state["content"].values()
            )
            crawl_state["fetched"] = crawl_state["done"]
            crawl_state["failed"] = sum(
                item.get("failed", 0) for item in crawl_state["content"].values()
            )

    return update


def _record_content_update(content_kind: str, report: dict) -> None:
    counts = report.get("statusCounts")
    if not isinstance(counts, dict):
        raise ValueError(f"{content_kind} update report has invalid status counts")
    completed_value = report.get("completed")
    completed = isinstance(completed_value, bool) and completed_value
    with _crawl_state_lock:
        crawl_state["contentStatus"][content_kind] = (
            "complete" if completed else "partial"
        )
        crawl_state["content"][content_kind] = {
            **crawl_state["content"].get(content_kind, {}),
            "statusCounts": dict(counts),
            "expectedCount": report.get("expectedCount", 0),
            "attemptedCount": report.get("attemptedCount", 0),
            "publishedCount": report.get("publishedCount", 0),
            "failed": report.get("failedCount", 0),
            "completed": completed,
        }
        crawl_state["cached"] = sum(
            item.get("statusCounts", {}).get("ready", 0)
            for item in crawl_state["content"].values()
        )
        crawl_state["failed"] = sum(
            item.get("failed", 0) for item in crawl_state["content"].values()
        )


def _update_content(content_kind: str, root: Path, updater) -> None:
    with _crawl_state_lock:
        crawl_state["contentStatus"][content_kind] = "crawling"
    print(f"[auto-update] crawling missing {content_kind} content...")
    try:
        report = updater(
            cache_root=root,
            progress_callback=_content_progress_callback(content_kind),
        )
        _record_content_update(content_kind, report)
    except Exception as error:
        with _crawl_state_lock:
            crawl_state["contentStatus"][content_kind] = "error"
            crawl_state["content"][content_kind] = {"error": str(error)}
        print(f"[auto-update] {content_kind} crawl failed: {error}")
        return
    counts = report["statusCounts"]
    print(
        f"[auto-update] {content_kind}: ready {counts.get('ready', 0)} | "
        f"known_absent {counts.get('known_absent', 0)} | "
        f"failed {report.get('failedCount', 0)}"
    )


def _reload_problems() -> None:
    global PROBLEMS
    try:
        with open(os.path.join(ROOT, "problems.json"), encoding="utf-8") as problem_file:
            value = json.load(problem_file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("unable to reload problems.json") from error
    if not isinstance(value, list):
        raise ValueError("problems.json must contain a list")
    PROBLEMS = value


def auto_update():
    """Refresh metadata, then crawl both content kinds from any store state."""
    try:
        with _crawl_state_lock:
            crawl_state.clear()
            crawl_state.update(
                stage="meta",
                done=0,
                total=0,
                cached=0,
                fetched=0,
                failed=0,
                contentStatus={"statement": "idle", "editorial": "idle"},
                content={},
            )
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "update.py")],
            capture_output=True,
            timeout=300,
        )
        output = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
        ok = result.returncode == 0
        print(f"[auto-update] {'metadata refreshed' if ok else 'metadata refresh failed'}")
        for line in output.splitlines()[-2:]:
            print(f"  {line}")
        if not ok:
            with _crawl_state_lock:
                crawl_state["stage"] = "error"
                crawl_state["error"] = "metadata refresh failed"
            return
        _reload_problems()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _update_content,
                    "statement",
                    STATEMENT_V2_ROOT,
                    update_statements,
                ),
                executor.submit(
                    _update_content,
                    "editorial",
                    EDITORIAL_V2_ROOT,
                    update_editorials,
                ),
            ]
            for future in futures:
                future.result()
        with _crawl_state_lock:
            crawl_state["stage"] = "done"
        print("[auto-update] all content crawls finished")
    except Exception as error:
        import traceback

        with _crawl_state_lock:
            crawl_state["stage"] = "error"
            crawl_state["error"] = str(error)
        print(f"[auto-update] error: {error}", flush=True)
        traceback.print_exc()


def _valid_ref(cid: str, idx: str) -> bool:
    """题号参数校验：contestId 为 1-6 位数字，index 为字母数字（A / A1 / 01 均可）"""
    if not (cid.isdigit() and 0 < len(cid) <= 6):
        return False
    if not idx:
        return False
    return bool(idx.isalnum() and 0 < len(idx) <= 3)


def _valid_contest_id(contest_id: str) -> bool:
    return contest_id.isdigit() and 0 < len(contest_id) <= 6


def _content_error(content_kind: str, status: str, error: str) -> dict:
    return {
        "format": None,
        "contentKind": content_kind,
        "html": None,
        "status": status,
        "known": False,
        "error": error,
    }


def _build_content_payload(
    content_id: str,
    *,
    content_kind: str,
    root: Path,
    renderer,
    document_validator=None,
) -> dict:
    store = ContentStore(root, content_kind)
    document_path = store.document_path(content_id)
    if not document_path.is_file():
        status_entry = store.item_status(content_id)
        status = status_entry.get("status")
        if status == ContentStatus.KNOWN_ABSENT.value:
            return {
                "format": None,
                "contentKind": content_kind,
                "html": None,
                "status": "known_absent",
                "known": True,
            }
        evidence = status_entry.get("evidence")
        status_error = None
        if isinstance(evidence, dict):
            status_error = evidence.get("error") or evidence.get("errors")
        if status in {
            ContentStatus.TRANSIENT_FAILURE.value,
            ContentStatus.INVALID_STRUCTURE.value,
        }:
            return _content_error(
                content_kind,
                str(status),
                str(status_error or status),
            )
        return _content_error(
            content_kind,
            "pending",
            f"{content_kind} has not been crawled yet",
        )
    try:
        document = store.load_document(content_id)
        if document_validator is not None and not document_validator(document):
            return _content_error(content_kind, "invalid_ref", "invalid ref")
        html = renderer(document)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        return _content_error(content_kind, "invalid_structure", str(error))

    payload = {
        "format": "html",
        "contentKind": content_kind,
        "schema": document.schema,
        "html": html,
        "url": getattr(document, "source_url"),
        "known": True,
        "status": "ready",
    }
    source_kind = getattr(document, "source_kind", None)
    if source_kind is not None:
        payload["sourceKind"] = source_kind
    return payload


def build_editorial_payload(contest_id, *, cache_root=None) -> dict:
    """Read one directly published editorial document."""
    contest_id = str(contest_id)
    if not _valid_contest_id(contest_id):
        return _content_error("editorial", "invalid_ref", "invalid ref")
    root = Path(cache_root) if cache_root is not None else EDITORIAL_V2_ROOT
    return _build_content_payload(
        contest_id,
        content_kind="editorial",
        root=root,
        renderer=render_editorial_html,
    )


def build_statement_payload(problem_code, *, cache_root=None) -> dict:
    """Read one directly published statement document."""
    problem_code = str(problem_code)
    if not problem_code or len(problem_code) > 9 or not problem_code.isalnum() or not problem_code[0].isdigit():
        return _content_error("statement", "invalid_ref", "invalid ref")
    root = Path(cache_root) if cache_root is not None else STATEMENT_V2_ROOT
    return _build_content_payload(
        problem_code,
        content_kind="statement",
        root=root,
        renderer=render_statement_html,
    )


def _build_statement_payload_from_parts(contest_id: str, index: str) -> dict:
    if not _valid_ref(contest_id, index):
        return _content_error("statement", "invalid_ref", "invalid ref")
    return _build_content_payload(
        f"{contest_id}{index}",
        content_kind="statement",
        root=STATEMENT_V2_ROOT,
        renderer=render_statement_html,
        document_validator=lambda document: (
            getattr(document, "contest_id", None) == contest_id
            and getattr(document, "index", None) == index
        ),
    )


def _read_content_asset(
    content_kind: str,
    raw_name: str,
    root: Path,
) -> tuple[bytes, str, dict[str, str]]:
    if urllib.parse.unquote(raw_name) != raw_name:
        raise ValueError("encoded asset name is not allowed")
    identity = parse_asset_name(raw_name, content_kind=content_kind)
    if identity is None or Path(raw_name).name != raw_name:
        raise ValueError("invalid asset name")
    asset_path = root / "assets" / identity.name
    if asset_path.is_symlink() or not asset_path.is_file():
        raise FileNotFoundError(raw_name)
    payload = asset_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != identity.digest:
        raise FileNotFoundError(raw_name)
    if not asset_magic_is_valid(identity.extension, payload):
        raise FileNotFoundError(raw_name)
    headers = {"X-Content-Type-Options": "nosniff"}
    if identity.extension == "pdf":
        headers["Content-Disposition"] = f'attachment; filename="{raw_name}"'
    return payload, ASSET_CONTENT_TYPES[identity.extension], headers


def _payload_http_status(payload: dict) -> int:
    status = payload.get("status")
    if status == "invalid_ref":
        return 400
    if status == "pending":
        return 202
    if status == "transient_failure":
        return 503
    if status == "invalid_structure":
        return 500
    return 200


def _ready_document_ids(root: Path, content_kind: str) -> set[str]:
    try:
        return ContentStore(root, content_kind).document_ids()
    except (OSError, ValueError, TypeError, KeyError):
        return set()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def _send(
        self,
        code,
        body,
        ctype,
        *,
        allow_opaque_origin=False,
        extra_headers=None,
        cache_control="no-store",
    ):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        if allow_opaque_origin and self.headers.get("Origin") == "null":
            self.send_header("Access-Control-Allow-Origin", "null")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            try:
                with open(os.path.join(ROOT, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"index.html not found", "text/plain")
        elif u.path == "/reader_payload.js":
            try:
                with open(os.path.join(ROOT, "reader_payload.js"), "rb") as f:
                    self._send(200, f.read(), "application/javascript; charset=utf-8")
            except OSError:
                self._send(404, b"not found", "text/plain")
        elif u.path.endswith(".html") and "/" not in u.path[1:-5]:
            # 通用静态页面（test.html 等调试页）
            try:
                with open(os.path.join(ROOT, u.path[1:]), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(404, b"not found", "text/plain")
        elif u.path.startswith("/statement-assets/") or u.path.startswith("/editorial-assets/"):
            is_statement = u.path.startswith("/statement-assets/")
            content_kind = "statement" if is_statement else "editorial"
            root = STATEMENT_V2_ROOT if is_statement else EDITORIAL_V2_ROOT
            raw_name = u.path.split("/", 2)[2]
            if urllib.parse.unquote(raw_name) != raw_name or parse_asset_name(raw_name) is None:
                self._send(400, b"invalid asset name", "text/plain")
            else:
                try:
                    body, ctype, headers = _read_content_asset(content_kind, raw_name, root)
                except (OSError, ValueError, TypeError, KeyError):
                    self._send(404, b"not found", "text/plain")
                else:
                    self._send(
                        200,
                        body,
                        ctype,
                        extra_headers=headers,
                        cache_control="public, max-age=31536000, immutable",
                    )
        elif u.path.startswith("/vendor/"):
            # 本地静态资源（字体、脚本与界面图片）
            name = os.path.basename(u.path)
            vpath = os.path.join(ROOT, "vendor", name)
            try:
                with open(vpath, "rb") as f:
                    if name.endswith(".js"):
                        ctype = "application/javascript"
                    elif name.endswith((".otf", ".ttf")):
                        ctype = "font/otf" if name.endswith(".otf") else "font/ttf"
                    elif name.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
                        ctype = {
                            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                            ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
                        }[os.path.splitext(name)[1].lower()]
                    else:
                        ctype = "application/octet-stream"
                    self._send(
                        200,
                        f.read(),
                        ctype,
                        allow_opaque_origin=name.endswith((".otf", ".ttf")),
                    )
            except OSError:
                self._send(404, b"not found", "text/plain")
        elif u.path == "/api/problems":
            # hasFile: a directly published statement exists | hasSolution: solution code exists
            cached = _ready_document_ids(STATEMENT_V2_ROOT, "statement")
            solved = set()
            try:
                for name in os.listdir(cfcrawl.SOLUTION_DIR):
                    solved.add(name.split(".")[0])
            except OSError:
                solved.clear()
            enriched = [{**p, "hasFile": p["id"] in cached,
                         "hasSolution": p["id"] in solved} for p in PROBLEMS]
            self._send(200, json.dumps(enriched).encode(), "application/json")
        elif u.path == "/api/statement":
            q = urllib.parse.parse_qs(u.query)
            cid, idx = q.get("contestId", [""])[0], q.get("index", [""])[0]
            payload = _build_statement_payload_from_parts(cid, idx)
            self._send(
                _payload_http_status(payload),
                json.dumps(payload).encode(),
                "application/json",
            )
        elif u.path == "/api/progress":
            self._send(200, json.dumps(crawl_state).encode(), "application/json")
        elif u.path == "/api/editorial":
            q = urllib.parse.parse_qs(u.query)
            cid = q.get("contestId", [""])[0]
            payload = build_editorial_payload(cid)
            self._send(
                _payload_http_status(payload),
                json.dumps(payload).encode(),
                "application/json",
            )
        elif u.path == "/api/solution":
            q = urllib.parse.parse_qs(u.query)
            cid, idx = q.get("contestId", [""])[0], q.get("index", [""])[0]
            if not _valid_ref(cid, idx):
                self._send(400, json.dumps({"files": [], "error": "invalid ref"}).encode(), "application/json")
                return
            files = cfcrawl.list_solutions(cid, idx)
            for f in files:
                f["content"] = cfcrawl.read_solution(f["name"]) or ""
            self._send(200, json.dumps({"files": files}).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_OPTIONS(self):
        self.send_response(200)
        # 局域网工具：允许任意来源（本机/局域网设备）
        origin = self.headers.get("Origin", "")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()


if __name__ == "__main__":
    try:
        reconfigure_stdout = getattr(sys.stdout, "reconfigure", None)
        if reconfigure_stdout is not None:
            reconfigure_stdout(line_buffering=True)
    except OSError as error:
        print(f"stdout line buffering unavailable: {error}", file=sys.stderr)
    print(f"cfdb 服务器启动: http://localhost:{PORT}", flush=True)
    try:
        import socket
        host_ip = socket.gethostbyname(socket.gethostname())
        if not host_ip.startswith("127."):
            print(f"局域网访问:   http://{host_ip}:{PORT}", flush=True)
    except Exception as error:
        print(f"局域网地址检测失败: {error}", file=sys.stderr)
    print(f"题目数: {len(PROBLEMS)} | 题面目录: {cfcrawl.STATEMENT_DIR} | 解题目录: {cfcrawl.SOLUTION_DIR}")
    threading.Thread(target=auto_update, daemon=True).start()
    print("后台刷新元数据中...")
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError as e:
        print(f"❌ 端口 {PORT} 已被占用: {e}")
        print(f"   cfdb 可能已在运行 → 直接访问 http://localhost:{PORT}")
        print("   如需重启: pkill -f server.py 后再启动")
        sys.exit(1)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
