from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    TAKE_PROFIT = "take_profit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SignalType(str, Enum):
    LONG = "long"
    SHORT = "short"
    CLOSE = "close"
    HOLD = "hold"


class TimeFrame(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1H"
    H4 = "4H"
    D1 = "1D"


class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class AggregationMode(str, Enum):
    UNANIMOUS = "unanimous"
    MAJORITY = "majority"
    WEIGHTED = "weighted"


class EventType(str, Enum):
    CANDLE_UPDATE = "candle_update"
    SIGNAL_GENERATED = "signal_generated"
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    PORTFOLIO_UPDATE = "portfolio_update"
    # Critical operator-facing conditions (drawdown halt, failed shutdown
    # close, reconnect storm). Payload: {"level": "critical"|"warning",
    # "message": str} (FABLE-011).
    ALERT = "alert"
