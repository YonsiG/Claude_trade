"""ABNORMAL —— 窗口内存在「不可交易的价格断裂」，趋势读数不可信。

设计说明（这里曾经是另一回事）：

  旧实现把 `TrendIntensity.EXTREME`（斜率很陡）直接改写成 `TrendType.ABNORMAL`。
  那实际上是在用"趋势很陡"冒充"数据异常"——陡峭的趋势是**真实行情**，
  而且恰恰是形态信号最有意义的地方。实测 AAPL 上 10% 的 bar 因此被吞掉。

  现在两者彻底分开：
    - 陡峭趋势 → 仍是 UPTREND / DOWNTREND，只是 intensity = EXTREME。
    - ABNORMAL → 窗口内出现了**换月导致的合约切换**（或涨跌停、坏数据等
      不可交易的断裂）。此时窗口里的 bar 来自两份不同的合约，
      跨断裂点的形态（吞噬需要两根、趋势拟合需要一串）都读不得。

换月的判定复用 `data/adjust.py`，那里以持仓量跳变为主信号。注意即使数据
已经做过后复权（loader 对期货默认开启），**价格断裂被抹平了，合约切换本身
仍然存在**——所以这一层依然要标，而且靠 hold 而不是靠价格跳空才标得准。

只对期货生效：股票没有换月，默认不检查（见 detector.detect_trend 的
`check_abnormal` 参数，默认按数据里有没有 hold 列自动判断）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data.adjust import (detect_rollover, has_hold, DEFAULT_GAP_THRESHOLD,
                         DEFAULT_HOLD_THRESHOLD)
from .base import TrendResult, TrendType, TrendIntensity


def detect_abnormal(bars: pd.DataFrame,
                    roll_dates=None,
                    gap_threshold: float = DEFAULT_GAP_THRESHOLD,
                    hold_threshold: float = DEFAULT_HOLD_THRESHOLD,
                    abnormal_bars=None) -> TrendResult:
    """
    窗口内是否存在价格断裂。有则返回 ABNORMAL，否则返回 OTHER。

    只看窗口**内部**的断裂：首根 bar 的跳空指向窗口之外的 bar，
    若换月恰好发生在首根，则窗口内全部来自新合约，是自洽的，不算异常。

    Args:
        abnormal_bars: 只检查窗口**末尾** N 根 bar 内的断裂。None（默认）= 整个窗口。

            这个参数在控制严格程度，代价很实在：换月一次会污染
            `窗口长度` 个窗口。RB0 五年 12 次换月、20 根窗口 →
            228/1331 个窗口（17%）被标为 ABNORMAL；取 N=2 则降到 ~2%。

            怎么选取决于你的信号跨几根 bar：
              - 数据已做后复权（loader 对期货默认开启），价格序列本身是连续的，
                趋势拟合跨换月点问题不大 → N 取信号需要的 bar 数（吞噬取 2）。
              - 想严格排除任何跨合约的读数 → 保持 None。
    """
    if len(bars) < 2 or "open" not in bars.columns or "close" not in bars.columns:
        return TrendResult(TrendType.OTHER, 0.0, "not enough bars")

    is_roll = detect_rollover(bars, gap_threshold, hold_threshold, roll_dates)
    is_roll.iloc[0] = False  # 首根的断裂属于窗口之外

    if abnormal_bars is not None and abnormal_bars < len(bars):
        is_roll.iloc[:len(bars) - abnormal_bars] = False

    if not is_roll.any():
        return TrendResult(TrendType.OTHER, 0.0, "no discontinuity")

    where = bars.index[is_roll]
    if roll_dates is not None:
        src = "roll_dates"
    elif has_hold(bars):
        src = "hold_jump"
    else:
        src = f"gap>{gap_threshold:.0%}(无持仓量，可能误判)"

    # 断裂点距离窗口末尾越近，对当前形态的污染越重
    last = int(np.flatnonzero(is_roll.values).max())
    recency = (last + 1) / len(bars)

    return TrendResult(
        trend=TrendType.ABNORMAL,
        confidence=float(min(1.0, 0.5 + 0.5 * recency)),
        description=f"discontinuity via {src} at {[str(d) for d in where]}",
        intensity=TrendIntensity.NORMAL,
    )
