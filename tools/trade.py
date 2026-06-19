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


def sell(state: dict, price: float, futures: bool = False, multiplier: float = 1.0,
         ratio: float = 1.0) -> None:
    """Liquidate `ratio` of long shares/lots to cash. No-op if flat or short."""
    if state["shares"] <= 0:
        return
    factor = price * multiplier if futures else price
    if futures:
        qty = math.floor(state["shares"] * ratio)
        if qty <= 0:
            return
    else:
        qty = state["shares"] * ratio
    state["cash"] += qty * factor
    state["shares"] -= qty


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


def cover(state: dict, price: float, futures: bool = False, multiplier: float = 1.0,
          ratio: float = 1.0) -> None:
    """Buy back `ratio` of short shares/lots to close position. No-op if flat or long."""
    if state["shares"] >= 0:
        return
    factor = price * multiplier if futures else price
    if futures:
        qty = math.floor(abs(state["shares"]) * ratio)
        if qty <= 0:
            return
    else:
        qty = abs(state["shares"]) * ratio
    state["cash"] -= qty * factor
    state["shares"] += qty


def take_profit(state: dict, current_price: float, tp_price: float,
                ratio: float = 1.0, futures: bool = False, multiplier: float = 1.0) -> bool:
    """
    Close `ratio` of position when profit target is hit.
    Long : triggers when current_price >= tp_price.
    Short: triggers when current_price <= tp_price.
    Returns True if triggered.
    """
    if state["shares"] == 0:
        return False
    if state["shares"] > 0:
        if current_price < tp_price:
            return False
        sell(state, current_price, futures, multiplier, ratio)
    else:
        if current_price > tp_price:
            return False
        cover(state, current_price, futures, multiplier, ratio)
    return True


def stop_loss(state: dict, current_price: float, sl_price: float,
              ratio: float = 1.0, futures: bool = False, multiplier: float = 1.0) -> bool:
    """
    Close `ratio` of position when stop is hit.
    Long : triggers when current_price <= sl_price.
    Short: triggers when current_price >= sl_price.
    Returns True if triggered.
    """
    if state["shares"] == 0:
        return False
    if state["shares"] > 0:
        if current_price > sl_price:
            return False
        sell(state, current_price, futures, multiplier, ratio)
    else:
        if current_price < sl_price:
            return False
        cover(state, current_price, futures, multiplier, ratio)
    return True


def trailing_take_profit(state: dict, current_price: float, bars_held: int,
                         window: int, x: float,
                         futures: bool = False, multiplier: float = 1.0) -> bool:
    """
    Trailing take-profit, active for `window` bars after entry signal.

    Tracks the peak favorable price in state['_trail_peak']. Closes the
    full position when price retraces more than `x` (fraction, e.g. 0.05
    for 5%) from that peak.

    Long : peak = highest price seen; triggers when current_price <= peak * (1 - x)
    Short: peak = lowest  price seen; triggers when current_price >= peak * (1 + x)

    Clears state['_trail_peak'] on trigger or when window expires.
    Returns True if triggered.
    """
    if state["shares"] == 0:
        state.pop("_trail_peak", None)
        return False

    if bars_held > window:
        state.pop("_trail_peak", None)
        return False

    if state["shares"] > 0:
        state["_trail_peak"] = max(state.get("_trail_peak", current_price), current_price)
        if current_price <= state["_trail_peak"] * (1 - x):
            state.pop("_trail_peak", None)
            sell(state, current_price, futures, multiplier)
            return True
    else:
        state["_trail_peak"] = min(state.get("_trail_peak", current_price), current_price)
        if current_price >= state["_trail_peak"] * (1 + x):
            state.pop("_trail_peak", None)
            cover(state, current_price, futures, multiplier)
            return True

    return False


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

    if state["shares"] > 0:
        sell(state, current_price, futures, multiplier)
    elif state["shares"] < 0:
        cover(state, current_price, futures, multiplier)

    return {
        "status": "Completely lost all money",
        "datetime": str(dt),
    }


def capital_stop_loss(state: dict, current_price: float, entry_price: float, y: float,
                      ratio: float = 1.0, futures: bool = False, multiplier: float = 1.0) -> bool:
    """
    Stop-loss based on capital loss ratio since entry signal.

    Triggers when the unrealized loss exceeds y * entry position value:
        Long : triggers when current_price <= entry_price * (1 - y)
        Short: triggers when current_price >= entry_price * (1 + y)

    y=0.05 means stop out when this trade has lost more than 5% of entry capital.
    Delegates to stop_loss once the threshold price is computed.
    """
    if state["shares"] > 0:
        sl_price = entry_price * (1 - y)
    else:
        sl_price = entry_price * (1 + y)
    return stop_loss(state, current_price, sl_price, ratio, futures, multiplier)
