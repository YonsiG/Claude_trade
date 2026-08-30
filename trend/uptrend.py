"""上升趋势检测：线性回归斜率 + 拟合优度。

判定门槛（硬条件，两条都要满足）：
    1. 斜率足够陡：slope_pct >= slope_min（默认 0.08%/bar）
    2. 拟合足够好：R² >= r2_min（默认 0.45）

旧版本只要 `slope > 0` 就判为上升趋势，confidence 直接取 R²。后果是一段
几乎水平但很规整的走势会拿到"高置信度上升趋势"——斜率 0.001%/bar、R²=0.95
这种，方向读数毫无意义却压过了真正的震荡判定。

confidence 的语义（全模块统一，见 trend/README.md）：
    合格才有 confidence，取值 [0.5, 1.0]。
    0.5 = 刚好压线达标，1.0 = 教科书级别。
    不合格直接返回 OTHER，而不是返回一个低 confidence 让上层去比大小。
"""
import numpy as np

from .base import TrendResult, TrendType, TrendIntensity

_SLOPE_MIN = 0.0008          # 每 bar 涨幅占均价比例，低于此不算趋势
_R2_MIN    = 0.45            # 低于此说明走势不是线性的，斜率没有代表性
_STRONG_THRESHOLD  = 0.003
_EXTREME_THRESHOLD = 0.008


def _intensity(slope_pct):
    if slope_pct >= _EXTREME_THRESHOLD:
        return TrendIntensity.EXTREME
    if slope_pct >= _STRONG_THRESHOLD:
        return TrendIntensity.STRONG
    return TrendIntensity.NORMAL


def fit_line(bars):
    """返回 (slope_pct, r2)。供 uptrend / downtrend 共用。"""
    close = bars["close"].values.astype(float)
    if len(close) < 2:
        return None, None
    x = np.arange(len(close))
    coeffs = np.polyfit(x, close, 1)
    mean = close.mean()
    slope_pct = coeffs[0] / mean if mean else 0.0
    predicted = np.polyval(coeffs, x)
    ss_res = np.sum((close - predicted) ** 2)
    ss_tot = np.sum((close - mean) ** 2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope_pct), float(np.clip(r2, 0.0, 1.0))


def grade(slope_pct, r2, slope_min, r2_min):
    """合格度打分 -> [0.5, 1.0]。斜率与拟合取几何平均，任一项差都会拉低。"""
    span = max(_STRONG_THRESHOLD - slope_min, 1e-9)
    slope_score = float(np.clip((abs(slope_pct) - slope_min) / span, 0.0, 1.0))
    r2_score = float(np.clip((r2 - r2_min) / max(1.0 - r2_min, 1e-9), 0.0, 1.0))
    return 0.5 + 0.5 * float(np.sqrt(slope_score * r2_score))


def detect_uptrend(bars, slope_min=_SLOPE_MIN, r2_min=_R2_MIN):
    slope_pct, r2 = fit_line(bars)
    if slope_pct is None:
        return TrendResult(TrendType.OTHER, 0.0, "not enough bars")

    if slope_pct < slope_min:
        return TrendResult(TrendType.OTHER, 0.0,
                           f"slope_pct={slope_pct:.5f} < {slope_min} not uptrend")
    if r2 < r2_min:
        return TrendResult(TrendType.OTHER, 0.0,
                           f"slope_pct={slope_pct:.5f} but r2={r2:.2f} < {r2_min} (not linear)")

    return TrendResult(TrendType.UPTREND,
                       grade(slope_pct, r2, slope_min, r2_min),
                       f"slope_pct={slope_pct:.5f}, r2={r2:.2f}",
                       intensity=_intensity(slope_pct))
