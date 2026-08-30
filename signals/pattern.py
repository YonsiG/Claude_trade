import numpy as np
import pandas as pd
from trend.detector import detect_trend
from trend.base import TrendType
from signals.base import Signal
from signals.feature import is_umbrella, is_engulfing


def umbrella(df: pd.DataFrame, trend_window: int = 20,
             shadow_ratio: float = 2.0, upper_ratio: float = 0.1,
             lower_cap: float = 6.0,
             check_abnormal=None, roll_dates=None, abnormal_bars=None) -> Signal:
    """
    伞形线（锤子线 / 上吊线）反转信号，返回 Signal(entry, size)。

      entry = +1: 下跌趋势中出现锤子线，在该 bar 发出做多脉冲。
      entry = -1: 上升趋势中出现上吊线，在**确认 bar**（次日收盘跌破实体下沿）
                  发出做空脉冲。
      size:       形态强度，权重为 下影线 40% / 上影线 20% / 实体大小 40%。
    """
    mask = is_umbrella(df, shadow_ratio, upper_ratio)
    body = (df["close"] - df["open"]).abs()
    body_low = df[["open", "close"]].min(axis=1)
    lower_shadow = body_low - df["low"]
    upper_shadow = df["high"] - df[["open", "close"]].max(axis=1)
    total_range = df["high"] - df["low"]

    sig = Signal.blank(df.index)
    for i, idx in enumerate(df.index):
        if not mask.loc[idx] or i < trend_window:
            continue
        prior = df.iloc[i - trend_window:i]
        trend = detect_trend(prior, n=trend_window,
                             check_abnormal=check_abnormal, roll_dates=roll_dates,
                             abnormal_bars=abnormal_bars)

        b = body.loc[idx]
        lower_score = np.clip((lower_shadow.loc[idx] / b - shadow_ratio) / (lower_cap - shadow_ratio), 0, 1) if b > 0 else 1.0
        upper_score = 1.0 - np.clip(upper_shadow.loc[idx] / (b * upper_ratio), 0, 1) if b > 0 else 1.0
        body_score = 1.0 - np.clip(b / total_range.loc[idx], 0, 1) if total_range.loc[idx] > 0 else 0.0
        strength = 0.4 * lower_score + 0.2 * upper_score + 0.4 * body_score

        if trend.trend == TrendType.DOWNTREND:
            # 锤子线信号可能落在某个上吊线的确认 bar 上；先到先得，不覆盖。
            if sig.entry.loc[idx] == 0:
                sig.entry.loc[idx] = 1
                sig.size.loc[idx] = strength

        elif trend.trend == TrendType.UPTREND:
            # hanging man: requires confirmation on next bar
            if i + 1 < len(df):
                next_idx = df.index[i + 1]
                if (df["close"].loc[next_idx] < body_low.loc[idx]
                        and sig.entry.loc[next_idx] == 0):
                    sig.entry.loc[next_idx] = -1
                    sig.size.loc[next_idx] = strength

    return sig


def engulfing(df: pd.DataFrame, trend_window: int = 20, doji_ratio: float = 0.1,
              vol_window: int = 20, body_cap: float = 3.0, vol_cap: float = 3.0,
              check_abnormal=None, roll_dates=None, abnormal_bars=None) -> Signal:
    """
    吞噬形态反转信号，返回 Signal(entry, size)。

      entry = +1: 下跌趋势中的看涨吞噬。
      entry = -1: 上升趋势中的看跌吞噬。
      size:       形态强度，权重为 成交量 60% / 实体倍数 40%。
                  - body_ratio_score: 第二根实体比第一根大多少，clip 到 [0, 1]
                  - vol_score:        成交量超出滚动均值多少，clip 到 [0, 1]
    """
    mask = is_engulfing(df, doji_ratio)
    bullish_bar = df["close"] > df["open"]
    body = (df["close"] - df["open"]).abs()
    avg_vol = df["volume"].rolling(vol_window).mean()

    sig = Signal.blank(df.index)
    for i, idx in enumerate(df.index):
        if not mask.loc[idx] or i < trend_window:
            continue
        prior = df.iloc[i - trend_window:i]
        trend = detect_trend(prior, n=trend_window,
                             check_abnormal=check_abnormal, roll_dates=roll_dates,
                             abnormal_bars=abnormal_bars)

        if trend.trend == TrendType.DOWNTREND and bullish_bar.loc[idx]:
            direction = 1
        elif trend.trend == TrendType.UPTREND and not bullish_bar.loc[idx]:
            direction = -1
        else:
            continue

        prev_idx = df.index[i - 1]
        prev_body = body.loc[prev_idx]
        body_ratio_score = np.clip((body.loc[idx] / prev_body - 1) / (body_cap - 1), 0, 1) if prev_body > 0 else 0.0

        avg = avg_vol.loc[idx]
        vol_score = np.clip((df["volume"].loc[idx] / avg - 1) / (vol_cap - 1), 0, 1) if avg > 0 else 0.0

        sig.entry.loc[idx] = direction
        sig.size.loc[idx] = 0.4 * body_ratio_score + 0.6 * vol_score

    return sig
