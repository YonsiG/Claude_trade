"""
Continuous-contract rollover adjustment.

Main continuous futures series (akshare "RB0"/"AU0", yfinance "GC=F"/"NQ=F")
are stitched from successive delivery months. At each roll the price level
jumps by the basis between the old and new contract. That jump is not a
tradable move, but a naive backtest counts it as real P&L and pattern
detectors read it as a large bar (a rollover gap is easily misread as an
engulfing or umbrella bar).

Ratio back-adjustment removes the jump: every bar before a roll is scaled by
the gap ratio so the series becomes continuous at the most recent contract's
price level. Percentage returns within each contract are preserved exactly;
only absolute price levels shift.

识别换月的三条路径，按可靠性排序：

1. `roll_dates` —— 调用方给出的真实换月日，最可靠。
2. `hold`（持仓量）跳变 —— **默认**。主力连续在换月时切换到另一份合约，
   持仓量必然断裂；而涨跌停、节假日缺口发生在同一份合约上，持仓量是连续的。
   akshare 的日线原始返回自带 hold 列，loader 已保留。
3. 价格跳空阈值 —— 仅在没有 hold 列时（yfinance 期货、老缓存）退而求其次。
   它分不清换月和真实行情：实测 2021-2026 年 RB0 有 7 根、AU0 有 18 根
   跳空 >2% 的 bar 其实是节后缺口或涨跌停，会被它误判为换月并抹平。
"""
from __future__ import annotations

import pandas as pd

_PRICE_COLS = ("open", "high", "low", "close")
HOLD_COL = "hold"

DEFAULT_GAP_THRESHOLD = 0.02
DEFAULT_HOLD_THRESHOLD = 0.30


def rollover_ratio(df: pd.DataFrame) -> pd.Series:
    """Per-bar ratio of this bar's open to the previous bar's close."""
    return df["open"] / df["close"].shift(1)


def hold_jump(df: pd.DataFrame) -> pd.Series:
    """持仓量相对前一根 bar 的变动幅度。无 hold 列时返回全 NaN。"""
    if HOLD_COL not in df.columns:
        return pd.Series(float("nan"), index=df.index)
    prev = df[HOLD_COL].shift(1)
    return (df[HOLD_COL] / prev.where(prev > 0) - 1).abs()


def has_hold(df: pd.DataFrame) -> bool:
    """该 DataFrame 是否带可用的持仓量列（即能用高可靠度的换月识别）。"""
    return HOLD_COL in df.columns and df[HOLD_COL].notna().any()


def detect_rollover(df: pd.DataFrame,
                    threshold: float = DEFAULT_GAP_THRESHOLD,
                    hold_threshold: float = DEFAULT_HOLD_THRESHOLD,
                    roll_dates=None) -> pd.Series:
    """
    标记换月 bar 的布尔序列。优先级：roll_dates > 持仓量跳变 > 价格跳空阈值。

    Args:
        threshold:      价格跳空阈值（0.02 = 2%），仅在没有 hold 列时使用。
        hold_threshold: 持仓量跳变阈值（0.30 = 30%）。主力换月通常是数倍变化，
                        日常增减仓远低于此，30% 有很宽的安全边际。
        roll_dates:     已知的真实换月日，给了就只认它。
    """
    if roll_dates is not None:
        return pd.Series(df.index.isin(pd.DatetimeIndex(roll_dates)), index=df.index)

    if has_hold(df):
        return (hold_jump(df) > hold_threshold).fillna(False)

    ratio = rollover_ratio(df)
    return ((ratio - 1).abs() > threshold).fillna(False)


def adjust_rollover(df: pd.DataFrame, threshold: float = DEFAULT_GAP_THRESHOLD,
                    roll_dates=None,
                    hold_threshold: float = DEFAULT_HOLD_THRESHOLD) -> pd.DataFrame:
    """
    Return a copy of `df` with rollover gaps removed by ratio back-adjustment.

    Each bar is multiplied by the product of gap ratios at every roll that
    happens after it, lifting older contracts onto the newest contract's
    price level. Volume 和 hold 不动。

    换月的判定交给 detect_rollover()（持仓量优先）。当数据带 hold 列时，
    涨跌停和节后缺口不会再被误抹平；只有没有 hold 列时才退回 `threshold`
    价格跳空阈值——那种情况下先看 rollover_report() 再决定是否信任。
    """
    if len(df) < 2:
        return df.copy()

    ratio = rollover_ratio(df)
    is_roll = detect_rollover(df, threshold, hold_threshold, roll_dates)

    # Keep the gap ratio at roll bars, 1.0 elsewhere.
    factors = ratio.where(is_roll, 1.0).fillna(1.0)

    # adj[i] = product of factors strictly after i (reverse cumprod, shifted).
    adj = factors[::-1].cumprod()[::-1].shift(-1).fillna(1.0)

    out = df.copy()
    for col in _PRICE_COLS:
        if col in out.columns:
            out[col] = out[col] * adj
    return out


def rollover_report(df: pd.DataFrame,
                    threshold: float = DEFAULT_GAP_THRESHOLD,
                    hold_threshold: float = DEFAULT_HOLD_THRESHOLD,
                    roll_dates=None) -> pd.DataFrame:
    """
    检出的换月明细：前收、开盘、跳空幅度、持仓量变动，以及判定依据。

    同时列出「跳空够大但持仓量平稳」的 bar（source='gap_only'）——那些多半是
    涨跌停或节假日缺口，**不是**换月。先看这张表再决定要不要传 roll_dates。
    """
    ratio = rollover_ratio(df)
    hj = hold_jump(df)
    is_roll = detect_rollover(df, threshold, hold_threshold, roll_dates)
    gap_only = ((ratio - 1).abs() > threshold).fillna(False) & ~is_roll

    rows = is_roll | gap_only
    source = pd.Series("", index=df.index)
    source[is_roll] = "roll_dates" if roll_dates is not None else (
        "hold_jump" if has_hold(df) else "gap")
    source[gap_only] = "gap_only(疑似涨跌停/节后缺口，非换月)"

    return pd.DataFrame({
        "prev_close": df["close"].shift(1)[rows],
        "open": df["open"][rows],
        "gap_pct": ((ratio - 1) * 100)[rows],
        "hold_jump_pct": (hj * 100)[rows],
        "source": source[rows],
    })
