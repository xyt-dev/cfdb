#!/usr/bin/env python3
"""HTML → Markdown 转换器（零依赖）
用于：Codeforces 题面（.problem-statement）和比赛题解博客（.ttypography）
"""
import html as html_mod
import re
from html.parser import HTMLParser


class Html2Md(HTMLParser):
    """把 HTML 转成结构化的 markdown：段落、标题、代码块（样例）、行内文本"""

    BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4",
                  "table", "tr", "section", "article"}
    SKIP_TAGS = {"script", "style", "iframe", "input", "button", "span.caption",
                 "div.input-output-copier"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.in_pre = 0          # pre 嵌套深度（pre 内容原样保留）
        self.pre_buf: list[str] = []
        self.in_math = False     # 行内公式 $...$
        self.li_level = 0
        self._skip_depth = 0
        self._in_title = False   # .title 元素（题目标题）
        self._title_depth = 0
        self._in_property = False  # 时间/内存限制块
        self._prop_depth = 0
        self._is_prop_value = False
        self._bold_pending = 0   # <b> 延迟输出计数（空粗体 <b><br/></b> 不产生 **）
        self._b_skip = False      # spoiler-title 粗体（Editorial 折叠标题——跳过，题号标题已表示题解）
        self._a_href = ""        # <a> 链接（markdown 链接输出）

    # ── 辅助 ──
    def _nl(self):
        """块级换行：先清理行尾空格"""
        while self.out and self.out[-1].endswith(" "):
            self.out[-1] = self.out[-1].rstrip()
        if not self.out or not self.out[-1].endswith("\n"):
            self.out.append("\n")

    def _flush_pre(self):
        if self.pre_buf:
            code = "".join(self.pre_buf).strip("\n")
            lang = _detect_code_lang(code)
            self._nl()  # 确保围栏在行首（清理前段尾空格）
            self.out.append(f"```{lang}\n" + code + "\n```\n")
            self.pre_buf = []

    def _push_line(self, text):
        text = re.sub(r"[ \t]+", " ", text).strip()
        if text:
            self.out.append(text + "\n")

    # ── 事件 ──
    DECORATIVE_IMG = ("ton-100x100", "/flags/", "assets.codeforces.com",
                      "codeforces.org/s/", "/images/features/")

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get("class") or ""
        if tag == "img":
            src = (attrs.get("src") or "").strip()
            alt = (attrs.get("alt") or "").strip()
            if src and not any(d in src for d in Html2Md.DECORATIVE_IMG):
                # 归一化 URL：//host/x → https://host/x；/path → 站内
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://codeforces.com" + src
                self.out.append(f"\n\n![{alt}]({src})\n\n")
            return

        # 跳过无用元素
        if tag in ("script", "style", "iframe"):
            self._skip_depth += 1
            return

        if tag == "pre":
            self.in_pre += 1
            return
        if tag == "div" and "input-output-copier" in cls:
            self._skip_depth += 1
            return

        if self.in_pre:
            if tag == "br":
                self.pre_buf.append("\n")
            return

        # 标题（.title）
        if tag == "div" and "title" in cls.split():
            self._in_title = True
            self._title_depth += 1
            self._nl()
            self.out.append("# ")
            return

        # 时间/内存限制（.property-title / .property-value）
        if tag == "div" and "property-title" in cls.split():
            self._in_property = True
            self._prop_depth += 1
            self._nl()
            self._nl()
            self.out.append("**")
            return
        if tag == "div" and "property-value" in cls.split():
            self._is_prop_value = True
            return

        # 小节标题（Input / Output / Note / Examples）——h4 小标题
        # （题目名称才是唯一大标题 h1；小节不占大标题层级）
        if tag == "div" and "section-title" in cls.split():
            self._nl()
            self._nl()
            self.out.append("#### ")
            return

        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4"):
            if tag == "li":
                self.li_level += 1
                self._nl()
                self.out.append("  " * (self.li_level - 1) + "- ")
            elif tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
                try:
                    level = int(tag[1])
                except ValueError:
                    level = 1
                self._nl()
                self.out.append("#" * level + " ")
            else:
                if self.li_level > 0:
                    pass  # li 内的 div/p：行内（CF 原文 li 内嵌套 div 分行，避免拆行）
                else:
                    self.out.append("\n")
            return

        if tag == "br":
            self.out.append("\n")
            return

        # 链接：<a href="...">text</a> → [text](<href>)；无效 href（锚点/js）仅文本
        if tag == "a":
            href = attrs.get("href") or ""
            # 相对/协议相对 URL 归一化为绝对（与 img 一致）
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://codeforces.com" + href
            self._a_href = href
            if self._a_href and not self._a_href.startswith(("#", "javascript:")):
                self.out.append("[")
            return
        # 行内格式：粗体延迟输出（等有内容再 flush；空粗体直接丢弃）
        if tag == "b" or tag == "strong":
            self._bold_pending += 1
            self._b_skip = "spoiler-title" in cls
            return
        elif tag == "i" or tag == "em":
            self.out.append("*")
        elif tag == "code":
            self.out.append("`")
        elif tag == "sub":
            # 输出 raw <sub>（marked 保留渲染下标；~ 会被 marked 当删除线）
            self.out.append("<sub>")
        elif tag == "sup":
            # 输出 raw <sup>（marked 不解析 ^...^）
            self.out.append("<sup>")
        elif tag == "ul":
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe"):
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if tag == "div" and self._skip_depth > 0:
            return
        if tag == "pre":
            if self.in_pre > 0:
                self.in_pre -= 1
                if self.in_pre == 0:
                    self._flush_pre()
            return
        if self.in_pre:
            if tag == "div":
                # CF 样例行用 <div class="test-example-line"> 每行一个 div——
                # div 结束补换行（否则各行文本被拼成单行）
                self.pre_buf.append("\n")
            return

        if tag == "div" and self._in_title and self._title_depth > 0:
            self._title_depth -= 1
            if self._title_depth == 0:
                self._in_title = False
                self.out.append("\n")
            return
        if tag == "div" and self._in_property and self._prop_depth > 0:
            self._prop_depth -= 1
            if self._prop_depth == 0:
                self._in_property = False
                self.out.append(":** ")
            return
        if tag == "div" and self._is_prop_value:
            self._is_prop_value = False
            self.out.append("\n")
            return
        if tag == "div" and "section-title" in "":  # noqa: 占位（section-title 由 text 事件处理）
            return

        if tag in ("p", "div", "h1", "h2", "h3", "h4"):
            if self.li_level > 0 and tag in ("p", "div"):
                pass  # li 内行内
            else:
                self.out.append("\n")
            return
        if tag == "li":
            if self.li_level > 0:
                self.li_level -= 1
            self.out.append("\n")
            return
        if tag == "a":
            href = self._a_href
            self._a_href = ""
            if href and not href.startswith(("#", "javascript:")):
                self.out.append(f"](<{href}>)")
            return
        if tag in ("b", "strong"):
            self._b_skip = False
            if self._bold_pending > 0:
                self._bold_pending -= 1  # 无内容即闭合：空粗体丢弃
            else:
                self.out.append("**")
        elif tag in ("i", "em"):
            self.out.append("*")
        elif tag == "code":
            self.out.append("`")
        elif tag == "sub":
            self.out.append("</sub>")
        elif tag == "sup":
            self.out.append("</sup>")

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        if self.in_pre:
            self.pre_buf.append(data)
            return
        # 保留单词间空格（连续空白压缩为单空格）；换行折叠为空格，
        # 防止公式/文本被 marked 转成 <br> 导致竖排（每行一个字符）
        data = re.sub(r"[ \t]{2,}", " ", data)
        data = data.replace("\r", " ").replace("\n", " ")
        # 保留所有文本节点（含纯空格节点）——跨标签的空格不能丢，否则文字连在一起
        if data:
            if self._bold_pending:
                if self._b_skip and data.strip().lower() == "editorial":
                    # 跳过 Editorial 折叠标题（CF 每题一个 spoiler-title——本地冗余）
                    self._bold_pending = 0
                    self._b_skip = False
                    return
                self.out.append("**" * self._bold_pending)
                self._bold_pending = 0
            self.out.append(data)

    def handle_entityref(self, name):
        if self._skip_depth == 0 and not self.in_pre:
            self.out.append(html_mod.unescape(f"&{name};"))



def _detect_code_lang(code: str) -> str:
    """启发式检测代码语言（CF 题解常见语言）——裸代码块输出语言标注"""
    c = code[:400]
    if re.search(r"#include\s*[<\"]", c) or re.search(r"\b(?:cin|cout|endl|std::|vector<|using namespace)\b", c):
        return "cpp"
    if re.search(r"\b(?:printf|scanf)\s*\(", c) and "#include" in c:
        return "c"
    if re.search(r"\bfn\s+\w+\s*\(", c) and re.search(r"\b(?:let\s+mut|println!|use \w+::)", c):
        return "rust"
    if re.search(r"\bfun\s+\w+\s*\(", c):
        return "kotlin"
    if re.search(r"\bfunc\s+\w+\s*\(", c) or re.search(r"\bpackage main\b", c):
        return "go"
    if re.search(r"\bpublic\s+(?:static\s+)?class\b", c) or "System.out" in c:
        return "java"
    if re.search(r"\bdef\s+\w+\s*\(", c) or re.search(r"\bprint\s*\(", c):
        return "python"
    if re.search(r"\b(?:function|const|let|var)\s+\w+", c) and "=>" in c:
        return "javascript"
    # 弱特征：代码片段（无 include 等强特征，但语法特征明显）
    if re.search(r"\b(?:long long|vector<|push_back|1ll\b|for \(int \w+\s*=|int main\s*\()", c):
        return "cpp"
    # C 风格括号块（if/for/while + {），排除已识别的其他语言
    if re.search(r"\)\s*\{", c) and not re.search(
        r"\b(?:def |print\s*\(|fn \w+\s*\(|func \w+\s*\()", c):
        return "cpp"
    if re.search(r"\b(?:for \w+ in |range\s*\(|import \w+)", c):
        return "python"
    if re.search(r"\b(?:let mut|println!|fn \w+\s*\()", c):
        return "rust"
    if re.search(r"\b(?:fmt\.|package main|func \w+\s*\()", c):
        return "go"
    if re.search(r"\b(?:System\.out|public class)", c):
        return "java"
    return ""



def _dedupe_problem_headers(md: str) -> str:
    """全局去重题号标题：同一题号的 h2 只保留第一个（无论来源——原文 <p><a>/
    tmd 补全/粗体/链接/h1-h4——转换管线末尾兜底）。内容不丢，只删重复标题行。
    这是根本层：不依赖替换路径（_replace_tutorial 的去重是替换时判断，
    覆盖不了"原文点号格式 + tmd 连字符"等未知组合）。"""
    seen = set()
    out = []
    for line in md.split("\n"):
        m = re.match(r'^## ([A-Z]?\d{1,4}[A-Z]?)\s*[-—–]\s*\S', line)
        if m:
            key = m.group(1)
            if key in seen:
                continue  # 重复标题行——删（内容保留）
            seen.add(key)
        out.append(line)
    return "\n".join(out)


def _normalize_math(md: str) -> str:
    """CF 用 $$$ 包裹公式，MathJax 会把 $$$x$$$ 误解析成 $$ + $x（渲染出前导 $）。
    统一转为单 $ 内联公式：$$$x$$$ → $x$"""
    return md.replace("$$$", "$")


def problem_statement_to_md(html_text: str) -> str:
    """提取 .problem-statement 并转 markdown（div 深度计数，嵌套安全）"""
    src = _extract_div(html_text, "problem-statement")
    if not src.strip():
        src = html_text  # 兜底：全文
    p = Html2Md()
    p.feed(src)
    md = _normalize_math(_clean(p.out))
    # 样例标记（.title 误转的 # Input / # Output）降级为 h4——题目名称才是唯一 h1
    md = re.sub(r"^# (Input|Output)\s*$", r"#### \1", md, flags=re.M)
    return md


def _normalize_problem_headers(md: str) -> str:
    """题解中的题号标题（[1000B - Name](<url>) / 1000B - Name / **1000B - Name**）
    统一转换为 `## 题号 - 名称`（h2）——原题链接由前端 wrap 提供"""
    # 链接格式: [1000B - Light It Up](<https://...>)
    # 整行处理：一行多链接（如 1190A/1191C 双入口）各自转 h2
    def _links_to_h2(m):
        parts = re.findall(
            r'\[([A-Z]?\d{1,4}[A-Z]?\s*[-—–]\s*[^\]]+)\]\(<[^>]+>\)', m.group(0))
        return "\n".join("## " + p for p in parts)
    md = re.sub(
        r'^\[[A-Z]?\d{1,4}[A-Z]?\s*[-—–][^\n]*$',
        _links_to_h2, md, flags=re.M)
    # 粗体格式: **1000B - Name**（整行）
    md = re.sub(
        r'^\*\*([A-Z]?\d{1,4}[A-Z]?\s*[-—–]\s*\S[^*\n]*?)\*\*\s*$',
        r'## \1', md, flags=re.M)
    # 裸文本格式: 1000B - Name（整行短行）
    md = re.sub(
        r'^([A-Z]?\d{1,4}[A-Z]?\s*[-—–]\s*\S[^\n]{0,50})$',
        r'## \1', md, flags=re.M)
    # 点号格式（旧博客）: 1000B. Light It Up（3+ 位数字 + 可选字母 + 点号 + 大写开头——
    # 防 "1) Sum" 列表/正文编号误判；转换后统一连字符 h2，前端同一套识别）
    def _dot_to_h2(m):
        t = re.sub(r'^(\d{3,4}[A-Z]?)\.\s+', r'\1 - ', m.group(1))
        return '## ' + t
    md = re.sub(
        r'^(\d{3,4}[A-Z]?\.\s+[A-Z][^\n]{0,50})$',
        _dot_to_h2, md, flags=re.M)
    # 标题格式（h1-h4）: ### 1004E - Name / #### 2044A - Name / ### [1004E - Name](<url>)
    md = re.sub(
        r'^#{1,4} \[([A-Z]?\d{1,4}[A-Z]?\s*[-—–]\s*[^\]]+)\]\(<[^>]+>\)\s*$',
        r'## \1', md, flags=re.M)
    md = re.sub(
        r'^#{1,4} ([A-Z]?\d{1,4}[A-Z]?\s*[-—–]\s*\S[^\n]{0,50})$',
        r'## \1', md, flags=re.M)
    return md


class _DivExtractor(HTMLParser):
    """按 div 深度提取指定 class 的容器内容（处理嵌套 div）"""

    def __init__(self, target_class):
        super().__init__(convert_charrefs=True)
        self.target = target_class
        self.started = False
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            cls = dict(attrs).get("class", "") or ""
            if not self.started and self.target in cls.split():
                self.started = True
                self.depth = 1
                return
            if self.started:
                self.depth += 1
        if self.started:
            self.parts.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag):
        if self.started and tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.started = False
                return
        if self.started:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self.started:
            self.parts.append(data)


