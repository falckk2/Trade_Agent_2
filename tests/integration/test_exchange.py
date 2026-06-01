"""Integration tests for BloFinExchange against the demo API.

These tests require BLOFIN_API_KEY, BLOFIN_SECRET, BLOFIN_PASSPHRASE
environment variables to be set. They are skipped if credentials are not available.
"""

import os

import pytest

from src.core.enums import TimeFrame
from src.exchange.blofin_exchange import BloFinExchange

_HAS_CREDENTIALS = all(
    os.environ.get(k)
    for k in ("BLOFIN_API_KEY", "BLOFIN_SECRET", "BLOFIN_PASSPHRASE")
)

pytestmark = pytest.mark.skipif(
    not _HAS_CREDENTIALS,
    reason="BloFin API credentials not set",
)


@pytest.fixture
async def exchange():
    ex = BloFinExchange(
        api_key=os.environ.get("BLOFIN_API_KEY", ""),
        secret=os.environ.get("BLOFIN_SECRET", ""),
        passphrase=os.environ.get("BLOFIN_PASSPHRASE", ""),
        demo_mode=True,
    )
    await ex.connect()
    yield ex
    await ex.disconnect()


class TestBloFinDemoAPI:
    @pytest.mark.asyncio
    async def test_get_balance(self, exchange):
        balance = await exchange.get_balance()
        assert "total_equity" in balance
        assert isinstance(balance["total_equity"], float)

    @pytest.mark.asyncio
    async def test_get_candles(self, exchange):
        candles = await exchange.get_candles("BTC-USDT", TimeFrame.M5, 50)
        assert len(candles) > 0
        assert candles[0].open > 0
        # Should be chronological
        assert candles[0].timestamp <= candles[-1].timestamp

    @pytest.mark.asyncio
    async def test_get_ticker(self, exchange):
        ticker = await exchange.get_ticker("BTC-USDT")
        assert "last" in ticker
        assert ticker["last"] > 0

    @pytest.mark.asyncio
    async def test_get_positions(self, exchange):
        positions = await exchange.get_positions()
        assert isinstance(positions, list)

    @pytest.mark.asyncio
    async def test_get_open_orders(self, exchange):
        orders = await exchange.get_open_orders()
        assert isinstance(orders, list)
