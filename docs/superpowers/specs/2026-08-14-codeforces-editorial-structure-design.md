# Codeforces Editorial Structure-Preservation Design

**Status:** Approved — amended for v2-only cutover on 2026-08-15

**Date:** 2026-08-14

## Summary

cfdb will replace its editorial-only HTML-to-Markdown pipeline with a typed semantic tree that is composed before rendering. Dynamic Codeforces tutorials will be inserted into exact `problemTutorial[problemcode]` slots as parsed subtrees, then rendered as strictly sanitized HTML. Problem statements remain on the current Markdown pipeline.

The migration creates versioned v2 editorial generations and makes the typed IR path the only editorial runtime. A full source recrawl remains an explicit operator action; it builds an inactive generation and activates it atomically only after all contests have a terminal valid status.

## User Decisions

- Fidelity means semantic parity with Codeforces: preserve source order, heading levels, spoilers, nesting, lists, quotes, tables, code, images, and formulas while retaining cfdb's local visual theme.
- The implementation uses a typed intermediate representation and sanitized HTML, not Markdown as the canonical editorial format.
- Existing editorials are rebuilt through a full recrawl.
- Contest 1700 is the first required live regression check before the full recrawl.
- The project remains Python-standard-library-only at runtime and in its automated test suite.
- The original `origin/main` revision is preserved as the `v1` branch.
- `main` is v2-only code, while `snapshot` contains the same code plus generated data.
- No server startup, API request, or plain incremental command may trigger an initial full recrawl automatically.
- Until an explicit full rebuild activates the first generation, editorial API requests fail with HTTP 503 instead of falling back to v1 Markdown.

## Problem Statement

The current pipeline loses parent-child relationships before dynamic tutorial content is composed:

1. `html2md.py` converts HTML through global streaming state rather than a node stack.
2. `editorial_to_md` extracts the first `.ttypography` with a regular expression.
3. `_normalize_problem_headers` infers problem headings from rendered text and promotes them to `h2`.
4. `cfcrawl.py` fetches dynamic tutorial fragments, strips their outer heading, and inserts their Markdown with text regular expressions and order-based fallbacks.
5. `index.html` applies a second, independent heading-normalization pass after Markdown rendering.

Once the source has been flattened, the code cannot reliably determine which title, spoiler, body, list, or code block belongs to which problem.

### Reproduced contest 1700 failure

The official base blog entry `103978` contains six `problemTutorial` slots. Each `/data/problemTutorial` response contains an outer `h3` problem title and one `.ttypography` body.

The current base conversion promotes six author-credit links into headings. Tutorial replacement then treats the API headings as duplicates and removes them. The cached result in `editorials/1700.md` has all six titles at lines 4-14, while the bodies start at line 16 and are concatenated without title/body boundaries.

### Additional reproduced failures

