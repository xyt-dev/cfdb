#!/usr/bin/env python3
"""cfdb 本地服务器：静态页面 + 题目元数据 API + 题面/题解（md 文件读取 + 按需爬取）
启动时自动后台刷新元数据（update.py）
"""
import http.server
import hashlib
from pathlib import Path
import re
import json
import os
import subprocess
import sys
import threading
import urllib.parse

import cfcrawl
from content_cache import ContentStatus, load_active_generation  # pyright: ignore[reportMissingImports]
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


# 爬取进度状态（/api/progress 暴露给前端）
crawl_state = {"stage": "idle", "done": 0, "total": 0, "cached": 0, "fetched": 0, "failed": 0}


def _on_progress(done, total, cached, fetched, failed):
    crawl_state.update(done=done, total=total, cached=cached,
                       fetched=fetched, failed=failed)


def _record_content_update(content_kind: str, report: dict) -> None:
    counts = report.get("counts")
    if not isinstance(counts, dict):
        raise ValueError(f"{content_kind} update report has invalid counts")
    activated_value = report.get("activated")
    activated = isinstance(activated_value, bool) and activated_value
    generation_id = report.get("generationId")
    crawl_state["contentStatus"][content_kind] = (
        "updated" if activated else "update_failed"
    )
    crawl_state["generations"][content_kind] = {
        "generationId": generation_id,
        "statusCounts": dict(counts),
        "activated": activated,
    }
    numeric_counts = [value for value in counts.values() if isinstance(value, int)]
    crawl_state.update(
        generationId=generation_id,
        statusCounts=dict(counts),
        total=sum(numeric_counts),
        cached=counts.get("ready", 0),
        fetched=counts.get("ready", 0),
        failed=(
            counts.get("transient_failure", 0)
            + counts.get("invalid_structure", 0)
        ),
    )


def _update_active_content(content_kind: str, root: Path, updater) -> None:
    if not (root / "current.json").is_file():
        crawl_state["contentStatus"][content_kind] = "v2_not_initialized"
        print(f"[auto-update] ⏭️ {content_kind} v2 尚未初始化，跳过增量更新")
        return
    crawl_state["stage"] = f"{content_kind}s"
    print(f"[auto-update] 构建增量 v2 {content_kind} 代际...")
    try:
        report = updater()
        _record_content_update(content_kind, report)
    except Exception as error:
        crawl_state["contentStatus"][content_kind] = "error"
        crawl_state["generations"][content_kind] = {"error": str(error)}
        print(f"[auto-update] ⚠️ {content_kind} 增量更新失败: {error}")
        return
    counts = crawl_state["generations"][content_kind]["statusCounts"]
    print(
        f"[auto-update] ✅ v2 {content_kind} 代际 "
        f"{report.get('generationId')}: ready {counts.get('ready', 0)} | "
        f"known_absent {counts.get('known_absent', 0)} | "
        f"failed {counts.get('transient_failure', 0) + counts.get('invalid_structure', 0)}"
    )


def auto_update():
    """Refresh metadata, then increment only initialized v2 content roots."""
    try:
        crawl_state.clear()
        crawl_state.update(
            stage="meta",
            done=0,
            total=0,
            cached=0,
            fetched=0,
            failed=0,
            contentStatus={},
            generations={},
        )
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "update.py")],
            capture_output=True,
            timeout=300,
        )
        output = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
        ok = result.returncode == 0
        print(f"[auto-update] {'✅ 元数据已刷新' if ok else '⚠️ 元数据更新失败'}")
        for line in output.splitlines()[-2:]:
            print(f"  {line}")
        if not ok:
            print("[auto-update] ⏭️ 跳过内容更新（元数据刷新失败）")
            crawl_state["stage"] = "idle"
            return

        _update_active_content("statement", STATEMENT_V2_ROOT, update_statements)
        _update_active_content("editorial", EDITORIAL_V2_ROOT, update_editorials)
        crawl_state["stage"] = "done"
        print("[auto-update] ✅ 全部完成")
    except Exception as error:
        import traceback

        crawl_state["stage"] = "error"
        crawl_state["error"] = str(error)
        print(f"[auto-update] ⚠️ 异常: {error}", flush=True)
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


def _uninitialized(content_kind: str) -> dict:
    return _content_error(
        content_kind,
        "v2_not_initialized",
        f"{content_kind} v2 is not initialized",
    )


