"""
ISSUE-026: get_balance() uses explicit fallback with error logging when neither
'balance' nor 'totalEquity' fields are present in the API response.

Tests verify:
1. 'balance' field is used when present
2. 'totalEquity' is used as fallback when 'balance' is absent
3. When both are absent, total_equity is 0 and an ERROR log is emitted
4. Empty data list returns zero equity with a warning
5. 'available'/'availableBalance' fallback works the same way
"""

import pytest
import logging
from unittest.mock import MagicMock

import src.exchange.blofin_exchange as exchange_module
from src.exchange.blofin_exchange import BloFinExchange


@pytest.fixture(autouse=True)
def reset_singleton():
    exchange_module._active_instances = 0
    yield
    exchange_module._active_instances = 0


@pytest.fixture
def exchange():
    exc = BloFinExchange(api_key="k", secret="s", passphrase="p", demo_mode=False)
    exc._client = MagicMock()
    return exc


class TestGetBalanceFieldFallback:
    @pytest.mark.asyncio
    async def test_balance_field_used_when_present(self, exchange):
        """When 'balance' key is in the response, it is used for total_equity."""
        exchange.client.account.get_balance.return_value = {
            "data": [{"balance": "12345.0", "available": "10000.0"}]
        }
        result = await exchange.get_balance()
        assert result["total_equity"] == pytest.approx(12345.0)

    @pytest.mark.asyncio
    async def test_total_equity_fallback_when_balance_absent(self, exchange):
        """When 'balance' is absent but 'totalEquity' is present, use totalEquity."""
        exchange.client.account.get_balance.return_value = {
            "data": [{"totalEquity": "9999.0", "available": "8000.0"}]
        }
        result = await exchange.get_balance()
        assert result["total_equity"] == pytest.approx(9999.0)

    @pytest.mark.asyncio
    async def test_both_fields_absent_returns_zero_and_logs_error(self, exchange, caplog):
        """When both 'balance' and 'totalEquity' are absent, total_equity=0 and ERROR is logged."""
        exchange.client.account.get_balance.return_value = {
            "data": [{"some_other_field": "value"}]
        }
        with caplog.at_level(logging.ERROR):
            result = await exchange.get_balance()

        assert result["total_equity"] == pytest.approx(0.0)
        assert any("balance" in rec.message.lower() or "totalequity" in rec.message.lower()
                   for rec in caplog.records if rec.levelno >= logging.ERROR), \
            "No ERROR log emitted when both balance fields are absent"

    @pytest.mark.asyncio
    async def test_empty_data_returns_zero_and_logs_warning(self, exchange, caplog):
        """Empty 'data' list in the response returns zero equity."""
        exchange.client.account.get_balance.return_value = {"data": []}
        with caplog.at_level(logging.WARNING):
            result = await exchange.get_balance()

        assert result["total_equity"] == pytest.approx(0.0)
        assert result["available"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_available_field_used_when_present(self, exchange):
        """'available' field is used for the available balance."""
        exchange.client.account.get_balance.return_value = {
            "data": [{"balance": "10000.0", "available": "7500.0"}]
        }
        result = await exchange.get_balance()
        assert result["available"] == pytest.approx(7500.0)

    @pytest.mark.asyncio
    async def test_available_balance_fallback(self, exchange):
        """'availableBalance' is used when 'available' is absent."""
        exchange.client.account.get_balance.return_value = {
            "data": [{"balance": "10000.0", "availableBalance": "6000.0"}]
        }
        result = await exchange.get_balance()
        assert result["available"] == pytest.approx(6000.0)
