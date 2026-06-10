"""Trade performance statistics shared by live tracking and backtesting.

Used by PortfolioManager.get_performance_stats (FABLE-012) and the
backtester (FABLE-010) so both report directly comparable numbers.
"""

from src.core.models import TradeRecord


def compute_performance_stats(trades: list[TradeRecord]) -> dict:
    """Compute performance statistics from a list of closed trades.

    TradeRecord.pnl is net of fees, so win/loss classification and all P&L
    sums are after-fee numbers. profit_factor is gross wins / gross losses;
    inf when there are wins but no losses, 0.0 with no trades.
    """
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net_pnl = gross_profit - gross_loss
    count = len(trades)

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    max_consec_losses = 0
    streak = 0
    for t in trades:  # expects close order
        if t.pnl <= 0:
            streak += 1
            max_consec_losses = max(max_consec_losses, streak)
        else:
            streak = 0

    return {
        "trade_count": count,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / count if count else 0.0,
        "net_pnl": net_pnl,
        "total_fees": sum(t.fee for t in trades),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "avg_win": gross_profit / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "expectancy": net_pnl / count if count else 0.0,
        "max_consecutive_losses": max_consec_losses,
        "avg_duration_seconds": (
            sum(t.duration_seconds for t in trades) / count if count else 0.0
        ),
    }
