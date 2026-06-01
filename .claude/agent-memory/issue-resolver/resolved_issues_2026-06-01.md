---
name: resolved-issues-2026-06-01
description: Issue assessment pass on 2026-06-01 — fixes for ISSUE-014, 017, 022 confirmed already in codebase; ISSUE-012 cost accepted; ISSUE-019 blocked on live API
metadata:
  type: project
---

# Issue Assessment Pass — 2026-06-01

## Key Finding
ISSUE-014, ISSUE-017, and ISSUE-022 all had their code-side fixes already implemented in the codebase (at some point after the 2026-05-17 test-validator pass). The issues.md had not been updated to reflect this. No new code changes were needed for these three issues.

## Issues Assessed

| Issue | Severity | Status → | Action |
|-------|----------|----------|--------|
| ISSUE-012 | LOW | Investigating → Fix Attempted | No code change — accepted O(N) cost documented |
| ISSUE-014 | MEDIUM | Investigating → Fix Attempted | FIFO deque fix already in manager.py |
| ISSUE-017 | MEDIUM | Open → Fix Attempted | Composite naming attribution already in trading_engine.py |
| ISSUE-019 | MEDIUM | Investigating → Investigating | Blocked on live API; warning log already in place |
| ISSUE-022 | MEDIUM | Open → Fix Attempted | Split P&L fields already in models.py + callbacks.py + manager.py |

## Details of Pre-existing Fixes Found

### ISSUE-014 (`_pending_fill_prices` FIFO deque)
- `src/portfolio/manager.py` lines 34-42: `defaultdict(collections.deque)` replaces scalar dict
- `_on_order_filled` uses `append()` not assignment
- `_record_trade` uses `popleft()` with cleanup of empty deques

### ISSUE-017 (composite naming attribution in `_update_portfolio`)
- `src/engine/trading_engine.py` lines 337-378: pre-builds `symbol_attribution` dict
- Multi-strategy symbols get `"composite[s1,s2,...]"` name matching `WeightedAggregatorFactory`
- Two-pass: enabled strategies preferred; disabled strategies as fallback

### ISSUE-022 (split realized/unrealized P&L in equity chart)
- `src/core/models.py` lines 100-103: `strategy_pnl_realized` and `strategy_pnl_unrealized` fields added to `PortfolioSnapshot`
- `src/portfolio/manager.py` lines 298-330: `_build_snapshot` populates both fields
- `src/dashboard/callbacks.py` lines 160-228: two traces per strategy (solid realized, dotted unrealized); `hasattr` guard for backward compat

## Test Suite
258 passed, 8 skipped — 0 regressions.

**Why:** The issue-resolver and bug-hunter agents working in prior passes apparently made code changes without updating issues.md status. This pass's primary job was to reconcile the issues.md with the actual code state.
**How to apply:** When reading issues.md, always cross-check the actual source files before concluding an issue is still open — the code may have already been fixed.
