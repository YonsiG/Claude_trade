"""趋势判定：门槛、confidence 语义、duration、换月标记。"""
import numpy as np
import pytest

from conftest import make_bars, ramp
from signals.base import Signal
from trend.base import TrendType
from trend.detector import detect_trend


def classify(closes, n=20, band=0.004, **kw):
    kw.setdefault("compute_duration", False)
    return detect_trend(make_bars(closes, band=band), n=n, **kw)


# ── 判定门槛 ────────────────────────────────────────────────────────────────

def test_nearly_flat_but_perfectly_linear_is_not_a_trend():
    """斜率 0.0005%/bar + R²≈1。旧实现只看 slope>0，会判成高置信度上升趋势。"""
    r = classify([100 + 0.0005 * i for i in range(20)])
    assert r.trend != TrendType.UPTREND


def test_steep_but_noisy_fails_the_r2_gate():
    rng = np.random.default_rng(0)
    noisy = [100 * (1.004 ** i) + rng.normal(0, 8) for i in range(20)]
    assert classify(noisy).trend != TrendType.UPTREND


def test_clean_trends_are_still_detected():
    assert classify(ramp(20, 1.006)).trend == TrendType.UPTREND
    assert classify(ramp(20, 0.994)).trend == TrendType.DOWNTREND


def test_oscillation_detected_with_realistic_bar_ranges():
    osc = [100 + 5 * np.sin(i / 1.2) for i in range(20)]
    assert classify(osc, band=0.012).trend == TrendType.OSCILLATING


def test_trend_wins_over_oscillation_by_priority():
    """带波动的趋势不该被震荡抢走——靠优先级裁决，不靠比 confidence。"""
    rng = np.random.default_rng(3)
    px = [100 * (1.005 ** i) + rng.normal(0, 0.6) for i in range(20)]
    assert classify(px, band=0.012).trend == TrendType.UPTREND


# ── confidence 语义 ─────────────────────────────────────────────────────────

def test_confidence_is_in_half_to_one_when_qualified():
    """旧实现各检测器 confidence 量纲不同却拿来比大小。"""
    for closes, band in [([100 + 0.001 * i for i in range(20)], 0.001),
                         (ramp(20, 1.006), 0.004),
                         (ramp(20, 0.994), 0.004),
                         ([100 + 5 * np.sin(i / 1.2) for i in range(20)], 0.012)]:
        r = classify(closes, band=band)
        if r.trend != TrendType.OTHER:
            assert 0.5 <= r.confidence <= 1.0, r


def test_other_has_zero_confidence():
    rng = np.random.default_rng(1)
    r = classify([100 + rng.normal(0, 3) for _ in range(20)], band=0.0001)
    if r.trend == TrendType.OTHER:
        assert r.confidence == 0.0


def test_confidence_grades_trend_quality():
    weak = classify(ramp(20, 1.001)).confidence
    strong = classify(ramp(20, 1.004)).confidence
    assert weak < strong


def test_min_confidence_is_a_meaningful_knob():
    px = ramp(20, 1.0015)
    assert classify(px, min_confidence=0.4).trend == TrendType.UPTREND
    assert classify(px, min_confidence=0.95).trend == TrendType.OTHER


# ── duration ────────────────────────────────────────────────────────────────

def test_duration_is_deterministic():
    """旧实现用二分搜索非单调谓词，结果是任意值。"""
    px = ramp(30, 0.99) + [100 * 0.99 ** 29 * (1.01 ** i) for i in range(1, 11)]
    bars = make_bars(px)
    vals = {detect_trend(bars, n=10).duration for _ in range(5)}
    assert len(vals) == 1


def test_duration_stops_when_the_fit_breaks():
    px = ramp(30, 0.99) + [100 * 0.99 ** 29 * (1.01 ** i) for i in range(1, 11)]
    r = detect_trend(make_bars(px), n=10)
    assert r.trend == TrendType.UPTREND
    assert r.duration < len(px)


def test_duration_is_capped_by_history_provided():
    px = ramp(40, 1.004)
    assert detect_trend(make_bars(px).iloc[-12:], n=10).duration <= 12


# ── ABNORMAL：换月与价格断裂 ────────────────────────────────────────────────

def test_steep_trend_stays_directional_and_is_not_abnormal():
    """旧实现把 EXTREME 强度改写成 ABNORMAL，吞掉了最该出信号的行情。"""
    r = classify(ramp(20, 1.02))
    assert r.trend == TrendType.UPTREND
    assert r.is_extreme and not r.is_abnormal


def test_stocks_never_get_roll_checks():
    """没有 hold 列 = 不是国内期货，自动不做换月检查。"""
    gappy = [100] * 10 + [130] * 10
    assert classify(gappy).trend != TrendType.ABNORMAL


def test_open_interest_jump_flags_abnormal():
    hold = [1e6] * 10 + [3e6] * 10
    r = detect_trend(make_bars([100 + i for i in range(20)], hold=hold),
                     n=20, compute_duration=False)
    assert r.trend == TrendType.ABNORMAL
    assert r.base_trend == TrendType.UPTREND
    assert r.direction == TrendType.UPTREND


def test_price_gap_without_oi_jump_is_not_a_roll():
    """涨跌停和节后缺口发生在同一份合约上，持仓量是连续的。"""
    gappy = [100] * 10 + [130] * 10
    r = detect_trend(make_bars(gappy, hold=[1e6] * 20), n=20, compute_duration=False)
    assert r.trend != TrendType.ABNORMAL


def test_abnormal_bars_limits_the_scan_window():
    hold = [1e6] * 10 + [3e6] * 10
    bars = make_bars([100 + i for i in range(20)], hold=hold)
    assert detect_trend(bars, n=20, compute_duration=False).trend == TrendType.ABNORMAL
    assert detect_trend(bars, n=20, abnormal_bars=2,
                        compute_duration=False).trend != TrendType.ABNORMAL


def test_explicit_roll_dates_override_everything():
    bars = make_bars([100 + i for i in range(20)])
    r = detect_trend(bars, n=20, roll_dates=[bars.index[1]], compute_duration=False)
    assert r.trend == TrendType.ABNORMAL


def test_roll_on_first_bar_of_window_is_not_abnormal():
    """首根即新合约第一根，窗口内 20 根全属新合约，是自洽的。"""
    bars = make_bars([100 + i for i in range(20)])
    r = detect_trend(bars, n=20, roll_dates=[bars.index[0]], compute_duration=False)
    assert r.trend != TrendType.ABNORMAL


# ── 真实数据 ────────────────────────────────────────────────────────────────

@pytest.mark.data
def test_trend_window_actually_changes_the_result(aapl):
    """旧实现 detect_trend(prior) 不传 n，永远只看默认的 10 根。"""
    def dist(n):
        out = {}
        for i in range(n, min(n + 300, len(aapl))):
            k = detect_trend(aapl.iloc[i - n:i], n=n, compute_duration=False).trend.name
            out[k] = out.get(k, 0) + 1
        return out
    assert dist(10) != dist(40)


@pytest.mark.data
def test_umbrella_never_emits_a_conflicting_signal(aapl):
    from signals.pattern import umbrella
    sig = umbrella(aapl, trend_window=10)
    assert set(sig.entry.unique()) <= {-1, 0, 1}
    assert not ((sig.entry != 0) & (sig.size < 0)).any()
