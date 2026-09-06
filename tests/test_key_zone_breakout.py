"""KeyZoneBreakoutStrategy：次日开盘执行+可接受区间放弃、R 倍数追踪止盈。

用手工构造的 `KeyZoneSignal`（绕开真正的关键区识别算法）隔离测试策略自己的
执行逻辑——关键区识别本身的测试在 tests/test_key_zone.py。仓位公式/强平/
爆仓完全复用 `PlatformBreakoutStrategy`，已经在 test_platform_breakout.py
里测过，这里不重复。
"""
import pandas as pd
import pytest

from conftest import make_ohlc
from signals.key_zone import KeyZoneSignal
from strategies.key_zone_breakout import KeyZoneBreakoutStrategy


def fixed_signal(n, entries=None, stops=None, touches=None, exec_los=None, exec_his=None):
    """把手写的逐 bar 数组包成一个 signal_fn，缺省填全 0/NaN。"""
    def fn(df):
        idx = df.index
        import numpy as np
        e = entries if entries is not None else [0] * n
        s = stops if stops is not None else [float("nan")] * n
        t = touches if touches is not None else [0] * n
        lo = exec_los if exec_los is not None else [float("nan")] * n
        hi = exec_his if exec_his is not None else [float("nan")] * n
        zl = [float("nan")] * n
        zh = [float("nan")] * n
        return KeyZoneSignal(pd.Series(e, idx), pd.Series(s, idx, dtype=float),
                             pd.Series(t, idx), pd.Series(lo, idx, dtype=float),
                             pd.Series(hi, idx, dtype=float),
                             pd.Series(zl, idx, dtype=float), pd.Series(zh, idx, dtype=float))
    return fn


FLAT_BAR = [100, 101, 99, 100]


# ── 执行：次日开盘 + 可接受区间 ──────────────────────────────────────────────

def test_entry_executes_at_next_bar_open_within_band():
    df = make_ohlc([FLAT_BAR, FLAT_BAR, [102, 103, 101, 102.5]] + [FLAT_BAR] * 5)
    sig = fixed_signal(len(df), entries=[0, 1, 0, 0, 0, 0, 0, 0],
                       stops=[float("nan"), 95, float("nan"), float("nan"),
                             float("nan"), float("nan"), float("nan"), float("nan")],
                       touches=[0, 2, 0, 0, 0, 0, 0, 0],
                       exec_los=[float("nan"), 100.5, float("nan"), float("nan"),
                                float("nan"), float("nan"), float("nan"), float("nan")],
                       exec_his=[float("nan"), 103.5, float("nan"), float("nan"),
                                float("nan"), float("nan"), float("nan"), float("nan")])
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, initial_capital=100_000)
    s.run()
    log = s.trade_log()
    assert len(log) == 1
    assert log.iloc[0]["entry_price"] == pytest.approx(102.0)   # bar2 的开盘价
    assert len(s.abandoned) == 0


def test_entry_abandoned_when_open_gaps_too_far_above_band():
    """开盘价跳空超过 exec_hi——放弃这笔交易，不追价。"""
    df = make_ohlc([FLAT_BAR, FLAT_BAR, [110, 111, 109, 110]] + [FLAT_BAR] * 5)
    sig = fixed_signal(len(df), entries=[0, 1] + [0] * 6,
                       stops=[float("nan"), 95] + [float("nan")] * 6,
                       touches=[0, 2] + [0] * 6,
                       exec_los=[float("nan"), 100.5] + [float("nan")] * 6,
                       exec_his=[float("nan"), 103.5] + [float("nan")] * 6)
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, initial_capital=100_000)
    s.run()
    assert len(s.trade_log()) == 0
    assert len(s.abandoned) == 1
    assert s.abandoned[0]["direction"] == 1


