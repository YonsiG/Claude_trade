import pandas as pd


def is_umbrella(df: pd.DataFrame, shadow_ratio: float = 2.0, upper_ratio: float = 0.1) -> pd.Series:
    """True where bar has umbrella structure: lower shadow >= body * shadow_ratio, upper shadow <= body * upper_ratio."""
    body = (df["close"] - df["open"]).abs()
    lower_shadow = df[["open", "close"]].min(axis=1) - df["low"]
    upper_shadow = df["high"] - df[["open", "close"]].max(axis=1)
    return (lower_shadow >= body * shadow_ratio) & (upper_shadow <= body * upper_ratio)


def is_doji(df: pd.DataFrame, body_ratio: float = 0.1) -> pd.Series:
    """True where bar has doji structure: body <= body_ratio * total range."""
    body = (df["close"] - df["open"]).abs()
    total_range = df["high"] - df["low"]
    return body <= total_range * body_ratio
