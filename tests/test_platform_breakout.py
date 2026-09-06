"""PlatformBreakoutStrategy：按风险定仓位、价格位止损、混合止盈。

用手工构造的 `PlatformSignal`（绕开真正的平台识别算法）隔离测试策略自己的
执行逻辑——平台识别本身的测试在 tests/test_platform.py。
"""
import pandas as pd
import pytest

from conftest import make_ohlc
from signals.platform import PlatformSignal
from strategies.platform_breakout import PlatformBreakoutStrategy


def fixed_signal(entries, stops, vals):
    """把手写的 (entry, stop_price, validations) 三元组包成一个 signal_fn。"""
    def fn(df):
        idx = df.index
        return PlatformSignal(pd.Series(entries, idx),
                              pd.Series(stops, idx, dtype=float),
                              pd.Series(vals, idx))
    return fn


FLAT_BAR = [100, 101, 99, 100]


# ── 风险定仓位：_position_ratio 的公式推导，见类 docstring ──────────────────

def test_futures_sizing_matches_hand_derivation():
    """risk=1%equity=1000, stop_dist=100, multiplier=10 -> 1000/(100*10)=1手。"""
    df = make_ohlc([[3500, 3510, 3490, 3500]] * 5)
    s = PlatformBreakoutStrategy(df, futures=True, multiplier=10, margin_rate=0.20)
    state = {"cash": 100_000.0, "shares": 0.0}
    s._open_position(state, "d0", 1, stop_price=3400, validations=1, price=3500)
    assert state["shares"] == 1


def test_more_validations_means_more_risk_and_bigger_size():
    df = make_ohlc([[3500, 3510, 3490, 3500]] * 5)
    s = PlatformBreakoutStrategy(df, futures=True, multiplier=10, margin_rate=0.20)
    state = {"cash": 100_000.0, "shares": 0.0}
    s._open_position(state, "d0", 1, stop_price=3400, validations=3, price=3500)
    assert state["shares"] == 3   # risk=3% -> 3000/1000=3手


def test_risk_is_capped_at_max_risk_regardless_of_validations():
    df = make_ohlc([[3500, 3510, 3490, 3500]] * 5)
    s = PlatformBreakoutStrategy(df, futures=True, multiplier=10, margin_rate=0.20,
                                 risk_per_validation=0.01, max_risk=0.04)
    state = {"cash": 100_000.0, "shares": 0.0}
    s._open_position(state, "d0", 1, stop_price=3400, validations=10, price=3500)
    assert state["shares"] == 4   # 10%>4%，封顶在 4%


def test_stock_sizing_matches_hand_derivation():
    """risk=1000, stop_dist=5 -> qty=200 股。"""
    df = make_ohlc([FLAT_BAR] * 5)
    s = PlatformBreakoutStrategy(df, risk_per_validation=0.01, max_risk=0.04)
    state = {"cash": 100_000.0, "shares": 0.0}
    s._open_position(state, "d0", 1, stop_price=95, validations=1, price=100)
    assert state["shares"] == pytest.approx(200)


def test_short_sizing_is_symmetric():
    df = make_ohlc([[3500, 3510, 3490, 3500]] * 5)
    s = PlatformBreakoutStrategy(df, futures=True, multiplier=10, margin_rate=0.20)
    state = {"cash": 100_000.0, "shares": 0.0}
    s._open_position(state, "d0", -1, stop_price=3600, validations=1, price=3500)
    assert state["shares"] == -1


# ── 风险预算 ≠ 保证金：折算不到 1 手不等于开不起仓 ───────────────────────────
#
# 触发止损亏掉的是 stop_distance*multiplier，不是保证金本身——保证金率通常
# 远大于止损幅度占价格的比例，所以"即使触发止损也不会亏光保证金"。据此：
# 风险预算折算的手数不到 1，只要保证金掏得出 1 手，就该开 1 手，而不是直接
# 拒绝；真正决定"能不能开"的是保证金够不够，不是风险预算够不够。

def test_at_least_one_lot_when_margin_allows_even_if_risk_budget_implies_less():
    """
    risk_amount=1000(1%*100000), stop_dist=1000(很宽) -> qty_risk=floor(1000/10000)=0。
    margin_per_lot=3500*10*0.20=7000，free=100000 够开(甚至不止)1手。
    风险预算不够 1 手不该直接拒绝——保证金够，就按 1 手开。
    """
    df = make_ohlc([[3500, 3510, 3490, 3500]] * 5)
    s = PlatformBreakoutStrategy(df, futures=True, multiplier=10, margin_rate=0.20,
                                 risk_per_validation=0.01, max_risk=0.04)
    state = {"cash": 100_000.0, "shares": 0.0}
    opened = s._open_position(state, "d0", 1, stop_price=2500, validations=1, price=3500)
    assert opened and state["shares"] == 1


