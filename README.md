# <img src="vendor/cf-favicon.png" width="28" height="28" align="center" alt="Codeforces"> Codeforces Database (cfdb)

**English** · [中文](README.zh-CN.md)

![](vendor/screenshot.png)

A fully-local Codeforces problem database with a built-in web UI. Browse **11k+ problems**, read statements and editorials offline — no CDN, no account, works on your LAN.

## Features

- **Full problem metadata** — 11k+ problems from the Codeforces API (rating 800–3500, tags, solved counts) in `problems.json`.
- **Unchanged statement path** — statements remain Markdown in `statements/`, with localized images and local MathJax rendering.
- **Structured editorial v2** — editorials are parsed into a typed semantic tree, stored as canonical JSON, and rendered as sanitized HTML. Source order, heading levels, nested spoilers/lists/quotes/tables, code whitespace, images, and TeX retain semantic parity with Codeforces.
- **Exact dynamic tutorial composition** — each complete `problemTutorial` fragment, including its official heading, replaces only the slot with the exact full Codeforces problem code. Contest 1700 is composed as A title/body through F title/body; no letter-, order-, or text-based fallback is used.
- **Atomic rebuilds** — documents and manifests are written into an inactive generation. A full rebuild activates with one atomic pointer replacement only after every contest is `ready` or `known_absent`; prior generations and v1 Markdown remain available for rollback.
- **Read-only editorial API** — `GET /api/editorial` never crawls Codeforces or mutates cache/failure memory. Before v2 activation it serves legacy Markdown; after activation the v2 manifest is authoritative and ready documents are returned as sanitized HTML.
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

## Update and Editorial Operations

```bash
python3 update.py                              # refresh problems.json metadata
python3 update.py --statements                 # pre-crawl all statement Markdown
python3 update.py --validate-editorial 1700    # live build/render validation; no activation
python3 update.py --editorials                 # legacy crawl before v2; incremental successor after v2
python3 update.py --editorials --rebuild       # full resumable v2 rebuild
```

`--validate-editorial` uses temporary localized assets and does not change the active generation. A full rebuild ignores v1 cache hits and failure memory, writes an inactive generation in bounded batches, resumes a compatible unfinished full generation, and exits nonzero without activation while any contest is nonterminal. After v2 activation, plain `--editorials` seeds a new successor from the active generation, rechecks known absences and new contests, and activates only when complete.

### Status meanings

- `ready` — a validated semantic document exists, with no unresolved tutorial slots or remote image dependencies.
- `known_absent` — two successful, recognized contest-page checks found no editorial/tutorial link; this is rechecked in each new rebuild.
- `transient_failure` — retryable network, rate-limit, CSRF, response, or asset failure; blocks activation.
- `invalid_structure` — parsing, exact problem-code composition, or semantic validation failed; blocks activation.

### Cache layout and rollback

```text
editorials/
├── *.md                              # legacy v1 Markdown, retained
├── images/                           # shared localized editorial assets
└── v2/
    ├── current.json                  # atomic active-generation pointer
    └── generations/<generation-id>/
        ├── manifest.json             # expected contests, statuses, receipts, digests
        └── documents/<contestId>.json # canonical schema-2 semantic trees
```

Rendered HTML is derived from the canonical JSON and returned by `/api/editorial`; it is not cached as source HTML. To roll back when `current.json` names a previous generation, atomically reactivate it:

```bash
python3 - <<'PY'
import json
from editorial_cache import activate_generation
with open("editorials/v2/current.json", encoding="utf-8") as source:
    previous = json.load(source)["previousGenerationId"]
activate_generation("editorials/v2", previous)
PY
```

Do not delete old generations until the replacement has passed verification and the rollback window is closed.

## API and Reader Behavior

For active v2 content, `GET /api/editorial?contestId=1700` returns `format: "html"`, `schema: 2`, sanitized `html`, the Codeforces `url`, `known: true`, and `status: "ready"`. A confirmed absence has a null body and `status: "known_absent"`. Before any v2 pointer exists, the existing `format: "markdown"` response remains available; after activation there is no per-contest fallback to v1.

Statements and legacy editorials continue through the Markdown reader. V2 HTML bypasses Markdown parsing and heading normalization, then uses the same local MathJax, syntax highlighting, copy buttons, image diagnostics, external-link handling, and iframe height synchronization.

## Development Verification

Node.js is optional and used only for the dependency-free reader development tests, not at runtime:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/reader_payload.test.js
python3 -m py_compile editorial_model.py editorial_parser.py editorial_render.py editorial_cache.py editorial_rebuild.py cfcrawl.py server.py update.py
```

## Notes

- If Codeforces temporarily bans your IP, currently active local data remains usable; network updates fail without replacing the active generation.
- Generated data (`problems.json`, statements, editorials, images, and failure memories) belongs to the snapshot/data workflow rather than ordinary code commits.
- See [CHANGELOG.md](CHANGELOG.md) for the full history.
