# Codeforces Content IR V2-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace both statement and editorial Markdown pipelines with typed IR, independent atomic generations, sanitized HTML APIs, and a v2-only runtime while preserving the original implementation as `v1` and creating dated full snapshot tag `snapshot-2026-08-15`.

**Architecture:** Extract shared semantic nodes, bounded source-tree parsing, safe rendering, immutable assets, and generic generation storage. Add a statement-specific document/parser/crawler/rebuild path alongside the existing editorial semantic path, then switch both APIs and frontend tabs to structured HTML only and delete legacy Markdown runtime code. Statement and editorial generations remain independently built and activated.

**Tech Stack:** Python 3 standard library, `unittest`, browser JavaScript, Node `node:test`, canonical JSON, atomic filesystem operations, local MathJax/highlight.js, Git branches and annotated tags.

**Spec:** `docs/superpowers/specs/2026-08-15-codeforces-content-ir-v2-design.md`

## Global Constraints

- Do not access Codeforces or any external network endpoint during implementation or verification.
- Do not run `--statements --rebuild`, `--editorials --rebuild`, either validation command, or any command that creates `statements/v2` or `editorials/v2` in the production working tree.
- Do not modify, stage, restore, or commit production crawl data while implementing code.
- Preserve the ten existing modified editorial fixture working copies; immutable committed fixture bytes remain authoritative.
- Use Python standard-library-only runtime code and `unittest`; add no Python, npm, or browser dependency.
- Statements and editorials use independent generation roots, manifests, pointers, rebuild locks, and rollback histories.
- Missing active generations return HTTP 503 `v2_not_initialized`; no API, startup path, or plain incremental command may trigger an initial rebuild.
- Legacy statement and editorial Markdown files may remain in `snapshot`, but v2 code must never read, write, import, or serve them.
- PDF statements are content-addressed local attachments; never invoke `pdftotext`, embed PDF in `srcdoc`, or serve remote PDF URLs.
- `v1` must point exactly to `e4b6099e8c5ee3da9a8a156b2a6006bbcb4332f3`.
- `main` contains code/tests/docs only and retains the `origin/main` production-data ignore policy.
- `snapshot` remains a descendant of `main` and contains complete code plus data.
- Create annotated tag `snapshot-2026-08-15` only after all code, branch, data, and test verification succeeds.
- Do not push branches or tags unless the user explicitly requests it.
- Use TDD for every behavior change: failing focused test, observed failure, minimal implementation, focused pass, broader pass, path-specific commit.

---

### Task 1: Extract the Shared Semantic Content Model

**Files:**

- Create: `content_model.py`
- Create: `statement_model.py`
- Modify: `editorial_model.py`
- Modify: `tests/test_editorial_model.py`
- Create: `tests/test_statement_model.py`
- Create: `tests/test_content_model.py`

**Interfaces:**

- Produces: `Diagnostic`, `ContentNode`, `SemanticDocument`, `canonical_json(document)`, `validate_content_tree(root, diagnostics, assets, *, ready, content_kind)`.
- Produces: `StatementDocument` with exact `problem_code`, `contest_id`, `index`, `source_url`, `source_kind`, `root`, `diagnostics`, and `assets`.
- Preserves: `EditorialDocument`, `Node`, `canonical_json`, and `validate_document` import compatibility while callers migrate.

- [ ] **Step 1: Add failing shared-model tests**

```python
from content_model import ContentNode, canonical_json, validate_content_tree


def test_content_node_canonical_json_is_stable():
    node = ContentNode(kind="paragraph", children=[ContentNode(kind="text", text="x")])
    assert canonical_json({"root": node.to_dict(), "schema": 2}) == (
        '{"root":{"children":[{"kind":"text","text":"x"}],"kind":"paragraph"},"schema":2}'
    )


def test_ready_attachment_requires_local_pdf_route(self):
    root = ContentNode(kind="document", children=[
        ContentNode(kind="attachment", attrs={"href": "https://example.com/a.pdf", "mediaType": "application/pdf"})
    ])
    with self.assertRaises(ValueError):
        validate_content_tree(root, [], [], ready=True, content_kind="statement")
```

Use a local `unittest.TestCase` context manager or `self.assertRaises(ValueError)` rather than importing pytest.

- [ ] **Step 2: Add failing statement-envelope tests**

```python
from statement_model import StatementDocument, validate_statement_document


def test_statement_identity_round_trips_exact_compound_index(self):
    document = StatementDocument(
        problem_code="1970A1",
        contest_id="1970",
        index="A1",
        source_url="https://codeforces.com/contest/1970/problem/A1",
        source_kind="html",
        root=ContentNode(kind="document"),
    )
    assert StatementDocument.from_dict(document.to_dict()) == document
    validate_statement_document(document, ready=False)
```

- [ ] **Step 3: Run the model tests and observe import failures**

Run:

```bash
python3 -m unittest -v tests.test_content_model tests.test_statement_model tests.test_editorial_model
```

Expected: FAIL because `content_model` and `statement_model` do not exist.

- [ ] **Step 4: Implement shared types and statement envelope**

```python
# content_model.py
SCHEMA_VERSION = 2

@dataclass
class ContentNode:
    kind: str
    text: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list["ContentNode"] = field(default_factory=list)

class SemanticDocument(Protocol):
    schema: int
    content_kind: str
    content_id: str
    root: ContentNode
    diagnostics: list[Diagnostic]
    assets: list[str]
    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError


def canonical_json(document_or_value: SemanticDocument | Mapping[str, Any]) -> str:
    value = document_or_value.to_dict() if hasattr(document_or_value, "to_dict") else document_or_value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

`validate_content_tree` must enforce node kinds, per-kind attributes, depth/size limits, local ready-resource routes, attachment media type, heading levels, and absence of unresolved slots. `statement_model.py` adds statement-specific identity and `sourceKind in {"html", "pdf"}` checks. `editorial_model.py` imports `ContentNode as Node` and delegates common validation without changing serialized editorial bytes.

- [ ] **Step 5: Run focused and existing model tests**

```bash
python3 -m unittest -v tests.test_content_model tests.test_statement_model tests.test_editorial_model
python3 -m unittest -v tests.test_editorial_parser tests.test_editorial_render
```

Expected: PASS.

- [ ] **Step 6: Commit the model extraction**

```bash
git add content_model.py statement_model.py editorial_model.py \
  tests/test_content_model.py tests/test_statement_model.py tests/test_editorial_model.py
