"""兜底：没有任何检测器合格。

confidence 固定为 0.0——在新的语义下 [0.5, 1.0] 表示"合格且有多典型"，
OTHER 本身就是"不合格"，给它高 confidence 会让上层误以为这是个强判定。
"""
from .base import TrendResult, TrendType, TrendIntensity


def detect_other(bars):
    return TrendResult(TrendType.OTHER, 0.0, "no dominant trend detected",
                       TrendIntensity.NORMAL)
