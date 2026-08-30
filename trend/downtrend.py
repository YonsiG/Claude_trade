"""下降趋势检测。判定门槛与 confidence 语义完全对称于 uptrend.py。"""
from .base import TrendResult, TrendType, TrendIntensity
from .uptrend import fit_line, grade, _SLOPE_MIN, _R2_MIN
from .uptrend import _STRONG_THRESHOLD, _EXTREME_THRESHOLD


def _intensity(slope_pct):
    abs_pct = abs(slope_pct)
    if abs_pct >= _EXTREME_THRESHOLD:
        return TrendIntensity.EXTREME
    if abs_pct >= _STRONG_THRESHOLD:
        return TrendIntensity.STRONG
    return TrendIntensity.NORMAL


def detect_downtrend(bars, slope_min=_SLOPE_MIN, r2_min=_R2_MIN):
    slope_pct, r2 = fit_line(bars)
    if slope_pct is None:
        return TrendResult(TrendType.OTHER, 0.0, "not enough bars")

    if -slope_pct < slope_min:
        return TrendResult(TrendType.OTHER, 0.0,
                           f"slope_pct={slope_pct:.5f} > -{slope_min} not downtrend")
    if r2 < r2_min:
        return TrendResult(TrendType.OTHER, 0.0,
                           f"slope_pct={slope_pct:.5f} but r2={r2:.2f} < {r2_min} (not linear)")

    return TrendResult(TrendType.DOWNTREND,
                       grade(slope_pct, r2, slope_min, r2_min),
                       f"slope_pct={slope_pct:.5f}, r2={r2:.2f}",
                       intensity=_intensity(slope_pct))
