"""平台识别与突破信号：signals/platform.py。

算法本身有几处原始需求里模糊的地方，这里的测试同时也是"锁定最终敲定的
那种解释"的文档——具体取舍见 signals/platform.py 模块 docstring。

核心算法（倒序扫描 + "验证即更新" + 遇到显著突破就停）见 `_scan_backward`
自己的单元测试，这是最容易在重构里被悄悄破坏、也最难从端到端回测结果
反推出问题所在的一层，所以先在最小单元上锁死，再测端到端的 detect_platforms
/platform_breakout。
"""
import numpy as np
import pandas as pd
import pytest

from conftest import make_ohlc
from signals.platform import find_pivots, _scan_backward, detect_platforms, platform_breakout

_BAND = 0.006  # 日内振幅，约0.6%，贴近真实日线水平


def _rows(closes, band=_BAND):
    return [[c, c * (1 + band), c * (1 - band), c] for c in closes]


# ── find_pivots ─────────────────────────────────────────────────────────────

def test_find_pivots_detects_simple_peak_and_trough():
    high = pd.Series([10, 12, 15, 12, 10], index=pd.date_range("2024-01-01", periods=5))
    low = pd.Series([9, 11, 13, 11, 9], index=high.index)
    ph, pl = find_pivots(high, low, window=3)
    assert ph.tolist() == [False, False, True, False, False]
    assert not pl.any()   # 严格递减序列没有局域低点


def test_find_pivots_edges_are_excluded():
    high = pd.Series([100, 90, 80], index=pd.date_range("2024-01-01", periods=3))
    low = high - 1
    ph, pl = find_pivots(high, low, window=3)
    assert ph.iloc[0] == False and ph.iloc[-1] == False


def test_find_pivots_rejects_even_window():
    high = pd.Series([1, 2, 3])
    with pytest.raises(ValueError, match="奇数"):
        find_pivots(high, high, window=4)


# ── _scan_backward：倒序扫描核心状态机 ───────────────────────────────────────
#
# 起点 = 最近的 pivot。此后每个更早的 pivot 三选一：
#   落在容差内       -> 验证 = 更新（候选价挪到新价格，+1 次验证）
#   与候选价相差过大  -> 显著突破，停止扫描
#   中间地带         -> 噪音，忽略，继续往更早扫
#
# 三元组按时间**降序**传入（最近的在前），符合"从 T-1 往回走"的语义。
#
# 容差/突破阈值现在是**动态**的："平均每日波动率"取当前日(t)到对应日(每次
# 比较的那个历史 pivot)之间逐日 high-low 的均值，不是整个回看窗口算一次。
# 下面这些用例把 high-low 序列造成常数 `_D`，这样均值不管跨度多长都恒等于
# `_D`，等价于测一个"固定 tol/thr"的场景——先把状态机本身的分支逻辑测干净，
# 动态平均值的算法单独在 test_touch_tolerance_uses_the_tighter_or_bound 里测。

_D = 1.0   # 常数日振幅，_avg_range(j, t) 恒等于 _D，不随跨度变化
_CUM = np.arange(0, 10_000, dtype=float) * _D   # cum_range[i] = i * _D
_RNG = 1e9   # 大到 range 项永远不起作用，min() 恒选中 vol 项

# touch_vol_pct=0.5 -> tol = min(huge, 0.5*_D) = 0.5
# breakout_vol_pct=5.0 -> thr = min(huge, 5.0*_D) = 5.0
_TOL_KW = dict(cum_range=_CUM, rng=_RNG,
              touch_range_pct=1.0, touch_vol_pct=0.5,
              breakout_range_pct=1.0, breakout_vol_pct=5.0)


def test_scan_counts_repeated_validations_near_the_same_level():
    idx = np.array([20, 16, 12, 8])
    price = np.array([100.0, 99.9, 100.1, 99.8])
    c, v, _ = _scan_backward(idx, price, t=24, min_gap_bars=3, **_TOL_KW)
    assert v == 3
    assert c == 99.8   # 候选价落在最后一次（最早那次）验证的价格上，不是全局极值


def test_scan_stops_at_a_significant_breakout():
    """扫到显著偏离（超过突破阈值）的价格，说明找到了平台源头，就此打住。"""
    idx = np.array([20, 16, 12, 8, 4])
    price = np.array([100.0, 99.9, 100.1, 85.0, 70.0])   # 85 相差 15 > thr(5.0)
    c, v, _ = _scan_backward(idx, price, t=24, min_gap_bars=3, **_TOL_KW)
    assert v == 2 and c == 100.1   # 85 之前的两次验证保留，85 本身和更早的 70 都不再看


