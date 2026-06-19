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
