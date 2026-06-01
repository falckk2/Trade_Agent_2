---
name: blofin-sdk-quirks
description: Verified gaps and quirks in the BloFin SDK v0.5.0 surface area, useful when auditing exchange/blofin_exchange.py
metadata:
  type: reference
---

The installed SDK at `.venv/lib/python3.12/site-packages/blofin/` was inspected on 2026-05-16. Key findings, **beyond** what CLAUDE.md already documents:

- **`client.trading.get_order(...)` does NOT exist.** Only `get_active_orders`, `get_order_history`, and `get_trade_history` are available on `TradingAPI`. Any call to `get_order` raises `AttributeError`. (Drives ISSUE-001 in `issues.md`.)
- `place_order` POSTs camelCase fields (`instId`, `marginMode`, `positionSide`, `orderType`) — the snake_case is only for the Python kwargs. So response fields are also camelCase (`orderId`, `instId`, `state`, `accFillSz`, `avgPx`, `createTime`).
- Order states from `ORDER_STATES` constant: `live`, `canceled` (US spelling, single L), `filled`, `partially_filled`. Code at `blofin_exchange.py:_parse_order_status` correctly handles both `cancelled` and `canceled` spellings.
- `get_positions` response field for position size is documented as `positions` (the signed contract count). Field names like `positionId`, `averagePrice`, `markPrice`, `unrealizedPnl`, `realizedPnl`, `createTime` are unverified against live responses — bug-hunter added diagnostic logging to confirm (see [[diagnostic-logging-locations]]).
- `get_balance(account_type="futures")` returns `data[0]` with field `balance` or `totalEquity` — the exact name is uncertain because it varies between SDK versions. Code defensively tries both.
- Demo mode URL switching is global module state. There's no per-instance URL — both `blofin.constants.REST_API_URL` AND `blofin.utils.REST_API_URL` must be patched.

**Why:** These quirks are not in the project's CLAUDE.md and were discovered by reading the SDK source directly. They drive several issues in `issues.md` and should inform any review of code that uses the BloFin SDK.

**How to apply:** When auditing code under `src/exchange/`, cross-check method calls against the SDK source (paths above). When auditing position/order parsing, treat field names from the BloFin API docs as authoritative — the SDK does not validate them.
