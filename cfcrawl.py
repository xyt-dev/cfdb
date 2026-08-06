#!/usr/bin/env python3
"""cfcrawl — 共享爬取库：题面/题解抓取、转 md、本地缓存
目录：
  {ROOT}/statements/{contestId}{index}.md   题面
  {ROOT}/solutions/{contestId}{index}.*     自己的解题代码（任意扩展名）
"""
import json
import os
import re
import subprocess
import sys
import time
import zlib

import html2md

ROOT = os.path.dirname(os.path.abspath(__file__))
STATEMENT_DIR = os.path.join(ROOT, "statements")
SOLUTION_DIR = os.path.join(ROOT, "solutions")
EDITORIAL_DIR = os.path.join(ROOT, "editorials")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
CURL = os.environ.get("CFGEN_CURL") or "curl"


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


def _valid_statement(html: str) -> bool:
    return "problem-statement" in html and "Just a moment" not in html


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
def statement_path(cid, idx) -> str:
    return os.path.join(STATEMENT_DIR, f"{cid}{idx}.md")


IMAGE_DIR = os.path.join(STATEMENT_DIR, "images")


def _flatten_transparent_png(path: str) -> bool:
    """透明 PNG → 白色背景（项目功能，纯标准库 zlib/struct——零外部依赖）。
    支持 colortype 6（RGBA）/ 4（gray+alpha）/ 3（palette+tRNS），8-bit 非隔行。
    透明像素按 alpha 合成到白色；不透明/不支持格式返回 False（原图保留）"""
    try:
        with open(path, "rb") as f:
            data = f.read()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        pos = 8
        idat = b""
        plte = trns = None
        w = h = bitdepth = colortype = None
        while pos + 8 <= len(data):
            ln = int.from_bytes(data[pos:pos + 4], "big")
            typ = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + ln]
            if typ == b"IHDR":
                w = int.from_bytes(body[0:4], "big")
                h = int.from_bytes(body[4:8], "big")
                bitdepth = body[8]
                colortype = body[9]
                if body[12] != 0:
                    return False  # 隔行（Adam7）不支持
            elif typ == b"IDAT":
                idat += body
            elif typ == b"PLTE":
                plte = body
            elif typ == b"tRNS":
                trns = body
            elif typ == b"IEND":
                break
            pos += 12 + ln
        if w is None or h is None or not idat:
            return False
        if colortype == 3:
            if bitdepth not in (1, 2, 4, 8):
                return False  # palette 支持 1/2/4/8-bit
        elif colortype in (4, 6):
            if bitdepth != 8:
                return False
        elif colortype == 2:
            if not trns:
                return False  # RGB 无透明色键——不处理
        else:
            return False  # 仅透明格式需要处理
        raw = zlib.decompress(idat)
        if colortype == 3:
            stride = (w * bitdepth + 7) // 8
            channels = 1  # unfilter 的 bpp（低位 palette 用 1 近似——Sub/Paeth 罕见）
        else:
            channels = {6: 4, 4: 2, 2: 3}[colortype]
            stride = w * channels
        rows = []
        prev = bytearray(stride)
        p2 = 0
        for _ in range(h):
            ft = raw[p2]
            row = bytearray(raw[p2 + 1:p2 + 1 + stride])
            p2 += 1 + stride
            if ft == 1:  # Sub
                for i in range(channels, stride):
                    row[i] = (row[i] + row[i - channels]) & 0xFF
            elif ft == 2:  # Up
                for i in range(stride):
                    row[i] = (row[i] + prev[i]) & 0xFF
            elif ft == 3:  # Average
                for i in range(stride):
                    left = row[i - channels] if i >= channels else 0
                    row[i] = (row[i] + ((left + prev[i]) >> 1)) & 0xFF
            elif ft == 4:  # Paeth
                for i in range(stride):
                    a = row[i - channels] if i >= channels else 0
                    b = prev[i]
                    c = prev[i - channels] if i >= channels else 0
                    pv = a + b - c
                    pa, pb, pc = abs(pv - a), abs(pv - b), abs(pv - c)
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    row[i] = (row[i] + pr) & 0xFF
            rows.append(row)
            prev = row
        # alpha 合成到白色 → RGB
        out = bytearray(w * h * 3)
        o = 0
        if colortype == 6:  # RGBA
            for row in rows:
                for i in range(0, stride, 4):
                    r, g, b, a = row[i], row[i + 1], row[i + 2], row[i + 3]
                    if a == 255:
                        out[o], out[o + 1], out[o + 2] = r, g, b
                    elif a == 0:
                        out[o], out[o + 1], out[o + 2] = 255, 255, 255
                    else:
                        inv = 255 - a
                        out[o] = (r * a + 255 * inv + 127) // 255
                        out[o + 1] = (g * a + 255 * inv + 127) // 255
                        out[o + 2] = (b * a + 255 * inv + 127) // 255
                    o += 3
        elif colortype == 4:  # gray + alpha
            for row in rows:
                for i in range(0, stride, 2):
                    g, a = row[i], row[i + 1]
                    if a == 255:
                        v = g
                    elif a == 0:
                        v = 255
                    else:
                        v = (g * a + 255 * (255 - a) + 127) // 255
                    out[o] = out[o + 1] = out[o + 2] = v
                    o += 3
        elif colortype == 2:  # RGB + tRNS 透明色键（tRNS 每色 2 字节 16-bit——8-bit 图取低字节）
            kr, kg, kb = trns[1], trns[3], trns[5]
            for row in rows:
                for i in range(0, stride, 3):
                    r, g, b = row[i], row[i + 1], row[i + 2]
                    if (r, g, b) == (kr, kg, kb):
                        out[o], out[o + 1], out[o + 2] = 255, 255, 255
                    else:
                        out[o], out[o + 1], out[o + 2] = r, g, b
                    o += 3
        else:  # palette + tRNS（含 1/2/4-bit 拆位）
            ncolors = len(plte) // 3 if plte else 0
            pal = [(plte[i * 3], plte[i * 3 + 1], plte[i * 3 + 2]) for i in range(ncolors)]
            alphas = [255] * ncolors
            if trns:
                for i in range(min(len(trns), ncolors)):
                    alphas[i] = trns[i]
            if bitdepth == 8:
                def idx_iter(row):
                    return iter(row)
            else:
                per_byte = 8 // bitdepth
                mask = (1 << bitdepth) - 1
                def idx_iter(row):
                    for byte in row:
                        for sh in range(per_byte - 1, -1, -1):
                            yield (byte >> (sh * bitdepth)) & mask
            for row in rows:
                for idx in idx_iter(row):
                    if idx < ncolors:
                        r, g, b = pal[idx]
                        a = alphas[idx]
                    else:
                        r = g = b = 255
                        a = 255
                    if a == 255:
                        out[o], out[o + 1], out[o + 2] = r, g, b
                    elif a == 0:
                        out[o], out[o + 1], out[o + 2] = 255, 255, 255
                    else:
                        inv = 255 - a
                        out[o] = (r * a + 255 * inv + 127) // 255
                        out[o + 1] = (g * a + 255 * inv + 127) // 255
                        out[o + 2] = (b * a + 255 * inv + 127) // 255
                    o += 3
        # 重编码（filter 0）写回
        enc = bytearray()
        for y in range(h):
            enc.append(0)
            enc += out[y * w * 3:(y + 1) * w * 3]

        def _chunk(typ, body):
            c = len(body).to_bytes(4, "big") + typ + body
            return c + (zlib.crc32(typ + body) & 0xFFFFFFFF).to_bytes(4, "big")

        ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
        new = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
               + _chunk(b"IDAT", zlib.compress(bytes(enc), 6)) + _chunk(b"IEND", b""))
        with open(path, "wb") as f:
            f.write(new)
        return True
    except Exception:
        return False


