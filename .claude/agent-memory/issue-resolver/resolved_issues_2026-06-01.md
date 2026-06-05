---
name: resolved-issues-2026-06-01
description: Issue assessment and fix pass on 2026-06-01 — ISSUE-014/017/022 confirmed in codebase; ISSUE-029/030/031 fixed by this resolver pass
metadata:
  type: project
---

# Issue Assessment Pass — 2026-06-01

## Key Finding (First Pass)
ISSUE-014, ISSUE-017, and ISSUE-022 all had their code-side fixes already implemented in the codebase (at some point after the 2026-05-17 test-validator pass). The issues.md had not been updated to reflect this. No new code changes were needed for these three issues.

## New Fixes Applied (Second Pass — ISSUE-029, 030, 031)

### ISSUE-029 (MEDIUM) — Flip records stale realized PnL
- **Root cause**: `_record_trade(prev)` used `prev.realized_pnl` which is one tick stale. The flip's realized delta lands on `pos.realized_pnl` (current tick).
- **Fix**: Added `realized_pnl_override` and `override_unrealized_pnl` optional parameters to `_record_trade`. In `update()` flip branch, now calls `_record_trade(prev, realized_pnl_override=pos.realized_pnl, override_unrealized_pnl=0.0)`.
- **Watermark**: Set to `realized_now` (= `pos.realized_pnl`) so the next event for this position ID starts from the correct baseline.
- **File**: `src/portfolio/manager.py` lines 84-92, 251-302

### ISSUE-030 (LOW) — `None` assigned to str-typed `strategy_name`
- **Root cause**: `symbol_attribution.get(pos.symbol)` returns `None` for uncovered symbols; assigned to `Position.strategy_name` which is declared `str = ""`.
- **Fix**: Single-line change: `symbol_attribution.get(pos.symbol) or ""`.
- **File**: `src/engine/trading_engine.py` line 381

### ISSUE-031 (LOW) — CLOSE signal can't close stale-tagged positions
- **Root cause**: CLOSE handler matched `position.strategy_name == strategy.name`. After a UI toggle, a position tagged `composite[sma,rsi]` never equals the current single-strategy name `rsi_btc`.
- **Fix**: Removed the strategy_name equality check. Now only matches `position.symbol == symbol` — correct for BloFin net mode (one position per symbol).
- **File**: `src/engine/trading_engine.py` lines 303-307

## Issues Assessed (First Pass)

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

## Test Suite Baseline
- Before this pass: 276 passed, 8 skipped (per issue-test-validator 2026-06-01)
- After this pass: not run (WSL crash constraint — pytest prohibited)

**Why:** The ISSUE-029/030/031 fixes are targeted one-to-three-line changes that can be verified by source inspection. No new tests written in this pass (test-validator agent handles that).
**How to apply:** When reading issues.md, always cross-check the actual source files before concluding an issue is still open — the code may have already been fixed.
