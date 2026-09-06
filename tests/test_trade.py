"""仓位原语：手续费、加仓、保证金、强平。

这是全框架的地基——策略层所有盈亏都由这几个函数决定。
"""
import pytest

from tools.trade import (buy, sell, short, cover, free_capital,
                         trailing_take_profit, margin_call, force_close)


def new_state(cash=100_000.0):
    return {"cash": cash, "shares": 0.0}


# ── 基本记账 ────────────────────────────────────────────────────────────────

def test_buy_sell_roundtrip_conserves_cash_without_fees():
    st = new_state()
    buy(st, 100)
    assert st["shares"] == pytest.approx(1000)
    sell(st, 100)
    assert st["shares"] == 0
    assert st["cash"] == pytest.approx(100_000)


def test_fee_is_deducted_on_both_legs():
    st = new_state()
    buy(st, 100, fee_rate=0.001)
    sell(st, 100, fee_rate=0.001)
    # 买入手数由 budget/(price*(1+fee)) 决定，卖出再扣一次
    qty = 100_000 / (100 * 1.001)
    assert st["cash"] == pytest.approx(qty * 100 * (1 - 0.001))
    assert st["cash"] < 100_000


def test_short_cover_roundtrip():
    st = new_state()
    short(st, 100)
    assert st["shares"] == pytest.approx(-1000)
    cover(st, 100)
    assert st["shares"] == 0
    assert st["cash"] == pytest.approx(100_000)


def test_partial_sell_keeps_remainder():
    st = new_state()
    buy(st, 100)
    sell(st, 100, ratio=0.4)
    assert st["shares"] == pytest.approx(600)


# ── 移动止盈的峰值生命周期 ──────────────────────────────────────────────────

def test_trail_peak_is_cleared_when_position_closes():
    """峰值属于单次持仓。不清除会让下一笔仓位继承旧峰值并被立刻打掉。"""
    st = new_state()
    buy(st, 100)
    trailing_take_profit(st, 200, 1, 10_000, 0.30)
    assert st["_trail_peak"] == 200

    sell(st, 200)                       # 经由信号平仓，不是 trailing 自己触发
    assert "_trail_peak" not in st

    buy(st, 100)                        # 全新一笔
    fired = trailing_take_profit(st, 100, 0, 10_000, 0.30)
    assert fired is False
    assert st["shares"] > 0


def test_trail_peak_cleared_on_cover_too():
    st = new_state()
    short(st, 200)
    trailing_take_profit(st, 100, 1, 10_000, 0.30)
    assert "_trail_peak" in st
    cover(st, 100)
    assert "_trail_peak" not in st


# ── 可用资金 ────────────────────────────────────────────────────────────────

def test_free_capital_equals_cash_when_flat():
    assert free_capital(new_state(), 100) == pytest.approx(100_000)


def test_free_capital_after_partial_entry():
    st = new_state()
    buy(st, 100, ratio=0.5)
    assert free_capital(st, 100) == pytest.approx(50_000)


def test_free_capital_positive_while_cash_negative_under_leverage():
    """杠杆持仓下 cash 是负数（借入头寸），但仍可能有加仓空间。"""
    st = new_state()
    buy(st, 3000, futures=True, multiplier=10, margin_rate=0.20)
    assert st["cash"] < 0
    assert free_capital(st, 3000, True, 10, 0.20) >= 0


# ── 加仓 ────────────────────────────────────────────────────────────────────

def test_buy_without_add_is_noop_when_already_long():
    st = new_state()
    buy(st, 100, ratio=0.5)
    before = st["shares"]
    buy(st, 100, ratio=0.5)
    assert st["shares"] == before


def test_buy_with_add_accumulates():
    st = new_state()
    buy(st, 100, ratio=0.5)
    buy(st, 100, ratio=1.0, add=True)
    assert st["shares"] == pytest.approx(1000)
    assert st["cash"] == pytest.approx(0.0, abs=1e-9)


def test_short_with_add_accumulates():
    st = new_state()
    short(st, 100, ratio=0.5)
    short(st, 100, ratio=1.0, add=True)
    assert st["shares"] == pytest.approx(-1000)


def test_futures_add_increases_lots():
    st = new_state()
    buy(st, 3000, futures=True, multiplier=10, margin_rate=0.20, ratio=0.5)
    first = st["shares"]
    buy(st, 3000, futures=True, multiplier=10, margin_rate=0.20, ratio=1.0, add=True)
    assert st["shares"] > first