def test_rejected_only_when_margin_itself_is_insufficient():
    """连 1 手的保证金都拿不出来，才是真正的拒绝——不是风险预算折算的假象。"""
    df = make_ohlc([[3500, 3510, 3490, 3500]] * 5)
    s = PlatformBreakoutStrategy(df, futures=True, multiplier=10, margin_rate=0.20,
                                 risk_per_validation=0.01, max_risk=0.04)
    state = {"cash": 100.0, "shares": 0.0}   # 100 元连一手保证金(7000)零头都不够
    opened = s._open_position(state, "d0", 1, stop_price=2500, validations=1, price=3500)
    assert not opened and state["shares"] == 0
    assert len(s.rejected) == 1


def test_position_capped_by_margin_not_by_risk_budget_when_risk_wants_more():
    """
    equity=free=14500；risk_frac 封顶 4% -> risk_amount=580；stop_dist=10
    -> qty_risk=floor(580/100)=5。margin_per_lot=7000 -> qty_margin=floor(14500/7000)=2。
    风险预算想要 5 手，但保证金只够 2 手——最终应该是 2 手，不是 5 手，
    也不是被"至少 1 手"逻辑压成 1 手。
    """
    df = make_ohlc([[3500, 3510, 3490, 3500]] * 5)
    s = PlatformBreakoutStrategy(df, futures=True, multiplier=10, margin_rate=0.20,
                                 risk_per_validation=0.01, max_risk=0.04)
    state = {"cash": 14_500.0, "shares": 0.0}
    s._open_position(state, "d0", 1, stop_price=3490, validations=10, price=3500)
    assert state["shares"] == 2


def test_insufficient_capital_does_not_open_a_position():
    df = make_ohlc([[3500, 3510, 3490, 3500]] * 5)
    s = PlatformBreakoutStrategy(df, futures=True, multiplier=10, margin_rate=0.20)
    state = {"cash": 1.0, "shares": 0.0}
    opened = s._open_position(state, "d0", 1, stop_price=3400, validations=1, price=3500)
    assert not opened and state["shares"] == 0


def test_zero_stop_distance_does_not_open_a_position():
    """entry_price == stop_price 时 stop_distance=0，除以 0 是不允许发生的。"""
    df = make_ohlc([FLAT_BAR] * 5)
    s = PlatformBreakoutStrategy(df)
    state = {"cash": 100_000.0, "shares": 0.0}
    opened = s._open_position(state, "d0", 1, stop_price=100, validations=1, price=100)
    assert not opened


# ── 离场：价格位止损 ─────────────────────────────────────────────────────────

def test_stop_loss_fills_at_the_platform_stop_price():
    rows = [FLAT_BAR, FLAT_BAR, [99, 100, 93, 94]]   # low=93 跌破 stop=95
    df = make_ohlc(rows)
    s = PlatformBreakoutStrategy(df, signal_fn=fixed_signal([1, 0, 0], [95, 95, 95], [1, 1, 1]))
    s.run()
    t = s.trade_log()
    assert len(t) == 1
    assert t.exit_reason[0] == "stop_loss"
    assert t.exit_price[0] == pytest.approx(95.0)


def test_stop_loss_fills_at_open_when_gapped():
    rows = [FLAT_BAR, [85, 86, 84, 85], [85, 86, 84, 85]]   # 直接跳空低开
    df = make_ohlc(rows)
    s = PlatformBreakoutStrategy(df, signal_fn=fixed_signal([1, 0, 0], [95, 95, 95], [1, 1, 1]))
    s.run()
    t = s.trade_log()
    assert t.exit_price[0] == pytest.approx(85.0)   # 按开盘价成交，不是止损价 95


# ── 离场：固定止盈 ───────────────────────────────────────────────────────────

def test_fixed_take_profit_at_configured_target():
    rows = [FLAT_BAR, FLAT_BAR, [128, 132, 127, 131]]   # high=132 > 130(30% TP)
    df = make_ohlc(rows)
    s = PlatformBreakoutStrategy(df, signal_fn=fixed_signal([1, 0, 0], [70, 70, 70], [1, 1, 1]),
                                 fixed_take_profit=0.30)
    s.run()
    t = s.trade_log()
    assert t.exit_reason[0] == "take_profit"
    assert t.exit_price[0] == pytest.approx(130.0)


# ── 离场：利润追踪止盈（回吐峰值利润，不是价格回撤）─────────────────────────

