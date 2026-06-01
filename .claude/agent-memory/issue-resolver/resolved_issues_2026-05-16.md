---
name: resolved-issues-2026-05-16
description: Summary of issue resolution pass on 2026-05-16 — 22 resolved, 2 investigating, 3 open
metadata:
  type: project
---

# Issue Resolution Summary — 2026-05-16

## Resolved (22/27)

| Issue | Severity | Fix Summary |
|-------|----------|-------------|
| ISSUE-001 | CRITICAL | `get_order`: two-phase active+history lookup replacing missing SDK method |
| ISSUE-002 | CRITICAL | `.env` permissions to 600; `.env.example` created |
| ISSUE-003 | HIGH | Singleton guard (`_active_instances`) in `BloFinExchange.__init__` |
| ISSUE-004 | HIGH | Post-retry cancel in `_await_fill` when order not terminal |
| ISSUE-005 | HIGH | Age-based candle cache refresh in `MarketDataProvider.get_candles` |
| ISSUE-006 | HIGH | `BloFinWebSocket` wired into `main.py`; WebSocket started in `run_engine()` |
| ISSUE-007 | HIGH | Exposure check now includes projected order value |
| ISSUE-008 | HIGH | `ping_pong_test` removed from `strategies.yaml` |
| ISSUE-009 | HIGH | `min_signal_strength` YAML documented (0.05 is intentional for SMA strategies) |
| ISSUE-010 | MEDIUM | Async subscriber callbacks dispatched via `loop.create_task()` |
| ISSUE-011 | MEDIUM | RSI threshold validation in `__init__` and `configure` |
| ISSUE-013 | HIGH | Incremental realized PnL watermarking in `_record_trade` |
| ISSUE-015 | HIGH | `_update_portfolio` moved before `_check_exits` in `_tick` |
| ISSUE-016 | LOW | `should_stop_out`/`should_take_profit` evaluated once, stored in locals |
| ISSUE-018 | LOW | `ValueError` message improved in `execute_signal` |
| ISSUE-019 | MEDIUM | Warning log when `positionId` absent from position response |
| ISSUE-020 | MEDIUM | Atomic CSV write via temp file + `os.replace` |
| ISSUE-021 | LOW | `refresh_interval_ms` threaded from YAML through `create_app`/`create_layout` |
| ISSUE-023 | MEDIUM | `dict(metadata)` copy in `_make_signal` and all Signal re-creation sites |
| ISSUE-024 | MEDIUM | `loop.add_signal_handler` replaces `signal.signal` in `run_engine` |
| ISSUE-025 | MEDIUM | Per-row error handling in `_load_trade_history`; atomic commit to state |
| ISSUE-026 | MEDIUM | Error log with key list when balance fields missing |
| ISSUE-027 | LOW | All BUG-HUNTER markers removed |

## Investigating (2/27)

| Issue | Severity | Reason |
|-------|----------|--------|
| ISSUE-014 | MEDIUM | `_pending_fill_prices` keyed by (symbol, side) — full fix needs order-ID tracking through position lifecycle; deferred |
| ISSUE-019 | MEDIUM | Position field name (`positions` vs others) cannot be confirmed without live API response |

## Open (3/27)

| Issue | Severity | Reason |
|-------|----------|--------|
| ISSUE-012 | LOW | SMA O(N) slice recompute — negligible at <1ms, accepted |
| ISSUE-017 | MEDIUM | Multi-strategy position attribution — inherent BloFin net-mode limitation |
| ISSUE-022 | MEDIUM | Dashboard equity chart realized vs unrealized — requires PortfolioSnapshot schema change + dashboard refactor |

**Why:** Test suite after all fixes: 106 passed, 5 skipped (integration tests require live credentials).
