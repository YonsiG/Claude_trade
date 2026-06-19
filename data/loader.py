from __future__ import annotations

import os
import re
import time
import requests
import yfinance as yf
import akshare as ak
import pandas as pd
from pathlib import Path
from tqdm import tqdm

# ── Proxy config ──────────────────────────────────────────────────────────────
# Set to your local VPN proxy address so yfinance uses it.
# akshare (Sina) routes through domestic servers and does NOT use this proxy.
# Common ports: Clash=7890, V2RayN=10809, SSR=1080
# Set to None to disable.
YF_PROXY: str | None = "http://127.0.0.1:7890"
# ─────────────────────────────────────────────────────────────────────────────

RAW_DIR = Path(__file__).parent / "raw"

_SOURCE_DIRS = {
    "yf": RAW_DIR / "yf",
    "ak": RAW_DIR / "ak",
}
for _d in _SOURCE_DIRS.values():
    _d.mkdir(parents=True, exist_ok=True)

# Matches: AAPL_2020-01-01_2024-12-31_1d  or  RB0_2020-01-01_2024-12-31_1d
# Greedy first group handles tickers with underscores (e.g. BRK_B)
_FNAME_RE = re.compile(r"^(.+)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})_(.+)$")

PRESETS = {
    "year":  {"interval": "1d", "period_days": 365 * 5},
    "month": {"interval": "1h", "period_days": 30},
    "day":   {"interval": "1m", "period_days": 5},
}

# akshare period strings for futures_zh_minute_sina
_AK_PERIOD = {
    "1h":  "60",
    "30m": "30",
    "15m": "15",
    "5m":  "5",
    "1m":  "1",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_filename(path: Path) -> dict | None:
    m = _FNAME_RE.match(path.stem)
    if not m:
        return None
    return {"ticker": m.group(1), "start": m.group(2),
            "end": m.group(3), "interval": m.group(4)}


def _yf_proxy_patch() -> dict:
    """Temporarily set proxy env vars for yfinance and return originals for restore."""
    _PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    originals = {k: os.environ.get(k) for k in _PROXY_KEYS}
    if YF_PROXY:
        for k in _PROXY_KEYS:
            os.environ[k] = YF_PROXY
    return originals


def _yf_proxy_restore(originals: dict) -> None:
    for k, v in originals.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _download_yf(ticker: str, start: str, end: str, interval: str,
                 retries: int = 3, base_delay: float = 10.0) -> pd.DataFrame:
    """
    Download from yfinance with proxy injected via env vars (works with yfinance 1.x)
    and exponential-backoff retry on rate-limit.
    """
    df = pd.DataFrame()

    for attempt in range(retries):
        originals = _yf_proxy_patch()
        try:
            df = yf.download(ticker, start=start, end=end, interval=interval,
                             auto_adjust=True, progress=False)
        except Exception:
            df = pd.DataFrame()
        finally:
            _yf_proxy_restore(originals)

        if not df.empty:
            break

        if attempt < retries - 1:
            wait = base_delay * (2 ** attempt)   # 10s → 20s → 40s
            tqdm.write(f"  rate-limited, retrying in {wait:.0f}s "
                       f"(attempt {attempt + 1}/{retries}) ...")
            time.sleep(wait)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    return df


def _download_ak(ticker: str, start: str, end: str, interval: str) -> pd.DataFrame:
    """
    Fetch Chinese futures via Sina.
    Daily  : futures_zh_daily_sina  — full history, filter client-side.
    Intraday: futures_zh_minute_sina — recent ~1000 bars only.
    """
    if interval == "1d":
        df = ak.futures_zh_daily_sina(symbol=ticker)
        df = df.rename(columns={"date": "datetime"})
    else:
        period = _AK_PERIOD.get(interval)
        if period is None:
            raise ValueError(
                f"akshare does not support interval '{interval}'. "
                f"Supported: 1d, {', '.join(_AK_PERIOD)}"
            )
        df = ak.futures_zh_minute_sina(symbol=ticker, period=period)

    df = df.set_index("datetime")
    df.index = pd.to_datetime(df.index)
    df.index.name = None
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].astype(float)
    return df.loc[start:end]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def download(ticker: str, start: str, end: str,
             interval: str = "1d", source: str = "yf",
             save: bool = True) -> pd.DataFrame:
    """
    Download OHLCV data with a progress bar and save to raw/{source}/.

    Args:
        ticker:   Symbol string.
                  yf  examples: "AAPL", "ES=F", "GC=F"
                  ak  examples: "RB0" (螺纹钢主力), "CU0" (铜主力), "IF0" (沪深300)
        start:    "YYYY-MM-DD"
        end:      "YYYY-MM-DD"
        interval: "1d" (default) | "1h" | "30m" | "15m" | "5m" | "1m"
                  Note: akshare intraday only returns recent ~1000 bars.
        source:   "yf" (yfinance, US stocks/futures) | "ak" (akshare, Chinese futures)
        save:     Persist to raw/{source}/{ticker}_{start}_{end}_{interval}.csv
    """
    if source not in _SOURCE_DIRS:
        raise ValueError(f"Unknown source '{source}'. Choose: {list(_SOURCE_DIRS)}")

    with tqdm(total=1, desc=f"[{source}] {ticker} {start}→{end} [{interval}]",
              unit="req", bar_format="{l_bar}{bar}| {elapsed}") as bar:
        if source == "yf":
            df = _download_yf(ticker, start, end, interval)
        else:
            df = _download_ak(ticker, start, end, interval)
        bar.update(1)

    if df.empty:
        raise ValueError(
            f"No data returned for '{ticker}' [{source}] {start}→{end} {interval}. "
            f"Check ticker spelling, date range, or network access."
        )

    if save:
        path = _SOURCE_DIRS[source] / f"{ticker}_{start}_{end}_{interval}.csv"
        df.to_csv(path)

    return df


def load(ticker: str, start: str, end: str,
         interval: str = "1d", source: str = "yf") -> pd.DataFrame:
    """
    Load OHLCV data for [start, end] at the given interval.

    Cache logic (applied per source independently):
      1. Scan raw/{source}/ for files matching ticker + interval.
      2. If any file's range fully covers [start, end] → slice and return.
      3. Otherwise compute the union of all matching files' ranges plus [start, end],
         delete old files, download the union, and return the sliced result.

    Args:
        ticker:   Symbol string (see download() for examples).
        start:    "YYYY-MM-DD"
        end:      "YYYY-MM-DD"
        interval: "1d" (default) | "1h" | "30m" | "15m" | "5m" | "1m"
        source:   "yf" | "ak"
    """
    if source not in _SOURCE_DIRS:
        raise ValueError(f"Unknown source '{source}'. Choose: {list(_SOURCE_DIRS)}")

    raw_dir = _SOURCE_DIRS[source]
    existing: list[tuple[Path, str, str]] = []

    for path in sorted(raw_dir.glob("*.csv")):
        info = _parse_filename(path)
        if info is None or info["ticker"] != ticker or info["interval"] != interval:
            continue

        if info["start"] <= start and info["end"] >= end:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if df.empty:
                path.unlink()  # stale empty cache — delete and fall through to re-download
                continue
            return df.loc[start:end]

        existing.append((path, info["start"], info["end"]))

    # No covering file — compute union of all existing ranges + requested range
    union_start = min([start] + [s for _, s, _ in existing])
    union_end   = max([end]   + [e for _, _, e in existing])

    for path, _, _ in existing:
        path.unlink()

    df = download(ticker, union_start, union_end, interval, source=source, save=True)
    return df.loc[start:end]
