# Codeforces Editorial Structure Preservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the lossy editorial Markdown pipeline with a typed semantic tree, exact tutorial-slot composition, sanitized HTML rendering, and an atomically activated full v2 recrawl.

**Architecture:** Parse Codeforces blog and tutorial fragments into a JSON-serializable semantic IR, compose tutorials by exact `problemcode`, localize assets, and render only allowlisted HTML. Store documents in versioned generations, expose v2 through the existing editorial API, retain statement Markdown and pre-activation v1 fallback, then validate contest 1700 before activating a complete full recrawl.

**Tech Stack:** Python 3.10+ standard library (`dataclasses`, `html.parser`, `json`, `unittest`, filesystem primitives), existing `curl`, existing local MathJax/marked/highlight assets, and Node's built-in test runner for one dependency-free browser payload module.

**Spec:** `docs/superpowers/specs/2026-08-14-codeforces-editorial-structure-design.md`

## Global Constraints

- Preserve semantic hierarchy, not Codeforces pixel styling.
- Do not change the statement Markdown pipeline except where shared behavior must remain compatible.
- Do not add pip or npm dependencies.
- Never infer v2 headings from text or use Markdown regexes to associate tutorial titles and bodies.
- Dynamic tutorial replacement must use the exact full problem code.
- V2 source HTML is always parsed and allowlist-rendered; raw source HTML is never cached or returned.
- Ready v2 documents must have no unresolved tutorial slots or remote image dependencies.
- API GET requests must not crawl or mutate cache/failure state.
- Every document, manifest, and activation pointer write must be atomic.
- Existing modified/generated files (`problems.json`, `failed_editorials.json`, current statements/editorials/images) are user data: do not overwrite, stage, or commit them during implementation.
- Run every RED command and observe the expected failure before writing the corresponding production code.
- Use targeted `python3 -m unittest` commands for Python tests and targeted `node --test` commands for the dependency-free reader module.

---

## File Structure

### New production files

- `editorial_model.py` — semantic node/document/diagnostic types, canonical JSON, schema validation.
- `editorial_parser.py` — bounded tolerant HTML parser, Codeforces semantic mapping, tutorial-fragment parsing and exact composition.
- `editorial_render.py` — deterministic sanitized HTML renderer and URL policy.
- `editorial_cache.py` — atomic JSON writes, generation manifests, activation pointer, rollback, and rebuild lock.
- `editorial_rebuild.py` — live validation, full/incremental generation orchestration, status accounting.
- `reader_payload.js` — dependency-free normalization of Markdown versus HTML API payloads; usable by browser and Node tests.

### Modified production files

- `cfcrawl.py` — fetch raw tutorial fragments, build v2 documents, localize typed image nodes, retain isolated v1 compatibility readers.
- `server.py` — read-only v2 payload helper, active generation access, static reader module route, background successor-generation update.
- `update.py` — `argparse` dispatch for metadata, statements, editorial validation, and full v2 rebuild.
- `index.html` — format-aware cache/rendering, structured HTML iframe path, spoiler styles, exact sandbox.

### New tests and fixtures

- `tests/test_editorial_model.py`
- `tests/test_editorial_parser.py`
- `tests/test_editorial_composer.py`
- `tests/test_editorial_render.py`
- `tests/test_editorial_cache.py`
- `tests/test_editorial_crawler.py`
- `tests/test_editorial_server.py`
- `tests/test_editorial_rebuild.py`
- `tests/reader_payload.test.js`
- `tests/fixtures/editorials/1700/*`
- `tests/fixtures/editorials/1369/*`
- `tests/fixtures/editorials/1706/*`
- `tests/fixtures/editorials/synthetic/*`
- `tests/fixtures/editorials/manifest.json`

---

### Task 1: Define the canonical semantic model

**Files:**

- Create: `editorial_model.py`
- Create: `tests/__init__.py`
- Create: `tests/test_editorial_model.py`

**Interfaces:**

- Produces: `Diagnostic`, `Node`, `EditorialDocument`, `SCHEMA_VERSION`, `canonical_json()`, `validate_document()`.
- Consumers: parser, renderer, cache, crawler, and tests in Tasks 2-8.

- [ ] **Step 1: Write the failing model tests**

`tests/test_editorial_model.py`:

```python
import json
import unittest

from editorial_model import (
    Diagnostic,
    EditorialDocument,
    Node,
    canonical_json,
    validate_document,
)


class EditorialModelTests(unittest.TestCase):
    def test_canonical_json_is_stable_and_sorted(self):
        document = EditorialDocument(
            contest_id="1700",
            source_url="https://codeforces.com/blog/entry/103978",
            root=Node(
                kind="document",
                children=[
                    Node(
                        kind="heading",
                        attrs={"level": 3},
                        children=[Node(kind="text", text="1700A - Optimal Path")],
                    )
                ],
            ),
            diagnostics=[Diagnostic("warning", "recovered-close", "closed p")],
            assets=[],
        )

        first = canonical_json(document)
        second = canonical_json(EditorialDocument.from_dict(json.loads(first)))

        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["schema"], 2)
        self.assertNotIn(": ", first)
        self.assertNotIn(", ", first)

    def test_validation_rejects_slot_in_ready_document(self):
        document = EditorialDocument(
            contest_id="1700",
            source_url="https://codeforces.com/blog/entry/103978",
            root=Node(
                kind="document",
                children=[Node(kind="tutorial_slot", attrs={"problemCode": "1700A"})],
            ),
        )

        errors = validate_document(document, ready=True)

        self.assertEqual([e.code for e in errors], ["unresolved-tutorial-slot"])

    def test_validation_rejects_invalid_heading_level(self):
        document = EditorialDocument(
            contest_id="1700",
            source_url="https://codeforces.com/blog/entry/103978",
            root=Node(
                kind="document",
                children=[Node(kind="heading", attrs={"level": 7})],
            ),
        )

        errors = validate_document(document, ready=False)

        self.assertEqual([e.code for e in errors], ["invalid-heading-level"])

    def test_validation_rejects_remote_image_in_ready_document(self):
        document = EditorialDocument(
            contest_id="1700",
            source_url="https://codeforces.com/blog/entry/103978",
            root=Node(
                kind="document",
                children=[Node(kind="image", attrs={"src": "https://codeforces.com/image.png", "alt": "x"})],
            ),
        )

        errors = validate_document(document, ready=True)

        self.assertEqual([e.code for e in errors], ["remote-image-in-ready-document"])


if __name__ == "__main__":
    unittest.main()
```

`tests/__init__.py` is an empty file.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_editorial_model -v
```

Expected: import failure for missing `editorial_model`.

- [ ] **Step 3: Implement the minimal semantic model**

`editorial_model.py` must contain:

```python
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
```

Keep model validation limited to schema invariants; parser/composer-specific validation belongs in their modules.

- [ ] **Step 4: Run the model tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_editorial_model -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add editorial_model.py tests/__init__.py tests/test_editorial_model.py
git commit -m "test: define editorial semantic model"
```

---

### Task 2: Parse core HTML hierarchy without flattening

**Files:**

- Create: `editorial_parser.py`
- Create: `tests/test_editorial_parser.py`

**Interfaces:**

