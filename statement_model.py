from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from content_model import (
    SCHEMA_VERSION,
    ContentNode,
    Diagnostic,
    canonical_json,
    validate_content_tree,
)


@dataclass(slots=True)
class StatementDocument:
    problem_code: str
    contest_id: str
    index: str
    source_url: str
    source_kind: str
    root: ContentNode
    diagnostics: list[Diagnostic] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    schema: int = SCHEMA_VERSION

    @property
    def content_kind(self) -> str:
        return "statement"

    @property
    def content_id(self) -> str:
        return self.problem_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "contentKind": self.content_kind,
            "problemCode": self.problem_code,
            "contestId": self.contest_id,
            "index": self.index,
            "sourceUrl": self.source_url,
            "sourceKind": self.source_kind,
            "document": self.root.to_dict(),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "assets": list(self.assets),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StatementDocument":
        try:
            if value.get("contentKind") != "statement":
                raise ValueError("invalid statement content kind")
            return cls(
                schema=int(value["schema"]),
                problem_code=str(value["problemCode"]),
                contest_id=str(value["contestId"]),
                index=str(value["index"]),
                source_url=str(value["sourceUrl"]),
                source_kind=str(value["sourceKind"]),
                root=ContentNode.from_dict(value["document"]),
                diagnostics=[Diagnostic.from_dict(item) for item in value.get("diagnostics", [])],
                assets=[str(item) for item in value.get("assets", [])],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid statement document") from error


def validate_statement_document(
    document: StatementDocument,
    *,
    ready: bool,
) -> list[Diagnostic]:
    errors: list[Diagnostic] = []
    if document.schema != SCHEMA_VERSION:
        errors.append(
            Diagnostic(
                "error",
                "unsupported-schema",
                str(document.schema),
                "document",
            )
        )
    if document.problem_code != document.contest_id + document.index:
        errors.append(
            Diagnostic(
                "error",
                "invalid-problem-identity",
                document.problem_code,
                "document",
            )
        )
    if document.source_kind not in {"html", "pdf"}:
        errors.append(
            Diagnostic(
                "error",
                "invalid-statement-source-kind",
                document.source_kind,
                "document",
            )
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
            ready=ready,
            content_kind="statement",
        )
    )
    return errors


__all__ = [
    "SCHEMA_VERSION",
    "StatementDocument",
    "canonical_json",
    "validate_statement_document",
]