def test_scan_ignores_the_middle_zone_without_stopping():
    """超过验证容差、但没到突破幅度——当噪音跳过，不终止扫描。"""
    idx = np.array([20, 16, 12, 8])
    price = np.array([100.0, 97.0, 99.9, 99.7])   # 97: diff=3，在 0.5(tol) 和 5(thr) 之间
    c, v, _ = _scan_backward(idx, price, t=24, min_gap_bars=3, **_TOL_KW)
    assert v == 2 and c == 99.7   # 97 被跳过，继续验证了 99.9 和 99.7 两次


def test_scan_respects_minimum_gap_between_validations():
    """间隔不够 min_gap_bars 的触及，既不算验证也不更新候选价。"""
    idx = np.array([20, 19, 12])   # 20 和 19 只差 1 天，不够 min_gap=3
    price = np.array([100.0, 99.9, 99.8])
    c, v, _ = _scan_backward(idx, price, t=24, min_gap_bars=3, **_TOL_KW)
    assert v == 1 and c == 99.8   # 99.9 被跳过（间隔不够），99.8 正常验证


def test_scan_on_empty_pivots_returns_no_platform():
    c, v, last = _scan_backward(np.array([]), np.array([]), t=24, min_gap_bars=3, **_TOL_KW)
    assert v == 0 and np.isnan(c) and last == -1


def test_scan_with_only_the_starting_pivot_has_zero_validations():
    """只有起点、没有后续 pivot 可比较——候选价就是起点本身，但验证数为 0。"""
    c, v, last = _scan_backward(np.array([20]), np.array([100.0]), t=24, min_gap_bars=3, **_TOL_KW)
    assert v == 0 and c == 100.0 and last == 20


# ── detect_platforms：端到端整合 ─────────────────────────────────────────────

def test_platform_requires_at_least_one_validation():
    """只有一个孤零零的高点、没有第二次测试——不构成平台。"""
    closes = [90, 92, 95, 98.5, 96, 93, 90] + [90 + i * 0.3 for i in range(1, 20)]
    df = make_ohlc(_rows(closes))
    info = detect_platforms(df)
    assert (info.high_validations.fillna(0) == 0).all()


def test_platform_settles_on_the_most_recently_validated_price_not_a_global_extreme():
    """
    平台源头在 85（一路涨到 100 附近后开始震荡测试），中途测试了三次
    （99.8、100.1、99.9，彼此都在容差内）——报告的平台价应该落在其中
    某次验证值上，而不是这组数据里从未出现过的"全局最高"之类的数字。
    """
    closes = [85, 88, 92, 96, 100,
             97, 99.8, 96, 94,
             95, 100.1, 94, 92,
             93, 99.9, 95, 90]
    t = len(closes) - 1
    closes += [90 + i for i in range(1, 25)]
    df = make_ohlc(_rows(closes))

    info = detect_platforms(df)
    assert info.high_validations.iloc[t] >= 1
    assert 99.0 <= info.high_level.iloc[t] <= 101.0


def test_platform_absent_beyond_lookback_window():
    """
    看不到震荡历史（lookback 太短）就看不到平台，即使正常窗口能验证出来。
    两次测试之间刻意拉开到 7 根 bar，这样约一周的 lookback 连上一次测试都
    看不到（而不是恰好还在窗口内，造成假阴性）。
    """
    closes = [85, 88, 92, 96, 100,           # 建立平台源头
             97, 94, 91, 88, 91, 94, 99.8,   # 第一次测试
             96, 93, 90, 87, 90, 93, 100.1]  # 第二次测试
    t = len(closes) - 1
    closes += [90 + i for i in range(1, 25)]
    df = make_ohlc(_rows(closes))

    info_normal = detect_platforms(df, lookback_years=3)
    info_tiny = detect_platforms(df, lookback_years=0.02)   # 约一周，看不到上一次测试
    assert info_normal.high_validations.iloc[t] >= 1
    assert info_tiny.high_validations.iloc[t] == 0