- Consumes: `Node`, `Diagnostic`, `EditorialDocument` from Task 1.
- Produces: `ParseLimits`, `ParseError`, `parse_blog_html(html_text, contest_id, source_url)`.
- Later tasks extend the same parser with Codeforces slots and tutorial fragments.

- [ ] **Step 1: Write failing hierarchy tests**

`tests/test_editorial_parser.py` initially contains:

```python
import unittest

from editorial_parser import parse_blog_html


class EditorialParserTests(unittest.TestCase):
    def test_preserves_heading_levels_nested_lists_quotes_and_code(self):
        source = """
        <div class="ttypography">
          <h4>Problem A</h4>
          <ol><li>first<ul><li>nested</li></ul></li><li>second</li></ol>
          <blockquote><p>proof</p></blockquote>
          <pre><code>#include &lt;bits/stdc++.h&gt;\n  return 0;</code></pre>
        </div>
        """

        result = parse_blog_html(source, contest_id="1", source_url="https://codeforces.com/blog/entry/1")
        root = result.root

        self.assertEqual([node.kind for node in root.children], ["heading", "list", "blockquote", "code_block"])
        self.assertEqual(root.children[0].attrs, {"level": 4})
        self.assertTrue(root.children[1].attrs["ordered"])
        self.assertEqual(root.children[1].children[0].children[1].kind, "list")
        self.assertEqual(root.children[3].text, "#include <bits/stdc++.h>\n  return 0;")

    def test_author_credit_link_stays_a_paragraph(self):
        source = """
        <div class="ttypography">
          <p><a href="/contest/1700/problem/A">1700A - Optimal Path</a> was prepared by Alice.</p>
        </div>
        """

        result = parse_blog_html(source, contest_id="1700", source_url="https://codeforces.com/blog/entry/103978")

        self.assertEqual(result.root.children[0].kind, "paragraph")
        self.assertEqual(result.root.children[0].children[0].kind, "link")
        self.assertFalse(any(node.kind == "heading" for node in result.root.children))
```

- [ ] **Step 2: Run parser tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_editorial_parser -v
```

Expected: import failure for missing `editorial_parser`.

- [ ] **Step 3: Implement stack-based parsing**

Create `ParseLimits` with literal defaults:

```python
@dataclass(frozen=True, slots=True)
class ParseLimits:
    max_input_bytes: int = 4_000_000
    max_depth: int = 128
    max_nodes: int = 100_000
    max_attributes: int = 32
    max_text_chars: int = 2_000_000
    max_recoveries: int = 500
```

Implement an internal `HTMLParser` subclass with:

- A source-tag stack that carries the current semantic destination node.
- Explicit void tags: `br`, `hr`, `img`, `meta`, `link`, `input`, `source`, `wbr`.
- Optional close sets for `p`, `li`, `tr`, `th`, and `td`.
- Block mappings for `h1`-`h6`, `p`, `ol`, `ul`, `li`, `blockquote`, `pre`, `table`, `tr`, `th`, `td`, `hr`, and generic `div`.
- Inline mappings for `a`, `strong`/`b`, `em`/`i`, `code`, `sub`, and `sup`.
- `pre` capture that appends data exactly and suppresses ordinary whitespace normalization.
- Text normalization that collapses HTML whitespace only outside code.
- Pure indentation/formatting whitespace between block nodes is discarded; meaningful spaces inside inline content are preserved.
- Relative Codeforces links normalized to absolute URLs such as `https://codeforces.com/contest/1700/problem/A`.

The public function returns an `EditorialDocument` whose root is `Node(kind="document")` and whose diagnostics contain recoveries. It must select the first top-level `.ttypography` island and stop before a second island.

Use these exact helper signatures:

```python
class ParseError(ValueError):
    pass


def parse_blog_html(
    html_text: str,
    *,
    contest_id: str,
    source_url: str,
    limits: ParseLimits = ParseLimits(),
) -> EditorialDocument:
    if len(html_text.encode("utf-8")) > limits.max_input_bytes:
        raise ParseError("input-too-large")
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
```

- [ ] **Step 4: Run parser and model tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_editorial_model tests.test_editorial_parser -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add editorial_parser.py tests/test_editorial_parser.py
git commit -m "feat: parse editorial HTML into a semantic tree"
```

---

### Task 3: Add Codeforces spoilers, slots, malformed recovery, and safety limits

**Files:**

- Modify: `editorial_parser.py`
- Modify: `tests/test_editorial_parser.py`
- Create: `tests/fixtures/editorials/1369/base.html`
- Create: `tests/fixtures/editorials/1369/expected.json`
- Create: `tests/fixtures/editorials/1706/base.html`
- Create: `tests/fixtures/editorials/1706/expected.json`
- Create: `tests/fixtures/editorials/synthetic/malformed.html`
- Create: `tests/fixtures/editorials/synthetic/nested-structure.html`
- Create: `tests/fixtures/editorials/synthetic/unsafe.html`
- Create: `tests/fixtures/editorials/synthetic/expected.json`

**Interfaces:**

- Extends: `parse_blog_html` from Task 2.
- Produces semantic `spoiler` and `tutorial_slot` nodes and bounded diagnostics.

- [ ] **Step 1: Add minimal immutable fixtures**

`tests/fixtures/editorials/1369/base.html`:

```html
<div class="ttypography">
  <h4><a href="/contest/1369/problem/A">A. FashionabLee :</a></h4>
  <div class="spoiler">
    <b class="spoiler-title">Brief Solution</b>
    <div class="spoiler-content"><p>A_BRIEF_SENTINEL</p></div>
  </div>
  <div class="spoiler">
    <b class="spoiler-title">Complete Proof</b>
    <div class="spoiler-content"><p>A_PROOF_SENTINEL</p></div>
  </div>
</div>
```

`tests/fixtures/editorials/1369/expected.json`:

```json
{"headingLevels":[4],"spoilers":[{"title":"Brief Solution","body":"A_BRIEF_SENTINEL"},{"title":"Complete Proof","body":"A_PROOF_SENTINEL"}]}
```

`tests/fixtures/editorials/1706/base.html`:

```html
<div class="ttypography">
  <h2>Some stats about the round</h2>
  <h4>General Credits</h4>
  <h2>Solutions</h2>
  <h4>Problem A</h4>
</div>
```

`tests/fixtures/editorials/1706/expected.json`:

```json
{"headingLevels":[2,4,2,4],"headings":["Some stats about the round","General Credits","Solutions","Problem A"]}
```

`tests/fixtures/editorials/synthetic/malformed.html`:

```html
<div class="ttypography"><p>before<div class="spoiler"><b class="spoiler-title">Proof</b><div class="spoiler-content"><ol><li>one<li>two</ol></div></div>
<div class="ttypography"><p>comment must not enter editorial</p></div>
```

`tests/fixtures/editorials/synthetic/nested-structure.html`:

```html
<div class="ttypography">
  <div class="spoiler"><b class="spoiler-title">Outer</b><div class="spoiler-content">
    <div class="spoiler"><b class="spoiler-title">Inner</b><div class="spoiler-content"><p>INNER_BODY</p></div></div>
  </div></div>
  <div class="problemTutorial" problemcode="1700A">Tutorial is loading...</div>
