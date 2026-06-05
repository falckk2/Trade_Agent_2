from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(tz=timezone.utc)


# Keep private alias for backward compatibility with dataclass field defaults
_utcnow = utcnow

from src.core.enums import (
    OrderStatus,
    OrderType,
    PositionStatus,
    Side,
    SignalType,
)


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Signal:
    signal_type: SignalType
    symbol: str
    strength: float  # 0.0 to 1.0
    strategy_name: str
    timestamp: datetime
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Order:
    id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity: float
    price: float | None
    status: OrderStatus
    strategy_name: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    filled_quantity: float = 0.0
    filled_at: datetime | None = None
    average_fill_price: float | None = None
    fee: float = 0.0


@dataclass
class Position:
    id: str
    symbol: str
    side: Side
    entry_price: float
    current_price: float
    quantity: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    status: PositionStatus = PositionStatus.OPEN
    strategy_name: str = ""
    opened_at: datetime = field(default_factory=_utcnow)
    closed_at: datetime | None = None


@dataclass(frozen=True)
class TradeRecord:
    id: str
    symbol: str
    side: Side
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float       # net of fees
    strategy_name: str
    opened_at: datetime
    closed_at: datetime
    duration_seconds: float = 0.0
    fee: float = 0.0  # total fee for this trade (entry + exit)


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: datetime
    total_equity: float
    unrealized_pnl: float
    realized_pnl: float
    positions: list[Position] = field(default_factory=list)
    strategy_pnl: dict[str, float] = field(default_factory=dict)
    # ISSUE-022: split P&L into realized (closed trades) and unrealized (open positions)
    # so the equity curve can show stable realized performance without mark-price noise.
    strategy_pnl_realized: dict[str, float] = field(default_factory=dict)
    strategy_pnl_unrealized: dict[str, float] = field(default_factory=dict)
