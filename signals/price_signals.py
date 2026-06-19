import pandas as pd


def is_increase(close: pd.Series, n: int = 3) -> pd.Series:
    """Returns 1 if price has risen for n consecutive days, else 0."""
    result = pd.Series(0, index=close.index)
    for i in range(n, len(close)):
        if all(close.iloc[i - k] > close.iloc[i - k - 1] for k in range(n)):
            result.iloc[i] = 1
    return result


def is_decrease(close: pd.Series, n: int = 3) -> pd.Series:
    """Returns 1 if price has fallen for n consecutive days, else 0."""
    result = pd.Series(0, index=close.index)
    for i in range(n, len(close)):
        if all(close.iloc[i - k] < close.iloc[i - k - 1] for k in range(n)):
            result.iloc[i] = 1
    return result


def is_consolidation(close: pd.Series, n: int = 5, threshold: float = 0.02) -> pd.Series:
    """Returns 1 if price range over n days is within threshold (e.g. 2%), else 0."""
    rolling_max = close.rolling(n).max()
    rolling_min = close.rolling(n).min()
    range_pct = (rolling_max - rolling_min) / rolling_min
    return (range_pct <= threshold).astype(int)