def test_volatility_term_uses_today_to_the_specific_pivot_not_the_whole_window():
    """
    "平均每日波动率"是**动态**的：每次比较用"当前日到对应日"这一段的均值，
    不是对整个回看窗口只算一次然后全程复用。

    构造：早期 12 天剧烈震荡（high-low=10/bar），近期 8 天平静（high-low≈0.2/bar），
    近期恰好有两个 pivot（100.1 和 102.1，相差 2.0）。用整窗均值算容差
    （≈6.39*0.5=3.2）宽到能验证这两次；用"今天到对应日"算（两个 pivot 都在
    近期平静段内，均值≈0.2*0.5=0.1）窄到根本验证不出来。这个差异只有在
    "对应日"随扫描推进动态变化时才会体现——如果退回成对整个窗口只算一次，
    这条测试会验证出 1 次而不是 0 次。
    """
    early = [100, 110, 90, 108, 92, 106, 94, 104, 96, 102, 98, 100]
    rows = [[c, c + 5, c - 5, c] for c in early]
    late = [95, 98, 100.0, 96, 94, 98, 102.0, 96]   # 两个 pivot: 100.1、102.1（含band）
    rows += [[c, c + 0.1, c - 0.1, c] for c in late]
    df = make_ohlc(rows)

    info = detect_platforms(df, touch_range_pct=1.0, touch_vol_pct=0.5,
                            breakout_range_pct=1.0, breakout_vol_pct=100.0,
                            lookback_years=3)
    t = len(df) - 1
    assert info.high_validations.iloc[t] == 0, (
        "近期两次测试相差 2.0，用整窗均值算的容差(≈3.2)会验证出来，"
        "但用'今天到对应日'算的容差(≈0.1)不该验证出来——如果这里冒出 "
        "1 次验证，说明动态窗口退化成了整窗均值。"
    )


def test_touch_tolerance_uses_the_tighter_or_bound():
    """
    容差 = min(range_pct*range, vol_pct*avg_range)，不是 max。

    构造两次测试，价差刚好落在两个阈值之间（0.22 < diff=1.0 < 1.61）：
    用 min 组合验证不出来，用 max 组合才能验证出来——直接钉住这个选择，
    不能只在 _scan_backward 的单元测试里传字面量 tol，那样测不出
    _platform_state 里到底用的是 min 还是 max。
    """
    closes = [85, 88, 92, 96, 100,
             97, 94, 91, 88, 91, 94, 99.0,   # 第一次测试 99.0
             96, 93, 90, 87, 90, 93, 100.0]  # 第二次测试 100.0，与 99.0 相差 1.0
    t = len(closes) - 1
    closes += [90 + i for i in range(1, 25)]
    df = make_ohlc(_rows(closes))

    info = detect_platforms(df)
    assert info.high_validations.iloc[t] == 0, (
        "价差 1.0 大于 vol_pct*avg_range≈0.22（更紧的那个），"
        "min 组合下不该验证出平台"
    )


def test_lookback_years_accepts_fractional_values():
    """旧 bug：pd.DateOffset(years=...) 不支持小数年，传 2.5 直接崩溃。"""
    closes = [90 + i * 0.3 for i in range(30)]
    df = make_ohlc(_rows(closes))
    detect_platforms(df, lookback_years=2.5)   # 不应该抛异常


def test_min_validations_filters_out_weakly_tested_platforms():
    """
    起点本身不算验证，`min_validations=1`（默认）意味着"至少被触及过 2 次"；
    `min_validations=2` 意味着"至少 3 次"。用 `_platform_then_breakout()`
    数据里已知只验证过 1 次的那几根 bar，确认提高门槛后这个平台不再成立。
    """
    df = _platform_then_breakout()
    info1 = detect_platforms(df, min_validations=1)
    val1_days = info1.high_validations[info1.high_validations == 1]
    assert len(val1_days) > 0, "数据集本身应该存在恰好验证 1 次的 bar"
    t = val1_days.index[0]

    assert info1.high_validations.loc[t] == 1
    assert not np.isnan(info1.high_level.loc[t])

    info2 = detect_platforms(df, min_validations=2)
    assert info2.high_validations.loc[t] == 0
    assert np.isnan(info2.high_level.loc[t])


def test_min_validations_rejects_less_than_one():
    df = make_ohlc(_rows([90 + i * 0.3 for i in range(20)]))
    with pytest.raises(ValueError, match="min_validations"):
        detect_platforms(df, min_validations=0)


# ── platform_breakout：突破信号 + 止损价 + 验证次数冻结 ─────────────────────

def _platform_then_breakout():
    closes = [85, 88, 92, 96, 100,
             97, 99.8, 96, 94,
             95, 100.1, 94, 92,
             93, 99.9, 95, 90]
    closes += [90 + i for i in range(1, 25)]   # 后段涨破平台
    return make_ohlc(_rows(closes))


