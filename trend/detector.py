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


def _no_reversal(bars, trend):
    """
    Confirm trend has not reversed:
    - UPTREND:   the window high must appear in the last 2 bars.
    - DOWNTREND: the window low must appear in the last 2 bars.
    """
    if trend not in (TrendType.UPTREND, TrendType.DOWNTREND, TrendType.ABNORMAL):
        return True
    if len(bars) < 2:
        return True

    base = trend if trend != TrendType.ABNORMAL else None  # checked after wrap; use high/low col
    highs = bars["high"].values
    lows  = bars["low"].values

    # For ABNORMAL we still check using base direction; caller passes original
    # trend via base_trend but here we infer from the pipeline result.
    # Simple heuristic: if up-like (slope positive) check high, else check low.
    # We check both conservatively: uptrend needs recent high, downtrend needs recent low.
    peak_idx   = int(highs.argmax())
    trough_idx = int(lows.argmin())
    last_two   = {len(bars) - 1, len(bars) - 2}

    if trend == TrendType.UPTREND:
        return peak_idx in last_two
    if trend == TrendType.DOWNTREND:
        return trough_idx in last_two
    if trend == TrendType.ABNORMAL:
        # check both; at least one direction must hold
        return peak_idx in last_two or trough_idx in last_two
    return True


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
    """Binary search: earliest k where bars[k:] still yields target_trend."""
    n = len(bars)
    lo, hi = 0, n - _MIN_BARS

    while lo < hi:
        mid = (lo + hi) // 2
        candidate = _run_pipeline(bars.iloc[mid:], pipeline, min_confidence)
        if candidate.trend == target_trend:
            hi = mid
        else:
            lo = mid + 1

    candidate = _run_pipeline(bars.iloc[lo:], pipeline, min_confidence)
    if candidate.trend == target_trend:
        return n - lo
    return n - (lo + 1)


def detect_trend(bars, n=10, pipeline=None, min_confidence=0.4, compute_duration=True):
    """
    Identify the dominant trend over the last n bars (default 10).

    Reversal guard: UPTREND is invalidated if the window high does not
    appear in the last 2 bars; DOWNTREND is invalidated if the window low
    does not appear in the last 2 bars. Both downgrade to OTHER.

    Args:
        bars:             OHLCV DataFrame, ascending datetime index.
        n:                Look-back window, default 10.
        pipeline:         Custom list of (fn, kwargs) to override defaults.
        min_confidence:   Detector must meet this threshold to win.
        compute_duration: Compute how many consecutive bars going backwards
                          share this trend (binary search, O log n).

    Returns:
        TrendResult — result.duration is 0 if compute_duration=False.
    """
    bars = bars.iloc[-n:]
    if len(bars) < 2:
        return TrendResult(TrendType.OTHER, 0.0, "insufficient data")

    _pipeline = pipeline if pipeline is not None else _DEFAULT_PIPELINE
    best = _run_pipeline(bars, _pipeline, min_confidence)

    if not _no_reversal(bars, best.trend):
        best = TrendResult(TrendType.OTHER, 0.0,
                           f"reversal detected: {best.trend.name} invalidated")

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
