import pandas as pd
from .base import BaseStrategy
from tools.trade import buy, sell, short, cover


class SingleSignalStrategy(BaseStrategy):
    """
    Trades a single continuous signal in [-1, 1].

    signal > 0 : go long  with position size = signal
    signal < 0 : go short with position size = |signal|
    signal == 0: close any open position and stay flat
    """

    def __init__(self, df: pd.DataFrame,
                 signal_fn,
                 futures: bool = False,
                 multiplier: float = 1.0,
                 initial_capital: float = 100_000):
        super().__init__(df, initial_capital)
        self.signal_fn = signal_fn
        self.futures = futures
        self.multiplier = multiplier

    def run(self) -> pd.Series:
        signal = self.signal_fn(self.df)
        close = self.df["close"]

        state = {"cash": self.initial_capital, "shares": 0.0}
        equity = []

        for date, price in close.items():
            sig = signal.loc[date]
            kwargs = {"futures": self.futures, "multiplier": self.multiplier}

            if sig > 0:
                if state["shares"] <= 0:   # flat or short → go long
                    buy(state, price, **kwargs)
            elif sig < 0:
                if state["shares"] >= 0:   # flat or long → go short
                    short(state, price, **kwargs)
            else:
                if state["shares"] > 0:
                    sell(state, price, **kwargs)
                elif state["shares"] < 0:
                    cover(state, price, **kwargs)

            # equity = cash + market value of position
            if self.futures:
                equity.append(state["cash"] + state["shares"] * price * self.multiplier)
            else:
                equity.append(state["cash"] + state["shares"] * price)

        return pd.Series(equity, index=close.index, name="equity")
