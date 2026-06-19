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
_MIN_BARS = 3


def _run_pipeline(bars, pipeline, min_confidence):
    best = TrendResult(TrendType.OTHER, 0.0, "no detector matched")
    for fn, kwargs in pipeline:
        result = fn(bars, **kwargs)
        if result.trend != TrendType.OTHER and result.confidence >= min_confidence:
            if result.confidence > best.confidence:
                best = result
    if best.trend == TrendType.OTHER:
        best = detect_other(bars)
    if best.is_abnormal:
        best = wrap_as_abnormal(best)
    return best


def _compute_duration(bars, target_trend, pipeline, min_confidence):
    """
    Binary search for the earliest bar index k such that bars[k:] still
    yields target_trend. Duration = len(bars) - k.
    """
    n = len(bars)
    lo, hi = 0, n - _MIN_BARS   # k in [0, n-_MIN_BARS]

    while lo < hi:
        mid = (lo + hi) // 2
        candidate = _run_pipeline(bars.iloc[mid:], pipeline, min_confidence)
        if candidate.trend == target_trend:
            hi = mid          # can go further back
        else:
            lo = mid + 1      # too far back, shrink window

    # verify lo is still valid
    candidate = _run_pipeline(bars.iloc[lo:], pipeline, min_confidence)
    if candidate.trend == target_trend:
        return n - lo
    return n - (lo + 1)


def detect_trend(bars, n=None, pipeline=None, min_confidence=0.4, compute_duration=True):
    """
    Identify the dominant trend over the last n bars.

    Args:
        bars:             OHLCV DataFrame, ascending datetime index.
        n:                Look-back window (uses all bars if None).
        pipeline:         Custom list of (fn, kwargs) to override defaults.
        min_confidence:   Detector must meet this threshold to win.
        compute_duration: If True, also compute how many consecutive bars
                          going backwards are covered by the detected trend.

    Returns:
        TrendResult — result.duration is the number of consecutive bars
        (from most recent bar backwards) sharing this trend. 0 if not computed.
    """
    if n is not None:
        bars = bars.iloc[-n:]
    if len(bars) < 2:
        return TrendResult(TrendType.OTHER, 0.0, "insufficient data")

    _pipeline = pipeline if pipeline is not None else _DEFAULT_PIPELINE
    best = _run_pipeline(bars, _pipeline, min_confidence)

    if compute_duration and best.trend != TrendType.OTHER and len(bars) >= _MIN_BARS:
        duration = _compute_duration(bars, best.trend, _pipeline, min_confidence)
        best = TrendResult(
            trend=best.trend,
            confidence=best.confidence,
            description=best.description,
            intensity=best.intensity,
            base_trend=best.base_trend,
            duration=duration,
        )

    return best
