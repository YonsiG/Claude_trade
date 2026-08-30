"""信号函数的统一返回类型。

约定：信号函数返回 `Signal(entry, size)` 两条与 df 同索引的序列。

    entry: 开仓方向脉冲，取值 -1 / 0 / +1。
           **只在形态成立的那一根 bar 非 0**——它是「事件」，不是「持仓状态」。
           非 0 的 bar 表示"此刻建立一个该方向的仓位"，0 表示"无事发生"，
           而**不是**"应该空仓"。何时离场由策略的止损/止盈/反向信号决定。

    size:  该次开仓投入的资金比例，取值 [0, 1]。
           entry == 0 的 bar 上 size 无意义，约定填 0。

这样拆分是为了把「方向」和「下多大」解耦：方向来自形态是否成立，
仓位来自形态的强度（或任何独立的风控/凯利/波动率定标逻辑）。
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd


class Signal(NamedTuple):
    entry: pd.Series   # -1 / 0 / +1
    size: pd.Series    # [0, 1]

    # ── 构造 ────────────────────────────────────────────────────────────────
    @classmethod
    def blank(cls, index) -> "Signal":
        """全 0 的信号，供信号函数逐 bar 填写。"""
        return cls(pd.Series(0, index=index, dtype=int),
                   pd.Series(0.0, index=index, dtype=float))

    @classmethod
    def from_strength(cls, strength: pd.Series) -> "Signal":
        """把旧式 [-1, 1] 连续信号拆成 (方向, 仓位)。"""
        entry = pd.Series(np.sign(strength.values).astype(int), index=strength.index)
        size = strength.abs().clip(0.0, 1.0)
        return cls(entry, size)

    # ── 校验 ────────────────────────────────────────────────────────────────
    def validate(self, index=None) -> "Signal":
        """检查取值域与索引对齐，返回归一化后的自身。策略在 run() 开头调用。"""
        if not self.entry.index.equals(self.size.index):
            raise ValueError("Signal.entry 与 Signal.size 的索引不一致")
        if index is not None and not self.entry.index.equals(index):
            raise ValueError("Signal 的索引与行情数据不一致")

        entry = self.entry.fillna(0)
        bad = ~entry.isin([-1, 0, 1])
        if bad.any():
            raise ValueError(
                f"Signal.entry 只能取 -1/0/+1，发现 {int(bad.sum())} 个非法值，"
                f"首个位于 {entry.index[bad][0]}（值 ={entry[bad].iloc[0]}）"
            )

        size = self.size.fillna(0.0).clip(0.0, 1.0)
        # entry 为 0 的 bar 上 size 无意义，统一清零，避免误读
        size = size.where(entry != 0, 0.0)
        return Signal(entry.astype(int), size.astype(float))

    # ── 便利属性 ────────────────────────────────────────────────────────────
    @property
    def n_entries(self) -> int:
        return int((self.entry != 0).sum())

    def summary(self) -> str:
        e = self.entry
        return (f"entries={self.n_entries} "
                f"(long={int((e > 0).sum())}, short={int((e < 0).sum())}) "
                f"avg_size={self.size[e != 0].mean() if self.n_entries else 0:.3f}")
