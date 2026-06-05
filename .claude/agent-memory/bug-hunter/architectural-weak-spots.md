---
name: architectural-weak-spots
description: Recurring categories of bugs and design weaknesses found across the Trade Agent 2 codebase that warrant extra scrutiny on future audits
metadata:
  type: project
---

After the 2026-05-16 full audit, these areas account for the highest density of bugs:

1. **`src/exchange/blofin_exchange.py`** — Most CRITICAL/HIGH issues stem from incorrect SDK usage (ISSUE-001, ISSUE-003, ISSUE-019, ISSUE-026). Treat any BloFin SDK method call as suspect until verified against the installed SDK source. See [[blofin-sdk-quirks]].

2. **`src/engine/trading_engine.py`** — Order-of-operations bugs (ISSUE-015 — exit check before portfolio update), strategy-attribution bugs (ISSUE-017), and broad `try/except Exception` in the tick loop that swallows AttributeError from ISSUE-001. The engine catches exceptions and keeps running, so SDK-level bugs don't surface as crashes — they manifest as silent data loss.

3. **`src/portfolio/manager.py`** — Cache invariants are fragile: `_pending_fill_prices` (ISSUE-014), strategy P&L double-counting on net-mode flips (ISSUE-013), non-atomic CSV writes (ISSUE-020), and silently-dropped malformed rows on load (ISSUE-025). The net-mode FLIP path is especially error-prone: ISSUE-013 fixed double-counting but the 2026-06-01 sweep found ISSUE-029 — `_record_trade(prev)` on a flip uses the *previous-tick* `Position` object, so the realizing close's PnL (which lands on the *current* object) is omitted. Any time you touch flip handling, check whether `prev` vs `pos` (current) is the right realized-PnL source.

6. **Strategy attribution + CLOSE matching (`src/engine/trading_engine.py`)** — The ISSUE-017 composite-name attribution (`composite[s1,s2,...]`) is only assigned when `not pos.strategy_name`, so positions are tagged once and never re-tagged. This created two 2026-06-01 findings: ISSUE-030 (`None` assigned to the `str`-typed `strategy_name` for uncovered symbols via `dict.get` with no default) and ISSUE-031 (CLOSE handler requires exact `position.strategy_name == strategy.name`, so a position tagged with a stale composite name — after the enabled set changes via the dashboard — can never be closed by a CLOSE signal). When auditing CLOSE/exit logic, consider that the enabled-strategy set is mutable at runtime (dashboard toggles) while position tags are frozen at open time.

4. **Dead/disconnected code paths** — `BloFinWebSocket` is implemented but never instantiated (ISSUE-006), causing candle caches to never refresh after the first tick (ISSUE-005). When auditing, always grep for the class/function name to confirm wiring.

5. **Config drift** — YAML defaults disagree with code defaults silently (ISSUE-009, ISSUE-021). Always cross-check `config/default.yaml` against constructor defaults in `src/risk/manager.py`, `src/engine/trading_engine.py`, etc.

**Why:** These weak spots reflect architectural choices (broad exception handling in the tick loop, global module state in the SDK URL patch, async/sync mixing in EventBus) — they will keep producing similar bug patterns until the architecture changes, not just the specific lines.

**How to apply:** On future audit passes, start with these four areas before reading other modules. They produce the highest bug yield per unit of analysis time.