git commit -m "refactor: share semantic content model"
```

---

### Task 2: Extract the Bounded Source DOM Builder

**Files:**

- Create: `content_parser.py`
- Modify: `editorial_parser.py`
- Create: `tests/test_content_parser.py`
- Modify: `tests/test_editorial_parser.py`

**Interfaces:**

- Consumes: `ContentNode`, `Diagnostic`.
- Produces: `ParseLimits`, `ParseError`, `SourceNode`, `parse_source_html(html_text, *, limits) -> SourceNode`.
- Produces helpers: `class_tokens(node)`, `find_first_by_class(root, class_name)`, `text_content(node)`.
- Preserves editorial APIs: `parse_blog_html`, `parse_tutorial_fragment`, and `compose_tutorials`.

- [ ] **Step 1: Add failing bounded-tree tests**

```python
from content_parser import ParseError, ParseLimits, parse_source_html


def test_source_tree_preserves_parent_child_and_sibling_order(self):
    root = parse_source_html("<section><h2>A</h2><div><p>B</p></div></section>")
    section = root.children[0]
    self.assertEqual([child.tag for child in section.children], ["h2", "div"])
    self.assertEqual(section.children[1].children[0].tag, "p")


def test_source_tree_rejects_depth_over_limit(self):
    with self.assertRaises(ParseError):
        parse_source_html("<div><div><div>x</div></div></div>", limits=ParseLimits(max_depth=2))
```

- [ ] **Step 2: Run parser tests and observe missing module failure**

```bash
python3 -m unittest -v tests.test_content_parser tests.test_editorial_parser
```

Expected: FAIL because `content_parser` does not exist.

- [ ] **Step 3: Implement the generic source tree**

```python
@dataclass
class SourceNode:
    tag: str
    attrs: dict[str, str]
    text: str = ""
    children: list["SourceNode"] = field(default_factory=list)


def parse_source_html(html_text: str, *, limits: ParseLimits = ParseLimits()) -> SourceNode:
    parser = _BoundedTreeBuilder(limits)
    parser.feed(html_text)
    return parser.finish()
```

Move malformed-tag recovery, attribute limits, node counts, text limits, and optional-closing behavior out of the editorial semantic parser. Keep source scripts/styles represented only as ignored source nodes; they must never become semantic nodes.

- [ ] **Step 4: Refactor editorial semantic mapping onto `SourceNode`**

`editorial_parser.py` should traverse the bounded source tree and produce the same editorial `ContentNode` bytes as before. Do not mix statement mapping into this file.

- [ ] **Step 5: Run parser/composer fixtures**

```bash
python3 -m unittest -v tests.test_content_parser tests.test_editorial_parser tests.test_editorial_composer
```

Expected: PASS with fixture canonical hashes unchanged.

- [ ] **Step 6: Commit the source-tree extraction**

```bash
git add content_parser.py editorial_parser.py tests/test_content_parser.py tests/test_editorial_parser.py
git commit -m "refactor: build bounded source DOM tree"
```

---

### Task 3: Parse Statement DOM into Typed IR

**Files:**

- Create: `statement_parser.py`
- Create: `tests/test_statement_parser.py`
- Create: `tests/fixtures/statements/manifest.json`
- Create: `tests/fixtures/statements/normal.html`
- Create: `tests/fixtures/statements/interaction.html`
- Create: `tests/fixtures/statements/localized.html`
- Create: `tests/fixtures/statements/malformed.html`
- Create: `tests/fixtures/statements/unsafe.html`
- Create: `tests/fixtures/statements/expected.json`

**Interfaces:**

- Consumes: `SourceNode`, `ParseLimits`, `ContentNode`, `StatementDocument`.
- Produces: `parse_statement_html(html_text, *, problem_code, contest_id, index, source_url, limits=ParseLimits()) -> StatementDocument`.
- Uses exact structural roles in `ContentNode.attrs["role"]`: `title`, `time_limit`, `memory_limit`, `input_channel`, `output_channel`, `body`, `input_specification`, `output_specification`, `samples`, `sample`, `sample_input`, `sample_output`, `note`, `interaction`, `scoring`, `custom`.

- [ ] **Step 1: Add checksum-pinned statement fixtures**

Each fixture must contain a complete synthetic `.problem-statement` DOM island and no network-derived bytes. `manifest.json` records path, SHA-256, UTF-8/LF, and rationale.

- [ ] **Step 2: Add failing statement hierarchy tests**

```python
def test_statement_preserves_metadata_sections_and_sample_pairs(self):
    document = parse_statement_html(
        fixture("normal.html"),
        problem_code="1700A",
        contest_id="1700",
        index="A",
        source_url="https://codeforces.com/contest/1700/problem/A",
    )
    roles = [node.attrs.get("role") for node in walk(document.root)]
    self.assertEqual(roles.count("sample"), 2)
    for sample in nodes_with_role(document.root, "sample"):
        self.assertEqual([child.attrs["role"] for child in sample.children], ["sample_input", "sample_output"])
```

- [ ] **Step 3: Add failing localized and unsafe tests**

```python
def test_localized_labels_do_not_determine_section_identity(self):
    document = parse_fixture("localized.html", "1000A")
    self.assertEqual(nodes_with_role(document.root, "input_specification")[0].text, None)


def test_statement_parser_ignores_script_and_event_attributes(self):
    document = parse_fixture("unsafe.html", "1000A")
    serialized = canonical_json(document)
    self.assertNotIn("onclick", serialized)
    self.assertNotIn("<script", serialized)
