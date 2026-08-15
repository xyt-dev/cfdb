from __future__ import annotations

from content_cache import DocumentCodec  # pyright: ignore[reportMissingImports]
from content_model import Diagnostic, SemanticDocument
from editorial_model import EditorialDocument, validate_document
from statement_model import StatementDocument, validate_statement_document


def _validate_editorial(
    document: SemanticDocument,
    ready: bool,
) -> list[Diagnostic]:
    if not isinstance(document, EditorialDocument):
        raise ValueError("invalid editorial document type")
    return validate_document(document, ready=ready)


def _validate_statement(
    document: SemanticDocument,
    ready: bool,
) -> list[Diagnostic]:
    if not isinstance(document, StatementDocument):
        raise ValueError("invalid statement document type")
    return validate_statement_document(document, ready=ready)


EDITORIAL_CODEC = DocumentCodec(
    content_kind="editorial",
    from_dict=EditorialDocument.from_dict,
    validate=_validate_editorial,
)
STATEMENT_CODEC = DocumentCodec(
    content_kind="statement",
    from_dict=StatementDocument.from_dict,
    validate=_validate_statement,
)
_CODECS = {
    EDITORIAL_CODEC.content_kind: EDITORIAL_CODEC,
    STATEMENT_CODEC.content_kind: STATEMENT_CODEC,
}


def codec_for_kind(content_kind: str) -> DocumentCodec:
    try:
        return _CODECS[content_kind]
    except KeyError as error:
        raise ValueError(f"unsupported content kind: {content_kind}") from error


__all__ = ["EDITORIAL_CODEC", "STATEMENT_CODEC", "codec_for_kind"]
