"""平台突破信号：识别历史关键支撑/压力位（"平台"），价格突破时给出方向脉冲。

## "平台"是什么

在过去若干年的历史里找一个反复被测试过的价格水平——局域高点（压力，未来的
突破方向=做多）或局域低点（支撑，突破方向=做空）。被测试的次数（"验证次数"）
越多，这个价位越"关键"，仓位也应该越大（验证次数怎么影响仓位见
`strategies/platform_breakout.py`）。

## 算法（原始需求里几处口语化描述，这里定死成可实现、可测试的版本）

1. **局域高点/低点** = 标准的 3 日 pivot（`local_window` 可调）：
   `high[d] == max(high[d-1:d+2])` 即 d 的最高价在以它为中心的 3 天窗口里最大。
   低点对称。原文"三天中的最高价"就是这个意思——不是任意 3 日滚动窗口的最大值，
   而是"这一天本身就是这 3 天里最高的那个"，标准技术分析里的摆动高点/低点。

2. **扫描方向**：从 T-1（评估日的前一天）**往回走**（时间逆序），逐个检查
   该方向上更早的 pivot。起点 = 最近的一个 pivot 的价格。

3. **"验证"和"更新"是同一件事，不是两个分支**：新 pivot 落在当前候选价
   附近（容差内）—— 这就是一次验证，同时把候选价**更新**成这个新 pivot 的
   价格（候选价跟着最近一次验证过的价位漂移，不是死死焊在某个历史极值上；
   最终报告的平台价 = 最后一次验证到的价格，不是全局最高/最低价）。
   继续往回扫，直到遇到一个价格**显著突破**当前候选价（超出突破阈值，不只是
   超出验证容差）——这标志着已经找到了这个平台的源头（价格从那里一路冲上来
   才形成了这个平台，再往前的历史跟"当前这个平台"无关），扫描到此为止，
   此时累计的验证次数就是这个平台最终的验证次数。若中途某个 pivot 既没落在
   验证容差内、也没到突破幅度（不远不近的中间地带），当噪音忽略，继续往回扫。
   一直扫到 `lookback_years` 年前（还没遇到显著突破就把历史看完了）或验证
   次数为 0 时遇到显著突破，都视为**该方向暂无平台**。

4. **容差 / 突破阈值的"或"怎么算**：原文"10%（最高-最低）或 20%平均每日
   波动率"—— 两个阈值取**较小的那个**（`min`）：只要满足其中任意一个更严格
   的条件就算数，这样容差不会因为某个尺度算出来特别大就变得形同虚设。

   `最高-最低` 取整个 `lookback_years` 回看窗口内的整体 high-low 区间——
   这一项**不随扫描逐步推进而变化**，每个评估日固定算一次。

   `平均每日波动率`则是**动态的**：从"当前日"（T-1，扫描的起点）到"对应日"
   （倒序扫描当前正在比较的那个历史 pivot 所在的那一天）之间，逐日
   `high-low` 的均值——扫描每往回推进一步，对应日往前挪，这个平均值就要
   重新算一次，不是对整个 3 年窗口只算一次然后全程复用。用累积和数组
   O(1) 查询区间均值，不必每次比较都重新切片。

   突破阈值（用于判定"显著突破"，也用于下面第 5 点的真正开仓判定）用
   同一套组合逻辑（固定的整体区间 + 动态的"当前日到对应日"波动率取更小值），
   只是系数换成 `breakout_range_pct`/`breakout_vol_pct`（原文"平均日波动
   30% 或 10%（最高-最低）"）。开仓判定用的"对应日"是**最后一次验证发生的
   那一天**（扫描到此为止時候选价最后一次被更新的位置）。

5. **开仓判定**（和上面的历史扫描是两回事）：每天用当天收盘价与当天算出的
   平台价 + 突破阈值比较，`close >= high_level + threshold` 时做多、
   `close <= low_level - threshold` 时做空——这是前瞻性的"今天有没有真的
   突破"判定，不是扫描历史时用来找平台源头的那个"显著突破"（那个是回看历史、
   划定平台起点用的，两者阈值公式相同但服务的时间方向不同）。

6. 止损：多头止损价 = 突破时的平台价 - 突破阈值（价格跌回平台且达到反向突破
   幅度）；空头对称。**止损价和验证次数在突破那一刻冻结**，交易期间不再随平台
   本身的演化而改变——平台每天都在重新计算，但一笔交易的风险基准只认开仓那
   一刻的值，否则仓位管理和止损会变得不可预测。

用法：

```python
from signals.platform import platform_breakout, detect_platforms

sig = platform_breakout(df)            # PlatformSignal(entry, stop_price, validations)
info = detect_platforms(df)            # PlatformInfo，逐日的平台价位+验证次数，供画图/诊断
```
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd


class PlatformInfo(NamedTuple):
    """逐 bar 的平台状态，供诊断/画图用。NaN = 该方向当天无平台。"""
    high_level: pd.Series
    high_validations: pd.Series
    low_level: pd.Series
    low_validations: pd.Series


class PlatformSignal(NamedTuple):
    """
    平台突破的开仓信号。

    **entry 的语义和 `signals.base.Signal` 不一样，注意区分**：那边的 entry
    是"事件脉冲"，只在形态成立的那一根 bar 非 0（K 线形态本来就是离散事件）。
    这里的 entry 是"条件持续成立"——只要收盘价还在突破阈值之外，就会连续多根
    bar 保持非 0，直到价格跌回平台内侧。这是有意的：突破更像一个状态而不是
    一次性事件，策略层"只在空仓时响应 entry"天然去重成单次开仓；反而如果
    entry 只在穿越的那一根 bar 触发一次，策略若恰好那天忙于处理别的仓位没能
    响应，这个突破就永久错过了——而"条件持续成立"允许晚几天照样能进场。

    stop_price / validations 只在 entry!=0 的 bar 上有意义，且是**用那一根
    bar 自己的平台状态现算的**（不是开仓那天的旧值）——所以策略在哪根 bar
    真正成交，就该读那根 bar 对应的 stop_price/validations，天然保证止损价
    和仓位风险始终对应"实际开仓时刻"的平台状态。
    """
    entry: pd.Series
    stop_price: pd.Series
    validations: pd.Series


def find_pivots(high: pd.Series, low: pd.Series, window: int = 3):
    """
    标准 3 日（或 `window` 日，须为奇数）pivot high/low。

    两端各 `window//2` 天因为凑不出完整的居中窗口，天然不参与判定
    （`rolling(center=True)` 在窗口不满时返回 NaN，`==` 比较自动为 False）。
    """
    if window % 2 == 0:
        raise ValueError(f"window 必须是奇数，收到 {window}")
    roll_max = high.rolling(window, center=True).max()
    roll_min = low.rolling(window, center=True).min()
    return (high == roll_max).fillna(False), (low == roll_min).fillna(False)


def _avg_range(cum_range: np.ndarray, j: int, t: int) -> float:
    """[j, t) 区间逐日 (high-low) 的均值，O(1) 查询（`cum_range` 是前缀和）。"""
    return (cum_range[t] - cum_range[j]) / (t - j)


def _scan_backward(idx_desc: np.ndarray, price_desc: np.ndarray, t: int,
                   cum_range: np.ndarray, rng: float,
                   touch_range_pct: float, touch_vol_pct: float,
                   breakout_range_pct: float, breakout_vol_pct: float,
                   min_gap_bars: int):
    """
    从新到旧（时间逆序）扫描一串 pivot，返回 (candidate_price, validations,
    last_touch_idx)。`idx_desc` 为空返回 (nan, 0, -1)。

    起点 = 最近的 pivot。此后每个更早的 pivot（位置 j）三选一，容差/突破
    阈值都是**动态**算的——"平均每日波动率"取从当前日 `t` 到对应日 `j`
    这段区间的均值，`最高-最低` 用固定传入的 `rng`（整个回看窗口算一次，
    不随扫描推进变化），两者取 min：
      - 落在容差内 -> **验证 = 更新**：候选价挪到这个新价格，验证数 +1
        （要求离上次验证 >= min_gap_bars 根 bar，否则本次既不算验证也不
        更新，跳过继续扫）。
      - 与候选价相差超过突破阈值（无论更高还是更低）-> **显著突破**，
        说明扫到了这个平台的源头，停止扫描。
      - 不远不近的中间地带 -> 噪音，忽略，继续往更早扫。
    """
    if len(idx_desc) == 0:
        return float("nan"), 0, -1

    candidate = price_desc[0]
    validations = 0
    last_touch_idx = idx_desc[0]

    for i in range(1, len(idx_desc)):
        j = idx_desc[i]
        p = price_desc[i]
        avg_range = _avg_range(cum_range, j, t)
        tol = min(touch_range_pct * rng, touch_vol_pct * avg_range)
        breakout_thr = min(breakout_range_pct * rng, breakout_vol_pct * avg_range)

        diff = abs(candidate - p)
        if diff <= tol:
            if (last_touch_idx - j) >= min_gap_bars:
                validations += 1
                candidate = p
                last_touch_idx = j
            # 间隔不够：忽略（既不算验证也不更新），继续往更早扫
        elif diff > breakout_thr:
            break
        # 中间地带（超过验证容差但没到突破幅度）：噪音，继续往更早扫

    return candidate, validations, last_touch_idx


def _lookback_start(dates: pd.DatetimeIndex, t: int, years: float) -> int:
    """
    `t` 往前 `years` 年对应的最早 bar 位置（用日历年，不是交易日近似）。

    用天数（`365.25 * years`）而不是 `pd.DateOffset(years=years)`：后者只接受
    整数年，非整数年（比如做参数敏感性分析时传 `lookback_years=2.5`）会直接
    抛 `ValueError: Non-integer years and months are ambiguous`。
    """
    cutoff = dates[t] - pd.Timedelta(days=365.25 * years)
    return int(dates.searchsorted(cutoff, side="left"))


def _platform_state(df: pd.DataFrame, lookback_years: float, local_window: int,
                    touch_range_pct: float, touch_vol_pct: float, min_gap_days: int,
                    breakout_range_pct: float, breakout_vol_pct: float,
                    min_validations: int = 1):
    """
    共享的逐 bar 计算核心：`detect_platforms` 和 `platform_breakout` 都基于
    这一份结果，避免重复扫描 pivot。返回的 Series 都对齐 df.index；额外
    返回 `high_thr`/`low_thr`——用"当前日到最后一次验证发生的那天"这段
    区间的波动率算出的开仓判定阈值，`platform_breakout` 直接用，不用重新
    算一遍。高低两个方向各自的"对应日"通常不同，所以分开返回，不能共用
    一个阈值（两个方向恰好同一天都有平台时，共用会把其中一个方向的阈值
    算错）。

    `min_validations`：**起点（最近的那个 pivot）本身不算一次验证**，只有
    倒序扫描时后续落在容差内的才 +1。所以 `min_validations=1` 对应"这个
    价位一共被触及过 2 次"（起点 + 1 次验证），`min_validations=2` 对应
    "一共触及过 3 次"，以此类推——设置门槛时按"触及次数 = validations + 1"
    换算，不要直接搬"我要求至少 N 次触及"里的 N。
    """
    if min_validations < 1:
        raise ValueError(f"min_validations 至少为 1，收到 {min_validations}")
    n = len(df)
    high, low = df["high"], df["low"]
    is_ph, is_pl = find_pivots(high, low, local_window)
    ph_idx = np.flatnonzero(is_ph.values)
    ph_price = high.values[ph_idx]
    pl_idx = np.flatnonzero(is_pl.values)
    pl_price = low.values[pl_idx]

    # 前缀和：O(1) 查出任意 [j, t) 区间逐日 (high-low) 的均值
    cum_range = np.concatenate([[0.0], np.cumsum(high.values - low.values)])

    high_level = np.full(n, np.nan)
    high_val = np.zeros(n, dtype=int)
    low_level = np.full(n, np.nan)
    low_val = np.zeros(n, dtype=int)
    high_thr = np.full(n, np.nan)
    low_thr = np.full(n, np.nan)

    dates = df.index
    for t in range(1, n):   # t=0 没有"前一天"，天然无平台
        lo = _lookback_start(dates, t, lookback_years)
        if lo >= t:
            continue

        window_high = high.values[lo:t].max()
        window_low = low.values[lo:t].min()
        rng = window_high - window_low

        # 传给 _scan_backward 的必须是时间逆序（新->旧）
        lo_h = np.searchsorted(ph_idx, lo, side="left")
        hi_h = np.searchsorted(ph_idx, t, side="left")
        seg_idx = ph_idx[lo_h:hi_h][::-1]
        seg_price = ph_price[lo_h:hi_h][::-1]
        c, v, last_h = _scan_backward(seg_idx, seg_price, t, cum_range, rng,
                                      touch_range_pct, touch_vol_pct,
                                      breakout_range_pct, breakout_vol_pct, min_gap_days)
        if v >= min_validations:
            high_level[t], high_val[t] = c, v
            high_thr[t] = min(breakout_range_pct * rng,
                              breakout_vol_pct * _avg_range(cum_range, last_h, t))

        lo_l = np.searchsorted(pl_idx, lo, side="left")
        hi_l = np.searchsorted(pl_idx, t, side="left")
        seg_idx2 = pl_idx[lo_l:hi_l][::-1]
        seg_price2 = pl_price[lo_l:hi_l][::-1]
        c2, v2, last_l = _scan_backward(seg_idx2, seg_price2, t, cum_range, rng,
                                        touch_range_pct, touch_vol_pct,
                                        breakout_range_pct, breakout_vol_pct, min_gap_days)
        if v2 >= min_validations:
            low_level[t], low_val[t] = c2, v2
            low_thr[t] = min(breakout_range_pct * rng,
                             breakout_vol_pct * _avg_range(cum_range, last_l, t))

    idx = df.index
    return (pd.Series(high_level, idx), pd.Series(high_val, idx),
           pd.Series(low_level, idx), pd.Series(low_val, idx),
           pd.Series(high_thr, idx), pd.Series(low_thr, idx))


def detect_platforms(df: pd.DataFrame, lookback_years: float = 3.0,
                     local_window: int = 3, touch_range_pct: float = 0.10,
                     touch_vol_pct: float = 0.20, min_gap_days: int = 3,
                     breakout_range_pct: float = 0.10,
                     breakout_vol_pct: float = 0.30,
                     min_validations: int = 1) -> PlatformInfo:
    """
    逐 bar 的平台价位 + 验证次数，两个方向独立。供诊断/画图，策略用
    `platform_breakout`。`breakout_range_pct`/`breakout_vol_pct` 虽然是给
    "开仓"判定用的参数，这里也要传——因为倒序扫描历史时用同一套阈值判定
    "显著突破"来划定平台的源头，见模块 docstring 第 3、4 点。

    `min_validations`：平台成立的最低验证次数（起点本身不算验证，
    换算成"触及次数"要 +1，见 `_platform_state` 的说明）。
    """
    hl, hv, ll, lv, _, _ = _platform_state(df, lookback_years, local_window,
                                           touch_range_pct, touch_vol_pct, min_gap_days,
                                           breakout_range_pct, breakout_vol_pct,
                                           min_validations)
    return PlatformInfo(hl, hv, ll, lv)


def platform_breakout(df: pd.DataFrame, lookback_years: float = 3.0,
                      local_window: int = 3,
                      touch_range_pct: float = 0.10, touch_vol_pct: float = 0.20,
                      min_gap_days: int = 3,
                      breakout_range_pct: float = 0.10, breakout_vol_pct: float = 0.30,
                      min_validations: int = 1,
                      volume_window: int = 20, volume_mult: float | None = None,
                      ) -> PlatformSignal:
    """
    收盘价突破平台 + 突破阈值时发出开仓脉冲。

    entry=+1：close >= high_level + breakout_threshold（压力位突破，做多）
    entry=-1：close <= low_level  - breakout_threshold（支撑位跌破，做空）
    `volume_mult` 给了的话还要求当日成交量放量确认（见下）。

    stop_price 是该次突破对应的"跌回平台 + 反向达到同等阈值"价位：
        多头 stop_price = high_level - breakout_threshold
        空头 stop_price = low_level  + breakout_threshold
    在突破当根 bar 冻结，交易期间不再随平台演化而变化。

    `min_validations`：平台成立的最低验证次数，见 `detect_platforms` 的说明。

    放量确认（`volume_mult`，默认 `None` 关闭）：
        `volume[t] >= volume_mult * mean(volume[t-volume_window:t])`
        均量基准**不含当日**（`shift(1)` 后再滚动平均）——当日成交量本来就
        是要检验的对象，把它自己算进基准会稀释比较，参照系应该是"平时"的量。

        这个检验只用当日收盘时点就能拿到的数据（收盘价 + 收盘成交量），
        不需要分时行情——K 线收盘前你确实看不到当天最终成交量，但这个信号
        本来就是收盘时点才评估（`execution="close"`），不存在"盘中就要知道
        会不会放量"的问题。price 和 volume 两个条件当天都满足才算真突破；
        只满足价格没放量，entry 当天保持 0，等后面某天量价都满足了再触发——
        `entry` 是"条件持续成立"（见 PlatformSignal 说明），不会因为今天没
        放量就永久错过这次突破。
    """
    if volume_mult is not None and "volume" not in df.columns:
        raise ValueError("volume_mult 需要 df 里有 volume 列")

    hl, hv, ll, lv, h_thr, l_thr = _platform_state(
        df, lookback_years, local_window, touch_range_pct, touch_vol_pct,
        min_gap_days, breakout_range_pct, breakout_vol_pct, min_validations)
    close = df["close"]
    n = len(df)

    if volume_mult is not None:
        vol_baseline = df["volume"].shift(1).rolling(volume_window).mean().values
        volume = df["volume"].values

    entry = np.zeros(n, dtype=int)
    stop_price = np.full(n, np.nan)
    validations = np.zeros(n, dtype=int)

    for t in range(1, n):
        h_lvl, h_val, h_t = hl.iloc[t], hv.iloc[t], h_thr.iloc[t]
        l_lvl, l_val, l_t = ll.iloc[t], lv.iloc[t], l_thr.iloc[t]
        c = close.values[t]

        if h_val >= min_validations and c >= h_lvl + h_t:
            price_ok, direction, lvl, thr, val = True, 1, h_lvl, h_t, h_val
        elif l_val >= min_validations and c <= l_lvl - l_t:
            price_ok, direction, lvl, thr, val = True, -1, l_lvl, l_t, l_val
        else:
            price_ok = False

        if not price_ok:
            continue
        if volume_mult is not None:
            base = vol_baseline[t]
            if np.isnan(base) or volume[t] < volume_mult * base:
                continue   # 价格突破了但没放量，今天不算数，等下一根 bar

        entry[t] = direction
        stop_price[t] = lvl - thr if direction > 0 else lvl + thr
        validations[t] = val

    idx = df.index
    return PlatformSignal(pd.Series(entry, idx), pd.Series(stop_price, idx),
                          pd.Series(validations, idx))
