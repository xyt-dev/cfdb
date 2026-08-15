# <img src="vendor/cf-favicon.png" width="28" height="28" align="center" alt="Codeforces"> Codeforces Database (cfdb)

**English** · [中文](README.zh-CN.md)

![](vendor/screenshot.png)

A fully-local Codeforces problem database with a built-in web UI. Browse **11k+ problems**, read statements and editorials offline — no CDN, no account, works on your LAN.

## Features

- **Full problem metadata** — 11k+ problems from the Codeforces API (rating 800–3500, tags, solved counts) in `problems.json`.
- **Structured statement v2** — current statement DOM or authoritative PDFs become typed canonical JSON; HTML statements render semantically, while PDFs remain immutable local attachments.
- **Structured editorial v2** — editorials become typed canonical JSON and deterministic sanitized HTML while preserving source order, heading levels, nested spoilers/lists/quotes/tables, code whitespace, images, and TeX.
- **Exact dynamic tutorial composition** — each complete `problemTutorial` fragment, including its official heading, replaces only the slot with the exact full Codeforces problem code. Contest 1700 is composed as A title/body through F title/body; no letter-, order-, or text-based fallback is used.
- **Independent atomic generations** — statements and editorials have separate manifests, pointers, rebuilds, activation, rollback, and history; only complete validated generations activate.
- **Read-only v2 APIs** — statement and editorial GET requests are network-free and mutation-free; missing pointers return HTTP 503, and runtime never reads or serves legacy Markdown.
- **Local web UI** (`index.html`, single file) — filtering, sorting, statement/editorial tabs, local solutions, English/Chinese UI, two themes, offline MathJax/highlighting, and copy buttons.
- **Defense in depth** — source HTML is allowlist-parsed and rendered; the structured reader uses `sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"` without `allow-same-origin`.
- **Zero CDN and LAN ready** — vendored browser assets, default bind to `0.0.0.0`, and no authentication.

## Quick Start

```bash
# Code only (no data) — data lives on the `snapshot` branch
git clone https://github.com/xyt-dev/cfdb.git
cd cfdb
python3 server.py          # start server and background incremental updates

# With the full data snapshot:
git clone -b snapshot https://github.com/xyt-dev/cfdb.git
```

Then open <http://localhost:8765> (the LAN address is printed at startup).

**Runtime dependencies:** Python 3.10+, `curl`, and `poppler-utils` (`pdftotext`, only for PDF statements). No pip, npm, or Node.js runtime dependency is required; all browser assets are in `vendor/`.

Port override: `CFDB_PORT=9000 python3 server.py`

## Update and Content Operations

```bash
python3 update.py                              # refresh problems.json metadata
python3 update.py --validate-statement 1700A  # validate one statement; no activation
python3 update.py --validate-editorial 1700    # validate one editorial; no activation
python3 update.py --statements --rebuild       # explicit full statement generation
python3 update.py --editorials --rebuild       # explicit full editorial generation
python3 update.py --statements                 # incremental statement successor
python3 update.py --editorials                 # incremental editorial successor
```

Only an explicit `--rebuild` may create the first generation. A plain statement or editorial update requires its own active `current.json` pointer and exits nonzero when that content root is uninitialized. Server startup follows the same rule: it incrementally updates initialized roots independently and skips missing roots without starting a content crawl.

Both validation modes use temporary localized assets and never activate a generation. Full rebuilds write bounded inactive generations and activate only complete, validated results. Incremental commands seed successors from their respective active generations; statements and editorials activate, roll back, and retain history independently.

### Status meanings

- `ready` — a validated semantic document exists and all resources are local.
- `known_absent` — source-specific authoritative checks confirmed that content is unavailable.
- `transient_failure` — a retryable network, rate-limit, response, or asset failure blocks activation.
- `invalid_structure` — parsing, exact identity composition, or semantic validation failed and blocks activation.

### Cache layout and rollback

```text
statements/v2/                         editorials/v2/
├── current.json                       ├── current.json
└── generations/<generation-id>/       └── generations/<generation-id>/
    ├── manifest.json                      ├── manifest.json
    ├── documents/<problemCode>.json       ├── documents/<contestId>.json
    └── assets/<sha256>.<ext>               └── assets/<sha256>.<ext>
```

Canonical JSON is the cache source of truth; rendered HTML is derived on read and is never stored as canonical content. Finalized generations and content-addressed assets are immutable. To roll back a root when `current.json` records a previous generation, atomically reactivate it with the generic cache API:

```bash
python3 - <<'PY'
import json
from content_cache import activate_generation
root = "editorials/v2"
with open(f"{root}/current.json", encoding="utf-8") as source:
    previous = json.load(source)["previousGenerationId"]
activate_generation(root, previous)
PY
```

Do not delete old generations until the replacement has passed verification and the rollback window is closed.

## API and Reader Behavior

`GET /api/statement?contestId=1700&index=A` and `GET /api/editorial?contestId=1700` serve only v2 content. A ready response contains `format: "html"`, `contentKind`, `schema: 2`, sanitized `html`, the exact Codeforces `url`, and `status: "ready"`. Confirmed absence returns a null body with `status: "known_absent"`.

If a content root has no active pointer, its endpoint returns HTTP 503 with `status: "v2_not_initialized"`. There is no Markdown, request-time crawl, or per-item legacy fallback. The reader passes server-rendered HTML directly into the sandboxed frame, preserving semantic hierarchy while reusing local MathJax, syntax highlighting, copy controls, image diagnostics, and height synchronization. PDF-only statements link to immutable local attachments in a new browsing context.

## Development Verification

Node.js is optional and used only for dependency-free reader development tests:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/reader_payload.test.js
python3 -m py_compile content_model.py content_parser.py content_render.py content_cache.py content_codecs.py statement_model.py statement_parser.py statement_crawl.py statement_rebuild.py editorial_model.py editorial_parser.py editorial_rebuild.py cfcrawl.py server.py update.py
```

## Notes

- If Codeforces temporarily bans your IP, currently active local data remains usable; network updates fail without replacing the active generation.
- Generated metadata and v2 generations/assets belong to the snapshot/data workflow rather than ordinary code commits. Legacy v1 files may remain in snapshots as inert historical data but are never runtime inputs.
- See [CHANGELOG.md](CHANGELOG.md) for the full history.
