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
├── backtest/       # 回测引擎、组合层、绩效指标、图表
├── tests/          # 回归测试（pytest）
└── run_example.py  # 端到端示例
```

## 安装与自检

```bash
pip install -r requirements.txt
pytest                     # 81 项回归测试
pytest -m "not data"       # 只跑合成数据，离线可用，<1 秒
```

改动任何仓位、离场或判定逻辑后先跑一遍 `pytest`。详见
[tests/README.md](tests/README.md) —— 那里的用例几乎每一条都对应一个
曾经真实存在过的**静默** bug：不报错，只是悄悄给出错误的数字。

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

每个信号函数接收 OHLCV `pd.DataFrame`，返回 `Signal(entry, size)`：

```python
from signals.base import Signal

class Signal(NamedTuple):
    entry: pd.Series   # -1 / 0 / +1，开仓方向脉冲
    size:  pd.Series   # [0, 1]，该次开仓投入的资金比例
```

**关键约定**：`entry` 是**事件**，只在形态成立的那一根 bar 非 0。
`entry == 0` 表示"无事发生"，**不是**"应该空仓"——持仓由策略的止损/止盈/反向信号决定何时离场。

| 函数 | 说明 |
|------|------|
| `umbrella(df, ...)` | 伞形线：下跌中的锤子线做多；上升中的上吊线（次日确认）做空 |
| `engulfing(df, ...)` | 吞噬形态：下跌中的看涨吞噬做多；上升中的看跌吞噬做空 |

新增信号：在 `signals/` 下定义 `fn(df: pd.DataFrame) -> Signal` 即可。用
`Signal.blank(df.index)` 起手逐 bar 填写，或用 `Signal.from_strength(s)`
把已有的 [-1, 1] 连续序列拆成方向与仓位。

---

## 仓位工具（`tools/`）

详见 [tools/README.md](tools/README.md)。

`trade.py` 提供有状态的仓位管理函数，共享 `state` 字典（`cash` + `shares`，`shares < 0` 为空头）。支持股票（小数份额）和期货（整数手数 × 合约乘数 × 保证金）两种模式。

**期货保证金**：`buy()` / `short()` 接受 `margin_rate`，一手占用资金 = `price × multiplier × margin_rate`。
现金仍按全额名义价值扣减，杠杆持仓下 `cash` 变为负数（即借入的保证金头寸），
权益 `cash + shares × price × multiplier` 与杠杆收益率自动正确。
`margin_rate=1.0`（默认）= 全额开仓 = 无杠杆。杠杆开仓**必须**配合 `margin_call()`。

| 函数 | 说明 |
|------|------|
| `buy(state, price, ..., add=False)` | 做多；持空时先平空。`add=True` 用剩余可用资金加仓 |
| `free_capital(state, price, ...)` | 还能开仓的资金 = 总权益 − 已占用保证金 |
| `sell(state, price, ..., ratio=1.0)` | 平多，可指定比例 |
| `short(state, price, ..., add=False)` | 做空；持多时先平多。`add=True` 同上 |
| `cover(state, price, ..., ratio=1.0)` | 平空，可指定比例 |
| `take_profit(..., ratio=1.0)` | 多空双向止盈，返回 `bool` |
| `stop_loss(..., ratio=1.0)` | 多空双向止损，返回 `bool` |
| `trailing_take_profit(..., window, x)` | 浮动止盈：从峰值回撤 x 则平仓，返回 `bool` |
| `margin_call(..., margin_rate, maintenance=1.0)` | 权益跌破所需保证金则强平，返回记录 dict 或 `None`；强平后仍可继续交易 |
| `force_close(state, price, dt, ...)` | 总资产归零时强制平仓，返回 bust dict 或 `None` |

---

## 期货合约规格（`data/futures_spec.py`）

**每个品种一手对应的乘数完全不同**，用统一的 `multiplier=1.0` 回测期货，每个品种都是错的：

| 品种 | 乘数 | 保守保证金 | 一手名义价值 |
|------|------|-----------|-------------|
| AU 黄金 | 1000 克 | 26% | 价格 × 1000 |
| AG 白银 | 15 千克 | 30% | 价格 × 15 |
| RB 螺纹钢 | 10 吨 | 20% | 价格 × 10 |
| LH 生猪 | 16 吨 | 11% | 价格 × 16 |
| CU 铜 | 5 吨 | 20% | 价格 × 5 |
| T 10年期国债 | 10000 | 2.5% | 价格 × 10000 |

离线规格表 `data/futures_spec.csv` 由两个独立来源合并，**保证金取 max 作保守值**
（交易所规则表是下限，券商实际执行值有时更高，两边互有高低）：

```bash
python data/futures_spec.py --refresh          # 联网刷新（非交易日自动回溯）
python data/futures_spec.py --show RB0 AU0     # 查看指定品种
python data/futures_spec.py --show             # 打印全表（88 个品种）
```

```python
from data.futures_spec import spec
s = spec("RB0")     # FuturesSpec(multiplier=10, margin_rate=0.20, tick_size=1.0, ...)
```

---

## 策略模块（`strategies/`）

### `BaseStrategy`（抽象基类）

```python
class BaseStrategy(ABC):
    def __init__(self, df: pd.DataFrame, initial_capital: float = 100_000): ...
    def run(self) -> pd.Series: ...        # 返回以日期为索引的资金曲线
    def trade_log(self) -> pd.DataFrame: ...  # 一行一笔往返交易
