import pandas as pd
from .base import BaseStrategy
from tools.trade import buy, sell, short, cover, trailing_take_profit, capital_stop_loss


class SingleSignalStrategy(BaseStrategy):
    """
    Trades a single continuous signal in [-1, 1].

    signal > 0 : go long  with position size = signal
    signal < 0 : go short with position size = |signal|
    signal == 0: close any open position and stay flat

    Exit conditions (checked every bar while in position):
      - Trailing take-profit: close when price retraces trail_pct from peak
      - Capital stop-loss   : close when price moves sl_pct against entry
    """

    def __init__(self, df: pd.DataFrame,
                 signal_fn,
                 trail_pct: float = 0.30,
                 sl_pct: float = 0.10,
                 futures: bool = False,
                 multiplier: float = 1.0,
                 initial_capital: float = 100_000):
        super().__init__(df, initial_capital)
        self.signal_fn = signal_fn
        self.trail_pct = trail_pct
        self.sl_pct = sl_pct
        self.futures = futures
        self.multiplier = multiplier

    def run(self) -> pd.Series:
        signal = self.signal_fn(self.df)
        close = self.df["close"]

        state = {"cash": self.initial_capital, "shares": 0.0}
        entry_price = None
        equity = []
        kwargs = {"futures": self.futures, "multiplier": self.multiplier}

        for date, price in close.items():
            sig = signal.loc[date]

            # 1. 检查止盈止损（持仓中才触发）
            if state["shares"] \!= 0:
                if trailing_take_profit(state, price, self.trail_pct, **kwargs):
                    entry_price = None
                elif capital_stop_loss(state, price, entry_price, self.sl_pct, **kwargs):
                    entry_price = None

            # 2. 信号触发开仓或平仓
            if state["shares"] == 0:
                if sig > 0:
                    buy(state, price, **kwargs)
                    entry_price = price
                elif sig < 0:
                    short(state, price, **kwargs)
                    entry_price = price
            elif sig == 0:
                if state["shares"] > 0:
                    sell(state, price, **kwargs)
                elif state["shares"] < 0:
                    cover(state, price, **kwargs)
                entry_price = None

            # 3. 计算当日权益
            if self.futures:
                equity.append(state["cash"] + state["shares"] * price * self.multiplier)
            else:
                equity.append(state["cash"] + state["shares"] * price)

        return pd.Series(equity, index=close.index, name="equity")
