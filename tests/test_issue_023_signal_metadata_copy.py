"""
ISSUE-023: Signal metadata dict is always copied (not shared by reference) when
creating Signal instances via IStrategy._make_signal() and TradingEngine._process_strategy_symbol().

Tests verify:
1. _make_signal copies the metadata dict (caller's dict not mutated)
2. Passing the same metadata dict to two _make_signal calls produces independent copies
3. TradingEngine copies metadata when re-creating signals in _process_strategy_symbol
4. Mutation of one signal's metadata does not affect another signal sharing the same source dict
"""

import pytest
from datetime import datetime, timezone

from src.core.enums import SignalType
from src.core.models import Signal
from src.strategies.interface import IStrategy
from src.core.models import Candle


class ConcreteStrategy(IStrategy):
    """Minimal concrete strategy for testing _make_signal."""

    @property
    def name(self) -> str:
        return "concrete_test"

    def analyze(self, candles: list[Candle]) -> Signal:
        return self._make_signal(SignalType.HOLD)

    def configure(self, params: dict) -> None:
        pass


@pytest.fixture
def strategy():
    return ConcreteStrategy()


class TestSignalMetadataCopy:
    def test_make_signal_copies_metadata(self, strategy):
        """_make_signal must create a copy of the metadata dict."""
        source_meta = {"rsi": 42.0, "extra": "value"}
        signal = strategy._make_signal(SignalType.LONG, 0.5, metadata=source_meta)

        # Mutate the source dict after signal creation
        source_meta["rsi"] = 999.0

        # Signal's metadata must not be affected
        assert signal.metadata["rsi"] == 42.0

    def test_make_signal_none_metadata_produces_empty_dict(self, strategy):
        """When metadata is None, _make_signal produces an empty dict (not None)."""
        signal = strategy._make_signal(SignalType.HOLD)
        assert signal.metadata == {}
        assert isinstance(signal.metadata, dict)

    def test_two_signals_from_same_metadata_are_independent(self, strategy):
        """Two signals created from the same source dict have independent metadata copies."""
        source_meta = {"key": "original"}
        sig1 = strategy._make_signal(SignalType.LONG, 0.5, metadata=source_meta)
        sig2 = strategy._make_signal(SignalType.SHORT, 0.3, metadata=source_meta)

        # Mutating source does not affect either signal
        source_meta["key"] = "mutated"
        assert sig1.metadata["key"] == "original"
        assert sig2.metadata["key"] == "original"

        # The two signals' metadata dicts are distinct objects
        assert sig1.metadata is not sig2.metadata

    def test_engine_signal_re_creation_copies_metadata(self):
        """
        In TradingEngine._process_strategy_symbol, the re-created Signal uses
        dict(signal.metadata) — verify the copy idiom is in the source code.
        """
        import ast
        from pathlib import Path

        source = Path("/home/rehan/Trade_Agent_2/src/engine/trading_engine.py").read_text()
        tree = ast.parse(source)

        # Look for dict(sig.metadata) or dict(signal.metadata) call patterns
        found_copy = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # dict(something.metadata)
                if isinstance(node.func, ast.Name) and node.func.id == "dict":
                    for arg in node.args:
                        if isinstance(arg, ast.Attribute) and arg.attr == "metadata":
                            found_copy = True
                            break

        assert found_copy, (
            "TradingEngine does not appear to copy signal.metadata with dict() — "
            "ISSUE-023 metadata sharing fix may have been reverted"
        )

    def test_strategy_interface_make_signal_copies_metadata_in_source(self):
        """
        Verify IStrategy._make_signal source contains the dict(metadata) copy idiom.
        """
        import ast
        from pathlib import Path

        source = Path("/home/rehan/Trade_Agent_2/src/strategies/interface.py").read_text()
        tree = ast.parse(source)

        found_copy = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "dict":
                    for arg in node.args:
                        if isinstance(arg, ast.Name) and arg.id == "metadata":
                            found_copy = True
                            break

        assert found_copy, (
            "IStrategy._make_signal does not copy metadata with dict() — "
            "ISSUE-023 fix may have been reverted"
        )
