# signals/

原子信号模块，每个函数接收 OHLCV DataFrame，返回 `Signal(entry, size)`。

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `base.py` | `Signal` 返回类型与校验 |
| `pattern.py` | K 线形态信号（伞形线、吞噬） |
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

---

## 现有信号

### pattern.py

| 函数 | entry = +1 | entry = -1 | size 权重 |
|------|-----------|-----------|-----------|
| `umbrella(df, ...)` | 下跌趋势中的锤子线 | 上升趋势中的上吊线（次日确认） | 下影 40% / 上影 20% / 实体 40% |
| `engulfing(df, ...)` | 下跌趋势中的看涨吞噬 | 上升趋势中的看跌吞噬 | 成交量 60% / 实体倍数 40% |

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
