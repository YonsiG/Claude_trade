# trend/

市场状态识别模块。对最近 N 根 K 线进行整体分类，输出单一趋势类型，与买卖信号独立。

---

## 趋势类型

| 值 | 名称 | 判定门槛（硬条件，全部满足才合格） |
|----|------|------|
| 1 | `UPTREND` | 斜率 >= +0.08%/bar **且** R² >= 0.45 |
| 2 | `DOWNTREND` | 斜率 <= -0.08%/bar **且** R² >= 0.45 |
| 3 | `OSCILLATING` | 均值穿越 >= 3 次 **且** ATR/价格 >= 1.5% |
| 4 | `FLAT` | 区间 <= 1% **且** ATR/价格 <= 0.5% |
| 5 | `OTHER` | 没有任何检测器合格（confidence 固定为 0） |
| 6 | `ABNORMAL` | 窗口内存在不可交易的价格断裂（期货换月为主），读数不可信 |

---

## 强度等级

除 `FLAT` 和 `OTHER` 外，每种趋势都携带强度：

| 值 | 名称 | 上涨/下跌阈值 | 震荡阈值 |
|----|------|--------------|---------|
| 1 | `NORMAL` | 斜率 < 0.3%/bar | ATR/价格 < 3% |
| 2 | `STRONG` | 斜率 0.3%–0.8%/bar | ATR/价格 3%–6% |
| 3 | `EXTREME` | 斜率 > 0.8%/bar | ATR/价格 > 6% |

**强度与 ABNORMAL 无关。** 陡峭的趋势保持 `UPTREND` / `DOWNTREND`，
只是 `intensity = EXTREME` —— 它是真实行情，而且恰恰是形态信号最有意义的地方。

> 旧版本把 `EXTREME` 直接改写成 `ABNORMAL`，等于用"趋势很陡"冒充"数据异常"。
> 实测 AAPL 上 10% 的 bar 因此被吞掉，形态信号拿不到方向读数。现已分开。

---

## ABNORMAL：换月与价格断裂

`ABNORMAL` 现在只表示一件事：**窗口内的 bar 不属于同一份可交易的标的**，
最典型的就是期货主力连续在换月时切换了合约。此时跨断裂点的形态
（吞噬要两根、趋势拟合要一串）都读不得。

识别换月复用 `data/adjust.py`，按可靠性排序：

1. `roll_dates` —— 调用方给出的真实换月日
2. **持仓量（`hold`）跳变** —— 默认。换月必然切换合约，持仓量断裂；
   涨跌停和节后缺口发生在同一份合约上，持仓量连续
3. 价格跳空阈值 —— 仅在没有 `hold` 列时（yfinance 期货、老缓存）退而求其次

**只对期货生效。** `check_abnormal=None`（默认）会自动判断：数据带 `hold` 列
或给了 `roll_dates` 才检查。股票没有换月，永远不会误触发。
yfinance 期货（`GC=F` 等）没有 `hold` 列，需显式传 `check_abnormal=True`
才启用，那种情况下只能靠跳空阈值，可靠性明显更低。

### `abnormal_bars`：严格程度与信号数量的取舍

一次换月会污染 `n` 个窗口，所以这个参数直接决定屏蔽掉多少信号。
RB0 实测（2021-01 至 2026-07，1331 个 20 根窗口，12 次换月）：

| `abnormal_bars` | ABNORMAL 占比 | engulfing 信号数 |
|---|---|---|
| `None`（默认，整个窗口） | 17.1% | 10 |
| `5` | 4.5% | — |
| `2` | 1.8% | 12 |
| `check_abnormal=False` | 0% | 13 |

数据经过后复权（loader 对期货默认开启）后价格序列本身是连续的，趋势拟合
跨换月点问题不大，此时把 `abnormal_bars` 设成信号需要的 bar 数（吞噬取 2）
更划算；要严格排除任何跨合约读数则保持 `None`。

---

## confidence 与判定顺序

**confidence 的语义**：合格才有 confidence，取值 `[0.5, 1.0]`。
0.5 = 刚好压线达标，1.0 = 教科书级别。不合格直接返回 `OTHER`（confidence = 0），
而不是返回一个低分让上层去比大小。

> 旧版本各检测器的 confidence 量纲完全不同——FLAT 是 `1-range/max`、
> 趋势是 R²、震荡是穿越次数比——却在 pipeline 里直接比大小选优。
> 那个比较没有意义：一段几乎水平但极规整的走势能拿到 R²≈1 的"高置信度上升趋势"，
> 压过真正的震荡判定。