```

- [ ] **Step 4: Run tests and observe parser absence**

```bash
python3 -m unittest -v tests.test_statement_parser
```

Expected: FAIL because `statement_parser` does not exist.

- [ ] **Step 5: Implement structural statement mapping**

```python
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
    island = select_unique_problem_statement(source_root)
    root = map_statement_island(island, limits=limits)
    document = StatementDocument(
        problem_code=problem_code,
        contest_id=contest_id,
        index=index,
        source_url=source_url,
        source_kind="html",
        root=root,
    )
    validate_statement_document(document, ready=False)
    return document
```

Use DOM classes and containment, not visible Input/Output/Examples text, to assign roles. Pair sample input/output within each source sample subtree. Preserve custom sections in order with `role="custom"` and their original safe title text.

- [ ] **Step 6: Run statement and shared parser suites**

```bash
python3 -m unittest -v tests.test_statement_parser tests.test_content_parser tests.test_editorial_parser tests.test_editorial_composer
```

Expected: PASS.

- [ ] **Step 7: Commit the statement parser**

```bash
git add statement_parser.py tests/test_statement_parser.py tests/fixtures/statements
git commit -m "feat: parse statements into semantic IR"
```

---

### Task 4: Share Safe HTML Rendering and Add Attachments

**Files:**

- Create: `content_render.py`
- Create: `statement_render.py`
- Modify: `editorial_render.py`
- Create: `tests/test_content_render.py`
- Create: `tests/test_statement_render.py`
- Modify: `tests/test_editorial_render.py`

**Interfaces:**

- Consumes: `SemanticDocument`, `ContentNode`, statement roles.
- Produces: `render_content_html(document) -> str`, `sanitize_link_url`, `sanitize_image_url`, `sanitize_attachment_url`.
- Produces wrappers: `render_statement_html(document)` and existing `render_editorial_html(document)`.

- [ ] **Step 1: Add failing statement rendering tests**

```python
def test_statement_samples_render_as_paired_semantic_sections(self):
    html = render_statement_html(make_statement_with_two_samples())
    self.assertEqual(html.count('class="cf-sample"'), 2)
    self.assertLess(html.index('class="cf-sample-input"'), html.index('class="cf-sample-output"'))


def test_pdf_attachment_requires_local_content_address(self):
    node = ContentNode(kind="attachment", attrs={
        "href": "/statement-assets/" + "a" * 64 + ".pdf",
        "mediaType": "application/pdf",
        "label": "Open PDF",
    })
    self.assertIn("Open PDF", render_node_fixture(node, content_kind="statement"))
```

- [ ] **Step 2: Add failing attachment security tests**

```python
for href in ("https://evil.test/a.pdf", "javascript:alert(1)", "/statement-assets/a.pdf"):
    with self.subTest(href=href):
        with self.assertRaises(RenderError):
            sanitize_attachment_url(href)
```

- [ ] **Step 3: Run renderer tests and observe missing modules**

```bash
python3 -m unittest -v tests.test_content_render tests.test_statement_render tests.test_editorial_render
```

Expected: FAIL because shared/statement renderers do not exist.

- [ ] **Step 4: Implement shared allowlist rendering**

```python
_STATEMENT_ASSET_RE = re.compile(r"^/statement-assets/[0-9a-f]{64}\.pdf$")


def sanitize_attachment_url(value: object) -> str:
    if not isinstance(value, str) or not _STATEMENT_ASSET_RE.fullmatch(value):
        raise RenderError("unsafe attachment URL")
    return value


def render_content_html(document: SemanticDocument) -> str:
    validate_for_render(document)
    return _render_node(document.root, content_kind=document.content_kind)
```

Render statement role sections with semantic classes and preserve heading levels. Emit PDF attachments as escaped `<a>` elements only; never emit `iframe`, `object`, `embed`, or inline bytes.

- [ ] **Step 5: Run all renderer/security tests**

```bash
python3 -m unittest -v tests.test_content_render tests.test_statement_render tests.test_editorial_render
```

Expected: PASS.

- [ ] **Step 6: Commit shared rendering**

```bash
git add content_render.py statement_render.py editorial_render.py \
  tests/test_content_render.py tests/test_statement_render.py tests/test_editorial_render.py
git commit -m "feat: render semantic content safely"
```

---

### Task 5: Generalize Immutable Generation Storage

**Files:**

- Create: `content_cache.py`
- Create: `content_codecs.py`
- Modify: `editorial_cache.py`
- Create: `tests/test_content_cache.py`
- Modify: `tests/test_editorial_cache.py`

**Interfaces:**

- Consumes: `SemanticDocument`, `canonical_json`.
- Produces: `ContentStatus`, `DocumentCodec`, `GenerationStore`, `RebuildLock`, `activate_generation`, `load_active_generation`, `load_active_document`, `STATEMENT_CODEC`, and `EDITORIAL_CODEC`.
- Generic manifest fields: `contentKind`, `expectedIds`, `entries`, `counts`.

- [ ] **Step 1: Add failing generic statement/editorial store tests**

```python
# content_codecs.py
STATEMENT_CODEC = DocumentCodec(
    content_kind="statement",
    from_dict=StatementDocument.from_dict,
    validate=lambda document, ready: validate_statement_document(document, ready=ready),
)
EDITORIAL_CODEC = DocumentCodec(
    content_kind="editorial",
    from_dict=EditorialDocument.from_dict,
    validate=lambda document, ready: validate_document(document, ready=ready),
)


def test_statement_and_editorial_roots_activate_independently(self):
    statement_store = GenerationStore.create(statement_root, "s1", ["1700A"], STATEMENT_CODEC, "p", "f")
    editorial_store = GenerationStore.create(editorial_root, "e1", ["1700"], EDITORIAL_CODEC, "p", "f")
    ready(statement_store, make_statement())
    self.assertTrue(statement_store.is_activation_ready())
    self.assertFalse(editorial_store.is_activation_ready())
