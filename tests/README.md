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
| `test_trade.py` | 22 | 仓位原语：记账、手续费、峰值生命周期、可用资金、加仓、保证金、强平、爆仓 |
| `test_strategy.py` | 22 | 执行循环：信号语义、离场条件、成交价、加仓、合约规格、流水对账 |
| `test_trend.py` | 21 | 判定门槛、confidence 语义、duration、换月标记 |
| `test_engine.py` | 16 | Signal 契约、基准、绩效指标、组合聚合 |

`conftest.py` 提供合成行情与合成信号的构造器；根目录的 `conftest.py`
负责把项目根塞进 `sys.path`（项目内部用扁平导入）。

---

## 两类用例

**合成数据**（79 条）：不联网、不依赖缓存，任何时候都能跑，是回归的主力。

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
