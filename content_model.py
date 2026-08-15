from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Mapping, Protocol

SCHEMA_VERSION = 2
BLOCK_KINDS = {
    "document",
    "container",
    "section",
    "problem_section",
    "heading",
    "paragraph",
    "list",
    "list_item",
    "blockquote",
    "quote",
    "code_block",
    "math_block",
    "table",
    "table_head",
    "table_body",
    "table_row",
    "table_cell",
    "spoiler",
    "tutorial_slot",
    "image",
    "attachment",
    "horizontal_rule",
    "line_break",
    "missing_asset",
}
INLINE_KINDS = {
    "text",
    "strong",
    "emphasis",
    "inline_code",
    "math_inline",
    "link",
    "subscript",
    "superscript",
}
NODE_KINDS = BLOCK_KINDS | INLINE_KINDS

_STATEMENT_PDF_ROUTE = re.compile(r"^/statement-assets/[0-9a-f]{64}\.pdf$")
_EDITORIAL_IMAGE_ROUTE = re.compile(
    r"^(?:/eimages/[A-Za-z0-9._-]+|/editorial-assets/[0-9a-f]{64}\.(?:png|jpe?g|gif|webp))$",
    re.IGNORECASE,
)
_STATEMENT_IMAGE_ROUTE = re.compile(
    r"^(?:/images/[A-Za-z0-9._-]+|/statement-assets/[0-9a-f]{64}\.(?:png|jpe?g|gif|webp))$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        result = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.path:
            result["path"] = self.path
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Diagnostic":
        return cls(
            severity=str(value["severity"]),
            code=str(value["code"]),
            message=str(value["message"]),
            path=str(value.get("path", "")),
        )


@dataclass(slots=True)
class ContentNode:
    kind: str
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list["ContentNode"] = field(default_factory=list)
    text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind}
        if self.attrs:
            result["attrs"] = self.attrs
        if self.children:
            result["children"] = [child.to_dict() for child in self.children]
        if self.text is not None:
            result["text"] = self.text
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContentNode":
        return cls(
            kind=str(value["kind"]),
            attrs=dict(value.get("attrs", {})),
            children=[cls.from_dict(child) for child in value.get("children", [])],
            text=value.get("text"),
        )


class SemanticDocument(Protocol):
    schema: int
    @property
    def content_kind(self) -> str:
        raise NotImplementedError

    @property
    def content_id(self) -> str:
        raise NotImplementedError
    root: ContentNode
    diagnostics: list[Diagnostic]
    assets: list[str]

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError


def canonical_json(document_or_value: SemanticDocument | Mapping[str, Any]) -> str:
    if isinstance(document_or_value, Mapping):
        value = dict(document_or_value)
    else:
        value = document_or_value.to_dict()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_content_tree(
    root: ContentNode,
    diagnostics: list[Diagnostic],
    assets: list[str],
    *,
    ready: bool,
    content_kind: str,
) -> list[Diagnostic]:
    errors: list[Diagnostic] = []
    node_count = 0

    if content_kind not in {"statement", "editorial"}:
        errors.append(Diagnostic("error", "invalid-content-kind", content_kind, "document"))
    if not isinstance(diagnostics, list):
        errors.append(Diagnostic("error", "invalid-diagnostics", "diagnostics must be a list", "document"))
    if not isinstance(assets, list) or any(not isinstance(asset, str) for asset in assets):
        errors.append(Diagnostic("error", "invalid-assets", "assets must be strings", "document"))

    def visit(node: ContentNode, path: str, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > 50_000:
            errors.append(Diagnostic("error", "node-limit-exceeded", str(node_count), path))
            return
        if depth > 64:
            errors.append(Diagnostic("error", "depth-limit-exceeded", str(depth), path))
            return
        if node.kind not in NODE_KINDS:
            errors.append(Diagnostic("error", "unknown-node-kind", node.kind, path))
        if node.kind == "heading" and node.attrs.get("level") not in range(1, 7):
            errors.append(Diagnostic("error", "invalid-heading-level", str(node.attrs.get("level")), path))
        if ready and node.kind == "tutorial_slot":
            errors.append(
                Diagnostic(
                    "error",
                    "unresolved-tutorial-slot",
                    str(node.attrs.get("problemCode", "")),
                    path,
                )
            )
        if ready and node.kind == "image":
            source = str(node.attrs.get("src", ""))
            route = _EDITORIAL_IMAGE_ROUTE if content_kind == "editorial" else _STATEMENT_IMAGE_ROUTE
            if not route.fullmatch(source):
                errors.append(Diagnostic("error", "remote-image-in-ready-document", source, path))
        if node.kind == "attachment":
            if content_kind != "statement":
                errors.append(Diagnostic("error", "invalid-attachment-content-kind", content_kind, path))
            else:
                media_type = str(node.attrs.get("mediaType", ""))
                if media_type != "application/pdf":
                    errors.append(Diagnostic("error", "invalid-attachment-media-type", media_type, path))
                elif ready:
                    href = str(node.attrs.get("href", ""))
                    if not _STATEMENT_PDF_ROUTE.fullmatch(href):
                        errors.append(Diagnostic("error", "remote-attachment-in-ready-document", href, path))
        if node.text is not None and not isinstance(node.text, str):
            errors.append(Diagnostic("error", "invalid-node-text", type(node.text).__name__, path))
        for index, child in enumerate(node.children):
            visit(child, f"{path}/{index}", depth + 1)

    visit(root, "document", 0)
    return errors
