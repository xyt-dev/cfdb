# Codeforces Content IR V2-Only Design

**Status:** Approved in chat; pending written review

**Date:** 2026-08-15

**Supersedes:** `2026-08-14-codeforces-editorial-structure-design.md`

## Summary

cfdb will replace both lossy HTML-to-Markdown content pipelines with a typed semantic intermediate representation. Codeforces problem statements and editorials will be parsed from their current source DOM, validated as canonical JSON, stored in independently activated versioned generations, and rendered through one deterministic allowlist HTML renderer.

Problem statements and editorials share semantic nodes, resource handling, sanitization, and rendering, but retain separate document envelopes, crawler semantics, manifests, activation pointers, rebuild commands, and rollback histories. Neither content type may fall back to legacy Markdown at runtime.

The implementation and branch cutover do not crawl Codeforces. Initial statement and editorial rebuilds remain separate, explicit operator actions performed after the code is reviewed and deployed.

## User Decisions

- The original code-only `origin/main` revision is preserved as the `v1` branch.
- `main` becomes v2-only code and contains no production crawl data.
- `snapshot` remains a descendant of `main` and contains the same code plus the complete generated-data snapshot.
- Both statements and editorials use typed IR as their canonical cache format.
- Statements and editorials use independent generation roots and activation pointers.
- Missing initial generations return HTTP 503 and never trigger automatic rebuilding or legacy fallback.
- Plain incremental commands require an active generation; only an explicit `--rebuild` command may create the first generation.
- PDF-only statements are downloaded as immutable local PDF resources and exposed through safe attachment links. They are not converted with `pdftotext` and are not embedded as executable content.
- Runtime and tests remain Python-standard-library-only and dependency-free in the browser.
- The implementation phase must not access Codeforces, build a generation, or modify production data.

## Problem Statement

### Legacy statement pipeline

The current statement path is:

1. Fetch the Codeforces problem page.
2. Extract `.problem-statement` HTML.
3. Convert it to Markdown through global streaming state in `html2md.py`.
4. Download images into a mutable shared directory.
5. Cache `statements/<problemCode>.md` permanently.
6. Render Markdown in the browser and run a second heading-normalization pass.

This loses or synthesizes structure. Existing cached examples show duplicate problem titles, repeated Input and Output headings, malformed sample boundaries, headings inferred from literal source text, and PDF formulas degraded through text extraction.

### Legacy editorial pipeline

The legacy editorial path similarly flattens blog DOM into Markdown before composing dynamic tutorial fragments. It cannot reliably associate titles, spoilers, bodies, lists, or code blocks after hierarchy has been discarded.

### Runtime split

The existing migration code contains a typed editorial path but still falls back to legacy statement and editorial Markdown when no v2 pointer exists. Existing Markdown files are permanent cache hits, so parser fixes do not repair them. A v2-only runtime must remove this split rather than add another compatibility branch.

## Goals

1. Preserve source DOM hierarchy and sibling order for statements and editorials.
2. Use exact full problem identity for every statement and dynamic tutorial slot.
3. Preserve headings, metadata, sections, samples, interaction text, scoring, spoilers, lists, tables, quotes, code, formulas, images, and attachments.
4. Store canonical typed JSON rather than Markdown or raw source HTML.
5. Render deterministic sanitized HTML without frontend structural rewriting.
6. Use immutable content-addressed local resources with atomic writes.
7. Keep statement and editorial rebuilds, failures, activation, and rollback independent.
8. Make API GET requests read-only and network-free.
9. Make all initial rebuilds explicit and prevent server startup from triggering them.
10. Preserve the original implementation at the Git branch level rather than through runtime fallback.

## Non-Goals

- Pixel-perfect reproduction of Codeforces CSS.
- Executing Codeforces JavaScript, widgets, forms, or embedded frames.
- Importing legacy Markdown into IR.
- Recovering lost hierarchy from existing Markdown caches.
- Running a statement or editorial rebuild during implementation.
- Combining statements and editorials into one all-or-nothing generation.
- Adding third-party Python, npm, or browser dependencies.
- Parsing PDF layout or formulas into semantic nodes.