</div>
```

`tests/fixtures/editorials/synthetic/unsafe.html`:

```html
<div class="ttypography">
  <script><p>SCRIPT_TEXT_MUST_NOT_SURVIVE</p></script>
  <form><p>FORM_TEXT_MUST_NOT_SURVIVE</p></form>
  <p><a href="javascript:alert(1)" onclick="alert(2)">unsafe link text</a></p>
  <p>safe text</p>
</div>
```

`tests/fixtures/editorials/synthetic/expected.json`:

```json
{"requiredDiagnostics":["dropped-dangerous-subtree","recovered-close"],"excludedText":["SCRIPT_TEXT_MUST_NOT_SURVIVE","FORM_TEXT_MUST_NOT_SURVIVE","comment must not enter editorial"],"requiredText":["safe text","INNER_BODY"]}
```

- [ ] **Step 2: Add failing parser regression tests**

Add tests named:

```python
from pathlib import Path

from editorial_model import Node
from editorial_parser import ParseError, ParseLimits

FIXTURES = Path(__file__).parent / "fixtures" / "editorials"


def _fixture(relative: str) -> str:
    return (FIXTURES / relative).read_text(encoding="utf-8")


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _plain_text(node) -> str:
    return (node.text or "") + "".join(_plain_text(child) for child in node.children)


class EditorialParserSemanticTests(unittest.TestCase):
    def test_spoiler_keeps_title_and_body_together(self):
        result = parse_blog_html(_fixture("1369/base.html"), contest_id="1369", source_url="u")
        spoiler = next(node for node in _walk(result.root) if node.kind == "spoiler")
        self.assertEqual(spoiler.attrs["title"][0]["text"], "Brief Solution")
        self.assertIn("A_BRIEF_SENTINEL", _plain_text(spoiler))

    def test_nested_spoilers_remain_nested(self):
        result = parse_blog_html(_fixture("synthetic/nested-structure.html"), contest_id="1700", source_url="u")
        spoilers = [node for node in _walk(result.root) if node.kind == "spoiler"]
        self.assertEqual([node.attrs["title"][0]["text"] for node in spoilers], ["Outer", "Inner"])
        self.assertIn("INNER_BODY", _plain_text(spoilers[0]))

    def test_problem_tutorial_becomes_identity_slot(self):
        result = parse_blog_html(_fixture("synthetic/nested-structure.html"), contest_id="1700", source_url="u")
        slots = [node for node in _walk(result.root) if node.kind == "tutorial_slot"]
        self.assertEqual([node.attrs["problemCode"] for node in slots], ["1700A"])

    def test_malformed_second_typography_island_is_excluded(self):
        result = parse_blog_html(_fixture("synthetic/malformed.html"), contest_id="1", source_url="u")
        self.assertIn("before", _plain_text(result.root))
        self.assertNotIn("comment must not enter editorial", _plain_text(result.root))
        self.assertIn("recovered-close", [item.code for item in result.diagnostics])

    def test_official_mixed_heading_levels_are_unchanged(self):
        result = parse_blog_html(_fixture("1706/base.html"), contest_id="1706", source_url="u")
        levels = [node.attrs["level"] for node in result.root.children if node.kind == "heading"]
        self.assertEqual(levels, [2, 4, 2, 4])

    def test_dangerous_subtrees_are_dropped_with_diagnostics(self):
        result = parse_blog_html(_fixture("synthetic/unsafe.html"), contest_id="1", source_url="u")
        text = _plain_text(result.root)
        self.assertNotIn("SCRIPT_TEXT_MUST_NOT_SURVIVE", text)
        self.assertNotIn("FORM_TEXT_MUST_NOT_SURVIVE", text)
        self.assertIn("unsafe link text", text)
        self.assertIn("safe text", text)
        self.assertIn("dropped-dangerous-subtree", [item.code for item in result.diagnostics])

    def test_depth_limit_raises_parse_error(self):
        source = '<div class="ttypography">' + "<div>" * 5 + "x" + "</div>" * 5 + "</div>"
        with self.assertRaisesRegex(ParseError, "max-depth-exceeded"):
            parse_blog_html(source, contest_id="1", source_url="u", limits=ParseLimits(max_depth=3))
```

Use `pathlib.Path` to read each fixture. Assert literal node kinds, titles, body sentinels, levels `[2, 4, 2, 4]`, slot code `1700A`, absence of comment/script/form sentinel text, and diagnostic codes `dropped-dangerous-subtree` and `recovered-close`.

- [ ] **Step 3: Run targeted tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_editorial_parser -v
```

Expected: new spoiler, slot, recovery, and safety assertions fail against the generic parser.

- [ ] **Step 4: Implement Codeforces semantic mapping and recovery**

Implement these rules before generic tag mapping:

```python
if tag == "div" and "problemTutorial" in classes:
    return Node(kind="tutorial_slot", attrs={"problemCode": required_attr("problemcode")})

if tag == "div" and "spoiler" in classes:
    return Node(kind="spoiler", attrs={"title": []}, children=[])
```

Route `.spoiler-title` inline nodes into `spoiler.attrs["title"]` as serialized inline-node dictionaries, and `.spoiler-content` block nodes into `spoiler.children`. Do not keep the wrapper nodes.

Add dangerous-subtree skip depth, recovery counting, input/depth/node/text limits, and second-island termination. A missing `problemcode`, exceeded limit, or excessive recovery raises `ParseError` with a stable code-prefixed message.

- [ ] **Step 5: Run all parser/model tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_editorial_model tests.test_editorial_parser -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add editorial_parser.py tests/test_editorial_parser.py tests/fixtures/editorials/1369 tests/fixtures/editorials/1706 tests/fixtures/editorials/synthetic
git commit -m "feat: preserve Codeforces editorial hierarchy"
```

---

### Task 4: Compose contest 1700 tutorials by exact problem code

**Files:**

- Modify: `editorial_parser.py`
- Create: `tests/test_editorial_composer.py`
- Create: `tests/fixtures/editorials/1700/base.html`
- Create: `tests/fixtures/editorials/1700/tutorial-A.html`
- Create: `tests/fixtures/editorials/1700/tutorial-B.html`
- Create: `tests/fixtures/editorials/1700/tutorial-C.html`
- Create: `tests/fixtures/editorials/1700/tutorial-D.html`
- Create: `tests/fixtures/editorials/1700/tutorial-E.html`
- Create: `tests/fixtures/editorials/1700/tutorial-F.html`
- Create: `tests/fixtures/editorials/1700/expected.json`
- Create: `tests/fixtures/editorials/manifest.json`

**Interfaces:**

- Produces: `parse_tutorial_fragment(html_text, expected_code) -> Node` and `compose_tutorials(document, tutorials, missing_codes) -> EditorialDocument`.
- Consumed by: crawler integration and live validation.

- [ ] **Step 1: Create the contest 1700 fixture corpus**

`base.html` contains the credit paragraph and six slots in A-F order:

```html
<div class="ttypography">
  <p>Thanks for the participation!</p>
  <p><a href="/contest/1700/problem/A">1700A - Optimal Path</a> and <a href="/contest/1700/problem/B">1700B - Palindromic Numbers</a> were prepared by the authors.</p>
  <div class="problemTutorial" problemcode="1700A">Tutorial is loading...</div>
  <div class="problemTutorial" problemcode="1700B">Tutorial is loading...</div>
  <div class="problemTutorial" problemcode="1700C">Tutorial is loading...</div>
  <div class="problemTutorial" problemcode="1700D">Tutorial is loading...</div>
  <div class="problemTutorial" problemcode="1700E">Tutorial is loading...</div>
  <div class="problemTutorial" problemcode="1700F">Tutorial is loading...</div>
