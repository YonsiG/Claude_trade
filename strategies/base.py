import pandas as pd
from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    def __init__(self, df: pd.DataFrame, initial_capital: float = 100_000):
        self.df = df.copy()
        self.initial_capital = initial_capital

    @abstractmethod
    def run(self) -> pd.Series:
        """Execute strategy on self.df. Returns equity curve indexed by date."""
        ...
