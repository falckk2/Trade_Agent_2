"""
ISSUE-009: min_signal_strength in config/default.yaml is 0.05 (intentional for SMA
strategies) while the code default is 0.3. The YAML value must be respected over
the code default.

Tests verify:
1. default.yaml contains min_signal_strength: 0.05
2. The comment in default.yaml explains the rationale
3. RiskManager constructed from YAML config respects 0.05 threshold
4. A signal with strength 0.06 is accepted with the YAML value but rejected with the code default
"""

import pytest
import yaml
from pathlib import Path
from datetime import datetime, timezone

from src.core.enums import SignalType
from src.core.models import PortfolioSnapshot, Signal
from src.risk.manager import RiskManager

DEFAULT_YAML = Path("/home/rehan/Trade_Agent_2/config/default.yaml")


def _signal(strength: float) -> Signal:
    return Signal(
        signal_type=SignalType.LONG,
        symbol="BTC-USDT",
        strength=strength,
        strategy_name="sma_test",
        timestamp=datetime.now(tz=timezone.utc),
    )


def _snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp=datetime.now(tz=timezone.utc),
        total_equity=10000.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        positions=[],
    )


class TestMinSignalStrengthConfig:
    def test_yaml_contains_min_signal_strength_0_05(self):
        """config/default.yaml must specify min_signal_strength: 0.05."""
        with open(DEFAULT_YAML) as f:
            config = yaml.safe_load(f)
        assert config["risk"]["min_signal_strength"] == pytest.approx(0.05)

    def test_yaml_comment_explains_rationale(self):
        """The YAML file must contain a comment explaining why 0.05 is used."""
        raw = DEFAULT_YAML.read_text()
        # The comment should mention SMA or strength or 0.3
        assert "SMA" in raw or "sma" in raw or "0.3" in raw, \
            "No explanatory comment found in default.yaml for min_signal_strength"

    def test_risk_manager_with_yaml_value_accepts_weak_sma_signal(self):
        """With min_signal_strength=0.05 (YAML value), a strength=0.06 signal passes."""
        rm = RiskManager(min_signal_strength=0.05)
        assert rm.validate_signal(_signal(strength=0.06), _snapshot()) is True

    def test_risk_manager_with_code_default_rejects_weak_sma_signal(self):
        """With min_signal_strength=0.3 (code default), a strength=0.06 signal is rejected."""
        rm = RiskManager(min_signal_strength=0.3)
        assert rm.validate_signal(_signal(strength=0.06), _snapshot()) is False

    def test_risk_manager_constructed_from_yaml_config_uses_yaml_value(self):
        """When built from YAML config (as in main.py), the manager uses 0.05."""
        with open(DEFAULT_YAML) as f:
            config = yaml.safe_load(f)
        risk_cfg = config.get("risk", {})
        rm = RiskManager(
            min_signal_strength=risk_cfg.get("min_signal_strength", 0.3)
        )
        # Verify it uses the YAML value, not the code fallback
        assert rm._min_signal_strength == pytest.approx(0.05)
        # And that it accepts a weak signal
        assert rm.validate_signal(_signal(strength=0.06), _snapshot()) is True
