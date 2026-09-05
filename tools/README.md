# tools/

仓位管理原语。所有函数共享一个 `state` 字典，结构如下：

```python
state = {"cash": float, "shares": float}
# shares > 0：多头（股票为份额，期货为手数）
# shares < 0：空头（绝对值为手数/份额）
# shares = 0：空仓
# 杠杆持仓下 cash 可以为负——那是借入的保证金头寸，不是 bug
```

---

## 函数一览

### `buy(state, price, futures=False, multiplier=1.0, ratio=1.0, fee_rate=0.0, fee_per_lot=0.0, margin_rate=1.0, add=False)`

用 `ratio` 比例的**可用资金**（见 `free_capital`）做多。若当前持空则先平空再做多。

- `futures=True`：整数手数，一手占用资金 = `price × multiplier × margin_rate`
- `futures=False`：小数份额，`margin_rate`/`multiplier` 不生效（等价于 `margin_rate=1.0`）
- `add=False`（默认）：已持多时直接返回，不动仓位——保持旧版本"全仓一次性开"的行为
- `add=True`：已持多时用剩余可用资金**追加**，持仓量累加。加仓后的止损基准
  由调用方自己算加权平均价（`SingleSignalStrategy._add_position` 是现成的参考实现）

```python
buy(state, price=100.0)                                    # 股票，全仓
buy(state, price=4000.0, futures=True, multiplier=10, margin_rate=0.20)   # 期货，20% 保证金
buy(state, price=105.0, ratio=0.5, add=True)                # 用一半可用资金加仓
```

### `free_capital(state, price, futures=False, multiplier=1.0, margin_rate=1.0) -> float`

还能用来开仓的资金 = 总权益 − 已占用保证金。空仓时等于 `cash`。

杠杆持仓下不能直接用 `cash` 判断还能开多少——`cash` 可能是负数（借入的头寸），
但只要权益还高于已占用保证金，就仍有加仓空间。`buy()`/`short()` 内部用它算 `budget`。

```python
free_capital(state, price=100.0)                                  # 空仓/股票：等于 cash
free_capital(state, price=4000.0, futures=True, multiplier=10, margin_rate=0.20)
```

### `sell(state, price, futures=False, multiplier=1.0, ratio=1.0) -> None`

平多，可指定比例。空仓或持空时无操作。

```python
sell(state, price=105.0)          # 全部平仓
sell(state, price=105.0, ratio=0.5)   # 平一半
```

### `short(state, price, futures=False, multiplier=1.0, ratio=1.0, margin_rate=1.0, add=False) -> None`

做空，参数含义与 `buy` 完全对称（先平多再做空，`add=True` 支持加仓）。

```python
short(state, price=100.0)
short(state, price=4000.0, futures=True, multiplier=10, margin_rate=0.20)
```

### `cover(state, price, futures=False, multiplier=1.0, ratio=1.0) -> None`

平空，可指定比例。空仓或持多时无操作。

```python
cover(state, price=95.0)
```

### `margin_call(state, current_price, dt, margin_rate, futures=True, multiplier=1.0, maintenance=1.0, fee_rate=0.0, fee_per_lot=0.0) -> dict | None`

维持保证金检查：权益跌破所需保证金时按当前价强平。**杠杆持仓（`margin_rate<1.0`）每根
bar 都要调用**，否则回测会让早就该被强平的仓位"扛"回来，系统性高估收益、低估回撤。

`maintenance`：维持保证金相对开仓保证金的比例，默认 1.0（权益一跌破开仓保证金就强平，
比真实券商的 0.7~0.8 更早出局——保守设定，回测结果只会更差不会更好）。

强平后仍可继续交易（区别于 `force_close` 的"权益归零、彻底出局"）。`margin_rate>=1.0`
（无杠杆）时直接返回 `None`，不做检查。

```python
called = margin_call(state, price, dt, margin_rate=0.20, futures=True, multiplier=10)
if called:
    print(f"{called['datetime']} 强平：权益 {called['equity']:.0f} < 所需保证金 {called['required_margin']:.0f}")
```

### `force_close(state, current_price, dt, futures=False, multiplier=1.0) -> dict | None`

爆仓检查：总资产归零（≤ 0）时立即平掉所有仓位，返回 bust 记录；否则返回 `None`。
每根 bar 都要调用（不止杠杆持仓——满仓股票理论上也可能亏光）。