## Branch and Deployment Model

- `v1` points to the original `origin/main` revision before IR migration.
- `main` contains v2 source, tests, fixtures, and documentation only. Its existing production-data ignore policy remains unchanged.
- `snapshot` is a descendant of `main` and contains the same code plus generated statements, editorials, images, manifests, and activation data.
- Returning to v1 is an explicit branch-level deployment rollback.
- Runtime rollback never consults v1 files; it switches only to a prior validated generation for the affected content type.
- Legacy `.md` files may remain temporarily in `snapshot` as inert historical data, but v2 code never reads, writes, imports, or serves them.

## Shared Semantic IR

### Content node

The shared `ContentNode` model is a bounded recursive tree with these general node kinds:

- `document`
- `section`
- `heading`
- `paragraph`
- `text`
- `emphasis`
- `strong`
- `inline_code`
- `code_block`
- `math_inline`
- `math_block`
- `link`
- `image`
- `attachment`
- `list`
- `list_item`
- `quote`
- `table`
- `table_head`
- `table_body`
- `table_row`
- `table_cell`
- `line_break`
- `spoiler`
- `missing_asset`

Statement- and editorial-specific meaning is represented through typed wrapper nodes and allowlisted attributes, not arbitrary source classes or raw HTML.

Every node is subject to limits on depth, child count, text length, attribute count, URL length, table dimensions, code-block size, and total document size.

### Statement document

A canonical statement document contains:

```json
{
  "schema": 2,
  "contentKind": "statement",
  "problemCode": "1700A",
  "contestId": "1700",
  "index": "A",
  "sourceUrl": "https://codeforces.com/contest/1700/problem/A",
  "sourceKind": "html",
  "document": {
    "kind": "document",
    "children": []
  },
  "diagnostics": [],
  "assets": []
}
```

`problemCode` is the canonical identity and must equal the exact concatenation required by current metadata. The document may not be matched by title text, ordinal position, or first-letter fallback.

Statement-specific semantic nodes or section roles include:

- title
- time limit
- memory limit
- input channel
- output channel
- statement body
- input specification
- output specification
- sample collection
- sample case
- sample input
- sample output
- note
- interaction
- scoring
- custom named section

Sample inputs and outputs remain paired by their source subtree rather than by matching translated heading text.

### Editorial document

`EditorialDocument` retains contest identity, source URL, exact tutorial slots, semantic problem sections, diagnostics, and assets. Dynamic tutorial fragments replace only slots with the exact full `problemCode`. Fragment headings are suppressed only when an exact structural source problem heading already owns the slot.

### Canonical serialization

Documents use sorted-key, UTF-8 canonical JSON with deterministic list order. Raw source HTML, source scripts, arbitrary attributes, and rendered HTML are never cached as canonical content.

## Source Parsing

### Shared bounded HTML tree builder

A standard-library HTML tree builder creates a bounded source tree before semantic mapping. It preserves nesting and order, performs deterministic malformed-HTML recovery, records recovery diagnostics, and refuses documents that exceed safety thresholds.

### Statement semantic mapping

The statement parser selects one credible `.problem-statement` island and maps Codeforces structural classes and element relationships, including:

- header and title containers
- time and memory limits
- input and output file descriptors
- `.input-specification`
- `.output-specification`
- `.sample-tests`
- per-sample `.input` and `.output` containers
- `.note`
- `.interaction`
- scoring and custom sections
- lists, tables, block quotes, preformatted samples, TeX, images, and links

Semantic mapping is structural. Localized visible labels such as Input, Output, Examples, Note, or their Russian equivalents are preserved as text but are not used as the primary identity of a section.

The parser must not call `problem_statement_to_md`, infer headings from rendered text, or run a browser heading-normalization pass.

### Editorial semantic mapping

Editorial parsing continues to select the bounded editorial island, preserve source hierarchy, discover exact `problemTutorial[problemcode]` slots, parse tutorial fragments as subtrees, and compose by full problem identity.

## PDF Statement Attachments

When the authoritative statement source is PDF-only:

1. Download the original PDF bytes with existing network limits and explicit size bounds.
2. Require a valid `%PDF-` signature and reject HTML interstitials, truncated files, redirects to unsafe origins, or oversized payloads.
3. Compute the full SHA-256 digest.
4. Atomically store the file under the inactive statement generation as `assets/<sha256>.pdf`.
5. Create a `StatementDocument` with `sourceKind: "pdf"`, a title, an explanatory paragraph, and an `attachment` node pointing to the local immutable resource.
6. Record a `pdf_attachment` diagnostic without treating the document as structurally invalid.

PDFs are never passed through `pdftotext`. The renderer emits only a safe local anchor. It does not emit `iframe`, `object`, `embed`, `data:` URLs, inline PDF bytes, or executable content. The server returns `application/pdf` with safe content-disposition and nosniff headers.

## Resource Localization

- Statement and editorial resources are scoped to their own inactive generation.
- Raster image formats are limited to `.png`, `.jpg`, `.jpeg`, `.gif`, and `.webp` with matching magic-byte validation.
- PDF is allowed only for a statement `attachment` node and must pass PDF-specific validation.
- Every resource route is content-addressed by the full SHA-256 digest.
- Downloads write to a temporary file, flush, fsync, validate, and atomically replace the final destination.
- A ready HTML document has no remote image dependency.
- Missing or permanently unsupported images become `missing_asset` nodes with diagnostics.
- Transient resource failures block that document from becoming ready.

## Deterministic Safe Rendering

One allowlist renderer consumes both document types. It:

- validates the full document before rendering
- escapes all text and attributes
- emits only approved semantic elements
- validates local resource URLs by content type and route
- rejects unsafe schemes and protocol-relative URLs
- preserves heading levels and source order
- renders samples, code, and tables without Markdown round-trips
- renders spoilers as nested `details` and `summary`
- emits attachments only as safe anchors
- never emits raw source HTML

The frontend sandbox remains `allow-scripts allow-popups allow-popups-to-escape-sandbox`; `allow-same-origin` remains forbidden.

## Independent Generation Model

### Statement generations

```text
statements/
└── v2/
    ├── current.json
    └── generations/
        └── <generation-id>/
            ├── manifest.json
            ├── documents/
            │   └── <problemCode>.json
            └── assets/
                └── <sha256>.<extension>
```

The statement manifest contains `expectedProblems` using exact full problem codes and per-problem terminal state.

### Editorial generations

```text
editorials/
└── v2/
    ├── current.json
    └── generations/
        └── <generation-id>/
            ├── manifest.json
            ├── documents/
            │   └── <contestId>.json
            └── assets/
                └── <sha256>.<extension>
```

The editorial manifest contains `expectedContests` and per-contest terminal state.

### Shared generation guarantees

- Statement and editorial pointers are independent.
- A failure in one content type cannot block activation of the other.
- Documents, resources, manifests, and pointers use atomic writes.
- Finalized generations are immutable.
- Activation verifies canonical document hashes and resource hashes transitively.
- A shared interprocess rebuild lock prevents conflicting writers within one content root.
- Only `ready` and rigorously confirmed `known_absent` entries are activation-terminal.
- `transient_failure`, `invalid_structure`, unresolved assets, or pending entries prevent activation.

## Crawler and CLI Contracts

### Validation commands

```text
python3 update.py --validate-statement 1700A
python3 update.py --validate-editorial 1700
```

Validation fetches, parses, localizes, validates, and renders in a temporary root. It never changes active generations, legacy caches, failure memory, or production assets.

### Statement commands

```text
python3 update.py --statements
python3 update.py --statements --rebuild
```

- Plain `--statements` requires an active statement v2 pointer and builds an incremental successor.
- Without a pointer, plain `--statements` exits nonzero and instructs the operator to run the explicit rebuild.
- `--statements --rebuild` is the only command allowed to create the first complete statement generation.

### Editorial commands

```text
python3 update.py --editorials
python3 update.py --editorials --rebuild
```

