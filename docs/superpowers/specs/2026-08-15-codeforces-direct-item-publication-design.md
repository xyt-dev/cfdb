# Codeforces Direct Item Publication Design

**Status:** Approved and implemented

**Date:** 2026-08-15

**Supersedes:** `2026-08-15-codeforces-content-ir-v2-design.md` storage, publication, bootstrap, and API initialization sections. Typed IR, exact identity composition, safe rendering, and immutable asset requirements remain in force.

## Product Rule

Crawled means available. A successfully crawled and validated statement problem or editorial contest is published immediately and independently. There is no dataset-wide publication state.

## Storage

Each content kind has one direct store:

```text
statements/v2/                         editorials/v2/
├── documents/<problemCode>.json       ├── documents/<contestId>.json
├── assets/<sha256>.<ext>              ├── assets/<sha256>.<ext>
└── status/<problemCode>.json          └── status/<contestId>.json
```

Document existence is the only visibility source of truth. Status sidecars record `known_absent`, `transient_failure`, or `invalid_structure`; they never hide a valid document. One temporary `crawl.lock` excludes competing writers for the same root.

## Atomic Publication

1. Fetch source content.
2. Parse and validate typed canonical IR.
3. Fetch and validate content-addressed local assets.
4. Write canonical JSON to a temporary file in the document directory.
5. Flush the file, atomically replace the stable document path, and fsync the directory.
6. Remove any previous failed-attempt sidecar.

Readers therefore see either the previous complete document or the replacement complete document, never a partial write. A transient or invalid refresh keeps the previous valid document readable. Confirmed absence removes only that item.

## Crawling

- Plain statement/editorial commands initialize empty direct stores and crawl missing or failed items.
- `--rebuild` force-attempts every metadata item but still publishes successful items one at a time.
- Statement identities come from exact `contestId` and `index` metadata; concatenated codes are never split heuristically, including numeric indices such as `921/01`.
- Editorial contests use bounded concurrency and publish futures in completion order.
- Server startup refreshes metadata and launches statement/editorial crawlers concurrently.
- Progress callbacks update `/api/progress` after each item is persisted.
- A crawler interruption leaves all previously published items readable and restartable.
- Unreferenced content-addressed assets are collected after a crawl.

## API

GET requests are read-only, network-free, and mutation-free.

- Existing valid document: HTTP 200, `status: "ready"`.
- Confirmed absence: HTTP 200, `status: "known_absent"`.
- Not yet crawled: HTTP 202, `status: "pending"`.
- Invalid request identity: HTTP 400, `status: "invalid_ref"`.
- Invalid stored document: HTTP 500, `status: "invalid_structure"`.

The frontend treats `pending` and transient failures as retryable and reloads the currently open item automatically. There is no global initialization error and no request-time crawl.

## Preserved Invariants

- Canonical content is typed JSON, never Markdown or raw HTML.
- Tutorial composition matches only exact full `problemCode`.
- PDF-only statements preserve original bytes as local SHA-256 attachments.
- Raster assets and PDF attachments obey kind-specific lowercase canonical routes, MIME, magic-byte, digest, and basename validation.
- Rendering is deterministic and allowlisted.
- The iframe sandbox remains exactly `allow-scripts allow-popups allow-popups-to-escape-sandbox` without `allow-same-origin`.
- Statement/editorial failures and locks remain independent.
- Legacy Markdown, legacy image routes, failure memories, and stale publication data are not runtime inputs.

## Acceptance Tests

- The first completed item is readable while a later crawl is blocked.
- Empty stores bootstrap through plain commands and server startup.
- Numeric problem indices use exact metadata identities.
- Failed items retry without hiding valid existing documents.
- Assets survive incremental crawls and are digest-validated on reads.
- GET requests do not create or modify files.
- Production source contains no generation store, publication pointer, global initialization status, or legacy fallback symbols.
