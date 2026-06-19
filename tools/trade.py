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


def force_close(state: dict, current_price: float, dt,
                futures: bool = False, multiplier: float = 1.0) -> dict | None:
    """
    Check if total equity has reached zero (or gone negative).
    If so, liquidate all positions and return a bust record.
    Otherwise return None.

    Total equity:
        long  shares: cash + shares * price [* multiplier]
        short shares: cash - abs(shares) * price [* multiplier] (margin model)

    The returned dict can be stored by the strategy and passed into the
    backtest summary without affecting the equity curve or plot logic.
    """
    factor = current_price * multiplier if futures else current_price
    if state["shares"] >= 0:
        equity = state["cash"] + state["shares"] * factor
    else:
        equity = state["cash"] - abs(state["shares"]) * factor

    if equity > 0:
        return None

    # Liquidate
    if state["shares"] > 0:
        sell(state, current_price, futures, multiplier)
    elif state["shares"] < 0:
        cover(state, current_price, futures, multiplier)

    return {
        "status": "Completely lost all money",
        "datetime": str(dt),
    }