```

- [ ] **Step 2: Add failing content-kind and digest tests**

```python
def test_store_rejects_wrong_document_kind(self):
    store = GenerationStore.create(root, "s1", ["1700A"], STATEMENT_CODEC, "p", "f")
    with self.assertRaises(ValueError):
        store.write_document(make_editorial())
```

Retain existing canonical manifest, pointer, tamper detection, lock, immutable-finalization, seeding, and rollback tests.

- [ ] **Step 3: Run cache tests and observe missing generic cache**

```bash
python3 -m unittest -v tests.test_content_cache tests.test_editorial_cache
```

Expected: FAIL because `content_cache` does not exist.

- [ ] **Step 4: Implement codec-driven generic storage**

```python
@dataclass(frozen=True)
class DocumentCodec:
    content_kind: str
    from_dict: Callable[[dict[str, Any]], SemanticDocument]
    validate: Callable[[SemanticDocument, bool], None]

class GenerationStore:
    @classmethod
    def create(cls, root, generation_id, expected_ids, codec, parser_version, fixture_version, *, lock=None):
        normalized = sorted({_validate_component(value, "content ID") for value in expected_ids})
        return _run_with_writer_lock(
            Path(root), lock,
            lambda held: cls._create_locked(Path(root), generation_id, normalized, codec, parser_version, fixture_version, held),
        )

    def write_document(self, document, *, lock=None):
        if document.content_kind != self.codec.content_kind:
            raise ValueError("document content kind does not match generation")
        return _run_with_writer_lock(self.root, lock, lambda held: self._write_document_locked(document, held))

    def set_status(self, content_id, status, *, evidence, document_path=None, lock=None):
        return _run_with_writer_lock(
            self.root, lock,
            lambda held: self._set_status_locked(content_id, status, evidence, document_path, held),
        )
```

The pointer remains root-local; statement and editorial roots cannot share locks or manifests. Since no production v2 pointer exists, no legacy manifest migration path is required.

- [ ] **Step 5: Port editorial callers through compatibility exports**

`content_codecs.py` owns both production codecs. `editorial_cache.py` temporarily re-exports generic cache types and `EDITORIAL_CODEC`; Task 12 removes that wrapper after every caller imports `content_cache` and `content_codecs` directly.

- [ ] **Step 6: Run cache suites**

```bash
python3 -m unittest -v tests.test_content_cache tests.test_editorial_cache
```

Expected: PASS.

- [ ] **Step 7: Commit generic generation storage**

```bash
git add content_cache.py content_codecs.py editorial_cache.py tests/test_content_cache.py tests/test_editorial_cache.py
git commit -m "refactor: generalize content generations"
```

---

### Task 6: Localize Immutable Images and PDF Attachments

**Files:**

- Create: `content_assets.py`
- Modify: `cfcrawl.py`
- Create: `tests/test_content_assets.py`
- Modify: `tests/test_editorial_crawler.py`

**Interfaces:**

- Consumes: documents containing remote `image` or statement `attachment` source nodes.
- Produces: `AssetPolicy`, `AssetFetchResult`, `localize_content_assets(document, *, generation_asset_dir, route_prefix, fetcher, policy)`.
- PDF policy requires `application/pdf`, `%PDF-`, bounded bytes, full SHA-256 route, atomic write.

- [ ] **Step 1: Add failing raster and PDF asset tests**

```python
def test_pdf_is_written_by_full_digest_and_linked_locally(self):
    payload = b"%PDF-1.7\nfixture"
    localized = localize_statement_assets(
        make_pdf_statement("https://codeforces.com/a.pdf"),
        generation_asset_dir=temp_dir,
        fetcher=lambda _url: AssetFetchResult(payload, "application/pdf"),
    )
    digest = hashlib.sha256(payload).hexdigest()
    self.assertEqual(attachment(localized).attrs["href"], f"/statement-assets/{digest}.pdf")
    self.assertEqual((temp_dir / f"{digest}.pdf").read_bytes(), payload)
```

- [ ] **Step 2: Add failing PDF rejection and atomicity tests**

Cover HTML interstitial bytes, wrong MIME, missing `%PDF-`, oversized payload, interrupted write, existing digest mismatch, and remote URL remaining after localization.

- [ ] **Step 3: Run asset tests and observe missing module**

```bash
python3 -m unittest -v tests.test_content_assets tests.test_editorial_crawler
```

Expected: FAIL because `content_assets` does not exist.

- [ ] **Step 4: Implement shared immutable localization**

```python
@dataclass(frozen=True)
class AssetPolicy:
    allow_raster: bool
    allow_pdf_attachment: bool
    max_bytes: int


def localize_content_assets(document, *, generation_asset_dir, route_prefix, fetcher, policy):
    localized = copy.deepcopy(document)
    for node, node_path in walk_resource_nodes(localized.root):
        source = resource_source(node)
        fetched = fetcher(source)
        extension, payload = validate_asset_payload(node, fetched, policy)
        digest = hashlib.sha256(payload).hexdigest()
        target = Path(generation_asset_dir) / f"{digest}{extension}"
        atomic_write_asset(target, payload)
        rewrite_resource_node(node, f"{route_prefix}/{digest}{extension}")
    validate_localized_resources(localized, ready=True)
    return localized
```

Use full digest filenames, destination-directory fsync, exact magic-byte validation, and no shared mutable legacy image directory.

- [ ] **Step 5: Run asset/crawler tests**

```bash
python3 -m unittest -v tests.test_content_assets tests.test_editorial_crawler
```

Expected: PASS.

- [ ] **Step 6: Commit immutable content assets**

```bash
git add content_assets.py cfcrawl.py tests/test_content_assets.py tests/test_editorial_crawler.py
git commit -m "feat: localize immutable content assets"
```

---

### Task 7: Build Statement IR Without Markdown

**Files:**

- Create: `statement_crawl.py`
- Create: `tests/test_statement_crawler.py`
- Modify: `cfcrawl.py`

**Interfaces:**

- Consumes: `StatementDocument`, parser, asset localizer, existing bounded HTTP helper and problem metadata.
- Produces: `StatementBuildResult(status, document, evidence)`, `StatementSource`, `build_statement_document`, `fetch_statement_v2`.
- HTML source produces parsed/localized IR; PDF source produces title plus local attachment IR.

- [ ] **Step 1: Add failing HTML statement crawl tests**

```python
def test_fetch_statement_v2_returns_ready_ir_without_markdown(self):
    result = fetch_statement_v2(
        "1700A",
        source=FixtureStatementSource(html=fixture("normal.html")),
        asset_root=temp_dir,
    )
    self.assertEqual(result.status, ContentStatus.READY)
    self.assertEqual(result.document.content_kind, "statement")
    self.assertNotIn("md", result.evidence)
