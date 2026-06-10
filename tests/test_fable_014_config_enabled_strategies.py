"""
FABLE-014: Enabled-strategy state was lost on every restart.

The engine's enabled set started empty and only dashboard clicks mutated it,
so an unattended restart silently stopped trading. Fix: strategies.yaml
entries support `enabled: true` (default false) and main.register_strategies
activates flagged strategies at startup.
"""

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from main import register_strategies


def _factory_with(names):
    factory = MagicMock()
    instances = {}
    for n in names:
        strategy = MagicMock()
        strategy.name = n
        instances[n] = strategy
    factory.get_instance.side_effect = instances.get
    return factory


class TestRegisterStrategies:
    def test_enabled_true_activates_strategy(self):
        engine = MagicMock()
        factory = _factory_with(["s1", "s2"])
        config = [
            {"name": "s1", "enabled": True, "symbols": ["BTC-USDT"]},
            {"name": "s2", "symbols": ["ETH-USDT"]},  # no flag → stays disabled
        ]
        names = register_strategies(engine, factory, config)

        assert names == ["s1", "s2"]
        assert engine.add_strategy.call_count == 2
        engine.enable_strategy.assert_called_once_with("s1")

    def test_enabled_false_stays_disabled(self):
        engine = MagicMock()
        factory = _factory_with(["s1"])
        register_strategies(engine, factory, [{"name": "s1", "enabled": False}])
        engine.enable_strategy.assert_not_called()

    def test_unknown_strategy_skipped(self):
        engine = MagicMock()
        factory = _factory_with(["s1"])
        names = register_strategies(
            engine, factory, [{"name": "missing", "enabled": True}]
        )
        assert names == []
        engine.enable_strategy.assert_not_called()


class TestProductionConfig:
    def test_production_strategies_are_enabled_for_trial(self):
        """Demo trial config (2026-06-10): both SMA strategies start enabled."""
        with open(Path(__file__).parent.parent / "config" / "strategies.yaml") as f:
            entries = yaml.safe_load(f)["strategies"]
        assert all(e.get("enabled") is True for e in entries), (
            "Trial config expects both strategies enabled at startup; "
            "update this test if the roster's enablement policy changes"
        )
