"""震荡检测：围绕均值反复穿越 + 波动足够大。

排在方向性趋势之后：一条干净的线性趋势只会穿越自身均值一次，达不到
`_MIN_CROSSINGS`，所以两者天然互斥；真有边界情况时，由 pipeline 的
优先级顺序（趋势优先）决定，而不是比 confidence 大小。

confidence 语义与其他检测器统一：合格后取 [0.5, 1.0]。
"""
import numpy as np

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


def detect_oscillating(bars, min_crossings=_MIN_CROSSINGS, atr_ratio_min=_ATR_RATIO_MIN):
    close = bars["close"].values.astype(float)
    if len(close) < 4:
        return TrendResult(TrendType.OTHER, 0.0, "not enough bars")

    mean = close.mean()
    crossings = int(np.sum(np.diff(np.sign(close - mean)) != 0))
    high = bars["high"].values.astype(float)
    low = bars["low"].values.astype(float)
    atr_ratio = np.mean(high - low) / mean if mean else 0.0

    if crossings < min_crossings or atr_ratio < atr_ratio_min:
        return TrendResult(TrendType.OTHER, 0.0,
                           f"crossings={crossings}, atr_ratio={atr_ratio:.4f} not oscillating")

    # 穿越次数越接近"每 3 根 bar 一次"越典型；波动越接近 STRONG 越典型
    cross_span = max(len(close) / 3.0 - min_crossings, 1e-9)
    cross_score = float(np.clip((crossings - min_crossings) / cross_span, 0.0, 1.0))
    atr_span = max(_STRONG_ATR - atr_ratio_min, 1e-9)
    atr_score = float(np.clip((atr_ratio - atr_ratio_min) / atr_span, 0.0, 1.0))
    confidence = 0.5 + 0.5 * float(np.sqrt(cross_score * atr_score))

    return TrendResult(TrendType.OSCILLATING, confidence,
                       f"crossings={crossings}, atr_ratio={atr_ratio:.4f}",
                       intensity=_intensity(atr_ratio))
