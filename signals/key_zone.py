"""关键区突破信号——`platform.py` 的对照组，规则明显更严格、更贴近真实交易约束。

两者的定位不同，不是谁取代谁：`platform.py` 用倒序扫描回答"今天回头看，
这个价位历史上被验证过几次"；这里用**正向逐 bar 状态机**维护一组关键区，
更接近真实交易时"眼睁睁看着一个区间被反复测试、然后突破"的过程，并且
明确处理了 platform.py 没有严格处理的三件事：

1. **不能用未来数据确认局域高低点**——局域高点要等右侧 2 根 K 线走完才算数，
   在那之前，回测里不能提前用到它。
2. **横盘不算重复验证**——两次触碰之间，价格必须先离开区间 1 个 ATR 以上，
   再回来测试，才算一次独立验证；纯粹的横盘（没离开过）不算。
3. **区间一旦建立就不再随后续触碰移动**，避免"不断修改关键位去迎合行情"。

## 算法

1. **ATR(14)**：简单滚动均值（不是 Wilder 平滑），`TR = max(high-low,
   |high-prev_close|, |low-prev_close|)`。

2. **局域高点/低点**：严格 5 日 pivot（左右各 2 根，`local_window` 可调），
   用**严格不等号**（不像 `platform.py` 的 3 日 pivot 允许打平）：
   `high[i] > high[i-2:i].max() and high[i] > high[i+1:i+3].max()`。
   **在 `i+local_window` 根 bar 之后才算确认**——右侧的 K 线走完之前，
   这一天是不是局域高点还不知道，正向状态机严格按这个时间点才把它纳入考虑。

3. **候选区**：确认到一个新局域高点 K，若它落在某个仍然有效（未失效/未过期/
   未交易过）的既有阻力区 `[L,U]` 内，算作对那个区的一次触碰（走第 4 步的
   独立验证判定）；否则新建一个区：`L=K-0.25A, U=K+0.25A`，`A` 取这个高点
   确认时刻最新已完成 K 线的 ATR，**固定不变**。低点对称建立支撑区。

4. **独立验证**：两次触碰之间，价格必须跌破阻力区下沿 `L` 以下 1 个 `A`
   （支撑区反向：涨破上沿 `U` 以上 1 个 `A`），才算"离开过"；没离开过的
   新触碰不计入验证次数，也不重置"离开"状态。触碰之间还要求至少间隔
   `min_gap_bars`（默认 5）根 bar。

5. **有效期**：确认时刻起超过 `lookback_bars`（默认 60）根 bar，或最近一次
   触碰起超过 `max_since_touch`（默认 20）根 bar，区间过期，不再参与后续
   判定（但已经形成的信号不受影响）。

6. **提前突破作废**：验证次数不够 `required_touches` 之前，收盘价就已经
   显著突破（`close > U + 0.25A`，对称：`close < L - 0.25A`），这个区间
   永久作废，之后就算价格再回来触碰也不能重新计入——不允许"事后补票"。

7. **突破信号 + 一次性**：验证次数达到 `required_touches` 后，收盘价首次
   显著突破 `U + 0.25A`（支撑对称：`L - 0.25A`）——**这是关键区唯一一次
   突破信号**，不管后面执行不执行，这个区都不再产生第二次信号。
   同一根 bar 多个区间同时满足条件，多头选**突破前离收盘价最近**的那个
   （空头对称），别的区间当天不发信号（也不再有第二次机会）。

8. **执行**：突破信号发生在 bar T，在 T+1 的开盘价执行，且开盘价必须落在
   可接受区间内（多头 `(U, U+A]`，空头 `[L-A, L)`）——超出这个范围（跳空
   太远）或者反向跳空（开盘价没能维持在区间外），直接放弃这笔交易，
   不递延、不追价。

用法：

```python
from signals.key_zone import key_zone_breakout, detect_key_zones

sig = key_zone_breakout(df, required_touches=3)   # 基准版：3 次验证（含建区那次）
sig2 = key_zone_breakout(df, required_touches=2)  # 对照版：2 次验证
zones = detect_key_zones(df, required_touches=3)  # 完整区间列表，供诊断/画图
```
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Optional

import numpy as np
import pandas as pd


class KeyZoneSignal(NamedTuple):
    """
    关键区突破的开仓信号。

    entry 是事件脉冲（只在信号产生的那根 bar 非 0，语义和 `signals.base.Signal`
    一致）——和 `platform.py` 的 `PlatformSignal`（条件持续成立）不同，这里
    "每个关键区只有一次机会"本身就是一次性事件，不需要"条件持续成立"那种
    去重手段。

    stop_price 用建区时固定的 A 算（`L-0.20A`/`U+0.20A`），touch_count
    是这个区间总共被触及的次数（**含建区那一次**，和 `platform.py` 的
    `validations` 不算种子那次的口径不同，注意区分）。

    exec_lo/exec_hi 是次日开盘可接受区间——策略层用它判断次日开盘要不要
    放弃这笔交易；signal 本身不做这个判断，因为"次日开盘价是多少"要等到
    那根 bar 才知道。zone_low/zone_high 是区间本身的 [L,U]，止盈止损公式
    要用到。
    """
    entry: pd.Series
    stop_price: pd.Series
    touch_count: pd.Series
    exec_lo: pd.Series
    exec_hi: pd.Series
    zone_low: pd.Series
    zone_high: pd.Series


@dataclass
class Zone:
    direction: int          # +1 阻力(做多突破) / -1 支撑(做空突破)
    low: float               # L
    high: float              # U
    atr: float                # 建区时固定的 A
    established_idx: int
    last_touch_idx: int
    touch_count: int = 1
    left_since_last_touch: bool = False
    status: str = "building"   # building / confirmed / invalidated / expired / traded


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """简单滚动均值版 ATR（不是 Wilder 平滑），含跳空：TR 取三者最大。"""
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def find_strict_pivots(high: pd.Series, low: pd.Series, k: int = 2):
    """
    严格 `2k+1` 日 pivot（左右各 `k` 根），**严格不等号**，不允许打平。
    返回两个布尔 Series，在位置 `i` 为 True 表示"bar i 是局域高/低点"——
    这是"是不是"的判定，不代表"现在就能用"，右侧 `k` 根走完之前不能用
    （由调用方的正向状态机负责延迟到 `i+k` 之后才纳入考虑）。
    """
    h, l = high.values, low.values
    n = len(h)
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    for i in range(k, n - k):
        if h[i] > h[i - k:i].max() and h[i] > h[i + 1:i + k + 1].max():
            is_high[i] = True
        if l[i] < l[i - k:i].min() and l[i] < l[i + 1:i + k + 1].min():
            is_low[i] = True
    return pd.Series(is_high, index=high.index), pd.Series(is_low, index=high.index)


def _find_matching_zone(zones: list[Zone], direction: int, price: float) -> Optional[Zone]:
    """去重：新局域高/低点落在某个仍然"活跃"（未失效/未过期/未交易）的既有
    同方向区间 [L,U] 内，就算作对那个区的触碰，不新建区间。"""
    for z in zones:
        if z.direction == direction and z.status in ("building", "confirmed") \
                and z.low <= price <= z.high:
            return z
    return None


def _run_state_machine(df: pd.DataFrame, local_window: int, atr_period: int,
                       zone_width_atr: float, confirm_atr: float, stop_atr: float,
                       min_gap_bars: int, independent_atr: float, lookback_bars: int,
                       max_since_touch: int, required_touches: int, emit_signals: bool):
    """
    共享的正向状态机：`detect_key_zones` 和 `key_zone_breakout` 都基于这
    一次扫描，避免同一段复杂逻辑维护两份（这类重复正是本模块想避免的
    "两份实现分叉"问题本身）。`emit_signals=False` 时只返回最终 zones
    列表；`True` 时额外返回逐 bar 的信号数组。
    """
    n = len(df)
    high, low, close = df["high"], df["low"], df["close"]
    atr = compute_atr(df, atr_period)
    is_ph, is_pl = find_strict_pivots(high, low, local_window)
    h_vals, l_vals, c_vals, atr_vals = high.values, low.values, close.values, atr.values

    zones: list[Zone] = []
    sig = None
    if emit_signals:
        sig = {
            "entry": np.zeros(n, dtype=int), "stop_price": np.full(n, np.nan),
            "touch_count": np.zeros(n, dtype=int),
            "exec_lo": np.full(n, np.nan), "exec_hi": np.full(n, np.nan),
            "zone_low": np.full(n, np.nan), "zone_high": np.full(n, np.nan),
        }

    for t in range(n):
        # 1. 确认 t-local_window 位置的 pivot（右侧 local_window 根已走完）
        conf_idx = t - local_window
        if conf_idx >= 0 and not np.isnan(atr_vals[conf_idx]):
            for is_piv, arr, direction in ((is_ph, h_vals, 1), (is_pl, l_vals, -1)):
                if not is_piv.iloc[conf_idx]:
                    continue
                K = arr[conf_idx]
                matched = _find_matching_zone(zones, direction, K)
                if matched is not None:
                    if matched.left_since_last_touch and \
                            (conf_idx - matched.last_touch_idx) >= min_gap_bars:
                        matched.touch_count += 1
                        matched.last_touch_idx = conf_idx
                        matched.left_since_last_touch = False
                else:
                    a = atr_vals[conf_idx]
                    zones.append(Zone(direction, K - zone_width_atr * a,
                                      K + zone_width_atr * a, a, conf_idx, conf_idx))

        # 2. 用今天的 high/low 更新"是否已离开过"
        for z in zones:
            if z.status not in ("building", "confirmed"):
                continue
            if z.direction > 0 and l_vals[t] <= z.low - independent_atr * z.atr:
                z.left_since_last_touch = True
            elif z.direction < 0 and h_vals[t] >= z.high + independent_atr * z.atr:
                z.left_since_last_touch = True

        # 3. 提前突破作废 / 过期 / 确认
        for z in zones:
            if z.status not in ("building", "confirmed"):
                continue
            if z.touch_count < required_touches:
                if z.direction > 0 and c_vals[t] > z.high + confirm_atr * z.atr:
                    z.status = "invalidated"
                    continue
                if z.direction < 0 and c_vals[t] < z.low - confirm_atr * z.atr:
                    z.status = "invalidated"
                    continue
            if (t - z.established_idx) > lookback_bars or \
                    (t - z.last_touch_idx) > max_since_touch:
                z.status = "expired"
                continue
            if z.touch_count >= required_touches and z.status == "building":
                z.status = "confirmed"

        if not emit_signals:
            continue

        # 4. 突破信号：confirmed 区间里，收盘价显著突破
        long_candidates = [z for z in zones if z.status == "confirmed" and z.direction > 0
                           and c_vals[t] > z.high + confirm_atr * z.atr]
        short_candidates = [z for z in zones if z.status == "confirmed" and z.direction < 0
                            and c_vals[t] < z.low - confirm_atr * z.atr]

        chosen = None
        if long_candidates:
            chosen = min(long_candidates, key=lambda z: c_vals[t] - z.high)
        elif short_candidates:
            chosen = min(short_candidates, key=lambda z: z.low - c_vals[t])

        if chosen is not None:
            chosen.status = "traded"   # 不管下一根开盘执不执行，机会用掉了
            if chosen.direction > 0:
                sig["entry"][t] = 1
                sig["stop_price"][t] = chosen.low - stop_atr * chosen.atr
                sig["exec_lo"][t] = chosen.high
                sig["exec_hi"][t] = chosen.high + 1.0 * chosen.atr
            else:
                sig["entry"][t] = -1
                sig["stop_price"][t] = chosen.high + stop_atr * chosen.atr
                sig["exec_lo"][t] = chosen.low - 1.0 * chosen.atr
                sig["exec_hi"][t] = chosen.low
            sig["touch_count"][t] = chosen.touch_count
            sig["zone_low"][t] = chosen.low
            sig["zone_high"][t] = chosen.high
            # 同一根 bar 里没被选中的候选区当天不发信号，也不再有第二次机会
            for z in long_candidates + short_candidates:
                if z is not chosen:
                    z.status = "traded"

    return zones, sig


def detect_key_zones(df: pd.DataFrame, local_window: int = 2, atr_period: int = 14,
                     zone_width_atr: float = 0.25, confirm_atr: float = 0.25,
                     stop_atr: float = 0.20, min_gap_bars: int = 5,
                     independent_atr: float = 1.0, lookback_bars: int = 60,
                     max_since_touch: int = 20, required_touches: int = 3) -> list[Zone]:
    """
    正向逐 bar 扫描全部历史，返回完整的区间列表（含已失效/过期/成交的，
    供诊断/画图用）。`key_zone_breakout` 跑的是同一套状态机
    （`_run_state_machine`），只是多输出逐 bar 信号——两者结果互相印证。
    """
    zones, _ = _run_state_machine(df, local_window, atr_period, zone_width_atr,
                                  confirm_atr, stop_atr, min_gap_bars, independent_atr,
                                  lookback_bars, max_since_touch, required_touches,
                                  emit_signals=False)
    return zones


def key_zone_breakout(df: pd.DataFrame, local_window: int = 2, atr_period: int = 14,
                      zone_width_atr: float = 0.25, confirm_atr: float = 0.25,
                      stop_atr: float = 0.20, min_gap_bars: int = 5,
                      independent_atr: float = 1.0, lookback_bars: int = 60,
                      max_since_touch: int = 20, required_touches: int = 3,
                      ) -> KeyZoneSignal:
    """收盘价显著突破已确认关键区时发出一次性开仓脉冲，见模块 docstring。"""
    _, sig = _run_state_machine(df, local_window, atr_period, zone_width_atr,
                                confirm_atr, stop_atr, min_gap_bars, independent_atr,
                                lookback_bars, max_since_touch, required_touches,
                                emit_signals=True)
    idx = df.index
    return KeyZoneSignal(
        pd.Series(sig["entry"], idx), pd.Series(sig["stop_price"], idx),
        pd.Series(sig["touch_count"], idx),
        pd.Series(sig["exec_lo"], idx), pd.Series(sig["exec_hi"], idx),
        pd.Series(sig["zone_low"], idx), pd.Series(sig["zone_high"], idx),
    )
