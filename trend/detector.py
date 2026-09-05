import pandas as pd

from .base import TrendResult, TrendType, TrendIntensity
from .uptrend import detect_uptrend
from .downtrend import detect_downtrend
from .oscillating import detect_oscillating
from .flat import detect_flat
from .other import detect_other
from .abnormal import detect_abnormal

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
    Other trend types are unaffected.
    """
    if trend not in (TrendType.UPTREND, TrendType.DOWNTREND):
        return True
    if len(bars) < 2:
        return True

    last_two = {len(bars) - 1, len(bars) - 2}
    if trend == TrendType.UPTREND:
        return int(bars["high"].values.argmax()) in last_two
    return int(bars["low"].values.argmin()) in last_two


def _run_pipeline(bars, pipeline, min_confidence):
    """
    按 pipeline 顺序取**第一个**合格的检测器，而不是比 confidence 大小。

    旧实现在各检测器之间用 `confidence` 选优，但那几个数的量纲完全不同
    （flat 是 1-range/max、趋势是 R²、震荡是穿越次数比），直接比大小没有意义。

    现在改成「硬条件互斥 + 优先级顺序」：每个检测器自己判定是否合格，
    不合格返回 OTHER；合格后 confidence 统一落在 [0.5, 1.0]，只用于
    与 `min_confidence` 比较（"我要多典型的匹配"），不再跨类型比大小。

    顺序即优先级，由具体到宽泛：FLAT -> UPTREND -> DOWNTREND -> OSCILLATING。
    """
    for fn, kwargs in pipeline:
        result = fn(bars, **kwargs)
        if result.trend != TrendType.OTHER and result.confidence >= min_confidence:
            return result
    return detect_other(bars)


def _compute_duration(bars, n, target_trend, pipeline, min_confidence):
    """
    从 n 根的判定窗口开始逐根往前扩展，返回仍然维持 target_trend 的最长长度。

    旧实现用二分搜索在 `bars[k:]` 上找"最早仍成立的 k"。但趋势判定对窗口起点
    **不是单调的**——某个 k 不成立，更早的 k 完全可能重新成立——二分在非单调
    谓词上会收敛到任意位置，得出的 duration 是不可靠的数字。而且当时 `bars`
    已被截断成 n 根，二分再怎么找也超不出窗口本身。

    现在改成线性扩展：窗口只有几十根 bar，代价可以忽略，结果是精确的。

    **duration 受你传入的历史长度限制**：只传 n 根 bar 就最多得到 n。
    想测出真实的持续时长，就要把更长的历史传给 detect_trend（它只用最后
    n 根做分类，多余的部分专门用来往回量）。

    **口径注意**：duration 的定义是"仍能被判为该趋势的最长后缀窗口"，
    不等于"肉眼看到的趋势起点"。一段横盘或一段浅回调如果不破坏线性拟合，
    窗口会继续往前扩，duration 就会超出视觉起点。例如 V 形走势里上涨段
    只有 20 根，但把前面 12 根下跌也纳入后整体仍拟合出正斜率，
    duration 会给到 32。把它当作"趋势影响范围"而不是"趋势起始日"来用。
    """
    total = len(bars)
    duration = min(n, total)
    d = duration
    while d < total:
        d += 1
        if _run_pipeline(bars.iloc[-d:], pipeline, min_confidence).trend == target_trend:
            duration = d
        else:
            break
    return duration


def detect_trend(bars, n=10, pipeline=None, min_confidence=0.4, compute_duration=True,
                 check_abnormal=None, roll_dates=None,
                 gap_threshold=0.02, hold_threshold=0.30, abnormal_bars=None):
    """
    Identify the dominant trend over the last n bars (default 10).

    检查顺序：
      1. ABNORMAL —— 窗口内是否存在不可交易的价格断裂（期货换月为主）。
         命中则短路返回，因为跨合约的窗口做趋势拟合没有意义。
         `base_trend` 仍带上断裂前的方向读数，供调用方参考。
      2. 常规 pipeline（flat / uptrend / downtrend / oscillating）。
      3. 反转守卫：UPTREND 要求窗口最高点出现在最后 2 根 bar，
         DOWNTREND 要求最低点出现在最后 2 根，否则降级为 OTHER。

    注意：陡峭的趋势现在**保持** UPTREND / DOWNTREND，只是 `intensity`
    为 EXTREME —— 它是真实行情，不再被改写成 ABNORMAL。

    Args:
        bars:             OHLCV DataFrame, ascending datetime index.
        n:                Look-back window, default 10.
        pipeline:         Custom list of (fn, kwargs) to override defaults.
        min_confidence:   Detector must meet this threshold to win.
        compute_duration: Compute how many consecutive bars going backwards
                          share this trend (binary search, O log n).
        check_abnormal:   是否做换月/断裂检查。None（默认）= 自动：数据带
                          hold（持仓量）列或给了 roll_dates 就开，否则关。
                          股票天然没有换月，自动模式下不会误触发。
                          yfinance 期货（GC=F 等）没有 hold 列，需显式传 True
                          才会启用——那种情况下只能用价格跳空阈值，可靠性较低。
        roll_dates:       已知的真实换月日，给了就只认它。
        gap_threshold:    无持仓量时的价格跳空阈值。
        hold_threshold:   持仓量跳变阈值，默认 30%。
        abnormal_bars:    只在窗口末尾 N 根 bar 内查断裂。None（默认）= 整个窗口。
                          一次换月会污染 `n` 个窗口，所以这个参数直接决定被
                          屏蔽掉多少信号——详见 abnormal.detect_abnormal 的说明。

    Returns:
        TrendResult — result.duration is 0 if compute_duration=False.
    """
    history = bars                    # 保留完整历史，专供 duration 往回量
    bars = bars.iloc[-n:]             # 分类只用最后 n 根
    if len(bars) < 2:
        return TrendResult(TrendType.OTHER, 0.0, "insufficient data")

    _pipeline = pipeline if pipeline is not None else _DEFAULT_PIPELINE

    if check_abnormal is None:
        check_abnormal = roll_dates is not None or "hold" in bars.columns

    if check_abnormal:
        flagged = detect_abnormal(bars, roll_dates, gap_threshold, hold_threshold,
                                  abnormal_bars)
        if flagged.trend == TrendType.ABNORMAL:
            underlying = _run_pipeline(bars, _pipeline, min_confidence)
            return TrendResult(
                trend=TrendType.ABNORMAL,
                confidence=flagged.confidence,
                description=flagged.description,
                intensity=flagged.intensity,
                base_trend=underlying.trend,
            )

    best = _run_pipeline(bars, _pipeline, min_confidence)

    if not _no_reversal(bars, best.trend):
        best = TrendResult(TrendType.OTHER, 0.0,
                           f"reversal detected: {best.trend.name} invalidated")

    if compute_duration and best.trend != TrendType.OTHER and len(bars) >= _MIN_BARS:
        duration = _compute_duration(history, len(bars), best.trend,
                                     _pipeline, min_confidence)
        best = TrendResult(
            trend=best.trend,
            confidence=best.confidence,
            description=best.description,
            intensity=best.intensity,
            base_trend=best.base_trend,
            duration=duration,
        )

    return best
