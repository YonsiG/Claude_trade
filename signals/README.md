# signals/

原子信号模块，每个函数接收价格序列，返回逐 bar 的 0/1 信号。

---

## 现状

`price_signals.py` 中的三个函数（`is_increase`、`is_decrease`、`is_consolidation`）描述的是趋势而非信号，待后续重构或删除。

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `price_signals.py` | 基础价格信号（连涨、连跌、横盘） |
| `pattern.py` | K 线形态信号（锤子线、吞噬等） |
| `feature/single_bar.py` | 单根 K 线特征提取 |
| `feature/two_bar.py` | 双根 K 线特征提取 |

---

## 信号接口规范

所有信号函数遵循统一签名：

```python
def signal_name(close: pd.Series, **kwargs) -> pd.Series:
    ...  # 返回值：与 close 同索引的 0/1 整数序列
```

---

## 现有信号

### price_signals.py

| 函数 | 触发条件（返回 1） |
|------|-------------------|
| `is_increase(close, n=3)` | 连续 n 日上涨 |
| `is_decrease(close, n=3)` | 连续 n 日下跌 |
| `is_consolidation(close, n=5, threshold=0.02)` | n 日内振幅小于阈值（如 2%） |

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

在 `signals/` 下新建文件或在现有文件中添加函数：

```python
def my_signal(close: pd.Series, window: int = 10) -> pd.Series:
    # 计算逻辑
    return signal.astype(int)
```

然后在策略中直接引用：

```python
from signals.my_file import my_signal

strategy = MultiSignalStrategy(df, buy_signals=[my_signal], ...)
```
