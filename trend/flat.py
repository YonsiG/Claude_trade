import numpy as np
import pandas as pd
from .base import TrendResult, TrendType, TrendIntensity

def detect_flat(bars, price_range_max=0.01, atr_ratio_max=0.005):
    close = bars["close"].values.astype(float)
    if len(close) < 2:
        return TrendResult(TrendType.OTHER, 0.0, "not enough bars")
    mean        = close.mean()
    price_range = (close.max() - close.min()) / mean if mean else 0.0
    high        = bars["high"].values.astype(float)
    low         = bars["low"].values.astype(float)
    atr_ratio   = np.mean(high - low) / mean if mean else 0.0
    if price_range <= price_range_max and atr_ratio <= atr_ratio_max:
        confidence = 1.0 - price_range / (price_range_max + 1e-9)
        return TrendResult(TrendType.FLAT, float(np.clip(confidence,0,1)),
                           f"range={price_range:.4f}, atr_ratio={atr_ratio:.4f}",
                           intensity=TrendIntensity.NORMAL)
    return TrendResult(TrendType.OTHER, 0.0, f"range={price_range:.4f} not flat")
