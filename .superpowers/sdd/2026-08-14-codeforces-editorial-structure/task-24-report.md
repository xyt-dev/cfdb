# Task 24 implementation report

## Status

PASS — redundant nested tutorial headings are suppressed only when a tutorial slot is structurally owned by the matching source problem heading. The exact commit subject is `fix: avoid redundant nested tutorial headings`.

## Changed files

- `editorial_parser.py` — added structural source-problem context detection from exact Codeforces problem-link URLs and context-aware nested fragment heading suppression.
- `tests/test_editorial_composer.py` — added nested spoiler regression coverage, a text-only-heading negative case, and an explicit top-level 1700 fragment-heading preservation assertion.
- `.superpowers/sdd/2026-08-14-codeforces-editorial-structure/task-24-report.md` — this implementation and validation report.

No generated data, fixtures, frontend, cache, plans, or unrelated paths were changed, staged, or committed.

## Strict TDD evidence

### Focused RED

Added `EditorialComposerTests.test_nested_tutorial_omits_heading_under_exact_source_problem_context` before changing production code and ran:

```console
python3 -m unittest tests.test_editorial_composer.EditorialComposerTests.test_nested_tutorial_omits_heading_under_exact_source_problem_context -v
```

The test failed as required. The failure showed the composed nested `problem_section` still contained the fragment `heading` as its first child instead of matching `fragment.children[1:]`.

### Focused GREEN

After the parser change, ran the three focused tests:

```console
python3 -m unittest tests.test_editorial_composer.EditorialComposerTests.test_nested_tutorial_omits_heading_under_exact_source_problem_context tests.test_editorial_composer.EditorialComposerTests.test_heading_text_without_exact_problem_link_keeps_fragment_heading tests.test_editorial_composer.EditorialComposerTests.test_composes_contest_1700_in_exact_source_order -v
```

Result: 3 tests passed.

The complete composer module then passed 8 tests.

## Implementation details

- `_source_problem_context` recognizes only a heading with exactly one direct link whose path is `/contest/<contest>/problem/<index>`.
- It accepts relative paths and normalized absolute `http`/`https` Codeforces URLs, rejects other hosts/schemes, and ignores query/fragment variants.
- Composer traversal carries `(problemCode, headingLevel)` through sibling and descendant content. A slot is eligible only when its exact full code equals the active source context.
- For an eligible nested slot, the replacement remains a `problem_section` with the exact `problemCode` and all fragment body children; only the fragment heading is removed.
- Heading-level boundaries clear stale ownership, while source heading nodes and spoiler hierarchy remain untouched.
- Slots without a source context retain the complete fragment, preserving the existing contest 1700 behavior and all prior exact-code, missing, duplicate, image, spoiler, list, code, diagnostic, and serialization paths.

## Validation

The immutable suite was run from a temporary `git archive` of `HEAD`, overlaid only with the changed `editorial_parser.py` and `tests/test_editorial_composer.py`:

- `python3 -m unittest discover -s tests -p 'test_*.py' -v` — PASS, 115 tests.
- `node --test tests/reader_payload.test.js` — PASS, 7 tests.
- `python3 -m py_compile editorial_model.py editorial_parser.py editorial_render.py editorial_cache.py editorial_rebuild.py cfcrawl.py server.py update.py tests/test_editorial_composer.py` — PASS.
- Current-tree `python3 -m py_compile editorial_parser.py tests/test_editorial_composer.py` — PASS.
- Current-tree `git diff --check -- editorial_parser.py tests/test_editorial_composer.py` — PASS.
- Current-tree `node --test tests/reader_payload.test.js` — PASS, 7 tests.

Primary Python LSP diagnostics were not run because no `pyright`, `basedpyright`, `pylsp`, or `pyright-langserver` executable/module is installed in this environment. `py_compile` and the full import-running Python suite provide the available static/runtime validation.

## Scope and staging

Before staging, the index was empty. Path-specific staging is restricted to:

```console
editorial_parser.py
tests/test_editorial_composer.py
.superpowers/sdd/2026-08-14-codeforces-editorial-structure/task-24-report.md
```

The final commit is created with subject `fix: avoid redundant nested tutorial headings`. Post-commit checks verify that the commit contains only those three paths, the staged diff is whitespace-clean, and `git diff --cached --name-only` is empty.

## Residual risks

- No live Codeforces fetch or browser rendering was run; this task is covered by deterministic structural fixtures and the immutable offline suite.
- The checkout contains substantial pre-existing generated-data and working-copy fixture changes; they remain untouched and are excluded from the Task 24 commit.
- The primary LSP executable is unavailable, as noted above; no source-level diagnostic errors were observed through compile or test execution.

## Acceptance report

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Implemented and tested structural exact-link context detection and redundant nested tutorial-heading suppression in editorial_parser.py; composer regressions pass, and the final commit is restricted to the three authorized paths."
    }
  ],
  "changedFiles": [
    "editorial_parser.py",
    "tests/test_editorial_composer.py",
    ".superpowers/sdd/2026-08-14-codeforces-editorial-structure/task-24-report.md"
  ],
  "testsAddedOrUpdated": [
    "tests/test_editorial_composer.py"
  ],
  "commandsRun": [
    {
      "command": "python3 -m unittest tests.test_editorial_composer.EditorialComposerTests.test_nested_tutorial_omits_heading_under_exact_source_problem_context -v",
      "result": "passed",
      "summary": "Expected initial RED was observed before production code; the same focused test passed after the fix."
    },
    {
      "command": "python3 -m unittest tests.test_editorial_composer -v",
      "result": "passed",
      "summary": "All 8 composer tests passed."
    },
    {
      "command": "python3 -m unittest discover -s tests -p 'test_*.py' -v (immutable git archive overlay)",
      "result": "passed",
      "summary": "All 115 Python tests passed."
    },
    {
      "command": "node --test tests/reader_payload.test.js (immutable git archive overlay)",
      "result": "passed",
      "summary": "All 7 Node tests passed."
    },
    {
      "command": "python3 -m py_compile editorial_model.py editorial_parser.py editorial_render.py editorial_cache.py editorial_rebuild.py cfcrawl.py server.py update.py tests/test_editorial_composer.py",
      "result": "passed",
      "summary": "No Python syntax errors."
    },
    {
      "command": "git diff --check -- editorial_parser.py tests/test_editorial_composer.py",
      "result": "passed",
      "summary": "No whitespace errors."
    },
    {
      "command": "primary Python LSP diagnostics on editorial_parser.py and tests/test_editorial_composer.py",
      "result": "not-run",
      "summary": "No supported LSP executable or module is installed in the environment."
    }
  ],
  "validationOutput": [
    "Focused nested-context regression passed after the observed RED.",
    "Text-only headings retain complete fragment headings.",
    "Top-level contest 1700 fragments retain their headings.",
    "Immutable Python suite: 115 passed; immutable Node suite: 7 passed; py_compile passed.",
    "Final commit contains only the three authorized paths and the post-commit index is empty."
  ],
  "residualRisks": [
    "Primary Python LSP unavailable in this environment.",
    "No live network or browser validation was run.",
    "Pre-existing generated-data and fixture working-copy changes remain outside the commit."
  ],
  "noStagedFiles": true,
  "diffSummary": "Composer traversal now tracks exact Codeforces problem-heading ownership and omits only redundant nested fragment headings; regressions cover nested, negative, and top-level behavior.",
  "reviewFindings": [
    "no blockers"
  ],
  "manualNotes": "Protected generated-data paths and unrelated working-copy changes were left untouched."
}
```
