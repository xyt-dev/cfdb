from __future__ import annotations

from html import escape
from content_asset_policy import asset_identity_from_route  # pyright: ignore[reportMissingImports]
from urllib.parse import unquote, urlsplit

from content_model import (
    SCHEMA_VERSION,
    ContentNode,
    Diagnostic,
    SemanticDocument,
    validate_content_tree,
)
from statement_model import StatementDocument, validate_statement_document


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
_STATEMENT_ROLE_ELEMENTS = {
    "body": ("section", "cf-statement-body"),
    "input_specification": ("section", "cf-input-specification"),
    "output_specification": ("section", "cf-output-specification"),
    "samples": ("section", "cf-samples"),
    "sample": ("section", "cf-sample"),
    "sample_input": ("section", "cf-sample-input"),
    "sample_output": ("section", "cf-sample-output"),
    "note": ("section", "cf-note"),
    "interaction": ("section", "cf-interaction"),
    "scoring": ("section", "cf-scoring"),
    "custom": ("section", "cf-custom-section"),
    "time_limit": ("div", "cf-time-limit"),
    "memory_limit": ("div", "cf-memory-limit"),
    "input_channel": ("div", "cf-input-channel"),
    "output_channel": ("div", "cf-output-channel"),
}


def _has_unsafe_url_characters(value: str) -> bool:
    return any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    )


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


def sanitize_image_url(
    url: object,
    *,
    content_kind: str = "editorial",
) -> str | None:
    if not isinstance(url, str) or _has_unsafe_url_characters(url) or "\\" in url:
        return None
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return None
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    decoded_path = unquote(parsed.path)
    if _has_unsafe_url_characters(decoded_path) or "\\" in decoded_path:
        return None
    if any(part in {".", ".."} for part in decoded_path.split("/")):
        return None
    identity = asset_identity_from_route(
        decoded_path,
        content_kind=content_kind,
        resource_kind="image",
    )
    return url if identity is not None else None


def sanitize_attachment_url(value: object) -> str:
    if not isinstance(value, str) or asset_identity_from_route(
        value,
        content_kind="statement",
        resource_kind="attachment",
    ) is None:
        raise RenderError("unsafe-attachment-url")
    return value


def _validate_for_render(document: SemanticDocument) -> None:
    content_kind = document.content_kind
    if content_kind == "statement":
        if not isinstance(document, StatementDocument):
            raise RenderError("invalid-statement-document")
        errors = validate_statement_document(document, ready=True)
    elif content_kind == "editorial":
        errors: list[Diagnostic] = []
        if document.schema != SCHEMA_VERSION:
            errors.append(
                Diagnostic("error", "unsupported-schema", str(document.schema), "document")
            )
        if document.root.kind != "document":
            errors.append(
                Diagnostic(
                    "error",
                    "invalid-document-root",
                    document.root.kind,
                    "document",
                )
            )
        errors.extend(
            validate_content_tree(
                document.root,
                document.diagnostics,
                document.assets,
                ready=True,
                content_kind="editorial",
            )
        )
    else:
        raise RenderError("invalid-content-kind")
    if errors:
        raise RenderError(errors[0].code)


def render_content_html(document: SemanticDocument) -> str:
    _validate_for_render(document)
    return _render_node(document.root, content_kind=document.content_kind)


def _render_children(node: ContentNode, *, content_kind: str) -> str:
    return "".join(
        _render_node(child, content_kind=content_kind)
        for child in node.children
    )


def _render_wrapped(node: ContentNode, tag: str, *, content_kind: str) -> str:
    return f"<{tag}>{_render_children(node, content_kind=content_kind)}</{tag}>"


def _render_spoiler_title(node: ContentNode, *, content_kind: str) -> str:
    raw_title = node.attrs.get("title", [])
    if not isinstance(raw_title, list):
        raise RenderError("invalid-spoiler-title")
    title_nodes: list[ContentNode] = []
    try:
        for value in raw_title:
            if not isinstance(value, dict):
                raise TypeError
            title_nodes.append(ContentNode.from_dict(value))
    except (KeyError, TypeError, ValueError):
        raise RenderError("invalid-spoiler-title") from None
    title_root = ContentNode(kind="document", children=title_nodes)
    errors = validate_content_tree(
        title_root,
        [],
        [],
        ready=True,
        content_kind=content_kind,
    )
    if errors:
        raise RenderError(errors[0].code)
    return "".join(
        _render_node(title_node, content_kind=content_kind)
        for title_node in title_nodes
    )


def _render_role_node(node: ContentNode, *, content_kind: str) -> str | None:
    role = node.attrs.get("role")
    if role is None:
        return None
    if content_kind != "statement" or not isinstance(role, str):
        raise RenderError("invalid-content-role")
    if role == "title":
        if node.kind != "heading":
            raise RenderError("invalid-statement-role-node")
        level = node.attrs.get("level")
        if level != 1:
            raise RenderError("invalid-heading-level")
        body = _render_children(node, content_kind=content_kind)
        return f'<h1 class="cf-statement-title">{body}</h1>'
    element = _STATEMENT_ROLE_ELEMENTS.get(role)
    if element is None:
        raise RenderError("unknown-statement-role")
    tag, class_name = element
    body = _render_children(node, content_kind=content_kind)
    return f'<{tag} class="{class_name}">{body}</{tag}>'