- Contest 1369 represents each problem with both an official `h4` title and a generated `h2` title, with flattened spoiler titles between them.
- Mixed official heading levels, such as contest 1706, are interpreted as anomalies even when they reflect the source hierarchy.
- `_clean` recognizes only a bare closing/opening ````` fence, not language-tagged openings such as `````cpp`; this can clean prose and code under the wrong fence state.
- Existing v1 caches are permanent cache hits, so parser improvements do not repair already-generated editorials.

## Goals

1. Preserve editorial block hierarchy and source order.
2. Keep each dynamic problem title attached to its exact tutorial body.
3. Preserve nested spoilers, lists, quotes, tables, code blocks, images, and TeX.
4. Remove text-based heading inference from the v2 editorial path.
5. Produce deterministic, sanitized HTML suitable for offline rendering.
6. Keep statements on Markdown while removing legacy editorials from the active runtime.
7. Make full recrawl activation atomic and reversible.
8. Distinguish permanent absence, transient failure, and invalid structure.
9. Verify the parser with deterministic offline fixtures before network validation.

## Non-Goals

- Pixel-perfect reproduction of Codeforces styling.
- Migrating problem statements away from Markdown.
- Executing Codeforces scripts or embedded widgets.
- Preserving arbitrary source CSS, classes, IDs, inline styles, forms, or interactive embeds.
- Importing v1 Markdown into the v2 semantic format.
- Adding third-party Python, npm, or browser dependencies.

## Architecture

### 1. Typed semantic model

The canonical document is a JSON-serializable typed tree. Nodes use explicit kinds rather than arbitrary HTML tags.

Required block node kinds:

- `document`
- `container`
- `problem_section`
- `heading`
- `paragraph`
- `list`
- `list_item`
- `blockquote`
- `code_block`
- `table`
- `table_row`
- `table_cell`
- `spoiler`
- `tutorial_slot`
- `image`
- `horizontal_rule`
- `line_break`
- `missing_asset`

Required inline node kinds:

- `text`
- `strong`
- `emphasis`
- `inline_code`
- `link`
- `subscript`
- `superscript`

Node-specific fields are bounded and typed:

- `heading.level` is an integer from 1 through 6.
- `problem_section.problemCode` contains the exact Codeforces code, such as `1700A`.
- `tutorial_slot.problemCode` contains the exact slot identity from the source attribute.
- `list.ordered` is a boolean.
- `code_block.language` is an optional detected language; code text is preserved byte-for-byte after newline normalization.
- `link.href` and `image.src` are normalized URLs before sanitization.
- `spoiler.title` is an inline-node list and `spoiler.children` is a block-node list.
- `text.value` contains decoded Unicode text.

Arbitrary source attributes are not copied into the IR.

### 2. Tolerant HTML tree builder

A standard-library `HTMLParser` implementation builds the semantic tree through an explicit node stack.

The parser must:

- Recognize void elements without waiting for closing tags.
- Auto-close optional elements such as `p`, `li`, `tr`, `th`, and `td` when incompatible siblings begin.
- Recover from mismatched closing tags by closing only to a compatible open ancestor.
- Enforce maximum input bytes, nesting depth, node count, attribute count, and text-node size.
- Record recoveries and dropped constructs in diagnostics.
- Stop a malformed editorial content island when a new top-level `.ttypography` island begins, preventing comments from being absorbed into the editorial.
- Reject a page when no credible editorial content island exists or recovery exceeds configured safety limits.

Dangerous subtrees are skipped with their descendants:

- `script`
- `style`
- `iframe`
- `object`
- `embed`
- `form`
- form controls

Unknown non-dangerous block elements become `container` nodes. Unknown inline elements retain their safe children. Text is never discarded merely because its wrapper is unknown.

### 3. Codeforces semantic mapping

Known Codeforces structures are mapped before generic elements:

- `.ttypography` selects an editorial content island.
- `.problemTutorial[problemcode]` becomes a `tutorial_slot`.
- `.spoiler` becomes one `spoiler` node.
- `.spoiler-title` supplies the spoiler title.
- `.spoiler-content` supplies spoiler children.
- `.problem-statement` in tutorial API fragments becomes a transparent content container.
- `pre > code` becomes one `code_block` without whitespace cleanup.
- `h1` through `h6` preserve their exact source level.
- `ol` and `ul` preserve ordered versus unordered semantics and nesting.

Text that merely resembles a problem code is not promoted to a heading. Author-credit links therefore remain paragraph content.

### 4. Dynamic tutorial composition

The base blog is parsed first. Every tutorial placeholder is retained as a slot keyed by its exact `problemcode`.

For each slot:

1. Fetch `/data/problemTutorial` using the current session and CSRF flow.
2. Parse the complete returned fragment, including its outer `h3`.
3. Validate that the heading link resolves to the exact expected contest and problem index.
4. Build a `problem_section` containing that heading and tutorial body.
5. Replace only the matching slot node.

There is no first-letter fallback, first-placeholder fallback, global title deduplication, or Markdown regular-expression replacement.

Response handling is explicit:

- `success == true` with valid HTML replaces the slot.
- `success == false` removes the slot and records confirmed tutorial absence for that problem.
- Network, JSON, CSRF, validation, or parse failures abort the contest cache transaction as `transient_failure` or `invalid_structure`.
- Any unresolved slot prevents the document from becoming `ready`.

For contest 1700, the output order is A title, A body, B title, B body, through F.

### 5. Text, TeX, and code normalization

Normalization occurs on typed nodes rather than raw Markdown:

- Consecutive HTML whitespace is normalized only in ordinary text nodes.
- Code block and inline-code text is never whitespace-compressed.
- Code language detection reads code without modifying it.
- Codeforces `$$$...$$$` delimiters are normalized only in non-code text nodes for local MathJax compatibility.
- Subscript and superscript remain typed nodes.
- Markdown-sensitive characters do not need a protection pass because v2 bypasses Markdown.

### 6. Asset localization

Image nodes are localized before a document becomes ready:

- HTTP and HTTPS source URLs are normalized and downloaded to the shared local editorial image directory.
- The node source is rewritten to the `/eimages/` route only after a successful atomic asset write.
- A transient image fetch failure keeps the contest in `transient_failure` for retry.
- A confirmed missing or unsupported asset becomes `missing_asset` with a diagnostic rather than a remote reference.
- SVG is rejected in v2 and becomes `missing_asset` with a diagnostic. Enabling SVG requires a separate reviewed design and is outside this migration.

No ready v2 document depends on a remote image.

### 7. Sanitized HTML renderer

A deterministic renderer converts the semantic tree to HTML.

The renderer:

- Escapes all text and attribute values.
- Emits only a fixed allowlist of structural tags.
- Emits no source inline styles, IDs, event attributes, or arbitrary classes.
- Allows only `http`, `https`, and approved local URL forms for links.
- Allows only approved local `/eimages/` URLs for images in ready documents.
- Adds safe `target` and `rel` values to external links.
- Renders spoilers as `<details class="cf-spoiler"><summary>...</summary>...</details>`.
- Renders `problem_section` as a section containing the original API heading level and body.
- Emits language classes only from a bounded language-name allowlist.
- Produces byte-identical HTML for byte-identical canonical JSON.

The renderer must never pass source HTML through unchanged.

## Branch and Deployment Model

- `v1` permanently identifies the original code-only `origin/main` revision before the IR migration.
- `main` contains only v2 application code, tests, fixtures, and documentation; production crawl data remains ignored.
- `snapshot` is a descendant of `main` and contains the same code plus the complete generated-data snapshot.
- V1 is not a runtime fallback. Returning to v1 requires an explicit branch-level deployment rollback.
- V2 runtime rollback switches only between previously validated v2 generation pointers.

## Cache and Generation Model

### Directory layout

```text
editorials/
├── images/                      # shared localized assets
└── v2/
    ├── current.json             # atomic active-generation pointer
    └── generations/
        └── <generation-id>/
            ├── manifest.json
            └── documents/
                └── <contestId>.json