# ── 保证金 ──────────────────────────────────────────────────────────────────

def test_full_notional_sizing_is_the_default():
    """margin_rate=1.0 = 无杠杆，保持旧行为。"""
    st = new_state()
    buy(st, 3000, futures=True, multiplier=10)
    assert st["shares"] == 3                        # 100k / 30k 一手


def test_margin_sizing_increases_lots_and_leverage():
    st = new_state()
    buy(st, 3000, futures=True, multiplier=10, margin_rate=0.20)
    assert st["shares"] == 16
    notional = st["shares"] * 3000 * 10
    assert notional / 100_000 == pytest.approx(4.8)


def test_ratio_round_trip_does_not_lose_an_affordable_lot_to_float_noise():
    """
    回归用例：策略层先算出"能买几手"再反推 ratio=qty*cost_per_lot/free，
    short()/buy() 内部又要把 ratio 乘回 free 换算回预算再重新 floor 一次
    手数——这一去一回的浮点乘除不保证严格可逆，真实恰好等于整数手数的
    位置可能算出 0.999999999999，直接 floor 会凭空丢掉本该买得起的最后
    一手。这里用一组实盘回测里真实触发过这个问题的数值（AG0 期货，
    multiplier=15, margin_rate=0.3）复现：ratio 是按买得起 1 手反推出来的，
    short() 应该真的开出这 1 手，不能因为浮点噪声变成 0 手静默放弃。
    """
    st = new_state(cash=107456.59307686519)
    price = 7220.376002211777
    multiplier, margin_rate = 15.0, 0.3
    margin_per_lot = price * multiplier * margin_rate
    ratio = min(1.0, (1 * margin_per_lot) / st["cash"])   # 反推：买 1 手对应的 ratio
    short(st, price, futures=True, multiplier=multiplier, margin_rate=margin_rate, ratio=ratio)
    assert st["shares"] == -1, "ratio 明明是按买得起 1 手反推出来的，不该因为浮点误差变成 0 手"


def test_equity_is_self_consistent_right_after_leveraged_entry():
    st = new_state()
    buy(st, 3000, futures=True, multiplier=10, margin_rate=0.20)
    equity = st["cash"] + st["shares"] * 3000 * 10
    assert equity == pytest.approx(100_000)


def test_leveraged_return_scales_with_leverage():
    st = new_state()
    buy(st, 3000, futures=True, multiplier=10, margin_rate=0.20)
    equity = st["cash"] + st["shares"] * 3300 * 10   # +10%
    assert equity == pytest.approx(148_000)          # 4.8x 杠杆


def test_gold_multiplier_makes_one_lot_unaffordable_at_small_capital():
    """AU 一手 = 价格 x 1000，十万本金连一手保证金都不够——真实约束，不是 bug。"""
    st = new_state()
    buy(st, 800, futures=True, multiplier=1000, margin_rate=0.26)
    assert st["shares"] == 0
    st = new_state(500_000)
    buy(st, 800, futures=True, multiplier=1000, margin_rate=0.26)
    assert st["shares"] == 2


def test_entry_never_triggers_an_immediate_margin_call():
    """开仓手数由 floor 得出，权益必然不低于所需保证金。"""
    st = new_state()
    buy(st, 3000, futures=True, multiplier=10, margin_rate=0.20)
    assert margin_call(st, 3000, "d0", 0.20, multiplier=10) is None


def test_margin_call_liquidates_when_equity_falls_below_requirement():
    st = new_state()
    buy(st, 3000, futures=True, multiplier=10, margin_rate=0.20)
    rec = margin_call(st, 2700, "d1", 0.20, multiplier=10)
    assert rec is not None
    assert st["shares"] == 0
    assert rec["equity"] < rec["required_margin"]


def test_no_margin_call_in_full_notional_mode():
    st = new_state()
    buy(st, 3000, futures=True, multiplier=10)
    assert margin_call(st, 1000, "d", 1.0, multiplier=10) is None


# ── 爆仓 ────────────────────────────────────────────────────────────────────

def test_force_close_liquidates_at_zero_equity():
    st = new_state()
    short(st, 100)
    assert force_close(st, 100, "d") is None          # 权益仍是 100k
    bust = force_close(st, 200, "d")                  # 价格翻倍，权益归零
    assert bust is not None
    assert st["shares"] == 0