def _download_image(url: str, name: str, img_dir: str | None = None) -> str | None:
    """下载图片到 {img_dir}/{name}{ext}，返回本地绝对路径"""
    img_dir = img_dir or IMAGE_DIR
    ext = os.path.splitext(url.split("?")[0])[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        ext = ".png"
    path = os.path.join(img_dir, name + ext)
    if os.path.isfile(path):
        return path  # 已下载（断点续传）
    try:
        os.makedirs(img_dir, exist_ok=True)
        r = subprocess.run(
            [CURL, "-sL", "--max-time", "20", "-H", f"User-Agent: {UA}", url],
            capture_output=True, timeout=30)
        if r.returncode == 0 and r.stdout and not r.stdout.startswith(b"<html"):
            with open(path, "wb") as f:
                f.write(r.stdout)
            if ext == ".png":
                _flatten_transparent_png(path)  # 透明 PNG → 白底（深色主题可读）
            return path
    except Exception:
        pass
    return None


def _embed_images(md: str, prefix: str, img_dir: str, url_prefix: str) -> str:
    """把 md 中的图片 URL 下载到本地并替换为相对路径
    prefix: 文件名前缀（题面=题号+字母，题解=比赛号）
    img_dir: 图片保存目录
    url_prefix: md 中使用的相对路径前缀"""
    import re
    counter = [0]

    def repl(m):
        url = m.group(2)
        if not url.startswith(("http://", "https://")):
            return ""  # 非 http(s) 引用直接移除（防 javascript: 等异常 URL）
        counter[0] += 1
        local = _download_image(url, f"{prefix}_{counter[0]}", img_dir)
        if local:
            return f"![{m.group(1)}]({url_prefix}/{os.path.basename(local)})"
        return m.group(0)  # 下载失败：保留原引用（联网时可看）

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl, md)


