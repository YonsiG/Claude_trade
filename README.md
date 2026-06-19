# Claude Trade Expert

A modular quantitative trading framework for signal-based strategy research and backtesting.

---

## Project Structure

```
claude_trade_expert/
├── data/           # Data downloading and caching
├── signals/        # Atomic signal functions (buy/sell indicators)
├── trend/          # Market regime / trend detection
├── tools/          # Position management primitives (buy, sell)
├── strategies/     # Strategy logic combining signals
├── backtest/       # Backtesting engine, metrics, plots
└── run_example.py  # End-to-end example
```

---

## Data (`data/`)

**`loader.py`** downloads OHLCV data from yfinance and caches it locally under `data/raw/`.

```python
from data.loader import load

df = load("AAPL", "2020-01-01", "2024-12-31")
```

- First call downloads and saves to CSV.
- Subsequent calls load from cache automatically.
- Columns are normalized to lowercase: `open`, `high`, `low`, `close`, `volume`.

---

## Signals (`signals/`)

Each signal function takes a `pd.Series` of closing prices and returns a `pd.Series` of `0` or `1` for every date.

| Function | Returns 1 when... |
|----------|-------------------|
| `is_increase(close, n=3)` | Price has risen for `n` consecutive days |
| `is_decrease(close, n=3)` | Price has fallen for `n` consecutive days |
| `is_consolidation(close, n=5, threshold=0.02)` | Price range over `n` days is within `threshold` (e.g. 2%) |

To add a new signal, define a function with the signature `fn(close: pd.Series) -> pd.Series` in `signals/price_signals.py` (or a new file under `signals/`).

---

## Tools (`tools/`)

**`trade.py`** provides two stateful position management functions. Both operate on a shared `state` dict with keys `cash` and `shares`.

```python
state = {"cash": 100_000, "shares": 0.0}

buy(state, price)   # converts all cash to shares; no-op if already holding
sell(state, price)  # liquidates all shares to cash; no-op if flat
```

These are the only two functions that modify positions. All strategies must go through them.

---

## Strategies (`strategies/`)

### `BaseStrategy` (abstract)

All strategies inherit from `BaseStrategy`:

```python
class BaseStrategy(ABC):
    def __init__(self, df: pd.DataFrame, initial_capital: float = 100_000): ...
    def run(self) -> pd.Series: ...  # must return equity curve indexed by date
```

### `MultiSignalStrategy`

Combines multiple buy signals and sell signals independently.

```python
from strategies.multi_signal import MultiSignalStrategy

strategy = MultiSignalStrategy(
    df,
    buy_signals=[is_increase],   # list of signal functions
    sell_signals=[is_decrease],
    buy_threshold=1,             # buy when >= this many buy signals fire
    sell_threshold=1,            # sell when >= this many sell signals fire
    initial_capital=100_000,
)
```

**Logic per bar:**
1. Compute buy score = sum of all buy signal values at that date.
2. Compute sell score = sum of all sell signal values at that date.
3. If `buy_score >= buy_threshold` → call `buy()`.
4. Else if `sell_score >= sell_threshold` → call `sell()`.
5. Record current portfolio value (cash + shares × price) as equity.

Buy and sell conditions are checked in that order; buy takes priority.

---

## Backtest (`backtest/`)

**`engine.py`** runs a strategy and computes performance metrics.

```python
from backtest import engine

summary = engine.run(ticker, start, end, strategy)
# summary keys: ticker, total_return, sharpe, max_drawdown, equity_curve

engine.plot(summary, "MyStrategy")         # saves PNG to backtest/plots/
engine.save_results(summary, "MyStrategy") # saves CSV to backtest/results/
```

Metrics computed:

| Metric | Description |
|--------|-------------|
| Total Return | `(final / initial - 1) × 100` % |
| Sharpe Ratio | Annualized (252 trading days), using daily returns |
| Max Drawdown | Maximum peak-to-trough decline as % |

---

