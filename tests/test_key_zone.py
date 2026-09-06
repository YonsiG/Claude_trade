"""关键区突破信号：signals/key_zone.py（platform.py 的对照组）。

用完全显式、逐根手写的 OHLC 构造场景——这个模块的正确性高度依赖"在正确的
时间点看到正确的信息"，不能用随机数据糊弄过去。
"""
import numpy as np
import pandas as pd
import pytest

from conftest import make_ohlc
from signals.key_zone import (compute_atr, find_strict_pivots,
                              detect_key_zones, key_zone_breakout)


def _stable_base(n=18, level=100.0, band=0.5):
    """让 ATR(14) 收敛到 2*band 的稳定基础段。"""
    return [[level, level + band, level - band, level] for _ in range(n)]


# ── ATR / find_strict_pivots ────────────────────────────────────────────────

def test_atr_matches_hand_calculation():
    rows = [[100, 102, 98, 100], [100, 103, 99, 101], [101, 104, 100, 102]]
    df = make_ohlc(rows)
    tr1 = max(103 - 99, abs(103 - 100), abs(99 - 100))
    tr2 = max(104 - 100, abs(104 - 101), abs(100 - 101))
    atr = compute_atr(df, period=2)
    assert atr.iloc[2] == pytest.approx((tr1 + tr2) / 2)


def test_strict_pivot_requires_strict_inequality_both_sides():
    high = pd.Series([10, 12, 15, 12, 10, 9, 20, 9, 8],
                     index=pd.date_range("2024-01-01", periods=9))
    low = high - 1
    ph, _ = find_strict_pivots(high, low, k=2)
    assert ph.tolist() == [False, False, True, False, False, False, True, False, False]


def test_strict_pivot_rejects_a_tie():
    """3 日 pivot（platform.py）允许打平；这里要求严格不等号，打平不算。"""
    high = pd.Series([10, 12, 15, 15, 10, 9], index=pd.date_range("2024-01-01", periods=6))
    low = high - 1
    ph, _ = find_strict_pivots(high, low, k=2)
    assert not ph.iloc[2] and not ph.iloc[3]


# ── 核心状态机：不能用未来数据 ───────────────────────────────────────────────

def test_zone_is_not_usable_before_its_confirmation_bar():
    """
    局域高点要等右侧 local_window 根走完才能判定——只喂到右侧邻居走完之前
    的历史，`find_strict_pivots` 本身就没有足够的未来数据去判定 idx18 是不是
    局域高点，遑论用它建区。这条测的是"流式安全"的边界情况：数据不够时，
    连"是不是局域高点"这件事本身都判定不出来（不是判定出来了但延迟生效）。
    """
    rows = _stable_base()
    rows.append([104.5, 105, 104, 104.8])   # idx18: 候选局域高点 K=105
    rows.append([100, 100.5, 99.5, 100])    # idx19: 右邻居1
    rows.append([100, 100.5, 99.5, 100])    # idx20: 右邻居2 -> idx18 在这根 bar 走完后确认
    rows.append([100, 100.5, 99.5, 100])    # idx21
    df = make_ohlc(rows)

    # 在 idx19（右邻居1还没走完）时，用截止 idx19 的历史看，区间不该存在
    zones_early = detect_key_zones(df.iloc[:20])   # 只喂到 idx19（不含 idx20）
    assert len(zones_early) == 0, "确认延迟没生效，提前用到了未来的右侧 K 线"

    zones_full = detect_key_zones(df)
    assert any(z.established_idx == 18 for z in zones_full), "确认延迟之后应该正常识别"


