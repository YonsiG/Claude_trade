# signals/

原子信号模块，每个函数接收 OHLCV DataFrame，返回 `Signal(entry, size)`。

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `base.py` | `Signal` 返回类型与校验 |
| `pattern.py` | K 线形态信号（伞形线、吞噬） |
| `platform.py` | 平台突破（返回独立的 `PlatformSignal`，不是 `Signal`，见下） |
| `key_zone.py` | 关键区突破（`platform.py` 的对照组，返回独立的 `KeyZoneSignal`，见下） |
| `feature/single_bar.py` | 单根 K 线特征提取 |
| `feature/two_bar.py` | 双根 K 线特征提取 |

---

## 信号接口规范

所有信号函数遵循统一签名：

```python
def signal_name(df: pd.DataFrame, **kwargs) -> Signal:
    ...
```

返回的 `Signal` 是一个 NamedTuple，含两条与 `df` 同索引的序列：

| 字段 | 取值 | 含义 |
|------|------|------|
| `entry` | `-1` / `0` / `+1` | 开仓方向脉冲 |
| `size` | `[0, 1]` | 该次开仓投入的资金比例 |

**`entry` 是事件，不是持仓状态。** 它只在形态成立的那一根 bar 非 0；
`entry == 0` 表示"无事发生"，**不是**"应该空仓"。持仓会一直保留，
直到被策略的止损、止盈或反向信号终结。这两件事必须分开想：
信号负责回答"此刻要不要建仓、建多大"，策略负责回答"什么时候走"。

`size` 用来把方向和下注大小解耦：可以是形态强度，也可以来自
波动率倒数定标、凯利公式或任何独立的风控逻辑。`entry == 0` 的
bar 上 `size` 无意义，`validate()` 会统一清零。

**这条统一签名不是强制的。** `platform.py` 返回自己的 `PlatformSignal`
（entry, stop_price, validations），因为按风险定仓位需要止损价和验证次数，
`Signal` 那两个字段不够用；它的 `entry` 语义也不同（"条件持续成立"而不是
"事件脉冲"，原因见 `PlatformSignal` 自己的 docstring）。新增信号如果需要
携带 `Signal` 装不下的信息，照着这个先例另起一个专属的返回类型 + 专属的
策略类去消费它，不要硬塞进 `Signal` 或悄悄改变 `entry` 的既有含义。

---

## 现有信号

### pattern.py

| 函数 | entry = +1 | entry = -1 | size 权重 |
|------|-----------|-----------|-----------|
| `umbrella(df, ...)` | 下跌趋势中的锤子线 | 上升趋势中的上吊线（次日确认） | 下影 40% / 上影 20% / 实体 40% |
| `engulfing(df, ...)` | 下跌趋势中的看涨吞噬 | 上升趋势中的看跌吞噬 | 成交量 60% / 实体倍数 40% |

### platform.py

平台突破：识别历史上反复被测试过的关键支撑/压力位（"平台"），价格突破时开仓。
详见模块自身的 docstring（算法里几处口语化描述如何落成可实现版本的取舍）。

返回类型是**独立的** `PlatformSignal(entry, stop_price, validations)`，不是
`signals.base.Signal`——`entry` 语义也不同：这里是"条件持续成立"，不是
"只在形态成立那一根 bar 触发一次"的事件脉冲，见 `PlatformSignal` 自己的说明。
配套策略是 `strategies/platform_breakout.py::PlatformBreakoutStrategy`
（价格位止损 + 按验证次数分级的风险仓位），不是 `SingleSignalStrategy`。

```python
from signals.platform import platform_breakout, detect_platforms

sig = platform_breakout(df)   # PlatformSignal，直接喂给 PlatformBreakoutStrategy
info = detect_platforms(df)   # PlatformInfo，逐日平台价位+验证次数，供画图/诊断
```

### key_zone.py

`platform.py` 的对照组——同样是"识别反复测试过的关键位、突破时开仓"，但换成
**正向逐 bar 状态机**（不是倒序扫描），规则明显更严格、更贴近真实交易约束：

- 局域高低点用严格 5 日 pivot（不像 `platform.py` 的 3 日 pivot 允许打平），
  且要等右侧 K 线走完才算数，不能用未来数据。
- 两次触碰之间必须先离开区间 1 个 ATR 以上再回来，纯粹横盘不算重复验证。
- 区间一旦建立（`L=K±0.25A`，`A` 是建区时刻冻结的 ATR(14)）就不再随后续
  触碰移动。验证不够就提前显著突破——区间永久作废，不允许事后补票。
- 每个关键区一辈子只有一次突破信号（`entry` 是一次性事件脉冲，和
  `PlatformSignal` 的"条件持续成立"不同），次日开盘价必须落在信号自带的
  `exec_lo`/`exec_hi` 可接受区间内才执行，否则直接放弃、不追价。

返回类型是**又一个独立的** `KeyZoneSignal(entry, stop_price, touch_count,
exec_lo, exec_hi, zone_low, zone_high)`。配套策略是
`strategies/key_zone_breakout.py::KeyZoneBreakoutStrategy`，止盈追踪的激活
门槛按初始风险的 R 倍数算，不是开仓价的固定百分比。详见模块自身的 docstring
（8 条算法规则的完整表述）。

```python
from signals.key_zone import key_zone_breakout, detect_key_zones

sig = key_zone_breakout(df, required_touches=3)   # KeyZoneSignal，喂给 KeyZoneBreakoutStrategy
zones = detect_key_zones(df, required_touches=3)  # 完整区间列表，供诊断/画图
```

---

## 路线图

### K 线形态类（进行中）

- [x] 单根 K 线特征提取（`feature/single_bar.py`）
- [x] 双根 K 线特征提取（`feature/two_bar.py`）
- [ ] 锤子线、射击之星、十字星、大阳线/大阴线
- [ ] 吞噬形态（Engulfing）、孕线（Harami）、穿刺/乌云盖顶
- [ ] 早晨之星/黄昏之星、三白兵/三黑鸦

### 技术指标类（待做）

- [ ] 均线交叉（MA Crossover）
- [ ] RSI 超买超卖
- [ ] MACD
- [ ] 布林带突破
- [ ] 成交量异常放大

### 统计类（待做）

- [ ] Z-score 偏离
- [ ] 均值回归信号

---

## 新增信号

在 `signals/` 下新建文件或在现有文件中添加函数。逐 bar 填写：

```python
from signals.base import Signal

def my_signal(df: pd.DataFrame, window: int = 10) -> Signal:
    sig = Signal.blank(df.index)
    cond = ...                      # 你的形态/指标条件，布尔序列
    sig.entry[cond] = 1             # 或 -1
    sig.size[cond] = 0.5            # 仓位比例
    return sig
```

若已有 [-1, 1] 的连续强度序列，直接拆开即可：

```python
def my_signal(df: pd.DataFrame) -> Signal:
    strength = ...                  # [-1, 1]，符号为方向，绝对值为强度
    return Signal.from_strength(strength)
```

然后在策略中直接引用：

```python
from signals.my_file import my_signal

strategy = SingleSignalStrategy(df, signal_fn=my_signal, trail_pct=0.30, sl_pct=0.10)
```
