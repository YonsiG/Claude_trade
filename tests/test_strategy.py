"""执行循环：信号语义、离场、成交价、加仓、流水对账。

这里的每一条都对应一个曾经真实存在过的 bug。
"""
import pytest

from conftest import make_ohlc, make_signal_fn, flat_signal
from signals.base import Signal
from strategies.single_signal import SingleSignalStrategy


FLAT_BAR = [100, 101, 99, 100]


def strat(df, entries, sizes=None, **kw):
    kw.setdefault("trail_pct", None)
    kw.setdefault("sl_pct", None)
    return SingleSignalStrategy(df, make_signal_fn(entries, sizes), **kw)


# ── 信号语义：entry 是事件，不是持仓状态 ────────────────────────────────────

def test_position_survives_zero_entry_bars():
    """entry==0 是"无事发生"，不是"应该空仓"。旧实现每笔只持 1 根 bar。"""
    df = make_ohlc([FLAT_BAR] * 10)
    s = strat(df, [1] + [0] * 9)
    s.run()
    t = s.trade_log()
    assert len(t) == 1
    assert t.bars_held[0] == 9
    assert t.exit_reason[0] == "end_of_data"


def test_reverse_signal_flips_position():
    """持多时收到空头信号，旧实现两个分支都不进，信号被静默吞掉。"""
    df = make_ohlc([FLAT_BAR] * 5)
    s = strat(df, [1, 0, -1, 0, 0])
    s.run()
    t = s.trade_log()
    assert list(t.direction) == ["long", "short"]
    assert t.exit_reason[0] == "reverse"


def test_exit_on_reverse_false_ignores_opposite_signal():
    df = make_ohlc([FLAT_BAR] * 5)
    s = strat(df, [1, 0, -1, 0, 0], exit_on_reverse=False)
    s.run()
    assert len(s.trade_log()) == 1


def test_same_direction_signal_is_ignored_without_pyramid():
    df = make_ohlc([FLAT_BAR] * 5)
    s = strat(df, [1, 1, 1, 0, 0])
    s.run()
    t = s.trade_log()
    assert len(t) == 1 and t.n_adds[0] == 0


# ── 止损成交价 ──────────────────────────────────────────────────────────────

def test_intrabar_stop_fills_at_trigger_price_not_close():
    """low 击穿止损位但收盘收回。用收盘价判定会系统性低估回撤。"""
    df = make_ohlc([FLAT_BAR, [100, 101, 88, 100], FLAT_BAR])
    s = strat(df, [1, 0, 0], sl_pct=0.10)
    s.run()
    t = s.trade_log()
    assert t.exit_price[0] == pytest.approx(90.0)
    assert t.exit_reason[0] == "stop_loss"


def test_close_only_stops_do_not_trigger_on_wick():
    df = make_ohlc([FLAT_BAR, [100, 101, 88, 100], FLAT_BAR])
    s = strat(df, [1, 0, 0], sl_pct=0.10, intrabar_stops=False)
    s.run()
    assert s.trade_log().exit_reason[0] == "end_of_data"


def test_gap_through_stop_fills_at_open():
    """跳空越过止损价时不能假设能在止损价拿到。"""
    df = make_ohlc([FLAT_BAR, [85, 86, 84, 85], [85, 86, 84, 85]])
    s = strat(df, [1, 0, 0], sl_pct=0.10)
    s.run()
    assert s.trade_log().exit_price[0] == pytest.approx(85.0)


def test_stopped_bar_does_not_rebuy_at_same_price():
    """否则止损当场失效。"""
    df = make_ohlc([FLAT_BAR, [100, 101, 88, 100], FLAT_BAR])
    s = strat(df, [1, 1, 0], sl_pct=0.10)
    s.run()
    assert len(s.trade_log()) == 1


def test_same_bar_reentry_can_be_enabled():
    df = make_ohlc([FLAT_BAR, [100, 101, 88, 100], FLAT_BAR])
    s = strat(df, [1, 1, 0], sl_pct=0.10, allow_same_bar_reentry=True)
    s.run()
    assert len(s.trade_log()) == 2


def test_trailing_peak_does_not_leak_between_positions():
    """第一笔冲到 200 后回落止盈；第二笔在 100 附近开仓，不该继承旧峰值。"""
    df = make_ohlc([FLAT_BAR, [100, 200, 99, 200]] + [FLAT_BAR] * 4)
    s = strat(df, [1, 0, 0, 1, 0, 0], trail_pct=0.30)
    s.run()
    t = s.trade_log()
    assert t.exit_reason[0] == "trailing"
    assert t.exit_reason[1] == "end_of_data"    # 泄漏的话这里会是 trailing


# ── 成交时点 ────────────────────────────────────────────────────────────────

