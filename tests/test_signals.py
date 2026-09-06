"""形态识别：单根/双根 K 线特征，以及 umbrella/engulfing 信号。

这是策略最可能直接复用或照着写的一层，之前完全没有用合成数据构造已知形态的
直接单测——只有一条依赖真实 AAPL 缓存、只检查取值合法性的间接测试
（见 test_trend.py::test_umbrella_never_emits_a_conflicting_signal）。
这里补上确定性的、不需要网络/缓存的版本，覆盖具体的判定边界。
"""
import numpy as np
import pytest

from conftest import make_ohlc, ramp
from signals.feature import is_umbrella, is_doji, is_engulfing
from signals.pattern import umbrella, engulfing


# ── is_umbrella ─────────────────────────────────────────────────────────────

def test_is_umbrella_detects_long_lower_shadow_small_upper():
    # body=2, lower_shadow=5(>=2*2), upper_shadow=0.1(<=2*0.1)
    df = make_ohlc([[100, 102.1, 95, 102]])
    assert is_umbrella(df).iloc[0]


def test_is_umbrella_rejects_short_lower_shadow():
    # body=3, lower_shadow=2 < 3*2=6 -> 不是伞形线
    df = make_ohlc([[100, 105, 98, 103]])
    assert not is_umbrella(df).iloc[0]


def test_is_umbrella_rejects_long_upper_shadow():
    # lower_shadow 够长，但 upper_shadow=3 > body(2)*0.1=0.2
    df = make_ohlc([[100, 105, 95, 102]])
    assert not is_umbrella(df).iloc[0]


def test_is_umbrella_zero_body_needs_zero_upper_shadow():
    """body=0（十字星）时 lower_shadow>=0 恒成立，全靠 upper_shadow<=0 判定。"""
    df = make_ohlc([[100, 100, 90, 100]])   # high==max(open,close) -> upper_shadow=0
    assert is_umbrella(df).iloc[0]
    df2 = make_ohlc([[100, 100.5, 90, 100]])   # upper_shadow=0.5>0
    assert not is_umbrella(df2).iloc[0]


# ── is_doji ─────────────────────────────────────────────────────────────────

def test_is_doji_detects_tiny_body():
    df = make_ohlc([[100, 105, 95, 100.05]])   # body=0.05, range=10, ratio=0.1*10=1.0
    assert is_doji(df).iloc[0]


def test_is_doji_rejects_large_body():
    df = make_ohlc([[100, 105, 95, 103]])   # body=3 > 1.0
    assert not is_doji(df).iloc[0]


def test_is_doji_on_zero_range_bar():
    """全平的 bar（open=high=low=close）：0 <= 0，按 doji 处理，不应崩溃。"""
    df = make_ohlc([[100, 100, 100, 100]])
    assert is_doji(df).iloc[0]


# ── is_engulfing ────────────────────────────────────────────────────────────

def test_is_engulfing_bullish_covers_prior_bearish():
    # bar0 bearish body[100,105]; bar1 bullish covering [99,107]
    df = make_ohlc([[105, 106, 99, 100], [99, 107, 98, 107]])
    r = is_engulfing(df)
    assert not r.iloc[0]   # 首根总是 False（没有前一根）
    assert r.iloc[1]


def test_is_engulfing_requires_full_coverage():
    # bar1 实体 [101,104]，没有覆盖 bar0 实体 [100,105]（104<105）
    df = make_ohlc([[105, 106, 99, 100], [101, 104.5, 99, 104]])
    assert not is_engulfing(df).iloc[1]


def test_is_engulfing_same_color_needs_prior_doji():
    """颜色相同本不该触发，但如果前一根是十字星就放行（十字星没有方向）。"""
    # bar0 十字星（body=0.05, range=2）, bar1 与 bar0 同为阳线但完全覆盖
    df = make_ohlc([[100, 101, 99, 100.05], [95, 110, 94, 108]])
    assert is_engulfing(df).iloc[1]

    # 同样的覆盖关系，但 bar0 不是十字星（body 够大）-> 同色不触发
    df2 = make_ohlc([[100, 106, 99, 105], [95, 110, 94, 108]])
    assert not is_engulfing(df2).iloc[1]


# ── umbrella()：需要趋势上下文 ───────────────────────────────────────────────

def _downtrend_rows(n=5, rate=0.97):
    return [[c, c * 1.002, c * 0.998, c] for c in ramp(n, rate)]


def _uptrend_rows(n=5, rate=1.03):
    return [[c, c * 1.002, c * 0.998, c] for c in ramp(n, rate)]


def test_umbrella_hammer_in_downtrend_fires_immediately():
    rows = _downtrend_rows() + [[85, 86.05, 75, 86]]   # 锤子线
    sig = umbrella(make_ohlc(rows), trend_window=5)
    assert sig.entry.tolist() == [0, 0, 0, 0, 0, 1]
    assert 0 < sig.size.iloc[5] <= 1


def test_umbrella_hanging_man_needs_next_bar_confirmation():
    rows = _uptrend_rows() + [
        [120, 121.05, 110, 121],   # 上吊线形态
        [121, 122, 114, 115],      # 确认：收盘 115 < 形态实体下沿 120
    ]
    sig = umbrella(make_ohlc(rows), trend_window=5)
    assert sig.entry.tolist() == [0, 0, 0, 0, 0, 0, -1]
    assert 0 < sig.size.iloc[6] <= 1


