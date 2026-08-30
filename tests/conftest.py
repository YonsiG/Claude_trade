"""共享 fixture 与工具。

约定：
  - 绝大多数用例用**合成行情**，不联网、不依赖缓存，任何时候都能跑。
  - 需要真实数据的用例统一打 `@pytest.mark.data`，缓存缺失时自动 skip，
    不会让整套测试因为没网而变红。
"""
import numpy as np
import pandas as pd
import pytest

from signals.base import Signal


# ── 合成行情 ────────────────────────────────────────────────────────────────

def make_bars(closes, band=0.004, start="2024-01-01", volume=1e6, hold=None):
    """
    用收盘价序列造一段 OHLCV。

    band: 每根 bar 高低相对收盘价的幅度。真实日线约 0.4%~2%，
          需要触发震荡判定（ATR/价格 >= 1.5%）时要调大。
    hold: 持仓量序列，给了就带上 hold 列（触发期货换月检测）。
    """
    idx = pd.date_range(start, periods=len(closes), freq="D")
    df = pd.DataFrame({
        "open": closes,
        "high": [c * (1 + band) for c in closes],
        "low": [c * (1 - band) for c in closes],
        "close": closes,
        "volume": volume,
    }, index=idx, dtype=float)
    if hold is not None:
        df["hold"] = pd.Series(hold, index=idx, dtype=float)
    return df


def make_ohlc(rows, start="2024-01-01", volume=1e6):
    """逐根指定 [open, high, low, close]，用于精确控制盘中触发价。"""
    idx = pd.date_range(start, periods=len(rows), freq="D")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                        index=idx, dtype=float).assign(volume=volume)


# ── 合成信号 ────────────────────────────────────────────────────────────────

def make_signal_fn(entries, sizes=None):
    """把一串 -1/0/1 变成信号函数，逐 bar 对应。"""
    def fn(df):
        sig = Signal.blank(df.index)
        szs = sizes if sizes is not None else [1.0] * len(entries)
        for i, (e, z) in enumerate(zip(entries, szs)):
            sig.entry.iloc[i] = e
            sig.size.iloc[i] = z if e else 0.0
        return sig
    return fn


def flat_signal(df):
    """永不开仓。"""
    return Signal.blank(df.index)


# ── 价格形态 ────────────────────────────────────────────────────────────────

def ramp(n, rate, start=100.0):
    """复利涨/跌序列：rate=1.004 表示每根 +0.4%。"""
    return [start * (rate ** i) for i in range(n)]


# ── 真实数据（可选）─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def aapl():
    """AAPL 日线；缓存缺失或无网时跳过。"""
    return _load_or_skip("AAPL", "2021-08-02", "2026-07-30", "yf")


@pytest.fixture(scope="session")
def rb0():
    """RB0 螺纹钢主力连续（带 hold 列）；缓存缺失或无网时跳过。"""
    return _load_or_skip("RB0", "2021-09-01", "2026-07-31", "ak")


def _load_or_skip(ticker, start, end, source):
    from data.loader import load
    try:
        df = load(ticker, start, end, source=source)
    except Exception as exc:                                  # noqa: BLE001
        pytest.skip(f"{ticker} 数据不可用（{type(exc).__name__}）")
    if df.empty:
        pytest.skip(f"{ticker} 数据为空")
    return df


def pytest_configure(config):
    config.addinivalue_line("markers", "data: 依赖真实行情数据，缓存缺失时跳过")
