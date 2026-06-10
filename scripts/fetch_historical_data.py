"""Fetch historical OHLCV candle data from BloFin and save to CSV.

Data is stored under data/historical/<symbol>/<timeframe>.csv and is suitable
for ML model training, validation, and backtesting.

The script pages backwards through BloFin's public candlestick API (no API
credentials required) using the `before` cursor parameter, collecting up to
`--days` calendar days of history per symbol/timeframe combination.

On subsequent runs it performs an incremental update: only candles newer than
the latest row already on disk are fetched, so reruns are fast.

Usage
-----
    python scripts/fetch_historical_data.py                      # defaults
    python scripts/fetch_historical_data.py --days 2278          # 2020-present
    python scripts/fetch_historical_data.py --symbols BTC-USDT   # one symbol
    python scripts/fetch_historical_data.py --timeframes 5m 1H   # two TFs

Output
------
    data/historical/BTC-USDT/5m.csv
    data/historical/BTC-USDT/15m.csv
    ...

CSV columns: timestamp, open, high, low, close, volume
"""

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import blofin.constants
import blofin.utils
import pandas as pd
from blofin.client import BloFinClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROD_REST_URL = "https://openapi.blofin.com"
CANDLES_PER_REQUEST = 1440          # BloFin hard max per call
RATE_LIMIT_SLEEP = 0.3              # seconds between requests
RETRY_SLEEP = 5.0
MAX_RETRIES = 3

DEFAULT_SYMBOLS = ["BTC-USDT", "ETH-USDT"]
DEFAULT_TIMEFRAMES = ["5m", "15m", "30m", "1H", "4H", "1D"]
DEFAULT_DAYS = 730

# Minutes per bar — used to estimate expected total candle count
_BAR_MINUTES: dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1H": 60, "2H": 120, "4H": 240, "6H": 360,
    "8H": 480, "12H": 720, "1D": 1440,
}

# ---------------------------------------------------------------------------
# BloFin client (public endpoints — no real credentials required)
# ---------------------------------------------------------------------------

def _make_client() -> BloFinClient:
    blofin.constants.REST_API_URL = PROD_REST_URL
    blofin.utils.REST_API_URL = PROD_REST_URL
    return BloFinClient(api_key="", api_secret="", passphrase="")


# ---------------------------------------------------------------------------
# Single-page fetch
# ---------------------------------------------------------------------------

def _fetch_page(
    client: BloFinClient,
    symbol: str,
    bar: str,
    after_ms: int | None = None,
) -> list[dict]:
    """Return one page of candles in chronological order (oldest first).

    BloFin/OKX pagination convention (results sorted newest-first):
      - `after`  = return data OLDER than this timestamp (paginate backwards)
      - `before` = return data NEWER than this timestamp (paginate forwards)

    We pass `after_ms` to walk backwards through history.
    """
    kwargs: dict = {"inst_id": symbol, "bar": bar, "limit": CANDLES_PER_REQUEST}
    if after_ms is not None:
        kwargs["after"] = str(after_ms)

    resp = client.public.get_candlesticks(**kwargs)
    rows = []
    for item in resp.get("data", []):
        rows.append({
            "timestamp": datetime.fromtimestamp(int(item[0]) / 1000, tz=timezone.utc),
            "open":   float(item[1]),
            "high":   float(item[2]),
            "low":    float(item[3]),
            "close":  float(item[4]),
            "volume": float(item[5]),
        })
    # API returns newest-first; reverse to chronological (oldest first)
    rows.reverse()
    return rows