</div>
```

Each tutorial file uses its exact letter and sentinel. `tutorial-A.html` is:

```html
<h3><a href="/contest/1700/problem/A">1700A - Optimal Path</a></h3><div class="ttypography"><div class="problem-statement"><p>A_BODY_SENTINEL</p></div></div>
```

`tutorial-B.html`:

```html
<h3><a href="/contest/1700/problem/B">1700B - Palindromic Numbers</a></h3><div class="ttypography"><div class="problem-statement"><p>B_BODY_SENTINEL</p></div></div>
```

`tutorial-C.html`:

```html
<h3><a href="/contest/1700/problem/C">1700C - Helping the Nature</a></h3><div class="ttypography"><div class="problem-statement"><p>C_BODY_SENTINEL</p></div></div>
```

`tutorial-D.html`:

```html
<h3><a href="/contest/1700/problem/D">1700D - River Locks</a></h3><div class="ttypography"><div class="problem-statement"><p>D_BODY_SENTINEL</p></div></div>
```

`tutorial-E.html`:

```html
<h3><a href="/contest/1700/problem/E">1700E - Serega the Pirate</a></h3><div class="ttypography"><div class="problem-statement"><p>E_BODY_SENTINEL</p></div></div>
```

`tutorial-F.html`:

```html
<h3><a href="/contest/1700/problem/F">1700F - Puzzle</a></h3><div class="ttypography"><div class="problem-statement"><p>F_BODY_SENTINEL</p></div></div>
```

`expected.json`:

```json
{
  "problemCodes": ["1700A", "1700B", "1700C", "1700D", "1700E", "1700F"],
  "bodySentinels": ["A_BODY_SENTINEL", "B_BODY_SENTINEL", "C_BODY_SENTINEL", "D_BODY_SENTINEL", "E_BODY_SENTINEL", "F_BODY_SENTINEL"],
  "creditText": "1700A - Optimal Path and 1700B - Palindromic Numbers were prepared by the authors."
}
```

The top-level fixture manifest records each relative path, SHA-256, UTF-8/LF normalization, and the rationale string `contest-1700-detached-title-regression`.

- [ ] **Step 2: Write failing exact-composition tests**

`tests/test_editorial_composer.py` must assert:

```python
problem_sections = [node for node in composed.root.children if node.kind == "problem_section"]
self.assertEqual([node.attrs["problemCode"] for node in problem_sections], expected["problemCodes"])
for problem, sentinel in zip(problem_sections, expected["bodySentinels"]):
    self.assertIn(sentinel, plain_text(problem))
self.assertEqual(composed.root.children[1].kind, "paragraph")
self.assertIn(expected["creditText"], plain_text(composed.root.children[1]))
self.assertFalse(any(node.kind == "tutorial_slot" for node in walk(composed.root)))
```

Also add:

- `test_fragment_code_must_match_full_expected_code`: parsing 1700A as expected 1700B raises `ParseError`.
- `test_missing_code_removes_only_its_exact_slot`: missing 1700C removes C without shifting any other body.
- `test_transient_omission_leaves_slot_and_fails_ready_validation`: absent fragment not listed in `missing_codes` remains unresolved.
- `test_duplicate_fragment_code_is_rejected`: duplicate A fragment raises `ParseError`.

Test helpers `walk()` and `plain_text()` remain in the test file.

- [ ] **Step 3: Run composer tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_editorial_composer -v
```

Expected: import failures for missing composition APIs.

- [ ] **Step 4: Implement complete-fragment parsing and slot replacement**

Use exact signatures:

```python
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
    remaining = dict(tutorials)
    missing = set(missing_codes or ())
    seen_slots: set[str] = set()
    diagnostics = list(document.diagnostics)

    def replace(node: Node) -> list[Node]:
        if node.kind == "tutorial_slot":
            code = str(node.attrs.get("problemCode", ""))
            if code in seen_slots:
                raise ParseError(f"duplicate-tutorial-slot:{code}")
            seen_slots.add(code)
            if code in remaining:
                return [copy.deepcopy(remaining.pop(code))]
            if code in missing:
                diagnostics.append(Diagnostic("warning", "tutorial-known-absent", code))
                return []
            return [copy.deepcopy(node)]
        clone = copy.deepcopy(node)
        clone.children = [replacement for child in node.children for replacement in replace(child)]
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
```

`parse_tutorial_fragment` must parse the outer `h3` and following `.ttypography`, extract the heading link path, compare exact contest and index with `expected_code`, and return one `problem_section` with `attrs={"problemCode": expected_code}`.

`compose_tutorials` recursively replaces `tutorial_slot` nodes. It deep-copies the input tree, tracks used codes, rejects duplicate/unexpected fragments, removes confirmed-missing slots, preserves all non-slot content, and appends diagnostics for confirmed per-problem absence.

The implementation uses the shown `None` default for `missing_codes` and normalizes it to a new local set on every call.

- [ ] **Step 5: Run model/parser/composer tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_editorial_model tests.test_editorial_parser tests.test_editorial_composer -v
```

Expected: all tests pass, including exact A-F adjacency.

- [ ] **Step 6: Commit Task 4**

```bash
git add editorial_parser.py tests/test_editorial_composer.py tests/fixtures/editorials/1700 tests/fixtures/editorials/manifest.json
git commit -m "fix: compose contest tutorials by exact problem code"
```

---

### Task 5: Render deterministic sanitized HTML

**Files:**

- Create: `editorial_render.py`
- Create: `tests/test_editorial_render.py`
- Create: `tests/fixtures/editorials/synthetic/code-and-math.html`

**Interfaces:**

- Consumes: validated semantic documents.
- Produces: `render_editorial_html(document) -> str`, `sanitize_link_url(url)`, and `sanitize_image_url(url)`.

`tests/fixtures/editorials/synthetic/code-and-math.html` contains:

```html
<div class="ttypography"><pre><code>#include &lt;bits/stdc++.h&gt;
  return 0;</code></pre><p>Formula: $$$x_1 \lt y$$$</p></div>
```

- [ ] **Step 1: Write failing renderer and sanitizer tests**

Tests must exercise real `Node` trees and literal expected HTML:

```python
def test_renders_nested_spoiler_and_preserves_heading_level(self):
    document = fixture_document(
        Node(
            kind="spoiler",
            attrs={"title": [Node(kind="text", text="Proof").to_dict()]},
            children=[Node(kind="heading", attrs={"level": 4}, children=[Node(kind="text", text="Case 1")])],
        )
    )
    self.assertEqual(
        render_editorial_html(document),
        '<details class="cf-spoiler"><summary>Proof</summary><h4>Case 1</h4></details>',
    )


def test_escapes_text_and_rejects_unsafe_link(self):
    document = fixture_document(
        Node(kind="paragraph", children=[
            Node(kind="link", attrs={"href": "javascript:alert(1)"}, children=[Node(kind="text", text="<click>")])
        ])
    )
    self.assertEqual(render_editorial_html(document), "<p>&lt;click&gt;</p>")