def test_local_window_controls_the_confirmation_lag():
    """local_window 越大，确认延迟越长——直接验证这个参数真的在起作用。"""
    rows = _stable_base()
    rows.append([104.5, 105, 104, 104.8])   # idx18
    rows += [[100, 100.5, 99.5, 100]] * 5   # idx19-23
    df = make_ohlc(rows)

    zones_k1 = detect_key_zones(df.iloc[:20], local_window=1)   # 到 idx19，k=1 应该已确认
    zones_k3 = detect_key_zones(df.iloc[:20], local_window=3)   # k=3 需要到 idx21 才确认
    assert len(zones_k1) == 1
    assert len(zones_k3) == 0


# 下面这段固定数值取自一次随机游走的真实分叉案例（np.random.seed(0) 生成后
# 手动定位出的最小可复现窗口）——状态机内 `conf_idx = t - local_window` 这一行
# 本身不改变"哪个 bar 是局域高点"（find_strict_pivots 已经整段算好），但决定
# 了"这个触碰被处理的时刻"落在成立区间的 left_since_last_touch 标志被当天
# high/low 更新*之前*还是*之后*。手写场景很难精确构造出这个时序分叉（两个
# 独立的局域高点靠得越近，pivot 自身的严格性越容易互相打架），所以改用这段
# 已验证会分叉的真实数值——用固定随机种子生成后原样写死，不依赖运行时的
# numpy 状态，可重复。
_TIMING_SPLIT_ROWS = [
    [122.8923, 124.0681, 121.7322, 122.4052], [122.8408, 123.579, 123.0815, 123.1973],
    [122.2224, 124.4054, 121.7447, 122.2004], [122.1691, 123.2471, 121.885, 122.2471],
    [121.0369, 121.3518, 120.8739, 121.3143], [121.9733, 123.92, 121.9045, 122.299],
    [123.4912, 124.1161, 122.2409, 123.1452], [122.6437, 124.2838, 122.5893, 122.8374],
    [123.5216, 125.0485, 122.6112, 123.4212], [121.5094, 121.9623, 120.4771, 121.8023],
    [119.3904, 120.1655, 119.5361, 119.6226], [120.4237, 120.4676, 118.9377, 120.2533],
    [120.8302, 120.8346, 119.4967, 120.4939], [121.3482, 122.9822, 120.4445, 121.4121],
    [124.3515, 125.1758, 123.6674, 124.8842], [127.0566, 127.069, 125.9915, 126.2996],
    [124.5951, 125.7735, 123.8879, 124.9161], [126.0051, 127.7461, 126.5173, 126.5905],
    [125.5613, 126.0533, 123.9929, 124.5915], [123.7513, 124.7465, 122.3628, 123.9014],
    [123.2571, 124.3929, 123.5158, 123.8], [126.0863, 127.4732, 123.9997, 126.3453],
    [125.3542, 125.9841, 124.8984, 125.2162], [124.0451, 124.3277, 123.4586, 123.9744],
    [123.5115, 125.5798, 123.6044, 123.8279], [123.5137, 123.1913, 122.4001, 122.842],
    [124.308, 125.3141, 124.4358, 124.5028], [122.4526, 122.9473, 121.5932, 122.8893],
    [121.5465, 121.3766, 120.8378, 121.1972], [121.0411, 121.3394, 119.6484, 120.5604],
    [119.546, 121.2267, 118.9457, 119.8399], [122.0993, 123.3998, 121.3758, 122.6147],
    [124.3951, 124.3184, 123.5628, 124.0117], [123.3795, 124.3738, 124.0448, 124.142],
    [121.8663, 124.012, 121.8779, 122.3164], [123.9144, 124.2324, 122.914, 123.5558],
    [122.0525, 122.435, 122.0499, 122.0728], [119.7151, 119.9461, 118.7755, 119.8099],
    [121.6466, 122.9956, 119.5696, 121.518], [121.9242, 123.6582, 121.6124, 121.9801],
    [122.8476, 124.2451, 122.7896, 123.3281], [124.2737, 124.3764, 121.9333, 123.7998],
    [125.5698, 127.1685, 123.1258, 125.0727], [124.172, 124.2184, 123.1894, 124.0956],
    [122.572, 122.683, 122.3402, 122.5554], [124.425, 123.6507, 123.1693, 123.5578],
]