```

- [ ] **Step 2: Add failing PDF and identity tests**

```python
def test_pdf_source_becomes_local_attachment_not_text(self):
    result = fetch_statement_v2("1000A", source=FixtureStatementSource(pdf=b"%PDF-1.7\nfixture"), asset_root=temp_dir)
    self.assertEqual(result.document.source_kind, "pdf")
    self.assertEqual(nodes(result.document, "attachment")[0].attrs["mediaType"], "application/pdf")
    self.assertNotIn("pdf text", canonical_json(result.document).lower())
```

Also reject source pages whose derived contest/index do not match exact `problemCode`.

- [ ] **Step 3: Run crawler tests and observe missing module**

```bash
python3 -m unittest -v tests.test_statement_crawler
```

Expected: FAIL because `statement_crawl` does not exist.

- [ ] **Step 4: Implement source protocol and build flow**

```python
@dataclass(frozen=True)
class SourceFetch:
    source_url: str
    source_kind: str
    body: str | bytes
    content_type: str

@dataclass
class StatementBuildResult:
    status: ContentStatus
    document: StatementDocument | None
    evidence: dict[str, object]

class StatementSource(Protocol):
    def problem_codes(self) -> list[str]:
        raise NotImplementedError
    def fetch_problem(self, problem_code: str) -> SourceFetch:
        raise NotImplementedError
    def fetch_asset(self, url: str) -> AssetFetchResult:
        raise NotImplementedError


def build_statement_document(problem, source_fetch, *, asset_root, source):
    if source_fetch.source_kind == "html":
        document = parse_statement_html(
            source_fetch.body,
            problem_code=problem.problem_code,
            contest_id=problem.contest_id,
            index=problem.index,
            source_url=source_fetch.source_url,
        )
    elif source_fetch.source_kind == "pdf":
        document = make_pdf_attachment_document(problem, source_fetch.source_url)
    else:
        raise ValueError("unsupported statement source kind")
    return localize_statement_assets(document, generation_asset_dir=asset_root, fetcher=source.fetch_asset)


def fetch_statement_v2(problem_code, *, source=None, asset_root=None) -> StatementBuildResult:
    active_source = source if source is not None else LiveStatementSource()
    problem = require_exact_problem(active_source, problem_code)
    try:
        document = build_statement_document(problem, active_source.fetch_problem(problem_code), asset_root=asset_root, source=active_source)
        validate_statement_document(document, ready=True)
        return StatementBuildResult(ContentStatus.READY, document, {"sourceUrl": document.source_url})
    except TransientStatementError as error:
        return StatementBuildResult(ContentStatus.TRANSIENT_FAILURE, None, {"error": str(error)})
```

A recognized HTML interstitial, invalid PDF, missing island, or unresolved resource returns typed failure and writes nothing.

- [ ] **Step 5: Remove statement Markdown calls from active fetch flow**

No active function may call `problem_statement_to_md`, `_fetch_statement_pdf`, `_embed_images`, or write `statements/<problemCode>.md`.

- [ ] **Step 6: Run statement crawler/parser/model suites**

```bash
python3 -m unittest -v tests.test_statement_crawler tests.test_statement_parser tests.test_statement_model tests.test_content_assets
```

Expected: PASS.

- [ ] **Step 7: Commit the statement IR crawler**

```bash
git add statement_crawl.py cfcrawl.py tests/test_statement_crawler.py
git commit -m "feat: crawl statements into semantic IR"
```

---

### Task 8: Add Independent Statement Rebuild Workflows

**Files:**

- Create: `statement_rebuild.py`
- Create: `tests/test_statement_rebuild.py`
- Modify: `editorial_rebuild.py`

**Interfaces:**

- Consumes: generic generation store and `StatementSource`.
- Produces: `validate_statement(problem_code: str, *, source: StatementSource | None = None) -> dict`, `rebuild_statements(*, source=None, cache_root=None, generation_id=None, delay=DEFAULT_DELAY, sleep_fn=time.sleep) -> dict`, and `update_statements(*, source=None, cache_root=None, generation_id=None, requested_problems=None, delay=DEFAULT_DELAY, sleep_fn=time.sleep) -> dict`.
- Preserves editorial interfaces while moving shared generation-run helpers into content-neutral functions where practical.

- [ ] **Step 1: Add failing validation and full-rebuild tests**

```python
def test_validate_statement_uses_temporary_root_without_activation(self):
    report = validate_statement("1700A", source=FixtureStatementSource())
    self.assertTrue(report["ok"])
    self.assertFalse((production_root / "current.json").exists())


def test_complete_statement_rebuild_activates_independently(self):
    report = rebuild_statements(source=FixtureStatementSource(["1700A", "1700B"]), cache_root=root, generation_id="s1", sleep_fn=lambda _: None)
    self.assertTrue(report["activated"])
    self.assertEqual(load_active_generation(root, STATEMENT_CODEC).generation_id, "s1")
```

- [ ] **Step 2: Add failing incremental/no-parent tests**

```python
def test_incremental_statement_update_requires_active_parent(self):
    with self.assertRaisesRegex(ValueError, "statement v2 is not initialized"):
        update_statements(source=FixtureStatementSource(), cache_root=root)
