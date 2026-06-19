"""Position management primitives used by strategies."""


def buy(state: dict, price: float) -> None:
    """Convert all cash to shares at given price. No-op if already holding."""
    if state["cash"] > 0 and state["shares"] == 0:
        state["shares"] = state["cash"] / price
        state["cash"] = 0.0


def sell(state: dict, price: float) -> None:
    """Liquidate all shares to cash at given price. No-op if flat."""
    if state["shares"] > 0:
        state["cash"] = state["shares"] * price
        state["shares"] = 0.0


def take_profit(state: dict, current_price: float, tp_price: float, ratio: float = 1.0) -> bool:
    """
    Sell `ratio` of current shares if current_price >= tp_price.

    Returns True if the order was triggered.
    ratio: fraction of shares to sell, 0 < ratio <= 1.
    """
    if state["shares"] <= 0 or current_price < tp_price:
        return False
    shares_to_sell = state["shares"] * ratio
    state["cash"] += shares_to_sell * current_price
    state["shares"] -= shares_to_sell
    return True


def stop_loss(state: dict, current_price: float, sl_price: float, ratio: float = 1.0) -> bool:
    """
    Sell `ratio` of current shares if current_price <= sl_price.

    Returns True if the order was triggered.
    ratio: fraction of shares to sell, 0 < ratio <= 1.
    """
    if state["shares"] <= 0 or current_price > sl_price:
        return False
    shares_to_sell = state["shares"] * ratio
    state["cash"] += shares_to_sell * current_price
    state["shares"] -= shares_to_sell
    return True