```

子类在开仓/平仓时调用 `_log_entry()` / `_log_exit()`，`trade_log()` 即可给出
每笔交易的进出场时间、方向、成交价、持仓 bar 数、盈亏和离场原因。
盈亏按「开仓前总权益」与「平仓后总权益」之差计算，手续费自动含在内。

### `SingleSignalStrategy`

把单个信号的开仓脉冲转成持仓，并负责离场：

```python
from strategies.single_signal import SingleSignalStrategy
from signals.pattern import engulfing

strategy = SingleSignalStrategy(
    df,
    signal_fn=engulfing,
    trail_pct=0.30,          # 自峰值回撤 30% 移动止盈；None 关闭
    sl_pct=0.10,             # 自开仓价反向 10% 固定止损；None 关闭
    exit_on_reverse=True,    # 收到反向 entry 时反手
    intrabar_stops=True,     # 用 high/low 判断止损触发，触发价成交
    execution="close",       # 或 "next_open"：信号挂到下一根开盘成交
    size_by_strength=True,   # 用 Signal.size 定仓位；False 则一律满仓
    initial_capital=100_000,
)

# 期货：传 ticker 自动查合约乘数与保证金率
strategy = SingleSignalStrategy(
    df, signal_fn=engulfing,
    futures=True, ticker="RB0",   # -> multiplier=10, margin_rate=0.20
    maintenance=1.0,              # 强平线，1.0 = 权益跌破开仓保证金即强平（保守）
    initial_capital=100_000,
)
```

`futures=True` 时必须给出规格：传 `ticker` 自动查表，或同时显式传
`multiplier` 和 `margin_rate`（美盘期货 `GC=F` 等不在表里，只能显式传）。
**不给规格会直接报错**，而不是沉默地按 `multiplier=1.0` 全额开仓。

离场条件按顺序检查：移动止盈 → 固定止损 → 反向信号。
止损与移动止盈都在价格下方（多头）时，取**较高**者先触发；
开盘已跳空越过触发价则按开盘价成交。
被止损的那一根 bar 不会用同一个信号原价买回（`allow_same_bar_reentry=True` 可放开）。
权益归零时通过 `force_close()` 强制平仓并在 `strategy.bust` 留痕。

**金字塔加仓**（`pyramid=True`，默认关闭）：持仓期间收到同向信号时用剩余
可用资金追加，最多 `max_adds` 次。加仓后止损基准改用**加权平均开仓价**——
先建的底仓不该因为后加的仓位而被按原价止损。流水里 `n_adds` 记录加仓次数，
一次往返仍是一条记录。

---

## 回测模块（`backtest/`）

```python
from backtest import engine

summary = engine.run(ticker, start, end, strategy, rf=0.04)

print(engine.report(summary))             # 策略 vs 买入持有 的对照表
engine.plot(summary, "策略名称")          # 保存 PNG 到 backtest/plots/
engine.save_results(summary, "策略名称")  # 保存指标 CSV + 交易流水 CSV
```

`summary` 中的曲线与流水：`equity_curve`、`benchmark_curve`、`trades`。

**每个策略指标都配一个 `bench_` 前缀的买入持有对照值。** 基准为首根 bar 收盘价
满仓买入并持有到最后，使用与策略相同的手续费参数，保证可比。

| 指标 | 说明 |
|------|------|
| 总收益率 / 年化收益 | 年化按索引实际跨越的自然日数折算 |
| 夏普比率 | 年化（252 交易日），可通过 `rf` 传入无风险利率 |
| 最大回撤 / Calmar | Calmar = 年化收益 / \|最大回撤\| |
| 交易次数 / 胜率 / 盈亏比 | 来自交易流水；盈亏比 = 总盈利 / 总亏损 |
| 平均持仓 bar 数 / 平均盈亏 | 用于判断策略实际的持仓周期是否符合预期 |

期货基准沿用策略的保证金率（同杠杆才可比），并同样受强平约束——
否则杠杆下的买入持有会算出负权益，夏普和回撤全部失真。

`report()` 会显式提示这几种情况，避免把它们误读成"低风险"：

- **零成交**：信号从未触发
- **资金不足以开出一手**：会同时给出一手所需保证金和信号实际可用资金
  （黄金一手要 20 万保证金，十万本金根本开不出来）
- **保证金不足被强平 N 次**
- **权益归零，彻底出局**

### 组合回测（`backtest/portfolio.py`）

把资金按权重切成若干「子仓」，每个子仓独立跑一个策略，再把资金曲线相加：

```python
from backtest.portfolio import run_portfolio, report_portfolio