def read_statement_md(cid, idx) -> str | None:
    """纯读本地题面 md（未预爬返回 None）——server 端用"""
    path = statement_path(cid, idx)
    try:
        if os.path.getsize(path) <= 50:
            return None  # 空壳（爬取异常产物，1 字节 \n）：视为未爬
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _probe_content_type(url: str, timeout: int = 20) -> str | None:
    """HEAD/GET 探测响应 content-type（不下载正文）"""
    try:
        r = subprocess.run(
            [CURL, "-sL", "-o", "/dev/null", "-w", "%{content_type}",
             "--max-time", str(timeout), "-H", f"User-Agent: {UA}", url],
            capture_output=True, timeout=timeout + 10)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", "replace").lower()
    except Exception:
        pass
    return None


def _fetch_statement_pdf(cid: str, idx: str, url: str, timeout: int = 30) -> str | None:
    """PDF 题面：下载 → pdftotext → markdown（公式降级为文本，标注来源）"""
    import tempfile
    pdf = os.path.join(tempfile.gettempdir(), f"cfdb-{cid}{idx}.pdf")
    try:
        os.remove(pdf)
    except OSError:
        pass
    try:
        r = subprocess.run(
            [CURL, "-sL", "--compressed", "-o", pdf, "--max-time", str(timeout),
             "-H", f"User-Agent: {UA}", url],
            capture_output=True, timeout=timeout + 10)
        if r.returncode != 0 or not os.path.exists(pdf) or os.path.getsize(pdf) < 500:
            return None
        t = subprocess.run(["pdftotext", "-layout", pdf, "-"],
                           capture_output=True, timeout=60)
        if t.returncode != 0:
            return None
        text = t.stdout.decode("utf-8", "replace").strip()
        if len(text) < 200:
            return None
        # 标题：找 "Problem A. Name" / "A. Name" 行（PDF 首行常是页码/空行）
        lines = text.split("\n")
        title = f"{cid}{idx}"
        ti = 0
        for i, ln in enumerate(lines[:6]):
            t = ln.strip()
            if re.match(r"^(?:Problem\s+)?[A-Z][A-Z0-9]*\.\s+\S", t):
                title = t
                ti = i
                break
        body = "\n".join(l.rstrip() for l in lines[ti + 1:]).strip()
        md = f"# {title}\n\n> 📄 本场比赛仅有 PDF 题面（无 HTML 版），已用文本提取，公式可能失真\n\n{body}"
        os.makedirs(STATEMENT_DIR, exist_ok=True)
        with open(statement_path(cid, idx), "w", encoding="utf-8") as f:
            f.write(md)
        return md
    except Exception:
        return None
    finally:
        try:
            os.remove(pdf)
        except OSError:
            pass