```

### Document schema

Each document contains:

```json
{
  "schema": 2,
  "contestId": "1700",
  "sourceUrl": "https://codeforces.com/blog/entry/103978",
  "document": {
    "kind": "document",
    "children": []
  },
  "diagnostics": [],
  "assets": []
}
```

The semantic tree is canonical. Rendered HTML is derived and is not duplicated in the cache.

### Manifest schema

The generation manifest contains:

- schema version
- generation ID
- creation timestamp
- parser version
- expected contest IDs
- per-contest status and document path
- aggregate counts
- fixture-suite version
- live-validation receipts for required contests

Allowed per-contest statuses:

- `ready`
- `known_absent`
- `transient_failure`
- `invalid_structure`

Only `ready` and `known_absent` are activation-terminal.

### Atomic writes

Every document, manifest, and pointer write uses a temporary file in the destination directory, flushes and fsyncs it, then calls `os.replace`. Generation activation atomically replaces only `editorials/v2/current.json` after the manifest is complete and validated.

An interprocess lock prevents two rebuilds or activations from running concurrently. The lock contains PID and start-time metadata and supports conservative stale-lock recovery.

## Crawler and CLI Behavior

`update.py` exposes validation, incremental v2 update, and explicit full-rebuild commands; none dispatches to the legacy editorial crawler.

Required commands:

```text
python3 update.py --validate-editorial 1700
python3 update.py --editorials
python3 update.py --editorials --rebuild
```

### `--editorials`

- Requires an existing, valid `editorials/v2/current.json` pointer.
- Builds and activates an incremental successor generation.
- If no active pointer exists, exits nonzero with instructions to run the explicit full-rebuild command.
- Never invokes the legacy Markdown crawler and never upgrades a missing generation into an implicit full recrawl.

### `--validate-editorial 1700`

- Fetches the base blog and every required tutorial fragment.
- Parses, composes, localizes, validates, and renders without changing the active generation.
- Checks the contest-1700 structural contract.
- Writes only an explicitly named temporary diagnostic artifact, or no artifact when not requested.
- Exits nonzero on unresolved slots, detached problem bodies, duplicate problem sections, unsafe output, or transient failures.

### `--editorials --rebuild`

- Ignores v1 Markdown files and v1 failure memories.
- Creates a new inactive v2 generation.
- Uses bounded batch concurrency and rate limiting.
- Writes each successful contest atomically.
- Retries transient failures without redoing ready documents in the same generation.
- Refuses activation while any contest remains `transient_failure` or `invalid_structure`.
- Activates the complete generation only after validation gates pass.
- Leaves the previously active v2 generation untouched for pointer rollback.
- Is the only command permitted to create the first complete generation; it is never invoked automatically by the server.

The existing metadata and statement commands retain their behavior.

## Server Contract

`/api/editorial` remains the public route and becomes read-only.

For v2 content it returns:

```json
{
  "format": "html",
  "schema": 2,
  "html": "<section>...</section>",
  "url": "https://codeforces.com/blog/entry/103978",
  "known": true,
  "status": "ready"
}
```

For a known-absent editorial it returns a null body with `status: "known_absent"`.

Before any v2 generation is active, the route returns HTTP 503:

```json
{
  "format": null,
  "status": "v2_not_initialized",
  "error": "editorial v2 is not initialized"
}
```

After activation, the active manifest is authoritative: `ready` returns v2 HTML and `known_absent` returns a null body. There is no per-contest or global fallback to v1 Markdown, and v1 content is never imported or promoted into v2.

API GET requests do not fetch Codeforces or mutate failure memory. When the server background updater finds no active pointer, it records the uninitialized state and performs no editorial crawl. Only explicit CLI commands own network crawling, and only explicit `--rebuild` may create the first generation.

## Frontend Contract

The frontend keeps content-type-specific rendering paths:

- Statements continue to use the Markdown frame.
- Editorials accept only the structured-HTML payload path.
- `v2_not_initialized` is rendered as an explicit service-unavailable message and is never normalized into a missing editorial.

The v2 path:

- Does not call `normalizeProblemHeaders`.
- Applies local CSS to semantic tags without changing their nesting or heading levels.
- Styles `.cf-spoiler` details and summary elements.
- Runs vendored MathJax and syntax highlighting after insertion.
- Keeps copy buttons, image diagnostics, height synchronization, and external links.
- Sets `sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"`; `allow-same-origin` is forbidden. Local asset delivery must work under this sandbox, and a failure is a release blocker rather than a reason to relax it.