def test_code_whitespace_and_math_text_survive(self):
    document = fixture_document(
        Node(kind="code_block", attrs={"language": "cpp"}, text="#include <x>\n  return 0;"),
        Node(kind="paragraph", children=[Node(kind="text", text="$x_1 < y$")]),
    )
    self.assertEqual(
        render_editorial_html(document),
        '<pre><code class="language-cpp">#include &lt;x&gt;\n  return 0;</code></pre><p>$x_1 &lt; y$</p>',
    )
```

Add this fixture round-trip test:

```python
def test_code_and_math_fixture_round_trips_without_whitespace_loss(self):
    source = (Path(__file__).parent / "fixtures/editorials/synthetic/code-and-math.html").read_text(encoding="utf-8")
    document = parse_blog_html(source, contest_id="1", source_url="u")
    html = render_editorial_html(document)
    self.assertIn("#include &lt;bits/stdc++.h&gt;\n  return 0;", html)
    self.assertIn("Formula: $x_1 &lt; y$", html)
```

Also add table-driven tests with literal expectations: external HTTP links receive `target="_blank" rel="noopener noreferrer"`; `/eimages/1.png` is retained; `https://codeforces.com/1.png` becomes the missing-asset span; an unknown language emits no class; a two-cell table emits `<table><tr><td>A</td><td>B</td></tr></table>`; subscript/superscript emit `<sub>`/`<sup>`; and two renders of the same document are equal.

- [ ] **Step 2: Run renderer tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_editorial_render -v
```

Expected: import failure for missing `editorial_render`.

- [ ] **Step 3: Implement the allowlist renderer**

Implement one dispatch function per node kind. Use `html.escape(value, quote=True)` for text and attributes. Allowed external link schemes are exactly `http` and `https`. Allowed image paths begin exactly with `/eimages/`. SVG image paths are rejected.

Use this public contract:

```python
class RenderError(ValueError):
    pass


def render_editorial_html(document: EditorialDocument) -> str:
    errors = validate_document(document, ready=True)
    if errors:
        raise RenderError(errors[0].code)
    return _render_node(document.root)
```

Render `document` and `container` transparently, `problem_section` as `<section data-problem-code="1700A"><h3>1700A - Optimal Path</h3><p>A body</p></section>`, and `missing_asset` as `<span class="img-missing">Image unavailable</span>`. Only renderer-owned class names are emitted.

- [ ] **Step 4: Run renderer plus upstream tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_editorial_model tests.test_editorial_parser tests.test_editorial_composer tests.test_editorial_render -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add editorial_render.py tests/test_editorial_render.py tests/fixtures/editorials/synthetic/code-and-math.html
git commit -m "feat: render sanitized editorial HTML"
```

---

### Task 6: Add atomic versioned editorial generations

**Files:**

- Create: `editorial_cache.py`
- Create: `tests/test_editorial_cache.py`

**Interfaces:**

- Consumes: canonical model JSON from Task 1.
- Produces: `ContestStatus`, `GenerationStore`, `RebuildLock`, `atomic_write_json`, `load_active_document`, and `activate_generation`.

- [ ] **Step 1: Write real-filesystem failing tests**

Use `tempfile.TemporaryDirectory` and real filesystem operations; do not mock `open`, `os.replace`, or JSON.

Required tests:

- `test_document_write_round_trips_canonical_json`
- `test_incomplete_generation_cannot_activate`
- `test_only_ready_and_known_absent_are_terminal`
- `test_activation_pointer_switch_and_rollback`
- `test_failed_atomic_write_leaves_previous_document_visible`
- `test_rebuild_lock_excludes_second_process_owner`
- `test_seed_successor_hardlinks_or_copies_ready_documents`

The activation test creates generation `g1`, writes one ready document and one known-absent status, activates it, creates complete `g2`, activates it, then rolls back and asserts `current.json` again names `g1`.

- [ ] **Step 2: Run cache tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_editorial_cache -v
```

Expected: import failure for missing `editorial_cache`.

- [ ] **Step 3: Implement atomic cache primitives**

Use this status enum:

```python
class ContestStatus(str, Enum):
    READY = "ready"
    KNOWN_ABSENT = "known_absent"
    TRANSIENT_FAILURE = "transient_failure"
    INVALID_STRUCTURE = "invalid_structure"
```

`atomic_write_json` writes UTF-8 canonical JSON to a named temporary file in the target directory, flushes and `os.fsync`s it, calls `os.replace`, then fsyncs the parent directory on platforms that support directory descriptors.

`GenerationStore` must expose:

```python
GenerationStore.create(root, generation_id, expected_contests, parser_version, fixture_version)
GenerationStore.open(root, generation_id)
store.write_document(document)
store.set_status(contest_id, status, *, evidence, document_path=None)
store.is_activation_ready()
store.write_manifest()
store.seed_from(active_generation)
store.load_document(contest_id)
```

The manifest stores status evidence and timestamps. `KNOWN_ABSENT` evidence includes both successful check timestamps and recognized contest-page receipts.

`RebuildLock` uses atomic `O_CREAT | O_EXCL`, records PID and process start metadata, and refuses ambiguous stale recovery.

- [ ] **Step 4: Run cache and upstream tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_editorial_cache tests.test_editorial_model -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 6**

```bash
git add editorial_cache.py tests/test_editorial_cache.py
git commit -m "feat: add atomic editorial generations"
```

---

### Task 7: Integrate v2 fetching, exact API fragments, and local assets

**Files:**

- Modify: `cfcrawl.py:17-75`
- Modify: `cfcrawl.py:287-330`
- Replace v2 path around: `cfcrawl.py:472-693`
- Create: `tests/test_editorial_crawler.py`

**Interfaces:**

- Consumes: parser/composer/model/cache interfaces.
- Produces: `TutorialBatch`, `EditorialBuildResult`, `build_editorial_document`, `fetch_editorial_v2`, `localize_editorial_assets`.
- Preserves: v1 `read_editorial_md` only for pre-activation fallback.

- [ ] **Step 1: Write failing crawler boundary tests**

Use fixture-backed callables for external HTTP and image download boundaries. The fake tutorial response mirrors the real API shape exactly: `{"success": "true", "html": "<h3><a href='/contest/1700/problem/A'>1700A - Optimal Path</a></h3><div class='ttypography'><p>A_BODY_SENTINEL</p></div>"}` or `{"success": "false"}`.

Required tests:

- `test_build_editorial_document_composes_1700_a_through_f`
- `test_transient_tutorial_failure_returns_no_document`
- `test_success_false_removes_only_exact_slot`
- `test_wrong_fragment_problem_code_is_invalid_structure`
- `test_localize_assets_rewrites_only_after_atomic_download`
- `test_transient_image_failure_prevents_ready_document`
- `test_confirmed_missing_image_becomes_missing_asset`
- `test_ready_document_has_no_remote_image_source`

Assert returned document structure and status, not fake call counts.

- [ ] **Step 2: Run crawler tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_editorial_crawler -v
```

Expected: import errors for missing v2 crawler interfaces.

- [ ] **Step 3: Implement raw tutorial fetching and pure document building**

Add dataclasses:

```python
@dataclass(slots=True)
class TutorialBatch:
    html_by_code: dict[str, str]
    missing_codes: set[str]
    transient_errors: list[str]


@dataclass(slots=True)
class EditorialBuildResult:
    status: ContestStatus
    document: EditorialDocument | None
    evidence: dict[str, object]
```

Change the tutorial fetcher to return raw HTML by exact code. It must not call `html2md`, extract `h3` with regex, or return Markdown.

Implement:

```python
def build_editorial_document(contest_id, source_url, base_html, tutorial_batch, asset_localizer):
    if tutorial_batch.transient_errors:
        return EditorialBuildResult(
            ContestStatus.TRANSIENT_FAILURE,
            None,
            {"errors": list(tutorial_batch.transient_errors)},
        )
    try:
        base = parse_blog_html(base_html, contest_id=contest_id, source_url=source_url)
        parsed = {
            code: parse_tutorial_fragment(fragment, expected_code=code)
            for code, fragment in tutorial_batch.html_by_code.items()
        }
        composed = compose_tutorials(
            base,
            tutorials=parsed,
            missing_codes=tutorial_batch.missing_codes,
        )
    except ParseError as error:
        return EditorialBuildResult(ContestStatus.INVALID_STRUCTURE, None, {"error": str(error)})
    localized = asset_localizer(composed)
    if localized.status is not ContestStatus.READY or localized.document is None:
        return localized
    validation_errors = validate_document(localized.document, ready=True)
    if validation_errors:
        return EditorialBuildResult(
            ContestStatus.INVALID_STRUCTURE,
            None,
            {"errors": [item.to_dict() for item in validation_errors]},
        )
    return EditorialBuildResult(ContestStatus.READY, localized.document, {"sourceUrl": source_url})
```

Any transient error, unresolved slot, parse error, or validation error returns a non-ready result without writing a cache file.

- [ ] **Step 4: Implement typed asset localization**

Traverse image nodes, use the existing curl/image flattening support, write assets atomically, and update node sources only after success. Treat unsupported SVG as confirmed missing. Preserve current `/eimages/` naming compatibility with a deterministic contest-prefixed name.

- [ ] **Step 5: Run crawler and all semantic tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_editorial_crawler tests.test_editorial_parser tests.test_editorial_composer tests.test_editorial_render -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 7**

```bash
git add cfcrawl.py tests/test_editorial_crawler.py
git commit -m "feat: build structured editorials from Codeforces fragments"
```

---

### Task 8: Serve v2 editorials without request-time crawling

**Files:**

- Modify: `server.py:13-31`
- Modify: `server.py:82-208`
- Create: `tests/test_editorial_server.py`

**Interfaces:**

- Consumes: active-generation reads and HTML renderer.
- Produces: `build_editorial_payload(contest_id, *, cache_root=None)` and unchanged `/api/editorial` route with format-aware JSON.

- [ ] **Step 1: Write failing payload tests against real temporary generations**

Required tests:

- `test_ready_v2_payload_contains_sanitized_html_and_schema`
- `test_known_absent_v2_payload_has_null_body`
- `test_before_v2_activation_payload_uses_legacy_markdown`
- `test_after_v2_activation_missing_contest_does_not_fall_back_to_v1`
- `test_payload_read_does_not_change_cache_or_failure_memory`
- `test_invalid_contest_reference_returns_invalid_ref_payload`

Build real temporary generation files with `GenerationStore`. For the no-mutation test, snapshot directory file names, sizes, mtimes, and `failed_editorials.json` bytes before and after calling the helper; assert equality.

- [ ] **Step 2: Run server tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_editorial_server -v
```

Expected: import failure for missing `build_editorial_payload`.

- [ ] **Step 3: Implement the read-only payload helper**

Exact v2 ready payload:

```python
{
    "format": "html",
    "schema": 2,
    "html": render_editorial_html(document),
    "url": document.source_url,
    "known": True,
    "status": "ready",
}
```

Before any v2 pointer exists, return current Markdown as `format: "markdown"`. Once v2 is active, the manifest is authoritative and no contest falls back individually.
If a valid contest ID is absent from an active manifest, return HTTP 500 with `{format: null, html: null, status: "invalid_structure", known: false, error: "active manifest missing contest"}`. This is corruption, not known absence and not a reason to read v1.

Replace the `/api/editorial` route body with query validation plus this helper. Remove calls to `fetch_editorial_md`, `_remember_failed_editorial`, and request-time failure-memory mutation from `Handler.do_GET`.

- [ ] **Step 4: Add a safe static route for `reader_payload.js`**

Serve exactly `/reader_payload.js` from the repository root with `application/javascript; charset=utf-8`. Do not introduce a generic root-file route.

- [ ] **Step 5: Run server and cache tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_editorial_server tests.test_editorial_cache -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 8**

```bash
git add server.py tests/test_editorial_server.py
git commit -m "feat: serve read-only structured editorials"
```

---

### Task 9: Add the format-aware frontend reader and exact iframe sandbox

**Files:**

- Create: `reader_payload.js`
- Create: `tests/reader_payload.test.js`
- Modify: `index.html:1382-1460`
- Modify: `index.html:1600-1800`

**Interfaces:**

- Produces browser/CommonJS module functions `normalizeApiPayload(data)` and `prepareBody(payload, markdownRenderer, markdownNormalizer)`.
- Consumed by `index.html` tab cache and frame builder.

- [ ] **Step 1: Write failing Node behavior tests**

`tests/reader_payload.test.js`:

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeApiPayload, prepareBody } = require("../reader_payload.js");

test("v2 html bypasses markdown parsing and heading normalization", () => {
  const payload = normalizeApiPayload({
    format: "html",
    schema: 2,
    html: "<h4>Official Level</h4>",
    url: "https://codeforces.com/blog/entry/1",
    status: "ready",
  });
  const rendered = prepareBody(
    payload,
    () => { throw new Error("markdown renderer called"); },
    () => { throw new Error("heading normalizer called"); },
  );
  assert.equal(rendered, "<h4>Official Level</h4>");
});

test("legacy markdown still uses both markdown stages", () => {
  const payload = normalizeApiPayload({ format: "markdown", md: "## 1A - A", url: "u" });
  const rendered = prepareBody(payload, (md) => `<h2>${md}</h2>`, (html) => `<hr>${html}`);
  assert.equal(rendered, "<hr><h2>## 1A - A</h2>");
});

test("known absent normalizes to an empty payload", () => {
  assert.deepEqual(
    normalizeApiPayload({ format: null, html: null, status: "known_absent", known: true }),
    { format: null, body: null, url: null, status: "known_absent", known: true, schema: null },
  );
});
```

- [ ] **Step 2: Run Node tests and verify RED**

Run:

```bash
node --test tests/reader_payload.test.js
```

Expected: module-not-found failure for `reader_payload.js`.

- [ ] **Step 3: Implement the dependency-free UMD module**

`reader_payload.js` exports under CommonJS and sets `window.CFDBReaderPayload` in browsers. It validates `format`, selects `html` or `md`, carries `url/status/known/schema`, and calls Markdown functions only for `format === "markdown"`.

Use this wrapper:

