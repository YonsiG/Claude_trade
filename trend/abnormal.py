from .base import TrendResult, TrendType, TrendIntensity

def wrap_as_abnormal(result):
    return TrendResult(
        trend=TrendType.ABNORMAL,
        confidence=result.confidence,
        description=f"ABNORMAL({result.trend.name}): {result.description}",
        intensity=TrendIntensity.EXTREME,
        base_trend=result.trend,
    )
