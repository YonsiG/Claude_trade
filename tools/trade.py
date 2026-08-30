"""Position management primitives used by strategies.

state dict schema:
    cash   (float): available cash
    shares (float): signed position — positive = long, negative = short (lots for futures)

Fee model (applied in buy/sell/short/cover, propagates to all higher-level functions):
    fee = traded_value * fee_rate + quantity * fee_per_lot
    traded_value = qty * price          (stocks)
                 = lots * price * mult  (futures)
    quantity     = qty  (stocks) | lots (futures)

保证金模型（`margin_rate`，仅对 futures=True 生效）:
    期货是保证金交易，开一手只需缴纳名义价值的一小部分：
        一手名义价值 = price * multiplier
        一手占用资金 = 名义价值 * margin_rate
    所以可开手数 = floor(可用资金 / (一手占用资金 + 一手手续费))。

    `margin_rate=1.0`（默认）表示按全额名义价值开仓，即**没有杠杆**——
    这是股票的行为，也是本模块的向后兼容默认值。真实期货应传入品种对应的
    保证金率，见 `data/futures_spec.py`（RB 0.20、AU 0.26、AG 0.30 …）。

    现金记账仍按全额名义价值扣减，`cash` 因此会变成负数——那正是「借入的
    保证金头寸」。权益 `cash + shares * price * multiplier` 依然正确，
    杠杆收益率也自动正确，无需额外记账。

    杠杆开仓后必须配合 `margin_call()` 检查，否则回测会让爆仓的持仓活下去。
"""
from __future__ import annotations

import math


def _fee(quantity: float, price: float, fee_rate: float, fee_per_lot: float,
         futures: bool, multiplier: float) -> float:
    traded_value = quantity * price * (multiplier if futures else 1.0)
    return traded_value * fee_rate + quantity * fee_per_lot


def _lots_for_budget(budget: float, price: float, multiplier: float,
                     margin_rate: float, fee_rate: float, fee_per_lot: float) -> int:
    """保证金约束下 `budget` 能开的整数手数。"""
    notional = price * multiplier
    cost_per_lot = notional * margin_rate + notional * fee_rate + fee_per_lot
    if cost_per_lot <= 0:
        return 0
    return math.floor(budget / cost_per_lot)


def free_capital(state: dict, price: float, futures: bool = False,
                 multiplier: float = 1.0, margin_rate: float = 1.0) -> float:
    """
    还能用来开仓的资金 = 总权益 - 已占用保证金。

    空仓时等于 `cash`，所以对首次开仓与旧行为完全一致。
    加仓时不能直接用 `cash`：杠杆持仓下 cash 是负数（借入的头寸），
    但只要权益还高于已占用保证金，就仍有加仓空间。
    """
    factor = price * multiplier if futures else price
    equity = state["cash"] + state["shares"] * factor
    used = abs(state["shares"]) * factor * margin_rate
    return max(0.0, equity - used)


def buy(state: dict, price: float, futures: bool = False, multiplier: float = 1.0,
        ratio: float = 1.0, fee_rate: float = 0.0, fee_per_lot: float = 0.0,
        margin_rate: float = 1.0, add: bool = False) -> None:
    """
    Open long position using `ratio` of free capital (default: all-in).
    If short, covers first before going long.

    期货按 `margin_rate` 计算占用资金决定手数（见模块 docstring），
    现金按全额名义价值扣减，杠杆持仓下 cash 会变成负数。

    Args:
        add: 已持有多头时是否加仓。False（默认）= 直接返回，与旧行为一致。
             True = 用剩余可用资金（`free_capital`）追加，持仓量累加。
             加仓后开仓成本基准由调用方负责（策略层用加权平均价）。
    """
    if state["shares"] < 0:
        cover(state, price, futures, multiplier, fee_rate=fee_rate, fee_per_lot=fee_per_lot)
    if state["shares"] > 0 and not add:
        return
    if ratio <= 0:
        return
    budget = free_capital(state, price, futures, multiplier, margin_rate) * min(ratio, 1.0)
    if budget <= 0:
        return
    if futures:
        lots = _lots_for_budget(budget, price, multiplier, margin_rate,
                                fee_rate, fee_per_lot)
        if lots <= 0:
            return
        fee = _fee(lots, price, fee_rate, fee_per_lot, futures, multiplier)
        state["cash"] -= lots * price * multiplier + fee
        state["shares"] += lots
    else:
        # qty * price * (1 + fee_rate) + qty * fee_per_lot = budget
        denom = price * (1 + fee_rate) + fee_per_lot
        qty = budget / denom
        fee = _fee(qty, price, fee_rate, fee_per_lot, futures, multiplier)
        state["cash"] -= qty * price + fee
        state["shares"] += qty


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
    if state["shares"] == 0:
        state.pop("_trail_peak", None)  # 峰值属于单次持仓，平仓即作废


