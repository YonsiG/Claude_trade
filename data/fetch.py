"""
Batch-download tickers from a list file.

Usage:
    python data/fetch.py                   # 5-year daily  → raw/year/
    python data/fetch.py --month           # 1-month hourly → raw/month/
    python data/fetch.py --day             # 5-day minute  → raw/day/
    python data/fetch.py --tickers my.txt  # custom list
"""

import argparse
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
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--year",  action="store_true", default=False)
    group.add_argument("--month", action="store_true", default=False)
    group.add_argument("--day",   action="store_true", default=False)
    args = parser.parse_args()

    if args.month:
        mode = "month"
    elif args.day:
        mode = "day"
    else:
        mode = "year"

    cfg = PRESETS[mode]
    tickers = read_tickers(args.tickers)
    print(f"Mode: {mode}  interval={cfg['interval']}  → raw/{cfg['subdir']}/")
    print(f"Tickers: {tickers}\n")

    for ticker in tickers:
        print(f"  {ticker} ...", end=" ", flush=True)
        try:
            df = download(ticker, mode=mode, save=True)
            print(f"OK ({len(df)} rows)")
        except Exception as e:
            print(f"FAILED: {e}")


if __name__ == "__main__":
    main()
