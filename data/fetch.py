"""
Batch-download tickers from a list file.

Usage:
    python data/fetch.py                          # 5-year daily, yfinance
    python data/fetch.py --source ak              # 5-year daily, akshare (Chinese futures)
    python data/fetch.py --month                  # 1-month hourly
    python data/fetch.py --day                    # 5-day minute
    python data/fetch.py --tickers my.txt         # custom ticker list
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from loader import download, PRESETS


def read_tickers(path: str) -> list[str]:
    return [
        line.strip().upper()
        for line in Path(path).read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default="data/tickers.txt")
    parser.add_argument("--source", default="yf", choices=["yf", "ak"],
                        help="Data source: yf=yfinance (US), ak=akshare (CN futures)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--year",  action="store_true", default=False)
    group.add_argument("--month", action="store_true", default=False)
    group.add_argument("--day",   action="store_true", default=False)
    args = parser.parse_args()

    mode = "month" if args.month else "day" if args.day else "year"
    cfg  = PRESETS[mode]
    end  = date.today()
    start = end - timedelta(days=cfg["period_days"])

    tickers = read_tickers(args.tickers)
    print(f"Source: {args.source}  Mode: {mode}  interval={cfg['interval']}  {start} → {end}")
    print(f"Tickers: {tickers}\n")

    for ticker in tickers:
        try:
            df = download(ticker, str(start), str(end), cfg["interval"],
                          source=args.source, save=True)
            print(f"  {ticker}: {len(df)} rows saved")
        except Exception as e:
            print(f"  {ticker}: FAILED — {e}")


if __name__ == "__main__":
    main()