def _build_content_payload(
    content_id: str,
    *,
    content_kind: str,
    root: Path,
    renderer,
    document_validator=None,
) -> dict:
    if not (root / "current.json").is_file():
        return _uninitialized(content_kind)
    try:
        store = load_active_generation(root)
        if store is None:
            return _uninitialized(content_kind)
        if store.manifest["contentKind"] != content_kind:
            return _content_error(
                content_kind,
                "invalid_structure",
                "active generation has the wrong content kind",
            )
        entry = store.manifest["entries"].get(content_id)
        if not isinstance(entry, dict):
            return _content_error(
                content_kind,
                "invalid_structure",
                f"active manifest missing {content_kind}",
            )
        status = entry.get("status")
        if status == ContentStatus.KNOWN_ABSENT.value:
            return {
                "format": None,
                "contentKind": content_kind,
                "html": None,
                "status": "known_absent",
                "known": True,
            }
        if status != ContentStatus.READY.value:
            return _content_error(
                content_kind,
                "invalid_structure",
                f"active {content_kind} entry is nonterminal",
            )
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
    """Read one editorial from the active immutable v2 generation."""
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
    """Read one statement from the active immutable v2 generation."""
    problem_code = str(problem_code)
    match = re.fullmatch(r"([0-9]{1,6})([A-Za-z0-9]{1,3})", problem_code)
    if match is None or not _valid_ref(match.group(1), match.group(2)):
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


_ASSET_NAME_RE = re.compile(
    r"(?P<digest>[0-9a-f]{64})\.(?P<extension>png|jpg|jpeg|gif|webp|pdf)"
)
_ASSET_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
}


def _asset_magic_is_valid(extension: str, payload: bytes) -> bool:
    if extension == "png":
        return payload.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {"jpg", "jpeg"}:
        return payload.startswith(b"\xff\xd8\xff")
    if extension == "gif":
        return payload.startswith((b"GIF87a", b"GIF89a"))
    if extension == "webp":
        return len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP"
    return extension == "pdf" and payload.startswith(b"%PDF-")


def _read_active_asset(
    content_kind: str,
    raw_name: str,
    root: Path,
) -> tuple[bytes, str, dict[str, str]]:
    if urllib.parse.unquote(raw_name) != raw_name:
        raise ValueError("encoded asset name is not allowed")
    match = _ASSET_NAME_RE.fullmatch(raw_name)
    if match is None or Path(raw_name).name != raw_name:
        raise ValueError("invalid asset name")
    extension = match.group("extension")
    if content_kind == "editorial" and extension == "pdf":
        raise ValueError("editorial PDF assets are not allowed")
    store = load_active_generation(root)
    if store is None or store.manifest["contentKind"] != content_kind:
        raise FileNotFoundError(raw_name)
    asset_path = store.path / "assets" / raw_name
    if asset_path.is_symlink() or not asset_path.is_file():
        raise FileNotFoundError(raw_name)
    payload = asset_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != match.group("digest"):
        raise FileNotFoundError(raw_name)
    if not _asset_magic_is_valid(extension, payload):
        raise FileNotFoundError(raw_name)
    headers = {"X-Content-Type-Options": "nosniff"}
    if extension == "pdf":
        headers["Content-Disposition"] = f'attachment; filename="{raw_name}"'
    return payload, _ASSET_CONTENT_TYPES[extension], headers


def _payload_http_status(payload: dict) -> int:
    status = payload.get("status")
    if status == "invalid_ref":
        return 400
    if status == "v2_not_initialized":
        return 503
    if status == "invalid_structure":
        return 500
    return 200


def _active_ready_ids(root: Path, content_kind: str) -> set[str]:
    try:
        store = load_active_generation(root)
    except (OSError, ValueError, TypeError, KeyError):
        return set()
    if store is None or store.manifest.get("contentKind") != content_kind:
        return set()
    return {
        content_id
        for content_id, entry in store.manifest["entries"].items()
        if isinstance(entry, dict)
        and entry.get("status") == ContentStatus.READY.value
    }


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
            if urllib.parse.unquote(raw_name) != raw_name or _ASSET_NAME_RE.fullmatch(raw_name) is None:
                self._send(400, b"invalid asset name", "text/plain")
            else:
                try:
                    body, ctype, headers = _read_active_asset(content_kind, raw_name, root)
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
        elif u.path.startswith("/eimages/") or u.path.startswith("/images/"):
            # 题面/题解图片（content-type 按扩展名）
            base = cfcrawl.EDITORIAL_IMAGE_DIR if u.path.startswith("/eimages/") else cfcrawl.IMAGE_DIR
            rel = u.path.split("/", 2)[2]
            img_path = os.path.join(base, os.path.basename(rel))
            ext = os.path.splitext(img_path)[1].lower()
            ctype = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
            }.get(ext, "application/octet-stream")
            try:
                with open(img_path, "rb") as f:
                    self._send(200, f.read(), ctype)
            except OSError:
                self._send(404, b"not found", "text/plain")
        elif u.path.startswith("/vendor/"):
            # 本地静态资源（marked 等）
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
            # hasFile: 题面已预爬 | hasSolution: 已有自己的解题代码
            cached = _active_ready_ids(STATEMENT_V2_ROOT, "statement")
            solved = set()
            try:
                for n in os.listdir(cfcrawl.SOLUTION_DIR):
                    solved.add(n.split(".")[0])
            except OSError:
                pass
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
    except OSError:
        pass
    print(f"cfdb 服务器启动: http://localhost:{PORT}", flush=True)
    try:
        import socket
        host_ip = socket.gethostbyname(socket.gethostname())
        if not host_ip.startswith("127."):
            print(f"局域网访问:   http://{host_ip}:{PORT}", flush=True)
    except Exception:
        pass
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
