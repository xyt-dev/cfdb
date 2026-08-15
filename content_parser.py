from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass(frozen=True, slots=True)
class ParseLimits:
    max_input_bytes: int = 4_000_000
    max_depth: int = 128
    max_nodes: int = 100_000
    max_attributes: int = 32
    max_text_chars: int = 2_000_000
    max_recoveries: int = 500


class ParseError(ValueError):
    pass


@dataclass(slots=True)
class SourceNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""
    children: list["SourceNode"] = field(default_factory=list)


_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_P_CLOSE_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "dl",
    "fieldset",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "menu",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
}
_OPTIONAL_START_CLOSES = {
    "p": _P_CLOSE_TAGS,
    "li": {"li"},
    "tr": {"tr"},
    "th": {"th", "td", "tr"},
    "td": {"th", "td", "tr"},
}


class _BoundedTreeBuilder(HTMLParser):
    def __init__(self, limits: ParseLimits) -> None:
        super().__init__(convert_charrefs=True)
        self.limits = limits
        self.root = SourceNode(tag="#document")
        self._stack: list[SourceNode] = []
        self._node_count = 1
        self._text_chars = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        if len(attrs) > self.limits.max_attributes:
            raise ParseError("max-attributes-exceeded")
        self._close_optional_for_start(tag)
        if len(self._stack) >= self.limits.max_depth:
            raise ParseError("max-depth-exceeded")
        normalized_attrs = {
            name.lower(): value or ""
            for name, value in attrs
        }
        node = SourceNode(tag=tag, attrs=normalized_attrs)
        self._append_node(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        match = next(
            (
                index
                for index in range(len(self._stack) - 1, -1, -1)
                if self._stack[index].tag == tag
            ),
            None,
        )
        if match is not None:
            del self._stack[match:]

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self._text_chars += len(data)
        if self._text_chars > self.limits.max_text_chars:
            raise ParseError("max-text-chars-exceeded")
        destination = self._stack[-1] if self._stack else self.root
        if destination.children and destination.children[-1].tag == "#text":
            destination.children[-1].text += data
            return
        self._append_node(SourceNode(tag="#text", text=data), destination=destination)

    def finish(self) -> SourceNode:
        self.close()
        self._stack.clear()
        return self.root

    def _append_node(
        self,
        node: SourceNode,
        *,
        destination: SourceNode | None = None,
    ) -> None:
        self._node_count += 1
        if self._node_count > self.limits.max_nodes:
            raise ParseError("max-nodes-exceeded")
        parent = destination or (self._stack[-1] if self._stack else self.root)
        parent.children.append(node)

    def _close_optional_for_start(self, incoming: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if incoming in _OPTIONAL_START_CLOSES.get(self._stack[index].tag, set()):
                del self._stack[index:]
                return


def parse_source_html(
    html_text: str,
    *,
    limits: ParseLimits = ParseLimits(),
) -> SourceNode:
    if len(html_text.encode("utf-8")) > limits.max_input_bytes:
        raise ParseError("max-input-bytes-exceeded")
    parser = _BoundedTreeBuilder(limits)
    parser.feed(html_text)
    return parser.finish()


def class_tokens(node: SourceNode | None) -> set[str]:
    if node is None:
        return set()
    return set(node.attrs.get("class", "").split())


def find_first_by_class(root: SourceNode, class_name: str) -> SourceNode | None:
    stack = [root]
    while stack:
        node = stack.pop()
        if class_name in class_tokens(node):
            return node
        stack.extend(reversed(node.children))
    return None


def text_content(node: SourceNode | None) -> str:
    if node is None:
        return ""
    return node.text + "".join(text_content(child) for child in node.children)


__all__ = [
    "ParseError",
    "ParseLimits",
    "SourceNode",
    "class_tokens",
    "find_first_by_class",
    "parse_source_html",
    "text_content",
]