**判定顺序即优先级**，由具体到宽泛，取**第一个**合格的：

```
FLAT  ->  UPTREND  ->  DOWNTREND  ->  OSCILLATING  ->  OTHER
```

这几类在硬条件上本就近乎互斥（一条干净的线性趋势只穿越自身均值一次，
达不到震荡的 3 次门槛），优先级只用来裁决边界情况。

`min_confidence` 现在是一个有意义的旋钮：调到 0.8 就是"只要教科书级别的匹配"。

---

## duration 的口径

`duration` = **仍能被判为该趋势的最长后缀窗口长度**，用线性扫描精确计算。

> 旧版本用二分搜索找"最早仍成立的窗口起点"。但趋势判定对窗口起点不是单调的
> （某个起点不成立，更早的起点完全可能重新成立），二分在非单调谓词上会收敛到
> 任意位置——得出的 duration 是不可靠的数字。

两个必须知道的限制：

1. **受传入历史长度封顶**。只传 n 根 bar 就最多得到 n。想测真实持续时长，
   要把更长的历史传给 `detect_trend`——它只用最后 n 根做分类，多余部分专门用来往回量。
2. **不等于"肉眼看到的趋势起点"**。横盘或浅回调如果不破坏线性拟合，窗口会继续
   往前扩。V 形走势里上涨段只有 20 根，但纳入前面 12 根下跌后整体仍拟合出正斜率，
   duration 会给到 32。**把它当作"趋势影响范围"，不是"趋势起始日"。**

---

## 使用方法

```python
from trend import detect_trend, TrendType, TrendIntensity

result = detect_trend(bars, n=30)                    # 对最近 30 根 bar 分类
result = detect_trend(bars, compute_duration=False)  # 跳过持续时长计算（更快）

# 期货：自动启用换月检查（数据带 hold 列即可），或手工指定
result = detect_trend(bars, n=30, abnormal_bars=2)             # 只查末尾 2 根
result = detect_trend(bars, n=30, roll_dates=["2024-04-03"])   # 已知真实换月日
result = detect_trend(bars, n=30, check_abnormal=False)        # 完全关闭

int(result.trend)          # 1–6
result.trend.name          # "UPTREND"、"ABNORMAL" 等
result.intensity.name      # "NORMAL"、"STRONG"、"EXTREME"
result.confidence          # 合格时 [0.5, 1.0]；OTHER 固定为 0.0
result.is_abnormal         # True 表示窗口内有价格断裂（换月等）
result.is_extreme          # True 表示趋势极陡——真实行情，别和上一个混用
result.direction           # 方向读数，ABNORMAL 时回落到 base_trend
result.base_trend          # ABNORMAL 时为断裂前的趋势读数，否则为 None
result.duration            # 趋势影响范围，受历史长度封顶（0 = 未计算）
result.description         # 人类可读详情（斜率、ATR 比率、断裂位置等）
```

---

## 文件说明

| 文件 | 职责 |
|------|------|
| `base.py` | `TrendType`、`TrendIntensity`、`TrendResult` 数据类、`TrendDetector` 协议 |
| `uptrend.py` | 线性回归斜率检测（正斜率） |
| `downtrend.py` | 线性回归斜率检测（负斜率） |
| `oscillating.py` | 均值穿越 + ATR 比率检测 |
| `flat.py` | 极窄区间检测 |
| `other.py` | 兜底检测器 |
| `abnormal.py` | `detect_abnormal()` — 换月/价格断裂检测，复用 `data/adjust.py` |
| `detector.py` | `detect_trend()` — 先查断裂，再按优先级取第一个合格的检测器 |

---

## 扩展

新增趋势类型（例如 `BREAKOUT = 7`）：

1. 在 `base.py` 的 `TrendType` 中添加枚举值。
2. 新建 `trend/breakout.py`，实现 `detect_breakout(bars) -> TrendResult`。
3. 在 `detector.py` 的 `_DEFAULT_PIPELINE` 中**按优先级插入** `(detect_breakout, {})`——
   顺序决定裁决权，越具体的类型越靠前。
4. 合格时 confidence 返回 `[0.5, 1.0]`，不合格返回 `TrendType.OTHER`。

也可在调用时传入自定义管道：

```python
from trend.uptrend import detect_uptrend
result = detect_trend(bars, pipeline=[(detect_uptrend, {"slope_min": 0.001})])
```
