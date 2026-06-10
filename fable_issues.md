# Fable Issues Register

_Last updated: 2026-06-10_

This register tracks architectural and structural issues found during a full-codebase review by Claude (Fable 5) on 2026-06-10. It complements `issues.md` (the original bug-by-bug register, 43 issues, 39 resolved). Issue IDs here use the `FABLE-` prefix to avoid colliding with `ISSUE-` numbering. Agents (issue-resolver, bug-hunter, issue-test-validator) should treat entries here exactly like `issues.md` entries: investigate, fix, append to **Fix History**, and update **Status** (`Open` → `Investigating` → `Fix Attempted` → `Resolved` / `Fix Failed`).

Note: ISSUE-040 through ISSUE-043 remain open in `issues.md` and are NOT duplicated here. FABLE-002 is likely a contributing cause of ISSUE-043's reconnect storms — resolve them together.

## Summary
- Total Issues: 16
- Critical: 1 | High: 3 | Medium: 8 | Low: 4
- Open: 2 (FABLE-015 supervision, FABLE-016 Dash deprecation) | Investigating: 0 | Fix Attempted: 10 | Fix Failed: 0 | Resolved: 4
- _2026-06-10 (fourth pass): FABLE-014/015/016 filed from operational review. FABLE-014 fixed same day (`enabled:` flag in strategies.yaml + `register_strategies()`); both SMA strategies enabled and a long-running demo trial launched on the 1H config. FABLE-015 (systemd unit) and FABLE-016 (dash-ag-grid migration) remain open by choice — small, well-scoped tasks._
- _Third pass 2026-06-10 (evening): ISSUE-040/042/043 in issues.md resolved (043 root cause: server requires client pings every ~30s and inbound data does not reset the timer — fixed with fixed 15s ping cadence, verified by 10-min soak: 1 reconnect vs ~16 expected). FABLE-002 promoted to Resolved with corrected attribution. pytest → 395 passed, 8 skipped._
- _Verification pass 2026-06-10 (later same day): FABLE-001 RESOLVED via live demo trade (TP/SL trigger registered on exchange with exact submitted prices; BloFin auto-cancels attached TP/SL on position close — `scripts/verify_tpsl_demo.py`). FABLE-003 RESOLVED via observed clean SIGTERM shutdown + unit coverage. FABLE-013 (new: fetch script single-page incremental bug) found, fixed, and verified — all historical data now continuous through 2026-06-10. Parameter sweep (`scripts/tune_strategies.py`) drove config changes: timeframe 5m → 1H, ETH SMA 10/30 → 5/30, RSI strategies removed (net-negative across the entire grid). See FABLE-010 fix history for numbers._
- _Review basis: full read of `main.py`, `src/engine/`, `src/exchange/`, `src/execution/`, `src/portfolio/`, `src/risk/`, `src/data/`, plus config. Baseline test run: pytest tests/ → 308 passed, 8 skipped._
- _Suggested fix order: FABLE-001 → FABLE-003 → FABLE-002 → FABLE-004 → FABLE-005 → remainder._
- _Fix pass by Fable 5: 2026-06-10 — all 12 issues addressed in one session (FABLE-008 partially: config mitigation only). 58 new tests across 8 new test files (test_fable_001/003/004/005/006/007/009/010/011/012). pytest tests/ → 383 passed, 8 skipped — 0 regressions against the 308-pass baseline. Backtest CLI verified against real historical data (and found all 4 configured strategies net-negative after fees — see FABLE-010). ISSUE-041 from issues.md fixed incidentally alongside FABLE-012. Items needing live/demo verification before promotion to Resolved: FABLE-001 (TP/SL trigger format against real API), FABLE-002 (ISSUE-043 reconnect frequency re-measurement), FABLE-011 (real Telegram delivery)._

---

## Issue Log

### FABLE-001: Positions have no exchange-side stop-loss/take-profit — protection dies with the process
- **Status**: Resolved
- **Severity**: CRITICAL
- **Category**: Risk / Design Gap
- **File(s)**: `src/execution/executor.py` (`execute_signal`), `src/risk/manager.py` (`get_stop_loss` :167, `get_take_profit` :174), `src/engine/trading_engine.py` (`_check_exits` :221)
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
`RiskManager.get_stop_loss()` and `get_take_profit()` are defined (and declared in `IRiskManager`) but have **zero callers** anywhere in `src/` (verified by grep). Stop-loss and take-profit are enforced exclusively by `TradingEngine._check_exits`, which polls position prices once per tick and submits a market close when a threshold is breached. This means open futures positions are completely unprotected whenever the bot is not running and healthy: process crash, network outage, WSL VM suspend, or a hung event loop (see FABLE-002) all leave positions exposed with no exit orders on the exchange.

**Evidence**:
```bash
$ grep -rn "get_stop_loss\|get_take_profit" src/ | grep -v "def \|interface"
# (no output — no call sites)
```
`OrderExecutor.execute_signal` (`executor.py:45-51`) places a plain market/limit order with no TP/SL parameters.

