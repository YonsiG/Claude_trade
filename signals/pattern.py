import numpy as np
import pandas as pd
from trend.detector import detect_trend
from trend.base import TrendType
from signals.feature import is_umbrella, is_engulfing


def umbrella(df: pd.DataFrame, trend_window: int = 20,
             shadow_ratio: float = 2.0, upper_ratio: float = 0.1,
             lower_cap: float = 6.0) -> pd.Series:
    """
    Umbrella line reversal signal, returns continuous value in [-1, 1].
      Hammer (downtrend):     signal on umbrella bar itself.
      Hanging man (uptrend):  signal on confirmation day (next close < umbrella body low).
    Strength factors (weights: lower shadow 40%, upper shadow 20%, body size 40%).
    """
    mask = is_umbrella(df, shadow_ratio, upper_ratio)
    body = (df["close"] - df["open"]).abs()
    body_low = df[["open", "close"]].min(axis=1)
    lower_shadow = body_low - df["low"]
    upper_shadow = df["high"] - df[["open", "close"]].max(axis=1)
    total_range = df["high"] - df["low"]

    result = pd.Series(0.0, index=df.index)
    for i, idx in enumerate(df.index):
        if not mask.loc[idx] or i < trend_window:
            continue
        prior = df.iloc[i - trend_window:i]
        trend = detect_trend(prior)

        b = body.loc[idx]
        lower_score = np.clip((lower_shadow.loc[idx] / b - shadow_ratio) / (lower_cap - shadow_ratio), 0, 1) if b > 0 else 1.0
        upper_score = 1.0 - np.clip(upper_shadow.loc[idx] / (b * upper_ratio), 0, 1) if b > 0 else 1.0
        body_score = 1.0 - np.clip(b / total_range.loc[idx], 0, 1) if total_range.loc[idx] > 0 else 0.0
        strength = 0.4 * lower_score + 0.2 * upper_score + 0.4 * body_score

        if trend.trend == TrendType.DOWNTREND:
            result.loc[idx] = strength

        elif trend.trend == TrendType.UPTREND:
            # hanging man: requires confirmation on next bar
            if i + 1 < len(df):
                next_idx = df.index[i + 1]
                if df["close"].loc[next_idx] < body_low.loc[idx]:
                    result.loc[next_idx] = -strength

    return result


def engulfing(df: pd.DataFrame, trend_window: int = 20, doji_ratio: float = 0.1,
              vol_window: int = 20, body_cap: float = 3.0, vol_cap: float = 3.0) -> pd.Series:
    """
    Engulfing reversal signal, returns continuous value in [-1, 1].
    Direction: 1 = bullish reversal, -1 = bearish reversal, 0 = no signal.
    Strength factors (weights: volume 60%, body ratio 40%):
      - body_ratio_score: how much larger second body is vs first, clipped to [0, 1]
      - vol_score:        how much volume exceeds rolling average, clipped to [0, 1]
    """
    mask = is_engulfing(df, doji_ratio)
    bullish_bar = df["close"] > df["open"]
    body = (df["close"] - df["open"]).abs()
    avg_vol = df["volume"].rolling(vol_window).mean()

    result = pd.Series(0.0, index=df.index)
    for i, idx in enumerate(df.index):
        if not mask.loc[idx] or i < trend_window:
            continue
        prior = df.iloc[i - trend_window:i]
        trend = detect_trend(prior)

        if trend.trend == TrendType.DOWNTREND and bullish_bar.loc[idx]:
            direction = 1.0
        elif trend.trend == TrendType.UPTREND and not bullish_bar.loc[idx]:
            direction = -1.0
        else:
            continue

        prev_idx = df.index[i - 1]
        prev_body = body.loc[prev_idx]
        body_ratio_score = np.clip((body.loc[idx] / prev_body - 1) / (body_cap - 1), 0, 1) if prev_body > 0 else 0.0

        avg = avg_vol.loc[idx]
        vol_score = np.clip((df["volume"].loc[idx] / avg - 1) / (vol_cap - 1), 0, 1) if avg > 0 else 0.0

        strength = 0.4 * body_ratio_score + 0.6 * vol_score
        result.loc[idx] = direction * strength

    return result
