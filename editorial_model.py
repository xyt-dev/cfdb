from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

SCHEMA_VERSION = 2
BLOCK_KINDS = {
    "document", "container", "problem_section", "heading", "paragraph",
    "list", "list_item", "blockquote", "code_block", "table",
    "table_row", "table_cell", "spoiler", "tutorial_slot", "image",
    "horizontal_rule", "line_break", "missing_asset",
}
INLINE_KINDS = {
    "text", "strong", "emphasis", "inline_code", "link", "subscript",
    "superscript",
}
NODE_KINDS = BLOCK_KINDS | INLINE_KINDS


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
class Node:
    kind: str
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
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
    def from_dict(cls, value: dict[str, Any]) -> "Node":
        return cls(
            kind=str(value["kind"]),
            attrs=dict(value.get("attrs", {})),
            children=[cls.from_dict(child) for child in value.get("children", [])],
            text=value.get("text"),
        )


@dataclass(slots=True)
class EditorialDocument:
    contest_id: str
    source_url: str
    root: Node
    diagnostics: list[Diagnostic] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    schema: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "contestId": self.contest_id,
            "sourceUrl": self.source_url,
            "document": self.root.to_dict(),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "assets": list(self.assets),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EditorialDocument":
        return cls(
            schema=int(value["schema"]),
            contest_id=str(value["contestId"]),
            source_url=str(value["sourceUrl"]),
            root=Node.from_dict(value["document"]),
            diagnostics=[Diagnostic.from_dict(item) for item in value.get("diagnostics", [])],
            assets=[str(item) for item in value.get("assets", [])],
        )


def canonical_json(document: EditorialDocument) -> str:
    return json.dumps(document.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_document(document: EditorialDocument, *, ready: bool) -> list[Diagnostic]:
    errors: list[Diagnostic] = []

    def visit(node: Node, path: str) -> None:
        if node.kind not in NODE_KINDS:
            errors.append(Diagnostic("error", "unknown-node-kind", node.kind, path))
        if node.kind == "heading" and node.attrs.get("level") not in range(1, 7):
            errors.append(Diagnostic("error", "invalid-heading-level", str(node.attrs.get("level")), path))
        if ready and node.kind == "tutorial_slot":
            errors.append(Diagnostic("error", "unresolved-tutorial-slot", str(node.attrs.get("problemCode", "")), path))
        if ready and node.kind == "image" and not str(node.attrs.get("src", "")).startswith("/eimages/"):
            errors.append(Diagnostic("error", "remote-image-in-ready-document", str(node.attrs.get("src", "")), path))
        for index, child in enumerate(node.children):
            visit(child, f"{path}/{index}")

    visit(document.root, "document")
    return errors
