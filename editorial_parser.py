from __future__ import annotations

import copy
from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

from editorial_model import Diagnostic, EditorialDocument, Node
from content_parser import ParseError, ParseLimits, SourceNode, parse_source_html  # pyright: ignore[reportMissingImports]




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
            node = Node(kind="heading", attrs={"level": ord(tag[1]) - ord("0")})
        elif tag == "pre":
            node = Node(kind="code_block", text="")
        elif tag == "hr":
            node = Node(kind="horizontal_rule")
        elif tag == "br":
            node = Node(kind="line_break")
        elif tag == "img":
            alt = next(
                (value or "" for name, value in attrs if name.lower() == "alt"),
                "",
            )
            source = next(
                (value or "" for name, value in attrs if name.lower() == "src"),
                "",
            )
            try:
                resolved_source = urljoin(self.source_url, source) if source else ""
                parsed_source = urlsplit(resolved_source)
                scheme = parsed_source.scheme.lower()
                has_host = bool(parsed_source.netloc)
            except ValueError:
                resolved_source = ""
                scheme = ""
                has_host = False
            if resolved_source and scheme in {"http", "https"} and has_host:
                node = Node(kind="image", attrs={"src": resolved_source, "alt": alt})
            else:
                self._recover(
                    "unsupported-image-source",
                    "Replaced an image with a missing-asset node",
                )
                node = Node(kind="missing_asset", attrs={"alt": alt})
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
                try:
                    resolved = urljoin(self.source_url, href)
                    scheme = urlsplit(resolved).scheme.lower()
                except ValueError:
                    resolved = None
                else:
                    if scheme in self._SAFE_URL_SCHEMES:
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


def _map_source_node(parser: _SemanticHTMLParser, node: SourceNode) -> None:
    if node.tag == "#text":
        parser.handle_data(node.text)
        return
    parser.handle_starttag(node.tag, list(node.attrs.items()))
    for child in node.children:
        _map_source_node(parser, child)
    if node.tag not in parser._VOID_TAGS:
        parser.handle_endtag(node.tag)


def _parse_semantic_source(
    html_text: str,
    *,
    mode: str,
    contest_id: str,
    source_url: str,
    limits: ParseLimits,
) -> tuple[Node, list[Diagnostic]]:
    source_root = parse_source_html(html_text, limits=limits)
    parser = _SemanticHTMLParser(
        mode=mode,
        contest_id=contest_id,
        source_url=source_url,
        limits=limits,
    )
    for child in source_root.children:
        _map_source_node(parser, child)
    parser.close()
    return parser.finish()


