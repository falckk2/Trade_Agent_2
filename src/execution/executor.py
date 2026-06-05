import asyncio
import logging

from src.core.enums import EventType, OrderStatus, OrderType, Side, SignalType
from src.core.events import Event, EventBus
from src.core.models import Order, Position, Signal
from src.exchange.interface import ITradingAccount
from src.execution.interface import IOrderExecutor

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.FAILED}


class OrderExecutor(IOrderExecutor):
    """Translates signals into orders and manages execution."""

    def __init__(
        self,
        exchange: ITradingAccount,
        event_bus: EventBus,
        fill_poll_interval: float = 0.5,
        fill_max_retries: int = 6,
    ) -> None:
        self._exchange = exchange
        self._event_bus = event_bus
        self._fill_poll_interval = fill_poll_interval
        self._fill_max_retries = fill_max_retries

    async def execute_signal(
        self, signal: Signal, quantity: float, symbol: str
    ) -> Order:
        if signal.signal_type == SignalType.LONG:
            side = Side.BUY
        elif signal.signal_type == SignalType.SHORT:
            side = Side.SELL
        else:
            raise ValueError(
                f"execute_signal() only handles LONG/SHORT signals; "
                f"got {signal.signal_type}. "
                f"Use close_position() for CLOSE signals; HOLD signals should be "
                f"filtered by the caller before reaching execute_signal()."
            )

        order = await self._exchange.place_order(
            symbol=symbol,
            side=side,
            order_type=signal.order_type,
            quantity=quantity,
            price=signal.limit_price,
        )
        order.strategy_name = signal.strategy_name

        await self._event_bus.publish(
            Event(
                event_type=EventType.ORDER_PLACED,
                payload={"order": order, "signal": signal},
            )
        )

        logger.info(
            "Order placed: %s %s %.4f %s (strategy: %s)",
            side.value,
            symbol,
            quantity,
            order.id,
            signal.strategy_name,
        )

        order = await self._await_fill(order, symbol)

        if order.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            await self._event_bus.publish(
                Event(
                    event_type=EventType.ORDER_FILLED,
                    payload={"order": order, "signal": signal},
                )
            )
            if order.status == OrderStatus.PARTIALLY_FILLED:
                logger.warning(
                    "Partial fill on %s: requested %.4f, filled %.4f — remainder cancelled",
                    order.id,
                    order.quantity,
                    order.filled_quantity,
                )
            else:
                logger.info(
                    "Order filled: %s %.4f @ %s",
                    order.id,
                    order.filled_quantity,
                    order.average_fill_price,
                )
        else:
            logger.warning(
                "Order %s did not fill (status=%s) — no ORDER_FILLED event published",
                order.id,
                order.status,
            )

        return order

    async def close_position(
        self,
        position: Position,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
    ) -> Order:
        close_side = Side.SELL if position.side == Side.BUY else Side.BUY

        order = await self._exchange.place_order(
            symbol=position.symbol,
            side=close_side,
            order_type=order_type,
            quantity=position.quantity,
            price=price,
        )
        order.strategy_name = position.strategy_name

        await self._event_bus.publish(
            Event(
                event_type=EventType.ORDER_PLACED,
                payload={"order": order, "position": position},
            )
        )

        logger.info(
            "Position close order: %s %s %.4f %s",
            close_side.value,
            position.symbol,
            position.quantity,
            order.id,
        )

        order = await self._await_fill(order, position.symbol)

        if order.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            await self._event_bus.publish(
                Event(
                    event_type=EventType.ORDER_FILLED,
                    payload={"order": order, "position": position},
                )
            )
            if order.status == OrderStatus.PARTIALLY_FILLED:
                logger.warning(
                    "Partial fill closing %s %s: requested %.4f, filled %.4f — remainder cancelled",
                    position.symbol,
                    order.id,
                    order.quantity,
                    order.filled_quantity,
                )
        else:
            logger.warning(
                "Close order %s did not fill (status=%s)",
                order.id,
                order.status,
            )

        return order

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        return await self._exchange.cancel_order(order_id, symbol)

    async def get_active_orders(self) -> list[Order]:
        return await self._exchange.get_open_orders()

    async def _await_fill(self, order: Order, symbol: str) -> Order:
        """Poll the exchange until the order reaches a terminal state.

        If the order is partially filled when retries are exhausted, the
        unfilled remainder is cancelled and the order is returned with
        status PARTIALLY_FILLED.

        If the order is still non-terminal after all retries (e.g. still
        PENDING), we attempt to cancel it to avoid leaving a live order on
        the exchange unmonitored.
        """
        for attempt in range(self._fill_max_retries):
            updated = await self._exchange.get_order(order.id, symbol)
            if updated is None:
                # Order not found yet — may be a transient propagation lag
                # (market orders fill instantly but history API has a small
                # delay). Retry rather than aborting immediately.
                await asyncio.sleep(self._fill_poll_interval)
                continue
            order = updated
            if order.status in _TERMINAL_STATUSES:
                return order
            if order.status == OrderStatus.PARTIALLY_FILLED:
                cancelled = await self._exchange.cancel_order(order.id, symbol)
                if not cancelled:
                    logger.warning(
                        "Failed to cancel remainder of partially filled order %s on %s",
                        order.id,
                        symbol,
                    )
                return order
            await asyncio.sleep(self._fill_poll_interval)

        # Retries exhausted — before cancelling, check whether a position
        # actually opened. A market order may have filled before the history
        # API propagated the state, making get_order() transiently return
        # None. If a matching position exists the order filled successfully.
        if order.status not in _TERMINAL_STATUSES:
            try:
                positions = await self._exchange.get_positions(symbol)
                expected_side = order.side
                if any(p.symbol == symbol and p.side == expected_side for p in positions):
                    logger.info(
                        "Order %s not found in history but position exists — "
                        "market order filled before history propagated; marking FILLED",
                        order.id,
                    )
                    # One final history lookup now that the position confirms a
                    # fill — by this point propagation lag should be resolved
                    # and we can retrieve the actual fill price.
                    try:
                        filled = await self._exchange.get_order(order.id, symbol)
                        if filled is not None:
                            order = filled
                    except Exception:
                        logger.debug("Could not fetch fill details for %s", order.id)
                    order.status = OrderStatus.FILLED
                    return order
            except Exception:
                logger.exception("Could not verify fill via position check for %s", order.id)

            logger.warning(
                "Order %s did not fill within %d polls (status=%s) — cancelling",
                order.id,
                self._fill_max_retries,
                order.status,
            )
            try:
                await self._exchange.cancel_order(order.id, symbol)
                order.status = OrderStatus.CANCELLED
            except Exception:
                logger.exception(
                    "Failed to cancel timed-out order %s on %s", order.id, symbol
                )
                order.status = OrderStatus.FAILED

        return order
