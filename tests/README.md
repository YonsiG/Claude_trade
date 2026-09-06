# tests/

回归测试。**这里的几乎每一条都对应一个曾经真实存在过的 bug** ——
而且大多是静默的：不报错，只是悄悄给出错误的数字。

```bash
pytest                      # 全部
pytest -m "not data"        # 只跑合成数据（离线可用，<1 秒）
pytest tests/test_trade.py  # 单个文件
pytest -q -k margin         # 按名字筛选
```

---

## 文件分工

| 文件 | 条数 | 覆盖 |
|------|------|------|
| `test_trade.py` | 23 | 仓位原语：记账、手续费、峰值生命周期、可用资金、加仓、保证金、强平、爆仓 |
| `test_strategy.py` | 22 | 执行循环：信号语义、离场条件、成交价、加仓、合约规格、流水对账 |
| `test_trend.py` | 21 | 判定门槛、confidence 语义、duration、换月标记 |
| `test_engine.py` | 18 | Signal 契约、基准、绩效指标、组合聚合、组合不留副作用 |
| `test_signals.py` | 24 | K 线形态：is_umbrella/is_doji/is_engulfing、趋势上下文匹配 |
| `test_platform.py` | 26 | 平台识别：倒序扫描状态机、动态波动率窗口、"或"阈值取舍、最低验证门槛、放量确认、lookback 窗口 |
| `test_platform_breakout.py` | 22 | 平台策略：风险预算≠保证金、价格位止损、利润追踪止盈 |
| `test_key_zone.py` | 19 | 关键区识别（`platform.py` 对照组）：不能用未来数据确认局域高低点、必须离开再回来才算验证、最小触碰间隔、区间固定不移动、提前突破永久作废、lookback/max_since_touch 双重有效期、同 bar 多候选区选最近的 |
| `test_key_zone_breakout.py` | 12 | 关键区策略：次日开盘执行 + 可接受区间放弃、R 倍数追踪止盈、固定止盈上限可关闭、股票不能裸卖空 |

`conftest.py` 提供合成行情与合成信号的构造器；根目录的 `conftest.py`
负责把项目根塞进 `sys.path`（项目内部用扁平导入）。

---

## 两类用例

**合成数据**（193 条）：不联网、不依赖缓存，任何时候都能跑，是回归的主力。

**真实数据**（2 条，打 `@pytest.mark.data`）：需要 `data/raw/` 里的缓存或联网，
缺失时自动 skip，不会因为没网让整套测试变红。

---

## 几条特别值得保留的

这些是最容易在重构中被悄悄破坏、又最难在回测结果里看出来的：

| 测试 | 它挡住的问题 |
|------|-------------|
| `test_position_survives_zero_entry_bars` | 把 `entry==0` 误读成"应该空仓"，每笔只持 1 根 bar，止损止盈全成死代码 |
| `test_trailing_peak_does_not_leak_between_positions` | 峰值跨仓位残留，新仓开出来当场被打掉 |
| `test_intrabar_stop_fills_at_trigger_price_not_close` | 用收盘价判止损，系统性低估回撤、高估夏普 |
| `test_stopped_bar_does_not_rebuy_at_same_price` | 止损后同 bar 原价买回，止损当场失效 |
| `test_trade_pnl_reconciles_with_equity_curve` | 流水与资金曲线对不上，归因分析全错 |
| `test_futures_without_contract_spec_raises` | 沉默地用 `multiplier=1.0`，每个期货品种都算错（AU 差 1000 倍） |
| `test_duration_is_deterministic` | 二分搜索非单调谓词，duration 是任意值 |
| `test_steep_trend_stays_directional_and_is_not_abnormal` | 把"趋势很陡"当成"数据异常"，吞掉最该出信号的行情 |
| `test_price_gap_without_oi_jump_is_not_a_roll` | 把涨跌停和节后缺口误判为换月并抹平真实行情 |
| `test_benchmark_never_goes_negative_under_leverage` | 基准算出 -200%，夏普和回撤失真 |
| `test_scan_stops_at_a_significant_breakout` | 平台扫描方向/终止条件搞反会把"验证"和"找到源头"判错 |
| `test_touch_tolerance_uses_the_tighter_or_bound` | 容差"或"取错（该用 min 却用了 max），验证次数算出两种截然不同的答案却测不出来——只在 `_scan_backward` 单元测试里传字面量 tol，测不到 `_platform_state` 里的组合逻辑 |
| `test_new_peak_within_the_same_bar_does_not_trigger_giveback_immediately` | 当根 bar 创新高又用来判断"回吐"，无中生有触发止盈 |
| `test_volatility_term_uses_today_to_the_specific_pivot_not_the_whole_window` | "平均每日波动率"退化成整窗算一次，而不是随扫描逐步推进动态变化 |
| `test_at_least_one_lot_when_margin_allows_even_if_risk_budget_implies_less` | 混淆风险预算和保证金，导致仓位小的品种（生猪、白银期货）算出 0 手直接不开仓 |
| `test_volume_baseline_excludes_the_breakout_day_itself` | 放量确认的均量基准把当日算了进去，抬高了自己的比较基准 |
| `test_min_validations_filters_out_weakly_tested_platforms` | 门槛判定漏传/写死，永远只要求验证过 1 次 |
| `test_sideways_bars_do_not_count_as_a_second_validation` | 关键区状态机没做"必须离开再回来"这道独立验证，纯横盘就把重复触碰当成新一次验证 |
| `test_touch_timing_does_not_leak_same_bar_updates_across_the_confirmation_delay` | 状态机处理某次触碰时把"当天自己"对 left_since_last_touch 的更新提前生效，导致两个本该独立的区间被错误合并 |
| `test_ratio_round_trip_does_not_lose_an_affordable_lot_to_float_noise` | 仓位公式反推 ratio 再乘回去重新 floor 手数，浮点乘除不严格可逆，恰好够 1 手的位置会因为 0.999999999 被 floor 成 0 手静默弃单 |
| `test_trailing_never_fires_as_a_phantom_same_bar_exit_at_breakeven` | 追踪止盈激活门槛设为"任意正盈利"时，刚开仓那根 bar 自己的最高价还没并入 peak，回吐价精确退化成开仓价，几乎每次开仓都在同一根 bar 里凭空按保本价"止盈"离场 |

---

## 加新测试

改动任何一处仓位/离场/判定逻辑时，先在这里加一条会失败的用例，再去改代码。
合成数据的构造器已经备好：

```python
from conftest import make_ohlc, make_bars, make_signal_fn, ramp

df = make_ohlc([[100, 101, 88, 100]])        # 逐根指定 OHLC，精确控制触发价
df = make_bars(ramp(20, 1.006), band=0.012)  # 用收盘价序列造 bar
fn = make_signal_fn([1, 0, -1], [0.5, 0, 0.8])
```