def _fetch_page_retry(
    client: BloFinClient,
    symbol: str,
    bar: str,
    after_ms: int | None = None,
) -> list[dict]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return _fetch_page(client, symbol, bar, after_ms)
        except Exception as exc:
            if attempt == MAX_RETRIES:
                raise
            print(f"\n    [retry {attempt}/{MAX_RETRIES}] {exc} — waiting {RETRY_SLEEP}s")
            time.sleep(RETRY_SLEEP)
    return []


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _load_existing(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df.sort_values("timestamp", inplace=True)
    df.drop_duplicates(subset=["timestamp"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
    df.to_csv(path, index=False)


# ---------------------------------------------------------------------------
# Full backfill (pages backwards from now to start_dt)
# ---------------------------------------------------------------------------

def _backfill(
    client: BloFinClient,
    symbol: str,
    bar: str,
    start_dt: datetime,
) -> list[dict]:
    """Page backwards through history, returning all candles >= start_dt.

    Uses the `after` cursor (OKX convention) to walk backwards in time:
    each request returns candles OLDER than the oldest candle we have so far.
    Pages are collected newest-first and reversed at the end for chronological output.
    """
    bar_minutes = _BAR_MINUTES.get(bar, 60)
    elapsed_minutes = (datetime.now(tz=timezone.utc) - start_dt).total_seconds() / 60
    expected_total = int(elapsed_minutes / bar_minutes)

    pages: list[list[dict]] = []
    after_ms: int | None = None   # None = start from now; advances backwards each page

    while True:
        page = _fetch_page_retry(client, symbol, bar, after_ms)

        if not page:
            break

        # Drop rows that fall before our target window
        page = [r for r in page if r["timestamp"] >= start_dt]

        if not page:
            break  # entire page is before start_dt — we've gone far enough

        pages.append(page)
        total_so_far = sum(len(p) for p in pages)
        oldest_ts = page[0]["timestamp"]       # oldest row in THIS page
        pct = min(99, int(total_so_far / max(expected_total, 1) * 100))

        print(
            f"    fetched {total_so_far:>7,} rows | "
            f"oldest: {oldest_ts.strftime('%Y-%m-%d')} | ~{pct}%   ",
            end="\r", flush=True,
        )

        if oldest_ts <= start_dt:
            break

        # Advance cursor: next call returns candles older than this page's oldest
        after_ms = int(oldest_ts.timestamp() * 1000)
        time.sleep(RATE_LIMIT_SLEEP)

    print()  # newline after \r progress line

    # pages are collected newest-first; reverse to get chronological order
    pages.reverse()
    return [row for page in pages for row in page]


# ---------------------------------------------------------------------------
# Incremental update (append candles newer than latest on disk)
# ---------------------------------------------------------------------------

def _incremental_update(
    client: BloFinClient,
    symbol: str,
    bar: str,
    existing: pd.DataFrame,
) -> list[dict]:
    """Return all candles newer than the last row in `existing`.

    Pages backwards from now using the `after` cursor (same convention as
    _backfill) until the page overlaps data already on disk. The previous
    single-request implementation could only append one page (~1440 candles),
    silently leaving a hole whenever the file was more than one page stale
    (FABLE-013).
    """
    latest_ts = existing["timestamp"].max().to_pydatetime()

    pages: list[list[dict]] = []
    after_ms: int | None = None  # None = newest page; walks backwards
    while True:
        page = _fetch_page_retry(client, symbol, bar, after_ms)
        if not page:
            break
        new_rows = [r for r in page if r["timestamp"] > latest_ts]
        if new_rows:
            pages.append(new_rows)
        oldest_ts = page[0]["timestamp"]
        if oldest_ts <= latest_ts:
            break  # reached data we already have
        after_ms = int(oldest_ts.timestamp() * 1000)
        time.sleep(RATE_LIMIT_SLEEP)

    pages.reverse()
    rows = [row for page in pages for row in page]
    rows.sort(key=lambda r: r["timestamp"])
    return rows


# ---------------------------------------------------------------------------
# Orchestration per symbol/timeframe
# ---------------------------------------------------------------------------

def fetch_symbol_timeframe(
    client: BloFinClient,
    symbol: str,
    bar: str,
    start_dt: datetime,
    data_dir: Path,
) -> None:
    path = data_dir / symbol / f"{bar}.csv"
    existing = _load_existing(path)

    if existing is not None and not existing.empty:
        latest_ts = existing["timestamp"].max().to_pydatetime()
        bar_minutes = _BAR_MINUTES.get(bar, 60)
        age_minutes = (datetime.now(tz=timezone.utc) - latest_ts).total_seconds() / 60

        if age_minutes < bar_minutes * 2:
            print(f"    already up to date ({len(existing):,} rows)")
            return

        print(f"    updating from {latest_ts.strftime('%Y-%m-%d %H:%M')} "
              f"({len(existing):,} existing rows)...")
        new_rows = _incremental_update(client, symbol, bar, existing)

        if new_rows:
            new_df = pd.DataFrame(new_rows)
            new_df["timestamp"] = pd.to_datetime(new_df["timestamp"], utc=True)
            combined = pd.concat([existing, new_df], ignore_index=True)
            _save(combined, path)
            print(f"    +{len(new_rows)} new rows → {len(combined):,} total")
        else:
            print(f"    no new candles")
        return

    # Full backfill
    print(f"    full fetch from {start_dt.strftime('%Y-%m-%d')} to now...")
    rows = _backfill(client, symbol, bar, start_dt)

    if rows:
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        _save(df, path)
        oldest = df["timestamp"].min().strftime("%Y-%m-%d")
        newest = df["timestamp"].max().strftime("%Y-%m-%d")
        size_kb = path.stat().st_size / 1024
        print(f"    saved {len(df):,} rows  [{oldest} → {newest}]  ({size_kb:.0f} KB)")
    else:
        print(f"    no data returned — symbol may not have history this far back")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch historical BloFin OHLCV data for ML training."
    )
    parser.add_argument(
        "--symbols", nargs="+", default=DEFAULT_SYMBOLS,
        help="Instrument IDs (default: BTC-USDT ETH-USDT)",
    )
    parser.add_argument(
        "--timeframes", nargs="+", default=DEFAULT_TIMEFRAMES,
        help="Bar sizes (default: 5m 15m 30m 1H 4H 1D)",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS,
        help="Calendar days of history to fetch (default: 730)",
    )
    parser.add_argument(
        "--data-dir", default="data/historical",
        help="Output directory (default: data/historical)",
    )
    args = parser.parse_args()

    start_dt = datetime.now(tz=timezone.utc) - timedelta(days=args.days)
    data_dir = Path(args.data_dir)

    print("BloFin Historical Data Fetcher")
    print(f"  Symbols:    {', '.join(args.symbols)}")
    print(f"  Timeframes: {', '.join(args.timeframes)}")
    print(f"  From:       {start_dt.strftime('%Y-%m-%d')}  ({args.days} days)")
    print(f"  Output:     {data_dir}/")
    print()

    client = _make_client()
    total = len(args.symbols) * len(args.timeframes)
    done = 0

    for symbol in args.symbols:
        print(f"[{symbol}]")
        for bar in args.timeframes:
            done += 1
            print(f"  ({done}/{total}) {bar}:")
            try:
                fetch_symbol_timeframe(client, symbol, bar, start_dt, data_dir)
            except Exception as exc:
                print(f"    ERROR — {exc}")
            time.sleep(RATE_LIMIT_SLEEP)
        print()

    # Summary
    print("─" * 50)
    total_size = sum(f.stat().st_size for f in data_dir.rglob("*.csv"))
    print(f"Total disk usage: {total_size / 1024 / 1024:.1f} MB")
    print("Done.")


if __name__ == "__main__":
    main()
