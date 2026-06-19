"""
Load and cache futures data.

Daily  (2020-01-01 → 2026-06-10):  CN + US futures
3-hour (2025-01-01 → 2026-06-10):  downloaded as 1h, resampled to 3h
  Note: akshare Sina intraday returns only the recent ~1000 bars;
        the 3h CN data may not reach back to 2025-01-01.

Run from project root:
    python script/load_data.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.loader import load

# ── Date ranges ───────────────────────────────────────────────────────────────
DAILY_START = "2020-01-01"
DAILY_END   = "2026-06-10"
H3_START    = "2025-01-01"
H3_END      = "2026-06-10"

# ── Ticker maps  (ticker → 中文名) ────────────────────────────────────────────
# CN_TICKERS: dict[str, str] = {
#     "LH0": "生猪",
#     "JD0": "鸡蛋",
#     "AU0": "黄金",
#     "AG0": "白银",
#     "RB0": "螺纹钢",
#     "M0":  "豆粕",
# }

CN_TICKERS: dict[str, str] = {
}

US_TICKERS: dict[str, str] = {
    "ZS=F": "美豆",
    "ZC=F": "美玉米",
    "NQ=F": "纳指100",
    "GC=F": "黄金",
    "SI=F": "白银",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_range(df: pd.DataFrame) -> str:
    return f"{str(df.index[0])[:10]} → {str(df.index[-1])[:10]}"


def resample_3h(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1h OHLCV bars into 3h bars."""
    return (
        df.resample("3h").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}
        )
        .dropna(subset=["close"])
    )


def load_batch(tickers: dict[str, str], source: str,
               start: str, end: str,
               interval: str = "1d",
               inter_delay: float = 0.0) -> dict[str, pd.DataFrame]:
    results: dict[str, pd.DataFrame] = {}
    for i, (ticker, name) in enumerate(tickers.items()):
        if i > 0 and inter_delay > 0:
            time.sleep(inter_delay)
        label = f"{name}({ticker})"
        try:
            df = load(ticker, start, end, interval=interval, source=source)
            results[ticker] = df
            status = f"{len(df):>5d} bars  {_fmt_range(df)}"
            print(f"  OK  {label:<18s}  {status}")
        except Exception as exc:
            print(f"  !!  {label:<18s}  FAILED: {exc}")
    return results


# ── Daily ─────────────────────────────────────────────────────────────────────

print("=" * 65)
print(f"Daily  {DAILY_START} → {DAILY_END}")
print("=" * 65)

print("\n[CN] akshare")
cn_daily = load_batch(CN_TICKERS, source="ak",
                      start=DAILY_START, end=DAILY_END, interval="1d")

print("\n[US] yfinance")
us_daily = load_batch(US_TICKERS, source="yf",
                      start=DAILY_START, end=DAILY_END, interval="1d",
                      inter_delay=2.0)

# ── 3-hour (1h → resample) ────────────────────────────────────────────────────

print()
print("=" * 65)
print(f"3-hour {H3_START} → {H3_END}  (downloaded as 1h, resampled)")
print("=" * 65)

print("\n[CN] akshare  * Sina intraday: recent ~1000 bars only")
cn_1h = load_batch(CN_TICKERS, source="ak",
                   start=H3_START, end=H3_END, interval="1h")
cn_3h: dict[str, pd.DataFrame] = {}
for ticker, df in cn_1h.items():
    cn_3h[ticker] = resample_3h(df)
    name = CN_TICKERS[ticker]
    r3 = cn_3h[ticker]
    if not r3.empty:
        print(f"  →   {name}({ticker}): {len(r3)} 3h bars  {_fmt_range(r3)}")

print("\n[US] yfinance")
us_1h = load_batch(US_TICKERS, source="yf",
                   start=H3_START, end=H3_END, interval="1h",
                   inter_delay=2.0)
us_3h: dict[str, pd.DataFrame] = {}
for ticker, df in us_1h.items():
    us_3h[ticker] = resample_3h(df)
    name = US_TICKERS[ticker]
    r3 = us_3h[ticker]
    if not r3.empty:
        print(f"  →   {name}({ticker}): {len(r3)} 3h bars  {_fmt_range(r3)}")

# ── Summary ───────────────────────────────────────────────────────────────────

print()
print("=" * 65)
print("Done. Data cached in data/raw/ak/ and data/raw/yf/")
print("=" * 65)