def _extract_div(html_text: str, target_class: str) -> str:
    """提取 class=target_class 的容器完整 HTML（嵌套安全）"""
    ex = _DivExtractor(target_class)
    ex.feed(html_text)
    return "".join(ex.parts)




# CF 题解博客的 Feedback 投票组件（标准 6 选项，爬取无价值）
_FEEDBACK_RE = re.compile(
    r"\*\*Feedback\*\*\s*\n+(?:- (?:Didn't attempt|Great problem|Nice problem|OK problem|Bad problem|Terrible problem) \* \*\s*\n+)+"
)

def editorial_to_md(html_text: str) -> str:
    """博客题解转 markdown：只取第一个 .ttypography（正文）。
    CF 博客中每个评论也是独立 ttypography 容器，且页面 div 常未闭合
    导致 _extract_div 提取过头 —— 用正则截到下一个 ttypography 前"""
    m = re.search(
        r'<div class="ttypography">(.*?)(?=<div class="ttypography"|$)',
        html_text, re.S)
    src = m.group(1) if m else _extract_div(html_text, "ttypography")
    if not src.strip():
        src = html_text  # 兜底：全文
    p = Html2Md()
    p.feed(src)
    md = _normalize_math(_clean(p.out))
    # 过滤 Feedback 投票小节（CF 标准 6 选项组件，无价值）
    md = _FEEDBACK_RE.sub("", md)
    # 题号标题统一转 h2（下一题边界正确——Solution 折叠不吞下一题描述）
    md = _normalize_problem_headers(md)
    return md