def test_touch_timing_does_not_leak_same_bar_updates_across_the_confirmation_delay():
    """
    idx15 建区（阻力区），idx21 是它的第 2 次触碰，idx42 是另一个独立局域高点。
    正确实现下，idx42 因为落在 idx15 这个区间仍然有效的价格范围内、且已经
    间隔足够、也确实"离开过又回来"，本该被判定为对 idx15 这个区间的第 3 次
    触碰——但真正的分叉点在于：如果状态机在处理 idx42 这次触碰时，把"当天
    (idx42 自己) high/low 对 left_since_last_touch 的更新"错误地提前生效
    （即在触碰判定之前就已经应用），会导致触碰判定用错时序基准，让 idx42
    本该形成的独立新区间被错误地合并/吞并进 idx15 那个区间——总触碰次数
    和区间数量都会变，属于典型的"处理顺序错了，结果跟着错"。
    """
    df = make_ohlc(_TIMING_SPLIT_ROWS)
    zones = detect_key_zones(df, local_window=1, required_touches=1)

    z15 = [z for z in zones if z.direction == 1 and z.established_idx == 15]
    z42 = [z for z in zones if z.direction == 1 and z.established_idx == 42]
    assert len(z15) == 1 and z15[0].touch_count == 2, \
        "idx15 建立的区间应该恰好有 2 次触碰（建区 + idx21），不多不少"
    assert len(z42) == 1, "idx42 应该形成一个独立的新区间，而不是被并入 idx15"


# ── 两次触碰之间的最小间隔 ───────────────────────────────────────────────────

def _min_gap_scenario(bridge_bars):
    """
    idx20 建区，idx23 跌破触发"已离开"，之后经过 `bridge_bars` 根过渡 bar
    再出现第二个候选局域高点——用来控制它和上一次触碰之间的间隔天数，
    验证 `min_gap_bars`（默认 5）是不是真的在拦人，而不是摆设。
    """
    rows = _stable_base()
    rows.append([100, 100.5, 99.5, 100])       # idx18 左邻居
    rows.append([100, 100.5, 99.5, 100])       # idx19 左邻居
    rows.append([104.5, 105, 104, 104.8])      # idx20: 建区 K=105
    rows.append([104.2, 104.3, 104.0, 104.1])  # idx21 右邻居
    rows.append([104.2, 104.3, 104.0, 104.1])  # idx22 右邻居
    rows.append([95, 95.5, 90, 95])            # idx23: 跌破，触发"已离开"
    rows += [[95, 95.5, 95.0, 95.2]] * bridge_bars  # 过渡 bar，控制间隔天数
    rows.append([104.5, 105, 104, 104.8])      # 候选第二次触碰 K=105
    rows.append([104.2, 104.3, 104.0, 104.1])  # 右邻居
    rows.append([104.2, 104.3, 104.0, 104.1])  # 右邻居
    rows += [[104.0, 104.3, 103.8, 104.0]] * 10
    return make_ohlc(rows)


def test_touch_within_min_gap_bars_is_rejected():
    """第二次触碰确认时距上次触碰只隔 4 根 bar（< min_gap_bars=5）——不算数。"""
    df = _min_gap_scenario(bridge_bars=0)   # established_idx=20，第二个候选在 idx24，间隔 4
    zones = detect_key_zones(df, required_touches=3)
    resistance = [z for z in zones if z.direction == 1 and z.established_idx == 20][0]
    assert resistance.touch_count == 1, "间隔不够 5 根 bar，不该计入第二次验证"


