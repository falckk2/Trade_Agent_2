---
name: project-issue-tracker-state
description: Current state of issues.md — which issues are resolved, test baseline, and patterns found across validation sessions
metadata:
  type: project
---

As of 2026-06-01 (second validation pass), the issue tracker state is:

- Total: 28 issues
- Resolved: 28 | Investigating: 0 | Open/Fix Attempted/Failed: 0
- Full test suite baseline: `pytest tests/ → 276 passed, 8 skipped` (last confirmed run; ISSUE-028 test file not executed — see WSL note below)
- 8 skips are always integration tests needing real BLOFIN_API_KEY / BLOFIN_SECRET / BLOFIN_PASSPHRASE credentials

**Why:** ISSUE-028 (BloFinWebSocket plain "ping" string) was validated by code review only — running pytest causes a hard WSL crash in this environment. Source fix confirmed present; 8 pre-existing tests in `tests/test_issue_028_websocket_ping_format.py` assessed as logically correct by static analysis.

**How to apply:** Before writing new tests, check whether a targeted test file already exists (pattern: `tests/test_issue_NNN_*`). All 28 issues now have either targeted test files or code-review-only validation notes.

**WSL constraint (discovered 2026-06-01):** Running any Python/pytest shell command in WSL causes a hard crash and shutdown in this agent session. All test validation for ISSUE-028 was performed by static analysis of source and test files only.

Key baseline history:
- 2026-05-16: 106 passed, 5 skipped (pre-resolver)
- 2026-05-17: 256 passed, 8 skipped (after issue-test-validator pass 1, 150 tests added)
- 2026-06-01 (pre-session): 258 passed, 8 skipped
- 2026-06-01 (post-session): 276 passed, 8 skipped (18 tests added)
- 2026-06-01 (ISSUE-028 session): 276 passed, 8 skipped (no new tests run; ISSUE-028 test file pre-existed)
