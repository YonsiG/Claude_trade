import pandas as pd
from .base import BaseStrategy
from tools.trade import buy, sell


class MultiSignalStrategy(BaseStrategy):
    """Buy when >= buy_threshold buy signals fire; sell when >= sell_threshold sell signals fire."""

    def __init__(self, df: pd.DataFrame,
                 buy_signals: list, sell_signals: list,
                 buy_threshold: int = 1, sell_threshold: int = 1,
                 initial_capital: float = 100_000):
        super().__init__(df, initial_capital)
        self.buy_signals = buy_signals
        self.sell_signals = sell_signals
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

    def run(self) -> pd.Series:
        close = self.df["close"]
        buy_score = sum(fn(close) for fn in self.buy_signals)
        sell_score = sum(fn(close) for fn in self.sell_signals)

        state = {"cash": self.initial_capital, "shares": 0.0}
        equity = []

        for date, price in close.items():
            if buy_score.loc[date] >= self.buy_threshold:
                buy(state, price)
            elif sell_score.loc[date] >= self.sell_threshold:
                sell(state, price)
            equity.append(state["cash"] + state["shares"] * price)

        return pd.Series(equity, index=close.index, name="equity")
