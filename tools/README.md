# tools/

交易执行原语，供策略层调用。

---

## trade.py

所有函数共享同一个 `state` 字典：

```python
state = {"cash": 100_000.0, "shares": 0.0}
# shares > 0 : 多头持仓
# shares < 0 : 空头持仓
# shares == 0: 空仓
```

### 开平仓

| 函数 | 说明 |
|------|------|
| `buy(state, price, futures, multiplier)` | 全仓做多；若当前空头则先平空再做多 |
| `sell(state, price, futures, multiplier)` | 平多；空仓或空头时无效 |
| `short(state, price, futures, multiplier)` | 全仓做空；若当前多头则先平多再做空 |
| `cover(state, price, futures, multiplier)` | 平空；空仓或多头时无效 |

### 止盈止损

| 函数 | 说明 |
|------|------|
| `take_profit(state, current_price, tp_price, ratio, futures, multiplier)` | 多头：价格 >= tp_price 时平 ratio 比例；空头：价格 <= tp_price 时平 ratio 比例 |
| `stop_loss(state, current_price, sl_price, ratio, futures, multiplier)` | 多头：价格 <= sl_price 时平 ratio 比例；空头：价格 >= sl_price 时平 ratio 比例 |

### 参数说明

- `futures` (bool, 默认 False)：期货模式，持仓取整手，价值乘以 `multiplier`
- `multiplier` (float, 默认 1.0)：合约乘数（股票忽略此参数）
- `ratio` (float, 默认 1.0)：平仓比例，范围 (0, 1]

### 示例

```python
from tools.trade import buy, sell, short, cover, take_profit, stop_loss

state = {"cash": 100_000.0, "shares": 0.0}

# 股票做多
buy(state, price=150.0)
take_profit(state, current_price=180.0, tp_price=175.0, ratio=0.5)
sell(state, price=180.0)

# 期货做空（ES=F，乘数50）
short(state, price=5000.0, futures=True, multiplier=50)
stop_loss(state, current_price=5100.0, sl_price=5080.0, futures=True, multiplier=50)
cover(state, price=4900.0, futures=True, multiplier=50)
```