def short(state: dict, price: float, futures: bool = False, multiplier: float = 1.0,
          ratio: float = 1.0, fee_rate: float = 0.0, fee_per_lot: float = 0.0,
          margin_rate: float = 1.0, add: bool = False) -> None:
    """
    Open short position sized by `ratio` of available cash (default: all-in).
    If long, sells first before going short.
    Cash increases by proceeds minus fee; shares goes negative.
    `add=True` 时在已有空头上追加（见 buy 的说明）。

    期货手数同样按 `margin_rate` 计算（见模块 docstring）。
    """
    if state["shares"] > 0:
        sell(state, price, futures, multiplier, fee_rate=fee_rate, fee_per_lot=fee_per_lot)
    if state["shares"] < 0 and not add:
        return
    if ratio <= 0:
        return
    budget = free_capital(state, price, futures, multiplier, margin_rate) * min(ratio, 1.0)
    if budget <= 0:
        return
    if futures:
        lots = _lots_for_budget(budget, price, multiplier, margin_rate,
                                fee_rate, fee_per_lot)
        if lots <= 0:
            return
        fee = _fee(lots, price, fee_rate, fee_per_lot, futures, multiplier)
        state["cash"] += lots * price * multiplier - fee
        state["shares"] -= lots
    else:
        denom = price * (1 + fee_rate) + fee_per_lot
        qty = budget / denom
        fee = _fee(qty, price, fee_rate, fee_per_lot, futures, multiplier)
        state["cash"] += qty * price - fee
        state["shares"] -= qty


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
    if state["shares"] == 0:
        state.pop("_trail_peak", None)  # 峰值属于单次持仓，平仓即作废


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


def margin_call(state: dict, current_price: float, dt,
                margin_rate: float, futures: bool = True, multiplier: float = 1.0,
                maintenance: float = 1.0,
                fee_rate: float = 0.0, fee_per_lot: float = 0.0) -> dict | None:
    """
    维持保证金检查：权益不足以支撑当前持仓所需的保证金时，强制平仓。

    这是杠杆持仓**必须**有的一环。没有它，回测会让一个早就该被强平的仓位
    继续活着并"扛回来"，从而系统性高估收益、低估回撤。

        所需保证金 = |手数| * price * multiplier * margin_rate * maintenance
        权益       = cash + shares * price * multiplier
        权益 < 所需保证金  ->  按当前价全部平仓

    Args:
        maintenance: 维持保证金相对开仓保证金的比例。1.0（默认）= 权益一跌破
                     开仓保证金就强平，比真实券商（通常 0.7~0.8）更早出局，
                     是**保守**设定：回测结果只会更差，不会更好。
                     想贴近实盘可传 0.75。

    Returns:
        触发时返回记录 dict，否则 None。强平后仍可继续交易（与 force_close
        的"权益归零、彻底出局"不同）。
    """
    if state["shares"] == 0 or not futures or margin_rate >= 1.0:
        return None

    notional = abs(state["shares"]) * current_price * multiplier
    required = notional * margin_rate * maintenance
    equity = state["cash"] + state["shares"] * current_price * multiplier

    if equity >= required:
        return None

    lots = abs(state["shares"])
    if state["shares"] > 0:
        sell(state, current_price, futures, multiplier,
             fee_rate=fee_rate, fee_per_lot=fee_per_lot)
    else:
        cover(state, current_price, futures, multiplier,
              fee_rate=fee_rate, fee_per_lot=fee_per_lot)

    return {
        "status": "margin call",
        "datetime": str(dt),
        "price": current_price,
        "lots": lots,
        "equity": equity,
        "required_margin": required,
    }


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
