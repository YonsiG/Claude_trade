import yfinance as yf
import pandas as pd
from datetime import date, timedelta
from pathlib import Path

RAW_DIR = Path(__file__).parent / "raw"

PRESETS = {
    "year":  {"interval": "1d", "period_days": 365 * 5, "subdir": "year"},
    "month": {"interval": "1h", "period_days": 30,      "subdir": "month"},
    "day":   {"interval": "1m", "period_days": 5,       "subdir": "day"},
}


def _fix_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    return df


def download(ticker: str, mode: str = "year", save: bool = True) -> pd.DataFrame:
    cfg = PRESETS[mode]
    end = date.today()
    start = end - timedelta(days=cfg["period_days"])

    df = yf.download(
        ticker,
        start=str(start),
        end=str(end),
        interval=cfg["interval"],
        auto_adjust=True,
    )
    df = _fix_columns(df)

    if save:
        out_dir = RAW_DIR / cfg["subdir"]
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{ticker}_{start}_{end}.csv"
        df.to_csv(out_dir / filename)

    return df


def load(ticker: str, mode: str = "year") -> pd.DataFrame:
    cfg = PRESETS[mode]
    end = date.today()
    start = end - timedelta(days=cfg["period_days"])
    path = RAW_DIR / cfg["subdir"] / f"{ticker}_{start}_{end}.csv"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    return download(ticker, mode)
