---
name: architectural-weak-spots
description: Recurring categories of bugs and design weaknesses found across the Trade Agent 2 codebase that warrant extra scrutiny on future audits
metadata:
  type: project
---

After the 2026-05-16 full audit, these areas account for the highest density of bugs:

1. **`src/exchange/blofin_exchange.py`** — Most CRITICAL/HIGH issues stem from incorrect SDK usage (ISSUE-001, ISSUE-003, ISSUE-019, ISSUE-026). Treat any BloFin SDK method call as suspect until verified against the installed SDK source. See [[blofin-sdk-quirks]].

2. **`src/engine/trading_engine.py`** — Order-of-operations bugs (ISSUE-015 — exit check before portfolio update), strategy-attribution bugs (ISSUE-017), and broad `try/except Exception` in the tick loop that swallows AttributeError from ISSUE-001. The engine catches exceptions and keeps running, so SDK-level bugs don't surface as crashes — they manifest as silent data loss.

3. **`src/portfolio/manager.py`** — Cache invariants are fragile: `_pending_fill_prices` (ISSUE-014), strategy P&L double-counting on net-mode flips (ISSUE-013), non-atomic CSV writes (ISSUE-020), and silently-dropped malformed rows on load (ISSUE-025).

4. **Dead/disconnected code paths** — `BloFinWebSocket` is implemented but never instantiated (ISSUE-006), causing candle caches to never refresh after the first tick (ISSUE-005). When auditing, always grep for the class/function name to confirm wiring.

5. **Config drift** — YAML defaults disagree with code defaults silently (ISSUE-009, ISSUE-021). Always cross-check `config/default.yaml` against constructor defaults in `src/risk/manager.py`, `src/engine/trading_engine.py`, etc.

**Why:** These weak spots reflect architectural choices (broad exception handling in the tick loop, global module state in the SDK URL patch, async/sync mixing in EventBus) — they will keep producing similar bug patterns until the architecture changes, not just the specific lines.

**How to apply:** On future audit passes, start with these four areas before reading other modules. They produce the highest bug yield per unit of analysis time.
