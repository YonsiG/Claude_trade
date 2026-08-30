# data/

数据下载与缓存模块，支持 yfinance（美股/美盘期货）和 akshare（国内期货）双数据源。

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `loader.py` | 核心下载与缓存逻辑 |
| `fetch.py` | 批量下载脚本，命令行入口 |
| `tickers.txt` | 品种列表，三列格式：ticker、source、名称 |
| `tickerref.txt` | 期货品种参考表，用于校验 tickers.txt 中的期货合法性 |
| `raw/futures/yf/` | 美盘期货缓存（ticker 以 `=F` 结尾） |
| `raw/futures/ak/` | 国内期货缓存（akshare） |
| `raw/stock/yf/` | 美股 / ETF 缓存 |

---

## tickers.txt 格式

每行三列，空格分隔，`#` 开头为注释：

```
# ticker   source   name
GLD        yf       黄金ETF
LH0        ak       生猪
NQ=F       yf       纳指100
```

- `source=yf`：yfinance（美股、ETF、美盘期货）
- `source=ak`：akshare（国内期货，经由新浪 Sina 接口）

---

## 缓存命名规则

```
raw/{futures|stock}/{source}/{ticker}_{start}_{end}_{interval}.csv
```

例如：`raw/futures/yf/GC=F_2021-06-20_2026-06-19_1d.csv`、`raw/stock/yf/AAPL_2021-06-20_2026-06-19_1d.csv`

缓存逻辑（`loader.py`）：
- 若已有文件完整覆盖请求区间 → 直接切片返回
- 若有部分重叠文件 → 合并范围后重新下载，删除旧文件
- 否则全量下载

---

## fetch.py 用法

```bash
# 默认：tickers.txt 全部品种，日线，近 5 年
python data/fetch.py

# 按时间档位
python data/fetch.py --month        # 1h，近 30 天
python data/fetch.py --day          # 1m，近 5 天

# 按数据源过滤
python data/fetch.py --source yf    # 仅 yfinance 品种
python data/fetch.py --source ak    # 仅 akshare 品种

# 自定义日期（覆盖预设）
python data/fetch.py --start 2020-01-01 --end 2024-12-31

# 自定义品种文件
python data/fetch.py --tickers my_list.txt --source yf --month
```

### 时间档位预设

| 档位 | interval | 默认时间段 |
|------|----------|-----------|
| `--year`（默认） | `1d` | 近 5 年 |
| `--month` | `1h` | 近 30 天 |
| `--day` | `1m` | 近 5 天 |

---

## 期货校验

下载前会检查 tickers.txt 中的期货品种是否存在于 `tickerref.txt`：

- **国内期货**（`source=ak`）：必须在 tickerref.txt 中
- **美盘期货**（`source=yf`，ticker 以 `=F` 结尾）：必须在 tickerref.txt 中
- **股票 / ETF**（`source=yf`，不含 `=F`）：不受限制

不在参考表中的期货品种会被跳过并报错，但不影响其他品种的下载。

---

## loader.py API

```python
from data.loader import load, download

# 优先读缓存，缺失则下载
df = load("GC=F", "2021-01-01", "2024-12-31", interval="1d", source="yf")

# 强制下载并保存
df = download("AU0", "2021-01-01", "2024-12-31", interval="1h", source="ak")
```

返回的 DataFrame 列名统一为小写：`open`、`high`、`low`、`close`、`volume`。
akshare 期货额外带 `hold`（持仓量）—— 换月识别依赖它，见下节。

---

## 换月识别与后复权（`adjust.py`）

主力连续（`RB0`、`GC=F`）由多份交割月合约拼接而成，换月处的价格跳变不是
可交易的盈亏。`load()` 对期货**默认开启**比例后复权抹平这个跳变。

识别换月按可靠性排序：

| 优先级 | 依据 | 说明 |
|---|---|---|
| 1 | `roll_dates` | 调用方给出的真实换月日 |
| 2 | **`hold` 持仓量跳变**（默认，>30%） | 换月切换合约，持仓量必然断裂 |
| 3 | 价格跳空阈值（>2%） | 仅在无 `hold` 列时退而求其次 |

为什么持仓量比价格跳空可靠 —— RB0 2021-01 至 2026-07 实测：

- 12 次真实换月中，有 4 次跳空不足 1%（-0.64%、-0.51%、-0.27%、0.89%），
  **纯跳空阈值全部漏报**
- 另有 7 根跳空 >2% 的 bar 其实是五一/端午/国庆节后缺口和 2025-04 关税跳空，
  **纯跳空阈值全部误报**，还会把这些真实行情错误地抹平
- AU0 更严重：18 根隔夜跳空会被当成换月

```python
from data import adjust

adjust.rollover_report(df)        # 换月明细 + 标出疑似误判的缺口
adjust.detect_rollover(df)        # 布尔序列
adjust.adjust_rollover(df)        # 后复权

df = load("RB0", ..., source="ak", adjust=False)              # 要原始拼接序列
df = load("RB0", ..., roll_dates=["2024-04-03", ...])         # 用已知换月日
```

**老缓存 CSV 不含 `hold` 列**（该列是后加的），`load()` 会打印提示。
删除 `raw/futures/ak/` 下对应 CSV 重新下载即可启用持仓量识别。

---

## 注意事项

- `raw/` 目录下的 CSV 文件已加入 `.gitignore`，不会被提交
- 路由规则：`source=ak` 或 ticker 以 `=F` 结尾 → `futures/`；其余 `yf` ticker → `stock/`
- akshare 分钟/小时线仅返回最近约 1000 根 K 线，历史数据有限
- yfinance 在网络受限环境下可在 `loader.py` 中设置 `YF_PROXY`
