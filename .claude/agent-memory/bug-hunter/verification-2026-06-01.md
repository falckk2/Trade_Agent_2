---
name: verification-2026-06-01
description: Mode B pass confirming 4 issue-test-validator-promoted Resolved issues (012, 014, 017, 022); 0 reverted
metadata:
  type: project
---

Mode B verification on 2026-06-01 of the 4 issues promoted to Resolved by issue-test-validator.

**Result: all 4 CONFIRMED Resolved, 0 reverted to Fix Failed. ISSUE-019 stays Investigating (live API blocked).**

- ISSUE-012 — `src/strategies/sma_crossover.py:41-44`. Four `_sma()` calls unchanged (two over `candles[:-1]`). Accepted-cost LOW-priority, no code change. Resolved is the correct terminal status for a deliberate no-change decision when a regression guard locks the behavior.
- ISSUE-014 — `src/portfolio/manager.py:40-42, 231, 270-277`. `_pending_fill_prices` now `defaultdict(deque)` keyed `(symbol, Side)`. append in `_on_order_filled`, `popleft()` + empty-deque `del` in `_record_trade`. Overwrite race eliminated.
- ISSUE-017 — `src/engine/trading_engine.py:344-377`. `symbol_attribution` dict; composite name `"composite["+",".join(names)+"]"` matches `WeightedAggregatorFactory.build()` at `src/strategies/composite.py:167`. Name *ordering* matches because both iterate `self._strategies` in declaration order. Only un-attributed positions updated.
- ISSUE-022 — `src/core/models.py:102-103` (two new snapshot fields), `src/portfolio/manager.py:306-329` (`_build_snapshot` populates both), `src/dashboard/callbacks.py:188-217` (two traces: solid realized + dotted unrealized). Realized trace sourced from `_strategy_realized_pnl` (only updated on trade close) so it is mark-price-independent — the misleading drop-on-close is gone.

pytest tests/ → 276 passed, 8 skipped (matches expected baseline). Targeted files: test_issue_012/014/017/022 → 28 passed, 1 skipped (live-API).

See [[verification-2026-05-16]] for the prior baseline.
