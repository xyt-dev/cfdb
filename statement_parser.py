from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from content_model import ContentNode, Diagnostic
from content_parser import (  # pyright: ignore[reportMissingImports]
    ParseError,
    ParseLimits,
    SourceNode,
    class_tokens,
    find_first_by_class,
    parse_source_html,
    text_content,
)
from statement_model import StatementDocument, validate_statement_document

_ROLE_CLASSES = {
    "title": "title",
    "time-limit": "time_limit",
    "memory-limit": "memory_limit",
    "input-file": "input_channel",
    "output-file": "output_channel",
    "problem-description": "body",
    "statement-body": "body",
    "input-specification": "input_specification",
    "output-specification": "output_specification",
    "sample-tests": "samples",
    "note": "note",
    "interaction": "interaction",
    "scoring": "scoring",
    "custom-section": "custom",
}
_DANGEROUS_TAGS = {"embed", "form", "iframe", "object", "script", "style"}
_SAFE_URL_SCHEMES = {"", "http", "https", "mailto"}
_HTML_WHITESPACE = re.compile(r"[\t\n\f\r ]+")
_BLOCK_KINDS = {
    "p": "paragraph",
    "ol": "list",
    "ul": "list",
    "li": "list_item",
    "blockquote": "blockquote",
    "table": "table",
    "thead": "table_head",
    "tbody": "table_body",
    "tr": "table_row",
    "th": "table_cell",
    "td": "table_cell",
    "div": "container",
    "section": "container",
}
_INLINE_KINDS = {
    "a": "link",
    "strong": "strong",
    "b": "strong",
    "em": "emphasis",
    "i": "emphasis",
    "code": "inline_code",
    "sub": "subscript",
    "sup": "superscript",
}


def _walk_source(node: SourceNode):
    yield node
    for child in node.children:
        yield from _walk_source(child)


def _select_unique_problem_statement(root: SourceNode) -> SourceNode:
    matches = [
        node
        for node in _walk_source(root)
        if node.tag != "#text" and "problem-statement" in class_tokens(node)
    ]
    if not matches:
        raise ParseError("missing-problem-statement")
    if len(matches) != 1:
        raise ParseError("ambiguous-problem-statement")
    return matches[0]


def _source_role(node: SourceNode) -> str | None:
    for class_name, role in _ROLE_CLASSES.items():
        if class_name in class_tokens(node):
            return role
    return None


def _preformatted_text(node: SourceNode) -> str:
    if node.tag == "#text":
        return node.text
    if node.tag == "br":
        return "\n"
    value = "".join(_preformatted_text(child) for child in node.children)
    if "test-example-line" in class_tokens(node) and not value.endswith("\n"):
        value += "\n"
    return value