def test_entry_abandoned_when_open_fails_to_confirm_breakout():
    """开盘价没能维持在区间外（回落到 exec_lo 以下）——同样放弃。"""
    df = make_ohlc([FLAT_BAR, FLAT_BAR, [99, 100, 98, 99]] + [FLAT_BAR] * 5)
    sig = fixed_signal(len(df), entries=[0, 1] + [0] * 6,
                       stops=[float("nan"), 95] + [float("nan")] * 6,
                       touches=[0, 2] + [0] * 6,
                       exec_los=[float("nan"), 100.5] + [float("nan")] * 6,
                       exec_his=[float("nan"), 103.5] + [float("nan")] * 6)
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, initial_capital=100_000)
    s.run()
    assert len(s.trade_log()) == 0
    assert len(s.abandoned) == 1


# ── 同根 bar：止损照常生效，追踪止盈不应该无中生有 ──────────────────────────

def test_same_bar_stop_loss_can_still_trigger_on_entry_bar():
    """入场当根（开盘=102）自己就砸穿止损位 95——止损是价格位，同根 bar 照样生效。"""
    rows = [FLAT_BAR, FLAT_BAR, [102, 103, 90, 95]] + [FLAT_BAR] * 5
    df = make_ohlc(rows)
    n = len(df)
    entries = [0] * n; entries[1] = 1
    stops = [float("nan")] * n; stops[1] = 95.0
    touches = [0] * n; touches[1] = 2
    exec_los = [float("nan")] * n; exec_los[1] = 100.5
    exec_his = [float("nan")] * n; exec_his[1] = 103.5
    sig = fixed_signal(n, entries, stops, touches, exec_los, exec_his)
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, trail_start_r=0.0,
                                fixed_take_profit=0.30, initial_capital=100_000)
    s.run()
    log = s.trade_log()
    assert len(log) == 1
    assert log.iloc[0]["exit_reason"] == "stop_loss"
    assert log.iloc[0]["bars_held"] == 0   # 入场那根 bar 自己就止损了


def test_trailing_never_fires_as_a_phantom_same_bar_exit_at_breakeven():
    """
    回归用例：trail_start_r=0（"原始版"）时，刚开仓那根 bar 的 peak_price
    还没并入本 bar 自己的高点，如果追踪止盈照样在这根 bar 生效，回吐价会
    精确退化成 entry_price——而入场 bar 的最低价几乎总是低于它自己的开盘价，
    于是每次开仓都会在同一根 bar 里以保本价"止盈"离场。这里只验证这个精确
    的退化场景不会发生（"原始版"本身确实很容易在稍后的 bar 就被正常触发
    追踪止盈——那是设计使然，不是这条要防的问题）：如果交易以"trailing"
    离场，bars_held 不该是 0（入场当根自己）。
    """
    rows = [FLAT_BAR, FLAT_BAR, [102, 103, 101.5, 102.5]] + [FLAT_BAR] * 5
    df = make_ohlc(rows)
    n = len(df)
    entries = [0] * n; entries[1] = 1
    stops = [float("nan")] * n; stops[1] = 90.0   # 止损远低于本 bar 的低点，不会触发
    touches = [0] * n; touches[1] = 2
    exec_los = [float("nan")] * n; exec_los[1] = 100.5
    exec_his = [float("nan")] * n; exec_his[1] = 103.5
    sig = fixed_signal(n, entries, stops, touches, exec_los, exec_his)
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, trail_start_r=0.0,
                                fixed_take_profit=0.30, initial_capital=100_000)
    s.run()
    log = s.trade_log()
    assert len(log) == 1
    if log.iloc[0]["exit_reason"] == "trailing":
        assert log.iloc[0]["bars_held"] > 0, "入场当根不该凭空触发保本式追踪止盈"


# ── R 倍数追踪止盈 ───────────────────────────────────────────────────────────