**Fix Suggestion**:
BloFin's `place_order` supports attaching TP/SL trigger parameters (`tpTriggerPrice`/`tpOrderPrice`/`slTriggerPrice`/`slOrderPrice` — verify exact names against blofin SDK v0.5.0 `trading.place_order` signature and BloFin API docs); alternatively place a separate algo/trigger order after the fill. Wire it through:
1. `OrderExecutor.execute_signal` computes `stop = risk_manager.get_stop_loss(signal, fill_price)` and `tp = risk_manager.get_take_profit(signal, fill_price)` (requires injecting `IRiskManager` into the executor or passing the levels through the `Signal`).
2. `BloFinExchange.place_order` forwards the trigger prices.
3. Keep `_check_exits` as a redundant software backstop, but the exchange-side orders are the primary protection.
4. On `close_position`, cancel any outstanding TP/SL algo orders for that symbol to avoid orphaned triggers.

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: Verified SDK support: `trading.place_order(**kwargs)` forwards arbitrary params to `/api/v1/trade/order`, and `get_active_tpsl_orders`/`cancel_tpsl_order` exist in blofin 0.5.0. Implemented: (1) `IExchange.place_order` gained optional `stop_loss`/`take_profit`; `BloFinExchange.place_order` attaches `slTriggerPrice`/`tpTriggerPrice` (tick-rounded via new `_round_to_tick` using Decimal; `tick_size` now cached in `_instrument_specs`) with order price `-1` (market on trigger). (2) New `IExchange.cancel_tpsl_orders(symbol)` lists and cancels pending TP/SL orders; `OrderExecutor.close_position` calls it after `_await_fill` so orphaned triggers cannot re-open a position in net mode. (3) `TradingEngine._process_strategy_symbol` computes levels via `risk_manager.get_stop_loss/get_take_profit(signal, current_price)` and passes them through `execute_signal` (interface updated). `_check_exits` retained as software backstop. Tests: new `tests/test_fable_001_exchange_side_tpsl.py` (11 tests: trigger attachment, omission when unset, tick rounding, cancel iteration/error isolation, executor forwarding, close cleanup + failure tolerance, engine wiring); 3 assertions in `tests/unit/test_executor.py` updated for the new kwargs. pytest tests/ → 319 passed, 8 skipped. NOT yet verified against live/demo API — trigger price string format and `-1` order price convention need a live trial.
- **[2026-06-10] Verified live (demo account) — RESOLVED**: `scripts/verify_tpsl_demo.py` placed a min-size BTC-USDT market BUY with `slTriggerPrice=60648`/`tpTriggerPrice=64361.1` (order price `-1`): order filled at 61889.7, and `get_active_tpsl_orders` showed the trigger registered live with exactly the submitted prices (`state=live`, size 0.1 contracts). Bonus finding: after the position was closed by a regular order, BloFin **auto-cancelled the attached TP/SL** (`cancel_tpsl_orders → 0`, final tpsl count 0) — the executor's post-close cleanup is a redundant safety net, not a necessity. Final state flat. RESULT: PASS.

---

### FABLE-002: All BloFin SDK calls are synchronous inside `async def` — they block the event loop
- **Status**: Resolved
- **Severity**: HIGH
- **Category**: Architecture / Concurrency
- **File(s)**: `src/exchange/blofin_exchange.py` (every method that touches `self.client`: `get_balance` :170, `place_order` :210, `cancel_order` :242, `get_open_orders` :251, `get_order` :293/:302, `get_positions` :315, `get_candles` :352, `get_ticker` :372, `_load_instrument_specs` :125)
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
`BloFinExchange` presents an async interface, but the blofin SDK v0.5.0 is synchronous (requests-based HTTP). Every REST call blocks the entire asyncio event loop for its full network round-trip. Consequences:
1. The `BloFinWebSocket.listen()` task cannot receive messages or send heartbeat pings while any REST call is in flight — a slow REST response delays pings past the server's idle window. This is a likely contributing cause of the persistent reconnects documented in ISSUE-043 (5 reconnects per 10 minutes).
2. `_await_fill`'s `asyncio.sleep(0.5)` poll loop interleaves with blocking `get_order` calls, so each order confirmation freezes everything else repeatedly.
3. Per-symbol processing in `_tick` is serial even though the code is structured as if concurrent.

**Evidence**:
```python
# blofin_exchange.py:315 — sync HTTP inside async def
async def get_positions(self, symbol: str | None = None) -> list[Position]:
    resp = self.client.trading.get_positions(inst_id=symbol)  # blocks the loop
```
The blofin package uses `requests` under the hood (see `.venv/lib/python3.12/site-packages/blofin/`).

**Fix Suggestion**:
Wrap every SDK invocation in `asyncio.to_thread(...)`, e.g.:
```python
resp = await asyncio.to_thread(
    self.client.trading.get_positions, inst_id=symbol
)
```
Apply uniformly to all client calls listed above (a small private helper like `await self._call(fn, **kwargs)` keeps it tidy). The SDK client must be thread-safe for this usage (requests sessions are thread-safe for independent requests); if in doubt, serialize calls with an `asyncio.Lock` — still non-blocking for the loop. After this change, re-run a live trial and re-measure ISSUE-043 reconnect frequency.

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: Verified the SDK's `send_request` uses module-level `requests.get/post` (no shared Session) — thread-safe per call, so no serialization lock needed. Added static helper `BloFinExchange._call(fn, *args, **kwargs)` = `await asyncio.to_thread(fn, ...)` and converted every SDK call site: `get_balance`, `place_order`, `cancel_order`, `cancel_tpsl_orders` (list + per-order cancel), `get_open_orders`, `get_order` (both phases), `get_positions`, `get_candles`, `get_ticker`; `connect()` now runs `_load_instrument_specs` via `asyncio.to_thread` (its internal `get_instruments` call stays sync — it already executes in the worker thread). No call sites remain that invoke `self.client.*` directly on the event loop (verified by grep). No new tests — behavior is identical from callers' perspective and existing 325-test suite passes; the observable effect (WS ping latency) needs a live trial. Re-measure ISSUE-043 reconnect frequency on next demo run. pytest tests/ → 325 passed, 8 skipped.
- **[2026-06-10] Demo trial (4.6 min, post-fix)**: **0 WebSocket reconnects** (previous baseline: ~5 per 10 min, so 2–3 expected in this window). No warnings or exceptions anywhere in the log. Strongly suggests the event-loop blocking was the dominant cause of the ISSUE-043 reconnect storms — heartbeat pings now go out on time. A longer soak run would confirm, but behavior is verified working.
- **[2026-06-10] Correction + RESOLVED**: A later 1H-config soak showed ISSUE-043's storms had a separate root cause (ping-on-receive-timeout losing the race with the server's ~30s ping deadline — fixed independently, see ISSUE-043 in issues.md). FABLE-002's own claim — SDK calls no longer block the event loop — stands on its own: verified by code audit (zero direct `self.client.*` calls on the loop), the full test suite, and two clean demo runs. Resolved.