sleeves = {
    "AAPL": SingleSignalStrategy(df_aapl, engulfing),
    "RB0":  SingleSignalStrategy(df_rb, engulfing, futures=True, ticker="RB0"),
}
summary = run_portfolio(sleeves, capital=200_000)   # 不给 weights 则等权
print(report_portfolio(summary))                    # 组合汇总 + 子仓明细
```

命令行：

```bash
python run_example.py --tickers AAPL,NVDA,TSLA --capital 300000
python run_example.py --tickers RB0,M0,LH0 --source ak --futures --capital 300000
```

**范围**：这是「资金分配 + 独立执行 + 结果聚合」，不是截面选股，也不做子仓间
的风险预算或再平衡。每个子仓拿自己那份钱各跑各的，互不调拨。

美股和国内期货交易日不同，组合取所有子仓日期的**并集**并前向填充；
子仓开始交易之前的日期按其初始资金计入。

---

### 训练集/验证集切分（`backtest/split.py`）

调参之前先把历史切成两半：较早（离现在更远）的一段用来找参数（训练/优化集），
较晚的一段只用来最后验证一次（验证/测试集）。在同一段数据上反复调参、再拿
同一段数据夸自己表现好，是最常见也最隐蔽的过拟合。

```bash
python run_example.py --split                              # 默认 4:1，较早一段为训练集
python run_example.py --split --split-ratio 0.7             # 自定义比例
python run_example.py --tickers RB0,M0 --source ak --futures --split   # 组合同样支持

# 显式指定两段区间（不要求首尾相接）
python run_example.py --train-start 2021-01-01 --train-end 2023-12-31 \
                      --test-start  2024-01-01 --test-end  2026-07-31
```

按 **bar 数**切分（不是日历天数）——非交易日在日历上分布不均，日历切分
算出来的实际 bar 比例会偏离预期。两组各自独立回测（都从 `initial_capital`
起算，不是训练集打底接着跑验证集），输出与保存都按两组分开，文件名带
`_train` / `_test` 后缀。

调参只应该在训练集的结果上做；验证集理想情况下只跑一次——跑多次再回头
调参，就等于把验证集也变成了训练集的一部分，代码挡不住这件事，靠自觉。

```python
from backtest.split import resolve_split

r = resolve_split("AAPL", "2021-01-01", "2026-07-31", "yf", "1d", ratio=0.8)
r.train_df, r.test_df           # 切好的两段 DataFrame
r.train_range, r.test_range     # 各自的 (start, end) 字符串
```

---

## 趋势识别（`trend/`）

详见 [trend/README.md](trend/README.md)。

识别最近 N 根 K 线的**市场状态**，与买卖信号独立：

```python
from trend import detect_trend

result = detect_trend(bars, n=30)
result.trend.name      # UPTREND / DOWNTREND / OSCILLATING / FLAT / OTHER / ABNORMAL
result.intensity.name  # NORMAL / STRONG / EXTREME
result.confidence      # 合格时 [0.5, 1.0]；OTHER 固定为 0.0
result.duration        # 趋势影响范围（口径见 trend/README.md）
result.description     # 人类可读的详情字符串
```

---

## 快速开始

```python
from data.loader import load
from signals.pattern import engulfing
from strategies.single_signal import SingleSignalStrategy
from backtest import engine

df = load("AAPL", "2021-08-02", "2026-07-30")

strategy = SingleSignalStrategy(
    df,
    signal_fn=engulfing,
    trail_pct=0.30,
    sl_pct=0.10,
)

summary = engine.run("AAPL", "2021-08-02", "2026-07-30", strategy)
print(engine.report(summary))
print(summary["trades"])          # 交易流水

engine.plot(summary, "engulfing")
engine.save_results(summary, "engulfing")
```

或直接运行：

```bash
python run_example.py
```

---

## 扩展指南

- **新增信号**：在 `signals/` 下添加 `fn(df: pd.DataFrame) -> Signal`（`entry` 取 -1/0/+1，`size` 取 [0,1]）
- **新增策略**：继承 `BaseStrategy`，实现 `run()`；仓位操作通过 `buy()`/`sell()`/`short()`/`cover()`，
  开平仓时调用 `_log_entry()`/`_log_exit()` 以便产出交易流水
- **多标的**：用 `backtest.portfolio.run_portfolio()` 把多个策略实例组成组合
- **新增数据源**：在 `data/` 下添加返回小写 OHLCV 列的 DataFrame 的 loader
- **新增趋势类型**：在 `trend/base.py` 的 `TrendType` 中添加枚举值，新建检测文件，注册到 `_DEFAULT_PIPELINE`