class _TutorialHeadingExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.body_parts: list[str] = []
        self.outer_depth = 0
        self.heading_depth = 0
        self.body_depth = 0
        self.complete = False
        self.body_complete = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        markup = self.get_starttag_text() or ""
        is_void = tag in _SemanticHTMLParser._VOID_TAGS
        if self.heading_depth:
            self.parts.append(markup)
            if not is_void:
                self.heading_depth += 1
        elif self.body_depth:
            self.body_parts.append(markup)
            if not is_void:
                self.body_depth += 1
        elif not self.complete and self.outer_depth == 0 and tag == "h3":
            self.parts.append(markup)
            self.heading_depth = 1
        elif (
            self.complete
            and not self.body_complete
            and self.outer_depth == 0
            and tag == "div"
            and "ttypography" in self._classes(attrs)
        ):
            self.body_parts.append(markup)
            self.body_depth = 1
        if not is_void:
            self.outer_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        markup = self.get_starttag_text() or ""
        if self.heading_depth:
            self.parts.append(markup)
        elif self.body_depth:
            self.body_parts.append(markup)
        elif not self.complete and self.outer_depth == 0 and tag == "h3":
            self.parts.append(markup)
            self.complete = True
        elif (
            self.complete
            and not self.body_complete
            and self.outer_depth == 0
            and tag == "div"
            and "ttypography" in self._classes(attrs)
        ):
            self.body_parts.append(markup)
            self.body_complete = True

    def handle_endtag(self, tag: str) -> None:
        if self.heading_depth:
            self.parts.append(f"</{tag}>")
            self.heading_depth -= 1
            if self.heading_depth == 0:
                self.complete = True
        elif self.body_depth:
            self.body_parts.append(f"</{tag}>")
            self.body_depth -= 1
            if self.body_depth == 0:
                self.body_complete = True
        if self.outer_depth:
            self.outer_depth -= 1

    def handle_data(self, data: str) -> None:
        self._append_content(data)

    def handle_entityref(self, name: str) -> None:
        self._append_content(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._append_content(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._append_content(f"<!--{data}-->")

    def _append_content(self, content: str) -> None:
        if self.heading_depth:
            self.parts.append(content)
        elif self.body_depth:
            self.body_parts.append(content)

    def _classes(self, attrs: list[tuple[str, str | None]]) -> set[str]:
        value = next((value or "" for name, value in attrs if name.lower() == "class"), "")
        return set(value.split())


def _parse_tutorial_parts(
    html_text: str,
    *,
    limits: ParseLimits,
) -> tuple[Node, Node, list[Diagnostic]]:
    if len(html_text.encode("utf-8")) > limits.max_input_bytes:
        raise ParseError("max-input-bytes-exceeded")

    extractor = _TutorialHeadingExtractor()
    extractor.feed(html_text)
    extractor.close()
    if not extractor.complete:
        raise ParseError("missing-tutorial-heading")
    if not extractor.body_complete:
        raise ParseError("missing-tutorial-body")

    heading_root, heading_diagnostics = _parse_semantic_source(
        '<div class="ttypography">' + "".join(extractor.parts) + "</div>",
        mode="tutorial-heading",
        contest_id="",
        source_url="https://codeforces.com",
        limits=limits,
    )
    if len(heading_root.children) != 1 or heading_root.children[0].kind != "heading":
        raise ParseError("invalid-tutorial-heading")

    body, body_diagnostics = _parse_semantic_source(
        "".join(extractor.body_parts),
        mode="tutorial-body",
        contest_id="",
        source_url="https://codeforces.com",
        limits=limits,
    )
    if not body.children:
        raise ParseError("missing-tutorial-body")
    return heading_root.children[0], body, [*heading_diagnostics, *body_diagnostics]


def _problem_code_from_heading_link(heading: Node) -> str:
    links = [child for child in heading.children if child.kind == "link"]
    if len(links) != 1:
        raise ParseError("missing-problem-heading-link")
    href = str(links[0].attrs.get("href", ""))
    path = urlsplit(href).path
    match = re.fullmatch(r"/contest/(\d+)/problem/([A-Za-z][A-Za-z0-9]*)", path)
    if match is None:
        raise ParseError("invalid-problem-heading-link")
    return match.group(1) + match.group(2)


def _heading_level(heading: Node) -> int:
    value = heading.attrs.get("level")
    if isinstance(value, int) and not isinstance(value, bool) and value in range(1, 7):
        return value
    if isinstance(value, str) and len(value) == 1 and value in "123456":
        return ord(value) - ord("0")
    return 6


def _source_problem_context(heading: Node) -> tuple[str, int] | None:
    if heading.kind != "heading":
        return None
    links = [child for child in heading.children if child.kind == "link"]
    if len(links) != 1:
        return None
    href = str(links[0].attrs.get("href", ""))
    try:
        parsed = urlsplit(href)
    except ValueError:
        return None
    if parsed.query or parsed.fragment:
        return None
    if parsed.scheme or parsed.netloc:
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        if parsed.netloc.lower() not in {"codeforces.com", "www.codeforces.com"}:
            return None
    match = re.fullmatch(
        r"/contest/(\d+)/problem/([A-Za-z][A-Za-z0-9]*)",
        parsed.path,
    )
    if match is None:
        return None
    return match.group(1) + match.group(2), _heading_level(heading)


def parse_tutorial_fragment(
    html_text: str,
    *,
    expected_code: str,
    limits: ParseLimits = ParseLimits(),
) -> Node:
    heading, body, diagnostics = _parse_tutorial_parts(html_text, limits=limits)
    actual_code = _problem_code_from_heading_link(heading)
    if actual_code != expected_code:
        raise ParseError(f"problem-code-mismatch:{actual_code}:{expected_code}")
    return Node(
        kind="problem_section",
        attrs={"problemCode": expected_code},
        children=[heading, *body.children],
    )


def compose_tutorials(
    document: EditorialDocument,
    *,
    tutorials: dict[str, Node],
    missing_codes: set[str] | None = None,
) -> EditorialDocument:
    remaining: dict[str, Node] = {}
    for supplied_code, fragment in tutorials.items():
        fragment_code = str(fragment.attrs.get("problemCode", ""))
        if fragment_code in remaining:
            raise ParseError(f"duplicate-tutorial-fragment:{fragment_code}")
        if fragment_code != supplied_code:
            raise ParseError(f"tutorial-key-mismatch:{fragment_code}:{supplied_code}")
        remaining[fragment_code] = fragment

    missing = set(missing_codes or ())
    seen_slots: set[str] = set()
    diagnostics = list(document.diagnostics)

    def replace(
        node: Node,
        problem_context: tuple[str, int] | None = None,
    ) -> list[Node]:
        if node.kind == "tutorial_slot":
            code = str(node.attrs.get("problemCode", ""))
            if code in seen_slots:
                raise ParseError(f"duplicate-tutorial-slot:{code}")
            seen_slots.add(code)
            if code in remaining:
                fragment = copy.deepcopy(remaining.pop(code))
                if (
                    problem_context is not None
                    and problem_context[0] == code
                    and fragment.kind == "problem_section"
                    and fragment.children
                    and fragment.children[0].kind == "heading"
                ):
                    fragment.children = fragment.children[1:]
                return [fragment]
            if code in missing:
                diagnostics.append(Diagnostic("warning", "tutorial-known-absent", code))
                return []
            return [copy.deepcopy(node)]
        clone = copy.deepcopy(node)
        context = problem_context
        children: list[Node] = []
        for child in node.children:
            child_context = _source_problem_context(child)
            if child_context is not None:
                context = child_context
            elif (
                child.kind == "heading"
                and context is not None
                and _heading_level(child) <= context[1]
            ):
                context = None
            children.extend(replace(child, context))
        clone.children = children
        return [clone]

    root = replace(document.root)[0]
    unexpected = set(remaining) | (missing - seen_slots)
    if unexpected:
        raise ParseError("unexpected-tutorial-code:" + ",".join(sorted(unexpected)))
    return EditorialDocument(
        contest_id=document.contest_id,
        source_url=document.source_url,
        root=root,
        diagnostics=diagnostics,
        assets=list(document.assets),
    )


def parse_blog_html(
    html_text: str,
    *,
    contest_id: str,
    source_url: str,
    limits: ParseLimits = ParseLimits(),
) -> EditorialDocument:
    root, diagnostics = _parse_semantic_source(
        html_text,
        mode="blog",
        contest_id=contest_id,
        source_url=source_url,
        limits=limits,
    )
    return EditorialDocument(
        contest_id=contest_id,
        source_url=source_url,
        root=root,
        diagnostics=diagnostics,
    )