def test_touch_at_exactly_min_gap_bars_is_accepted():
    """间隔恰好 5 根 bar——达到门槛，应该计入。"""
    df = _min_gap_scenario(bridge_bars=1)   # 第二个候选在 idx25，间隔 5
    zones = detect_key_zones(df, required_touches=3)
    resistance = [z for z in zones if z.direction == 1 and z.established_idx == 20][0]
    assert resistance.touch_count == 2, "间隔恰好达到 min_gap_bars，应该计入第二次验证"


# ── 独立验证：必须离开再回来 ─────────────────────────────────────────────────

def _independent_touch_scenario(dip_low):
    """
    建一个阻力区（idx18），中间一段横盘在区间内（不该算验证），
    然后跌到 `dip_low`，再回升到第二次触碰（idx30 附近）。
    `dip_low` 传得够低时应该触发"已离开"，传得不够低则不该触发。
    """
    rows = _stable_base()
    rows.append([100, 100.5, 99.5, 100])       # idx18 左邻居
    rows.append([100, 100.5, 99.5, 100])       # idx19 左邻居
    rows.append([104.5, 105, 104, 104.8])      # idx20: 局域高点 K=105
    # idx21/22 只需要 high < 105（确认 idx20 是局域高点），low 必须留在区间
    # 附近——否则这两根“确认用”的邻居 bar 会在区间刚建立时就自己触发一次
    # "已离开"，把 dip_low 的对照意义抵消掉。
    rows.append([104.2, 104.3, 104.0, 104.1])  # idx21 右邻居
    rows.append([104.2, 104.3, 104.0, 104.1])  # idx22 右邻居
    rows += [[104.8, 105.0, 104.7, 104.9]] * 4  # idx23-26: 横盘在区间内
    rows.append([104.0, 104.2, dip_low, 104.0])  # idx27: 尝试离开
    # idx28-30 只是回升铺垫，low 必须留在独立验证阈值（约 103.39）之上，
    # 否则这几根固定的 bar 会自己触发"已离开"，让 dip_low 的对照失去意义。
    rows += [[103.7, 104.0, 103.6, 103.8]] * 3   # idx28-30: 回升铺垫
    rows.append([104.0, 104.3, 103.5, 104.0])    # idx31 左邻居
    rows.append([104.5, 105.0, 104.0, 104.8])    # idx32: 第二次触碰 K=105.0
    rows.append([104.0, 104.3, 103.5, 104.0])    # idx33 右邻居
    rows.append([104.0, 104.3, 103.5, 104.0])    # idx34 右邻居
    rows += [[104.0, 104.3, 103.5, 104.0]] * 10
    return make_ohlc(rows)


def test_sideways_bars_do_not_count_as_a_second_validation():
    """横盘在区间内、从没离开过——不该算验证。"""
    df = _independent_touch_scenario(dip_low=104.5)   # 没跌破，一直待在区间附近
    zones = detect_key_zones(df, required_touches=3)
    resistance = [z for z in zones if z.direction == 1 and z.established_idx == 20]
    assert len(resistance) == 1
    assert resistance[0].touch_count == 1, "没离开过区间，不该有第二次验证"


def test_leaving_and_returning_counts_as_a_second_validation():
    """真的跌破区间下沿 1A 以上、再回来测试——这才算一次独立验证。"""
    df = _independent_touch_scenario(dip_low=95.0)   # 明显跌破
    zones = detect_key_zones(df, required_touches=3)
    resistance = [z for z in zones if z.direction == 1 and z.established_idx == 20]
    assert len(resistance) == 1
    assert resistance[0].touch_count == 2, "跌破过区间后回来测试，应该算第二次验证"


# ── 有效期：lookback_bars / max_since_touch ─────────────────────────────────

def _stale_zone_scenario():
    """建区后再没有任何触碰，纯粹靠时间流逝观察 lookback_bars 到期。"""
    rows = _stable_base()
    rows.append([100, 100.5, 99.5, 100])       # idx18
    rows.append([100, 100.5, 99.5, 100])       # idx19
    rows.append([104.5, 105, 104, 104.8])      # idx20: 建区
    rows.append([100, 100.5, 99.5, 100])       # idx21
    rows.append([100, 100.5, 99.5, 100])       # idx22
    rows += [[104.0, 104.3, 103.8, 104.0]] * 20  # idx23-42: 纯粹的时间流逝
    return make_ohlc(rows)


