import pandas as pd
from typing import Sequence

from .base import TrendResult, TrendType, TrendIntensity
from .uptrend import detect_uptrend
from .downtrend import detect_downtrend
from .oscillating import detect_oscillating
from .flat import detect_flat
from .other import detect_other
from .abnormal import wrap_as_abnormal

_DEFAULT_PIPELINE = [
    (detect_flat,        {}),
    (detect_uptrend,     {}),
    (detect_downtrend,   {}),
    (detect_oscillating, {}),
]

def detect_trend(bars, n=None, pipeline=None, min_confidence=0.4):
    """
    Identify the dominant trend over the last n bars.
    Returns TrendType.ABNORMAL (6) when any trend hits EXTREME intensity.
    result.base_trend holds the underlying trend in that case.
    """
    if n is not None:
        bars = bars.iloc[-n:]
    if len(bars) < 2:
        return TrendResult(TrendType.OTHER, 0.0, "insufficient data")
    _pipeline = pipeline if pipeline is not None else _DEFAULT_PIPELINE
    best = TrendResult(TrendType.OTHER, 0.0, "no detector matched")
    for fn, kwargs in _pipeline:
        result = fn(bars, **kwargs)
        if result.trend != TrendType.OTHER and result.confidence >= min_confidence:
            if result.confidence > best.confidence:
                best = result
    if best.trend == TrendType.OTHER:
        best = detect_other(bars)
    if best.is_abnormal:
        best = wrap_as_abnormal(best)
    return best
