import numpy as np
import pandas as pd
from .base import TrendResult, TrendType, TrendIntensity

_MIN_CROSSINGS = 3
_ATR_RATIO_MIN = 0.015
_STRONG_ATR    = 0.030
_EXTREME_ATR   = 0.060

def _intensity(atr_ratio):
    if atr_ratio >= _EXTREME_ATR:
        return TrendIntensity.EXTREME
    if atr_ratio >= _STRONG_ATR:
        return TrendIntensity.STRONG
    return TrendIntensity.NORMAL

def detect_oscillating(bars):
    close = bars["close"].values.astype(float)
    if len(close) < 4:
        return TrendResult(TrendType.OTHER, 0.0, "not enough bars")
    mean      = close.mean()
    diff_sign = np.diff(np.sign(close - mean))
    crossings = int(np.sum(diff_sign != 0))
    high      = bars["high"].values.astype(float)
    low       = bars["low"].values.astype(float)
    atr_ratio = np.mean(high - low) / mean if mean else 0.0
    if crossings >= _MIN_CROSSINGS and atr_ratio >= _ATR_RATIO_MIN:
        confidence = min(1.0, crossings / max(_MIN_CROSSINGS, len(close) / 8))
        return TrendResult(TrendType.OSCILLATING, confidence,
                           f"crossings={crossings}, atr_ratio={atr_ratio:.4f}",
                           intensity=_intensity(atr_ratio))
    return TrendResult(TrendType.OTHER, 0.0,
                       f"crossings={crossings}, atr_ratio={atr_ratio:.4f} not oscillating")