def fetch_statement_md(cid, idx, retries: int = 3, timeout: int = 30) -> str | None:
    """爬取题面转 md 并缓存 —— 仅 update.py 预爬使用"""
    if not (str(cid).isdigit() and str(idx) and str(idx).isalnum() and len(str(idx)) <= 3):
        return None  # 非法参数直接失败，不触发网络请求（index：A / A1 / 01 均可）
    url = f"https://codeforces.com/contest/{cid}/problem/{idx}"
    # 先探测 content-type：PDF 题面（CF 对无 HTML 题面的比赛只提供 PDF）
    ct = _probe_content_type(url, timeout=timeout)
    if ct and "pdf" in ct:
        return _fetch_statement_pdf(cid, idx, url, timeout=timeout)
    html = fetch_url(url, timeout=timeout, retries=retries)
    if not html or not _valid_statement(html):
        return None
    md = html2md.problem_statement_to_md(html)
    md = _embed_images(md, f"{cid}{idx}", IMAGE_DIR, "images")  # 下载题面图片
    # 空壳保护：转换结果过短（页面异常产物）视为失败，不写空文件（根因修复）
    if not md or len(md.strip()) < 50:
        return None
    try:
        os.makedirs(STATEMENT_DIR, exist_ok=True)
        with open(statement_path(cid, idx), "w", encoding="utf-8") as f:
            f.write(md)
    except OSError:
        pass
    return md


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


def editorial_path(cid) -> str:
    return os.path.join(EDITORIAL_DIR, f"{cid}.md")


FAILED_EDITORIALS = os.path.join(ROOT, "failed_editorials.json")
import threading as _threading
_failed_lock = _threading.Lock()


def _load_failed_editorials() -> set:
    """无 Editorial 的比赛记忆（避免每次启动对 ~1800 场无题解比赛重试）"""
    try:
        with open(FAILED_EDITORIALS, encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, json.JSONDecodeError):
        return set()


def _remember_failed_editorial(cid):
    with _failed_lock:  # 并发写保护（HTTP 线程可能同时调用）
        s = _load_failed_editorials()
        s.add(str(cid))
        try:
            with open(FAILED_EDITORIALS, "w", encoding="utf-8") as f:
                json.dump(sorted(s), f, ensure_ascii=False, indent=1)
        except OSError:
            pass


def read_editorial_md(cid) -> str | None:
    """纯读本地题解 md"""
    path = editorial_path(cid)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def read_editorial_url(cid) -> str | None:
    """从 md 首行注释读取 editorial 原链接（自包含方案）"""
    try:
        with open(editorial_path(cid), encoding="utf-8") as f:
            first = f.readline()
        if first.startswith("<!-- url: "):
            return first[len("<!-- url: "):-len(" -->")].strip() or None
    except OSError:
        pass
    return None