# 页脚垃圾标记（提取失败 fallback 全文时裁剪）
FOOTER_MARKS = [
    "Codeforces (c) Copyright",
    "Server time:",
    "Privacy Policy",
    "Desktop version",
    "The only programming contests",
    "Supported by",
    "Tutorial of",  # 博客标题噪音（每场末尾出现，后接投票区）
]


def _strip_footer(md: str) -> str:
    """从页脚标记处截断"""
    for mark in FOOTER_MARKS:
        i = md.find(mark)
        if i >= 0:
            line_start = md.rfind("\n", 0, i) + 1
            return md[:line_start].rstrip()
    return md


def _clean(lines) -> str:
    text = "".join(lines)
    # 压缩连续空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 去掉 markdown 空标题
    text = re.sub(r"^#\s*$", "", text, flags=re.M)
    # 行内连续空格压缩 + 修复孤立 **（跳过 ``` 代码块，保护样例对齐）
    out_lines = []
    in_code = False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == "```":
            in_code = not in_code
        if not in_code:
            line = re.sub(r"[ \t]{2,}", " ", line).rstrip()
            line = _fix_broken_bold(line, lines[i + 1] if i + 1 < len(lines) else None)
        out_lines.append(line)
    text = "\n".join(out_lines)
    text = _strip_footer(text)
    return text.strip() + "\n"