def test_profit_trailing_long_exact_fill():
    rows = [FLAT_BAR,
           [100, 116, 99, 115],     # high=116 -> 下一根 bar 才生效的 peak_profit=0.16
           [114, 115, 110, 112]]    # 不跳空，low=110 精确命中 giveback 价
    df = make_ohlc(rows)
    s = PlatformBreakoutStrategy(df, signal_fn=fixed_signal([1, 0, 0], [70, 70, 70], [1, 1, 1]),
                                 profit_trail_start=0.10, profit_trail_giveback=0.30)
    s.run()
    t = s.trade_log()
    expected = 100 * (1 + 0.16 * 0.7)
    assert t.exit_reason[0] == "trailing"
    assert t.exit_price[0] == pytest.approx(expected)


def test_profit_trailing_short_exact_fill():
    rows = [FLAT_BAR,
           [100, 101, 85, 86],      # low=85 -> peak_profit=(100/85-1)
           [88, 91, 90, 89]]        # 不跳空，high=91 精确命中 giveback 价
    df = make_ohlc(rows)
    s = PlatformBreakoutStrategy(df, signal_fn=fixed_signal([-1, 0, 0], [130, 130, 130], [1, 1, 1]),
                                 profit_trail_start=0.10, profit_trail_giveback=0.30)
    s.run()
    t = s.trade_log()
    peak = 100 / 85 - 1
    expected = 100 / (1 + peak * 0.7)
    assert t.exit_reason[0] == "trailing"
    assert t.exit_price[0] == pytest.approx(expected)


def test_profit_below_trail_start_does_not_activate_trailing():
    """浮盈没到 profit_trail_start 门槛，追踪止盈不该介入。"""
    rows = [FLAT_BAR,
           [100, 105, 99, 104],    # peak_profit=0.05 < 0.10，追踪未激活
           [99, 100, 90, 95]]      # 大幅回落也不该被追踪止盈捕获
    df = make_ohlc(rows)
    s = PlatformBreakoutStrategy(df, signal_fn=fixed_signal([1, 0, 0], [70, 70, 70], [1, 1, 1]),
                                 profit_trail_start=0.10)
    s.run()
    t = s.trade_log()
    assert t.exit_reason[0] != "trailing"


def test_new_peak_within_the_same_bar_does_not_trigger_giveback_immediately():
    """
    回归测试：peak_profit_pct 必须先用于本 bar 的止盈判定，再用本 bar 的
    极值更新——不然"当天刚创新高、当天又回落"会被误判成"回吐了利润"当场止盈。
    """
    rows = [FLAT_BAR, [100, 116, 99, 115]]   # 创出新高的同一根 bar 不该立刻止盈
    df = make_ohlc(rows)
    s = PlatformBreakoutStrategy(df, signal_fn=fixed_signal([1, 0], [70, 70], [1, 1]),
                                 profit_trail_start=0.10)
    s.run()
    t = s.trade_log()
    assert t.exit_reason[0] == "end_of_data"   # 没有被中途止盈


# ── 反向信号 / 反手 ──────────────────────────────────────────────────────────

def test_reverse_signal_flips_the_position():
    rows = [FLAT_BAR, FLAT_BAR, FLAT_BAR]
    df = make_ohlc(rows)
    s = PlatformBreakoutStrategy(df, signal_fn=fixed_signal([1, -1, 0], [95, 105, 0], [1, 1, 0]))
    s.run()
    t = s.trade_log()
    assert len(t) == 2
    assert t.direction.tolist() == ["long", "short"]
    assert t.exit_reason[0] == "reverse"


def test_exit_on_reverse_false_ignores_opposite_signal():
    rows = [FLAT_BAR, FLAT_BAR, FLAT_BAR]
    df = make_ohlc(rows)
    s = PlatformBreakoutStrategy(df, signal_fn=fixed_signal([1, -1, 0], [95, 105, 0], [1, 1, 0]),
                                 exit_on_reverse=False)
    s.run()
    t = s.trade_log()
    assert len(t) == 1   # 反向信号被忽略，原多头仓位继续持有


# ── 与 engine 集成：交易流水携带 stop_price/validations ────────────────────

def test_trade_log_carries_stop_price_and_validations():
    rows = [FLAT_BAR, FLAT_BAR, [99, 100, 93, 94]]
    df = make_ohlc(rows)
    s = PlatformBreakoutStrategy(df, signal_fn=fixed_signal([1, 0, 0], [95, 95, 95], [3, 3, 3]))
    s.run()
    t = s.trade_log()
    assert t.stop_price[0] == pytest.approx(95.0)
    assert t.validations[0] == 3


def test_futures_requires_contract_spec():
    df = make_ohlc([FLAT_BAR] * 3)
    with pytest.raises(ValueError, match="必须确定合约规格"):
        PlatformBreakoutStrategy(df, futures=True)


def test_missing_ohlc_columns_raise():
    df = pd.DataFrame({"close": [1, 2, 3]}, index=pd.date_range("2024-01-01", periods=3))
    with pytest.raises(ValueError, match="缺少列"):
        PlatformBreakoutStrategy(df)