def test_zone_not_yet_expired_at_exactly_lookback_bars():
    df = _stale_zone_scenario()
    zones = detect_key_zones(df.iloc[:31], lookback_bars=10)   # t_max=30, 30-20=10，未超
    resistance = [z for z in zones if z.direction == 1 and z.established_idx == 20][0]
    assert resistance.status != "expired"


def test_zone_expires_once_lookback_bars_exceeded():
    df = _stale_zone_scenario()
    zones = detect_key_zones(df.iloc[:32], lookback_bars=10)   # t_max=31, 31-20=11，超了
    resistance = [z for z in zones if z.direction == 1 and z.established_idx == 20][0]
    assert resistance.status == "expired"


def test_zone_expires_from_max_since_touch_even_within_lookback_window():
    """
    建区(idx20)到第二次触碰(idx32)之间本来就有 12 根 bar 的空档，所以
    max_since_touch 必须先大到能撑过这段空档（否则区间在触碰真正发生之前
    就已经因为"建区后一直没人搭理"而提前过期，touch 根本没机会被记上）；
    真正要测的是touch(32)之后又经过足够久，才应该单独触发过期。
    """
    df = _independent_touch_scenario(dip_low=95.0)
    extra_pad = pd.DataFrame(
        [[104.0, 104.3, 103.5, 104.0]] * 15,
        columns=["open", "high", "low", "close"],
        index=pd.date_range(df.index[-1] + pd.Timedelta(days=1), periods=15),
    ).assign(volume=1e6)
    df = pd.concat([df, extra_pad])

    zones_ok = detect_key_zones(df.iloc[:46], max_since_touch=13, required_touches=3)
    r_ok = [z for z in zones_ok if z.direction == 1 and z.established_idx == 20][0]
    assert r_ok.touch_count == 2, "先确认第二次触碰(idx32)确实被记上了，不是因为提前过期而漏记"
    assert r_ok.status != "expired", "距上次触碰(idx32)只有 13 根，还不该过期"

    zones_expired = detect_key_zones(df, max_since_touch=13, required_touches=3)  # 用到最后，远超 13 根
    r_expired = [z for z in zones_expired if z.direction == 1 and z.established_idx == 20][0]
    assert r_expired.status == "expired"


# ── 区间固定不动 ─────────────────────────────────────────────────────────────

def test_zone_boundaries_never_move_after_establishment():
    """第二次触碰的价格和第一次不完全相同，区间边界也不该变。"""
    df = _independent_touch_scenario(dip_low=95.0)
    zones = detect_key_zones(df, required_touches=3)
    resistance = [z for z in zones if z.direction == 1 and z.established_idx == 20][0]
    original_low, original_high, original_atr = 104.75, 105.25, 1.0
    assert resistance.low == pytest.approx(original_low, abs=0.35)
    assert resistance.high == pytest.approx(original_high, abs=0.35)
    # 两次触碰之间即使有轻微价格差异，A 也应该保持建区时冻结的值不变
    assert resistance.atr == pytest.approx(original_atr, abs=0.35)


# ── 提前突破作废：不允许事后补票 ─────────────────────────────────────────────

