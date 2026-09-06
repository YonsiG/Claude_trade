from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Optional

import pandas as pd


@dataclass
class Trade:
    """一笔完整的往返交易。

    盈亏用「开仓前总权益」与「平仓后总权益」之差计算，因此手续费、
    做空的现金流全部自动包含在内，无需单独记账。
    """
    entry_dt:     object
    direction:    str            # "long" / "short"
    entry_price:  float
    qty:          float
    size_ratio:   float          # 开仓时使用的资金比例
    entry_equity: float
    exit_dt:      Optional[object] = None
    exit_price:   float = math.nan
    exit_equity:  float = math.nan
    exit_reason:  str = ""       # signal / reverse / stop_loss / trailing / margin_call / bust / end_of_data
    bars_held:    int = 0
    n_adds:       int = 0        # 加仓次数；entry_price 为加权平均开仓价
    stop_price:   float = math.nan   # 开仓时定下的止损价（按价格位止损的策略才有意义）
    validations:  int = 0            # 触发该次开仓的信号强度分级依据（如平台验证次数）

    @property
    def is_open(self) -> bool:
        return self.exit_dt is None

    @property
    def pnl(self) -> float:
        return self.exit_equity - self.entry_equity

    @property
    def return_pct(self) -> float:
        if not self.entry_equity:
            return math.nan
        return (self.exit_equity / self.entry_equity - 1) * 100

    def as_row(self) -> dict:
        row = asdict(self)
        row["pnl"] = self.pnl
        row["return_pct"] = self.return_pct
        return row


_TRADE_COLUMNS = [
    "entry_dt", "exit_dt", "direction", "entry_price", "exit_price",
    "qty", "size_ratio", "n_adds", "bars_held", "entry_equity", "exit_equity",
    "pnl", "return_pct", "exit_reason", "stop_price", "validations",
]


def resolve_futures_contract(futures: bool, ticker, multiplier, margin_rate):
    """
    确定合约乘数与保证金率。`SingleSignalStrategy` 和 `PlatformBreakoutStrategy`
    共用这一份逻辑，避免同样的规格解析代码在两处重复维护。

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


class BaseStrategy(ABC):
    def __init__(self, df: pd.DataFrame, initial_capital: float = 100_000):
        self.df = df.copy()
        self.initial_capital = initial_capital
        self.trades: list[Trade] = []
        self._open: Optional[Trade] = None

    @abstractmethod
    def run(self) -> pd.Series:
        """Execute strategy on self.df. Returns equity curve indexed by date."""
        ...

    # ── 交易流水 ────────────────────────────────────────────────────────────
    def _reset_log(self) -> None:
        """run() 开头调用，允许同一个策略实例被重复回测。"""
        self.trades = []
        self._open = None

    def _log_entry(self, dt, direction: str, price: float, qty: float,
                   size_ratio: float, equity_before: float,
                   stop_price: float = math.nan, validations: int = 0) -> None:
        if self._open is not None:
            raise RuntimeError(f"{dt}: 上一笔仓位尚未平仓就再次开仓")
        self._open = Trade(
            entry_dt=dt, direction=direction, entry_price=price,
            qty=abs(qty), size_ratio=size_ratio, entry_equity=equity_before,
            stop_price=stop_price, validations=validations,
        )
        self.trades.append(self._open)

    def _log_exit(self, dt, price: float, equity_after: float,
                  reason: str, bars_held: int) -> None:
        if self._open is None:
            return
        self._open.exit_dt = dt
        self._open.exit_price = price
        self._open.exit_equity = equity_after
        self._open.exit_reason = reason
        self._open.bars_held = bars_held
        self._open = None

    def trade_log(self) -> pd.DataFrame:
        """交易流水表，一行一笔往返交易。"""
        if not self.trades:
            return pd.DataFrame(columns=_TRADE_COLUMNS)
        return pd.DataFrame([t.as_row() for t in self.trades])[_TRADE_COLUMNS]
