from __future__ import annotations

from typing import Optional

import pandas as pd

from .base import BaseStrategy
from tools.trade import (buy, sell, short, cover, free_capital,
                         margin_call, force_close)

_REQUIRED_COLS = ("open", "high", "low", "close")


class SingleSignalStrategy(BaseStrategy):
    """
    单信号策略：把 `Signal(entry, size)` 的开仓脉冲转成持仓，并负责离场。

    信号语义（见 signals/base.py）：
        entry ∈ {-1, 0, +1} 是**事件**，只在形态成立的那一根 bar 非 0。
        entry == 0 表示"无事发生"，**不是**"应该空仓"——持仓会一直保留，
        直到被下面三种离场条件之一终结。

    离场条件（按检查顺序）：
        1. 移动止盈  —— 价格自持仓期峰值回撤 trail_pct
        2. 固定止损  —— 价格自开仓价反向走 sl_pct
        3. 反向信号  —— 收到相反方向的 entry（exit_on_reverse=True 时反手）

    成交价约定：
        execution="close"     信号在其所在 bar 的收盘价成交（最早可成交时点）。
        execution="next_open" 信号挂到下一根 bar 的开盘价成交（更贴近实盘）。
        止损/止盈是价格触发的，始终在触发的那一根 bar 内成交，与 execution 无关。

    止损成交价（intrabar_stops=True，默认）：
        用 bar 的 high/low 判断是否触发，用触发价成交；
        若开盘已跳空越过触发价，则按开盘价成交（不假设能在触发价拿到）。
        intrabar_stops=False 则退化为只看收盘价——会系统性低估回撤，仅供对照。

    手续费透传给 tools.trade：
        fee = traded_value * fee_rate + quantity * fee_per_lot

    期货合约规格：
        每个品种一手对应的乘数完全不同（AU=1000 克、AG=15 千克、RB=10 吨、
        LH=16 吨、CU=5 吨），保证金率也不同。`futures=True` 时必须确定规格：

            SingleSignalStrategy(df, fn, futures=True, ticker="RB0")
                -> 自动从 data/futures_spec.csv 查到 multiplier=10, margin_rate=0.20

            SingleSignalStrategy(df, fn, futures=True, multiplier=10, margin_rate=0.20)
                -> 显式指定（美盘期货 GC=F 等不在表里，只能这样传）

        不给规格会直接报错，而不是沉默地按 multiplier=1.0 全额开仓。
        杠杆持仓每根 bar 都做强平检查（`maintenance`，默认 1.0 = 保守）。
    """

    def __init__(self, df: pd.DataFrame,
                 signal_fn,
                 trail_pct: Optional[float] = 0.30,
                 sl_pct: Optional[float] = 0.10,
                 trail_window: Optional[int] = None,
                 exit_on_reverse: bool = True,
                 allow_same_bar_reentry: bool = False,
                 pyramid: bool = False,
                 max_adds: int = 3,
                 intrabar_stops: bool = True,
                 execution: str = "close",
                 futures: bool = False,
                 ticker: Optional[str] = None,
                 multiplier: Optional[float] = None,
                 margin_rate: Optional[float] = None,
                 maintenance: float = 1.0,
                 fee_rate: float = 0.0,
                 fee_per_lot: float = 0.0,
                 size_by_strength: bool = True,
                 initial_capital: float = 100_000):
        super().__init__(df, initial_capital)
        if execution not in ("close", "next_open"):
            raise ValueError(f"execution 只能是 'close' 或 'next_open'，收到 {execution!r}")
        missing = [c for c in _REQUIRED_COLS if c not in self.df.columns]
        if missing:
            raise ValueError(f"行情数据缺少列 {missing}，需要 {list(_REQUIRED_COLS)}")

        multiplier, margin_rate = self._resolve_contract(
            futures, ticker, multiplier, margin_rate)
        self.ticker = ticker
        self.margin_rate = margin_rate
        self.maintenance = maintenance
        self.margin_calls = []
        self.rejected = []

        self.signal_fn = signal_fn
        self.trail_pct = trail_pct
        self.sl_pct = sl_pct
        self.trail_window = trail_window
        self.exit_on_reverse = exit_on_reverse
        self.allow_same_bar_reentry = allow_same_bar_reentry
        self.pyramid = pyramid
        self.max_adds = max_adds
        self.intrabar_stops = intrabar_stops
        self.execution = execution
        self.futures = futures
        self.multiplier = multiplier
        self.fee_rate = fee_rate
        self.fee_per_lot = fee_per_lot
        self.size_by_strength = size_by_strength
        self.bust = None

    # ── 合约规格 ────────────────────────────────────────────────────────────
    @staticmethod
    def _resolve_contract(futures, ticker, multiplier, margin_rate):
        """
        确定合约乘数与保证金率。

        **每个期货品种一手对应的乘数都不同**（AU=1000克、AG=15千克、RB=10吨、
        LH=16吨、CU=5吨），保证金率也不同。所以 futures=True 时不允许沉默地
        用 1.0——要么给 ticker 让它查 data/futures_spec.csv，要么两个都显式传。
        """
        if not futures:
            return (1.0 if multiplier is None else multiplier), 1.0

        if multiplier is not None and margin_rate is not None:
            return multiplier, margin_rate

        if ticker is None:
            raise ValueError(
                "futures=True 时必须确定合约规格：传 ticker（如 ticker='RB0'，"
                "自动查 data/futures_spec.csv），或同时显式传 multiplier 和 "
                "margin_rate。不同品种一手的乘数差异极大（AU=1000, AG=15, "
                "RB=10, CU=5），沉默地用 1.0 会让每个品种都算错。"
            )

        from data.futures_spec import spec          # 延迟导入，股票回测无需此表
        s = spec(ticker)                            # 查不到会抛 KeyError 并说明原因
        return (s.multiplier if multiplier is None else multiplier,
                s.margin_rate if margin_rate is None else margin_rate)

    # ── 内部工具 ────────────────────────────────────────────────────────────
    @property
    def _kwargs(self) -> dict:
        return {"futures": self.futures, "multiplier": self.multiplier,
                "fee_rate": self.fee_rate, "fee_per_lot": self.fee_per_lot}

    @property
    def _open_kwargs(self) -> dict:
        """开仓额外需要保证金率；平仓不需要。"""
        return dict(self._kwargs, margin_rate=self.margin_rate)

    def _equity(self, state: dict, price: float) -> float:
        factor = price * self.multiplier if self.futures else price
        return state["cash"] + state["shares"] * factor

    def _stop_levels(self, direction: int, entry_price: float,
                     peak: float, bars_held: int):
        """返回本 bar 生效的 (触发价, 原因) 列表。"""
        levels = []
        if self.sl_pct:
            sl = entry_price * (1 - self.sl_pct) if direction > 0 else entry_price * (1 + self.sl_pct)
            levels.append((sl, "stop_loss"))
        trail_live = self.trail_pct and (self.trail_window is None or bars_held <= self.trail_window)
        if trail_live and peak is not None:
            tp = peak * (1 - self.trail_pct) if direction > 0 else peak * (1 + self.trail_pct)
            levels.append((tp, "trailing"))
        return levels

    def _exit_fill(self, direction: int, bar, entry_price: float,
                   peak: float, bars_held: int):
        """
        判断本 bar 是否触发离场。返回 (成交价, 原因)，未触发则 (None, None)。

        多头的止损与移动止盈都在价格下方，价格下跌时**较高**的那个先被触及；
        空头反之取较低者。跳空越过触发价时按开盘价成交。
        """
        levels = self._stop_levels(direction, entry_price, peak, bars_held)
        if not levels:
            return None, None

        if not self.intrabar_stops:
            hit = [(lv, r) for lv, r in levels
                   if (bar.close <= lv if direction > 0 else bar.close >= lv)]
            if not hit:
                return None, None
            lv, reason = max(hit, key=lambda t: t[0]) if direction > 0 else min(hit, key=lambda t: t[0])
            return bar.close, reason

        extreme = bar.low if direction > 0 else bar.high
        hit = [(lv, r) for lv, r in levels
               if (extreme <= lv if direction > 0 else extreme >= lv)]
        if not hit:
            return None, None

        # 先被触及的那一档
        lv, reason = max(hit, key=lambda t: t[0]) if direction > 0 else min(hit, key=lambda t: t[0])
        gapped = bar.open <= lv if direction > 0 else bar.open >= lv
        return (bar.open if gapped else lv), reason

    def _open_position(self, state: dict, dt, direction: int,
                       ratio: float, price: float) -> bool:
        """开仓并记流水。返回是否真的成交（期货整手取整可能算出 0 手）。"""
        if ratio <= 0:
            return False
        equity_before = self._equity(state, price)
        if direction > 0:
            buy(state, price, ratio=ratio, **self._open_kwargs)
        else:
            short(state, price, ratio=ratio, **self._open_kwargs)
        if state["shares"] == 0:
            # 期货整手取整算出 0 手：资金不够一手的保证金。
            # 必须记下来，否则回测会安静地变成"零成交"，被误读成"信号没触发"。
            if self.futures:
                self.rejected.append({
                    "datetime": str(dt), "price": price, "ratio": ratio,
                    "equity": equity_before,
                    "budget": equity_before * ratio,
                    "margin_per_lot": price * self.multiplier * self.margin_rate,
                })
            return False
        self._log_entry(dt, "long" if direction > 0 else "short",
                        price, state["shares"], ratio, equity_before)
        return True

    def _add_position(self, state: dict, dt, direction: int,
                      ratio: float, price: float, entry_price: float):
        """
        同向加仓。返回 (新的加权平均开仓价, 是否成交)。

        止损基准改用加权平均开仓价——这是加仓的标准约定：先建的底仓不该
        因为后加的仓位而被按原价止损，整个头寸用一个统一的成本线管理。
        """
        before = abs(state["shares"])
        if direction > 0:
            buy(state, price, ratio=ratio, add=True, **self._open_kwargs)
        else:
            short(state, price, ratio=ratio, add=True, **self._open_kwargs)

        total = abs(state["shares"])
        added = total - before
        if added <= 0:
            if self.futures:
                self.rejected.append({
                    "datetime": str(dt), "price": price, "ratio": ratio,
                    "equity": self._equity(state, price),
                    "budget": free_capital(state, price, self.futures,
                                           self.multiplier, self.margin_rate) * ratio,
                    "margin_per_lot": price * self.multiplier * self.margin_rate,
                })
            return entry_price, False

        avg = (entry_price * before + price * added) / total
        if self._open is not None:
            self._open.entry_price = avg
            self._open.qty = total
            self._open.n_adds += 1
        return avg, True

    def _close_position(self, state: dict, dt, price: float,
                        reason: str, bars_held: int) -> None:
        """平仓并记流水。"""
        if state["shares"] > 0:
            sell(state, price, **self._kwargs)
        elif state["shares"] < 0:
            cover(state, price, **self._kwargs)
        self._log_exit(dt, price, self._equity(state, price), reason, bars_held)

    # ── 主循环 ──────────────────────────────────────────────────────────────
    def run(self) -> pd.Series:
        self._reset_log()
        self.bust = None
        self.margin_calls = []
        self.rejected = []

        sig = self.signal_fn(self.df)
        # 无条件校验：signal_fn 必须返回 Signal（或带 .validate 的兼容对象）。
        # 之前这里有个 hasattr 分支想兼容"没有 validate 的信号"，但根本走不到——
        # 没有 .entry/.size 的对象在上一步就已经 AttributeError 了，这个分支是死代码，
        # 而且一旦真的命中（自定义了带 .entry/.size 却没有 .validate 的对象），
        # 会悄悄跳过校验，让非法 entry 值（比如 0.3）被 int() 截断成 0 而不是报错。
        entry_sig, size_sig = sig.validate(self.df.index)

        state = {"cash": self.initial_capital, "shares": 0.0}
        direction = 0           # 当前持仓方向 -1 / 0 / +1
        entry_price = None      # 实际成交价，止损基准
        peak = None             # 持仓期最有利价格
        bars_held = 0
        n_adds = 0              # 本次持仓已加仓次数
        pending = None          # (direction, ratio)，execution="next_open" 时的挂单
        equity = []

        for i, bar in enumerate(self.df.itertuples()):
            dt = bar.Index
            had_position = direction != 0
            exited_this_bar = False

            # A. 上一根 bar 的挂单在本 bar 开盘成交
            if pending is not None:
                p_dir, p_ratio = pending
                pending = None
                if direction != 0 and p_dir != direction and self.exit_on_reverse:
                    self._close_position(state, dt, bar.open, "reverse", bars_held)
                    direction, entry_price, peak, bars_held, n_adds = 0, None, None, 0, 0
                    had_position = False
                if direction == 0:
                    if self._open_position(state, dt, p_dir, p_ratio, bar.open):
                        direction, entry_price, peak, bars_held = p_dir, bar.open, bar.open, 0
                elif p_dir == direction and self.pyramid and n_adds < self.max_adds:
                    entry_price, done = self._add_position(
                        state, dt, direction, p_ratio, bar.open, entry_price)
                    if done:
                        n_adds += 1

            # B. 止损 / 移动止盈（价格触发，盘中成交）
            if direction != 0:
                if had_position:
                    bars_held += 1
                fill, reason = self._exit_fill(direction, bar, entry_price, peak, bars_held)
                if fill is not None:
                    self._close_position(state, dt, fill, reason, bars_held)
                    direction, entry_price, peak, bars_held, n_adds = 0, None, None, 0, 0
                    exited_this_bar = True

            # C. 用本 bar 的极值更新峰值（放在信号之前，新开的仓不会被本 bar 影响）
            if direction != 0:
                peak = max(peak, bar.high) if direction > 0 else min(peak, bar.low)

            # D. 信号
            e = int(entry_sig.iloc[i])
            if e != 0:
                ratio = float(min(max(size_sig.iloc[i], 0.0), 1.0)) \
                    if self.size_by_strength else 1.0
                # 本 bar 刚被止损，就不要用同一根 bar 的信号原价买回去，
                # 否则止损当场失效。next_open 模式下成交本来就落在下一根 bar，不受此限。
                blocked = (exited_this_bar and not self.allow_same_bar_reentry
                           and self.execution == "close")
                if not blocked:
                    if self.execution == "next_open":
                        pending = (e, ratio)
                    elif direction == 0:
                        if self._open_position(state, dt, e, ratio, bar.close):
                            direction, entry_price, peak, bars_held = e, bar.close, bar.close, 0
                    elif e == direction and self.pyramid and n_adds < self.max_adds:
                        entry_price, done = self._add_position(
                            state, dt, direction, ratio, bar.close, entry_price)
                        if done:
                            n_adds += 1
                    elif e != direction and self.exit_on_reverse:
                        self._close_position(state, dt, bar.close, "reverse", bars_held)
                        direction, entry_price, peak, bars_held, n_adds = 0, None, None, 0, 0
                        if self._open_position(state, dt, e, ratio, bar.close):
                            direction, entry_price, peak, bars_held = e, bar.close, bar.close, 0

            # E1. 强平检查：权益不足以支撑持仓所需保证金 -> 被强制平仓
            if direction != 0 and self.bust is None:
                called = margin_call(state, bar.close, dt, self.margin_rate,
                                     futures=self.futures, multiplier=self.multiplier,
                                     maintenance=self.maintenance,
                                     fee_rate=self.fee_rate, fee_per_lot=self.fee_per_lot)
                if called is not None:
                    self._log_exit(dt, bar.close, self._equity(state, bar.close),
                                   "margin_call", bars_held)
                    self.margin_calls.append(called)
                    direction, entry_price, peak, bars_held = 0, None, None, 0
                    pending, n_adds = None, 0

            # E2. 爆仓检查（权益归零，彻底出局）
            if self.bust is None:
                bust = force_close(state, bar.close, dt,
                                   futures=self.futures, multiplier=self.multiplier)
                if bust is not None:
                    self._log_exit(dt, bar.close, self._equity(state, bar.close),
                                   "bust", bars_held)
                    self.bust = bust
                    direction, entry_price, peak, bars_held = 0, None, None, 0
                    pending, n_adds = None, 0

            # F. 逐日盯市。按 0 封底：跳空击穿保证金线时强平价已经晚了，权益可能为负，
            #    但"亏光本金"就是 -100%，欠券商的部分不属于本金收益率。
            #    不封底会算出 -200% 这种数字，让夏普和回撤失真（bust 标记仍会如实报出）。
            equity.append(max(0.0, self._equity(state, bar.close)))

        # 数据末尾仍持仓：按最后收盘价登记平仓，只补流水，不动资金曲线
        if self._open is not None:
            last_dt, last_px = self.df.index[-1], float(self.df["close"].iloc[-1])
            self._log_exit(last_dt, last_px, equity[-1], "end_of_data", bars_held)

        return pd.Series(equity, index=self.df.index, name="equity")
