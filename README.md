# Claude Trade Expert

基于信号驱动的模块化量化交易研究框架，支持策略回测与市场状态识别。

---

## 项目结构

```
claude_trade_expert/
├── data/           # 数据下载与缓存（yfinance + akshare）
├── signals/        # 原子信号函数（买卖指标）
├── trend/          # 市场状态识别（趋势检测）
├── tools/          # 仓位管理原语（买入、卖出、止盈、止损）
├── strategies/     # 策略逻辑（组合信号）
├── backtest/       # 回测引擎、绩效指标、图表
└── run_example.py  # 端到端示例
```

---

## 数据模块（`data/`）

详见 [data/README.md](data/README.md)。

`loader.py` 支持双数据源，并自动缓存到本地 CSV：

```python
from data.loader import load

# yfinance：美股 / ETF / 美盘期货
df = load("GC=F", "2021-01-01", "2024-12-31", interval="1d", source="yf")

# akshare：国内期货
df = load("AU0", "2021-01-01", "2024-12-31", interval="1d", source="ak")
```

批量下载：

```bash
python data/fetch.py           # 全部品种，日线，近 5 年
python data/fetch.py --month   # 小时线，近 30 天
python data/fetch.py --day     # 分钟线，近 5 天
```

---

## 信号模块（`signals/`）

详见 [signals/README.md](signals/README.md)。

每个信号函数接收收盘价 `pd.Series`，返回逐 bar 的 0/1 信号序列。

| 函数 | 触发条件 |
|------|----------|
| `is_increase(close, n=3)` | 连续 n 日上涨 |
| `is_decrease(close, n=3)` | 连续 n 日下跌 |
| `is_consolidation(close, n=5, threshold=0.02)` | n 日内价格振幅小于阈值 |

新增信号：在 `signals/` 下定义 `fn(close: pd.Series) -> pd.Series` 即可。

---

## 仓位工具（`tools/`）

详见 [tools/README.md](tools/README.md)。

`trade.py` 提供四个有状态的仓位管理函数，共享 `state` 字典（含 `cash` 和 `shares`）：

| 函数 | 触发条件 |
|------|----------|
| `buy(state, price)` | 全仓买入 |
| `sell(state, price)` | 全仓卖出 |
| `take_profit(state, current_price, tp_price, ratio=1.0)` | `current_price >= tp_price` 时卖出 `ratio` 比例 |
| `stop_loss(state, current_price, sl_price, ratio=1.0)` | `current_price <= sl_price` 时卖出 `ratio` 比例 |

止盈/止损函数返回 `bool`，`True` 表示本次触发。

---

## 策略模块（`strategies/`）

### `BaseStrategy`（抽象基类）

```python
class BaseStrategy(ABC):
    def __init__(self, df: pd.DataFrame, initial_capital: float = 100_000): ...
    def run(self) -> pd.Series: ...  # 返回以日期为索引的资金曲线
```

### `MultiSignalStrategy`

组合多个买入信号和卖出信号：

```python
from strategies.multi_signal import MultiSignalStrategy

strategy = MultiSignalStrategy(
    df,
    buy_signals=[is_increase],
    sell_signals=[is_decrease],
    buy_threshold=1,   # 满足 >= 1 个买信号时买入
    sell_threshold=1,  # 满足 >= 1 个卖信号时卖出
    initial_capital=100_000,
)
```

每根 bar 的逻辑：买信号得分 >= 阈值 → 买入；否则卖信号得分 >= 阈值 → 卖出。买优先于卖。

---

## 回测模块（`backtest/`）

```python
from backtest import engine

summary = engine.run(ticker, start, end, strategy)
# summary 包含：ticker, total_return, sharpe, max_drawdown, equity_curve

engine.plot(summary, "策略名称")          # 保存 PNG 到 backtest/plots/
engine.save_results(summary, "策略名称")  # 保存 CSV 到 backtest/results/
```

| 指标 | 说明 |
|------|------|
| 总收益率 | `(期末 / 期初 - 1) × 100%` |
| 夏普比率 | 年化（252 交易日），基于日收益率 |
| 最大回撤 | 历史峰值到谷底的最大跌幅 |

---

## 趋势识别（`trend/`）

详见 [trend/README.md](trend/README.md)。

识别最近 N 根 K 线的**市场状态**，与买卖信号独立：

```python
from trend import detect_trend

result = detect_trend(bars, n=30)
result.trend.name      # UPTREND / DOWNTREND / OSCILLATING / FLAT / OTHER / ABNORMAL
result.intensity.name  # NORMAL / STRONG / EXTREME
result.confidence      # 0.0–1.0
result.duration        # 当前状态已持续的 bar 数
result.description     # 人类可读的详情字符串
```

---

## 快速开始

```python
from data.loader import load
from signals.price_signals import is_increase, is_decrease
from strategies.multi_signal import MultiSignalStrategy
from backtest import engine

df = load("AAPL", "2020-01-01", "2024-12-31")

strategy = MultiSignalStrategy(
    df,
    buy_signals=[is_increase],
    sell_signals=[is_decrease],
    buy_threshold=1,
    sell_threshold=1,
)

summary = engine.run("AAPL", "2020-01-01", "2024-12-31", strategy)
print(f"总收益率：{summary['total_return']:.2f}%")
print(f"夏普比率：{summary['sharpe']:.2f}")
print(f"最大回撤：{summary['max_drawdown']:.2f}%")

engine.plot(summary, "MultiSignal")
engine.save_results(summary, "MultiSignal")
```

或直接运行：

```bash
python run_example.py
```

---

## 扩展指南

- **新增信号**：在 `signals/` 下添加 `fn(close: pd.Series) -> pd.Series`（返回 0/1）
- **新增策略**：继承 `BaseStrategy`，实现 `run()`，仓位操作通过 `buy()`/`sell()`/`take_profit()`/`stop_loss()`
- **新增数据源**：在 `data/` 下添加返回小写 OHLCV 列的 DataFrame 的 loader
- **新增趋势类型**：在 `trend/base.py` 的 `TrendType` 中添加枚举值，新建检测文件，注册到 `_DEFAULT_PIPELINE`