The server-rendered HTML remains sanitized even though the iframe is sandboxed. The sandbox is defense in depth, not the primary sanitizer.

## Failure and Recovery Policy

### `v2_not_initialized`

Used only when `editorials/v2/current.json` does not exist. The API returns HTTP 503, performs no network or cache mutation, ignores any legacy Markdown files, and instructs the operator to run the explicit full rebuild. This status is not a contest absence and is never cached in a generation.

### `known_absent`

`known_absent` is a contest-level, generation-scoped observation; every new rebuild checks it again. It is recorded only when all of these conditions hold:

1. The contest exists in the current problem metadata.
2. Two successfully fetched, recognized contest pages contain neither an editorial nor tutorial link under structural link discovery.
3. Neither response is a 403, interstitial, announcement substitute, malformed page, or parser-recovery failure.
4. The two checks are separated by the normal batch delay and produce the same result.

An API `success == false` for one tutorial slot is recorded as a per-problem diagnostic inside an otherwise `ready` contest document; it does not make the entire contest `known_absent`. Generic network failures, blocked pages, malformed responses, and missing selectors never produce `known_absent`.

### `transient_failure`

Used for timeouts, rate limits, 403/interstitial pages, CSRF failures, invalid JSON, temporary image failures, and unresolved tutorial slots. These entries are retryable and do not activate.

