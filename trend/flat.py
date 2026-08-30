"""横盘检测：极窄价格区间 + 极低波动。

这是最严格、最具体的一类，所以在 pipeline 里排第一——一旦成立，其他类型
在定义上都不可能成立（区间窄到这个程度，斜率和均值穿越都没有意义）。

confidence 语义与其他检测器统一：合格后取 [0.5, 1.0]，
0.5 = 刚好压在阈值上，1.0 = 完全没动。
"""
import numpy as np

from .base import TrendResult, TrendType, TrendIntensity


def detect_flat(bars, price_range_max=0.01, atr_ratio_max=0.005):
    close = bars["close"].values.astype(float)
    if len(close) < 2:
        return TrendResult(TrendType.OTHER, 0.0, "not enough bars")

    mean = close.mean()
    price_range = (close.max() - close.min()) / mean if mean else 0.0
    high = bars["high"].values.astype(float)
    low = bars["low"].values.astype(float)
    atr_ratio = np.mean(high - low) / mean if mean else 0.0

    if price_range > price_range_max or atr_ratio > atr_ratio_max:
        return TrendResult(TrendType.OTHER, 0.0,
                           f"range={price_range:.4f}, atr_ratio={atr_ratio:.4f} not flat")

    # 两个维度取最差的那个决定分数：只要有一项贴着阈值，就不算典型横盘
    slack = 1.0 - max(price_range / max(price_range_max, 1e-12),
                      atr_ratio / max(atr_ratio_max, 1e-12))
    confidence = 0.5 + 0.5 * float(np.clip(slack, 0.0, 1.0))

    return TrendResult(TrendType.FLAT, confidence,
                       f"range={price_range:.4f}, atr_ratio={atr_ratio:.4f}",
                       intensity=TrendIntensity.NORMAL)