```

Cover independent rollback, ready-document seeding, rechecking absence, active-parent drift, resumable full rebuild, and no production data reads.

- [ ] **Step 3: Run rebuild tests and observe missing module**

```bash
python3 -m unittest -v tests.test_statement_rebuild tests.test_editorial_rebuild
```

Expected: FAIL because `statement_rebuild` does not exist.

- [ ] **Step 4: Implement statement generation orchestration**

```python
def validate_statement(problem_code: str, *, source: StatementSource | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="cfdb-statement-validation-") as directory:
        result = fetch_statement_v2(problem_code, source=source, asset_root=Path(directory) / "assets")
        return validation_report(problem_code, result, temporary_root=Path(directory))


def rebuild_statements(*, source=None, cache_root=None, generation_id=None, delay=DEFAULT_DELAY, sleep_fn=time.sleep) -> dict:
    active_source = source if source is not None else LiveStatementSource()
    expected = active_source.problem_codes()
    return run_statement_generation(active_source, cache_root, generation_id, expected, delay, sleep_fn, seed=None, allow_resume=True)


def update_statements(*, source=None, cache_root=None, generation_id=None, requested_problems=None, delay=DEFAULT_DELAY, sleep_fn=time.sleep) -> dict:
    active_source = source if source is not None else LiveStatementSource()
    active = load_active_generation(cache_root, STATEMENT_CODEC)
    if active is None:
        raise ValueError("statement v2 is not initialized")
    return run_statement_generation(
        active_source, cache_root, generation_id, active_source.problem_codes(), delay, sleep_fn,
        seed=active, requested_ids=set(requested_problems or ()), allow_resume=False,
    )
```

Use `expectedProblems` metadata translated into generic `expectedIds`; do not inspect legacy Markdown or failure files.

- [ ] **Step 5: Run statement/editorial rebuild suites**

```bash
python3 -m unittest -v tests.test_statement_rebuild tests.test_editorial_rebuild tests.test_content_cache
```

Expected: PASS.

- [ ] **Step 6: Commit statement rebuild workflows**

```bash
git add statement_rebuild.py editorial_rebuild.py tests/test_statement_rebuild.py tests/test_editorial_rebuild.py
git commit -m "feat: add atomic statement rebuilds"
```

---

### Task 9: Serve Both Content Types as Read-Only V2 HTML

**Files:**

- Modify: `server.py`
- Create: `tests/test_statement_server.py`
- Modify: `tests/test_editorial_server.py`

**Interfaces:**

- Produces: `build_statement_payload(problem_code, *, cache_root=None) -> dict`.
- Updates: `build_editorial_payload` to return 503-compatible `v2_not_initialized` data without legacy reads.
- Adds immutable routes: `/statement-assets/<sha256>.<ext>` and `/editorial-assets/<sha256>.<ext>`.

- [ ] **Step 1: Replace fallback tests with failing 503 tests**

```python
def test_statement_without_pointer_is_uninitialized_and_ignores_markdown(self):
    legacy.write_text("must not leak")
    payload = build_statement_payload("1700A", cache_root=root)
    self.assertEqual(payload["status"], "v2_not_initialized")
    self.assertNotIn("md", payload)


def test_editorial_without_pointer_is_uninitialized_and_ignores_markdown(self):
    payload = build_editorial_payload("1700", cache_root=root)
    self.assertEqual(payload["status"], "v2_not_initialized")
```

HTTP handler tests assert status code 503.

- [ ] **Step 2: Add failing ready-statement and PDF response tests**

Assert structured HTML, `contentKind`, exact source URL, `application/pdf`, `X-Content-Type-Options: nosniff`, safe disposition, basename-only route resolution, and rejection of path traversal.

- [ ] **Step 3: Run server tests and observe legacy behavior failures**

```bash
python3 -m unittest -v tests.test_statement_server tests.test_editorial_server
```

Expected: FAIL because statements still return Markdown and editorials still fall back before activation.

- [ ] **Step 4: Implement read-only payloads and status mapping**

```python
def _uninitialized(content_kind: str) -> dict:
    return {"format": None, "contentKind": content_kind, "status": "v2_not_initialized", "error": f"{content_kind} v2 is not initialized"}


def build_statement_payload(problem_code, *, cache_root=None):
    root = Path(cache_root) if cache_root is not None else STATEMENT_V2_ROOT
    if not (root / "current.json").is_file():
        return _uninitialized("statement")
    document, entry = load_active_content(root, problem_code, STATEMENT_CODEC)
    if document is not None:
        return ready_payload(document, render_statement_html(document))
    if entry["status"] == ContentStatus.KNOWN_ABSENT.value:
        return known_absent_payload("statement")
    return invalid_structure_payload("active statement entry is nonterminal")
```

`Handler.do_GET` maps `v2_not_initialized` to 503, `invalid_structure` to 500, invalid references to 400, and ready/known-absent to 200. GET paths perform no network or mutation.

- [ ] **Step 5: Run server and cache tests**

```bash
python3 -m unittest -v tests.test_statement_server tests.test_editorial_server tests.test_content_cache
```

Expected: PASS.

- [ ] **Step 6: Commit v2-only server APIs**

```bash
git add server.py tests/test_statement_server.py tests/test_editorial_server.py
git commit -m "feat: serve v2 content only"
```

---

### Task 10: Render Statement and Editorial HTML Without Markdown

**Files:**

- Modify: `reader_payload.js`
- Modify: `tests/reader_payload.test.js`
- Modify: `index.html`
- Delete: `vendor/marked.min.js`

**Interfaces:**

- Produces JS payload shape: `{format: "html", contentKind, body, status, url}` or typed non-ready status.
- Removes statement/editorial Markdown rendering and heading normalization.

- [ ] **Step 1: Replace legacy frontend tests with failing structured-only tests**

```javascript
test("statement and editorial payloads require structured html", () => {
  assert.equal(normalizeApiPayload({ format: "markdown", md: "legacy" }), null);
  assert.deepEqual(normalizeApiPayload({
    format: "html", contentKind: "statement", html: "<article>x</article>", status: "ready"
  }).body, "<article>x</article>");
});