---

### FABLE-003: Shutdown race — `close_all_positions` runs while a tick may still be in flight
- **Status**: Resolved
- **Severity**: HIGH
- **Category**: Concurrency / Logic Error
- **File(s)**: `src/engine/trading_engine.py` (`start` :132-154, `stop` :156-163), `main.py` (`run_engine` :225-233)
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
`main.py` calls `await engine.stop()` while `engine_task` (running `engine.start()`) is still alive. `stop()` sets `_running = False`, but a `_tick()` already in progress keeps executing concurrently with `stop()`'s `close_all_positions()`. The in-flight tick can place a NEW order *after* all positions were closed, leaving an orphaned open position after "Shutdown complete" is logged. Additionally:
1. `engine_task.cancel()` (`main.py:230`) is never awaited, so cancellation may not complete before `asyncio.run` tears down the loop (emits "Task was destroyed but it is pending" and skips cleanup).
2. `ws_task.cancel()` (`main.py:233`) likewise is not awaited.
3. `stop()` disconnects the exchange (`stop` :161) while the in-flight tick may still be using it — `self.client` raises `RuntimeError("Exchange not connected")` mid-tick (caught by the tick's broad except, but noisy and masks the race).

**Evidence**:
```python
# main.py:227-233
await stop_event.wait()
await engine.stop()        # closes positions while engine_task may be mid-tick
engine_task.cancel()       # never awaited
```
`start()`'s loop (`trading_engine.py:149-154`) only checks `_running` between iterations; a tick that started before `stop()` runs to completion.

**Fix Suggestion**:
1. Have the engine hold a reference to its own loop task, or restructure: `stop()` sets `_running = False`, then `main.py` does `engine_task.cancel()` followed by `await asyncio.gather(engine_task, return_exceptions=True)` (or await the task with the sleep made cancellation-safe) **before** calling a separate `await engine.shutdown_cleanup()` that closes positions and disconnects.
2. Simplest robust shape: move `close_all_positions()` + `disconnect()` out of `stop()` into the tail of `start()` (after the while loop exits) so cleanup is strictly ordered after the last tick; `stop()` then just flips the flag and waits on an `asyncio.Event` that `start()` sets when fully drained.
3. Await `ws_task` after cancelling.
Write a regression test: start engine with a slow fake tick, trigger stop mid-tick, assert no order is placed after close_all_positions ran.

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: Implemented fix-suggestion option 2. `start()` now owns the full lifecycle: cleanup (`_shutdown_cleanup` = close positions → disconnect → save trade history) runs in its `finally` block, strictly ordered after the last tick. New `_stop_requested`/`_drained` asyncio Events: the interval sleep is `asyncio.wait_for(_stop_requested.wait(), timeout=interval)` so stop is immediate even with long intervals; `stop()` flips the flag, sets the event, and awaits `_drained` — it returns only after cleanup completed. `stop(close_positions=False)` honored via `_close_positions_on_stop`. If `start()` never ran, `stop()` performs cleanup directly (previous behavior preserved). `main.py` now awaits `engine_task` (which completes naturally — no longer cancelled un-awaited) and cancels+awaits `ws_task`. Tests: new `tests/test_fable_003_shutdown_race.py` (6 tests: in-flight tick ordering vs close_all, stop-returns-after-cleanup, interval-sleep interruption, no tick after stop, close_positions=False path, stop-without-start). pytest tests/ → 325 passed, 8 skipped.
- **[2026-06-10] Demo trial — RESOLVED**: SIGTERM shutdown verified live — full ordered sequence (signal → engine stopping → position check → exchange disconnect → trade history save → engine stopped → WS disconnect → "Shutdown complete") completed cleanly in 0.4s, no errors, exit code 0. Combined with the 6 unit tests covering the race directly, promoted to Resolved. Minor cosmetic note: trade history is saved twice at shutdown (once in `_shutdown_cleanup`, once in `main.py`'s `finally`) — harmless pre-existing duplication.

---

### FABLE-004: Trade history is persisted only at shutdown — a crash loses the whole session's trades
- **Status**: Fix Attempted
- **Severity**: HIGH
- **Category**: Data Integrity
- **File(s)**: `src/portfolio/manager.py` (`save_trade_history` :149, `_record_trade` :260), `src/engine/trading_engine.py` (:162), `main.py` (:241)
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
`save_trade_history()` is called in exactly two places: `TradingEngine.stop()` and `main.py`'s `finally` block. Both require a graceful shutdown. On a hard crash (OOM, power loss, `kill -9`, unhandled error in a non-engine thread) every trade recorded during the session is lost. Because `_load_trade_history()` also reconstructs `_strategy_realized_pnl` from the CSV at startup, the loss silently corrupts cumulative per-strategy P&L across restarts — the dashboard's realized equity curve would understate P&L with no warning.

**Fix Suggestion**:
Persist incrementally. Options (pick one):
1. **Append-per-trade (preferred)**: at the end of `_record_trade`, append the single new row to `trade_history.csv` (write header if the file doesn't exist). Keep the atomic full rewrite in `save_trade_history()` for shutdown/compaction. Appends are cheap (a few per hour at most).
2. Call `save_trade_history()` from `update()` whenever a trade was recorded that cycle.
Mind the existing `_lock` (already held in `_record_trade`) and keep file I/O failures non-fatal (log + continue) as in the current save path.

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: Implemented option 1 (append-per-trade). Extracted shared `_CSV_HEADER` constant and `_trade_row()` helper (now used by both `save_trade_history` and the new path, eliminating the duplicated column list). New `_append_trade_to_csv(trade)` appends one row (writing the header when the file doesn't exist), called at the end of `_record_trade` (lock held); I/O failures log and never abort recording. `save_trade_history()` unchanged — its atomic full rewrite at shutdown compacts the file, so appended rows are never duplicated on reload. Tests: new `tests/test_fable_004_incremental_trade_persistence.py` (5 tests: append-without-save, single header across multiple appends, crash-reload reconstructs history + realized P&L, shutdown rewrite doesn't duplicate, append failure doesn't abort in-memory recording). pytest tests/ → 330 passed, 8 skipped.

---

### FABLE-005: `_to_contracts` silently rounds orders UP to exchange minimum size, exceeding risk-approved quantity
- **Status**: Fix Attempted
- **Severity**: MEDIUM
- **Category**: Risk / Logic Error
- **File(s)**: `src/exchange/blofin_exchange.py` (`_to_contracts` :136-149)
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
```python
rounded = round(round(contracts / lot) * lot, 10)
return max(rounded, spec["min_size"])   # :149
```
If the risk-manager-approved quantity converts to fewer contracts than the instrument's `minSize`, the order is silently bumped UP to the minimum instead of being rejected. The actual placed size can then exceed what `RiskManager.validate_signal`/`calculate_position_size` approved — on a small account or low-strength signal, by a large multiple. The same `max()` also turns a quantity that rounds to 0 lots into a min-size order, so "too small to trade" becomes "trade anyway".

**Fix Suggestion**:
Return `0.0` (and log at INFO) when `rounded < spec["min_size"]`, so `place_order`'s existing `contracts <= 0` guard rejects the order:
```python
if rounded < spec["min_size"]:
    logger.info(
        "%s: size %.10f contracts below exchange minimum %s — skipping order",
        symbol, rounded, spec["min_size"],
    )
    return 0.0
return rounded
```
Note `place_order` currently raises `ValueError` on `contracts <= 0`; callers treat that as an error. Consider downgrading the below-minimum case to a logged skip in `execute_signal` rather than an exception, since it is an expected small-account outcome, not a fault. Update any tests asserting the `max(..., min_size)` behavior.

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: `_to_contracts` now returns `0.0` with an INFO log when the lot-rounded contract count is below `min_size` (no test asserted the old `max()` behavior — verified by grep). `place_order`'s existing `contracts <= 0` guard then raises `ValueError`; `OrderExecutor.execute_signal` wraps only the `place_order` call in `try/except ValueError`, logs the skip at INFO, and returns a synthetic `Order(status=FAILED)` without publishing ORDER_PLACED/ORDER_FILLED — so an expected small-account skip no longer surfaces as a logged exception in `_tick`. The HOLD/CLOSE guard's own ValueError is raised before `place_order` and still propagates. `close_position` is unaffected: close quantities derive from positions that were opened at ≥ min size, so they always convert back to ≥ min size. Tests: new `tests/test_fable_005_min_size_no_roundup.py` (7 tests: below-min → 0, round-to-zero → 0 not min, at-min passthrough, above-min unchanged, place_order raises without calling SDK, executor returns FAILED with no events, HOLD ValueError still propagates). pytest tests/ → 337 passed, 8 skipped.

---

### FABLE-006: Fee/fill-price deques keyed by `(symbol, side)` drift permanently after any missed match
- **Status**: Fix Attempted
- **Severity**: MEDIUM
- **Category**: Data Integrity / Fragile Design
- **File(s)**: `src/portfolio/manager.py` (`_pending_fill_prices` :40, `_pending_fees` :46, `_on_order_filled` :237, `_record_trade` :313-346)
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
Fill prices and fees are matched to trades via FIFO deques keyed by `(symbol, side)`. The pairing is positional, not identity-based: nothing ties a queued fill to the specific order/position it belongs to. Failure modes:
1. If an ORDER_FILLED event fires but the corresponding close is never detected (e.g. position closed manually on the exchange UI, or a flip mis-detected), the queue entry is never consumed — every subsequent trade on that key pops the WRONG (stale) price/fee, forever.
2. If a close is detected without a preceding ORDER_FILLED (e.g. fill price was None per the `_on_order_filled` guard at :245), the entry/exit pairing for all later trades shifts by one.
3. A flip is a single order that simultaneously closes the old leg and opens the new one; its fee is queued once but conceptually belongs to two trades — entry-fee attribution after a flip is wrong by construction.
There is no reconciliation or queue-age eviction, so drift is silent and permanent until restart.

**Fix Suggestion**:
Replace positional matching with identity matching: key the cache by `order_id` and carry the closing order's id into trade recording (the ORDER_FILLED payload already contains the full `Order` and, on the close path, the `Position`). Longer term, the cleanest fix is to drop the cache entirely and query BloFin's fills endpoint (`trading.get_trade_history(inst_id=..., order_id=...)`) in `_record_trade` for authoritative price/fee — this also fixes the flip-fee split. Interim hardening: log a WARNING whenever a queue is non-empty at shutdown or exceeds depth N, so drift becomes visible.

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: Implemented identity matching for close fills plus the interim hardening. New `_pending_close_fills: dict[position_id, (price, fee)]` populated in `_on_order_filled` when the event payload carries a `position` with a non-empty id (the payload shape `OrderExecutor.close_position` publishes); such fills no longer enter the positional queues. `_record_trade` consumes by `position.id` first and falls back to the `(symbol, side)` queues for fills without a position — entries and flips (a flip arrives as an opposite-side entry order, so its positional fallback is retained by design; per-leg flip fee attribution remains approximate, see remaining-work note). Empty `position.id` (ISSUE-019 case) falls back to the queue to avoid identity collisions. Hardening: queue depth > 4 logs WARNING; `save_trade_history` warns when any cached fills were never matched. Querying BloFin's fills endpoint as authoritative source remains future work. Tests: new `tests/test_fable_006_close_fill_identity_matching.py` (8 tests, incl. the core "wrong position cannot steal another position's fill" regression). ISSUE-014 FIFO tests unchanged and passing. pytest tests/ → 355 passed, 8 skipped.

---

### FABLE-007: Drawdown baseline never ratchets up — halt threshold decays as equity grows
- **Status**: Fix Attempted
- **Severity**: MEDIUM
- **Category**: Risk / Design Gap
- **File(s)**: `src/risk/manager.py` (`set_initial_equity` :63, `validate_signal` :94-108), `data/initial_equity.json`
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
The drawdown check compares current equity against a fixed `initial_equity` persisted once (ISSUE-034) and never updated. Two failure modes:
1. **After profits**: if equity grows from $50k to $100k, the 10% halt only triggers below $45k — the bot can lose 55% of current equity before halting. The protection weakens the better the bot performs.
2. **Deposits/withdrawals**: any external transfer breaks the baseline semantics entirely (a withdrawal can trigger a false halt; a deposit masks real losses).

**Fix Suggestion**:
Use a trailing high-watermark: on every `validate_signal` (or in a dedicated `update_equity` hook), `self._peak_equity = max(self._peak_equity, portfolio.total_equity)` and measure drawdown from `_peak_equity`. Persist the peak in the same JSON file (rename key, keep backward-compat read of `initial_equity` as the starting peak). Deposit/withdrawal detection is out of scope for now — document that transfers require deleting the baseline file (already the documented reset mechanism, `main.py:81-83`).

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: `RiskManager._initial_equity` renamed to `_peak_equity`; `validate_signal` ratchets it up (`equity > peak` → update + `_save_baseline()`) and measures drawdown from the peak. Baseline JSON now stores `peak_equity`; legacy `initial_equity` key is read as the starting peak (backward compatible with existing `data/initial_equity.json`). `set_initial_equity()` keeps its interface name/semantics (seed-once; cannot lower an existing peak). Deposit/withdrawal detection deliberately out of scope — documented in the method docstring and `main.py` comment: delete the baseline file after transfers. Tests: new `tests/test_fable_007_drawdown_high_watermark.py` (6 tests: ratchet rejects 15%-from-new-peak, accepts 5%-from-peak, fixed-baseline behavior preserved, seed cannot lower peak, persistence round-trip writes `peak_equity`, legacy key accepted). Existing `test_rejected_at_max_drawdown` unchanged and passing. pytest tests/ → 343 passed, 8 skipped.

---

### FABLE-008: Tick interval (5s) re-evaluates 5m candles ~60× per bar — wasted REST load on a blocking client
- **Status**: Fix Attempted
- **Severity**: MEDIUM
- **Category**: Performance / Design
- **File(s)**: `config/default.yaml` (`interval_seconds: 5` :23, `timeframe: "5m"` :21), `src/engine/trading_engine.py` (`_tick` :238), `src/data/provider.py`
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
With `interval_seconds: 5` and 5-minute candles, every tick re-runs `get_positions` + `get_balance` (REST), strategy analysis on a candle set that changes only once per 300s, and `get_current_price` (REST ticker) per actionable signal. SMA/RSI signals cannot change meaningfully intra-candle except via the live candle's close price, so ~98% of evaluations are redundant. Each REST call also blocks the loop (FABLE-002), so the tight interval multiplies that problem and burns API rate limit. The WebSocket already pushes live candles into the cache — the engine just doesn't use it as a trigger.

**Fix Suggestion**:
Either (cheap) raise `interval_seconds` to ~30-60 and document the tradeoff against `_check_exits` stop-loss latency, or (better) split the loop: run `_check_exits` on a fast timer (5-10s, prices from the WS-fed cache rather than REST), and run strategy evaluation event-driven on candle close (subscribe via `MarketDataProvider.subscribe` or trigger when the cached candle's timestamp rolls over). Note FABLE-001, once fixed, removes the latency pressure on `_check_exits` entirely.

**Fix History**:
- **[2026-06-10] Partial fix by Fable 5 (cheap option)**: `interval_seconds` raised 5 → 30 in `config/default.yaml` with a comment documenting the rationale — a 6× reduction in redundant evaluation/REST load. The two amplifying factors are gone: FABLE-002 removed the event-loop blocking, and FABLE-001's exchange-side TP/SL means `_check_exits` is now only a backstop, so 30s latency is acceptable. The better event-driven design (exit checks on a fast timer using WS-fed cached prices; strategy evaluation triggered on candle close via `MarketDataProvider.subscribe`) remains open as future work — it changes signal-timing semantics (strategies currently see the forming candle each tick) and should be validated against a backtest (FABLE-010 now exists for this) plus a live trial before adoption. Config-only change; no new tests. pytest tests/ → 383 passed, 8 skipped.

---

### FABLE-009: Stale portfolio snapshot within a tick — exits taken by `_check_exits` are invisible to signal processing
- **Status**: Fix Attempted
- **Severity**: LOW
- **Category**: Logic Error
- **File(s)**: `src/engine/trading_engine.py` (`_tick` :238-247, duplicate-side check :360-367, CLOSE handling :353-357)
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
`_tick` refreshes the portfolio once at the top, then `_check_exits` may market-close positions. The rest of the tick (`_process_strategy_symbol`) still reads `get_snapshot()`, which returns the pre-close snapshot. Consequences within that same tick:
1. A LONG signal arriving right after the stop-loss closed the BUY position is skipped as a "duplicate" (:362) because the dead position still appears in the snapshot — a legitimate re-entry is delayed by one tick.
2. A CLOSE signal can target an already-closed position and submit a second close order (:355-356), which in net mode would OPEN a position in the opposite direction. Mitigated today because `close_position` sizes from the stale `position.quantity` and BloFin nets it out — but only if the original close actually filled.
3. Exposure check (:111-132 in risk manager) overstates current exposure.

**Fix Suggestion**:
Track whether `_check_exits` closed anything (return a bool/list) and call `_update_portfolio()` again before processing signals when it did. This costs one extra REST round-trip only on ticks that actually exited — negligible. Alternatively pass the set of symbols closed this tick into `_process_strategy_symbol` and filter the snapshot's positions accordingly (no extra API call, slightly more bookkeeping).

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: Implemented the first suggestion. `_check_exits` now returns the number of positions it closed; `_tick` calls `_update_portfolio()` a second time only when that count is non-zero, so signal processing always sees the post-exit state and quiet ticks pay no extra REST round-trip. Tests: new `tests/test_fable_009_post_exit_snapshot_refresh.py` (4 tests: close count returned, zero when no trigger, second portfolio update after an exit, no second update on quiet ticks). pytest tests/ → 347 passed, 8 skipped.

---

### FABLE-010: No backtesting capability — strategy parameters are unvalidated guesses
- **Status**: Fix Attempted
- **Severity**: MEDIUM
- **Category**: Enhancement / Test Coverage
- **File(s)**: new module (suggest `src/backtest/`), `src/strategies/*`
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
There is no way to evaluate a strategy against historical data. The SMA(10/30) and RSI(14/70/30) parameters in `config/strategies.yaml` have never been validated; the only feedback loop is live demo trials, which are slow, noisy, and non-reproducible. The clean `IStrategy.analyze(candles) -> Signal` interface makes an offline backtester cheap to build — strategies are already pure functions of candle history.

**Fix Suggestion**:
Build a minimal event-loop-free backtester:
1. Candle source: `BloFinExchange.get_candles` paginated fetch dumped to CSV/parquet under `data/history/` (one-off script in `scripts/`).
2. Core loop: walk candles chronologically, call `strategy.analyze(window)`, simulate fills at next-candle open with a configurable fee (BloFin taker ~0.06%) and optional slippage bps, apply the same `RiskManager` sizing.
3. Output: trade list + summary stats (net P&L, win rate, profit factor, max drawdown, trade count) — reuse `TradeRecord`.
Keep it synchronous and dependency-free (pandas is already available). Defer parameter optimization/walk-forward; a single honest replay is the 80% win.

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: Historical data already existed (`scripts/fetch_historical_data.py` → `data/historical/<symbol>/<tf>.csv`, BTC/ETH 5m through 2026-03-27), so only the replay engine was needed. New `src/backtest/engine.py`: synchronous `Backtester` — fills at next-candle open (no look-ahead), adverse slippage in bps, per-side fees (default 0.06% taker), net-mode single position with flip-on-opposite-signal, SL/TP checked against each candle's high/low (stop-first when ambiguous), end-of-data force close, sliding `window=200` candles per `analyze()` call matching the live engine's `candle_limit` (avoids O(n²) over large histories). Stats computed by the new shared `src/portfolio/stats.py:compute_performance_stats` — extracted from `PortfolioManager.get_performance_stats` (FABLE-012), which now delegates to it, so live and backtest numbers are identical in definition. CLI `scripts/run_backtest.py` loads strategies.yaml via StrategyFactory and prints a per-strategy table. Tests: new `tests/test_fable_010_backtester.py` (10 tests with a scripted stub strategy: round-trip P&L, per-side fees, short profits, flip, SL trigger price, no-look-ahead, slippage direction, force close, stats parity with shared function, equity curve). **First real run (26 days of data, `--days 100`): ALL FOUR configured strategies are net-negative after fees** — sma_crossover ~27% win rate / PF 0.62-0.73, rsi 50-56% win rate / PF 0.71-0.87. Strategy parameters need re-tuning before live use. pytest tests/ → 383 passed, 8 skipped. Note: historical data is stale (ends 2026-03-27) — rerun `scripts/fetch_historical_data.py` for current coverage.
- **[2026-06-10] Data refreshed + parameter sweep run**: Historical data refilled through 2026-06-10 with zero gaps (required fixing FABLE-013 in the fetch script first). New `scripts/tune_strategies.py` sweeps SMA (fast 5-30 × slow 20-150) and RSI (period 7/14/21 × 3 level pairs) grids with an in-sample (2026-01-01..05-01) / out-of-sample (05-01..06-10) split. Findings: **every combination is net-negative on 5m** for both symbols (fees + noise); on **1H**, SMA crossovers are modestly positive in both windows (BTC 10/30: IS PF 1.16 / OOS PF 1.31; ETH 5/30: IS +$26.27 PF 1.29 / OOS +$8.21 PF 1.49) while RSI loses across the whole grid (PF 0.39-0.93). Config updated accordingly: `engine.timeframe` 5m → 1H, ETH SMA params 10/30 → 5/30, both RSI strategies removed (evidence documented in strategies.yaml comments). Caveats: single 5-month window, no walk-forward, modest edges — demo-trial before real funds.

---

### FABLE-011: No alerting — drawdown halt and other critical states are silent
- **Status**: Fix Attempted
- **Severity**: MEDIUM
- **Category**: Enhancement / Operations
- **File(s)**: `src/risk/manager.py` (:103-108), `src/exchange/blofin_websocket.py` (`_reconnect`), `src/engine/trading_engine.py` (`close_all_positions` failure path :191-195)
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
The bot is designed to run unattended, but its most critical states only emit log lines:
1. Max-drawdown halt (`risk/manager.py:103`) silently rejects all new signals while existing positions stay open and keep moving — the operator doesn't learn the bot has effectively stopped until they read logs.
2. A failed position close on shutdown logs "manual close required" (`trading_engine.py:193`) to a terminal nobody is watching.
3. WebSocket reconnect storms (ISSUE-043) and repeated REST failures have no escalation.

**Fix Suggestion**:
Add a minimal `INotifier` interface (fits the existing ABC/DI pattern) with a Telegram bot implementation (~30 lines, just an HTTP POST to `api.telegram.org`; token/chat-id via env vars, no-op when unset). Publish ALERT-worthy events on the existing `EventBus` (new `EventType.ALERT`) from the three sites above and let the notifier subscribe. Severity levels: drawdown halt and failed shutdown close = page-worthy; reconnect storm (>N reconnects per 10 min) = warn.

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: Added `EventType.ALERT` (payload `{"level", "message"}`), `src/notifications/` with `INotifier` ABC (provides `attach(event_bus)` that subscribes `_on_alert`) and `TelegramNotifier` (Bot API POST in a daemon thread — never blocks the loop, never raises; no-op with INFO log when `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` unset). Publishers: (1) `RiskManager` (optional `event_bus` ctor param, wired in `main.py`) publishes a critical alert via `publish_sync` once per halt onset — `_drawdown_alerted` flag resets when drawdown recovers below the limit; (2) `TradingEngine.close_all_positions` publishes critical "MANUAL CLOSE REQUIRED" on a failed shutdown close; (3) `BloFinWebSocket._check_reconnect_storm` (called after each successful reconnect) publishes a warning when ≥5 reconnects land within 10 min, throttled to once per window. `main.py` builds the notifier from env vars and attaches it to the bus. Tests: new `tests/test_fable_011_alerting.py` (10 tests: halt alerts once + re-arms on recovery, no-bus safety, shutdown-close failure alert, storm threshold + once-per-window, notifier disabled/posting/never-raises/event-routing). pytest tests/ → 373 passed, 8 skipped. Note: Telegram delivery itself untested against the real API — needs a live token to verify end-to-end.

---

### FABLE-013: `_incremental_update` in fetch_historical_data.py fetches only one page — silent interior data holes
- **Status**: Resolved
- **Severity**: MEDIUM
- **Category**: Bug / Data Integrity
- **File(s)**: `scripts/fetch_historical_data.py` (`_incremental_update`)
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 — gap analysis after a refresh appended only 1440 rows against a 75-day hole

**Description**:
The incremental-update path issued a **single** API request with `before=latest_ts` (no pagination loop), so it could never append more than one page (~1440 candles) per run. Any file more than one page stale got the *newest* 1440 candles appended, leaving a silent interior hole — and because the file's max timestamp was then current, subsequent runs considered the file up to date and never repaired it. Observed: 5m data had a 1672-hour (~70-day) hole between 2026-03-27 and 2026-06-05 after an incremental run. Backtests spanning the hole would silently produce wrong results.

**Fix History**:
- **[2026-06-10] Fixed by Fable 5**: Rewrote `_incremental_update` to page backwards from now using the `after` cursor (same convention as `_backfill`) until the page overlaps `latest_ts`, collecting all rows newer than it. One-off repair for the pre-existing holes: truncated each affected CSV at its interior gap (rows after the hole dropped), then re-ran the fetch — recovered 21,507 5m rows per symbol; all 12 symbol/timeframe files verified continuous (0 gaps > 2× bar interval) through 2026-06-10. Note: the fixed incremental path assumes the file has no interior holes (only appends past max timestamp) — true for all files after this repair.

---

### FABLE-014: Enabled-strategy state lost on every restart — unattended restarts silently stop trading
- **Status**: Fix Attempted
- **Severity**: MEDIUM
- **Category**: Operations / Design Gap
- **File(s)**: `main.py` (strategy registration), `src/engine/trading_engine.py` (`_enabled_strategies`), `config/strategies.yaml`
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 — operational review after the fix pass

**Description**:
`TradingEngine._enabled_strategies` starts empty and is only mutated by dashboard toggle clicks; nothing persists it. Every process restart therefore comes up with all strategies disabled until a human clicks the dashboard. Safe-by-default for first run, but combined with any crash-restart mechanism (FABLE-015) it means an unattended bot silently stops trading after a restart — positions get closed at shutdown (FABLE-003), then the bot idles indefinitely while appearing healthy in logs.

**Fix Suggestion**:
Add an optional `enabled: true|false` (default false) per strategy entry in `config/strategies.yaml`; `main.py` enables flagged strategies at startup. This makes restart behavior explicit, declarative, and version-controlled. Dashboard toggles remain runtime overrides (intentionally not persisted — the config is the source of truth for what runs after a restart; persisting clicks would create a second, invisible source of truth).

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: Strategy registration extracted from `main()` into testable `register_strategies(engine, factory, strategies_config)`; entries with `enabled: true` are activated via `engine.enable_strategy()` at startup (default remains disabled). Both SMA strategies set `enabled: true` in strategies.yaml for the demo trial started 2026-06-10. Dashboard toggles unchanged (runtime overrides, deliberately not persisted). Tests: new `tests/test_fable_014_config_enabled_strategies.py` (4 tests: flag activates, false/absent stays disabled, unknown entry skipped, production config guard). pytest tests/ → 399 passed, 8 skipped.

---

### FABLE-015: No process supervision — a crash at 3am stays down until someone notices
- **Status**: Open
- **Severity**: MEDIUM
- **Category**: Operations
- **File(s)**: deployment (no systemd unit / Docker / supervisor config exists)
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 — operational review

**Description**:
The bot runs as a foreground `python main.py` process. There is no systemd unit, Docker container, or supervisor configuration, so any crash (OOM, unhandled error outside the tick loop, host reboot) leaves it down with no restart and no notification (FABLE-011's alerting only fires while the process is alive — it cannot report its own death).

**Fix Suggestion**:
Add a systemd user unit (`docs/trade-agent.service` or `deploy/`): `Restart=on-failure`, `RestartSec=30`, `WantedBy=default.target`, working directory + venv python path, journald for logs. Pairs with FABLE-014: with `enabled:` flags in strategies.yaml, a supervised restart resumes trading automatically. Consider a `WatchdogSec`/heartbeat later. Note WSL2 specifics: systemd user units require systemd enabled in wsl.conf.

---

### FABLE-016: Dash DataTable deprecation — dashboard tables need eventual migration to dash-ag-grid
- **Status**: Open
- **Severity**: LOW
- **Category**: Maintenance / Dependency
- **File(s)**: `src/dashboard/components.py` (all three `dash_table.DataTable` usages)
- **Discovered**: 2026-06-10
- **Discovered By**: pytest DeprecationWarning during FABLE-012 work

**Description**:
Dash 4.0 emits: "The dash_table.DataTable will be removed from the builtin dash components in a future major version. We recommend using dash-ag-grid." The positions, trade-history, and performance-stats tables all use DataTable. No functional impact today; a future `pip install -U dash` across a major version will break the dashboard.

**Fix Suggestion**:
No action until a Dash major-version bump is planned. When migrating: `pip install dash[ag-grid]`, replace the three table builders with `dash_ag_grid.AgGrid` (columnDefs/rowData mapping is mechanical; conditional row styling moves to `getRowStyle`). Pin `dash<5` in pyproject until then.

---

### FABLE-012: No performance metrics — trade history exists but is never analyzed
- **Status**: Fix Attempted
- **Severity**: LOW
- **Category**: Enhancement
- **File(s)**: `src/portfolio/manager.py`, `src/dashboard/components.py`, `src/dashboard/callbacks.py`
- **Discovered**: 2026-06-10
- **Discovered By**: Fable 5 architecture review

**Description**:
`trade_history.csv` carries everything needed for performance evaluation (per-trade P&L, fees, duration, strategy attribution), but nothing computes win rate, profit factor, average win/loss, max drawdown, or expectancy — overall or per strategy. The dashboard shows equity curves but cannot answer "which strategy is actually profitable after fees?" — the core question for deciding weights and enable/disable toggles.

**Fix Suggestion**:
Add `PortfolioManager.get_performance_stats(strategy_name: str | None = None) -> dict` computing from `_trade_history`: trade count, win rate, gross/net P&L, total fees, profit factor (gross wins / gross losses), avg win, avg loss, max consecutive losses, avg duration. Surface as a stats table in the dashboard strategy panel (one row per strategy + total). Pairs naturally with FABLE-010 — the backtester should reuse the same stats function so live and backtest numbers are directly comparable. Low risk: pure read-side feature, hold `_lock` while snapshotting the list.

**Fix History**:
- **[2026-06-10] Fix attempted by Fable 5**: Added `PortfolioManager.get_performance_stats(strategy_name=None)` (declared in `IPortfolioManager`): trade/win/loss counts, win rate, net P&L, total fees, gross profit/loss, profit factor (∞ when no losses, 0.0 when no trades), avg win/loss, expectancy, max consecutive losses, avg duration — computed under `_lock` from `_trade_history` (pnl already net of fees). Dashboard: new `build_performance_stats_table` component; `update_strategy_performance` callback now also outputs `performance-stats-table` with one row per strategy + a bold TOTAL row; layout adds a "Performance Statistics" section above Trade History in the Strategy Performance tab. Incidentally fixed ISSUE-041 (fee column) in `build_trade_history_table` while editing the same file — noted in issues.md. Tests: new `tests/test_fable_012_performance_stats.py` (8 tests: overall stats, per-strategy filter, loss streak, avg win/loss, empty zeroes, ∞ profit factor, table rows + TOTAL, ∞ rendering). pytest tests/ → 363 passed, 8 skipped. Backtest reuse (FABLE-010) should call this same function for comparable numbers.

---
