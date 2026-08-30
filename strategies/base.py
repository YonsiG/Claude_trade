from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
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
    "pnl", "return_pct", "exit_reason",
]


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
                   size_ratio: float, equity_before: float) -> None:
        if self._open is not None:
            raise RuntimeError(f"{dt}: 上一笔仓位尚未平仓就再次开仓")
        self._open = Trade(
            entry_dt=dt, direction=direction, entry_price=price,
            qty=abs(qty), size_ratio=size_ratio, entry_equity=equity_before,
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
