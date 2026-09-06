"""关键区突破策略：消费 `signals.key_zone.KeyZoneSignal`，`platform_breakout` 的对照组。

复用 `PlatformBreakoutStrategy` 已经踩过坑、验证过的部分——风险/保证金两步定
仓位公式、强平/爆仓流程——但入场执行机制和止盈追踪的判定基准完全不同，
这也是为什么要单独写一个策略类而不是给 `PlatformBreakoutStrategy` 加参数：

- **信号形状不同**：`entry` 是一次性事件脉冲（每个关键区一辈子只有一次突破
  机会），不是 `PlatformSignal` 那种"条件持续成立"。信号自带 `exec_lo`/
  `exec_hi`（次日开盘可接受区间）和 `stop_price`（已经算好的价格位止损），
  策略层不用重新推导这些。
- **执行时机固定为次日开盘，且带"可接受区间"**：多头要求开盘价落在
  `(exec_lo, exec_hi]`，空头落在 `[exec_lo, exec_hi)`，否则直接放弃这笔
  交易——不递延、不追价、没有第二次机会（和 `PlatformBreakoutStrategy` 的
  `execution="next_open"` 无条件按次日开盘成交不同）。
- **止盈追踪的激活门槛按 R 倍数算**，不是开仓价的固定百分比：`trail_start_r`
  是初始风险 `d = |entry - stop|` 的倍数，`H_t - E >= trail_start_r * d`
  （多头）才开始追踪；`trail_start_r=0` 时任何正盈利就开始追踪。配合
  `fixed_take_profit`（`None` 表示不设固定止盈上限）可以拼出用户要求
  对照的三个版本：

  | 版本 | trail_start_r | fixed_take_profit |
  |------|---------------|--------------------|
  | 原始版   | 0   | 0.30 |
  | 建议基准版 | 2.0 | 0.30 |
  | 趋势对照版 | 2.0 | None |

- **股票不能裸卖空**：`futures=False` 时，做空信号（关键支撑区跌破）只用来
  给已有多头平仓离场，空仓时收到做空信号直接忽略——不像期货可以多空都
  正常开仓/反手（`futures=True` 时不做这个限制）。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .base import BaseStrategy, resolve_futures_contract
from .platform_breakout import PlatformBreakoutStrategy
from tools.trade import margin_call, force_close

_REQUIRED_COLS = ("open", "high", "low", "close")


class KeyZoneBreakoutStrategy(PlatformBreakoutStrategy):
    """
    Args:
        signal_fn: `df -> KeyZoneSignal`，默认 `signals.key_zone.key_zone_breakout`。
                   想改关键区参数（required_touches 等）传
                   `functools.partial(key_zone_breakout, ...)`。
        risk_per_touch: 每一次关键区触碰对应的账户风险敞口，默认 1%
                        （仓位公式与 `PlatformBreakoutStrategy` 完全相同，
                        只是用 touch_count 代替 validations）。
        max_risk: 风险敞口上限，默认 4%。
        trail_start_r: 追踪止盈激活门槛，以初始风险 `d=|entry-stop|` 的倍数计。
        profit_trail_giveback: 峰值利润（按 R 倍数算）回吐这个比例就止盈。
        fixed_take_profit: 固定止盈目标（开仓价的百分比），`None` 表示不设上限。
        exit_on_reverse: 期货下持仓期间收到反向信号是否反手；股票不适用
                        （股票的反向信号只会触发平仓，不会开空）。

    仓位计算、强平、爆仓完全复用父类 `PlatformBreakoutStrategy` 的实现，
    见其 docstring；这里不重复。
    """

    def __init__(self, df: pd.DataFrame,
                 signal_fn=None,
                 risk_per_touch: float = 0.01,
                 max_risk: float = 0.04,
                 trail_start_r: float = 0.0,
                 profit_trail_giveback: float = 0.30,
                 fixed_take_profit: Optional[float] = 0.30,
                 exit_on_reverse: bool = True,
                 intrabar_stops: bool = True,
                 futures: bool = False,
                 ticker: Optional[str] = None,
                 multiplier: Optional[float] = None,
                 margin_rate: Optional[float] = None,
                 maintenance: float = 1.0,
                 fee_rate: float = 0.0,
                 fee_per_lot: float = 0.0,
                 initial_capital: float = 100_000):
        # 不走 PlatformBreakoutStrategy.__init__：它的 execution 校验/
        # profit_trail_start 语义都是为 PlatformSignal 设计的，这里执行
        # 机制（次日开盘 + 可接受区间）和止盈基准（R 倍数）完全不同，
        # 直接调 BaseStrategy.__init__ 自己搭剩下的共享状态。
        BaseStrategy.__init__(self, df, initial_capital)
        missing = [c for c in _REQUIRED_COLS if c not in self.df.columns]
        if missing:
            raise ValueError(f"行情数据缺少列 {missing}，需要 {list(_REQUIRED_COLS)}")

        if signal_fn is None:
            from signals.key_zone import key_zone_breakout
            signal_fn = key_zone_breakout
        self.signal_fn = signal_fn

        multiplier, margin_rate = resolve_futures_contract(
            futures, ticker, multiplier, margin_rate)
        self.ticker = ticker
        self.futures = futures
        self.multiplier = multiplier
        self.margin_rate = margin_rate
        self.maintenance = maintenance
        self.fee_rate = fee_rate
        self.fee_per_lot = fee_per_lot

        # _position_ratio（父类）按 self.risk_per_validation 读取，字段名
        # 保持一致以便直接复用，不重新实现一份。
        self.risk_per_validation = risk_per_touch
        self.max_risk = max_risk
        self.trail_start_r = trail_start_r
        self.profit_trail_giveback = profit_trail_giveback
        self.fixed_take_profit = fixed_take_profit
        self.exit_on_reverse = exit_on_reverse
        self.intrabar_stops = intrabar_stops
        self.execution = "next_open"   # 仅用于 _kwargs 等父类属性，run() 不读它
        self.bust = None
        self.margin_calls = []
        self.rejected = []
        self.abandoned = []   # 次日开盘价不在可接受区间、被放弃的交易

    # ── 止盈/止损（R 倍数版，覆盖父类的 % 利润版） ───────────────────────────
    def _exit_fill(self, direction: int, bar, entry_price: float, stop_price: float,
                   initial_risk: float, peak_price: float, bars_held: int):
        """
        与父类 `_exit_fill` 的整体优先级（亏损侧优先于止盈侧、跳空按开盘价
        成交）完全一致，唯一区别是追踪止盈的激活门槛和回吐目标按 R 倍数
        （`initial_risk = |entry-stop|`）算，不是开仓价的固定百分比；
        `fixed_take_profit=None` 时不设固定止盈上限。

        `bars_held==0`（刚开仓那一根 bar 本身）时不评估追踪止盈：这时
        `peak_price` 还等于 `entry_price`（本 bar 自己的favorable extreme
        要到 run() 的 C 步骤才会并入 peak，按惯例晚一根），如果同时又是
        `trail_start_r=0`（"原始版"：任何正盈利就开始追踪），回吐价会
        精确退化成 entry_price 本身——而几乎任何一根 bar 的低点都会低于
        它自己的开盘价，于是刚开仓就几乎必然在同一根 bar 以保本价"止盈"
        离场，等于每次都白开一单。止损是价格位，不依赖 peak，同一根 bar
        照常生效，不受这条限制。
        """
        loss_levels = [(stop_price, "stop_loss")]
        if bars_held > 0 and initial_risk > 0:
            peak_r = ((peak_price - entry_price) / initial_risk if direction > 0
                     else (entry_price - peak_price) / initial_risk)
            if peak_r >= self.trail_start_r:
                keep_r = peak_r * (1.0 - self.profit_trail_giveback)
                giveback_price = (entry_price + keep_r * initial_risk if direction > 0
                                  else entry_price - keep_r * initial_risk)
                loss_levels.append((giveback_price, "trailing"))

        tp_level = None
        if self.fixed_take_profit is not None:
            tp_level = (entry_price * (1 + self.fixed_take_profit) if direction > 0
                       else entry_price * (1 - self.fixed_take_profit))

        extreme_loss = bar.low if direction > 0 else bar.high
        extreme_tp = bar.high if direction > 0 else bar.low

        if not self.intrabar_stops:
            hit_loss = [(lv, r) for lv, r in loss_levels
                       if (bar.close <= lv if direction > 0 else bar.close >= lv)]
            if hit_loss:
                lv, reason = (max(hit_loss, key=lambda t: t[0]) if direction > 0
                             else min(hit_loss, key=lambda t: t[0]))
                return bar.close, reason
            if tp_level is not None and \
                    (bar.close >= tp_level if direction > 0 else bar.close <= tp_level):
                return bar.close, "take_profit"
            return None, None

        hit_loss = [(lv, r) for lv, r in loss_levels
                   if (extreme_loss <= lv if direction > 0 else extreme_loss >= lv)]
        if hit_loss:
            lv, reason = (max(hit_loss, key=lambda t: t[0]) if direction > 0
                         else min(hit_loss, key=lambda t: t[0]))
            gapped = bar.open <= lv if direction > 0 else bar.open >= lv
            return (bar.open if gapped else lv), reason

        if tp_level is not None and \
                (extreme_tp >= tp_level if direction > 0 else extreme_tp <= tp_level):
            gapped = bar.open >= tp_level if direction > 0 else bar.open <= tp_level
            return (bar.open if gapped else tp_level), "take_profit"

        return None, None

    # ── 主循环 ──────────────────────────────────────────────────────────────
    def run(self) -> pd.Series:
        self._reset_log()
        self.bust = None
        self.margin_calls = []
        self.rejected = []
        self.abandoned = []

        sig = self.signal_fn(self.df)
        entry_sig, stop_sig, touch_sig = sig.entry, sig.stop_price, sig.touch_count
        exec_lo_sig, exec_hi_sig = sig.exec_lo, sig.exec_hi
        for name, s in (("entry", entry_sig), ("stop_price", stop_sig),
                       ("touch_count", touch_sig), ("exec_lo", exec_lo_sig),
                       ("exec_hi", exec_hi_sig)):
            if not s.index.equals(self.df.index):
                raise ValueError(f"KeyZoneSignal.{name} 的索引必须与行情数据一致")

        state = {"cash": self.initial_capital, "shares": 0.0}
        direction = 0
        entry_price = None
        stop_price = None
        initial_risk = None      # d = |entry - stop|，开仓时冻结，R 倍数止盈用
        touch_count = 0
        peak_price = None        # 持仓期间的最优价（多头=最高/空头=最低）
        bars_held = 0
        pending = None           # (direction, stop_price, touch_count, exec_lo, exec_hi)
        equity = []

        for i, bar in enumerate(self.df.itertuples()):
            dt = bar.Index
            had_position = direction != 0
            exited_this_bar = False

            # A. 上一根 bar 的信号在本 bar 开盘执行；开盘价必须落在可接受
            #    区间内，否则直接放弃——不递延、不追价、没有第二次机会。
            if pending is not None:
                p_dir, p_stop, p_touch, p_lo, p_hi = pending
                pending = None
                in_band = (p_lo < bar.open <= p_hi) if p_dir > 0 else (p_lo <= bar.open < p_hi)
                if not in_band:
                    self.abandoned.append({
                        "datetime": str(dt), "direction": p_dir, "open": bar.open,
                        "exec_lo": p_lo, "exec_hi": p_hi,
                    })
                elif direction == 0:
                    if self._open_position(state, dt, p_dir, p_stop, p_touch, bar.open):
                        direction, entry_price, stop_price, touch_count = \
                            p_dir, bar.open, p_stop, p_touch
                        initial_risk = abs(entry_price - stop_price)
                        peak_price = entry_price
                        bars_held = 0

            # B. 止损/止盈（用上一根 bar 收盘时定下的 peak_price）
            if direction != 0:
                if had_position:
                    bars_held += 1
                fill, reason = self._exit_fill(direction, bar, entry_price,
                                               stop_price, initial_risk, peak_price, bars_held)
                if fill is not None:
                    self._close_position(state, dt, fill, reason, bars_held)
                    direction, entry_price, stop_price, touch_count = 0, None, None, 0
                    initial_risk, peak_price, bars_held = None, None, 0
                    exited_this_bar = True

            # C. 本 bar 极值更新 peak_price，供下一根 bar 的止盈判定使用
            #    （当根创新高不能当根就判定回吐——和父类的更新顺序一致）
            if direction != 0:
                fav_px = bar.high if direction > 0 else bar.low
                peak_price = max(peak_price, fav_px) if direction > 0 else min(peak_price, fav_px)

            # D. 信号：entry 是一次性事件脉冲，只在空仓时挂到下一根 bar 开盘
            #    （不像 platform.py 的条件持续型信号需要"只在空仓时响应"来去重
            #    ——这里信号本身就只在突破那一根 bar 非零，天然只有一次机会）。
            #    股票不能裸卖空：做空信号只用于给已有多头平仓，空仓时忽略。
            e = int(entry_sig.iloc[i])
            if e != 0 and pending is None:
                if not self.futures and e < 0:
                    if direction > 0:
                        self._close_position(state, dt, bar.close, "reverse", bars_held)
                        direction, entry_price, stop_price, touch_count = 0, None, None, 0
                        initial_risk, peak_price, bars_held = None, None, 0
                        exited_this_bar = True
                    # 空仓/已空头时收到做空信号：没有融券，直接忽略。
                elif direction == 0:
                    pending = (e, float(stop_sig.iloc[i]), int(touch_sig.iloc[i]),
                              float(exec_lo_sig.iloc[i]), float(exec_hi_sig.iloc[i]))
                elif e != direction and self.exit_on_reverse:
                    self._close_position(state, dt, bar.close, "reverse", bars_held)
                    direction, entry_price, stop_price, touch_count = 0, None, None, 0
                    initial_risk, peak_price, bars_held = None, None, 0
                    exited_this_bar = True
                    pending = (e, float(stop_sig.iloc[i]), int(touch_sig.iloc[i]),
                              float(exec_lo_sig.iloc[i]), float(exec_hi_sig.iloc[i]))

            # E1. 强平检查（与父类完全一致）
            if direction != 0 and self.bust is None:
                called = margin_call(state, bar.close, dt, self.margin_rate,
                                     futures=self.futures, multiplier=self.multiplier,
                                     maintenance=self.maintenance,
                                     fee_rate=self.fee_rate, fee_per_lot=self.fee_per_lot)
                if called is not None:
                    self._log_exit(dt, bar.close, self._equity(state, bar.close),
                                   "margin_call", bars_held)
                    self.margin_calls.append(called)
                    direction, entry_price, stop_price, touch_count = 0, None, None, 0
                    initial_risk, peak_price, bars_held, pending = None, None, 0, None

            # E2. 爆仓检查（与父类完全一致）
            if self.bust is None:
                bust = force_close(state, bar.close, dt,
                                   futures=self.futures, multiplier=self.multiplier)
                if bust is not None:
                    self._log_exit(dt, bar.close, self._equity(state, bar.close),
                                   "bust", bars_held)
                    self.bust = bust
                    direction, entry_price, stop_price, touch_count = 0, None, None, 0
                    initial_risk, peak_price, bars_held, pending = None, None, 0, None

            # F. 逐日盯市，按 0 封底
            equity.append(max(0.0, self._equity(state, bar.close)))

        if self._open is not None:
            last_dt, last_px = self.df.index[-1], float(self.df["close"].iloc[-1])
            self._log_exit(last_dt, last_px, equity[-1], "end_of_data", bars_held)

        return pd.Series(equity, index=self.df.index, name="equity")
