# Claude Trade Expert

A modular quantitative trading framework for signal-based strategy research and backtesting.

---

## Project Structure

```
claude_trade_expert/
├── data/           # Data downloading and caching
├── signals/        # Atomic signal functions (buy/sell indicators)
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

## Extending the Framework

- **New signal:** add a function `fn(close: pd.Series) -> pd.Series` returning 0/1 in `signals/`.
- **New strategy:** subclass `BaseStrategy`, implement `run()`, use `buy()`/`sell()` from `tools/trade.py`.
- **New data source:** add a loader in `data/` that returns a DataFrame with lowercase OHLCV columns.