def test_zone_invalidated_by_early_breakout_before_reaching_required_touches():
    """
    只验证过 1 次（还没到 required_touches=3）时，收盘价就已经显著突破——
    这个区间永久作废，就算价格后来又回来贴着测试，也不能重新计入。
    """
    rows = _stable_base()
    rows.append([100, 100.5, 99.5, 100])       # idx18
    rows.append([100, 100.5, 99.5, 100])       # idx19
    rows.append([104.5, 105, 104, 104.8])      # idx20: K=105，建区
    rows.append([100, 100.5, 99.5, 100])       # idx21
    rows.append([100, 100.5, 99.5, 100])       # idx22
    # 提前显著突破：收盘远超 U+0.25A（≈105.25+0.25），只验证了 1 次
    rows.append([105.5, 108, 105.3, 107.5])    # idx23
    rows += [[107.0, 107.5, 106.5, 107.0]] * 15
    df = make_ohlc(rows)

    zones = detect_key_zones(df, required_touches=3)
    resistance = [z for z in zones if z.direction == 1 and z.established_idx == 20][0]
    assert resistance.status == "invalidated"
    assert resistance.touch_count == 1


# ── required_touches 参数化 ──────────────────────────────────────────────────

def test_required_touches_two_confirms_earlier_than_three():
    """同一段历史，2 次验证版应该比 3 次验证版更早（或更容易）确认。"""
    df = _independent_touch_scenario(dip_low=95.0)
    zones2 = detect_key_zones(df, required_touches=2)
    zones3 = detect_key_zones(df, required_touches=3)
    r2 = [z for z in zones2 if z.direction == 1 and z.established_idx == 20][0]
    r3 = [z for z in zones3 if z.direction == 1 and z.established_idx == 20][0]
    assert r2.status == "confirmed"     # 2 次验证：达标
    assert r3.status != "confirmed"     # 3 次验证：还差一次，只是 building


# ── key_zone_breakout：突破信号 + 执行区间 ───────────────────────────────────

def test_breakout_signal_only_fires_once_confirmed_and_only_once_ever():
    df = _independent_touch_scenario(dip_low=95.0)
    # 在原有基础上接一段突破走势
    extra = [[105.0 + i * 0.5, 105.5 + i * 0.5, 104.5 + i * 0.5, 105.0 + i * 0.5]
            for i in range(1, 15)]
    idx2 = pd.date_range(df.index[-1] + pd.Timedelta(days=1), periods=len(extra))
    ext_df = pd.DataFrame(extra, columns=["open", "high", "low", "close"], index=idx2)
    ext_df["volume"] = 1e6
    full = pd.concat([df, ext_df])

    sig = key_zone_breakout(full, required_touches=2)
    fired = sig.entry[sig.entry != 0]
    assert len(fired) == 1, "关键区只应该产生一次突破信号"
    assert fired.iloc[0] == 1
    t = fired.index[0]
    assert sig.touch_count.loc[t] == 2
    assert not np.isnan(sig.stop_price.loc[t])
    assert sig.exec_lo.loc[t] < sig.exec_hi.loc[t]


def test_execution_band_uses_zone_boundary_and_one_atr():
    """exec_lo/exec_hi 应该是 (U, U+1A]（多头），不是别的区间。"""
    df = _independent_touch_scenario(dip_low=95.0)
    extra = [[105.0 + i * 0.5, 105.5 + i * 0.5, 104.5 + i * 0.5, 105.0 + i * 0.5]
            for i in range(1, 15)]
    idx2 = pd.date_range(df.index[-1] + pd.Timedelta(days=1), periods=len(extra))
    ext_df = pd.DataFrame(extra, columns=["open", "high", "low", "close"], index=idx2)
    ext_df["volume"] = 1e6
    full = pd.concat([df, ext_df])

    sig = key_zone_breakout(full, required_touches=2)
    t = sig.entry[sig.entry != 0].index[0]
    assert sig.exec_lo.loc[t] == pytest.approx(sig.zone_high.loc[t])
    # exec_hi - exec_lo 应该恰好是 1 个 A（用止损价反推 A：U - S0 = U-(U+0.2A) 不对，
    # 直接用 zone_high 和 stop_price 的关系式反推更直接：S0 = U + stop_atr*A（空头）
    # 这里是多头，S0 = L - 0.20A，A = zone_high - zone_low 除以 0.5（区间宽=0.5A）
    A = (sig.zone_high.loc[t] - sig.zone_low.loc[t]) / 0.5
    assert sig.exec_hi.loc[t] == pytest.approx(sig.zone_high.loc[t] + A, abs=1e-6)


