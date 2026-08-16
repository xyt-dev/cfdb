# <img src="vendor/cf-favicon.png" width="28" height="28" align="center" alt="Codeforces"> Codeforces Database (cfdb)

**English** · [中文](README.zh-CN.md)

![](vendor/screenshot.png)

A fully local Codeforces problem database with a built-in web UI. Browse problems, statements, editorials, and personal solutions without a CDN or account.

## Content Engine v2

Content Engine v2 is cfdb's canonical ingestion and publication layer. It converts current Codeforces statements and editorials into typed, schema-validated JSON IR, then renders deterministic sanitized HTML. Parsing, storage, and presentation remain independent while source order, semantic sections, code whitespace, formulas, tables, spoilers, samples, and attachments are preserved.

Assets are validated and localized under content-addressed names before a document becomes visible. Transparent PNGs are composited onto white before hashing, PDF-only statements retain their original bytes, and each problem or contest is published atomically as soon as it is ready. Serving remains read-only: requests never crawl, mutate storage, or fall back to legacy Markdown.

## Features

- **Canonical content pipeline** — statements and editorials share a typed IR, strict validation, and deterministic rendering.
- **Exact editorial composition** — tutorial fragments bind only to the complete `problemCode`; ambiguous positional or title-based fallbacks are rejected.
- **Progressive publication** — validated items become available independently, with no dataset-wide activation gate or restart requirement.
- **Safe local assets** — raster images and PDF attachments are verified, content-addressed, and served from kind-specific routes.
- **Hardened reader** — allowlist rendering, sandboxed content frames, offline MathJax/highlighting, themes, and copy controls.
- **Operationally read-only APIs** — GET requests perform no network access or filesystem writes; background crawlers own refresh and publication.

## Quick Start

```bash
git clone https://github.com/xyt-dev/cfdb.git
cd cfdb
python3 server.py
```

Open <http://localhost:8765>. The server prints its LAN address and starts both missing-content crawlers in the background. Each completed item becomes available immediately.

Runtime dependencies are Python 3.10+ and `curl`. No pip, npm, Node.js runtime, `pdftotext`, or CDN is required. Browser assets are vendored under `vendor/`.

Override the port with `CFDB_PORT=9000 python3 server.py`.

## Content Operations

```bash
python3 update.py                              # refresh problems.json metadata
python3 update.py --statements                 # crawl missing/failed statements
python3 update.py --editorials                 # crawl missing/failed editorials
python3 update.py --statements --rebuild       # force-refresh every statement
python3 update.py --editorials --rebuild       # force-refresh every editorial contest
python3 update.py --validate-statement 1700A  # validate one item in a temporary store
python3 update.py --validate-editorial 1700    # validate one item in a temporary store
```

Plain update commands also initialize empty stores. `--rebuild` means “attempt every metadata item,” not “wait for a whole-dataset release”: each successful replacement is published independently. During a refresh, an existing valid document remains readable until its replacement succeeds. Confirmed absence removes that individual stale document.

The web UI **Rebuild** action explicitly retries the current skipped set: its confirmation dialog shows separate scrollable Statements and Editorials tabs with counts and clickable local navigation. Confirming puts the complete preview snapshot back into the corresponding in-memory queues, so the displayed lists clear; ready and confirmed-absent items remain untouched, and an already-running startup crawl is joined instead of duplicated. CLI `--rebuild` remains the explicit force-refresh command.

Opening retryable unavailable content in the reader sends a separate strict `POST /api/prioritize` hint; statement and editorial GET APIs remain read-only. The latest click moves to the front of its in-memory content queue: statements are selected before the next fetch, while editorials are selected when the next eight-item batch is formed. Ready and confirmed-absent content sends no hint.

### Item statuses

- `ready` — a validated canonical document exists and every referenced asset is local and digest-valid.
- `pending` — the individual item has not been crawled yet; the API returns HTTP 202 and the reader retries automatically.
- `known_absent` — source-specific checks confirmed that the individual content item is unavailable.
- `transient_failure` — retryable network, rate-limit, response, or asset failure; the next update retries it.
- `invalid_structure` — parsing, exact identity composition, canonical validation, or local asset validation failed.

## Store Layout

```text
statements/v2/                         editorials/v2/
├── documents/<problemCode>.json       ├── documents/<contestId>.json
├── assets/<sha256>.<ext>              ├── assets/<sha256>.<ext>
└── status/<problemCode>.json          └── status/<contestId>.json
```

Document existence is the visibility source of truth. Assets are written first; canonical document JSON is fsynced and atomically renamed last. Status sidecars record absence or failed attempts but never gate a valid document. After crawling, unreferenced content-addressed assets are removed. A temporary `crawl.lock` only excludes competing writers for the same root.

## API and Reader Behavior

- `GET /api/statement?contestId=1700&index=A`
- `GET /api/editorial?contestId=1700`
- `GET /statement-assets/<sha256>.<ext>`
- `GET /editorial-assets/<sha256>.<ext>`
- `GET /api/progress`

A ready response contains `format: "html"`, `contentKind`, `schema: 2`, deterministic sanitized `html`, the exact Codeforces `url`, and `status: "ready"`. Confirmed absence has a null body and `status: "known_absent"`. Not-yet-crawled content has `status: "pending"`; there is no global initialization error and no Markdown fallback.

The already-running server sees each atomically published document immediately. PDF-only statements link to immutable local attachments in a new browsing context.

## Development Verification

Node.js is optional and used only for dependency-free reader tests:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/reader_payload.test.js
python3 -m py_compile content_model.py content_parser.py content_render.py content_cache.py content_codecs.py crawl_priority.py statement_model.py statement_parser.py statement_crawl.py statement_rebuild.py editorial_model.py editorial_parser.py editorial_rebuild.py cfcrawl.py server.py update.py
```

## Notes

- A temporary Codeforces/network failure does not hide an already published valid item; failed items remain retryable.
- Generated direct-store documents and assets belong to the data workflow. `problems.json` and `solutions/` remain independent.
- See [CHANGELOG.md](CHANGELOG.md) for project history.
