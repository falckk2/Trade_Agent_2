"""
ISSUE-005: MarketDataProvider.get_candles() refreshes the cache when the newest
cached candle is older than one timeframe period.

Tests verify:
1. Empty cache triggers a fetch
2. Fresh cache (within one period) does not trigger a re-fetch
3. Stale cache (older than one timeframe period) triggers a re-fetch
4. On fetch failure, stale data is preserved (no crash)
5. _TIMEFRAME_SECONDS map covers all TimeFrame enum values
"""

import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.enums import TimeFrame
from src.core.events import EventBus
from src.core.models import Candle
from src.data.provider import MarketDataProvider, _TIMEFRAME_SECONDS


def _make_candle(offset_seconds: int = 0) -> Candle:
    """Return a candle with a timestamp `offset_seconds` in the past."""
    ts = datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)
    return Candle(timestamp=ts, open=100.0, high=110.0, low=90.0, close=105.0, volume=1000.0)


@pytest.fixture
def mock_exchange():
    exchange = AsyncMock()
    exchange.get_candles = AsyncMock(return_value=[_make_candle(0)])
    return exchange


@pytest.fixture
def provider(mock_exchange):
    event_bus = EventBus()
    return MarketDataProvider(exchange=mock_exchange, event_bus=event_bus)


class TestCandleCacheAgeRefresh:
    @pytest.mark.asyncio
    async def test_empty_cache_triggers_fetch(self, provider, mock_exchange):
        """When cache is empty, get_candles must fetch from exchange."""
        result = await provider.get_candles("BTC-USDT", TimeFrame.M5)

        mock_exchange.get_candles.assert_called_once()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_fresh_cache_does_not_refetch(self, provider, mock_exchange):
        """A cache entry whose newest candle is within the timeframe period is not refreshed."""
        # Prime the cache with a fresh candle (0 seconds old)
        fresh_candle = _make_candle(0)
        provider._candle_cache[("BTC-USDT", TimeFrame.M5)] = [fresh_candle]

        await provider.get_candles("BTC-USDT", TimeFrame.M5)

        # Should not call exchange.get_candles because cache is fresh
        mock_exchange.get_candles.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_cache_triggers_refetch(self, provider, mock_exchange):
        """A cache entry older than one timeframe period (300s for M5) triggers a refetch."""
        # 301 seconds old — older than one M5 period
        stale_candle = _make_candle(301)
        provider._candle_cache[("BTC-USDT", TimeFrame.M5)] = [stale_candle]

        new_candle = _make_candle(0)
        mock_exchange.get_candles = AsyncMock(return_value=[new_candle])

        result = await provider.get_candles("BTC-USDT", TimeFrame.M5)

        mock_exchange.get_candles.assert_called_once()
        # Cache should now contain the fresh candle
        assert result[0].timestamp == new_candle.timestamp

    @pytest.mark.asyncio
    async def test_fetch_failure_preserves_stale_data(self, provider, mock_exchange):
        """When the exchange fetch fails, stale cached data is still returned (no crash)."""
        stale_candle = _make_candle(600)
        provider._candle_cache[("BTC-USDT", TimeFrame.M5)] = [stale_candle]
        mock_exchange.get_candles = AsyncMock(side_effect=RuntimeError("network error"))

        result = await provider.get_candles("BTC-USDT", TimeFrame.M5)

        # Stale data preserved
        assert len(result) == 1
        assert result[0].timestamp == stale_candle.timestamp

    def test_timeframe_seconds_covers_all_timeframes(self):
        """_TIMEFRAME_SECONDS must contain an entry for every TimeFrame enum value."""
        for tf in TimeFrame:
            assert tf in _TIMEFRAME_SECONDS, f"Missing TimeFrame.{tf.name} in _TIMEFRAME_SECONDS"
            assert _TIMEFRAME_SECONDS[tf] > 0

    def test_m5_period_is_300_seconds(self):
        assert _TIMEFRAME_SECONDS[TimeFrame.M5] == 300

    def test_h1_period_is_3600_seconds(self):
        assert _TIMEFRAME_SECONDS[TimeFrame.H1] == 3600

    @pytest.mark.asyncio
    async def test_boundary_candle_exactly_at_period_age_not_refreshed(self, provider, mock_exchange):
        """A candle exactly `tf_seconds` old is NOT stale (age > tf_seconds, not >=)."""
        # Exactly 300 seconds old — should NOT trigger refresh (age > 300 is False)
        # Note: due to timing, we use 299 to be safe
        borderline_candle = _make_candle(299)
        provider._candle_cache[("BTC-USDT", TimeFrame.M5)] = [borderline_candle]

        await provider.get_candles("BTC-USDT", TimeFrame.M5)

        mock_exchange.get_candles.assert_not_called()