def test_breakout_fires_only_after_clearing_threshold():
    df = _platform_then_breakout()
    sig = platform_breakout(df)
    fired = sig.entry[sig.entry != 0]
    assert len(fired) > 0
    assert (fired == 1).all()
    for t in fired.index:
        assert sig.validations.loc[t] >= 1
        assert not np.isnan(sig.stop_price.loc[t])
        assert sig.stop_price.loc[t] < df["close"].loc[t]   # 多头止损价在下方


def test_breakout_direction_matches_platform_side():
    """做空方向：跌破支撑位，entry 应该是 -1，止损价在上方。"""
    df = _platform_then_breakout()
    mirrored = 190 - df["close"]
    band = _BAND
    df2 = make_ohlc([[c, c * (1 + band), c * (1 - band), c] for c in mirrored])

    sig = platform_breakout(df2)
    fired = sig.entry[sig.entry != 0]
    assert len(fired) > 0
    assert (fired == -1).all()
    for t in fired.index:
        assert sig.stop_price.loc[t] > df2["close"].loc[t]


def test_no_platform_means_no_entry():
    """纯粹的单边上涨（没有反复测试的高点）——不该有任何突破信号。"""
    closes = [90 + i * 0.5 for i in range(60)]
    df = make_ohlc(_rows(closes))
    sig = platform_breakout(df)
    assert (sig.entry == 0).all()


def test_entry_stays_true_while_condition_holds_not_a_one_shot_pulse():
    """
    PlatformSignal.entry 的语义和 signals.base.Signal 不同：只要收盘价还在
    突破阈值外就持续非 0，不是只在穿越那一根 bar 触发一次。
    """
    df = _platform_then_breakout()
    sig = platform_breakout(df)
    fired = sig.entry[sig.entry != 0]
    assert len(fired) > 1


# ── 放量确认 ─────────────────────────────────────────────────────────────────
#
# 只用收盘时点能拿到的数据（收盘价 + 收盘成交量），均量基准不含当日——当日
# 成交量本来就是要检验的对象，算进自己的基准里会稀释比较。

def test_volume_mult_none_disables_the_filter_by_default():
    """默认关闭，行为和不传这个参数完全一样（向后兼容）。"""
    df = _platform_then_breakout()
    df = df.copy()
    df["volume"] = 1e6
    sig_default = platform_breakout(df)
    sig_explicit_none = platform_breakout(df, volume_mult=None)
    assert sig_default.entry.equals(sig_explicit_none.entry)


def test_volume_mult_blocks_breakout_without_a_volume_surge():
    df = _platform_then_breakout()
    df = df.copy()
    df["volume"] = 1e6   # 恒定量，永远不算放量
    sig = platform_breakout(df, volume_mult=1.5)
    assert (sig.entry == 0).all()


def test_volume_mult_fires_on_the_day_volume_actually_surges():
    df = _platform_then_breakout()
    df = df.copy()
    df["volume"] = 1e6
    sig_no_vol = platform_breakout(df)
    t = sig_no_vol.entry[sig_no_vol.entry != 0].index[0]   # 价格早就满足突破的那天

    df.loc[t, "volume"] = 3e6   # 远超均量基准，随便怎么算基准都该放行
    sig_vol = platform_breakout(df, volume_mult=1.5)
    assert sig_vol.entry.loc[t] == sig_no_vol.entry.loc[t]


def test_volume_baseline_excludes_the_breakout_day_itself():
    """
    均量基准不该把当日算进去，否则当日的大成交量会抬高自己的比较基准，
    需要更极端的放量才能触发。

    构造一个边界值：V = 1.52×基准。不含当日算基准 -> V/基准=1.52>=1.5，
    应该触发；含当日一起算基准（20 天窗口，19 天在基准值+当日 V）->
    V/新基准≈1.481<1.5，反而不该触发。这个差距只有基准算法真的把
    当日排除在外才能测出来——量放得特别大（比如 3 倍）时怎么算基准
    结果都一样，测不出这个 bug。
    """
    df = _platform_then_breakout()
    df = df.copy()
    df["volume"] = 1e6
    sig_no_vol = platform_breakout(df)
    t = sig_no_vol.entry[sig_no_vol.entry != 0].index[0]

    df.loc[t, "volume"] = 1_520_000
    sig = platform_breakout(df, volume_mult=1.5, volume_window=20)
    assert sig.entry.loc[t] == sig_no_vol.entry.loc[t], (
        "V=1.52×基准应该刚好达标——如果基准把当日算进去了，"
        "比较基准被自己抬高，算出来会差一点点不达标"
    )


def test_volume_mult_requires_volume_column():
    df = _platform_then_breakout().drop(columns=["volume"])
    with pytest.raises(ValueError, match="volume"):
        platform_breakout(df, volume_mult=1.5)
