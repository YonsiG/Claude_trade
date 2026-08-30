"""回测引擎：信号契约、基准、绩效指标、组合聚合。"""
import pandas as pd
import pytest

from conftest import make_ohlc, make_signal_fn, flat_signal, ramp
from backtest import engine
from backtest.portfolio import run_portfolio, report_portfolio
from signals.base import Signal
from strategies.single_signal import SingleSignalStrategy


FLAT_BAR = [100, 101, 99, 100]


# ── Signal 契约 ─────────────────────────────────────────────────────────────

def test_signal_validate_rejects_illegal_direction():
    idx = pd.date_range("2024-01-01", periods=3)
    bad = Signal(pd.Series([2, 0, 0], index=idx), pd.Series([1.0, 0, 0], index=idx))
    with pytest.raises(ValueError, match="只能取"):
        bad.validate()


def test_signal_validate_zeroes_size_where_no_entry():
    idx = pd.date_range("2024-01-01", periods=3)
    s = Signal(pd.Series([1, 0, 0], index=idx),
               pd.Series([0.5, 0.9, 0.3], index=idx)).validate()
    assert list(s.size) == [0.5, 0.0, 0.0]


def test_signal_from_strength_splits_direction_and_size():
    idx = pd.date_range("2024-01-01", periods=3)
    s = Signal.from_strength(pd.Series([0.8, 0.0, -0.4], index=idx))
    assert list(s.entry) == [1, 0, -1]
    assert list(s.size) == pytest.approx([0.8, 0.0, 0.4])


# ── 基准 ────────────────────────────────────────────────────────────────────

def test_benchmark_is_buy_and_hold():
    df = make_ohlc([FLAT_BAR, [100, 101, 99, 150], [100, 101, 99, 200]])
    s = SingleSignalStrategy(df, flat_signal, trail_pct=None, sl_pct=None)
    sm = engine.run("T", "a", "b", s)
    assert sm["bench_total_return"] == pytest.approx(100.0)
    assert sm["total_return"] == 0.0
    assert sm["n_trades"] == 0


def test_benchmark_never_goes_negative_under_leverage():
    """不封底会算出 -200% 这种数字，夏普和回撤全部失真。"""
    rows = [[p, p * 1.01, p * 0.99, p] for p in (5000, 5000, 4000, 3000, 1000, 500)]
    s = SingleSignalStrategy(make_ohlc(rows), flat_signal,
                             futures=True, multiplier=10, margin_rate=0.10)
    sm = engine.run("CRASH", "a", "b", s)
    assert sm["benchmark_curve"].min() >= 0
    assert sm["bench_total_return"] >= -100


# ── 指标 ────────────────────────────────────────────────────────────────────

def test_zero_trade_run_is_flagged():
    df = make_ohlc([FLAT_BAR] * 5)
    s = SingleSignalStrategy(df, flat_signal, trail_pct=None, sl_pct=None)
    assert "零成交" in engine.report(engine.run("T", "a", "b", s))


def test_insufficient_capital_is_reported_separately_from_zero_trades():
    """信号触发了但一手都开不出来——不该被读成"信号从未触发"。"""
    rows = [[800, 808, 792, 800]] * 5
    s = SingleSignalStrategy(make_ohlc(rows), make_signal_fn([1, 1, 1, 1, 1]),
                             trail_pct=None, sl_pct=None,
                             futures=True, multiplier=1000, margin_rate=0.26)
    txt = engine.report(engine.run("AU", "a", "b", s))
    assert "资金不足以开出一手" in txt
    assert "零成交" not in txt


def test_risk_free_rate_lowers_sharpe():
    df = make_ohlc([[p, p * 1.01, p * 0.99, p] for p in ramp(60, 1.002)])
    s = SingleSignalStrategy(df, make_signal_fn([1] + [0] * 59),
                             trail_pct=None, sl_pct=None)
    a = engine.run("T", "a", "b", s, rf=0.0)["sharpe"]
    b = engine.run("T", "a", "b", s, rf=0.10)["sharpe"]
    assert b < a


def test_metric_keys_are_all_serialisable():
    df = make_ohlc([FLAT_BAR] * 5)
    s = SingleSignalStrategy(df, make_signal_fn([1, 0, -1, 0, 0]),
                             trail_pct=None, sl_pct=None)
    sm = engine.run("T", "a", "b", s)
    for k in engine._METRIC_KEYS:
        assert not isinstance(sm.get(k), (pd.Series, pd.DataFrame))


# ── 组合 ────────────────────────────────────────────────────────────────────

def build_sleeves():
    up = make_ohlc([[p, p * 1.01, p * 0.99, p] for p in ramp(30, 1.01)])
    down = make_ohlc([[p, p * 1.01, p * 0.99, p] for p in ramp(30, 0.99)])
    return {
        "UP": SingleSignalStrategy(up, make_signal_fn([1] + [0] * 29),
                                   trail_pct=None, sl_pct=None),
        "DOWN": SingleSignalStrategy(down, make_signal_fn([1] + [0] * 29),
                                     trail_pct=None, sl_pct=None),
    }


def test_portfolio_splits_capital_equally():
    sm = run_portfolio(build_sleeves(), capital=200_000)
    assert sm["initial_capital"] == pytest.approx(200_000)
    assert list(sm["sleeves"].capital) == pytest.approx([100_000, 100_000])


def test_portfolio_equity_is_the_sum_of_sleeves():
    sm = run_portfolio(build_sleeves(), capital=200_000)
    total = sum(r["equity_curve"].iloc[-1] for r in sm["results"].values())
    assert sm["final_equity"] == pytest.approx(total)


def test_portfolio_weights_are_normalised():
    sm = run_portfolio(build_sleeves(), capital=200_000, weights={"UP": 3, "DOWN": 1})
    caps = dict(zip(sm["sleeves"].sleeve, sm["sleeves"].capital))
    assert caps["UP"] == pytest.approx(150_000)
    assert caps["DOWN"] == pytest.approx(50_000)


def test_portfolio_trades_carry_the_sleeve_name():
    sm = run_portfolio(build_sleeves(), capital=200_000)
    assert set(sm["trades"].sleeve) == {"UP", "DOWN"}


def test_portfolio_aligns_different_calendars():
    """美股与国内期货交易日不同：取并集，各自前向填充。"""
    a = make_ohlc([FLAT_BAR] * 10, start="2024-01-01")
    b = make_ohlc([FLAT_BAR] * 10, start="2024-01-05")
    sleeves = {
        "A": SingleSignalStrategy(a, flat_signal, trail_pct=None, sl_pct=None),
        "B": SingleSignalStrategy(b, flat_signal, trail_pct=None, sl_pct=None),
    }
    sm = run_portfolio(sleeves, capital=200_000)
    idx = sm["equity_curve"].index
    assert idx.min() == a.index.min() and idx.max() == b.index.max()
    assert sm["equity_curve"].notna().all()


def test_portfolio_report_renders():
    sm = run_portfolio(build_sleeves(), capital=200_000)
    txt = report_portfolio(sm)
    assert "子仓明细" in txt and "UP" in txt and "DOWN" in txt


def test_empty_portfolio_raises():
    with pytest.raises(ValueError):
        run_portfolio({})
