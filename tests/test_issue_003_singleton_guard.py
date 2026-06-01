"""
ISSUE-003: BloFinExchange raises RuntimeError when a second instance is constructed
while one is already active, preventing concurrent multi-instance URL corruption.

Tests verify:
1. First construction succeeds
2. Second construction while first is active raises RuntimeError
3. After disconnect(), a new instance can be created (counter resets)
4. Counter underflow protection (calling disconnect() twice is safe)
"""

import pytest
from unittest.mock import MagicMock, patch

import src.exchange.blofin_exchange as exchange_module
from src.exchange.blofin_exchange import BloFinExchange


@pytest.fixture(autouse=True)
def reset_active_instances():
    """Ensure the singleton counter is 0 before each test."""
    exchange_module._active_instances = 0
    yield
    exchange_module._active_instances = 0


class TestSingletonGuard:
    def test_first_instance_constructs_successfully(self):
        exc = BloFinExchange(api_key="k", secret="s", passphrase="p", demo_mode=False)
        assert exc is not None

    def test_second_instance_raises_runtime_error(self):
        _first = BloFinExchange(api_key="k", secret="s", passphrase="p", demo_mode=False)
        with pytest.raises(RuntimeError, match="Only one BloFinExchange"):
            BloFinExchange(api_key="k2", secret="s2", passphrase="p2", demo_mode=False)

    @pytest.mark.asyncio
    async def test_after_disconnect_new_instance_allowed(self):
        exc = BloFinExchange(api_key="k", secret="s", passphrase="p", demo_mode=False)
        # Simulate disconnect without real network (just call the method)
        exc._client = MagicMock()
        await exc.disconnect()

        # Counter should now be back to 0
        assert exchange_module._active_instances == 0

        # New instance should succeed
        exc2 = BloFinExchange(api_key="k", secret="s", passphrase="p", demo_mode=False)
        assert exc2 is not None

    @pytest.mark.asyncio
    async def test_disconnect_twice_does_not_underflow(self):
        """Calling disconnect() twice must not decrement counter below 0."""
        exc = BloFinExchange(api_key="k", secret="s", passphrase="p", demo_mode=False)
        exc._client = MagicMock()

        await exc.disconnect()
        await exc.disconnect()  # Second call must not underflow

        assert exchange_module._active_instances == 0

    def test_active_instances_incremented_on_construction(self):
        assert exchange_module._active_instances == 0
        _exc = BloFinExchange(api_key="k", secret="s", passphrase="p", demo_mode=False)
        assert exchange_module._active_instances == 1