def test_trailing_does_not_activate_before_reaching_r_multiple_threshold():
    """trail_start_r=2：浮盈不到 2 倍初始风险时，正常回撤不该被追踪止盈打断。"""
    # entry=102, stop=99 -> d=3；2R=106.5+ 附近才激活追踪。之后小幅回撤到 103.5
    # （远没到 2R），不该被"追踪"平仓，应该一直扛到数据结束。
    rows = ([FLAT_BAR, FLAT_BAR, [102, 103, 101.5, 102.5]]
           + [[104, 105, 103.5, 104.5]] * 3
           + [[103.5, 104, 103, 103.5]] * 3)
    df = make_ohlc(rows)
    n = len(df)
    entries = [0] * n; entries[1] = 1
    stops = [float("nan")] * n; stops[1] = 99.0
    touches = [0] * n; touches[1] = 2
    exec_los = [float("nan")] * n; exec_los[1] = 100.5
    exec_his = [float("nan")] * n; exec_his[1] = 103.5
    sig = fixed_signal(n, entries, stops, touches, exec_los, exec_his)
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, trail_start_r=2.0,
                                fixed_take_profit=0.30, initial_capital=100_000)
    s.run()
    log = s.trade_log()
    assert len(log) == 1
    assert log.iloc[0]["exit_reason"] == "end_of_data"


def test_trailing_activates_once_r_multiple_threshold_is_reached():
    """浮盈冲到 2R 以上后再显著回撤——这时追踪止盈应该生效并提前离场。"""
    # entry=102, stop=99 -> d=3, 2R=108。冲到 112(超过 2R 很多)后大幅回落到 105。
    rows = ([FLAT_BAR, FLAT_BAR, [102, 103, 101.5, 102.5]]
           + [[105, 112, 104, 111]]
           + [[105, 106, 104, 105]] * 5)
    df = make_ohlc(rows)
    n = len(df)
    entries = [0] * n; entries[1] = 1
    stops = [float("nan")] * n; stops[1] = 99.0
    touches = [0] * n; touches[1] = 2
    exec_los = [float("nan")] * n; exec_los[1] = 100.5
    exec_his = [float("nan")] * n; exec_his[1] = 103.5
    sig = fixed_signal(n, entries, stops, touches, exec_los, exec_his)
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, trail_start_r=2.0,
                                profit_trail_giveback=0.30,
                                fixed_take_profit=None, initial_capital=100_000)
    s.run()
    log = s.trade_log()
    assert len(log) == 1
    assert log.iloc[0]["exit_reason"] == "trailing"


# ── 固定止盈上限：None 时不生效（"趋势对照版"） ─────────────────────────────

def test_fixed_take_profit_none_disables_the_cap():
    """趋势对照版：fixed_take_profit=None 时，哪怕浮盈远超 30% 也不该被固定止盈打断。"""
    rows = ([FLAT_BAR, FLAT_BAR, [102, 103, 101.5, 102.5]]
           + [[104 + i, 105 + i, 103 + i, 104.5 + i] for i in range(0, 40, 4)])
    df = make_ohlc(rows)
    n = len(df)
    entries = [0] * n; entries[1] = 1
    stops = [float("nan")] * n; stops[1] = 99.0
    touches = [0] * n; touches[1] = 2
    exec_los = [float("nan")] * n; exec_los[1] = 100.5
    exec_his = [float("nan")] * n; exec_his[1] = 103.5
    sig = fixed_signal(n, entries, stops, touches, exec_los, exec_his)
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, trail_start_r=2.0,
                                fixed_take_profit=None, initial_capital=100_000)
    s.run()
    log = s.trade_log()
    assert len(log) == 1
    assert log.iloc[0]["exit_reason"] != "take_profit", "None 应该完全禁用固定止盈"


def test_fixed_take_profit_caps_gains_when_set():
    """同样的走势，设了 fixed_take_profit 就该在达到目标价时止盈离场。"""
    rows = ([FLAT_BAR, FLAT_BAR, [102, 103, 101.5, 102.5]]
           + [[104 + i, 105 + i, 103 + i, 104.5 + i] for i in range(0, 40, 4)])
    df = make_ohlc(rows)
    n = len(df)
    entries = [0] * n; entries[1] = 1
    stops = [float("nan")] * n; stops[1] = 99.0
    touches = [0] * n; touches[1] = 2
    exec_los = [float("nan")] * n; exec_los[1] = 100.5
    exec_his = [float("nan")] * n; exec_his[1] = 103.5
    sig = fixed_signal(n, entries, stops, touches, exec_los, exec_his)
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, trail_start_r=2.0,
                                fixed_take_profit=0.30, initial_capital=100_000)
    s.run()
    log = s.trade_log()
    assert len(log) == 1
    assert log.iloc[0]["exit_reason"] == "take_profit"


