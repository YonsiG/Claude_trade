import pandas as pd
from .single_bar import is_doji


def is_engulfing(df: pd.DataFrame, doji_ratio: float = 0.1) -> pd.Series:
    """
    True where current bar engulfs the previous bar's body.
    Conditions:
      - Current body covers previous body (low_body2 <= low_body1, high_body2 >= high_body1)
      - Current color is opposite to previous, unless previous is a doji
    Returns a boolean Series aligned to df.index (False at index 0).
    """
    body_low = df[["open", "close"]].min(axis=1)
    body_high = df[["open", "close"]].max(axis=1)
    bullish = df["close"] > df["open"]
    doji = is_doji(df, doji_ratio)

    covers = (body_low <= body_low.shift(1)) & (body_high >= body_high.shift(1))
    opposite_color = bullish \!= bullish.shift(1)
    prev_doji = doji.shift(1).fillna(False)

    return covers & (opposite_color | prev_doji)
