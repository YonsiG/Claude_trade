"""训练集 / 验证集切分。

在正式拿一段历史数据反复调参之前，先把它切成两半：较早（离现在更远）的
一段用来找参数（训练/优化集），较晚（离现在更近）的一段只用来最后验证一次
（验证/测试集）。在同一段数据上调到收敛、再拿同一段数据夸自己表现好，
是最常见也最隐蔽的过拟合——这个模块只负责切分，不负责防你自己在验证集上
调参调多了（那是纪律问题，不是代码能挡住的）。

两种指定方式：
  1. 默认（比例切分）：给整体 [start, end]，按 **bar 数**（不是日历天数）
     以 ratio 切分，较早一段是训练集。按 bar 数切是因为非交易日在日历上
     分布不均，日历切分算出来的实际 bar 比例会偏离预期。
  2. 显式（区间切分）：直接给 train_start/train_end/test_start/test_end
     四个日期，各自独立调用 load()，互不要求首尾相接，也不要求不重叠——
     由调用方对结果的解读负责。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from data.loader import load

_MIN_BARS_PER_SPLIT = 5


@dataclass
class SplitResult:
    train_df: pd.DataFrame
    test_df: pd.DataFrame
    train_range: tuple       # (start, end) 字符串，供 engine.run() 标注结果用
    test_range: tuple
    explicit: bool           # True = 用户给了四个显式日期；False = 比例自动切分


def resolve_split(ticker: str, start: str, end: str, source: str, interval: str,
                  ratio: float = 0.8,
                  train_start: str = None, train_end: str = None,
                  test_start: str = None, test_end: str = None) -> SplitResult:
    """
    Args:
        start/end: 整体区间。比例模式下必需；显式模式下被忽略（可以不传）。
        ratio:     训练集占比（按 bar 数），默认 0.8，即 4:1。
        train_*/test_*: 四个都给出才启用显式模式；给了 1~3 个是参数错误。
    """
    given = [train_start, train_end, test_start, test_end]
    n_given = sum(x is not None for x in given)
    if n_given not in (0, 4):
        names = ["train_start", "train_end", "test_start", "test_end"]
        missing = [n for n, v in zip(names, given) if v is None]
        raise ValueError(
            f"显式指定训练/验证区间时四个日期必须都给出，缺少: {missing}"
        )

    if n_given == 4:
        train_df = load(ticker, train_start, train_end, interval=interval, source=source)
        test_df = load(ticker, test_start, test_end, interval=interval, source=source)
        _check_enough_bars(ticker, train_df, test_df)
        return SplitResult(train_df, test_df,
                           (train_start, train_end), (test_start, test_end),
                           explicit=True)

    if not 0.0 < ratio < 1.0:
        raise ValueError(f"split ratio 必须在 (0,1) 之间，收到 {ratio}")
    if start is None or end is None:
        raise ValueError("比例切分模式需要给出整体 start/end")

    full = load(ticker, start, end, interval=interval, source=source)
    if len(full) < _MIN_BARS_PER_SPLIT * 2:
        raise ValueError(
            f"{ticker} 在 {start}~{end} 只有 {len(full)} 根 bar，"
            f"不足以切出训练/验证集（各至少需要 {_MIN_BARS_PER_SPLIT} 根）"
        )

    split_idx = round(len(full) * ratio)
    split_idx = max(_MIN_BARS_PER_SPLIT, min(len(full) - _MIN_BARS_PER_SPLIT, split_idx))

    train_df, test_df = full.iloc[:split_idx], full.iloc[split_idx:]
    train_range = (str(train_df.index[0].date()), str(train_df.index[-1].date()))
    test_range = (str(test_df.index[0].date()), str(test_df.index[-1].date()))
    return SplitResult(train_df, test_df, train_range, test_range, explicit=False)


def _check_enough_bars(ticker, train_df, test_df):
    if len(train_df) < _MIN_BARS_PER_SPLIT or len(test_df) < _MIN_BARS_PER_SPLIT:
        raise ValueError(
            f"{ticker}: 训练集 {len(train_df)} 根 / 验证集 {len(test_df)} 根，"
            f"各至少需要 {_MIN_BARS_PER_SPLIT} 根"
        )