- Plain `--editorials` requires an active editorial v2 pointer and builds an incremental successor.
- Without a pointer, plain `--editorials` exits nonzero and instructs the operator to run the explicit rebuild.
- `--editorials --rebuild` is the only command allowed to create the first complete editorial generation.

No API request or server startup path invokes either initial rebuild. The implementation phase runs none of these network commands.

## Read-Only API Contracts

### Statement API

A ready statement response is:

```json
{
  "format": "html",
  "schema": 2,
  "contentKind": "statement",
  "html": "<article>...</article>",
  "url": "https://codeforces.com/contest/1700/problem/A",
  "known": true,
  "status": "ready"
}
```

Without `statements/v2/current.json`, the endpoint returns HTTP 503:

```json
{
  "format": null,
  "contentKind": "statement",
  "status": "v2_not_initialized",
  "error": "statement v2 is not initialized"
}
```

### Editorial API

A ready editorial response retains the structured HTML schema with `contentKind: "editorial"`. Without `editorials/v2/current.json`, it returns HTTP 503 with `status: "v2_not_initialized"` and an editorial-specific error message.

Both endpoints are read-only. They perform no network requests, create no directories, modify no pointers, update no failure memory, and never inspect legacy Markdown files.

## Server Background Behavior

- Server startup may incrementally update a content type only when that content type already has a valid active pointer.
- If the statement pointer is missing, statement update is skipped and reported as uninitialized.
- If the editorial pointer is missing, editorial update is skipped and reported as uninitialized.
- Missing pointers never cause a full rebuild.
- Statement and editorial progress/status counts remain independent.
- API availability does not depend on a crawler finishing during the request.

## Frontend Contract

- Statement and editorial tabs accept only structured HTML payloads.
- Neither content tab calls the Markdown renderer or heading normalizer.
- `v2_not_initialized` displays an explicit service-unavailable message, distinct from `known_absent` and transient load failure.
- Statement title, metadata, sections, samples, notes, interactions, images, formulas, and PDF attachments are styled from semantic tags without changing hierarchy.
- Editorial spoilers and problem sections retain their structured behavior.
- Local MathJax, syntax highlighting, copy buttons, image diagnostics, external-link safety, and iframe height synchronization operate after insertion.
- The local solution viewer remains independent and is not part of the content IR migration.

## Failure and Recovery Policy

### `v2_not_initialized`

A runtime state caused only by a missing active pointer. It returns HTTP 503, performs no mutation, and is not persisted as a content status.

### `known_absent`

Recorded only after repeated successful, recognized source checks establish that the expected content genuinely has no public source. Blocked pages, announcements, malformed pages, missing selectors, PDF download failures, and transient network errors never become `known_absent`.

### `transient_failure`

Used for timeouts, rate limits, 403/interstitial pages, CSRF failures, invalid JSON, temporary resource failures, and incomplete downloads. Retryable and activation-blocking.

### `invalid_structure`

Used when no credible content island exists, exact identity validation fails, parser recovery exceeds limits, sample relationships are inconsistent, sanitized rendering fails, or canonical invariants are violated.

### Rollback

Each content type atomically restores its own previous validated pointer. Branch-level v1 rollback is separate and never occurs automatically.

## Security Requirements

1. No source script, style, iframe, form, object, embed, event attribute, or arbitrary inline style reaches output.
2. No unsafe URL scheme reaches a link, image, or attachment.
3. Raw source HTML and source JSON are never returned by the API.
4. PDF bytes must pass signature, size, origin, atomic-write, and immutable-route validation.
5. PDF is served only as a local attachment and never injected into `srcdoc`.
6. Raster resources require approved extensions and matching magic bytes.
7. All rendered text and attributes are escaped.
8. Every ready document validates before rendering.
9. API GET requests remain network-free and mutation-free.
10. The iframe sandbox is exact and cannot gain `allow-same-origin`.

## Testing Strategy

### Shared IR tests

- canonical serialization
- recursive validation and resource limits
- URL policies
- renderer determinism
- escaping and sanitizer allowlist
- local resource route validation
- atomic writes and immutable generation hashes

