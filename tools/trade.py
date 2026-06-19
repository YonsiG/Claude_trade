"""Position management primitives used by strategies.

state dict schema:
    cash   (float): available cash
    shares (float): signed position — positive = long, negative = short (lots for futures)
"""
import math


def buy(state: dict, price: float, futures: bool = False, multiplier: float = 1.0) -> None:
    """
    Open long position with all available cash. No-op if already long.
    If short, covers first before going long.

    futures=True : integer lots, position value = lots * price * multiplier
    futures=False: fractional shares, multiplier ignored
    """
    if state["shares"] < 0:
        cover(state, price, futures, multiplier)
    if state["cash"] <= 0 or state["shares"] > 0:
        return
    if futures:
        lots = math.floor(state["cash"] / (price * multiplier))
        if lots <= 0:
            return
        state["shares"] = lots
        state["cash"] -= lots * price * multiplier
    else:
        state["shares"] = state["cash"] / price
        state["cash"] = 0.0


def sell(state: dict, price: float, futures: bool = False, multiplier: float = 1.0) -> None:
    """Liquidate all long shares/lots to cash. No-op if flat or short."""
    if state["shares"] <= 0:
        return
    if futures:
        state["cash"] += state["shares"] * price * multiplier
    else:
        state["cash"] += state["shares"] * price
    state["shares"] = 0.0


def short(state: dict, price: float, futures: bool = False, multiplier: float = 1.0) -> None:
    """
    Open short position sized by available cash. No-op if already short.
    If long, sells first before going short.

    Borrows shares/lots and sells them: cash increases, shares goes negative.
    futures=True : integer lots
    futures=False: fractional shares
    """
    if state["shares"] > 0:
        sell(state, price, futures, multiplier)
    if state["cash"] <= 0 or state["shares"] < 0:
        return
    if futures:
        lots = math.floor(state["cash"] / (price * multiplier))
        if lots <= 0:
            return
        state["shares"] = -lots
        state["cash"] += lots * price * multiplier
    else:
        state["shares"] = -(state["cash"] / price)
        state["cash"] += abs(state["shares"]) * price


def cover(state: dict, price: float, futures: bool = False, multiplier: float = 1.0) -> None:
    """Buy back all short shares/lots to close position. No-op if flat or long."""
    if state["shares"] >= 0:
        return
    lots = abs(state["shares"])
    if futures:
        state["cash"] -= lots * price * multiplier
    else:
        state["cash"] -= lots * price
    state["shares"] = 0.0


def take_profit(state: dict, current_price: float, tp_price: float,
                ratio: float = 1.0, futures: bool = False, multiplier: float = 1.0) -> bool:
    """
    Close `ratio` of position when profit target is hit.
    Long : triggers when current_price >= tp_price.
    Short: triggers when current_price <= tp_price.
    futures=True: lots after floor must be >= 1.
    Returns True if triggered.
    """
    if state["shares"] == 0:
        return False

    if state["shares"] > 0:
        if current_price < tp_price:
            return False
        if futures:
            lots = math.floor(state["shares"] * ratio)
            if lots <= 0:
                return False
            state["cash"] += lots * current_price * multiplier
            state["shares"] -= lots
        else:
            qty = state["shares"] * ratio
            state["cash"] += qty * current_price
            state["shares"] -= qty

    else:  # short
        if current_price > tp_price:
            return False
        if futures:
            lots = math.floor(abs(state["shares"]) * ratio)
            if lots <= 0:
                return False
            state["cash"] -= lots * current_price * multiplier
            state["shares"] += lots
        else:
            qty = abs(state["shares"]) * ratio
            state["cash"] -= qty * current_price
            state["shares"] += qty

    return True


def stop_loss(state: dict, current_price: float, sl_price: float,
              ratio: float = 1.0, futures: bool = False, multiplier: float = 1.0) -> bool:
    """
    Close `ratio` of position when stop is hit.
    Long : triggers when current_price <= sl_price.
    Short: triggers when current_price >= sl_price.
    futures=True: lots after floor must be >= 1.
    Returns True if triggered.
    """
    if state["shares"] == 0:
        return False

    if state["shares"] > 0:
        if current_price > sl_price:
            return False
        if futures:
            lots = math.floor(state["shares"] * ratio)
            if lots <= 0:
                return False
            state["cash"] += lots * current_price * multiplier
            state["shares"] -= lots
        else:
            qty = state["shares"] * ratio
            state["cash"] += qty * current_price
            state["shares"] -= qty

    else:  # short
        if current_price < sl_price:
            return False
        if futures:
            lots = math.floor(abs(state["shares"]) * ratio)
            if lots <= 0:
                return False
            state["cash"] -= lots * current_price * multiplier
            state["shares"] += lots
        else:
            qty = abs(state["shares"]) * ratio
            state["cash"] -= qty * current_price
            state["shares"] += qty

    return True