```javascript
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.CFDBReaderPayload = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function normalizeApiPayload(data) {
    const source = data || {};
    if (source.format === "html" && typeof source.html === "string") {
      return {
        format: "html",
        body: source.html,
        url: source.url || null,
        status: source.status || "ready",
        known: source.known !== false,
        schema: source.schema == null ? null : source.schema,
      };
    }
    if ((source.format === "markdown" || (!source.format && source.md)) && typeof source.md === "string") {
      return {
        format: "markdown",
        body: source.md,
        url: source.url || null,
        status: source.status || "ready",
        known: source.known !== false,
        schema: source.schema == null ? null : source.schema,
      };
    }
    return {
      format: null,
      body: null,
      url: source.url || null,
      status: source.status || "unknown",
      known: source.known === true,
      schema: source.schema == null ? null : source.schema,
    };
  }

  function prepareBody(payload, markdownRenderer, markdownNormalizer) {
    if (payload.format === "html") return payload.body;
    if (payload.format === "markdown") {
      return markdownNormalizer(markdownRenderer(payload.body));
    }
    return null;
  }
  return { normalizeApiPayload, prepareBody };
});
```

Replace the comments with the literal branch logic required by the tests; do not leave comment-only bodies.

- [ ] **Step 4: Integrate the module into the real reader**

In `index.html`:

- Load `/reader_payload.js` before the main application script.
- Store `{format, body, url, status, known, schema}` in `tabCache.editorial`.
- Keep statement cache explicitly `format: "markdown"`.
- Replace direct `mdFrame` cache restoration with a format-aware `contentFrame(payload)`.
- Rename the existing Markdown conversion portion to `prepareMarkdownHtml`; call it only through `prepareBody`.
- Build one shared iframe from already-prepared HTML.
- Never call `normalizeProblemHeaders` for v2 HTML.
- Add `.cf-spoiler`, `.cf-spoiler > summary`, nested list, table, and problem-section styles without changing source heading levels.
- Set exactly:

```javascript
frame.setAttribute("sandbox", "allow-scripts allow-popups allow-popups-to-escape-sandbox");
```

- Do not add `allow-same-origin`.
- Escape `</script` in server-rendered HTML before interpolating into `srcdoc`.

- [ ] **Step 5: Run Node tests and frontend syntax check and verify GREEN**

Run:

```bash
node --test tests/reader_payload.test.js
node --check reader_payload.js
```

Expected: tests pass and syntax check exits 0.

- [ ] **Step 6: Commit Task 9**

```bash
git add reader_payload.js tests/reader_payload.test.js index.html
git commit -m "feat: render structured editorials in the web reader"
```

---

### Task 10: Implement validation, full rebuild, incremental successor generations, and CLI dispatch

**Files:**

- Create: `editorial_rebuild.py`
- Create: `tests/test_editorial_rebuild.py`
- Modify: `update.py:1-105`
- Modify: `server.py:32-70`

**Interfaces:**

- Produces: `EditorialSource` protocol, `validate_editorial`, `rebuild_editorials`, `update_editorials`, `build_argument_parser`, and `main(argv=None)`.
- Consumes: crawler build results and generation store.

- [ ] **Step 1: Write failing rebuild tests with a complete fixture source**

Define `FixtureEditorialSource` in the test file. It reads the 1700 fixtures and returns two recognized empty contest-page responses for known-absent contest `9999`. It mirrors all `EditorialSource` methods rather than returning partial dictionaries.

Required tests:

- `test_validate_1700_reports_a_through_f_adjacency_without_activation`
- `test_full_rebuild_ignores_v1_markdown_and_failure_memory`
- `test_full_rebuild_does_not_activate_with_transient_failure`
- `test_complete_rebuild_activates_ready_and_known_absent_contests`
- `test_known_absent_requires_two_matching_valid_page_checks`
- `test_incremental_successor_seeds_ready_documents_and_rechecks_absence`
- `test_resume_skips_ready_documents_in_same_generation`
- `test_cli_parses_validate_editorial_1700`
- `test_cli_parses_editorials_rebuild`

Assertions use real temporary generations and inspect manifests/pointers. No test asserts fake call count; it asserts resulting documents, statuses, activation, and evidence receipts.

- [ ] **Step 2: Run rebuild tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_editorial_rebuild -v
```

Expected: import failure for missing `editorial_rebuild` and CLI parser APIs.

- [ ] **Step 3: Implement rebuild orchestration**

`EditorialSource` exposes:

```python
@dataclass(frozen=True, slots=True)
class FetchReceipt:
    ok: bool
    body: str
    status_code: int | None
    blocked: bool
    recognized: bool
    fetched_at: str
    error: str | None = None


class EditorialSource(Protocol):
    def problem_contest_ids(self) -> list[str]:
        raise NotImplementedError

    def fetch_contest_page(self, contest_id: str) -> FetchReceipt:
        raise NotImplementedError

    def find_editorial_url(self, contest_html: str) -> str | None:
        raise NotImplementedError

    def fetch_editorial_page(self, url: str) -> FetchReceipt:
        raise NotImplementedError

    def fetch_tutorial_batch(self, contest_id: str, codes: list[str]) -> TutorialBatch:
        raise NotImplementedError

    def localize_assets(self, document: EditorialDocument) -> EditorialBuildResult:
        raise NotImplementedError


