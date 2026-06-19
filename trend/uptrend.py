import numpy as np
import pandas as pd
from .base import TrendResult, TrendType, TrendIntensity

_STRONG_THRESHOLD  = 0.003
_EXTREME_THRESHOLD = 0.008

def _intensity(slope_pct):
    if slope_pct >= _EXTREME_THRESHOLD:
        return TrendIntensity.EXTREME
    if slope_pct >= _STRONG_THRESHOLD:
        return TrendIntensity.STRONG
    return TrendIntensity.NORMAL

def detect_uptrend(bars, slope_min=0.0):
    close = bars["close"].values.astype(float)
    x = np.arange(len(close))
    if len(x) < 2:
        return TrendResult(TrendType.OTHER, 0.0, "not enough bars")
    coeffs    = np.polyfit(x, close, 1)
    slope     = coeffs[0]
    slope_pct = slope / close.mean() if close.mean() else 0.0
    predicted = np.polyval(coeffs, x)
    ss_res = np.sum((close - predicted) ** 2)
    ss_tot = np.sum((close - close.mean()) ** 2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    if slope > slope_min:
        return TrendResult(TrendType.UPTREND, float(np.clip(r2,0,1)),
                           f"slope_pct={slope_pct:.4f}", intensity=_intensity(slope_pct))
    return TrendResult(TrendType.OTHER, 0.0, f"slope={slope:.4f} not uptrend")