def _fix_broken_bold(line: str, next_line: str | None = None) -> str:
    """修复孤立 **（<b><br/></b> 空粗体等标签残缺）：
    只统计公式 $...$ 与行内代码 `...` 之外的 **（其中的 ** 是字面记号，不动）；
    奇数个时——单独行删除、行尾孤立删除、行首孤立删开标记
    （若下一行以 ** 结尾则视为跨行粗体闭合，保留）"""
    masked = re.sub(r"\$[^$\n]*\$|`[^`\n]*`", lambda m: "\x00" * len(m.group(0)), line)
    n = masked.count("**")
    if n % 2 == 1:
        if masked.strip() == "**":
            return ""
        if masked.endswith("**") and n == 1:
            return line[:-2]
        if masked.startswith("**") and n == 1:
            # 跨行粗体续行：下一行以 ** 结尾（闭标记）→ 保留
            if next_line is not None and next_line.rstrip().endswith("**"):
                return line
            return line[2:]
    return line


if __name__ == "__main__":
    # 自测：把第一个参数当 HTML 文件，输出 md
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 html2md.py <html文件>")
        sys.exit(1)
    try:
        with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError as e:
        print(f"读取失败: {e}")
        sys.exit(1)
    print(problem_statement_to_md(src)[:2000])
