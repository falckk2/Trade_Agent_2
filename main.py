"""Trade Agent 2 — Application entry point."""

import asyncio
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # Load .env file if present

import yaml

from src.core.enums import TimeFrame
from src.core.events import EventBus
from src.dashboard.app import create_app, run_dashboard
from src.data.provider import MarketDataProvider
from src.engine.trading_engine import TradingEngine
from src.exchange.blofin_exchange import BloFinExchange
from src.execution.executor import OrderExecutor
from src.portfolio.manager import PortfolioManager
from src.risk.manager import RiskManager
from src.exchange.blofin_websocket import BloFinWebSocket
from src.notifications.telegram import TelegramNotifier
from src.strategies.factory import StrategyFactory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_TIMEFRAME_LOOKUP: dict[str, TimeFrame] = {tf.value: tf for tf in TimeFrame}


def load_config(path: str = "config/default.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_strategies_config(path: str = "config/strategies.yaml") -> list[dict]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("strategies", [])


def build_components(config: dict):
    """Construct all components via dependency injection."""
    # API keys from environment
    api_key = os.environ.get("BLOFIN_API_KEY", "")
    secret = os.environ.get("BLOFIN_SECRET", "")
    passphrase = os.environ.get("BLOFIN_PASSPHRASE", "")

    if not all([api_key, secret, passphrase]):
        logger.warning(
            "BloFin API credentials not set. "
            "Set BLOFIN_API_KEY, BLOFIN_SECRET, BLOFIN_PASSPHRASE env vars."
        )

    exchange_cfg = config.get("exchange", {})
    risk_cfg = config.get("risk", {})
    engine_cfg = config.get("engine", {})
    data_cfg = config.get("data", {})

    # Event bus
    event_bus = EventBus()

    # Operator alerts (FABLE-011) — no-op unless TELEGRAM_BOT_TOKEN and
    # TELEGRAM_CHAT_ID are set in the environment/.env.
    notifier = TelegramNotifier(
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    notifier.attach(event_bus)

    # Exchange
    exchange = BloFinExchange(
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        demo_mode=exchange_cfg.get("demo_mode", True),
    )

    # Data provider
    data_provider = MarketDataProvider(exchange=exchange, event_bus=event_bus)

    # Risk manager — baseline_file persists the equity high-watermark across
    # restarts so the drawdown window is not silently reset each time the bot
    # is restarted. Delete data/initial_equity.json to deliberately reset it
    # (required after external deposits/withdrawals).
    data_dir = Path(data_cfg.get("data_dir", "data"))
    risk_manager = RiskManager(
        max_position_pct=risk_cfg.get("max_position_pct", 0.05),
        max_exposure_pct=risk_cfg.get("max_exposure_pct", 0.20),
        max_drawdown_pct=risk_cfg.get("max_drawdown_pct", 0.10),
        default_stop_loss_pct=risk_cfg.get("default_stop_loss_pct", 0.02),
        default_take_profit_pct=risk_cfg.get("default_take_profit_pct", 0.04),
        min_signal_strength=risk_cfg.get("min_signal_strength", 0.3),
        baseline_file=data_dir / "initial_equity.json",
        event_bus=event_bus,
    )

    # Order executor
    order_executor = OrderExecutor(exchange=exchange, event_bus=event_bus)

    # Portfolio manager
    portfolio_manager = PortfolioManager(
        data_dir=data_cfg.get("data_dir", "data"),
        event_bus=event_bus,
    )

    # Timeframe
    tf_str = engine_cfg.get("timeframe", "5m")
    timeframe = _TIMEFRAME_LOOKUP.get(tf_str, TimeFrame.M5)

    # Symbols
    symbols = exchange_cfg.get("symbols", ["BTC-USDT"])

    # Trading engine
    engine = TradingEngine(
        exchange=exchange,
        data_provider=data_provider,
        risk_manager=risk_manager,
        order_executor=order_executor,
        portfolio_manager=portfolio_manager,
        event_bus=event_bus,
        symbols=symbols,
        timeframe=timeframe,
        candle_limit=engine_cfg.get("candle_limit", 200),
    )

    # WebSocket client for real-time candle updates (wires CANDLE_UPDATE events
    # into MarketDataProvider._on_candle_update so the cache stays live).
    ws_client = BloFinWebSocket(
        event_bus=event_bus,
        demo_mode=exchange_cfg.get("demo_mode", True),
    )

    return {
        "engine": engine,
        "exchange": exchange,
        "data_provider": data_provider,
        "portfolio_manager": portfolio_manager,
        "event_bus": event_bus,
        "symbols": symbols,
        "timeframe": timeframe,
        "ws_client": ws_client,
        "config": config,
    }


def main():
    # Load configs
    config = load_config()
    strategies_config = load_strategies_config()

    # Build components
    components = build_components(config)
    engine: TradingEngine = components["engine"]

    # Create strategies
    factory = StrategyFactory()
    strategies = factory.create_from_config(strategies_config)

    # Register strategies with engine
    for entry in strategies_config:
        strategy = factory.get_instance(entry["name"])
        if strategy:
            engine.add_strategy(
                strategy,
                symbols=entry.get("symbols"),
                weight=float(entry.get("weight", 1.0)),
            )

    strategy_names = [s.name for s in strategies]

    # Create dashboard
    dash_cfg = config.get("dashboard", {})
    app = create_app(
        portfolio_manager=components["portfolio_manager"],
        data_provider=components["data_provider"],
        symbols=components["symbols"],
        strategy_names=strategy_names,
        timeframe=components["timeframe"],
        engine=engine,
        refresh_interval_ms=dash_cfg.get("refresh_interval_ms", 2000),
    )

    # Start dashboard in separate thread
    dash_thread = threading.Thread(
        target=run_dashboard,
        args=(app,),
        kwargs={
            "host": dash_cfg.get("host", "127.0.0.1"),
            "port": dash_cfg.get("port", 8050),
        },
        daemon=True,
    )
    dash_thread.start()
    logger.info("Dashboard started")

    ws_client: BloFinWebSocket = components["ws_client"]

    # Run trading engine
    async def run_engine():
        # Create stop event inside the loop so it's bound to the right loop
        stop_event = asyncio.Event()

        loop = asyncio.get_running_loop()

        # Use asyncio-native signal handlers so they integrate cleanly with the
        # event loop and do not conflict with KeyboardInterrupt (ISSUE-024).
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        interval = config.get("engine", {}).get("interval_seconds", 60)
        tf_str = config.get("engine", {}).get("timeframe", "5m")
        timeframe = _TIMEFRAME_LOOKUP.get(tf_str, TimeFrame.M5)

        # Connect WebSocket and subscribe to candles for all symbols (ISSUE-006)
        try:
            await ws_client.connect()
            for sym in components["symbols"]:
                await ws_client.subscribe_candles(sym, timeframe)
            ws_task = asyncio.create_task(ws_client.listen())
            logger.info("WebSocket candle feed started for %s", components["symbols"])
        except Exception:
            logger.exception(
                "Failed to start WebSocket — candle cache will refresh via REST only"
            )
            ws_task = None

        engine_task = asyncio.create_task(engine.start(interval_seconds=interval))

        await stop_event.wait()
        logger.info("Shutdown signal received — stopping engine")
        # stop() waits for any in-flight tick to drain before closing positions
        # (FABLE-003); start() then returns naturally, so await — don't cancel.
        await engine.stop()
        try:
            await engine_task
        except Exception:
            logger.exception("Engine task ended with error")
        if ws_task is not None:
            await ws_client.disconnect()
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("WebSocket task ended with error", exc_info=True)

    try:
        asyncio.run(run_engine())
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        # No explicit save needed here: every trade is appended to CSV at
        # record time (FABLE-004) and engine.stop() does a compacting full
        # rewrite during shutdown (FABLE-003).
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
