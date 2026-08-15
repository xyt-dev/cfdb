from __future__ import annotations

from content_render import (  # pyright: ignore[reportMissingImports]
    RenderError,
    render_content_html,
    sanitize_attachment_url,
    sanitize_image_url,
    sanitize_link_url,
)
from statement_model import StatementDocument


def render_statement_html(document: StatementDocument) -> str:
    return render_content_html(document)


__all__ = [
    "RenderError",
    "render_statement_html",
    "sanitize_attachment_url",
    "sanitize_image_url",
    "sanitize_link_url",
]
