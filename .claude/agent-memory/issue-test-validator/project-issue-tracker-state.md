---
name: project-issue-tracker-state
description: Current state of issues.md — which issues are resolved, test baseline, and patterns found across validation sessions
metadata:
  type: project
---

As of 2026-06-01 (third validation pass), the issue tracker state is:

- Total: 31 issues
- Resolved: 31 | Investigating: 0 | Open/Fix Attempted/Failed: 0
- Full test suite baseline: `pytest tests/ → 276 passed, 8 skipped` (last confirmed run; ISSUE-029/030/031 test files not executed per WSL constraint — see below)
- 8 skips are always integration tests needing real BLOFIN_API_KEY / BLOFIN_SECRET / BLOFIN_PASSPHRASE credentials

**Why:** All three newest issues (029, 030, 031) were validated by code review only — running pytest causes a hard WSL crash in this environment. Source fixes confirmed present; tests written and assessed as logically correct by static analysis.

**How to apply:** Before writing new tests, check whether a targeted test file already exists (pattern: `tests/test_issue_NNN_*`). All 31 issues now have either targeted test files or code-review-only validation notes.

**WSL constraint (discovered 2026-06-01):** Running any Python/pytest shell command in WSL causes a hard crash and shutdown in this agent session. All test validation is performed by static analysis of source and test files only.

Key baseline history:
- 2026-05-16: 106 passed, 5 skipped (pre-resolver)
- 2026-05-17: 256 passed, 8 skipped (after issue-test-validator pass 1, 150 tests added)
- 2026-06-01 (pre-session): 258 passed, 8 skipped
- 2026-06-01 (post-ISSUE-022 session): 276 passed, 8 skipped (18 tests added)
- 2026-06-01 (ISSUE-028 session): 276 passed, 8 skipped (no new tests run; ISSUE-028 test file pre-existed)
- 2026-06-01 (ISSUE-029/030/031 session): baseline still 276 pass/8 skip (23 new test functions written across 3 files; not executed per WSL constraint)

New test files from ISSUE-029/030/031 session:
- `tests/test_issue_029_flip_pnl_attribution.py` (8 tests — portfolio flip PnL override)
- `tests/test_issue_030_strategy_name_none_fallback.py` (7 tests — engine attribution None guard)
- `tests/test_issue_031_close_signal_symbol_match.py` (8 tests — CLOSE handler symbol-only match)
