from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

from editorial_model import Diagnostic, EditorialDocument, Node


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
class _Frame:
    tag: str
    destination: Node | None
    semantic: Node | None = None
    island: bool = False
    dropped: bool = False
    title_target: Node | None = None


class _SemanticHTMLParser(HTMLParser):
    _VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "source", "wbr"}
    _DANGEROUS_TAGS = {"embed", "form", "iframe", "object", "script", "style"}
    _SAFE_URL_SCHEMES = {"", "http", "https", "mailto"}
    _P_CLOSE_TAGS = {
        "address", "article", "aside", "blockquote", "div", "dl", "fieldset",
        "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hr", "menu", "nav", "ol", "p", "pre", "section", "table", "ul",
    }
    _OPTIONAL_START_CLOSES = {
        "p": _P_CLOSE_TAGS,
        "li": {"li"},
        "tr": {"tr"},
        "th": {"th", "td", "tr"},
        "td": {"th", "td", "tr"},
    }
    _OPTIONAL_END_PARENTS = {
        "li": {"ol", "ul"},
        "tr": {"table", "tbody", "thead", "tfoot"},
        "th": {"tr", "table", "tbody", "thead", "tfoot"},
        "td": {"tr", "table", "tbody", "thead", "tfoot"},
        "p": {"div", "blockquote", "body", "html"},
    }
    _BLOCK_KINDS = {
        "p": "paragraph",
        "ol": "list",
        "ul": "list",
        "li": "list_item",
        "blockquote": "blockquote",
        "table": "table",
        "tr": "table_row",
        "th": "table_cell",
        "td": "table_cell",
        "div": "container",
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
    _HTML_WHITESPACE = re.compile(r"[\t\n\f\r ]+")

    def __init__(
        self,
        *,
        mode: str,
        contest_id: str,
        source_url: str,
        limits: ParseLimits,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.mode = mode
        self.contest_id = contest_id
        self.source_url = source_url
        self.limits = limits
        self.root = Node(kind="document")
        self.diagnostics: list[Diagnostic] = []
        self._stack: list[_Frame] = []
        self._island_active = False
        self._island_complete = False
        self._dangerous_depth = 0
        self._node_count = 1
        self._text_chars = 0
        self._recoveries = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._check_attributes(attrs)
        if len(self._stack) >= self.limits.max_depth:
            raise ParseError("max-depth-exceeded")

        if self._dangerous_depth:
            self._stack.append(_Frame(tag=tag, destination=None, dropped=True))
            self._dangerous_depth += 1
            if tag in self._VOID_TAGS:
                self._pop_frame()
            return

        is_island = self._is_typography_island(tag, attrs)
        if is_island and self._island_active:
            self._recover(
                "recovered-close",
                "Closed the active typography island before a second island",
            )
            self._terminate_active_island()

        self._close_optional_for_start(tag)

        if is_island and not self._island_active and not self._island_complete:
            frame = _Frame(tag=tag, destination=self.root, island=True)
            self._island_active = True
        elif not self._island_active or self._island_complete:
            frame = _Frame(tag=tag, destination=None)
        elif tag in self._DANGEROUS_TAGS:
            self._recover(
                "dropped-dangerous-subtree",
                f"Dropped dangerous <{tag}> subtree",
            )
            frame = _Frame(tag=tag, destination=None, dropped=True)
            self._dangerous_depth = 1
        else:
            destination = self._current_destination()
            if self._current_pre() is not None:
                frame = _Frame(tag=tag, destination=destination)
            else:
                classes = self._classes(attrs)
                spoiler = self._current_spoiler()
                if spoiler is not None and "spoiler-title" in classes:
                    title_container = Node(kind="container")
                    frame = _Frame(
                        tag=tag,
                        destination=title_container,
                        title_target=spoiler,
                    )
                elif spoiler is not None and "spoiler-content" in classes:
                    frame = _Frame(tag=tag, destination=spoiler)
                else:
                    semantic = self._make_semantic_node(tag, attrs)
                    if semantic is not None:
                        if destination is not None:
                            destination.children.append(semantic)
                        destination = semantic
                    frame = _Frame(tag=tag, destination=destination, semantic=semantic)
        self._stack.append(frame)

        if tag in self._VOID_TAGS:
            self._pop_frame()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._dangerous_depth:
            match = next(
                (
                    index
                    for index in range(len(self._stack) - 1, -1, -1)
                    if self._stack[index].dropped and self._stack[index].tag == tag
                ),
                None,
            )
            if match is None:
                return
            while len(self._stack) - 1 >= match:
                self._pop_frame()
            return

        match = next((index for index in range(len(self._stack) - 1, -1, -1)
                      if self._stack[index].tag == tag), None)
        if match is None:
            if self._island_active:
                self._recover("unexpected-end-tag", f"Unexpected closing tag </{tag}>")
            return

        while len(self._stack) - 1 > match:
            frame = self._stack[-1]
            if tag not in self._OPTIONAL_END_PARENTS.get(frame.tag, set()):
                self._recover(
                    "recovered-close",
                    f"Implicitly closed <{frame.tag}> before </{tag}>",
                )
            self._pop_frame()
        self._pop_frame()

    def handle_data(self, data: str) -> None:
        self._text_chars += len(data)
        if self._text_chars > self.limits.max_text_chars:
            raise ParseError("max-text-chars-exceeded")
        if self._dangerous_depth or not self._island_active or self._island_complete or not data:
            return

        pre = self._current_pre()
        if pre is not None:
            pre.text = (pre.text or "") + data
            return

        destination = self._current_destination()
        if destination is None:
            return
        normalized = self._HTML_WHITESPACE.sub(" ", data)
        if not normalized:
            return
        if normalized == " " and not self._whitespace_is_meaningful(destination):
            return
        self._append_text(destination, normalized)

    def finish(self) -> tuple[Node, list[Diagnostic]]:
        while self._stack:
            frame = self._stack[-1]
            if frame.island or (self._island_active and frame.destination is not None):
                self._recover("recovered-close", f"Closed unclosed <{frame.tag}> tag")
            self._pop_frame()
        return self.root, self.diagnostics

    def _check_attributes(self, attrs: list[tuple[str, str | None]]) -> None:
        if len(attrs) > self.limits.max_attributes:
            raise ParseError("max-attributes-exceeded")

    def _classes(self, attrs: list[tuple[str, str | None]]) -> set[str]:
        value = next((value or "" for name, value in attrs if name.lower() == "class"), "")
        return set(value.split())

    def _is_typography_island(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        return tag == "div" and "ttypography" in self._classes(attrs)

    def _current_destination(self) -> Node | None:
        return self._stack[-1].destination if self._stack else None

    def _current_pre(self) -> Node | None:
        for frame in reversed(self._stack):
            if frame.tag == "pre" and frame.semantic is not None:
                return frame.semantic
            if frame.island:
                break
        return None

    def _current_spoiler(self) -> Node | None:
        for frame in reversed(self._stack):
            if frame.semantic is not None and frame.semantic.kind == "spoiler":
                return frame.semantic
            if frame.island:
                break
        return None

    def _make_semantic_node(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> Node | None:
        classes = self._classes(attrs)
        node: Node | None
        if tag == "div" and "problemTutorial" in classes:
            node = Node(
                kind="tutorial_slot",
                attrs={"problemCode": self._required_attr(attrs, "problemcode")},
            )
        elif tag == "div" and "spoiler" in classes:
            node = Node(kind="spoiler", attrs={"title": []})
        elif len(tag) == 2 and tag[0] == "h" and tag[1] in "123456":
            node = Node(kind="heading", attrs={"level": int(tag[1])})
        elif tag == "pre":
            node = Node(kind="code_block", text="")
        elif tag == "hr":
            node = Node(kind="horizontal_rule")
        elif tag == "br":
            node = Node(kind="line_break")
        elif tag in self._BLOCK_KINDS:
            node_attrs: dict[str, object] = {}
            if tag in {"ol", "ul"}:
                node_attrs["ordered"] = tag == "ol"
            elif tag in {"th", "td"}:
                node_attrs["header"] = tag == "th"
            node = Node(kind=self._BLOCK_KINDS[tag], attrs=node_attrs)
        elif tag in self._INLINE_KINDS:
            node_attrs = {}
            if tag == "a":
                href = next((value or "" for name, value in attrs if name.lower() == "href"), "")
                resolved = urljoin(self.source_url, href)
                if urlsplit(resolved).scheme.lower() in self._SAFE_URL_SCHEMES:
                    node_attrs["href"] = resolved
            node = Node(kind=self._INLINE_KINDS[tag], attrs=node_attrs)
        else:
            return None
        self._node_count += 1
        if self._node_count > self.limits.max_nodes:
            raise ParseError("max-nodes-exceeded")
        return node

    def _required_attr(self, attrs: list[tuple[str, str | None]], name: str) -> str:
        value = next((value for attr_name, value in attrs if attr_name.lower() == name), None)
        if value is None or value == "":
            raise ParseError(f"missing-required-attribute: {name}")
        return value

    def _append_text(self, destination: Node, text: str) -> None:
        if destination.children and destination.children[-1].kind == "text":
            previous = destination.children[-1]
            previous.text = (previous.text or "") + text
            return
        self._node_count += 1
        if self._node_count > self.limits.max_nodes:
            raise ParseError("max-nodes-exceeded")
        destination.children.append(Node(kind="text", text=text))

    def _whitespace_is_meaningful(self, destination: Node) -> bool:
        if not destination.children:
            return False
        previous_kind = destination.children[-1].kind
        return previous_kind == "text" or previous_kind in self._INLINE_KINDS.values()

    def _close_optional_for_start(self, incoming: str) -> None:
        while self._stack:
            for index in range(len(self._stack) - 1, -1, -1):
                frame = self._stack[index]
                if incoming in self._OPTIONAL_START_CLOSES.get(frame.tag, set()):
                    while len(self._stack) > index:
                        self._pop_frame()
                    break
                if frame.island or (
                    frame.semantic is not None
                    and frame.semantic.kind not in self._INLINE_KINDS.values()
                ):
                    return
            else:
                return

    def _terminate_active_island(self) -> None:
        while self._stack:
            frame = self._stack[-1]
            self._pop_frame()
            if frame.island:
                return

    def _pop_frame(self) -> None:
        frame = self._stack.pop()
        if frame.title_target is not None and frame.destination is not None:
            title = frame.title_target.attrs["title"]
            title.extend(child.to_dict() for child in frame.destination.children)
        if frame.dropped:
            self._dangerous_depth -= 1
        if frame.island:
            self._island_active = False
            self._island_complete = True

    def _recover(self, code: str, message: str) -> None:
        self._recoveries += 1
        if self._recoveries > self.limits.max_recoveries:
            raise ParseError("max-recoveries-exceeded")
        self.diagnostics.append(Diagnostic("warning", code, message))


def parse_blog_html(
    html_text: str,
    *,
    contest_id: str,
    source_url: str,
    limits: ParseLimits = ParseLimits(),
) -> EditorialDocument:
    if len(html_text.encode("utf-8")) > limits.max_input_bytes:
        raise ParseError("max-input-bytes-exceeded")
    parser = _SemanticHTMLParser(
        mode="blog",
        contest_id=contest_id,
        source_url=source_url,
        limits=limits,
    )
    parser.feed(html_text)
    parser.close()
    root, diagnostics = parser.finish()
    return EditorialDocument(
        contest_id=contest_id,
        source_url=source_url,
        root=root,
        diagnostics=diagnostics,
    )