def test_next_open_execution():
    df = make_ohlc([FLAT_BAR, [110, 111, 109, 110], [110, 111, 109, 110]])
    s = strat(df, [1, 0, 0], execution="next_open")
    s.run()
    assert s.trade_log().entry_price[0] == pytest.approx(110.0)


# ── 加仓 ────────────────────────────────────────────────────────────────────

def test_all_in_entry_leaves_nothing_to_pyramid_with():
    """size=1.0 首笔就用光资金，加仓自然是空操作——不是 bug，是没钱了。"""
    df = make_ohlc([FLAT_BAR, [110, 111, 109, 110], [120, 121, 119, 120], FLAT_BAR])
    s = strat(df, [1, 1, 1, 0], pyramid=True, max_adds=3)
    s.run()
    assert s.trade_log().n_adds[0] == 0


def test_pyramid_adds_and_averages_entry_price():
    df = make_ohlc([FLAT_BAR, [110, 111, 109, 110], [120, 121, 119, 120], FLAT_BAR])
    s = strat(df, [1, 1, 1, 0], sizes=[0.5, 0.5, 0.5, 0], pyramid=True, max_adds=3)
    s.run()
    t = s.trade_log()
    assert len(t) == 1
    assert t.n_adds[0] == 2
    assert 100 < t.entry_price[0] < 120          # 加权平均，介于各次成交价之间


def test_max_adds_caps_pyramiding():
    df = make_ohlc([FLAT_BAR, [110, 111, 109, 110], [120, 121, 119, 120], FLAT_BAR])
    s = strat(df, [1, 1, 1, 0], sizes=[0.5, 0.5, 0.5, 0], pyramid=True, max_adds=1)
    s.run()
    assert s.trade_log().n_adds[0] == 1


# ── 风控 ────────────────────────────────────────────────────────────────────

def test_bust_is_detected_and_equity_never_negative():
    df = make_ohlc([[p, p, p, p] for p in (100, 100, 200, 400, 600)])
    s = strat(df, [-1, 0, 0, 0, 0])
    eq = s.run()
    assert s.bust is not None
    assert eq.min() >= 0


def test_equity_is_floored_at_zero_under_leverage_gap():
    """跳空击穿保证金线时权益已为负，但资金曲线按 0 封底：亏光就是 -100%。"""
    rows = [[p, p * 1.01, p * 0.99, p] for p in (5000, 5000, 4000, 3000, 1000, 500)]
    df = make_ohlc(rows)
    s = SingleSignalStrategy(df, make_signal_fn([1, 0, 0, 0, 0, 0]),
                             trail_pct=None, sl_pct=None,
                             futures=True, multiplier=10, margin_rate=0.10)
    eq = s.run()
    assert eq.min() >= 0
    assert s.bust is not None or s.margin_calls


# ── 合约规格 ────────────────────────────────────────────────────────────────

def test_futures_without_contract_spec_raises():
    """不同品种乘数差异极大，沉默地用 1.0 会让每个品种都算错。"""
    df = make_ohlc([FLAT_BAR] * 2)
    with pytest.raises(ValueError, match="必须确定合约规格"):
        SingleSignalStrategy(df, flat_signal, futures=True)


def test_ticker_resolves_multiplier_and_margin():
    df = make_ohlc([FLAT_BAR] * 2)
    s = SingleSignalStrategy(df, flat_signal, futures=True, ticker="RB0")
    assert s.multiplier == 10.0
    assert 0 < s.margin_rate < 1


def test_unknown_ticker_raises_with_guidance():
    df = make_ohlc([FLAT_BAR] * 2)
    with pytest.raises(KeyError, match="不在这张表里"):
        SingleSignalStrategy(df, flat_signal, futures=True, ticker="GC=F")


def test_stock_mode_unaffected_by_contract_logic():
    df = make_ohlc([FLAT_BAR] * 2)
    s = SingleSignalStrategy(df, flat_signal)
    assert s.multiplier == 1.0 and s.margin_rate == 1.0


# ── 流水对账 ────────────────────────────────────────────────────────────────

def test_trade_pnl_reconciles_with_equity_curve():
    """流水盈亏合计必须等于资金曲线净变化，含手续费、含期末未平仓。"""
    df = make_ohlc([FLAT_BAR, [105, 106, 104, 105], [95, 96, 94, 95], FLAT_BAR])
    s = strat(df, [1, 0, -1, 0], fee_rate=0.001)
    eq = s.run()
    t = s.trade_log()
    assert t.pnl.sum() == pytest.approx(eq.iloc[-1] - 100_000, abs=1e-6)


def test_rerunning_a_strategy_resets_the_log():
    df = make_ohlc([FLAT_BAR] * 5)
    s = strat(df, [1, 0, -1, 0, 0])
    s.run()
    first = len(s.trade_log())
    s.run()
    assert len(s.trade_log()) == first