### Statement fixture corpus

Fixtures cover:

- normal title and metadata header
- Input and Output specifications
- multiple paired samples
- Note and Interaction sections
- Scoring and custom sections
- nested lists, tables, quotes, code, formulas, and images
- localized visible headings
- malformed but recoverable DOM
- unsafe elements and URLs
- exact problem identity including compound indexes
- PDF attachment success, invalid magic, oversize, and interrupted download

### Editorial fixture corpus

Existing 1369, 1700, 1706, malformed, unsafe, code/math, and nested-structure fixtures remain required.

### API and CLI tests

- no-pointer statement response is HTTP 503 and does not read Markdown
- no-pointer editorial response is HTTP 503 and does not read Markdown
- ready documents return only sanitized HTML
- known absence returns a null body
- plain update without a pointer exits nonzero without crawler calls
- explicit rebuild is the only initial-generation route
- server startup skips missing roots and never starts a full rebuild
- statement and editorial activation and rollback are independent

### Branch tests

- `v1` equals the original `origin/main` revision
- `main` contains no production data
- `snapshot` is a descendant of `main`
- v2 code does not reference legacy statement or editorial readers/crawlers

All implementation tests use fixtures, temporary roots, mocks, and local byte payloads. They do not access Codeforces.

## Rollout Sequence

1. Commit this approved design without changing runtime behavior.
2. Write and approve a task-level implementation plan.
3. Create `v1` at the original `origin/main` revision.
4. Generalize the semantic node, renderer, resource, and generation primitives.
5. Add statement document schema and parser with failing fixtures first.
6. Add PDF attachment localization and serving.
7. Add independent statement generation and rebuild workflows.
8. Convert statement API and frontend to structured HTML only.
9. Remove legacy statement readers, crawlers, Markdown rendering, and automatic startup crawl.
10. Remove remaining legacy editorial runtime fallback and compatibility tests.
11. Update documentation and CLI help.
12. Run offline Python, Node, LSP, security, branch, and cache verification.
13. Commit v2 code to `main` and merge it into `snapshot` without production data entering `main`.
14. Stop with both generation roots uninitialized and perform no network crawl.
15. At a later explicit operator decision, rebuild and activate statement and editorial generations independently.

## Acceptance Criteria

- Both statement and editorial canonical caches are typed IR JSON.
- Statement DOM hierarchy, section order, sample pairing, metadata, interaction, scoring, formulas, images, and attachments are preserved.
- Editorial hierarchy and exact tutorial composition remain correct.
- Neither API reads or serves legacy Markdown.
- Both missing-pointer states return HTTP 503 without network or mutation.
- Server startup never creates an initial generation.
- Plain incremental commands never create an initial generation.
- PDF statements are immutable local attachments and are never text-converted or embedded.
- Statement and editorial generations activate and roll back independently.
- Frontend statement and editorial paths bypass Markdown and heading normalization.
- All resources are validated, content-addressed, atomically written, and locally served.
- Full offline test suites, diagnostics, and branch checks pass.
- `v1`, `main`, and `snapshot` satisfy the approved branch model.
- The implementation completes without crawling Codeforces or modifying production data.

## Risks and Mitigations

### Statement format diversity

Mitigation: structural class mapping, custom-section fallback nodes, localized-label independence, representative fixtures, bounded recovery, and fail-closed validation.

### Large initial statement rebuild

Mitigation: independent resumable generations, immutable ready entries, bounded batches, explicit operator control, and no activation until complete.

### One content type blocks the other

Mitigation: independent statement and editorial roots, manifests, pointers, locks, statuses, and rollback.

### PDF content is not semantically parsed

Mitigation: preserve authoritative bytes exactly as an immutable attachment, label the source kind, and avoid misleading text extraction.

### V2-only cutover temporarily makes content unavailable

Mitigation: explicit HTTP 503 states, clear operator instructions, no false `known_absent` responses, branch-level v1 preservation, and independent later rebuild activation.

### Existing snapshot contains legacy files

Mitigation: runtime code never reads them; they remain inert until an explicit post-activation cleanup decision.