# ── 同一根 bar 多个候选区同时满足条件：选离收盘价最近的 ─────────────────────

def test_multiple_confirmed_zones_breaking_out_same_bar_picks_the_nearest_one():
    """
    两个互不相干的阻力区（K=105 和 K=115），都只要求验证 1 次（建区即确认），
    然后一根大阳线同时炸穿两边的突破阈值——按规则应该选"突破前离收盘价最近"
    的那个，也就是阈值更高、离收盘价更近的 K=115 区间；K=105 区间当天不发
    信号，而且这次机会也用掉了，之后哪怕价格回落再单独重新突破 A 的阈值，
    也不该再补发一次信号（"traded" 状态只在 `key_zone_breakout` 跑信号选择
    那一步才会打上，`detect_key_zones` 不产生信号也就不标记，所以这里只能
    通过"后面还会不会再发一次信号"来间接验证"没有第二次机会"）。
    """
    rows = _stable_base()
    rows.append([100, 100.5, 99.5, 100])       # idx18
    rows.append([100, 100.5, 99.5, 100])       # idx19
    rows.append([104.5, 105, 104, 104.8])      # idx20: 区间A K=105
    rows.append([104.2, 104.3, 104.0, 104.1])  # idx21
    rows.append([104.2, 104.3, 104.0, 104.1])  # idx22
    rows += [[104.0, 104.2, 103.8, 104.0]] * 16  # idx23-38: 填充，留在两个区间阈值之下
    rows.append([104.0, 104.2, 103.8, 104.0])    # idx39 区间B左邻居
    rows.append([104.0, 104.2, 103.8, 104.0])    # idx40 区间B左邻居
    # 长上影线：high 摸到 115（局域高点判定只看 high）但收盘价拉回 104 附近，
    # 不然这根 bar 自己的收盘价就会顺带把 A 的突破阈值也踩过去，干扰对照。
    rows.append([104.0, 115, 103, 104.2])        # idx41: 区间B K=115
    rows.append([104.2, 104.3, 104.0, 104.1])    # idx42 区间B右邻居
    rows.append([104.2, 104.3, 104.0, 104.1])    # idx43 区间B右邻居
    rows += [[104.0, 104.2, 103.8, 104.0]] * 5   # idx44-48: 继续填充
    rows.append([110.0, 141.0, 109.0, 140.0])    # idx49: 一根大阳线同时突破 A、B
    # idx50 起回落到 A 的区间附近，然后单独再冲一次 A 的阈值（不动 B）——
    # 用来验证 A 当天没被选中也不该留到以后再补一次信号。
    rows += [[130.0, 132.0, 128.0, 130.0]] * 3
    rows += [[106.0, 108.0, 104.0, 106.0]] * 3
    rows.append([106.0, 108.0, 105.5, 107.8])    # 单独重新冲高，只够着 A 的阈值
    rows += [[107.0, 107.5, 106.5, 107.0]] * 5
    df = make_ohlc(rows)

    sig = key_zone_breakout(df, required_touches=1, max_since_touch=40)
    fired = sig.entry[sig.entry != 0]
    assert len(fired) == 1, \
        "两个区间同一根 bar 同时满足条件只能选一个；A 事后单独重新突破也不该补发第二次信号"
    t = fired.index[0]
    zones = detect_key_zones(df, required_touches=1, max_since_touch=40)
    zB = [z for z in zones if z.direction == 1 and z.established_idx == 41][0]
    assert sig.zone_low.loc[t] == pytest.approx(zB.low), "应该选阈值更高、离收盘价更近的 B，不是 A"
