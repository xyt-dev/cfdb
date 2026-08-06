#!/usr/bin/env python3
"""cfdb 本地服务器：静态页面 + 题目元数据 API + 题面/题解（md 文件读取 + 按需爬取）
启动时自动后台刷新元数据（update.py）
"""
import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.parse

import cfcrawl

ROOT = os.path.dirname(os.path.abspath(__file__))
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


def auto_update():
    """启动后台任务：刷新元数据 → 增量爬缺失题面 → 增量爬缺失题解"""
    try:
        crawl_state["stage"] = "meta"
        r = subprocess.run([sys.executable, os.path.join(ROOT, "update.py")],
                           capture_output=True, timeout=300)
        out = (r.stdout + r.stderr).decode("utf-8", "replace").strip()
        print(f"[auto-update] {'✅ 元数据已刷新' if r.returncode == 0 else '⚠️ 元数据更新失败'}")
        for line in out.splitlines()[-2:]:
            print(f"  {line}")

        crawl_state["stage"] = "statements"
        print("[auto-update] 增量爬取缺失题面...")
        total, cached, fetched = cfcrawl.fetch_all_statements(delay=0.2, on_progress=_on_progress)
        print(f"[auto-update] ✅ 题面: 共 {total} | 已有 {cached} | 新爬 {fetched}")

        crawl_state["stage"] = "editorials"
        print("[auto-update] 增量爬取缺失题解...")
        total, cached, fetched = cfcrawl.fetch_all_editorials(delay=0.2, on_progress=_on_progress)
        print(f"[auto-update] ✅ 题解: 共 {total} 场比赛 | 已有 {cached} | 新爬 {fetched}")

        crawl_state["stage"] = "done"
        print("[auto-update] ✅ 全部完成")
    except Exception as e:
        import traceback
        crawl_state["stage"] = "error"
        crawl_state["error"] = str(e)
        print(f"[auto-update] ⚠️ 异常: {e}", flush=True)
        traceback.print_exc()


def _valid_ref(cid: str, idx: str) -> bool:
    """题号参数校验：contestId 为 1-6 位数字，index 为字母开头（可含数字，如 A1/B2/F1）"""
    if not (cid.isdigit() and 0 < len(cid) <= 6):
        return False
    if not idx:
        return False
    return bool(idx[0].isalpha() and idx.isalnum() and 0 < len(idx) <= 3)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 开发期禁用缓存：改代码后刷新即生效，避免旧版 JS 残留
        self.send_header("Cache-Control", "no-store")
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
        elif u.path.endswith(".html") and "/" not in u.path[1:-5]:
            # 通用静态页面（test.html 等调试页）
            try:
                with open(os.path.join(ROOT, u.path[1:]), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(404, b"not found", "text/plain")
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
                    ctype = "application/javascript" if name.endswith(".js") else "application/octet-stream"
                    self._send(200, f.read(), ctype)
            except OSError:
                self._send(404, b"not found", "text/plain")
        elif u.path == "/api/problems":
            # hasFile: 题面已预爬 | hasSolution: 已有自己的解题代码
            cached = set()
            solved = set()
            try:
                cached = {f[:-3] for f in os.listdir(cfcrawl.STATEMENT_DIR) if f.endswith(".md")}
            except OSError:
                pass
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
            if not _valid_ref(cid, idx):
                self._send(400, json.dumps({"md": None, "error": "invalid ref"}).encode(), "application/json")
            else:
                md = cfcrawl.read_statement_md(cid, idx)
                self._send(200, json.dumps({"md": md}).encode(), "application/json")
        elif u.path == "/api/progress":
            self._send(200, json.dumps(crawl_state).encode(), "application/json")
        elif u.path == "/api/editorial":
            q = urllib.parse.parse_qs(u.query)
            cid = q.get("contestId", [""])[0]
            if not (cid.isdigit() and 0 < len(cid) <= 6):
                self._send(400, json.dumps({"md": None, "error": "invalid ref"}).encode(), "application/json")
            else:
                md = cfcrawl.read_editorial_md(cid) or cfcrawl.fetch_editorial_md(cid)
                url = cfcrawl.read_editorial_url(cid)
                if md and md.startswith("<!-- url:"):
                    # 剥离首行注释（纯 md 返回给前端）
                    idx = md.find("\n")
                    md = md[idx + 1:] if idx >= 0 else ""
                self._send(200, json.dumps({"md": md, "url": url}).encode(), "application/json")
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
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
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
