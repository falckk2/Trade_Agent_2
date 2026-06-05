import logging
import threading
from datetime import datetime, timezone

import blofin.constants
import blofin.utils
from blofin.client import BloFinClient

from src.core.enums import (
    OrderStatus,
    OrderType,
    PositionStatus,
    Side,
    TimeFrame,
)
from src.core.models import Candle, Order, Position
from src.exchange.interface import IExchange

logger = logging.getLogger(__name__)

_DEMO_REST_URL = "https://demo-trading-openapi.blofin.com"
_PROD_REST_URL = "https://openapi.blofin.com"

# Protects global blofin.constants / blofin.utils URL mutation.
# Because the URL is a process-wide module global, only one BloFinExchange
# may exist at a time.  _active_instances enforces this constraint.
_url_patch_lock = threading.Lock()
_active_instances: int = 0

_TIMEFRAME_MAP: dict[TimeFrame, str] = {
    TimeFrame.M1: "1m",
    TimeFrame.M5: "5m",
    TimeFrame.M15: "15m",
    TimeFrame.M30: "30m",
    TimeFrame.H1: "1H",
    TimeFrame.H4: "4H",
    TimeFrame.D1: "1D",
}

_SIDE_MAP: dict[Side, str] = {
    Side.BUY: "buy",
    Side.SELL: "sell",
}

_ORDER_TYPE_MAP: dict[OrderType, str] = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
}


def _parse_order_status(status: str) -> OrderStatus:
    mapping = {
        "live": OrderStatus.PENDING,
        "partially_filled": OrderStatus.PARTIALLY_FILLED,
        "filled": OrderStatus.FILLED,
        "cancelled": OrderStatus.CANCELLED,
        "canceled": OrderStatus.CANCELLED,
    }
    return mapping.get(status.lower(), OrderStatus.PENDING)


def _ts_to_datetime(ts: str | int | float) -> datetime:
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
    except (ValueError, OSError):
        return datetime.now(tz=timezone.utc)


