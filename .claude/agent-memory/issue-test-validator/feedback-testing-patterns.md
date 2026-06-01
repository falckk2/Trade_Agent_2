---
name: feedback-testing-patterns
description: Effective testing patterns and strategies discovered across validation sessions for this codebase
metadata:
  type: feedback
---

Effective patterns for testing this codebase:

**Portfolio manager tests**: Use `tmp_path` fixture for `PortfolioManager(data_dir=str(tmp_path))`. Directly access `_pending_fill_prices`, `_strategy_realized_pnl`, and other private dicts to verify internals — the codebase is not overly encapsulated and these are the correct observation points.

**Engine tests with _update_portfolio**: Use `AsyncMock` for `exchange.get_positions` and `exchange.get_balance`. Mock `portfolio_manager.update` as `MagicMock()` and check `call_args[0][0]` (the positions list) to see what positions were passed after attribution. The `engine.add_strategy(strat, symbols=[...])` + `engine.enable_strategy(name)` pattern is how strategies are wired.

**Why:** The engine's `_update_portfolio` is a synchronous-in-spirit async method. Testing it with `@pytest.mark.asyncio` and `await engine._update_portfolio()` is reliable and doesn't need a full engine start.

**WeightedAggregatorFactory name convention**: The composite name format is `"composite[s1,s2,...]"` where names come from the order strategies were added. Always verify attribution names match `WeightedAggregatorFactory().build([(s1, w), (s2, w)]).name` to avoid hard-coding the format in tests.

**Dashboard callback tests**: Import `_build_strategy_equity_curves` directly from `src.dashboard.callbacks`. Build `PortfolioSnapshot` objects manually with all fields. Check `fig.data` for trace count, `t.name` for trace labels, `t.line.dash` for line style, and `list(t.y)` for y-values.

**WebSocket / asyncio tests**: For `BloFinWebSocket.listen()` tests, patch `asyncio.wait_for` with a `call_count` nonlocal counter — raise `asyncio.TimeoutError` on the first call, return a `WSMsgType.CLOSED` `MagicMock` on the second to cleanly terminate the while loop. Mock `_reconnect` with `AsyncMock` to avoid reconnect delays. Use `AsyncMock()` for `_ws` with `closed = False/True` as needed. This pattern is safe (no infinite loops, no resource exhaustion) and correctly exercises the TimeoutError handler without real network calls.

**WSL constraint (discovered 2026-06-01):** Running any Python/pytest shell command in this WSL environment causes a hard crash and shutdown. If pytest cannot be run, validate by static analysis: read the source fix, read the test file, reason through each test case's mock/assertion logic. Document clearly in issues.md that execution was skipped and why.

**How to apply:** Use these patterns when writing tests for exchange, engine, portfolio, and dashboard layers. Avoid starting real servers or loading YAML configs for unit tests — construct components directly.
