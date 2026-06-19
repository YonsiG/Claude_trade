# tools/

仓位管理原语。所有函数共享一个 `state` 字典，结构如下：

```python
state = {"cash": float, "shares": float}
```

---

## 函数一览

### `buy(state, price)`

全仓买入。将所有现金按 `price` 换成份额。已持仓时无操作。

```python
buy(state, price=100.0)
```

### `sell(state, price)`

全仓卖出。将所有份额按 `price` 换回现金。空仓时无操作。

```python
sell(state, price=105.0)
```

### `take_profit(state, current_price, tp_price, ratio=1.0) -> bool`

止盈。当 `current_price >= tp_price` 时，卖出 `ratio` 比例的持仓。返回 `True` 表示本次触发。

```python
# 价格涨到 110 时全部卖出
take_profit(state, current_price=112, tp_price=110)

# 价格涨到 110 时只卖一半
take_profit(state, current_price=112, tp_price=110, ratio=0.5)
```

### `stop_loss(state, current_price, sl_price, ratio=1.0) -> bool`

止损。当 `current_price <= sl_price` 时，卖出 `ratio` 比例的持仓。返回 `True` 表示本次触发。

```python
# 价格跌到 90 时全部止损
stop_loss(state, current_price=88, sl_price=90)

# 价格跌到 90 时只止损三分之一
stop_loss(state, current_price=88, sl_price=90, ratio=0.33)
```

---

## 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `state` | `dict` | 含 `cash`（现金）和 `shares`（份额）的状态字典，原地修改 |
| `price` / `current_price` | `float` | 当前成交价格 |
| `tp_price` | `float` | 止盈触发价，`current_price >= tp_price` 时卖出 |
| `sl_price` | `float` | 止损触发价，`current_price <= sl_price` 时卖出 |
| `ratio` | `float` | 卖出比例，取值 `(0, 1]`，默认 `1.0`（全仓） |

---

## 在策略中使用

```python
from tools import buy, sell, take_profit, stop_loss

state = {"cash": 100_000, "shares": 0.0}
entry_price = 100.0

# 建仓
buy(state, entry_price)

# 每根 bar 检查止盈止损
for price in price_series:
    if take_profit(state, price, tp_price=entry_price * 1.10):
        break  # 止盈离场
    if stop_loss(state, price, sl_price=entry_price * 0.95):
        break  # 止损离场
```

`take_profit` 和 `stop_loss` 均返回 `bool`，可用于记录触发日志或切换策略状态。
