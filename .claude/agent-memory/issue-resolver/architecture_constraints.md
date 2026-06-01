---
name: architecture-constraints
description: Critical architectural constraints in Trade Agent 2 discovered during issue resolution
metadata:
  type: project
---

# Architecture Constraints

## Singleton BloFinExchange
Only one `BloFinExchange` instance may exist per process — a module-level `_active_instances` counter (protected by `_url_patch_lock`) enforces this. `__init__` raises `RuntimeError` if `_active_instances > 0`. `disconnect()` decrements it.

**Why:** ISSUE-003 — demo mode patches process-wide module globals (`blofin.constants.REST_API_URL`). Two instances with different modes would corrupt each other's routing.

## Tick ordering: portfolio update MUST precede exit checks
`TradingEngine._tick` must call `await self._update_portfolio()` BEFORE `await self._check_exits()`. Stop-loss and take-profit decisions made on stale prices miss fast moves.

**Why:** ISSUE-015 — exit checks were using prices from previous tick.

## Cumulative realized PnL watermarking
BloFin's `realized_pnl` field is cumulative since position open, not since last flip. `PortfolioManager._last_recorded_realized_pnl` tracks the last-recorded value per position ID. `_record_trade` computes `incremental = position.realized_pnl - last_recorded`.

**Why:** ISSUE-013 — position flips (same ID, side changes) would double-count realized PnL.

## Candle cache is time-gated (not just empty-gated)
`MarketDataProvider.get_candles` refreshes via REST if the newest cached candle is older than one timeframe period. `_TIMEFRAME_SECONDS` maps `TimeFrame` enums to seconds.

**Why:** ISSUE-005 — cache was only refreshed on first call; strategies analyzed stale data indefinitely.

## Signal metadata must always be copied
`IStrategy._make_signal` and all Signal re-creation sites must use `dict(metadata)` (not `metadata` directly). `Signal` is `frozen=True` but the dict is mutable.

**Why:** ISSUE-023 — shared metadata dicts bleed changes across Signal instances.

## `save_trade_history` writes atomically via temp file
Uses `os.replace(tmp_path, filepath)` (POSIX atomic rename). Never open the real CSV with mode "w" directly.

**Why:** ISSUE-020 — interrupted writes would truncate the CSV permanently.