def _fetch_problem_tutorials(cid: str, codes: list) -> dict:
    """通过 /data/problemTutorial API 获取动态 per-problem 题解。
    返回 {problemCode: markdown 内容}（去掉自带标题，避免与占位标题重复）"""
    import tempfile
    jar = os.path.join(tempfile.gettempdir(), f"cfdb-{cid}.cookies")
    try:
        os.remove(jar)
    except OSError:
        pass
    # 1. GET 第一题页面拿会话 cookie + csrf token
    idx0 = codes[0][len(cid):]
    page = subprocess.run(
        [CURL, "-s", "-c", jar, "--max-time", "20",
         "-H", f"User-Agent: {UA}",
         f"https://codeforces.com/contest/{cid}/problem/{idx0}"],
        capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    m = re.search(r"data-csrf='([a-f0-9]+)'", page)
    if not m:
        return {}
    token = m.group(1)
    # 2. 逐个 POST problemCode
    out = {}
    missing = []  # API 明确 success=="false"（官方未发布）——占位删除，不整场丢弃
    for code in codes:
        try:
            r = subprocess.run(
                [CURL, "-s", "-b", jar, "-c", jar, "-X", "POST", "--max-time", "20",
                 "-H", f"User-Agent: {UA}",
                 "-H", "X-Requested-With: XMLHttpRequest",
                 "-H", f"X-Csrf-Token: {token}",
                 "--data", f"problemCode={code}",
                 "https://codeforces.com/data/problemTutorial"],
                capture_output=True, timeout=30)
            d = json.loads(r.stdout)
            # success 是字符串（"true"/"false"）——"false" 不能当真值
            if d.get("success") in (True, "true") and d.get("html"):
                tmd = html2md.editorial_to_md(d["html"])
                # 保留/补全题号标题：API html 的 <h3>标题</h3> 在 ttypography 外
                # （editorial_to_md 提取 ttypography 时被丢弃）→ 从 h3 提取补上
                if tmd.strip():
                    tm = re.search(r'<h3>.*?>([^<]+)</a>', d["html"])
                    if tm:
                        tmd = f"## {tm.group(1)}\n\n" + tmd.strip()
                    out[code] = tmd.strip()
            elif d.get("success") in (False, "false"):
                missing.append(code)
        except Exception:
            pass  # 网络/解析失败：不算确认缺失（整场 @temp 可重试）
        time.sleep(0.3)  # 限速防反爬
    return out, missing


def _drop_tutorial_placeholder(md: str, idx: str) -> str:
    """API 确认无题解（success=="false"，官方未发布）→ 删除该题的占位符。
    只删占位文本（可能只剩标题壳/空），不整场丢弃其他题"""
    return re.sub(r'\n*Tutorial is loading\.\.\.\n*', '\n', md, count=1)


def _replace_tutorial(md: str, idx: str, tmd: str) -> str:
    """把该题的 'Tutorial is loading...' 占位替换为真实题解。
    精确匹配题号（如 A1）；失败则用首字母 fallback（如 problemCode=F3 但标题是 F）"""
    # 去重：博客原文已有同题号标题（<p><a>1000A - Name</a></p> 转的 h2）时，
    # tmd 自带的题号标题（h3 补全）会造成双标题（如 1000：原文标题 + tmd 标题）。
    # 纯占位博客（1004 等无原文标题）保留 tmd 标题。
    m = re.match(r'^## ([A-Z]?\d{1,4}[A-Z]?)\s*[-—–].*', tmd)
    if m and re.search(r'^## ' + re.escape(m.group(1)) + r'\b', md, re.M):
        rest = tmd.split('\n', 1)
        tmd = rest[1].strip() if len(rest) > 1 else ''
        if not tmd:
            return md
    pat = re.compile(
        r'(\*\*[^*\n]*' + re.escape(idx) + r'[^*\n]*\*\*)\n+Tutorial is loading\.\.\.',
        re.S)
    m = pat.search(md)
    if not m:
        letter = idx[0]
        pat2 = re.compile(
            r'(\*\*[^*\n]*\b' + re.escape(letter) + r'\b[^*\n]*\*\*)\n+Tutorial is loading\.\.\.',
            re.S)
        m = pat2.search(md)
    if not m:
        # 2032 等格式：**Tutorial** 小节标题 + 占位
        pat3 = re.compile(
            r'(\*\*Tutorial\*\*)\n+Tutorial is loading\.\.\.',
            re.S)
        m = pat3.search(md)
    if m:
        return md[:m.start()] + m.group(1) + "\n\n" + tmd + md[m.end():]
    # fallback：无粗体标题的博客（如 1300——占位前没有标题行）→ 直接替换占位符
    # （按 codes 顺序逐个替换，页面顺序与 codes 一致）
    if "Tutorial is loading" in md:
        return md.replace("Tutorial is loading...", tmd, 1)
    return md


def fetch_editorial_md(cid, retries: int = 3, timeout: int = 30) -> str | None:
    """爬取比赛题解转 md 并缓存（contest 页找 editorial 链接 → 爬博客）"""
    cid = str(cid)  # 统一字符串（批量遍历来自 problems.json 的 int）
    contest_url = f"https://codeforces.com/contest/{cid}"
    contest_html = fetch_url(contest_url, timeout=timeout, retries=retries)
    if not contest_html:
        _remember_failed_editorial(f"{cid}@temp")  # 网络失败：临时标记，不误记为无题解
        return None
    # 404/不存在的比赛页检测（避免把错误页当正常处理）
    if ("Contest not found" in contest_html or "does not exist" in contest_html
            or "Just a moment" in contest_html):
        _remember_failed_editorial(f"{cid}@temp")
        return None
    link = _find_editorial_link(contest_html)
    if not link:
        return None
    blog_html = fetch_url(link)
    if not blog_html:
        return None
    # 403/被拦页面：丢弃并标记临时（解封后可重试），避免把错误页写入题解
    if "403 Forbidden" in blog_html or "Just a moment" in blog_html or "nginx/" in blog_html:
        _remember_failed_editorial(f"{cid}@temp")
        return None
    # editorial 未发布时，contest 页 tutorial 链接指向公告博客 → 丢弃并标记（可重试）
    if "Announcement of Codeforces Round" in blog_html:
        _remember_failed_editorial(f"{cid}@announcement")
        return None
    # 收集动态加载的 per-problem tutorial 占位
    codes = []
    if '<div class="problemTutorial"' in blog_html:
        codes = re.findall(r'problemcode="([^"]+)"', blog_html)
    md = html2md.editorial_to_md(blog_html)
    # md 层替换占位符为真实题解（避免 HTML 层 ttypography 嵌套截断）
    if codes and "Tutorial is loading" in md:
        tutorials, missing = _fetch_problem_tutorials(cid, codes)
        for code, tmd in tutorials.items():
            idx = code[len(cid):]  # "1970A1" → "A1"
            md = _replace_tutorial(md, idx, tmd)
        for code in missing:  # API 确认无题解（官方未发布）→ 删占位，不整场丢弃
            idx = code[len(cid):]
            md = _drop_tutorial_placeholder(md, idx)
    # 全局去重题号标题（根本层兜底——原文标题 + tmd 标题重复只留第一个）
    md = html2md._dedupe_problem_headers(md)
    # 写前校验：错误页/占位残留/过短 = 假题解，不写文件（@temp 可重试）
    if not md or len(md.strip()) < 100 or "403 Forbidden" in md \
            or "nginx/" in md or "Tutorial is loading" in md:
        _remember_failed_editorial(f"{cid}@temp")
        return None
    if not md.strip():
        return None
    md = _embed_images(md, f"{cid}", EDITORIAL_IMAGE_DIR, "eimages")  # 下载题解图片
    try:
        os.makedirs(EDITORIAL_DIR, exist_ok=True)
        # 原链接内嵌 md 首行注释（自包含，无需单独 .url 文件）
        with open(editorial_path(cid), "w", encoding="utf-8") as f:
            f.write(f"<!-- url: {link} -->\n{md}")
    except OSError:
        pass
    return md


# ═══ 全量预爬（update.py --statements / --editorials 使用）═══
FAILED_FILE = os.path.join(ROOT, "failed_statements.json")


def _load_failed() -> set:
    """加载失败题集合（避免反复重试拖慢遍历）"""
    try:
        with open(FAILED_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except (OSError, json.JSONDecodeError):
        return set()


def _save_failed(failed: set):
    with _failed_lock:  # 并发写保护
        try:
            with open(FAILED_FILE, "w", encoding="utf-8") as f:
                json.dump(sorted(failed), f)
        except OSError:
            pass


def _run_batch_crawler(todo, fetch_fn, on_fail, delay: float = 1.5,
                        on_progress=None, label: str = "") -> tuple[int, int]:
    """通用分批并发爬取：题面/题解共用。
    todo: 待爬项列表（已爬/已记忆的由调用方排除）
    fetch_fn(item): 单项目爬取，返回内容或 None
    on_fail(item): 失败处理（失败记忆等）
    403 自适应：整批失败自动暂停（下次启动续跑）；批间限速防封禁"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    BATCH = 8
    done = 0
    fetched = 0
    failed = 0
    total = len(todo)
    idx = 0
    while idx < len(todo):
        batch = todo[idx:idx + BATCH]
        idx += BATCH
        results = {}
        with ThreadPoolExecutor(max_workers=BATCH) as ex:
            futs = {ex.submit(fetch_fn, item): item for item in batch}
            for f in as_completed(futs):
                item = futs[f]
                try:
                    results[id(item)] = f.result()
                except Exception:
                    results[id(item)] = None
        failed_this = 0
        for item in batch:
            done += 1
            if results.get(id(item)):
                fetched += 1
            else:
                failed += 1
                failed_this += 1
                on_fail(item)
        if on_progress:
            on_progress(done, total, fetched, failed)
        # 403 自适应：整批失败 → 暂停（网络异常，避免无谓重试与封禁升级）
        if failed_this == len(batch) and len(batch) == BATCH:
            print(f"  ⚠️ 网络异常（CF 可能限流/封禁）——暂停{label}，下次启动续跑")
            break
        time.sleep(delay)  # 批间限速
    return fetched, failed


def fetch_all_statements(delay: float = 1.5, dry: bool = False,
                         on_progress=None) -> tuple[int, int, int]:
    """遍历 problems.json 预爬所有题面。返回 (总数, 命中缓存, 新爬)
    已爬/已知失败秒跳过；未爬的复用通用并发框架（8 并发 + 403 自适应 + 限速）"""
    problems = _load_problems()
    total = len(problems)
    cached = 0
    failed_set = _load_failed()
    todo = []
    for p in problems:
        key = f"{p["contestId"]}{p["index"]}"
        sp = statement_path(p["contestId"], p["index"])
        # 空壳文件（<=50B 爬取异常产物，如 1 字节 \n）视为未爬——下次自动补
        # （阈值与写前保护一致：短题面如愚人节题 151B 是正常内容，不误判）
        if key in failed_set or (os.path.isfile(sp) and os.path.getsize(sp) > 50):
            cached += 1  # 已预爬或已知失败：秒跳过
        else:
            todo.append(p)

    def fetch_one(p):
        return fetch_statement_md(p["contestId"], p["index"], 1, 12)

    def on_fail(p):
        key = f"{p["contestId"]}{p["index"]}"
        failed_set.add(key)
        _save_failed(failed_set)  # 记忆失败，避免反复重试

    def prog(done, n, fetched, failed):
        if on_progress:
            on_progress(cached + done, total, cached, fetched, failed)

    if on_progress:
        on_progress(cached, total, cached, 0, 0)
    fetched, _ = _run_batch_crawler(todo, fetch_one, on_fail, delay, prog, "题面")
    return total, cached, fetched


def fetch_all_editorials(delay: float = 1.5, dry: bool = False,
                          on_progress=None) -> tuple[int, int, int]:
    """遍历所有比赛预爬题解。返回 (比赛数, 命中缓存, 新爬)
    已爬/已确认无题解秒跳过；未确认的复用通用并发框架（8 并发 + 403 自适应 + 限速）"""
    problems = _load_problems()
    contests = sorted({p["contestId"] for p in problems})
    total = len(contests)
    cached = 0
    failed_set = _load_failed_editorials()
    todo = []
    for cid in contests:
        if os.path.isfile(editorial_path(cid)) or str(cid) in failed_set:
            cached += 1  # 已爬 或 已确认无：秒跳过
        else:
            todo.append(cid)

    def fetch_one(c):
        return fetch_editorial_md(c, 1, 12)

    def on_fail(c):
        if f"{c}@temp" in failed_set:
            return  # 网络失败/被拦：不记 cid（下次启动重试）
        if f"{c}@announcement" not in failed_set:
            failed_set.add(str(c))
            _remember_failed_editorial(c)  # 确认无题解：记忆，避免反复重试

    def prog(done, n, fetched, failed):
        if on_progress:
            on_progress(cached + done, total, cached, fetched, failed)

    if on_progress:
        on_progress(cached, total, cached, 0, 0)
    fetched, _ = _run_batch_crawler(todo, fetch_one, on_fail, delay, prog, "题解")
    return total, cached, fetched


if __name__ == "__main__":
    # 自测
    cid, idx = sys.argv[1], sys.argv[2]
    md = fetch_statement_md(cid, idx)
    print(md[:1500] if md else "题面爬取失败")
