"""Position management primitives used by strategies.

state dict schema:
    cash   (float): available cash
    shares (float): signed position — positive = long, negative = short (lots for futures)

Fee model (applied in buy/sell/short/cover, propagates to all higher-level functions):
    fee = traded_value * fee_rate + quantity * fee_per_lot
    traded_value = qty * price          (stocks)
                 = lots * price * mult  (futures)
    quantity     = qty  (stocks) | lots (futures)
"""
import math


def _fee(quantity: float, price: float, fee_rate: float, fee_per_lot: float,
         futures: bool, multiplier: float) -> float:
    traded_value = quantity * price * (multiplier if futures else 1.0)
    return traded_value * fee_rate + quantity * fee_per_lot


def buy(state: dict, price: float, futures: bool = False, multiplier: float = 1.0,
        fee_rate: float = 0.0, fee_per_lot: float = 0.0) -> None:
    """
    Open long position with all available cash. No-op if already long.
    If short, covers first before going long.
    Fee is deducted from cash; shares/lots are sized so cash stays >= 0.
    """
    if state["shares"] < 0:
        cover(state, price, futures, multiplier, fee_rate=fee_rate, fee_per_lot=fee_per_lot)
    if state["cash"] <= 0 or state["shares"] > 0:
        return
    if futures:
        # lots * price * mult + fee(lots) = cash
        # lots * (price * mult * (1 + fee_rate) + fee_per_lot) = cash
        denom = price * multiplier * (1 + fee_rate) + fee_per_lot
        lots = math.floor(state["cash"] / denom)
        if lots <= 0:
            return
        fee = _fee(lots, price, fee_rate, fee_per_lot, futures, multiplier)
        state["cash"] -= lots * price * multiplier + fee
        state["shares"] = lots
    else:
        # qty * price * (1 + fee_rate) + qty * fee_per_lot = cash
        denom = price * (1 + fee_rate) + fee_per_lot
        qty = state["cash"] / denom
        fee = _fee(qty, price, fee_rate, fee_per_lot, futures, multiplier)
        state["cash"] -= qty * price + fee
        state["shares"] = qty


def sell(state: dict, price: float, futures: bool = False, multiplier: float = 1.0,
         ratio: float = 1.0, fee_rate: float = 0.0, fee_per_lot: float = 0.0) -> None:
    """Liquidate `ratio` of long shares/lots to cash, net of fees. No-op if flat or short."""
    if state["shares"] <= 0:
        return
    if futures:
        qty = math.floor(state["shares"] * ratio)
        if qty <= 0:
            return
    else:
        qty = state["shares"] * ratio
    fee = _fee(qty, price, fee_rate, fee_per_lot, futures, multiplier)
    proceeds = qty * price * (multiplier if futures else 1.0)
    state["cash"] += proceeds - fee
    state["shares"] -= qty


def short(state: dict, price: float, futures: bool = False, multiplier: float = 1.0,
          fee_rate: float = 0.0, fee_per_lot: float = 0.0) -> None:
    """
    Open short position sized by available cash. No-op if already short.
    If long, sells first before going short.
    Cash increases by proceeds minus fee; shares goes negative.
    """
    if state["shares"] > 0:
        sell(state, price, futures, multiplier, fee_rate=fee_rate, fee_per_lot=fee_per_lot)
    if state["cash"] <= 0 or state["shares"] < 0:
        return
    if futures:
        denom = price * multiplier * (1 - fee_rate) - fee_per_lot
        if denom <= 0:
            return
        lots = math.floor(state["cash"] / denom) if False else math.floor(
            state["cash"] / (price * multiplier * (1 + fee_rate) + fee_per_lot)
        )
        if lots <= 0:
            return
        fee = _fee(lots, price, fee_rate, fee_per_lot, futures, multiplier)
        state["cash"] += lots * price * multiplier - fee
        state["shares"] = -lots
    else:
        denom = price * (1 + fee_rate) + fee_per_lot
        qty = state["cash"] / denom
        fee = _fee(qty, price, fee_rate, fee_per_lot, futures, multiplier)
        state["cash"] += qty * price - fee
        state["shares"] = -qty


def cover(state: dict, price: float, futures: bool = False, multiplier: float = 1.0,
          ratio: float = 1.0, fee_rate: float = 0.0, fee_per_lot: float = 0.0) -> None:
    """Buy back `ratio` of short shares/lots to close position, net of fees. No-op if flat or long."""
    if state["shares"] >= 0:
        return
    if futures:
        qty = math.floor(abs(state["shares"]) * ratio)
        if qty <= 0:
            return
    else:
        qty = abs(state["shares"]) * ratio
    fee = _fee(qty, price, fee_rate, fee_per_lot, futures, multiplier)
    cost = qty * price * (multiplier if futures else 1.0)
    state["cash"] -= cost + fee
    state["shares"] += qty


def take_profit(state: dict, current_price: float, tp_price: float,
                ratio: float = 1.0, futures: bool = False, multiplier: float = 1.0,
                fee_rate: float = 0.0, fee_per_lot: float = 0.0) -> bool:
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
        sell(state, current_price, futures, multiplier, ratio, fee_rate, fee_per_lot)
    else:
        if current_price > tp_price:
            return False
        cover(state, current_price, futures, multiplier, ratio, fee_rate, fee_per_lot)
    return True


def stop_loss(state: dict, current_price: float, sl_price: float,
              ratio: float = 1.0, futures: bool = False, multiplier: float = 1.0,
              fee_rate: float = 0.0, fee_per_lot: float = 0.0) -> bool:
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
        sell(state, current_price, futures, multiplier, ratio, fee_rate, fee_per_lot)
    else:
        if current_price < sl_price:
            return False
        cover(state, current_price, futures, multiplier, ratio, fee_rate, fee_per_lot)
    return True


def trailing_take_profit(state: dict, current_price: float, bars_held: int,
                         window: int, x: float,
                         futures: bool = False, multiplier: float = 1.0,
                         fee_rate: float = 0.0, fee_per_lot: float = 0.0) -> bool:
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
            sell(state, current_price, futures, multiplier, fee_rate=fee_rate, fee_per_lot=fee_per_lot)
            return True
    else:
        state["_trail_peak"] = min(state.get("_trail_peak", current_price), current_price)
        if current_price >= state["_trail_peak"] * (1 + x):
            state.pop("_trail_peak", None)
            cover(state, current_price, futures, multiplier, fee_rate=fee_rate, fee_per_lot=fee_per_lot)
            return True

    return False


def capital_stop_loss(state: dict, current_price: float, entry_price: float, y: float,
                      ratio: float = 1.0, futures: bool = False, multiplier: float = 1.0,
                      fee_rate: float = 0.0, fee_per_lot: float = 0.0) -> bool:
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
    return stop_loss(state, current_price, sl_price, ratio, futures, multiplier, fee_rate, fee_per_lot)


def force_close(state: dict, current_price: float, dt,
                futures: bool = False, multiplier: float = 1.0) -> dict | None:
    """
    Check if total equity has reached zero (or gone negative).
    If so, liquidate all positions and return a bust record.
    Otherwise return None.

    Note: fees are not applied here — force_close is triggered after equity
    is already <= 0, so there is nothing left to deduct from.
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