## Quick Start

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
print(f"Total Return : {summary['total_return']:.2f}%")
print(f"Sharpe Ratio : {summary['sharpe']:.2f}")
print(f"Max Drawdown : {summary['max_drawdown']:.2f}%")

engine.plot(summary, "MultiSignal")
engine.save_results(summary, "MultiSignal")
```

Or just run:

```bash
python run_example.py
```

---


## Trend Detection (`trend/`)

Identifies the **market regime** of a given price series, independent of any buy/sell signal.
Unlike signals, trend functions do not output 0/1 per bar — they classify the overall character
of the last N bars as a single trend type.

### Trend Types

| Value | Name | Description |
|-------|------|-------------|
| 1 | `UPTREND` | Price rising (linear regression slope > 0) |
| 2 | `DOWNTREND` | Price falling (linear regression slope < 0) |
| 3 | `OSCILLATING` | Price oscillating around mean with meaningful ATR |
| 4 | `FLAT` | Very tight range, low volatility |
| 5 | `OTHER` | No dominant trend detected |
| 6 | `ABNORMAL` | Any trend that reaches EXTREME intensity |

### Intensity

Each trend (except FLAT and OTHER) carries an intensity level:

| Value | Name | Uptrend/Downtrend threshold | Oscillating threshold |
|-------|------|-----------------------------|-----------------------|
| 1 | `NORMAL` | slope < 0.3%/bar | ATR/price < 3% |
| 2 | `STRONG` | slope 0.3%–0.8%/bar | ATR/price 3%–6% |
| 3 | `EXTREME` | slope > 0.8%/bar | ATR/price > 6% |

When intensity reaches **EXTREME**, the trend is automatically promoted to `ABNORMAL` (type 6).
`result.base_trend` preserves the original trend type (e.g. `UPTREND`) for downstream use.
result.duration            # bars going backwards this trend holds; 0 = not computed

### Usage

```python
from trend import detect_trend, TrendType, TrendIntensity

result = detect_trend(bars, n=30)   # last 30 bars of OHLCV DataFrame
result = detect_trend(bars, compute_duration=False)  # skip duration for speed

int(result.trend)          # 1–6
result.trend.name          # UPTREND, ABNORMAL, etc.
result.intensity.name      # NORMAL, STRONG, EXTREME
result.confidence          # 0.0–1.0
result.is_abnormal         # True when intensity == EXTREME
result.base_trend          # original trend when ABNORMAL, else None
result.description         # human-readable detail string
```

### File Layout

| File | Role |
|------|------|
| `base.py` | `TrendType`, `TrendIntensity`, `TrendResult`, `TrendDetector` protocol |
| `uptrend.py` | Linear-regression slope detector |
| `downtrend.py` | Linear-regression slope detector (negative) |
| `oscillating.py` | Mean-crossing + ATR detector |
| `flat.py` | Tight-range detector |
| `other.py` | Fallback |
| `abnormal.py` | `wrap_as_abnormal()` helper |
| `detector.py` | `detect_trend()` — runs the pipeline, returns best match |

### Extending

To add a new trend type (e.g. `BREAKOUT = 7`):
1. Add the value to `TrendType` in `base.py`.
2. Create `trend/breakout.py` with a `detect_breakout(bars) -> TrendResult` function.
3. Append `(detect_breakout, {})` to `_DEFAULT_PIPELINE` in `detector.py`.

You can also pass a fully custom pipeline at call time:
```python
from trend.uptrend import detect_uptrend
result = detect_trend(bars, pipeline=[(detect_uptrend, {"slope_min": 0.001})])
```

---
## Extending the Framework

- **New signal:** add a function `fn(close: pd.Series) -> pd.Series` returning 0/1 in `signals/`.
- **New strategy:** subclass `BaseStrategy`, implement `run()`, use `buy()`/`sell()` from `tools/trade.py`.
- **New data source:** add a loader in `data/` that returns a DataFrame with lowercase OHLCV columns.
- **New trend type:** add to `TrendType` in `trend/base.py`, create a detector file, register in `_DEFAULT_PIPELINE`. See the Trend Detection section above.
