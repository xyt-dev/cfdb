from __future__ import annotations

from html import escape
from urllib.parse import unquote, urlsplit

from editorial_model import EditorialDocument, Node, validate_document


class RenderError(ValueError):
    pass


_KNOWN_CODE_LANGUAGES = frozenset(
    {
        "bash",
        "c",
        "cpp",
        "csharp",
        "go",
        "java",
        "javascript",
        "kotlin",
        "pascal",
        "php",
        "python",
        "ruby",
        "rust",
        "scala",
        "swift",
        "typescript",
    }
)
_MISSING_ASSET_HTML = '<span class="img-missing">Image unavailable</span>'


def _has_unsafe_url_characters(value: str) -> bool:
    return any(character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _normalize_text(value: str) -> str:
    parts: list[str] = []
    cursor = 0
    while True:
        start = value.find("$$$", cursor)
        if start < 0:
            parts.append(value[cursor:])
            break
        end = value.find("$$$", start + 3)
        if end < 0:
            parts.append(value[cursor:])
            break
        parts.append(value[cursor:start])
        parts.append("$" + value[start + 3:end].replace(r"\lt", "<") + "$")
        cursor = end + 3
    return "".join(parts)


def sanitize_link_url(url: object) -> str | None:
    if not isinstance(url, str) or not url or _has_unsafe_url_characters(url) or "\\" in url:
        return None
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme in {"http", "https"}:
            if not parsed.netloc or parsed.hostname is None:
                return None
            parsed.port
            if not url.lower().startswith(scheme + "://"):
                return None
            return url
        if scheme or parsed.netloc:
            return None
    except (TypeError, ValueError):
        return None

    if url.startswith("/") and not url.startswith("//"):
        return url
    if url.startswith("#"):
        return url
    return None


def sanitize_image_url(url: object) -> str | None:
    if not isinstance(url, str) or not url.startswith("/eimages/"):
        return None
    if _has_unsafe_url_characters(url) or "\\" in url:
        return None
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return None
    decoded_path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or not decoded_path.startswith("/eimages/"):
        return None
    if _has_unsafe_url_characters(decoded_path) or "\\" in decoded_path:
        return None
    if any(part in {".", ".."} for part in decoded_path.split("/")):
        return None
    if decoded_path.lower().endswith(".svg"):
        return None
    return url


def render_editorial_html(document: EditorialDocument) -> str:
    errors = validate_document(document, ready=True)
    if errors:
        raise RenderError(errors[0].code)
    return _render_node(document.root)


def _render_children(node: Node) -> str:
    return "".join(_render_node(child) for child in node.children)


def _render_wrapped(node: Node, tag: str) -> str:
    return f"<{tag}>{_render_children(node)}</{tag}>"


def _render_spoiler_title(node: Node) -> str:
    raw_title = node.attrs.get("title", [])
    if not isinstance(raw_title, list):
        raise RenderError("invalid-spoiler-title")
    title_nodes: list[Node] = []
    try:
        for value in raw_title:
            if not isinstance(value, dict):
                raise TypeError
            title_nodes.append(Node.from_dict(value))
    except (KeyError, TypeError, ValueError):
        raise RenderError("invalid-spoiler-title") from None
    return "".join(_render_node(title_node) for title_node in title_nodes)


def _render_node(node: Node) -> str:
    kind = node.kind

    if kind in {"document", "container"}:
        return _render_children(node)
    if kind == "problem_section":
        problem_code = escape(str(node.attrs.get("problemCode", "")), quote=True)
        return f'<section data-problem-code="{problem_code}">{_render_children(node)}</section>'
    if kind == "heading":
        level = node.attrs.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or level not in range(1, 7):
            raise RenderError("invalid-heading-level")
        return f"<h{level}>{_render_children(node)}</h{level}>"
    if kind == "paragraph":
        return _render_wrapped(node, "p")
    if kind == "list":
        return _render_wrapped(node, "ol" if node.attrs.get("ordered") else "ul")
    if kind == "list_item":
        return _render_wrapped(node, "li")
    if kind == "blockquote":
        return _render_wrapped(node, "blockquote")
    if kind == "code_block":
        code = escape(node.text or "", quote=True)
        language = node.attrs.get("language")
        if language in _KNOWN_CODE_LANGUAGES:
            return f'<pre><code class="language-{language}">{code}</code></pre>'
        return f"<pre><code>{code}</code></pre>"
    if kind == "table":
        return _render_wrapped(node, "table")
    if kind == "table_row":
        return _render_wrapped(node, "tr")
    if kind == "table_cell":
        return _render_wrapped(node, "th" if node.attrs.get("header") else "td")
    if kind == "spoiler":
        title = _render_spoiler_title(node)
        return f'<details class="cf-spoiler"><summary>{title}</summary>{_render_children(node)}</details>'
    if kind == "tutorial_slot":
        raise RenderError("unresolved-tutorial-slot")
    if kind == "image":
        source = sanitize_image_url(node.attrs.get("src"))
        if source is None:
            return _MISSING_ASSET_HTML
        safe_source = escape(source, quote=True)
        alt = escape(str(node.attrs.get("alt", "")), quote=True)
        return f'<img src="{safe_source}" alt="{alt}">'
    if kind == "horizontal_rule":
        return "<hr>"
    if kind == "line_break":
        return "<br>"
    if kind == "missing_asset":
        return _MISSING_ASSET_HTML
    if kind == "text":
        return escape(_normalize_text(node.text or ""), quote=True)
    if kind == "strong":
        return _render_wrapped(node, "strong")
    if kind == "emphasis":
        return _render_wrapped(node, "em")
    if kind == "inline_code":
        return _render_wrapped(node, "code")
    if kind == "link":
        body = _render_children(node)
        href = sanitize_link_url(node.attrs.get("href"))
        if href is None:
            return body
        safe_href = escape(href, quote=True)
        if urlsplit(href).scheme.lower() in {"http", "https"}:
            return f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer">{body}</a>'
        return f'<a href="{safe_href}">{body}</a>'
    if kind == "subscript":
        return _render_wrapped(node, "sub")
    if kind == "superscript":
        return _render_wrapped(node, "sup")
    raise RenderError("unknown-node-kind")
