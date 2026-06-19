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
| `raw/yf/` | yfinance 数据缓存目录 |
| `raw/ak/` | akshare 数据缓存目录 |

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
raw/{source}/{ticker}_{start}_{end}_{interval}.csv
```

例如：`raw/yf/GC=F_2021-06-20_2026-06-19_1d.csv`

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

---

## 注意事项

- `raw/` 目录下的 CSV 文件已加入 `.gitignore`，不会被提交
- akshare 分钟/小时线仅返回最近约 1000 根 K 线，历史数据有限
- yfinance 在网络受限环境下可在 `loader.py` 中设置 `YF_PROXY`