### `invalid_structure`

Used when source HTML exceeds safety limits, no credible editorial island is found, exact problem-code validation fails, parser recovery exceeds its threshold, or the composed tree violates invariants.

### Rollback

Runtime rollback atomically restores a prior validated v2 `current.json` pointer. V1 is preserved only as the `v1` Git branch and is not consulted by the v2 runtime.

## Security Requirements

1. No source script, style, iframe, form, object, embed, event attribute, or arbitrary inline style reaches rendered output.
2. No `javascript:`, `data:`, `file:`, or protocol-relative unsafe URL reaches a link or image.
3. Source text containing `</script>` is escaped before insertion into `srcdoc`.
4. The renderer is allowlist-based; unsupported nodes preserve safe text or become diagnostics.
5. Parser depth, node, text, and input-size limits prevent resource exhaustion.
6. Tests cover entity-encoded attacks, malformed attributes, hostile URLs, nested unsafe elements, and SVG payloads.
7. The iframe remains sandboxed with minimum permissions.

## Testing Strategy

The test suite uses `unittest` and immutable, checked-in fixtures. Tests never depend on mutable root-level caches or live network access.

### Required fixture corpus

```text
tests/fixtures/editorials/
├── 1700/
│   ├── base.html
│   ├── tutorial-A.html
│   ├── tutorial-B.html
│   ├── tutorial-C.html
│   ├── tutorial-D.html
│   ├── tutorial-E.html
│   ├── tutorial-F.html
│   └── expected.json
├── 1369/
│   ├── base.html
│   ├── tutorials/
│   └── expected.json
├── 1706/
│   ├── base.html
│   └── expected.json
└── synthetic/
    ├── malformed.html
    ├── nested-structure.html
    ├── code-and-math.html
    ├── unsafe.html
    └── expected-*.json
```

Fixtures are trimmed to the smallest source fragments that preserve the observed structure. Each fixture is manually audited, normalized to LF, and checksum-pinned in a fixture manifest.

### Parser invariants

- Source order is preserved.
- Parent-child relationships are preserved.
- Heading levels remain unchanged.
- Ordered and unordered list identity and nesting remain unchanged.
- Spoiler title and body remain one node.
- Code whitespace remains unchanged.
- Unknown safe wrappers do not lose text.
- Dangerous wrappers and descendants are removed with diagnostics.
- Malformed input recovery is deterministic and bounded.

### Composition invariants

- Every successful tutorial fragment replaces exactly one slot with the same exact problem code.
- No slot is replaced by first letter or ordinal position.
- No unresolved slot remains in a ready document.
- Contest 1700 orders each title immediately before its own body sentinel.
- Author-credit links do not become problem-section headings.
- Contest 1369 has no duplicate semantic problem boundary.

### Renderer and sanitizer invariants

- Canonical JSON renders byte-identically across repeated runs.
- All text and attributes are escaped.
- Unsafe tags, attributes, and schemes are absent.
- Spoilers render as nested details/summary structures.
- TeX remains available to MathJax.
- Code blocks retain whitespace and a bounded language class.
- Ready documents contain no remote image dependency.

### Cache and API invariants

