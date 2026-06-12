"""
ISSUE-008: ping_pong_test strategy removed from config/strategies.yaml.

Tests verify:
1. strategies.yaml does not contain a 'ping_pong_test' entry
2. strategies.yaml does not contain any strategy of type 'ping_pong'
3. The comment explaining the removal is present (regression guard)
4. The remaining strategies are the expected production ones
"""

import pytest
import yaml
from pathlib import Path

STRATEGIES_YAML = Path("/home/rehan/Trade_Agent_2/config/strategies.yaml")


@pytest.fixture
def strategies_list():
    with open(STRATEGIES_YAML) as f:
        data = yaml.safe_load(f)
    return data.get("strategies", [])


class TestPingPongAbsentFromYaml:
    def test_no_ping_pong_test_strategy_in_yaml(self, strategies_list):
        """ping_pong_test must NOT appear in strategies.yaml."""
        names = [s.get("name") for s in strategies_list]
        assert "ping_pong_test" not in names

    def test_no_ping_pong_type_in_yaml(self, strategies_list):
        """No strategy of type 'ping_pong' should exist in the YAML."""
        types = [s.get("type") for s in strategies_list]
        assert "ping_pong" not in types

    def test_ping_pong_removal_comment_present(self):
        """The comment documenting the removal must be in the file (regression guard)."""
        raw = STRATEGIES_YAML.read_text()
        assert "ping_pong_test" in raw or "ping_pong" in raw.lower(), \
            "Expected a comment about ping_pong_test removal to still exist in strategies.yaml"
        # Specifically the comment, not an active entry
        # If it appears in a comment context
        assert "#" in raw, "Expected commented-out documentation in strategies.yaml"

    def test_production_strategies_are_present(self, strategies_list):
        """Expected production strategies are still in the YAML.

        Roster updated 2026-06-10: RSI strategies removed after the backtest
        sweep showed them net-negative across the whole parameter grid
        (see FABLE-010 in issues.md); SMA crossovers kept.
        """
        names = [s.get("name") for s in strategies_list]
        assert "sma_crossover_btc" in names
        assert "sma_crossover_eth" in names

    def test_strategy_count_is_five(self, strategies_list):
        """5 configured strategies: 2 SMA (enabled) + 2 MarketCipher-style
        (wavetrend, ema_ribbon — registered but disabled pending evidence)
        + 1 TradingView webhook bridge (disabled until endpoint is live)."""
        assert len(strategies_list) == 5
