from abc import ABC, abstractmethod

from src.core.enums import OrderType, Side, TimeFrame
from src.core.models import Candle, Order, Position


class IMarketDataSource(ABC):
    """Read-only market data — candles, tickers. No authentication required."""

    @abstractmethod
    async def get_candles(
        self, symbol: str, timeframe: TimeFrame, limit: int = 200
    ) -> list[Candle]:
        ...

    @abstractmethod
    async def get_ticker(self, symbol: str) -> dict[str, float]:
        ...


class ITradingAccount(ABC):
    """Authenticated trading operations — orders, positions, balance."""

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @abstractmethod
    async def get_balance(self) -> dict[str, float]:
        ...

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: Side,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
    ) -> Order:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        ...

    @abstractmethod
    async def get_order(self, order_id: str, symbol: str) -> Order | None:
        ...

    @abstractmethod
    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        ...


class IExchange(IMarketDataSource, ITradingAccount):
    """Full exchange interface — combines market data and trading operations."""
