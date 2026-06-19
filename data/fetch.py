"""
Batch-download futures/equity data via yfinance (US) and akshare (CN).

Reads tickers from a list file (default: data/tickers.txt).
Each non-comment line must have at least two columns: ticker and source.
Optional third column is a display name.

  # ticker   source   name(optional)
  GLD        yf       黄金ETF
  LH0        ak       生猪

Futures validation:
  US futures (source=yf, ticker ends with =F) and CN futures (source=ak)
  must exist in tickerref.txt. Stocks/ETFs (source=yf, no =F) are exempt.

Timeframe presets (override with --start / --end):
  --year   1d  interval, last 5 years   (default)
  --month  1h  interval, last 30 days
  --day    1m  interval, last 5 days

Source filter:
  --source yf    yfinance tickers only
  --source ak    akshare tickers only
  --source all   all tickers in the file (default)

Usage examples:
  python data/fetch.py
  python data/fetch.py --month
  python data/fetch.py --day --source ak
  python data/fetch.py --tickers data/tickers.txt --source yf
  python data/fetch.py --start 2020-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from loader import download, PRESETS

DEFAULT_TICKERS_FILE  = "data/tickers.txt"
DEFAULT_TICKERREF_FILE = "data/tickerref.txt"


def load_tickerref(path: str) -> set[str]:
    """Return the set of valid futures tickers from tickerref.txt."""
    valid = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ticker = line.split()[0].upper()
        valid.add(ticker)
    return valid


def read_ticker_file(path: str) -> list[tuple[str, str, str]]:
    """Parse tickers file into (ticker, source, name) tuples."""
    entries = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        ticker = parts[0].upper()
        source = parts[1].lower() if len(parts) > 1 else "yf"
        name   = parts[2] if len(parts) > 2 else ticker
        entries.append((ticker, source, name))
    return entries


def is_futures(ticker: str, source: str) -> bool:
    return source == "ak" or ticker.endswith("=F")


def validate_entries(
    entries: list[tuple[str, str, str]],
    tickerref: set[str],
) -> tuple[list[tuple[str, str, str]], bool]:
    """
    Check futures tickers against tickerref.
    Returns (valid_entries, had_errors).
    Stocks are passed through unchanged.
    """
    valid = []
    had_errors = False
    for ticker, source, name in entries:
        if is_futures(ticker, source) and ticker not in tickerref:
            print(f"  ERROR  {ticker} ({source}) not found in tickerref.txt — skipping")
            had_errors = True
        else:
            valid.append((ticker, source, name))
    return valid, had_errors


def run_batch(entries: list[tuple[str, str, str]],
              start: str, end: str, interval: str) -> None:
    by_source: dict[str, list[tuple[str, str]]] = {}
    for ticker, source, name in entries:
        by_source.setdefault(source, []).append((ticker, name))

    for source, items in by_source.items():
        print(f"\n[{source.upper()}] {len(items)} tickers")
        ok = fail = 0
        for ticker, name in items:
            label = f"{name}({ticker})" if name != ticker else ticker
            try:
                df = download(ticker, start, end, interval=interval,
                              source=source, save=True)
                print(f"  OK  {label:<22s}  {len(df):>5d} bars")
                ok += 1
            except Exception as exc:
                print(f"  !!  {label:<22s}  FAILED: {exc}")
                fail += 1
        print(f"  -> {ok} ok, {fail} failed")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-download OHLCV data (yfinance + akshare)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    tf = parser.add_mutually_exclusive_group()
    tf.add_argument("--year",  action="store_true", help="1d interval, last 5 years (default)")
    tf.add_argument("--month", action="store_true", help="1h interval, last 30 days")
    tf.add_argument("--day",   action="store_true", help="1m interval, last 5 days")

    parser.add_argument("--start", help="Start date YYYY-MM-DD (overrides preset)")
    parser.add_argument("--end",   help="End date   YYYY-MM-DD (overrides preset)")
    parser.add_argument("--tickers", default=DEFAULT_TICKERS_FILE,
                        help=f"Ticker list file (default: {DEFAULT_TICKERS_FILE})")
    parser.add_argument("--tickerref", default=DEFAULT_TICKERREF_FILE,
                        help=f"Futures reference file (default: {DEFAULT_TICKERREF_FILE})")
    parser.add_argument("--source", default="all", choices=["yf", "ak", "all"],
                        help="Filter by source: yf, ak, or all (default: all)")

    args = parser.parse_args()

    mode     = "month" if args.month else "day" if args.day else "year"
    cfg      = PRESETS[mode]
    today    = date.today()
    start    = args.start or str(today - timedelta(days=cfg["period_days"]))
    end      = args.end   or str(today)
    interval = cfg["interval"]

    tickerref = load_tickerref(args.tickerref)

    all_entries = read_ticker_file(args.tickers)
    if args.source != "all":
        all_entries = [(t, s, n) for t, s, n in all_entries if s == args.source]

    print("=" * 65)
    print(f"Mode: {mode}  interval={interval}  {start} -> {end}")
    print(f"File: {args.tickers}  ({len(all_entries)} tickers, source={args.source})")
    print("=" * 65)

    print("\nValidating futures tickers against tickerref.txt ...")
    entries, had_errors = validate_entries(all_entries, tickerref)
    n_futures  = sum(1 for t, s, _ in all_entries if is_futures(t, s))
    n_stocks   = len(all_entries) - n_futures
    n_rejected = len(all_entries) - len(entries)
    print(f"  {n_stocks} stocks/ETFs (exempt)  |  "
          f"{n_futures} futures checked  |  "
          f"{n_rejected} rejected  |  "
          f"{len(entries)} proceeding")

    if had_errors and not entries:
        print("\nNo valid tickers to download. Exiting.")
        sys.exit(1)

    run_batch(entries, start, end, interval)

    print()
    print("=" * 65)
    print("Done. Data cached in data/raw/ak/ and data/raw/yf/")
    print("=" * 65)


if __name__ == "__main__":
    main()