class _StatementMapper:
    def __init__(self, *, source_url: str, limits: ParseLimits) -> None:
        self.source_url = source_url
        self.limits = limits
        self.diagnostics: list[Diagnostic] = []
        self._node_count = 1

    def map_island(self, island: SourceNode) -> ContentNode:
        root = ContentNode(kind="document")
        body_seen = False
        for source_child in island.children:
            if source_child.tag == "#text" and not source_child.text.strip():
                continue
            classes = class_tokens(source_child)
            if "header" in classes:
                root.children.extend(self._map_header(source_child))
                continue
            role = _source_role(source_child)
            if role == "samples":
                root.children.append(self._map_samples(source_child))
                continue
            if role is not None:
                root.children.append(self._map_role_node(source_child, role))
                body_seen = body_seen or role == "body"
                continue
            if source_child.tag in _DANGEROUS_TAGS:
                self._drop_dangerous(source_child)
                continue
            if source_child.tag == "#text" and source_child.text.strip():
                role = "body" if not body_seen else "custom"
                root.children.append(self._map_role_node(source_child, role))
                body_seen = True
                continue
            role = "body" if not body_seen else "custom"
            root.children.append(self._map_role_node(source_child, role))
            body_seen = True
        return root

    def _map_header(self, header: SourceNode) -> list[ContentNode]:
        result: list[ContentNode] = []
        for child in header.children:
            if child.tag == "#text" and not child.text.strip():
                continue
            classes = class_tokens(child)
            if {"input-standard", "output-standard"} & classes:
                continue
            role = _source_role(child)
            if role in {
                "title",
                "time_limit",
                "memory_limit",
                "input_channel",
                "output_channel",
            }:
                result.append(self._map_role_node(child, role))
        return result

    def _map_role_node(self, source: SourceNode, role: str) -> ContentNode:
        attrs: dict[str, object] = {"role": role}
        if role == "custom":
            title = find_first_by_class(source, "section-title")
            safe_title = _HTML_WHITESPACE.sub(" ", text_content(title)).strip()
            if safe_title:
                attrs["title"] = safe_title
        if role == "title":
            node = self._new_node("heading", attrs={**attrs, "level": 1})
            node.children = self._map_children(source)
            return node
        kind = "container" if role in {
            "time_limit",
            "memory_limit",
            "input_channel",
            "output_channel",
        } else "section"
        node = self._new_node(kind, attrs=attrs)
        node.children = self._map_children(source)
        return node

    def _map_samples(self, source: SourceNode) -> ContentNode:
        samples = self._new_node("section", attrs={"role": "samples"})
        pending_input: SourceNode | None = None
        for child in source.children:
            if child.tag == "#text" and not child.text.strip():
                continue
            classes = class_tokens(child)
            if "section-title" in classes:
                mapped = self._map_node(child)
                if mapped is not None:
                    samples.children.append(mapped)
                continue
            if "sample-test" in classes:
                if pending_input is not None:
                    raise ParseError("unpaired-sample-input")
                samples.children.append(self._map_explicit_sample(child))
                continue
            if "input" in classes:
                if pending_input is not None:
                    raise ParseError("unpaired-sample-input")
                pending_input = child
                continue
            if "output" in classes:
                if pending_input is None:
                    raise ParseError("unpaired-sample-output")
                samples.children.append(self._make_sample(pending_input, child))
                pending_input = None
                continue
            mapped = self._map_node(child)
            if mapped is not None:
                samples.children.append(mapped)
        if pending_input is not None:
            raise ParseError("unpaired-sample-input")
        return samples

    def _map_explicit_sample(self, source: SourceNode) -> ContentNode:
        inputs = [child for child in source.children if "input" in class_tokens(child)]
        outputs = [child for child in source.children if "output" in class_tokens(child)]
        if len(inputs) != 1 or len(outputs) != 1:
            raise ParseError("invalid-sample-pair")
        return self._make_sample(inputs[0], outputs[0])

    def _make_sample(self, input_source: SourceNode, output_source: SourceNode) -> ContentNode:
        sample = self._new_node("section", attrs={"role": "sample"})
        sample.children = [
            self._map_sample_part(input_source, "sample_input"),
            self._map_sample_part(output_source, "sample_output"),
        ]
        return sample

    def _map_sample_part(self, source: SourceNode, role: str) -> ContentNode:
        part = self._new_node("section", attrs={"role": role})
        for child in source.children:
            if "title" in class_tokens(child):
                title = self._new_node("heading", attrs={"level": 3})
                title.children = self._map_children(child)
                part.children.append(title)
                continue
            mapped = self._map_node(child)
            if mapped is not None:
                part.children.append(mapped)
        return part

    def _map_children(self, source: SourceNode) -> list[ContentNode]:
        children: list[ContentNode] = []
        for child in source.children:
            mapped = self._map_node(child)
            if mapped is not None:
                children.append(mapped)
        return children

    def _map_node(self, source: SourceNode) -> ContentNode | None:
        if source.tag == "#text":
            normalized = _HTML_WHITESPACE.sub(" ", source.text)
            if not normalized or not normalized.strip():
                return None
            return self._new_node("text", text=normalized)
        if source.tag in _DANGEROUS_TAGS:
            self._drop_dangerous(source)
            return None

        classes = class_tokens(source)
        if source.tag == "pre":
            return self._new_node("code_block", text=_preformatted_text(source))
        if "tex-span" in classes:
            return self._new_node("math_inline", text=text_content(source))
        if "tex-display" in classes:
            return self._new_node("math_block", text=text_content(source))
        if "section-title" in classes or "title" in classes:
            node = self._new_node("heading", attrs={"level": 2})
            node.children = self._map_children(source)
            return node
        if len(source.tag) == 2 and source.tag[0] == "h" and source.tag[1] in "123456":
            node = self._new_node(
                "heading",
                attrs={"level": ord(source.tag[1]) - ord("0")},
            )
            node.children = self._map_children(source)
            return node
        if source.tag == "hr":
            return self._new_node("horizontal_rule")
        if source.tag == "br":
            return self._new_node("line_break")
        if source.tag == "img":
            return self._map_image(source)
        if source.tag in _BLOCK_KINDS:
            attrs: dict[str, object] = {}
            if source.tag in {"ol", "ul"}:
                attrs["ordered"] = source.tag == "ol"
            elif source.tag in {"th", "td"}:
                attrs["header"] = source.tag == "th"
            node = self._new_node(_BLOCK_KINDS[source.tag], attrs=attrs)
            node.children = self._map_children(source)
            return node
        if source.tag in _INLINE_KINDS:
            attrs = {}
            if source.tag == "a":
                href = self._safe_url(source.attrs.get("href", ""))
                if href is not None:
                    attrs["href"] = href
                elif source.attrs.get("href"):
                    self.diagnostics.append(
                        Diagnostic("warning", "unsupported-link-url", "Dropped unsupported link URL")
                    )
            node = self._new_node(_INLINE_KINDS[source.tag], attrs=attrs)
            node.children = self._map_children(source)
            return node

        node = self._new_node("container")
        node.children = self._map_children(source)
        return node

    def _map_image(self, source: SourceNode) -> ContentNode:
        alt = source.attrs.get("alt", "")
        image_url = self._safe_url(source.attrs.get("src", ""), require_host=True)
        if image_url is None:
            self.diagnostics.append(
                Diagnostic("warning", "unsupported-image-source", "Replaced unsupported image source")
            )
            return self._new_node("missing_asset", attrs={"alt": alt})
        return self._new_node("image", attrs={"src": image_url, "alt": alt})

    def _safe_url(self, raw_url: str, *, require_host: bool = False) -> str | None:
        if not raw_url:
            return None
        try:
            resolved = urljoin(self.source_url, raw_url)
            parsed = urlsplit(resolved)
        except ValueError:
            return None
        if parsed.scheme.lower() not in _SAFE_URL_SCHEMES:
            return None
        if require_host and (parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc):
            return None
        return resolved

    def _drop_dangerous(self, source: SourceNode) -> None:
        self.diagnostics.append(
            Diagnostic("warning", "dropped-dangerous-subtree", source.tag)
        )

    def _new_node(
        self,
        kind: str,
        *,
        attrs: dict[str, object] | None = None,
        text: str | None = None,
    ) -> ContentNode:
        self._node_count += 1
        if self._node_count > self.limits.max_nodes:
            raise ParseError("max-nodes-exceeded")
        return ContentNode(kind=kind, attrs=dict(attrs or {}), text=text)


def parse_statement_html(
    html_text: str,
    *,
    problem_code: str,
    contest_id: str,
    index: str,
    source_url: str,
    limits: ParseLimits = ParseLimits(),
) -> StatementDocument:
    source_root = parse_source_html(html_text, limits=limits)
    island = _select_unique_problem_statement(source_root)
    mapper = _StatementMapper(source_url=source_url, limits=limits)
    document = StatementDocument(
        problem_code=problem_code,
        contest_id=contest_id,
        index=index,
        source_url=source_url,
        source_kind="html",
        root=mapper.map_island(island),
        diagnostics=mapper.diagnostics,
    )
    validation_errors = validate_statement_document(document, ready=False)
    if validation_errors:
        raise ParseError(validation_errors[0].code)
    return document


__all__ = ["ParseError", "ParseLimits", "parse_statement_html"]
