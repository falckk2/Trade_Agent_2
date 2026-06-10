"""One-off live verification of FABLE-001 against the BloFin DEMO account.

Places a minimum-size BTC-USDT market BUY with attached stop-loss/take-profit,
verifies the TP/SL trigger order registered on the exchange, then closes the
position and verifies the trigger was cancelled. Demo mode only — refuses to
run otherwise.

Usage:
    python scripts/verify_tpsl_demo.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.core.enums import OrderType, Side
from src.exchange.blofin_exchange import BloFinExchange

SYMBOL = "BTC-USDT"


async def main() -> int:
    exchange = BloFinExchange(
        api_key=os.environ["BLOFIN_API_KEY"],
        secret=os.environ["BLOFIN_SECRET"],
        passphrase=os.environ["BLOFIN_PASSPHRASE"],
        demo_mode=True,
    )
    await exchange.connect()
    ok = True
    try:
        ticker = await exchange.get_ticker(SYMBOL)
        price = ticker["last"]
        spec = exchange._instrument_specs[SYMBOL]
        qty = spec["min_size"] * spec["contract_value"]  # smallest possible order
        stop_loss = price * 0.98
        take_profit = price * 1.04
        print(f"price={price:,.1f} qty={qty} sl={stop_loss:,.1f} tp={take_profit:,.1f}")

        # 1. Entry with attached TP/SL
        order = await exchange.place_order(
            symbol=SYMBOL, side=Side.BUY, order_type=OrderType.MARKET,
            quantity=qty, stop_loss=stop_loss, take_profit=take_profit,
        )
        print(f"[1] order placed: id={order.id}")
        await asyncio.sleep(2)

        filled = await exchange.get_order(order.id, SYMBOL)
        print(f"[2] order status={filled.status.value if filled else 'NOT FOUND'} "
              f"fill={filled.average_fill_price if filled else None}")

        # 2. Verify the TP/SL trigger registered on the exchange
        resp = await exchange._call(
            exchange.client.trading.get_active_tpsl_orders, inst_id=SYMBOL
        )
        tpsl = resp.get("data", [])
        print(f"[3] active TP/SL orders: {len(tpsl)}")
        for t in tpsl:
            print(f"    tpslId={t.get('tpslId')} sl={t.get('slTriggerPrice')} "
                  f"tp={t.get('tpTriggerPrice')} size={t.get('size')} state={t.get('state')}")
        if not tpsl:
            ok = False
            print("    FAIL: no TP/SL order found — attached triggers were not accepted")

        # 3. Close the position and cancel the triggers (mirrors executor cleanup)
        positions = await exchange.get_positions(SYMBOL)
        for pos in positions:
            close = await exchange.place_order(
                symbol=SYMBOL,
                side=Side.SELL if pos.side == Side.BUY else Side.BUY,
                order_type=OrderType.MARKET,
                quantity=pos.quantity,
            )
            print(f"[4] close order placed: id={close.id}")
        await asyncio.sleep(2)
        cancelled = await exchange.cancel_tpsl_orders(SYMBOL)
        print(f"[5] cancel_tpsl_orders → {cancelled} cancelled")

        # 4. Final state must be flat with no triggers left
        positions = await exchange.get_positions(SYMBOL)
        resp = await exchange._call(
            exchange.client.trading.get_active_tpsl_orders, inst_id=SYMBOL
        )
        leftover = resp.get("data", [])
        print(f"[6] final: positions={len(positions)} tpsl_orders={len(leftover)}")
        if positions or leftover:
            ok = False
            print("    FAIL: not flat after cleanup — check the demo account manually")
    finally:
        await exchange.disconnect()

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
