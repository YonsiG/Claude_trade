# trend/

市场状态识别模块。对最近 N 根 K 线进行整体分类，输出单一趋势类型，与买卖信号独立。

---

## 趋势类型

| 值 | 名称 | 描述 |
|----|------|------|
| 1 | `UPTREND` | 上涨趋势（线性回归斜率 > 0） |
| 2 | `DOWNTREND` | 下跌趋势（线性回归斜率 < 0） |
| 3 | `OSCILLATING` | 震荡（均值穿越 + ATR 有意义） |
| 4 | `FLAT` | 横盘（极窄价格区间，低波动） |
| 5 | `OTHER` | 未识别到主导趋势 |
| 6 | `ABNORMAL` | 任意趋势强度达到 EXTREME 时自动升级 |

---

## 强度等级

除 `FLAT` 和 `OTHER` 外，每种趋势都携带强度：

| 值 | 名称 | 上涨/下跌阈值 | 震荡阈值 |
|----|------|--------------|---------|
| 1 | `NORMAL` | 斜率 < 0.3%/bar | ATR/价格 < 3% |
| 2 | `STRONG` | 斜率 0.3%–0.8%/bar | ATR/价格 3%–6% |
| 3 | `EXTREME` | 斜率 > 0.8%/bar | ATR/价格 > 6% |

强度达到 `EXTREME` 时，趋势类型自动升级为 `ABNORMAL`（值 6）。  
`result.base_trend` 保留原始趋势类型（如 `UPTREND`），供下游使用。

---

## 使用方法

```python
from trend import detect_trend, TrendType, TrendIntensity

result = detect_trend(bars, n=30)                    # 对最近 30 根 bar 分类
result = detect_trend(bars, compute_duration=False)  # 跳过持续时长计算（更快）

int(result.trend)          # 1–6
result.trend.name          # "UPTREND"、"ABNORMAL" 等
result.intensity.name      # "NORMAL"、"STRONG"、"EXTREME"
result.confidence          # 0.0–1.0，检测器的拟合质量
result.is_abnormal         # True 表示强度为 EXTREME
result.base_trend          # ABNORMAL 时为原始趋势类型，否则为 None
result.duration            # 当前趋势已持续的 bar 数（0 = 未计算）
result.description         # 人类可读详情（斜率、ATR 比率等）
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
| `abnormal.py` | `wrap_as_abnormal()` 辅助函数 |
| `detector.py` | `detect_trend()` — 运行检测管道，返回最优结果 |

---

## 扩展

新增趋势类型（例如 `BREAKOUT = 7`）：

1. 在 `base.py` 的 `TrendType` 中添加枚举值。
2. 新建 `trend/breakout.py`，实现 `detect_breakout(bars) -> TrendResult`。
3. 在 `detector.py` 的 `_DEFAULT_PIPELINE` 中追加 `(detect_breakout, {})`。

也可在调用时传入自定义管道：

```python
from trend.uptrend import detect_uptrend
result = detect_trend(bars, pipeline=[(detect_uptrend, {"slope_min": 0.001})])
```