def _render_node(node: ContentNode, *, content_kind: str) -> str:
    role_html = _render_role_node(node, content_kind=content_kind)
    if role_html is not None:
        return role_html

    kind = node.kind
    if kind in {"document", "container"}:
        return _render_children(node, content_kind=content_kind)
    if kind == "section":
        return _render_wrapped(node, "section", content_kind=content_kind)
    if kind == "problem_section":
        problem_code = escape(str(node.attrs.get("problemCode", "")), quote=True)
        body = _render_children(node, content_kind=content_kind)
        return f'<section data-problem-code="{problem_code}">{body}</section>'
    if kind == "heading":
        level = node.attrs.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or level not in range(1, 7):
            raise RenderError("invalid-heading-level")
        body = _render_children(node, content_kind=content_kind)
        return f"<h{level}>{body}</h{level}>"
    if kind == "paragraph":
        return _render_wrapped(node, "p", content_kind=content_kind)
    if kind == "list":
        tag = "ol" if node.attrs.get("ordered") else "ul"
        return _render_wrapped(node, tag, content_kind=content_kind)
    if kind == "list_item":
        return _render_wrapped(node, "li", content_kind=content_kind)
    if kind in {"blockquote", "quote"}:
        return _render_wrapped(node, "blockquote", content_kind=content_kind)
    if kind == "code_block":
        code = escape(node.text or "", quote=True)
        language = node.attrs.get("language")
        if language in _KNOWN_CODE_LANGUAGES:
            return f'<pre><code class="language-{language}">{code}</code></pre>'
        return f"<pre><code>{code}</code></pre>"
    if kind == "math_inline":
        body = escape(_normalize_text(node.text or ""), quote=True)
        return f'<span class="cf-math-inline">{body}</span>'
    if kind == "math_block":
        body = escape(_normalize_text(node.text or ""), quote=True)
        return f'<div class="cf-math-block">{body}</div>'
    if kind == "table":
        return _render_wrapped(node, "table", content_kind=content_kind)
    if kind == "table_head":
        return _render_wrapped(node, "thead", content_kind=content_kind)
    if kind == "table_body":
        return _render_wrapped(node, "tbody", content_kind=content_kind)
    if kind == "table_row":
        return _render_wrapped(node, "tr", content_kind=content_kind)
    if kind == "table_cell":
        tag = "th" if node.attrs.get("header") else "td"
        return _render_wrapped(node, tag, content_kind=content_kind)
    if kind == "spoiler":
        title = _render_spoiler_title(node, content_kind=content_kind)
        body = _render_children(node, content_kind=content_kind)
        return f'<details class="cf-spoiler"><summary>{title}</summary>{body}</details>'
    if kind == "tutorial_slot":
        raise RenderError("unresolved-tutorial-slot")
    if kind == "image":
        source = sanitize_image_url(node.attrs.get("src"), content_kind=content_kind)
        if source is None:
            return _MISSING_ASSET_HTML
        safe_source = escape(source, quote=True)
        alt = escape(str(node.attrs.get("alt", "")), quote=True)
        return f'<img src="{safe_source}" alt="{alt}">'
    if kind == "attachment":
        if content_kind != "statement" or node.attrs.get("mediaType") != "application/pdf":
            raise RenderError("invalid-attachment")
        href = escape(sanitize_attachment_url(node.attrs.get("href")), quote=True)
        if node.children:
            label = _render_children(node, content_kind=content_kind)
        else:
            label = escape(str(node.attrs.get("label", "Open PDF")), quote=True)
        return (
            f'<a class="cf-attachment" href="{href}" target="_blank" '
            f'rel="noopener noreferrer" type="application/pdf">{label}</a>'
        )
    if kind == "horizontal_rule":
        return "<hr>"
    if kind == "line_break":
        return "<br>"
    if kind == "missing_asset":
        return _MISSING_ASSET_HTML
    if kind == "text":
        return escape(_normalize_text(node.text or ""), quote=True)
    if kind == "strong":
        return _render_wrapped(node, "strong", content_kind=content_kind)
    if kind == "emphasis":
        return _render_wrapped(node, "em", content_kind=content_kind)
    if kind == "inline_code":
        return _render_wrapped(node, "code", content_kind=content_kind)
    if kind == "link":
        body = _render_children(node, content_kind=content_kind)
        href = sanitize_link_url(node.attrs.get("href"))
        if href is None:
            return body
        safe_href = escape(href, quote=True)
        if urlsplit(href).scheme.lower() in {"http", "https"}:
            return (
                f'<a href="{safe_href}" target="_blank" '
                f'rel="noopener noreferrer">{body}</a>'
            )
        return f'<a href="{safe_href}">{body}</a>'
    if kind == "subscript":
        return _render_wrapped(node, "sub", content_kind=content_kind)
    if kind == "superscript":
        return _render_wrapped(node, "sup", content_kind=content_kind)
    raise RenderError("unknown-node-kind")


__all__ = [
    "RenderError",
    "render_content_html",
    "sanitize_attachment_url",
    "sanitize_image_url",
    "sanitize_link_url",
]