test("v2_not_initialized stays distinct", () => {
  assert.equal(normalizeApiPayload({ contentKind: "statement", status: "v2_not_initialized" }).status, "v2_not_initialized");
});
```

- [ ] **Step 2: Run Node tests and observe Markdown compatibility**

```bash
node --test tests/reader_payload.test.js
```

Expected: FAIL because Markdown is still normalized and rendered.

- [ ] **Step 3: Remove content Markdown paths**

Delete the marked.js script, math-protection Markdown transforms, `prepareMarkdownHtml`, `normalizeProblemHeaders`, and any statement/editorial fallback payload construction. Both tabs pass sanitized HTML directly to the existing sandboxed frame pipeline.

- [ ] **Step 4: Add statement semantic CSS and PDF link behavior**

Style metadata, statement sections, sample pairs, preformatted input/output, notes, interaction, scoring, and attachment links without changing the server-rendered hierarchy. PDF links open the local resource in a new browsing context permitted by the existing sandbox.

- [ ] **Step 5: Run Node tests and static assertions**

```bash
node --test tests/reader_payload.test.js
! grep -q "marked.min.js" index.html
! grep -q "normalizeProblemHeaders" index.html
! grep -q 'format: "markdown"' reader_payload.js
```

Expected: PASS.

- [ ] **Step 6: Commit structured-only frontend rendering**

```bash
git add reader_payload.js tests/reader_payload.test.js index.html
git rm vendor/marked.min.js
git commit -m "feat: render statements and editorials from IR"
```

---

### Task 11: Make CLI and Startup V2-Only Without Implicit Rebuilds

**Files:**

- Modify: `update.py`
- Modify: `server.py`
- Modify: `tests/test_statement_rebuild.py`
- Modify: `tests/test_editorial_rebuild.py`

**Interfaces:**

- Adds CLI: `--validate-statement PROBLEM_CODE`.
- `--statements --rebuild` dispatches `rebuild_statements`; plain `--statements` dispatches `update_statements` only with a pointer.
- Editorial commands mirror statement behavior.
- Server startup incrementally updates only roots with active pointers.

- [ ] **Step 1: Add failing CLI dispatch tests**

```python
def test_plain_statement_update_without_pointer_does_not_crawl(self):
    with patch("update.update_statements") as incremental, patch("update.rebuild_statements") as rebuild:
        self.assertNotEqual(update.main(["--statements"]), 0)
        incremental.assert_not_called()
        rebuild.assert_not_called()


def test_explicit_statement_rebuild_dispatches_once(self):
    with patch("update.rebuild_statements", return_value={"activated": False}) as rebuild:
        update.main(["--statements", "--rebuild"])
        rebuild.assert_called_once()
```

Also cover editorial no-pointer behavior, validation modes, both content flags rejected together, and bare `--rebuild` rejected.

- [ ] **Step 2: Add failing server startup tests**

Assert zero statement/editorial crawler or rebuild calls with no pointers, and independent incremental calls when one or both pointers exist.

- [ ] **Step 3: Run CLI/background tests and observe fallback calls**

```bash
python3 -m unittest -v tests.test_statement_rebuild tests.test_editorial_rebuild
```

Expected: FAIL because server and CLI still invoke legacy crawlers.

- [ ] **Step 4: Implement explicit dispatch**

```python
if args.statements:
    if args.rebuild:
        return report_exit(rebuild_statements())
    require_pointer(STATEMENT_V2_ROOT, "statement")
    return report_exit(update_statements())
```

Mirror for editorials. `auto_update` records each missing root as uninitialized and performs no initial network request.

- [ ] **Step 5: Run CLI/server suites**

```bash
python3 -m unittest -v tests.test_statement_rebuild tests.test_editorial_rebuild tests.test_statement_server tests.test_editorial_server
```

Expected: PASS.

- [ ] **Step 6: Commit v2-only orchestration**

```bash
git add update.py server.py tests/test_statement_rebuild.py tests/test_editorial_rebuild.py
git commit -m "fix: require explicit initial content rebuilds"
```

---

### Task 12: Delete Legacy Content Pipelines

**Files:**

- Modify: `cfcrawl.py`
- Delete: `html2md.py`
- Delete: `editorial_cache.py`
- Delete: `tests/test_legacy_editorial_crawler.py`
- Verify: root-level Python modules have no imports or identifier references to deleted legacy content helpers.
- Modify: `tests/test_editorial_crawler.py`
- Modify: `tests/test_statement_crawler.py`

**Interfaces:**

- Preserves only shared HTTP/problem metadata, v2 statement/editorial crawl helpers, solution reading, and generic batch utilities still used by v2.
- Removes every active/read compatibility symbol for Markdown content.

- [ ] **Step 1: Add a failing static legacy-reference test**

```python
def project_identifiers(names):
    root = Path(__file__).parents[1]
    matches = {}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                candidate = node.name
            elif isinstance(node, ast.Name):
                candidate = node.id
            elif isinstance(node, ast.Attribute):
                candidate = node.attr
            else:
                continue
            if candidate in names:
                found.add(candidate)
        if found:
            matches[path.name] = sorted(found)
    return matches


def test_v2_source_has_no_legacy_content_symbols(self):
    forbidden = {
        "read_statement_md", "fetch_statement_md", "fetch_all_statements",
        "read_editorial_md", "fetch_editorial_md", "fetch_all_editorials",
        "problem_statement_to_md", "editorial_to_md", "_replace_tutorial",
        "_fetch_statement_pdf", "_embed_images",
    }
    self.assertEqual(project_identifiers(forbidden), {})