def test_umbrella_hanging_man_without_confirmation_emits_nothing():
    rows = _uptrend_rows() + [
        [120, 121.05, 110, 121],   # 上吊线形态
        [121, 125, 119, 124],      # 不确认：收盘 124 > 120
    ]
    sig = umbrella(make_ohlc(rows), trend_window=5)
    assert (sig.entry == 0).all()


def test_umbrella_shape_without_trend_context_emits_nothing():
    """形态成立但趋势是震荡/横盘（既非上涨也非下跌）——不该触发。"""
    flat_rows = [[100, 100.4, 99.6, 100]] * 5   # 横盘
    rows = flat_rows + [[85, 86.05, 75, 86]]     # 同一根锤子线形态
    sig = umbrella(make_ohlc(rows), trend_window=5)
    assert (sig.entry == 0).all()


def test_umbrella_respects_trend_window_larger_than_detect_trend_default():
    """
    旧 bug：signals/pattern.py 调用 detect_trend(prior) 时没传 n，
    永远用 detect_trend 的默认值 10，trend_window 参数形同虚设。

    注意：trend_window <= 10 时这个 bug 观察不到——prior 已经被上层精确
    切成 trend_window 根，detect_trend 内部的 n 只能"缩短"不能"变长"，
    所以 trend_window=5/6 这种小窗口不管传不传 n 结果都一样（都不超过
    detect_trend 默认的 10 根）。要让 bug 现出原形，trend_window 必须
    > 10：前 10 根做出一段陡降建立 DOWNTREND，后 10 根几乎走平（斜率不够
    detect_trend 的门槛，单独看会判成 FLAT/OTHER）。trend_window=20 正确
    传给 detect_trend 时用满 20 根 -> DOWNTREND；如果 bug 复现（悄悄退回
    默认的 10 根，只看到后段的走平）-> FLAT，形态不触发。
    """
    steep = ramp(10, 0.95)                        # 前 10 根：陡降建立趋势
    flat_tail = ramp(10, 0.9995, start=steep[-1])  # 后 10 根：几乎走平
    rows = [[c, c * 1.001, c * 0.999, c] for c in steep + flat_tail]
    low = (steep + flat_tail)[-1]
    rows.append([low, low + 1.05, low - 10, low + 1])   # 锤子线
    df = make_ohlc(rows)

    sig = umbrella(df, trend_window=20)
    assert sig.entry.iloc[20] == 1, (
        "trend_window=20 时应该正确传给 detect_trend(n=20)，"
        "整段判为 DOWNTREND，锤子线触发；如果又退回默认 n=10，"
        "只看到走平的尾段，形态就不会触发。"
    )


# ── engulfing()：需要趋势上下文 ──────────────────────────────────────────────

def test_engulfing_bullish_in_downtrend_fires_immediately():
    rows = _downtrend_rows() + [
        [84, 84.5, 82, 83],     # 前一根，小阴线
        [82.5, 91, 82, 90],     # 看涨吞噬
    ]
    sig = engulfing(make_ohlc(rows), trend_window=6)
    assert sig.entry.tolist() == [0, 0, 0, 0, 0, 0, 1]
    assert 0 < sig.size.iloc[6] <= 1


def test_engulfing_bearish_in_uptrend_fires_immediately():
    rows = _uptrend_rows() + [
        [116, 118, 115, 117],     # 前一根，小阳线
        [118.5, 119, 108, 109],   # 看跌吞噬
    ]
    sig = engulfing(make_ohlc(rows), trend_window=6)
    assert sig.entry.tolist() == [0, 0, 0, 0, 0, 0, -1]


def test_engulfing_wrong_color_for_trend_emits_nothing():
    """下跌趋势中出现的是看跌吞噬（颜色对不上"反转"方向）——不是本策略要的信号。"""
    rows = _downtrend_rows() + [
        [84, 84.5, 79, 83],     # 前一根
        [83.5, 84, 74, 75],     # 看跌吞噬，覆盖前一根
    ]
    sig = engulfing(make_ohlc(rows), trend_window=6)
    assert (sig.entry == 0).all()


# ── 不变量：随机构造的长序列上不应出现非法值 ─────────────────────────────────

def _random_ohlc(n, seed):
    """带随机游走 + 偶发大波动的合成行情，用于压力测试不变量而非具体数值。"""
    rng = np.random.default_rng(seed)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.015, n))
    opens = closes * (1 + rng.normal(0, 0.005, n))
    spread = np.abs(rng.normal(0, 0.01, n)) + 0.001
    highs = np.maximum(opens, closes) * (1 + spread)
    lows = np.minimum(opens, closes) * (1 - spread)
    rows = list(zip(opens, highs, lows, closes))
    volume = rng.integers(1000, 100_000, n).astype(float)
    df = make_ohlc(rows)
    df["volume"] = volume
    return df


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_umbrella_never_produces_illegal_values_on_synthetic_data(seed):
    df = _random_ohlc(250, seed)
    sig = umbrella(df, trend_window=10)
    assert set(sig.entry.unique()) <= {-1, 0, 1}
    assert not ((sig.entry != 0) & (sig.size <= 0)).any()
    assert not ((sig.entry == 0) & (sig.size != 0)).any()


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_engulfing_never_produces_illegal_values_on_synthetic_data(seed):
    df = _random_ohlc(250, seed)
    sig = engulfing(df, trend_window=10)
    assert set(sig.entry.unique()) <= {-1, 0, 1}
    assert not ((sig.entry != 0) & (sig.size <= 0)).any()
    assert not ((sig.entry == 0) & (sig.size != 0)).any()