```python
bust = force_close(state, current_price=price, dt=bar.Index)
if bust:
    print(bust["status"], bust["datetime"])
```

---

## 简化版离场原语（`take_profit` / `stop_loss` / `trailing_take_profit` / `capital_stop_loss`）

> **这四个函数只看单一 `current_price`，没有盘中 high/low 触发、没有跳空按开盘价
> 成交的处理，会系统性低估回撤**（价格盘中扎穿止损位又收回的情况完全捕捉不到）。
>
> `strategies/single_signal.py` **不用这几个函数**——它在 `_exit_fill()` 里自己
> 实现了一套支持盘中触发 + 跳空成交价的更完整逻辑，那才是本项目的生产版本。
> 写新策略想要同等质量的止损处理，去参考/复用那一份，不要照抄下面的示例当范本。
>
> 保留它们是因为接口足够简单，写一个"只看收盘价"的快速原型时够用；
> 认真回测就不能只看收盘价。

| 函数 | 触发条件（多头 / 空头） |
|------|------|
| `take_profit(state, current_price, tp_price, ratio=1.0, ...)` | `>= tp_price` / `<= tp_price` |
| `stop_loss(state, current_price, sl_price, ratio=1.0, ...)` | `<= sl_price` / `>= sl_price` |
| `trailing_take_profit(state, current_price, bars_held, window, x, ...)` | 自峰值回撤 `x` |
| `capital_stop_loss(state, current_price, entry_price, y, ratio=1.0, ...)` | 自开仓价反向 `y`，内部委托给 `stop_loss` |

```python
take_profit(state, current_price=112, tp_price=110)              # 多头止盈
stop_loss(state, current_price=88, sl_price=90, ratio=0.5)        # 空头止损一半
trailing_take_profit(state, current_price=price, bars_held=5, window=10_000, x=0.30)
```

`trailing_take_profit` 的峰值存在 `state['_trail_peak']`，`buy`/`sell`/`short`/`cover`
在仓位归零时会自动清掉它——不会跨仓位泄漏（这是修过的一个真实 bug，见 git log）。

---

## 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `state` | `dict` | 含 `cash`（现金）和 `shares`（仓位，负为空头）的状态字典，原地修改 |
| `price` / `current_price` | `float` | 当前成交价格 |
| `ratio` | `float` | 开/平仓比例，取值 `(0, 1]`，默认 `1.0`（全仓/全部） |
| `futures` | `bool` | `True` 为期货模式（整数手数），默认 `False` |
| `multiplier` | `float` | 期货合约乘数，`futures=False` 时不生效 |
| `margin_rate` | `float` | 期货保证金率 `(0, 1]`，`1.0`（默认）= 无杠杆全额开仓。仅 `buy`/`short`/`margin_call` 接受 |
| `add` | `bool` | 已有同向仓位时是否加仓，默认 `False`。仅 `buy`/`short` 接受 |
| `fee_rate` / `fee_per_lot` | `float` | 手续费率 / 每手固定费用 |
| `dt` | any | 当前 bar 的时间戳，`margin_call`/`force_close` 用于记录触发时间 |

---

## 在策略中使用

真正的生产逻辑请直接看 `strategies/single_signal.py`（盘中止损触发、加仓、
保证金强平全部齐活）。下面只是最小可用的骨架，演示这几个原语怎么串起来
——**止损止盈是上面标注的简化版**，认真回测不要只用这个骨架：

```python
from tools.trade import buy, sell, short, cover, margin_call, force_close

state = {"cash": 100_000, "shares": 0.0}
margin_rate = 0.20   # 期货杠杆；股票场景删掉这行，下面调用也不用传

for bar in df.itertuples():
    price = bar.close

    # 每根 bar 最先做爆仓/强平检查
    bust = force_close(state, price, bar.Index)
    if bust:
        break
    if margin_rate < 1.0:
        margin_call(state, price, bar.Index, margin_rate, futures=True, multiplier=10)

    # 信号逻辑
    if long_signal:
        buy(state, price, futures=True, multiplier=10, margin_rate=margin_rate)
    elif short_signal:
        short(state, price, futures=True, multiplier=10, margin_rate=margin_rate)
```