class BloFinExchange(IExchange):
    """BloFin exchange implementation using the official SDK.

    Only ONE instance may exist per process because demo mode patches a
    process-wide module global (``blofin.constants.REST_API_URL``).
    Attempting to construct a second instance raises ``RuntimeError``.
    """

    def __init__(
        self,
        api_key: str,
        secret: str,
        passphrase: str,
        demo_mode: bool = True,
    ) -> None:
        global _active_instances
        with _url_patch_lock:
            if _active_instances > 0:
                raise RuntimeError(
                    "Only one BloFinExchange may be active per process. "
                    "The BloFin SDK uses a process-wide URL constant for demo mode; "
                    "multiple instances would corrupt each other's routing."
                )
            _active_instances += 1

        self._api_key = api_key
        self._secret = secret
        self._passphrase = passphrase
        self._demo_mode = demo_mode
        self._client: BloFinClient | None = None
        self._original_rest_url: str | None = None
        # instrument_id -> {contract_value, lot_size, min_size}
        self._instrument_specs: dict[str, dict[str, float]] = {}

    async def connect(self) -> None:
        # Switch REST_API_URL for demo mode (lock protects global module state)
        if self._demo_mode:
            with _url_patch_lock:
                self._original_rest_url = blofin.constants.REST_API_URL
                blofin.constants.REST_API_URL = _DEMO_REST_URL
                blofin.utils.REST_API_URL = _DEMO_REST_URL

        self._client = BloFinClient(
            api_key=self._api_key,
            api_secret=self._secret,
            passphrase=self._passphrase,
        )
        self._load_instrument_specs()
        logger.info(
            "Connected to BloFin (%s mode)",
            "demo" if self._demo_mode else "live",
        )

    def _load_instrument_specs(self) -> None:
        """Cache contract value, lot size, and min size for all SWAP instruments."""
        try:
            resp = self.client.public.get_instruments(inst_type="SWAP")
            for inst in resp.get("data", []):
                self._instrument_specs[inst["instId"]] = {
                    "contract_value": float(inst.get("contractValue", 1)),
                    "lot_size": float(inst.get("lotSize", 1)),
                    "min_size": float(inst.get("minSize", 1)),
                }
            logger.info("Loaded specs for %d instruments", len(self._instrument_specs))
        except Exception:
            logger.exception("Failed to load instrument specs")

    def _to_contracts(self, symbol: str, base_quantity: float) -> float:
        """Convert a quantity in base currency to contracts, rounded to lot size."""
        spec = self._instrument_specs.get(symbol)
        if not spec:
            logger.error(
                "No instrument spec for %s — cannot convert to contracts. "
                "Order will be skipped. Check that instrument specs loaded correctly.",
                symbol,
            )
            return 0.0
        contracts = base_quantity / spec["contract_value"]
        lot = spec["lot_size"]
        rounded = round(round(contracts / lot) * lot, 10)
        return max(rounded, spec["min_size"])

    async def disconnect(self) -> None:
        global _active_instances
        # Restore original URL and release the singleton slot
        with _url_patch_lock:
            if self._original_rest_url is not None:
                blofin.constants.REST_API_URL = self._original_rest_url
                blofin.utils.REST_API_URL = self._original_rest_url
                self._original_rest_url = None
            _active_instances = max(0, _active_instances - 1)
        self._client = None
        logger.info("Disconnected from BloFin")

    @property
    def client(self) -> BloFinClient:
        if self._client is None:
            raise RuntimeError("Exchange not connected. Call connect() first.")
        return self._client

    async def get_balance(self) -> dict[str, float]:
        resp = self.client.account.get_balance(account_type="futures")
        data = resp.get("data", [])
        if not data:
            logger.warning("get_balance returned empty data — total_equity will be 0")
            return {"total_equity": 0.0, "available": 0.0}
        acct = data[0]
        balance_val = acct.get("balance") or acct.get("totalEquity")
        if balance_val is None:
            logger.error(
                "get_balance: response missing 'balance'/'totalEquity' fields — "
                "available keys: %s. total_equity will be 0. "
                "Check BloFin API docs and verify account_type='futures'.",
                list(acct.keys()),
            )
            balance_val = 0
        available_val = acct.get("available") or acct.get("availableBalance") or 0
        return {
            "total_equity": float(balance_val),
            "available": float(available_val),
        }

    async def place_order(
        self,
        symbol: str,
        side: Side,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
    ) -> Order:
        order_price = price if price is not None else 0
        contracts = self._to_contracts(symbol, quantity)
        if contracts <= 0:
            raise ValueError(
                f"Cannot place order for {symbol}: contract size resolved to {contracts}. "
                "Check that instrument specs loaded correctly."
            )
        logger.info(
            "Placing order: %s %s %.4f base → %.1f contracts",
            _SIDE_MAP[side], symbol, quantity, contracts,
        )
        resp = self.client.trading.place_order(
            inst_id=symbol,
            margin_mode="cross",
            position_side="net",
            side=_SIDE_MAP[side],
            order_type=_ORDER_TYPE_MAP.get(order_type, "market"),
            price=order_price,
            size=contracts,
        )
        logger.debug("place_order raw response for %s: %s", symbol, resp)
        data = resp.get("data", [{}])
        if isinstance(data, list) and data:
            order_id = data[0].get("orderId", "")
        else:
            order_id = ""

        if not order_id:
            raise RuntimeError(
                f"place_order for {symbol} returned no orderId — response: {resp}"
            )

        return Order(
            id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            status=OrderStatus.PENDING,
        )

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        resp = self.client.trading.cancel_order(
            inst_id=symbol, order_id=order_id
        )
        data = resp.get("data", [{}])
        if isinstance(data, list) and data:
            return data[0].get("orderId", "") == order_id
        return False

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        resp = self.client.trading.get_active_orders(inst_id=symbol)
        return [self._parse_order(item) for item in resp.get("data", [])]

    def _parse_order(self, item: dict) -> Order:
        """Build an Order dataclass from a raw SDK order dict."""
        inst_id = item.get("instId", "")
        contract_value = self._instrument_specs.get(inst_id, {}).get("contract_value", 1.0)

        # BloFin returns filledSize in contracts; convert to base units for
        # consistency with Order.quantity (which callers compute in base units).
        filled_contracts = float(item.get("filledSize", item.get("accFillSz", 0)))
        filled_base = filled_contracts * contract_value

        # Market orders carry price='0'; treat that as no limit price (None).
        raw_price = item.get("price")
        limit_price = float(raw_price) if raw_price and raw_price != "0" else None

        return Order(
            id=item.get("orderId", ""),
            symbol=inst_id,
            side=Side.BUY if item.get("side") == "buy" else Side.SELL,
            order_type=OrderType.LIMIT
            if item.get("orderType") == "limit"
            else OrderType.MARKET,
            quantity=float(item.get("size", 0)),
            price=limit_price,
            status=_parse_order_status(item.get("state", "live")),
            filled_quantity=filled_base,
            average_fill_price=float(item["averagePrice"]) if item.get("averagePrice") else None,
            fee=abs(float(item.get("fee") or 0)),
            created_at=_ts_to_datetime(item.get("createTime", 0)),
        )

    async def get_order(self, order_id: str, symbol: str) -> Order | None:
        """Look up a single order by ID.

        BloFin SDK v0.5.0 does not expose trading.get_order().  We search
        active orders first (fast path for pending/partially-filled), then
        fall back to order history (filled/cancelled).
        """
        # Fast path: still active
        try:
            active_resp = self.client.trading.get_active_orders(inst_id=symbol)
            for item in active_resp.get("data", []):
                if item.get("orderId") == order_id:
                    return self._parse_order(item)
        except Exception:
            logger.exception("get_order: error fetching active orders for %s", symbol)

        # Slow path: already completed
        try:
            history_resp = self.client.trading.get_order_history(
                inst_id=symbol, limit=100
            )
            for item in history_resp.get("data", []):
                if item.get("orderId") == order_id:
                    return self._parse_order(item)
        except Exception:
            logger.exception("get_order: error fetching order history for %s", symbol)

        logger.warning("get_order: order %s not found for symbol %s", order_id, symbol)
        return None

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        resp = self.client.trading.get_positions(inst_id=symbol)
        positions = []
        for item in resp.get("data", []):
            qty_contracts = float(item.get("positions", 0))
            if qty_contracts == 0:
                continue
            inst_id = item.get("instId", "")
            contract_value = self._instrument_specs.get(inst_id, {}).get("contract_value", 1.0)
            qty_base = abs(qty_contracts) * contract_value
            side = Side.BUY if qty_contracts > 0 else Side.SELL
            position_id = item.get("positionId", "")
            if not position_id:
                logger.warning(
                    "get_positions: 'positionId' missing from response item for %s "
                    "— available keys: %s. Position flip detection will not work correctly.",
                    inst_id, list(item.keys()),
                )
            positions.append(
                Position(
                    id=position_id,
                    symbol=inst_id,
                    side=side,
                    entry_price=float(item.get("averagePrice", 0)),
                    current_price=float(item.get("markPrice", 0)),
                    quantity=qty_base,
                    unrealized_pnl=float(item.get("unrealizedPnl", 0)),
                    realized_pnl=float(item.get("realizedPnl", 0)),
                    status=PositionStatus.OPEN,
                    opened_at=_ts_to_datetime(item.get("createTime", 0)),
                )
            )
        return positions

    async def get_candles(
        self, symbol: str, timeframe: TimeFrame, limit: int = 200
    ) -> list[Candle]:
        bar = _TIMEFRAME_MAP.get(timeframe, "5m")
        resp = self.client.public.get_candlesticks(
            inst_id=symbol, bar=bar, limit=limit
        )
        candles = []
        for item in resp.get("data", []):
            candles.append(
                Candle(
                    timestamp=_ts_to_datetime(item[0]),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                )
            )
        # API returns newest first; reverse to chronological order
        candles.reverse()
        return candles

    async def get_ticker(self, symbol: str) -> dict[str, float]:
        resp = self.client.public.get_tickers(inst_id=symbol)
        data = resp.get("data", [])
        if not data:
            return {}
        tick = data[0]
        return {
            "last": float(tick.get("last", 0)),
            "bid": float(tick.get("bidPrice", 0)),
            "ask": float(tick.get("askPrice", 0)),
            "volume_24h": float(tick.get("volume24h", 0)),
        }
