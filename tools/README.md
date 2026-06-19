# tools/

仓位管理原语。所有函数共享一个 `state` 字典，结构如下：

```python
state = {"cash": float, "shares": float}
# shares > 0：多头（股票为份额，期货为手数）
# shares < 0：空头（绝对值为手数/份额）
# shares = 0：空仓
```

---

## 函数一览

### `buy(state, price, futures=False, multiplier=1.0)`

全仓做多。若当前持空则先平空再做多。已持多时无操作。

- `futures=True`：整数手数，占用保证金 = lots × price × multiplier
- `futures=False`：小数份额，multiplier 忽略

```python
buy(state, price=100.0)
buy(state, price=500.0, futures=True, multiplier=10.0)
```

### `sell(state, price, futures=False, multiplier=1.0)`

全仓平多。空仓或持空时无操作。

```python
sell(state, price=105.0)
```

### `short(state, price, futures=False, multiplier=1.0)`

全仓做空。若当前持多则先平多再做空。已持空时无操作。

借入并卖出：`cash` 增加，`shares` 变为负值。

```python
short(state, price=100.0)
short(state, price=500.0, futures=True, multiplier=10.0)
```

### `cover(state, price, futures=False, multiplier=1.0)`

全仓平空。空仓或持多时无操作。

```python
cover(state, price=95.0)
```

### `take_profit(state, current_price, tp_price, ratio=1.0, futures=False, multiplier=1.0) -> bool`

止盈。触发条件随方向自动切换，触发时平掉 `ratio` 比例仓位。

| 方向 | 触发条件 |
|------|----------|
| 多头 | `current_price >= tp_price` |
| 空头 | `current_price <= tp_price` |

```python
# 多头：涨到 110 时全部止盈
take_profit(state, current_price=112, tp_price=110)

# 空头：跌到 90 时止盈一半
take_profit(state, current_price=88, tp_price=90, ratio=0.5)
```

### `stop_loss(state, current_price, sl_price, ratio=1.0, futures=False, multiplier=1.0) -> bool`

止损。触发条件随方向自动切换，触发时平掉 `ratio` 比例仓位。

| 方向 | 触发条件 |
|------|----------|
| 多头 | `current_price <= sl_price` |
| 空头 | `current_price >= sl_price` |

```python
# 多头：跌到 90 时全部止损
stop_loss(state, current_price=88, sl_price=90)

# 空头：涨到 110 时止损三分之一
stop_loss(state, current_price=112, sl_price=110, ratio=0.33)
```

### `force_close(state, current_price, dt, futures=False, multiplier=1.0) -> dict | None`

强制平仓检查。每根 bar 调用一次。若总资产归零（≤ 0）则立即平掉所有仓位，返回 bust 记录；否则返回 `None`。

**总资产计算：**
- 多头：`cash + shares × price [× multiplier]`
- 空头：`cash − |shares| × price [× multiplier]`（保证金模型）

**返回值（触发时）：**
```python
{
    "status": "Completely lost all money",
    "datetime": "2024-03-15 14:30:00",   # str(dt)
}
```

策略层收到非 `None` 返回值后应立即 `break`，后续不再修改 `state` 或 `df`。回测的画图和统计不受影响。

```python
bust = force_close(state, current_price=price, dt=bar.Index)
if bust:
    summary["bust"] = bust
    break
```

---

## 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `state` | `dict` | 含 `cash`（现金）和 `shares`（仓位，负为空头）的状态字典，原地修改 |
| `price` / `current_price` | `float` | 当前成交价格 |
| `tp_price` | `float` | 止盈触发价 |
| `sl_price` | `float` | 止损触发价 |
| `ratio` | `float` | 平仓比例，取值 `(0, 1]`，默认 `1.0`（全仓） |
| `futures` | `bool` | `True` 为期货模式（整数手数），默认 `False` |
| `multiplier` | `float` | 期货合约乘数，`futures=False` 时忽略 |
| `dt` | any | 当前 bar 的时间戳，`force_close` 用于记录爆仓时间 |

---

## 在策略中使用

```python
from tools import buy, sell, short, cover, take_profit, stop_loss, force_close

state = {"cash": 100_000, "shares": 0.0}
bust_record = None

for bar in df.itertuples():
    price = bar.close

    # 强制平仓检查（每根 bar 最先执行）
    bust = force_close(state, price, bar.Index)
    if bust:
        bust_record = bust
        break

    # 止盈止损
    if take_profit(state, price, tp_price=entry * 1.10):
        continue
    if stop_loss(state, price, sl_price=entry * 0.95):
        continue

    # 信号逻辑
    if long_signal:
        buy(state, price)
    elif short_signal:
        short(state, price)
```
