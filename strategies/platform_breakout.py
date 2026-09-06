"""平台突破策略：消费 `signals.platform.PlatformSignal`，按风险定仓位。

和 `SingleSignalStrategy` 的关键差异——为什么要单独写一个策略类：

- 止损是**价格位**（平台反向突破价），不是开仓价的固定百分比。
- 仓位大小由**风险**反推：单笔止损造成的账户损失 = `min(risk_per_validation ×
  validations, max_risk)`，不是资金比例。
- 止盈是两条独立规则的组合：盈利触及 `profit_trail_start` 后开始按"回吐峰值
  利润的 `profit_trail_giveback` 比例"追踪止盈；同时无条件设一条固定
  `fixed_take_profit` 目标价。哪个先触发就按哪个平仓。

止损成交价同样支持盘中 high/low 触发 + 跳空按开盘价成交（不满足于只看收盘价，
这一点延续 `SingleSignalStrategy._exit_fill` 的标准）。
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from .base import BaseStrategy, resolve_futures_contract
from tools.trade import buy, sell, short, cover, free_capital, margin_call, force_close

_REQUIRED_COLS = ("open", "high", "low", "close")


class PlatformBreakoutStrategy(BaseStrategy):
    """
    Args:
        signal_fn: `df -> PlatformSignal`（entry, stop_price, validations），
                   默认 `signals.platform.platform_breakout`。想改平台参数
                   （lookback_years 等）传 `functools.partial(platform_breakout, ...)`。
        risk_per_validation: 每一次平台验证对应的账户风险敞口，默认 1%。
        max_risk: 风险敞口上限，默认 4%（validations 再多也不会超过这个比例）。
        profit_trail_start: 浮盈超过这个比例才开始追踪止盈（按峰值利润回撤判定，
                   不是价格回撤——这一点和 `SingleSignalStrategy` 的 `trail_pct` 不同）。
        profit_trail_giveback: 峰值利润回吐这个比例就止盈。
        fixed_take_profit: 无条件固定止盈目标（利润比例）。
        exit_on_reverse: 持仓期间收到反向 entry 是否反手（默认开启，语义与
                   SingleSignalStrategy 一致）。

    仓位计算：**风险预算和保证金是两个不同的东西，不能互相替代**。
        风险预算 = 触发止损时账户实际亏掉的钱 = qty * stop_distance * multiplier
                  （期货完全不涉及保证金——止损亏的是价格差乘以合约乘数，
                  不是保证金本身；`即使触发止损也不会亏光保证金`，因为
                  margin_rate 通常远大于 stop_distance/price 这个比例）。
        保证金   = 持有 qty 手需要占用的资金 = qty * price * multiplier * margin_rate
                  ——这是"能不能开这个仓"的资金约束，跟风险预算无关。

        期货手数分两步定，谁都不覆盖谁：
            qty_risk   = floor(risk_amount / (stop_distance * multiplier))     # 风险允许的手数
            qty_margin = floor(free_capital / (price * multiplier * margin_rate))  # 资金能扛住的上限
            qty = 0                          若 qty_margin < 1（连 1 手的保证金都拿不出）
            qty = min(max(1, qty_risk), qty_margin)   否则

        `max(1, qty_risk)`：风险预算折算下来不到 1 手也不代表这笔交易做不了——
        1 手的止损损失通常远小于 1 手的保证金，只要保证金掏得出来，就按 1 手
        起步（愿赌服输，实际风险可能略高于 risk_frac 目标，但不会没了保证金）；
        风险预算允许更多手数时才按风险预算往上加，最终仍不能超过保证金上限。

        股票没有保证金概念（`margin_rate` 恒为 1，`multiplier` 恒为 1），
        份额是连续量，不存在"不到 1 股"这种取整问题，维持纯风险公式：
            qty = risk_amount / stop_distance
    """

    def __init__(self, df: pd.DataFrame,
                 signal_fn=None,
                 risk_per_validation: float = 0.01,
                 max_risk: float = 0.04,
                 profit_trail_start: float = 0.10,
                 profit_trail_giveback: float = 0.30,
                 fixed_take_profit: float = 0.30,
                 exit_on_reverse: bool = True,
                 allow_same_bar_reentry: bool = False,
                 intrabar_stops: bool = True,
                 execution: str = "close",
                 futures: bool = False,
                 ticker: Optional[str] = None,
                 multiplier: Optional[float] = None,
                 margin_rate: Optional[float] = None,
                 maintenance: float = 1.0,
                 fee_rate: float = 0.0,
                 fee_per_lot: float = 0.0,
                 initial_capital: float = 100_000):
        super().__init__(df, initial_capital)
        if execution not in ("close", "next_open"):
            raise ValueError(f"execution 只能是 'close' 或 'next_open'，收到 {execution!r}")
        missing = [c for c in _REQUIRED_COLS if c not in self.df.columns]
        if missing:
            raise ValueError(f"行情数据缺少列 {missing}，需要 {list(_REQUIRED_COLS)}")

        if signal_fn is None:
            from signals.platform import platform_breakout
            signal_fn = platform_breakout
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

        self.risk_per_validation = risk_per_validation
        self.max_risk = max_risk
        self.profit_trail_start = profit_trail_start
        self.profit_trail_giveback = profit_trail_giveback
        self.fixed_take_profit = fixed_take_profit
        self.exit_on_reverse = exit_on_reverse
        self.allow_same_bar_reentry = allow_same_bar_reentry
        self.intrabar_stops = intrabar_stops
        self.execution = execution
        self.bust = None
        self.margin_calls = []
        self.rejected = []

    # ── 内部工具 ────────────────────────────────────────────────────────────
    @property
    def _kwargs(self) -> dict:
        return {"futures": self.futures, "multiplier": self.multiplier,
                "fee_rate": self.fee_rate, "fee_per_lot": self.fee_per_lot}

    @property
    def _open_kwargs(self) -> dict:
        return dict(self._kwargs, margin_rate=self.margin_rate)

    def _equity(self, state: dict, price: float) -> float:
        factor = price * self.multiplier if self.futures else price
        return state["cash"] + state["shares"] * factor

    def _profit_pct(self, direction: int, entry_price: float, price: float) -> float:
        return (price / entry_price - 1) if direction > 0 else (entry_price / price - 1)

    def _position_ratio(self, state: dict, price: float,
                        stop_price: float, validations: int) -> float:
        """见类 docstring 的推导：返回喂给 buy()/short() 的 `ratio`。"""
        stop_distance = abs(price - stop_price)
        if stop_distance <= 0 or validations <= 0:
            return 0.0
        equity_before = self._equity(state, price)
        risk_frac = min(self.risk_per_validation * validations, self.max_risk)
        risk_amount = equity_before * risk_frac
        free = free_capital(state, price, self.futures, self.multiplier, self.margin_rate)
        if free <= 0:
            return 0.0

        if not self.futures:
            # 股票：份额连续，没有"不到 1 股"的取整问题，纯风险公式即可。
            qty = risk_amount / stop_distance
            return min(1.0, (qty * price) / free)

        # 期货：风险预算折算的手数不到 1，不代表资金不够——1 手的止损损失
        # 通常远小于 1 手的保证金。只要保证金掏得出来就按 1 手起步，
        # 风险预算允许更多手数时再往上加，最终仍不能超过保证金能扛住的上限。
        margin_per_lot = price * self.multiplier * self.margin_rate
        if margin_per_lot <= 0:
            return 0.0
        qty_risk = math.floor(risk_amount / (stop_distance * self.multiplier))
        qty_margin = math.floor(free / margin_per_lot)
        if qty_margin < 1:
            return 0.0
        qty = min(max(1, qty_risk), qty_margin)
        return min(1.0, (qty * margin_per_lot) / free)

    def _open_position(self, state: dict, dt, direction: int,
                       stop_price: float, validations: int, price: float) -> bool:
        ratio = self._position_ratio(state, price, stop_price, validations)
        if ratio <= 0:
            if self.futures:
                # 走到这里说明连 1 手的保证金都拿不出来（_position_ratio 已经
                # 把"风险预算不够 1 手"的情况垫成了 1 手，能到这一步的都是
                # 真正的资金短缺，不是风险预算折算的假象）。
                free = free_capital(state, price, self.futures, self.multiplier, self.margin_rate)
                self.rejected.append({
                    "datetime": str(dt), "price": price,
                    "equity": self._equity(state, price), "budget": free,
                    "margin_per_lot": price * self.multiplier * self.margin_rate,
                })
            return False
        equity_before = self._equity(state, price)
        if direction > 0:
            buy(state, price, ratio=ratio, **self._open_kwargs)
        else:
            short(state, price, ratio=ratio, **self._open_kwargs)
        if state["shares"] == 0:
            return False
        self._log_entry(dt, "long" if direction > 0 else "short", price,
                        state["shares"], ratio, equity_before,
                        stop_price=stop_price, validations=validations)
        return True

    def _close_position(self, state: dict, dt, price: float,
                        reason: str, bars_held: int) -> None:
        if state["shares"] > 0:
            sell(state, price, **self._kwargs)
        elif state["shares"] < 0:
            cover(state, price, **self._kwargs)
        self._log_exit(dt, price, self._equity(state, price), reason, bars_held)

    def _exit_fill(self, direction: int, bar, entry_price: float,
                   stop_price: float, peak_profit_pct: float):
        """
        判断本 bar 是否触发离场。返回 (成交价, 原因)，未触发则 (None, None)。

        亏损侧（止损 + 利润回吐追踪）用 low（多头）/high（空头）触发；
        止盈侧（固定目标）用 high（多头）/low（空头）触发。跳空越过时按开盘价
        成交。同一根 bar 内亏损侧和止盈侧都触发时，保守起见按亏损侧优先
        （不假设那根宽幅 bar 里价格先冲到了对我们有利的方向）。
        """
        loss_levels = [(stop_price, "stop_loss")]
        if peak_profit_pct >= self.profit_trail_start:
            keep = 1.0 - self.profit_trail_giveback
            target_profit = peak_profit_pct * keep
            giveback_price = (entry_price * (1 + target_profit) if direction > 0
                              else entry_price / (1 + target_profit))
            loss_levels.append((giveback_price, "trailing"))
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
            if (bar.close >= tp_level if direction > 0 else bar.close <= tp_level):
                return bar.close, "take_profit"
            return None, None

        hit_loss = [(lv, r) for lv, r in loss_levels
                   if (extreme_loss <= lv if direction > 0 else extreme_loss >= lv)]
        if hit_loss:
            lv, reason = (max(hit_loss, key=lambda t: t[0]) if direction > 0
                         else min(hit_loss, key=lambda t: t[0]))
            gapped = bar.open <= lv if direction > 0 else bar.open >= lv
            return (bar.open if gapped else lv), reason

        if (extreme_tp >= tp_level if direction > 0 else extreme_tp <= tp_level):
            gapped = bar.open >= tp_level if direction > 0 else bar.open <= tp_level
            return (bar.open if gapped else tp_level), "take_profit"

        return None, None

    # ── 主循环 ──────────────────────────────────────────────────────────────
    def run(self) -> pd.Series:
        self._reset_log()
        self.bust = None
        self.margin_calls = []
        self.rejected = []

        sig = self.signal_fn(self.df)
        entry_sig, stop_sig, val_sig = sig.entry, sig.stop_price, sig.validations
        for name, s in (("entry", entry_sig), ("stop_price", stop_sig), ("validations", val_sig)):
            if not s.index.equals(self.df.index):
                raise ValueError(f"PlatformSignal.{name} 的索引必须与行情数据一致")

        state = {"cash": self.initial_capital, "shares": 0.0}
        direction = 0            # 当前持仓方向 -1/0/+1
        entry_price = None
        stop_price = None        # 开仓时冻结的平台止损价
        validations = 0
        peak_profit_pct = 0.0    # 持仓期间见过的最高浮盈比例（追踪止盈用）
        bars_held = 0
        pending = None           # execution="next_open" 时的挂单
        equity = []

        for i, bar in enumerate(self.df.itertuples()):
            dt = bar.Index
            had_position = direction != 0
            exited_this_bar = False

            # A. 上一根 bar 的挂单在本 bar 开盘成交
            if pending is not None:
                p_dir, p_stop, p_val = pending
                pending = None
                if direction != 0 and p_dir != direction and self.exit_on_reverse:
                    self._close_position(state, dt, bar.open, "reverse", bars_held)
                    direction, entry_price, stop_price, validations = 0, None, None, 0
                    bars_held, peak_profit_pct = 0, 0.0
                    had_position = False
                if direction == 0:
                    if self._open_position(state, dt, p_dir, p_stop, p_val, bar.open):
                        direction, entry_price, stop_price, validations = p_dir, bar.open, p_stop, p_val
                        bars_held, peak_profit_pct = 0, 0.0

            # B. 止损/止盈（盘中触发）。用的是上一根 bar 收盘时定下的
            #    peak_profit_pct——本 bar 自己创出的新高/新低要到下面 C 步骤
            #    才计入，不然"今天刚创新高、今天又回落"会被误判成"回吐了
            #    利润"当场止盈（新峰值和回吐不可能发生在同一根 bar 里，
            #    intrabar 路径不可知，保守地假设新峰值要下一根 bar 才算数——
            #    与 SingleSignalStrategy 的 peak 更新顺序完全一致）。
            if direction != 0:
                if had_position:
                    bars_held += 1
                fill, reason = self._exit_fill(direction, bar, entry_price,
                                               stop_price, peak_profit_pct)
                if fill is not None:
                    self._close_position(state, dt, fill, reason, bars_held)
                    direction, entry_price, stop_price, validations = 0, None, None, 0
                    bars_held, peak_profit_pct = 0, 0.0
                    exited_this_bar = True

            # C. 用本 bar 的极值更新 peak_profit_pct，供下一根 bar 的止盈判定使用
            if direction != 0:
                fav_px = bar.high if direction > 0 else bar.low
                fav_profit = self._profit_pct(direction, entry_price, fav_px)
                peak_profit_pct = max(peak_profit_pct, fav_profit)

            # D. 信号：entry 是"条件持续成立"，只在空仓时响应即可天然去重
            #    （见 signals/platform.py::PlatformSignal 的说明，语义和
            #    signals.base.Signal 的事件脉冲不同）
            e = int(entry_sig.iloc[i])
            if e != 0:
                s_price = float(stop_sig.iloc[i])
                v = int(val_sig.iloc[i])
                blocked = (exited_this_bar and not self.allow_same_bar_reentry
                          and self.execution == "close")
                if not blocked:
                    if self.execution == "next_open":
                        pending = (e, s_price, v)
                    elif direction == 0:
                        if self._open_position(state, dt, e, s_price, v, bar.close):
                            direction, entry_price, stop_price, validations = e, bar.close, s_price, v
                            bars_held, peak_profit_pct = 0, 0.0
                    elif e != direction and self.exit_on_reverse:
                        self._close_position(state, dt, bar.close, "reverse", bars_held)
                        direction, entry_price, stop_price, validations = 0, None, None, 0
                        bars_held, peak_profit_pct = 0, 0.0
                        if self._open_position(state, dt, e, s_price, v, bar.close):
                            direction, entry_price, stop_price, validations = e, bar.close, s_price, v
                            bars_held, peak_profit_pct = 0, 0.0

            # E1. 强平检查
            if direction != 0 and self.bust is None:
                called = margin_call(state, bar.close, dt, self.margin_rate,
                                     futures=self.futures, multiplier=self.multiplier,
                                     maintenance=self.maintenance,
                                     fee_rate=self.fee_rate, fee_per_lot=self.fee_per_lot)
                if called is not None:
                    self._log_exit(dt, bar.close, self._equity(state, bar.close),
                                   "margin_call", bars_held)
                    self.margin_calls.append(called)
                    direction, entry_price, stop_price, validations = 0, None, None, 0
                    bars_held, peak_profit_pct, pending = 0, 0.0, None

            # E2. 爆仓检查
            if self.bust is None:
                bust = force_close(state, bar.close, dt,
                                   futures=self.futures, multiplier=self.multiplier)
                if bust is not None:
                    self._log_exit(dt, bar.close, self._equity(state, bar.close),
                                   "bust", bars_held)
                    self.bust = bust
                    direction, entry_price, stop_price, validations = 0, None, None, 0
                    bars_held, peak_profit_pct, pending = 0, 0.0, None

            # F. 逐日盯市，按 0 封底
            equity.append(max(0.0, self._equity(state, bar.close)))

        if self._open is not None:
            last_dt, last_px = self.df.index[-1], float(self.df["close"].iloc[-1])
            self._log_exit(last_dt, last_px, equity[-1], "end_of_data", bars_held)

        return pd.Series(equity, index=self.df.index, name="equity")