- Partial document writes are never visible.
- Incomplete generations never become current.
- Pointer rollback restores the previous generation.
- GET requests perform no network or cache mutation.
- Editorial responses select only the structured HTML path.
- A missing active generation returns HTTP 503 `v2_not_initialized` without reading legacy files or crawling.
- Statements continue to select their independent Markdown path.

### Live validation gates

1. Run the complete offline test suite.
2. Run `python3 update.py --validate-editorial 1700`.
3. Confirm contest 1700 title/body adjacency for A through F.
4. Spot-check contests 1369 and 1706 against their official HTML structures.
5. Start the local server and verify spoilers, nested lists, code, images, links, MathJax, and iframe resizing.
6. Run the full editorial rebuild.
7. Activate only with zero transient and invalid entries.

## Rollout Sequence

1. Add failing contest-1700 fixtures and assertions.
2. Implement the typed parser and make parser tests pass.
3. Add exact tutorial composition and make contest-1700 composition pass.
4. Implement sanitization and structured HTML rendering.
5. Add versioned cache and atomic generation writes.
6. Add read-only v2 server responses and frontend HTML rendering.
7. Add the CLI validation and full-rebuild commands.
8. Pass offline, security, cache, API, and frontend checks.
9. Pass live contest-1700 validation.
10. Pass live 1369 and 1706 spot checks.
11. Create `v1` at the original `origin/main` revision.
12. Replace `main` with v2-only code and merge that code into `snapshot` without production data entering `main`.
13. Remove all legacy editorial runtime routing and update compatibility tests.
14. Verify HTTP 503 and zero crawler calls when no generation is active.
15. Stop without rebuilding; an operator later runs and activates the explicit full rebuild.
16. Retain previous validated v2 generations for pointer rollback.

## Acceptance Criteria

The work is complete only when all of the following are true:

- Contest 1700 renders A title with A body, then B title with B body, through F.
- Contest 1369 preserves official problem headings and nested spoiler boundaries without generated duplicates.
- Contest 1706 preserves its mixed official heading levels.
- Dynamic tutorial composition uses exact problem-code slots only.
- Editorial v2 has no text-based heading promotion or frontend heading rewrite.
- Spoilers, nested lists, tables, quotes, code, images, links, and TeX preserve semantic structure.
- Sanitizer and resource-limit tests pass.
- All cache and activation writes are atomic.
- `/api/editorial` performs no network mutation.
- Without an active generation, `/api/editorial` returns HTTP 503 `v2_not_initialized` and never reads v1 Markdown.
- Server startup performs no automatic initial editorial rebuild.
- Plain `--editorials` fails without a pointer; only explicit `--editorials --rebuild` may create the first generation.
- The full rebuild finishes with no transient or invalid entries before activation.
- The previous validated v2 generation remains available for rollback.
- `v1` preserves the original main revision, `main` contains no production data, and `snapshot` remains a full descendant of `main`.
- README, Chinese README, CLI help, and CHANGELOG describe the implemented behavior exactly.

## Risks and Mitigations

### Standard-library parser is not a full HTML5 parser

Mitigation: bounded recovery rules, real malformed fixtures, diagnostics, and refusal to cache documents that exceed recovery thresholds.

### Strict sanitization may remove rare widgets

Mitigation: preserve safe text, emit diagnostics, include representative tables/code/spoilers, and prefer explicit node support over raw HTML passthrough.

### Full recrawl may be interrupted or rate-limited

Mitigation: inactive generation, per-document atomic writes, resumable ready entries, typed transient status, batching, and no activation until complete.

### Migration can change frontend behavior

Mitigation: explicit HTTP 503 before initialization, sandboxed rendering, live spot checks, atomic v2 generation rollback, and branch-level preservation of the original implementation as `v1`.

### Generated data is currently changing in the working tree

Mitigation: implementation edits only source, tests, and documentation; existing generated data is never overwritten or committed during parser development. Live validation writes to an inactive generation or temporary location until the user starts the approved rebuild.