```

The test uses only `ast` and `pathlib`, scans root production Python modules, and never shells out.

- [ ] **Step 2: Run the static test and observe legacy symbols**

```bash
python3 -m unittest -v tests.test_v2_only_source
```

Expected: FAIL listing current legacy functions.

- [ ] **Step 3: Remove legacy readers, writers, converters, and failure memory**

Delete Markdown statement/editorial paths, PDF text extraction, mutable image embedding, legacy batch crawlers, legacy failure files from runtime imports, `html2md.py`, and the temporary legacy regression test. Keep v2 exact tutorial composition tests and statement PDF attachment tests.

- [ ] **Step 4: Run reference and full focused suites**

```bash
python3 -m unittest -v tests.test_v2_only_source tests.test_statement_crawler tests.test_editorial_crawler tests.test_statement_parser tests.test_editorial_parser
```

Expected: PASS.

- [ ] **Step 5: Commit legacy removal**

```bash
git add cfcrawl.py tests/test_editorial_crawler.py tests/test_statement_crawler.py tests/test_v2_only_source.py
git rm html2md.py editorial_cache.py tests/test_legacy_editorial_crawler.py
git commit -m "refactor: remove legacy content pipelines"
```

---

### Task 13: Document, Verify, Cut Over Branches, and Create Dated Snapshot Tag

**Files:**

- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-08-15-codeforces-content-ir-v2-design.md` only if implementation names differ from the approved interfaces
- Modify: this plan only for checked task boxes if execution policy records them

**Interfaces:**

- Produces Git refs: `v1`, updated code-only `main`, full descendant `snapshot`, annotated tag `snapshot-2026-08-15`.
- Does not produce or activate statement/editorial generations.

- [ ] **Step 1: Update user-facing documentation**

Document:

```text
python3 update.py --validate-statement 1700A
python3 update.py --validate-editorial 1700
python3 update.py --statements --rebuild
python3 update.py --editorials --rebuild
```

State that both APIs are v2-only, both initial rebuilds are explicit, both missing roots return 503, PDF statements are local attachments, and no Markdown fallback exists.

- [ ] **Step 2: Run proactive diagnostics before builds**

```text
lsp_diagnostics on all changed Python and JavaScript files
lens_diagnostics mode=all on edited files
```

Expected: zero blocking diagnostics.

- [ ] **Step 3: Run all offline tests from the working tree**

```bash
python3 - <<'PY'
import unittest
suite = unittest.defaultTestLoader.discover("tests")
def flatten(node):
    for item in node:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item
selected = [test for test in flatten(suite) if not test.id().endswith("EditorialParserTests.test_fixture_checksums")]
result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(selected))
raise SystemExit(not result.wasSuccessful())
PY
node --test tests/reader_payload.test.js
python3 -m py_compile *.py tests/*.py
git diff --check -- . ':(exclude)tests/fixtures/editorials/**'
```

Expected: all functional tests pass. Separately hash every committed editorial fixture with `git show HEAD:<path>` against `tests/fixtures/editorials/manifest.json`; require zero committed-byte mismatches. Do not restore or stage the ten modified working copies, and do not run validation or rebuild commands.

- [ ] **Step 4: Verify no production mutation or v2 generation creation**

```bash
test ! -e statements/v2/current.json
test ! -e editorials/v2/current.json
test -z "$(git diff --cached --name-only)"
git status --short -- statements editorials problems.json failed_editorials.json failed_statements.json
```

Expected: no implementation-created production changes. Pre-existing snapshot data remains committed and untouched.

- [ ] **Step 5: Commit documentation on `snapshot`**

```bash
git add README.md README.zh-CN.md CHANGELOG.md
git commit -m "docs: document v2-only content IR"
```

- [ ] **Step 6: Create and verify `v1`**

```bash
if git show-ref --verify --quiet refs/heads/v1; then
  test "$(git rev-parse v1)" = "e4b6099e8c5ee3da9a8a156b2a6006bbcb4332f3"
else
  git branch v1 e4b6099e8c5ee3da9a8a156b2a6006bbcb4332f3
fi
test "$(git rev-parse v1)" = "e4b6099e8c5ee3da9a8a156b2a6006bbcb4332f3"
```

If `v1` already exists, require it to resolve to the exact hash rather than moving it.

- [ ] **Step 7: Replay all implementation commits onto code-only `main`**

Use a temporary index or equivalent no-worktree method because the current `snapshot` checkout contains protected fixture modifications. Replay only source/test/doc paths from the implementation commit range onto the existing local `main`; reject any path under `statements/`, `editorials/`, `images/`, or generated JSON data.

Verify:

```bash
test -z "$(git diff --name-only origin/main..main -- statements editorials images problems.json failed_editorials.json failed_statements.json)"
test "$(git rev-parse main:.gitignore)" = "$(git rev-parse origin/main:.gitignore)"
```

- [ ] **Step 8: Merge `main` into `snapshot` without changing snapshot data**

Create a same-tree merge commit with current `snapshot` as first parent and updated `main` as second parent. Verify `git merge-base --is-ancestor main snapshot` succeeds and every implementation code path has the same blob on both branches.

- [ ] **Step 9: Run clean-tree tests from `main` archive**

```bash
tmp=$(mktemp -d)
git archive main | tar -x -C "$tmp"
(cd "$tmp" && python3 -m unittest && node --test tests/reader_payload.test.js)
rm -rf "$tmp"
```

Expected: PASS without snapshot production data.

- [ ] **Step 10: Validate committed full snapshot data without crawling**

Archive `snapshot` locally and verify JSON readability, no unresolved IR slots, no blocked-page markers, valid content-addressed asset names, main ancestry, and independent absence of active pointers. Do not modify the archive or working tree.

- [ ] **Step 11: Create the annotated dated snapshot tag last**

```bash
test -z "$(git tag -l snapshot-2026-08-15)"
git tag -a snapshot-2026-08-15 snapshot \
  -m "Full code and data snapshot 2026-08-15"
test "$(git rev-parse snapshot-2026-08-15^{})" = "$(git rev-parse snapshot)"
```

The tag is local only unless the user separately requests a push.

- [ ] **Step 12: Record final evidence**

Report:

- `v1`, `main`, `snapshot`, and tag object/peeled hashes
- commit list and per-commit path scope
- Python/Node/LSP/lens results
- no active generation and no crawler invocation
- remaining protected fixture working-copy modifications
- residual risk that content remains HTTP 503 until later explicit rebuilds

No additional commit is required after the tag unless documentation changed during verification.
