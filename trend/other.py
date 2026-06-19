from .base import TrendResult, TrendType, TrendIntensity

def detect_other(bars):
    return TrendResult(TrendType.OTHER, 1.0, "no dominant trend detected", TrendIntensity.NORMAL)