LIVE_1700_SENTINELS = {
    "1700A": "Let's notice that the optimal path",
    "1700B": "Let X be the number in input",
    "1700C": "Consider the difference array",
    "1700D": "To begin with, we note",
    "1700E": "We need to find a simple criteria",
    "1700F": "We are asked to find a minimum cost perfect matching",
}
```

`validate_editorial("1700")` builds and renders without creating or activating a generation. Its report contains literal `problemCodes`, the six matched `LIVE_1700_SENTINELS`, `unresolvedSlots`, `validationErrors`, and a SHA-256 of canonical JSON. Each sentinel must occur inside the `problem_section` with the same exact code, not merely somewhere in the document.

`rebuild_editorials` creates or resumes an inactive generation, ignores v1 files and failure memory, uses the existing batch size of 8 and delay policy, writes statuses/documents atomically, and activates only when terminal. It accepts an injected `sleep_fn` so tests verify two-check known-absence behavior without real delays; production passes `time.sleep`.

`update_editorials` seeds a successor from the active generation, keeps ready documents with the current parser/schema, rechecks every known-absent contest, crawls new contests, and activates only when complete.

- [ ] **Step 4: Replace ad hoc CLI dispatch with `argparse`**

Required command behavior:

```text
python3 update.py                         # metadata
python3 update.py --statements            # current statement crawl
python3 update.py --editorials            # incremental v2 update when active; legacy update before activation
python3 update.py --editorials --rebuild  # full inactive v2 rebuild and activation
python3 update.py --validate-editorial 1700
```

Reject `--rebuild` without `--editorials`. Preserve `--help`. `main(argv=None)` returns an integer and the module guard exits with it.

- [ ] **Step 5: Update server background orchestration**

`auto_update` continues metadata and statement updates. For editorials:

- Before v2 activation, keep v1 behavior until the explicit full rebuild is run.
- After v2 activation, call incremental `update_editorials` in the background.
- Preserve progress stages and add generation/status counts.
- Never mutate the active generation in place.

- [ ] **Step 6: Run rebuild, CLI, server, and crawler tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_editorial_rebuild tests.test_editorial_server tests.test_editorial_crawler -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit Task 10**

```bash
git add editorial_rebuild.py tests/test_editorial_rebuild.py update.py server.py
git commit -m "feat: add atomic editorial rebuild workflow"
```

---

### Task 11: Complete fixture coverage, documentation, and offline verification

**Files:**

- Modify: `tests/fixtures/editorials/manifest.json`
- Modify: `README.md:28-75`
- Modify: `README.zh-CN.md:28-75`
- Modify: `CHANGELOG.md`
- Modify: `.gitignore` only if temporary v2 work files are not already covered by exact runtime cleanup.

**Interfaces:**

- Verifies all previously produced interfaces against the approved spec.
- Produces exact operational documentation and immutable fixture receipts.

- [ ] **Step 1: Add fixture checksum verification**

Add a test method to `tests/test_editorial_parser.py` that reads `tests/fixtures/editorials/manifest.json`, computes SHA-256 for every listed fixture, and compares literal recorded hashes. A changed fixture without manifest review must fail.

- [ ] **Step 2: Run the checksum test and verify RED**

Run:

```bash
python3 -m unittest tests.test_editorial_parser.EditorialParserTests.test_fixture_checksums -v
```

Expected: failure until all final fixture hashes are recorded.

- [ ] **Step 3: Finalize fixture manifest and make checksum test GREEN**

Record every fixture path, SHA-256, encoding `utf-8`, newline `lf`, and one of these rationale values:

- `contest-1700-detached-title-regression`
- `contest-1369-spoiler-duplicate-regression`
- `contest-1706-heading-level-regression`
- `malformed-recovery`
- `nested-structure`
- `code-math-whitespace`
- `sanitizer-security`

Run the targeted checksum test again; expect PASS.

- [ ] **Step 4: Update English and Chinese documentation**

Document the real commands, v2 JSON/HTML layout, semantic parity, exact tutorial composition, full rebuild/rollback, status meanings, and unchanged statement Markdown path. Remove claims that `--editorials` works in ways the implementation does not provide. Document Node only as a development test command, not a runtime dependency.

Add a CHANGELOG entry naming:

- contest 1700 title/body fix
- exact problem-code composition
- nested spoilers and heading preservation
- sanitized HTML and sandbox
- atomic full recrawl and rollback
- read-only editorial GET

- [ ] **Step 5: Run static diagnostics before builds/tests**

Run:

```text
lsp_diagnostics on editorial_model.py, editorial_parser.py, editorial_render.py,
editorial_cache.py, editorial_rebuild.py, cfcrawl.py, server.py, and update.py
```

Expected: zero errors. Resolve all real errors before proceeding.

- [ ] **Step 6: Run the complete offline suite and verify GREEN**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/reader_payload.test.js
python3 -m py_compile editorial_model.py editorial_parser.py editorial_render.py editorial_cache.py editorial_rebuild.py cfcrawl.py server.py update.py
```

Expected: all tests pass; compilation exits 0; output contains no uncaught warnings/errors.

- [ ] **Step 7: Run repository diagnostics**

Run `lens_diagnostics(mode="all")` and resolve every blocking error in edited files. Record unrelated generated-data changes as pre-existing and leave them untouched.

- [ ] **Step 8: Commit Task 11**

```bash
git add tests/fixtures/editorials/manifest.json README.md README.zh-CN.md CHANGELOG.md
git commit -m "docs: document structured editorial rebuild"
```

---

### Task 12: Perform live regression validation and the requested full recrawl

**Files:**

- Runtime output only: inactive generation manifest and `editorials/v2/generations/<generation-id>/documents/<contestId>.json` files.
- Do not stage or commit runtime output until the user explicitly requests snapshot publication.

**Interfaces:**

- Exercises the completed CLI, crawler, cache, server, and frontend contracts.
- Produces validation receipts in the generation manifest and a reversible activation pointer.

- [ ] **Step 1: Capture the working-tree and process baseline**

Run read-only checks and record:

- `git status --short`
- active `server.py`/crawler processes
- existing `editorials/v2/current.json`, if any
- free disk space
- current v1 file counts

Do not stop a user-owned process without confirmation. If an existing crawler can write the same v2 paths, stop and ask before continuing.

- [ ] **Step 2: Run live contest 1700 validation before any rebuild**

Run:

```bash
python3 update.py --validate-editorial 1700
```

Expected report:

- `problemCodes` exactly `1700A` through `1700F`
- every body sentinel/structural body present directly under its matching problem section
- zero unresolved slots
- zero duplicate problem sections
- zero sanitizer violations
- exit code 0

If this fails, do not start the full recrawl.

- [ ] **Step 3: Run live 1369 and 1706 validation probes**

Use the same validator for contests 1369 and 1706. Confirm:

- 1369 retains `h4` source problem headings and nested spoiler boundaries without generated `h2` duplicates.
- 1706 retains the official level sequence, including `h2` to `h4` transitions.

Record canonical hashes and structural summaries.

- [ ] **Step 4: Start the full inactive rebuild**

Run through context-mode so long output is captured and summarized:

```bash
python3 update.py --editorials --rebuild
```

Expected: bounded batches, resumable generation, no v1 cache hits, atomic per-contest files, and no activation while transient/invalid statuses remain.

- [ ] **Step 5: Resolve all nonterminal statuses without weakening validation**

Retry transient entries through the same generation. For each invalid structure, capture contest ID, source URL, parser diagnostic, and minimal HTML fixture; add a failing fixture test before changing parser behavior. Never mark a network/parser failure as known absent.

Repeat until manifest counts show only `ready` and `known_absent`.

- [ ] **Step 6: Verify activation and rollback metadata**

Confirm:

- `editorials/v2/current.json` points to the completed generation.
- Previous pointer/generation remains available.
- Every ready document parses, validates, and renders.
- Manifest expected contest set equals current problem metadata contest set.
- No ready document contains unresolved slots or remote images.

- [ ] **Step 7: Start a non-conflicting local server smoke session**

Choose a confirmed free port and run `CFDB_PORT=<port> python3 server.py`. Verify HTTP payloads for 1700, 1369, 1706, one known-absent contest, and one statement. Confirm GET requests leave manifest and failure-memory bytes/mtimes unchanged.

Open the UI manually and verify:

- 1700 A-F title/body adjacency
- nested spoilers toggle and trigger MathJax resizing
- source heading levels
- nested ordered/unordered lists
- code whitespace/highlighting/copy
- local images
- external links
- statement Markdown and personal solutions remain unchanged
- iframe sandbox does not block local MathJax/highlight assets

- [ ] **Step 8: Run final verification after live data activation**

Run again:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/reader_payload.test.js
python3 -m py_compile editorial_model.py editorial_parser.py editorial_render.py editorial_cache.py editorial_rebuild.py cfcrawl.py server.py update.py
```

Then run `lsp_diagnostics` on all touched Python files and `lens_diagnostics(mode="all")`.

Expected: all checks pass with no blocking diagnostics.

- [ ] **Step 9: Report operational results without publishing data**

Report:

- active generation ID
- ready/known-absent counts
- retry/invalid counts (both zero at activation)
- live validation receipts for 1700/1369/1706
- test commands and exit codes
- rollback pointer/generation
- pre-existing generated-data changes left untouched
- v2 output paths and disk usage

Do not commit, push, tag, or publish the full recrawl snapshot without a separate explicit user instruction.