# ── 股票不能裸卖空 ───────────────────────────────────────────────────────────

def test_stock_ignores_short_signal_while_flat():
    df = make_ohlc([FLAT_BAR] * 8)
    n = len(df)
    entries = [0] * n; entries[1] = -1
    stops = [float("nan")] * n; stops[1] = 105.0
    touches = [0] * n; touches[1] = 2
    # exec 区间特意卡在次日真实开盘价(100)附近——如果这条规则失效、真的挂了
    # 一笔空单，它一定会顺利成交，不会被"开盘价不在可接受区间"这条无关的
    # 放弃逻辑意外掩盖掉，测试才是真的在测这条规则本身。
    exec_los = [float("nan")] * n; exec_los[1] = 99.5
    exec_his = [float("nan")] * n; exec_his[1] = 100.5
    sig = fixed_signal(n, entries, stops, touches, exec_los, exec_his)
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, futures=False, initial_capital=100_000)
    s.run()
    assert len(s.trade_log()) == 0, "股票没有融券，空仓时的做空信号应该被直接忽略"


def test_stock_short_signal_closes_an_existing_long_instead_of_opening_short():
    rows = [FLAT_BAR, FLAT_BAR, [102, 103, 101.5, 102.5], [100, 101, 98, 99]] + [FLAT_BAR] * 5
    df = make_ohlc(rows)
    n = len(df)
    entries = [0] * n; entries[1] = 1; entries[3] = -1
    stops = [float("nan")] * n; stops[1] = 90.0; stops[3] = 110.0
    touches = [0] * n; touches[1] = 2; touches[3] = 2
    exec_los = [float("nan")] * n; exec_los[1] = 100.5
    exec_his = [float("nan")] * n; exec_his[1] = 103.5
    # 第 3 根的做空信号也给一个真实能成交的 exec 区间（卡在次日 FLAT_BAR
    # 开盘价 100 附近）——如果"股票不能裸卖空"这条规则失效、真的挂了一笔
    # 反手空单，它会顺利成交并多出一笔交易，不会被无关的价格带检查掩盖。
    exec_los[3] = 99.5
    exec_his[3] = 100.5
    sig = fixed_signal(n, entries, stops, touches, exec_los, exec_his)
    # trail_start_r 拉高到不可能触及，避免默认的"原始版"(0) 提前把仓位
    # 追踪止盈平掉，干扰这里要测的"反向信号=离场"这条独立逻辑。
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, futures=False,
                                trail_start_r=100.0, initial_capital=100_000)
    s.run()
    log = s.trade_log()
    assert len(log) == 1, "不该多出一笔反手做空的交易"
    assert log.iloc[0]["direction"] == "long"
    assert log.iloc[0]["exit_reason"] == "reverse"


def test_futures_allows_short_entry_while_flat():
    df = make_ohlc([FLAT_BAR, FLAT_BAR, [98, 99, 97, 97.5]] + [FLAT_BAR] * 5)
    n = len(df)
    entries = [0] * n; entries[1] = -1
    stops = [float("nan")] * n; stops[1] = 105.0
    touches = [0] * n; touches[1] = 2
    exec_los = [float("nan")] * n; exec_los[1] = 96.5
    exec_his = [float("nan")] * n; exec_his[1] = 99.5
    sig = fixed_signal(n, entries, stops, touches, exec_los, exec_his)
    s = KeyZoneBreakoutStrategy(df, signal_fn=sig, futures=True, multiplier=10,
                                margin_rate=0.10, initial_capital=100_000)
    s.run()
    log = s.trade_log()
    assert len(log) == 1
    assert log.iloc[0]["direction"] == "short"
