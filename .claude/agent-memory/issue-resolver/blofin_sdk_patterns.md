---
name: blofin-sdk-patterns
description: BloFin SDK v0.5.0 API mismatches and correct usage patterns discovered during issue resolution
metadata:
  type: project
---

# BloFin SDK v0.5.0 API Patterns

## `get_order` does not exist — use two-phase lookup
`TradingAPI` exposes `get_active_orders`, `get_order_history`, `get_trade_history` but NOT `get_order`.
Correct pattern: search `get_active_orders(inst_id=symbol)` first, then fall back to `get_order_history(inst_id=symbol, limit=100)`, matching by `item.get("orderId") == order_id`.

**Why:** ISSUE-001 — every order was left PENDING because the AttributeError was swallowed by engine try/except.

## Balance field names are ambiguous
`get_balance` response may use `balance` or `totalEquity`. Use `acct.get("balance") or acct.get("totalEquity")` and log an ERROR with `list(acct.keys())` if both are absent.

**Why:** ISSUE-026 — silent zero balance would disable all trading with no alert.

## Position field `positions` vs `pos`/`size`
The `get_positions` response uses field name `positions` (signed contracts) and `positionId`. If `positionId` is missing, log a WARNING with available keys — position flip detection breaks without stable IDs.

**Why:** ISSUE-019 — unverified against live API; field names need confirmation with real responses.

## Demo mode requires monkey-patching BOTH module globals
`blofin.constants.REST_API_URL` AND `blofin.utils.REST_API_URL` must both be patched.

**Why:** Both are read at request time by different parts of the SDK.
