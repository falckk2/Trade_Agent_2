"""
ISSUE-019: get_positions() field name verification — 'positions' vs 'pos'/'size' and
'positionId' presence. Currently Investigating; live API response needed.

Tests verify what can be tested without live credentials:
1. get_positions() correctly skips items where 'positions' field is 0
2. get_positions() logs a warning when 'positionId' is missing
3. The fallback behavior (empty position id) is documented
4. Items with non-zero 'positions' field are returned

Live response field verification is Inconclusive — requires credentials.
"""

import logging
import pytest
from unittest.mock import MagicMock

import src.exchange.blofin_exchange as exchange_module
from src.exchange.blofin_exchange import BloFinExchange
from src.core.enums import Side


@pytest.fixture(autouse=True)
def reset_singleton():
    exchange_module._active_instances = 0
    yield
    exchange_module._active_instances = 0


@pytest.fixture
def exchange():
    exc = BloFinExchange(api_key="k", secret="s", passphrase="p", demo_mode=False)
    exc._client = MagicMock()
    exc._instrument_specs["BTC-USDT"] = {
        "contract_value": 0.001,
        "lot_size": 1.0,
        "min_size": 1.0,
    }
    return exc


_VALID_POSITION_ITEM = {
    "positionId": "pos_001",
    "instId": "BTC-USDT",
    "positions": "5.0",     # non-zero → included
    "averagePrice": "50000.0",
    "markPrice": "51000.0",
    "unrealizedPnl": "5.0",
    "realizedPnl": "0.0",
    "createTime": "1700000000000",
}

_ZERO_POSITION_ITEM = {
    "positionId": "pos_002",
    "instId": "BTC-USDT",
    "positions": "0",       # zero → skipped
    "averagePrice": "50000.0",
    "markPrice": "51000.0",
    "unrealizedPnl": "0.0",
    "realizedPnl": "0.0",
    "createTime": "1700000000000",
}

_MISSING_ID_ITEM = {
    # No positionId field
    "instId": "BTC-USDT",
    "positions": "3.0",
    "averagePrice": "50000.0",
    "markPrice": "51000.0",
    "unrealizedPnl": "1.0",
    "realizedPnl": "0.0",
    "createTime": "1700000000000",
}


class TestGetPositionsFieldNames:
    @pytest.mark.asyncio
    async def test_zero_positions_field_skipped(self, exchange):
        """Items where 'positions' == 0 must be filtered out."""
        exchange.client.trading.get_positions.return_value = {
            "data": [_ZERO_POSITION_ITEM]
        }
        result = await exchange.get_positions("BTC-USDT")
        assert result == []

    @pytest.mark.asyncio
    async def test_nonzero_positions_field_returned(self, exchange):
        """Items with non-zero 'positions' field must be included."""
        exchange.client.trading.get_positions.return_value = {
            "data": [_VALID_POSITION_ITEM]
        }
        result = await exchange.get_positions("BTC-USDT")
        assert len(result) == 1
        assert result[0].id == "pos_001"

    @pytest.mark.asyncio
    async def test_missing_position_id_logs_warning(self, exchange, caplog):
        """Missing 'positionId' in response must trigger a WARNING log."""
        exchange.client.trading.get_positions.return_value = {
            "data": [_MISSING_ID_ITEM]
        }
        with caplog.at_level(logging.WARNING):
            result = await exchange.get_positions("BTC-USDT")

        assert any("positionId" in rec.message or "position" in rec.message.lower()
                   for rec in caplog.records if rec.levelno >= logging.WARNING), \
            "No WARNING logged when positionId is missing from response"

    @pytest.mark.asyncio
    async def test_missing_position_id_falls_back_to_empty_string(self, exchange):
        """When positionId is absent, position.id falls back to '' (documents behavior)."""
        exchange.client.trading.get_positions.return_value = {
            "data": [_MISSING_ID_ITEM]
        }
        result = await exchange.get_positions("BTC-USDT")
        assert len(result) == 1
        assert result[0].id == ""

    @pytest.mark.asyncio
    async def test_side_is_buy_for_positive_positions(self, exchange):
        """Positive 'positions' value → BUY side."""
        exchange.client.trading.get_positions.return_value = {
            "data": [_VALID_POSITION_ITEM]
        }
        result = await exchange.get_positions()
        assert result[0].side == Side.BUY

    @pytest.mark.asyncio
    async def test_side_is_sell_for_negative_positions(self, exchange):
        """Negative 'positions' value → SELL side."""
        short_item = dict(_VALID_POSITION_ITEM)
        short_item["positions"] = "-3.0"
        exchange.client.trading.get_positions.return_value = {
            "data": [short_item]
        }
        result = await exchange.get_positions()
        assert result[0].side == Side.SELL

    @pytest.mark.asyncio
    async def test_mixed_zero_and_nonzero_items(self, exchange):
        """Only non-zero position items are returned from a mixed response."""
        exchange.client.trading.get_positions.return_value = {
            "data": [_VALID_POSITION_ITEM, _ZERO_POSITION_ITEM, _VALID_POSITION_ITEM]
        }
        result = await exchange.get_positions()
        assert len(result) == 2  # Two non-zero items

    @pytest.mark.skip(reason="ISSUE-019 Inconclusive: actual BloFin API field names "
                             "('positions' vs 'pos'/'size') require a live response to verify")
    def test_blofin_live_positions_field_name(self):
        """Verify BloFin uses 'positions' (not 'pos'/'size') as the qty field."""
        pass
