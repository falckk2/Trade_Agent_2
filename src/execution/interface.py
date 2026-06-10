from abc import ABC, abstractmethod

from src.core.enums import OrderType
from src.core.models import Order, Position, Signal


class IOrderExecutor(ABC):
    """Abstract interface for order execution."""

    @abstractmethod
    async def execute_signal(
        self,
        signal: Signal,
        quantity: float,
        symbol: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Order:
        ...

    @abstractmethod
    async def close_position(
        self,
        position: Position,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
    ) -> Order:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        ...

    @abstractmethod
    async def get_active_orders(self) -> list[Order]:
        ...
