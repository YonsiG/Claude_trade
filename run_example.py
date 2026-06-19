from data.loader import load
from signals.price_signals import is_increase, is_decrease
from strategies.multi_signal import MultiSignalStrategy
from backtest import engine

TICKER = "AAPL"
START  = "2020-01-01"
END    = "2024-12-31"

df = load(TICKER, START, END)

strategy = MultiSignalStrategy(
    df,
    buy_signals=[is_increase],
    sell_signals=[is_decrease],
    buy_threshold=1,
    sell_threshold=1,
)

summary = engine.run(TICKER, START, END, strategy)
print(f"Total Return : {summary['total_return']:.2f}%")
print(f"Sharpe Ratio : {summary['sharpe']:.2f}")
print(f"Max Drawdown : {summary['max_drawdown']:.2f}%")

engine.plot(summary, "MultiSignal")
engine.save_results(summary, "MultiSignal")
