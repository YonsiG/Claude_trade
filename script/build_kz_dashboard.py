"""生成《关键区突破回测台》——见 signals/key_zone.py + strategies/key_zone_breakout.py。

对一组标的，在 (required_touches, 止盈版本) 的组合上跑 KeyZoneBreakoutStrategy，
把汇总指标、资金曲线、逐笔交易、标的自身的价格/成交量一起打包进一份自包含的
HTML（数据内嵌，不依赖任何图表库，纯手写 SVG）。

用法：
    python script/build_kz_dashboard.py
    python script/build_kz_dashboard.py --tickers AAPL,RB0 --touches 2,3 --out my.html
    python script/build_kz_dashboard.py --tickers AG0 --start 2020-01-01 --end 2026-08-16 \
        --source ak --futures AG0

默认标的、区间沿用上一版本对照用的那一组（AAPL/AMD 股票 + LH0/JD0/AG0/RB0 期货）。
输出默认写到 backtest/plots/key_zone_dashboard.html（这是生成产物，已在 .gitignore
里排除，不进版本库——进版本库的是这个脚本本身）。
"""
from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.loader import load
from signals.key_zone import key_zone_breakout
from strategies.key_zone_breakout import KeyZoneBreakoutStrategy

# ── 默认标的：股票用 yfinance，期货用 akshare（自动查合约乘数/保证金率） ─────
DEFAULT_TARGETS = [
    # ticker, source, futures, start, end, 中文标签, 货币符号
    ("AAPL", "yf", False, "2018-01-01", "2026-07-30", "AAPL", "$"),
    ("AMD",  "yf", False, "2018-01-01", "2026-07-30", "AMD", "$"),
    ("LH0",  "ak", True,  "2020-01-01", "2026-08-16", "LH0 生猪", "¥"),
    ("JD0",  "ak", True,  "2020-01-01", "2026-08-16", "JD0 鸡蛋", "¥"),
    ("AG0",  "ak", True,  "2020-01-01", "2026-08-16", "AG0 白银", "¥"),
    ("RB0",  "ak", True,  "2020-01-01", "2026-08-16", "RB0 螺纹钢", "¥"),
]

# 三个止盈/追踪对照版本，见 KeyZoneBreakoutStrategy 类 docstring 的对照表。
VARIANTS = {
    "original": {"trail_start_r": 0.0, "fixed_take_profit": 0.30},   # 原始版
    "baseline": {"trail_start_r": 2.0, "fixed_take_profit": 0.30},   # 建议基准版
    "trend":    {"trail_start_r": 2.0, "fixed_take_profit": None},   # 趋势对照版
}
VARIANT_LABEL = {"original": "原始版", "baseline": "基准版", "trend": "趋势版"}


def bucket_last_indices(n: int, max_points: int) -> list[int]:
    """把 [0, n) 切成大小约 n/max_points 的桶，返回每个桶最后一行的下标。

    资金曲线、K 线、成交量都按同一组下标降采样——这样三张图叠在一起时
    横轴的日期完全对齐，不会出现"资金曲线取到周三，K 线取到周五"这种错位。
    """
    step = max(1, n // max_points)
    edges = list(range(step - 1, n, step))
    if not edges or edges[-1] != n - 1:
        edges.append(n - 1)
    return edges


def resample_ohlcv(df: pd.DataFrame, edges: list[int]) -> dict:
    """按 `edges` 把 OHLCV 聚合成桶：开=桶内首行开盘，收=桶内末行收盘，
    高低取桶内极值，量取桶内总和——不是简单地每隔几行抽一行，否则会
    漏掉被跳过的那些行里出现过的更高/更低价。"""
    dates, opens, highs, lows, closes, vols = [], [], [], [], [], []
    start = 0
    for edge in edges:
        chunk = df.iloc[start:edge + 1]
        dates.append(df.index[edge].strftime("%Y-%m-%d"))
        opens.append(round(float(chunk["open"].iloc[0]), 4))
        highs.append(round(float(chunk["high"].max()), 4))
        lows.append(round(float(chunk["low"].min()), 4))
        closes.append(round(float(chunk["close"].iloc[-1]), 4))
        vols.append(round(float(chunk["volume"].sum()), 2))
        start = edge + 1
    return {"dates": dates, "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": vols}


def resample_series(series: pd.Series, edges: list[int]) -> dict:
    dates = [series.index[e].strftime("%Y-%m-%d") for e in edges]
    values = [round(float(series.iloc[e]), 2) for e in edges]
    return {"dates": dates, "equity": values}


def run_one(df: pd.DataFrame, ticker: str, futures: bool, touches: int,
           variant_kw: dict, capital: float) -> tuple[dict, pd.Series, pd.DataFrame, object]:
    sig_fn = partial(key_zone_breakout, required_touches=touches)
    strat = KeyZoneBreakoutStrategy(df, signal_fn=sig_fn, futures=futures,
                                    ticker=ticker if futures else None,
                                    initial_capital=capital, **variant_kw)
    eq = strat.run()
    log = strat.trade_log()
    n = len(log)
    wins = int((log["pnl"] > 0).sum()) if n else 0
    win_rate = round(wins / n * 100, 1) if n else None
    total_ret = round((eq.iloc[-1] / capital - 1) * 100, 2)
    gross_win = log.loc[log.pnl > 0, "pnl"].sum() if n else 0.0
    gross_loss = abs(log.loc[log.pnl < 0, "pnl"].sum()) if n else 0.0
    if gross_loss > 0:
        pf = round(gross_win / gross_loss, 2)
    elif gross_win > 0:
        pf = "inf"
    else:
        pf = None
    roll_max = eq.cummax()
    max_dd = round(((eq / roll_max - 1) * 100).min(), 2)
    summary = {"n_trades": n, "win_rate": win_rate, "total_return": total_ret,
              "profit_factor": pf, "max_drawdown": max_dd,
              "abandoned": len(strat.abandoned), "rejected": len(strat.rejected)}
    return summary, eq, log, strat


def build_data(targets, touches_list, capital, max_points):
    summary_rows = []
    equity_curves, trade_logs, price_series = {}, {}, {}

    for ticker, source, futures, start, end, label, ccy in targets:
        print(f"[{ticker}] 加载数据 {start}..{end} ({source})")
        df = load(ticker, start, end, source=source)
        edges = bucket_last_indices(len(df), max_points)
        price_series[ticker] = resample_ohlcv(df, edges)

        for touches in touches_list:
            for vname, vkw in VARIANTS.items():
                summary, eq, log, strat = run_one(df, ticker, futures, touches, vkw, capital)
                key = f"{ticker}|{touches}|{vname}"
                row = {"ticker": ticker, "touches": touches, "variant": vname, **summary}
                summary_rows.append(row)
                equity_curves[key] = resample_series(eq, edges)
                if len(log):
                    cols = ["entry_dt", "exit_dt", "direction", "entry_price",
                           "exit_price", "pnl", "return_pct", "exit_reason", "validations"]
                    tl = log[cols].copy()
                    tl["entry_dt"] = tl["entry_dt"].astype(str)
                    tl["exit_dt"] = tl["exit_dt"].astype(str)
                    trade_logs[key] = tl.round(2).to_dict("records")
                else:
                    trade_logs[key] = []
                print(f"  touches={touches} {vname:9s} n={summary['n_trades']:>3} "
                     f"ret={summary['total_return']:>8.2f}%")

    return {"summary": summary_rows, "equity_curves": equity_curves,
           "trade_logs": trade_logs, "price_series": price_series}


# ── HTML 模板：CSS 全部走 token（浅/深主题都定义），JS 纯手写 SVG，不引入任何
#    图表库；数据以 JSON 内嵌，页面本身不发任何网络请求。 ─────────────────────
TEMPLATE = r'''<title>关键区回测台</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Sans+SC:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f7f5f0; --surface:#ffffff; --surface-2:#f1efe8; --border:#ddd7c8;
  --ink:#17191c; --ink-2:#52565c; --ink-muted:#83807a;
  --accent:#a3711f; --accent-ink:#ffffff; --accent-tint:rgba(163,113,31,0.10);
  --pos:#227a55; --pos-bg:rgba(34,122,85,0.10);
  --neg:#b23f28; --neg-bg:rgba(178,63,40,0.10);
  --shadow:0 1px 2px rgba(23,25,28,0.06), 0 8px 24px rgba(23,25,28,0.06);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#10141a; --surface:#171c24; --surface-2:#1d232c; --border:#2a323d;
    --ink:#eef1f5; --ink-2:#a8b1bd; --ink-muted:#6b7480;
    --accent:#d4a24e; --accent-ink:#10141a; --accent-tint:rgba(212,162,78,0.12);
    --pos:#3fae7c; --pos-bg:rgba(63,174,124,0.14);
    --neg:#d1604a; --neg-bg:rgba(209,96,74,0.14);
    --shadow:0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.35);
  }
}
:root[data-theme="dark"]{
  --bg:#10141a; --surface:#171c24; --surface-2:#1d232c; --border:#2a323d;
  --ink:#eef1f5; --ink-2:#a8b1bd; --ink-muted:#6b7480;
  --accent:#d4a24e; --accent-ink:#10141a; --accent-tint:rgba(212,162,78,0.12);
  --pos:#3fae7c; --pos-bg:rgba(63,174,124,0.14);
  --neg:#d1604a; --neg-bg:rgba(209,96,74,0.14);
  --shadow:0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.35);
}
*{box-sizing:border-box;}
body{
  background:var(--bg); color:var(--ink);
  font-family:"IBM Plex Sans","IBM Plex Sans SC",-apple-system,sans-serif;
  line-height:1.5;
}
.num{font-family:"IBM Plex Mono",ui-monospace,monospace; font-variant-numeric:tabular-nums;}
h1,h2,h3{font-family:"Fraunces",Georgia,serif; text-wrap:balance; margin:0;}
.wrap{max-width:1180px; margin:0 auto; padding:40px 28px 80px;}

.hero{display:flex; flex-direction:column; gap:10px; margin-bottom:32px;}
.eyebrow{
  font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:0.14em;
  text-transform:uppercase; color:var(--accent); font-weight:600;
}
.hero h1{font-size:34px; font-weight:600; color:var(--ink);}
.hero p{color:var(--ink-2); font-size:15px; max-width:66ch; margin:0;}
.meta-row{
  display:flex; flex-wrap:wrap; gap:8px 22px; margin-top:6px;
  font-size:13px; color:var(--ink-muted);
}
.meta-row b{color:var(--ink-2); font-weight:500;}

.callout{
  display:flex; gap:12px; align-items:flex-start;
  background:var(--accent-tint); border:1px solid var(--border);
  border-left:3px solid var(--accent); border-radius:8px;
  padding:14px 16px; margin-bottom:28px; font-size:13.5px; color:var(--ink-2);
}
.callout .mark{font-size:15px; line-height:1.4; flex-shrink:0;}
.callout b{color:var(--ink);}

.section-title{
  font-size:13px; font-weight:600; letter-spacing:0.04em; text-transform:uppercase;
  color:var(--ink-muted); margin:0 0 14px;
}
.matrix-scroll{overflow-x:auto; border:1px solid var(--border); border-radius:10px; background:var(--surface); box-shadow:var(--shadow);}
table.matrix{border-collapse:collapse; width:100%; min-width:820px;}
table.matrix th, table.matrix td{padding:0; text-align:center;}
table.matrix thead tr.grp th{
  font-size:11px; letter-spacing:0.08em; text-transform:uppercase; color:var(--ink-muted);
  font-weight:600; padding:12px 8px 4px; border-bottom:1px solid var(--border);
}
table.matrix thead tr.sub th{
  font-size:12px; color:var(--ink-2); font-weight:500; padding:2px 8px 10px;
  border-bottom:1px solid var(--border);
}
table.matrix tbody th{
  text-align:left; font-family:"IBM Plex Mono",monospace; font-weight:600; font-size:14px;
  padding:10px 16px; border-bottom:1px solid var(--border); white-space:nowrap;
  position:sticky; left:0; background:var(--surface);
}
table.matrix tbody tr:last-child th, table.matrix tbody tr:last-child td{border-bottom:none;}
td.cell{
  border-bottom:1px solid var(--border); border-left:1px solid var(--border);
  cursor:pointer; padding:9px 6px; transition:box-shadow .12s ease;
}
td.cell:hover{box-shadow:inset 0 0 0 1.5px var(--accent);}
td.cell.selected{box-shadow:inset 0 0 0 2px var(--accent); background:var(--accent-tint);}
td.cell .ret{font-family:"IBM Plex Mono",monospace; font-weight:600; font-size:15px;}
td.cell .sub{font-size:10.5px; color:var(--ink-muted); margin-top:2px; font-family:"IBM Plex Mono",monospace;}
td.cell.na{color:var(--ink-muted);}

.detail{margin-top:36px;}
.detail-head{display:flex; align-items:baseline; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:18px;}
.detail-head h2{font-size:22px; font-weight:600;}
.detail-head .sub{font-size:13px; color:var(--ink-muted); font-family:"IBM Plex Mono",monospace;}

.tiles{display:grid; grid-template-columns:repeat(5,1fr); gap:1px; background:var(--border);
  border:1px solid var(--border); border-radius:10px; overflow:hidden; margin-bottom:20px; box-shadow:var(--shadow);}
.tile{background:var(--surface); padding:16px 18px;}
.tile .label{font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-muted); margin-bottom:6px;}
.tile .value{font-family:"IBM Plex Mono",monospace; font-size:22px; font-weight:600; letter-spacing:-0.01em;}
.tile .value.pos{color:var(--pos);} .tile .value.neg{color:var(--neg);}

.chart-card{background:var(--surface); border:1px solid var(--border); border-radius:10px;
  padding:18px 20px 14px; box-shadow:var(--shadow); margin-bottom:20px;}
.chart-card .chead{display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;}
.chart-card .chead .title{font-size:13px; font-weight:600; color:var(--ink-2);}
.chart-card .chead .legend{display:flex; gap:14px; font-size:11.5px; color:var(--ink-muted);}
.chart-card .chead .legend span{display:inline-flex; align-items:center; gap:5px;}
.chart-card .chead .legend i{width:8px; height:8px; border-radius:50%; display:inline-block;}
.chart-card .chead .legend i.tri{width:0; height:0; border-radius:0; border-left:4px solid transparent; border-right:4px solid transparent; border-bottom:7px solid var(--accent); background:none;}
.chart-wrap{overflow-x:auto;}
svg.eqchart{width:100%; height:auto; display:block; min-width:640px;}
svg.eqchart text{font-family:"IBM Plex Mono",monospace; fill:var(--ink-muted);}
svg.eqchart .gridline{stroke:var(--border); stroke-width:1;}
svg.eqchart .baseline{stroke:var(--ink-muted); stroke-dasharray:3 3; stroke-width:1;}
svg.eqchart .eqline{fill:none; stroke:var(--accent); stroke-width:2; stroke-linejoin:round; stroke-linecap:round;}
svg.eqchart .eqfill{fill:url(#eqGradient);}
.trademarker{cursor:pointer;}

.table-card{background:var(--surface); border:1px solid var(--border); border-radius:10px; box-shadow:var(--shadow); overflow:hidden;}
.table-card .chead{padding:16px 20px 0;}
.table-scroll{overflow-x:auto; padding:0 0 4px;}
table.trades{border-collapse:collapse; width:100%; min-width:760px; font-size:13px;}
table.trades th{
  text-align:left; padding:10px 16px; font-size:11px; letter-spacing:.05em; text-transform:uppercase;
  color:var(--ink-muted); border-bottom:1px solid var(--border); white-space:nowrap; font-weight:600;
}
table.trades td{padding:9px 16px; border-bottom:1px solid var(--border); white-space:nowrap; font-family:"IBM Plex Mono",monospace;}
table.trades tbody tr:last-child td{border-bottom:none;}
table.trades td.dir-long{color:var(--pos);} table.trades td.dir-short{color:var(--neg);}
table.trades td.pnl-pos{color:var(--pos); font-weight:600;} table.trades td.pnl-neg{color:var(--neg); font-weight:600;}
.reason-pill{
  display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px;
  background:var(--surface-2); color:var(--ink-2); font-family:"IBM Plex Mono",monospace;
}
.empty-note{padding:28px 20px; color:var(--ink-muted); font-size:13.5px; text-align:center;}

footer{margin-top:48px; padding-top:20px; border-top:1px solid var(--border);
  font-size:12.5px; color:var(--ink-muted); display:flex; flex-direction:column; gap:6px;}
footer code{font-family:"IBM Plex Mono",monospace; background:var(--surface-2); padding:1px 6px; border-radius:4px; color:var(--ink-2);}

@media (max-width: 720px){
  .tiles{grid-template-columns:repeat(2,1fr);} .tile:last-child{grid-column:1 / -1;}
  .hero h1{font-size:27px;}
}
</style>

<div class="wrap">

  <div class="hero">
    <div class="eyebrow">Key-Zone Breakout &middot; 对照组回测</div>
    <h1>关键区突破 vs 平台突破</h1>
    <p>正向逐 bar 状态机、严格独立验证、次日开盘执行——__N_TICKERS__ 个标的 &times; __N_TOUCHES__ 档验证门槛 &times; 3 种止盈版本，共 __N_COMBOS__ 组回测结果。点击下方矩阵任一格查看资金曲线、价格/成交量与逐笔交易。</p>
    <div class="meta-row">
      <span><b>标的</b> __TICKER_LIST__</span>
      <span><b>初始资金</b> __CAPITAL__</span>
    </div>
  </div>

  <div class="callout">
    <span class="mark">&#9888;&#65039;</span>
    <div><b>小样本提示：</b>验证次数门槛越高，交易越稀少，胜率/盈亏比在小样本下不太可靠。三个止盈版本里"基准版"与"趋势版"仅在浮盈曾经触及固定止盈上限时才会分叉，多数行里两者结果相同。</div>
  </div>

  <div class="section-title">全部组合 &middot; 按总收益率着色</div>
  <div class="matrix-scroll">
    <table class="matrix" id="matrix"></table>
  </div>

  <div class="detail" id="detail"></div>

  <footer>
    <div>方法说明：局域高低点用严格 5 日 pivot + 确认延迟；两次触碰之间须先离开区间 1&times;ATR 再回来才算独立验证；区间建立后固定不动，验证不够就提前突破则永久作废；每个关键区一生只有一次突破信号，次日开盘价须落在信号自带的可接受区间内才执行，否则放弃不追价；止盈追踪的激活门槛按初始风险的 R 倍数计算。详见 <code>signals/key_zone.py</code> 与 <code>strategies/key_zone_breakout.py</code> 模块 docstring。</div>
    <div>本页由 <code>script/build_kz_dashboard.py</code> 生成，重新生成：<code>python script/build_kz_dashboard.py</code></div>
  </footer>

</div>

<script>
const DATA = __DATA_JSON__;

const TICKER_LABEL = __TICKER_LABEL_JSON__;
const CCY = __CCY_JSON__;
const TICKERS = Object.keys(TICKER_LABEL);
const VARIANTS = ["original","baseline","trend"];
const VARIANT_LABEL = {original:"原始版",baseline:"基准版",trend:"趋势版"};
const TOUCHES = __TOUCHES_JSON__;

const summaryByKey = {};
DATA.summary.forEach(r => { summaryByKey[`${r.ticker}|${r.touches}|${r.variant}`] = r; });

let selectedKey = null;
for (const row of DATA.summary){ if (row.n_trades > 0){ selectedKey = `${row.ticker}|${row.touches}|${row.variant}`; break; } }
if (!selectedKey) selectedKey = `${TICKERS[0]}|${TOUCHES[0]}|baseline`;

function fmtPct(v){
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = v.toFixed(2) + "%";
  return v > 0 ? "+" + s : s;
}
function fmtPF(v){
  if (v === null || v === undefined) return "—";
  if (v === "inf") return "&infin;";
  return v.toFixed(2);
}
function retClass(v){ if (v === null || v === undefined) return ""; return v > 0 ? "pos" : (v < 0 ? "neg" : ""); }

function buildMatrix(){
  const table = document.getElementById("matrix");
  let grpRow = "<tr class='grp'><th></th>";
  TOUCHES.forEach(t => grpRow += `<th colspan="3">验证 ${t} 次</th>`);
  grpRow += "</tr>";
  let subRow = "<tr class='sub'><th></th>";
  TOUCHES.forEach(() => VARIANTS.forEach(v => subRow += `<th>${VARIANT_LABEL[v]}</th>`));
  subRow += "</tr>";

  let body = "";
  TICKERS.forEach(tk => {
    body += `<tr><th>${TICKER_LABEL[tk]}</th>`;
    TOUCHES.forEach(t => VARIANTS.forEach(v => {
      const key = `${tk}|${t}|${v}`;
      const r = summaryByKey[key];
      const sel = key === selectedKey ? " selected" : "";
      if (!r || r.n_trades === 0){
        body += `<td class="cell na${sel}" data-key="${key}"><div class="ret">&mdash;</div><div class="sub">0 笔</div></td>`;
      } else {
        body += `<td class="cell${sel}" data-key="${key}">`
              + `<div class="ret ${retClass(r.total_return)}">${fmtPct(r.total_return)}</div>`
              + `<div class="sub">胜率 ${r.win_rate}% · ${r.n_trades} 笔</div></td>`;
      }
    }));
    body += "</tr>";
  });

  table.innerHTML = `<thead>${grpRow}${subRow}</thead><tbody>${body}</tbody>`;
  table.querySelectorAll("td.cell").forEach(td => {
    td.addEventListener("click", () => {
      selectedKey = td.dataset.key;
      table.querySelectorAll("td.cell").forEach(x => x.classList.remove("selected"));
      td.classList.add("selected");
      renderDetail();
    });
  });
}

function timeScale(dates, ML, plotW){
  const ts = dates.map(d => new Date(d).getTime());
  const t0 = ts[0], t1 = ts[ts.length-1];
  return t => ML + (t1===t0 ? 0 : (t-t0)/(t1-t0))*plotW;
}

function buildEquitySVG(curve, log, ccy){
  const W = 880, H = 260, ML = 58, MR = 14, MT = 14, MB = 26;
  const plotW = W - ML - MR, plotH = H - MT - MB;
  const dates = curve.dates.map(d => new Date(d).getTime());
  const eq = curve.equity;
  const x0 = dates[0], x1 = dates[dates.length-1];
  const xScale = t => ML + (x1 === x0 ? 0 : (t - x0) / (x1 - x0)) * plotW;
  let yMin = Math.min(...eq, 100000), yMax = Math.max(...eq, 100000);
  const pad = (yMax - yMin) * 0.08 || yMax * 0.05;
  yMin -= pad; yMax += pad;
  const yScale = v => MT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

  let path = "M " + dates.map((t,i) => `${xScale(t).toFixed(1)},${yScale(eq[i]).toFixed(1)}`).join(" L ");
  let areaPath = path + ` L ${xScale(x1).toFixed(1)},${(MT+plotH).toFixed(1)} L ${xScale(x0).toFixed(1)},${(MT+plotH).toFixed(1)} Z`;

  let grid = "";
  const steps = 4;
  for (let i=0;i<=steps;i++){
    const v = yMin + (yMax-yMin)*i/steps;
    const y = yScale(v);
    grid += `<line class="gridline" x1="${ML}" x2="${W-MR}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"/>`;
    grid += `<text x="${ML-8}" y="${(y+3.5).toFixed(1)}" font-size="10.5" text-anchor="end">${ccy}${Math.round(v).toLocaleString()}</text>`;
  }
  const by = yScale(100000);
  grid += `<line class="baseline" x1="${ML}" x2="${W-MR}" y1="${by.toFixed(1)}" y2="${by.toFixed(1)}"/>`;

  let xlabels = "";
  [0, Math.floor(dates.length/2), dates.length-1].forEach(i => {
    xlabels += `<text x="${xScale(dates[i]).toFixed(1)}" y="${H-6}" font-size="10.5" text-anchor="middle">${curve.dates[i]}</text>`;
  });

  let markers = "";
  (log||[]).forEach(t => {
    const et = new Date(t.exit_dt).getTime();
    if (et < x0 || et > x1) return;
    const cx = xScale(et);
    let cy = yScale(eq[eq.length-1]);
    for (let i=0;i<dates.length-1;i++){
      if (et >= dates[i] && et <= dates[i+1]){
        const frac = (dates[i+1]===dates[i]) ? 0 : (et-dates[i])/(dates[i+1]-dates[i]);
        const v = eq[i] + (eq[i+1]-eq[i])*frac;
        cy = yScale(v);
        break;
      }
    }
    const win = t.pnl > 0;
    markers += `<circle class="trademarker" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="4" fill="${win?'var(--pos)':'var(--neg)'}" stroke="var(--surface)" stroke-width="1.5"><title>${t.entry_dt} → ${t.exit_dt} · ${t.direction==='long'?'多':'空'} · ${t.pnl>0?'+':''}${t.pnl} (${t.return_pct>0?'+':''}${t.return_pct}%) · ${t.exit_reason}</title></circle>`;
  });

  return `<svg class="eqchart" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
    <defs><linearGradient id="eqGradient" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
    </linearGradient></defs>
    ${grid}
    <path class="eqfill" d="${areaPath}"/>
    <path class="eqline" d="${path}"/>
    ${markers}
    ${xlabels}
  </svg>`;
}

function buildPriceVolumeSVG(price, log, ccy){
  const W = 880, ML = 58, MR = 14, MT = 10, GAP = 10, MB = 26;
  const H_PRICE = 220, H_VOL = 80;
  const H = MT + H_PRICE + GAP + H_VOL + MB;
  const plotW = W - ML - MR;
  const dates = price.dates.map(d => new Date(d).getTime());
  const n = dates.length;
  const x0 = dates[0], x1 = dates[n-1];
  const xScale = t => ML + (x1===x0 ? 0 : (t-x0)/(x1-x0))*plotW;
  const slot = plotW / n;
  const candleW = Math.max(1.2, Math.min(6, slot*0.6));

  let pMin = Math.min(...price.low), pMax = Math.max(...price.high);
  const pPad = (pMax-pMin)*0.06 || pMax*0.05;
  pMin -= pPad; pMax += pPad;
  const yPrice = MT + H_PRICE;
  const priceScale = v => MT + H_PRICE - ((v-pMin)/(pMax-pMin))*H_PRICE;

  const vMax = Math.max(...price.volume, 1);
  const volTop = MT + H_PRICE + GAP;
  const volScale = v => volTop + H_VOL - (v/vMax)*H_VOL;

  let grid = "";
  for (let i=0;i<=3;i++){
    const v = pMin + (pMax-pMin)*i/3;
    const y = priceScale(v);
    grid += `<line class="gridline" x1="${ML}" x2="${W-MR}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"/>`;
    grid += `<text x="${ML-8}" y="${(y+3.5).toFixed(1)}" font-size="10.5" text-anchor="end">${ccy}${v.toLocaleString(undefined,{maximumFractionDigits:1})}</text>`;
  }

  let candles = "", vbars = "";
  for (let i=0;i<n;i++){
    const cx = xScale(dates[i]);
    const up = price.close[i] >= price.open[i];
    const col = up ? "var(--pos)" : "var(--neg)";
    const yHigh = priceScale(price.high[i]), yLow = priceScale(price.low[i]);
    const yO = priceScale(price.open[i]), yC = priceScale(price.close[i]);
    const bodyTop = Math.min(yO,yC), bodyH = Math.max(1, Math.abs(yC-yO));
    candles += `<line x1="${cx.toFixed(1)}" x2="${cx.toFixed(1)}" y1="${yHigh.toFixed(1)}" y2="${yLow.toFixed(1)}" stroke="${col}" stroke-width="1"/>`;
    candles += `<rect x="${(cx-candleW/2).toFixed(1)}" y="${bodyTop.toFixed(1)}" width="${candleW.toFixed(1)}" height="${bodyH.toFixed(1)}" fill="${col}"/>`;
    const vy = volScale(price.volume[i]);
    vbars += `<rect x="${(cx-candleW/2).toFixed(1)}" y="${vy.toFixed(1)}" width="${candleW.toFixed(1)}" height="${(volTop+H_VOL-vy).toFixed(1)}" fill="${col}" opacity="0.55"/>`;
  }

  let xlabels = "";
  [0, Math.floor(n/2), n-1].forEach(i => {
    xlabels += `<text x="${xScale(dates[i]).toFixed(1)}" y="${H-6}" font-size="10.5" text-anchor="middle">${price.dates[i]}</text>`;
  });
  const volLabel = `<text x="${ML-8}" y="${(volTop+10).toFixed(1)}" font-size="10.5" text-anchor="end">量</text>`;

  let markers = "";
  (log||[]).forEach(t => {
    const et = new Date(t.entry_dt).getTime(), xt = new Date(t.exit_dt).getTime();
    if (et >= x0 && et <= x1){
      const ex = xScale(et), ey = priceScale(t.entry_price);
      markers += `<path class="trademarker" d="M ${ex.toFixed(1)} ${(ey+9).toFixed(1)} l -4 7 l 8 0 z" fill="var(--accent)"><title>开仓 ${t.entry_dt} · ${t.direction==='long'?'多':'空'} · ${ccy}${t.entry_price}</title></path>`;
    }
    if (xt >= x0 && xt <= x1){
      const cx2 = xScale(xt), cy2 = priceScale(t.exit_price);
      const win = t.pnl > 0;
      markers += `<circle class="trademarker" cx="${cx2.toFixed(1)}" cy="${cy2.toFixed(1)}" r="3.5" fill="${win?'var(--pos)':'var(--neg)'}" stroke="var(--surface)" stroke-width="1.2"><title>平仓 ${t.exit_dt} · ${ccy}${t.exit_price} · ${t.exit_reason}</title></circle>`;
    }
  });

  return `<svg class="eqchart" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
    ${grid}
    ${candles}
    ${vbars}
    ${markers}
    ${volLabel}
    ${xlabels}
  </svg>`;
}

function renderDetail(){
  const parts = selectedKey.split("|");
  const ticker = parts[0], touches = Number(parts[1]), variant = parts[2];
  const r = summaryByKey[selectedKey];
  const curve = DATA.equity_curves[selectedKey];
  const price = DATA.price_series[ticker];
  const log = DATA.trade_logs[selectedKey] || [];
  const ccy = CCY[ticker];
  const el = document.getElementById("detail");

  if (!r){ el.innerHTML = "<div class='empty-note'>没有数据。</div>"; return; }

  const tiles = `
    <div class="tiles">
      <div class="tile"><div class="label">交易笔数</div><div class="value">${r.n_trades}</div></div>
      <div class="tile"><div class="label">胜率</div><div class="value">${r.n_trades ? r.win_rate + '%' : '—'}</div></div>
      <div class="tile"><div class="label">总收益率</div><div class="value ${retClass(r.total_return)}">${fmtPct(r.total_return)}</div></div>
      <div class="tile"><div class="label">盈亏比</div><div class="value">${fmtPF(r.profit_factor)}</div></div>
      <div class="tile"><div class="label">最大回撤</div><div class="value neg">${r.max_drawdown}%</div></div>
    </div>`;

  const chartHtml = curve ? `
    <div class="chart-card">
      <div class="chead">
        <div class="title">资金曲线</div>
        <div class="legend">
          <span><i style="background:var(--pos)"></i>盈利离场</span>
          <span><i style="background:var(--neg)"></i>亏损离场</span>
        </div>
      </div>
      <div class="chart-wrap">${buildEquitySVG(curve, log, ccy)}</div>
    </div>` : "";

  const priceHtml = price ? `
    <div class="chart-card">
      <div class="chead">
        <div class="title">价格走势 &amp; 成交量</div>
        <div class="legend">
          <span><i class="tri"></i>开仓</span>
          <span><i style="background:var(--pos)"></i>盈利平仓</span>
          <span><i style="background:var(--neg)"></i>亏损平仓</span>
        </div>
      </div>
      <div class="chart-wrap">${buildPriceVolumeSVG(price, log, ccy)}</div>
    </div>` : "";

  let tradeRows = "";
  if (log.length){
    log.forEach(t => {
      const pnlClass = t.pnl > 0 ? "pnl-pos" : "pnl-neg";
      const dirClass = t.direction === "long" ? "dir-long" : "dir-short";
      tradeRows += `<tr>
        <td>${t.entry_dt}</td><td>${t.exit_dt}</td>
        <td class="${dirClass}">${t.direction === 'long' ? '多' : '空'}</td>
        <td>${ccy}${t.entry_price}</td><td>${ccy}${t.exit_price}</td>
        <td class="${pnlClass}">${t.pnl>0?'+':''}${t.pnl}</td>
        <td class="${pnlClass}">${t.return_pct>0?'+':''}${t.return_pct}%</td>
        <td><span class="reason-pill">${t.exit_reason}</span></td>
        <td>${t.validations}</td>
      </tr>`;
    });
  }
  const tableHtml = `
    <div class="table-card">
      <div class="chead"><div class="title">逐笔交易</div></div>
      <div class="table-scroll">
        ${log.length ? `<table class="trades">
          <thead><tr><th>开仓日</th><th>平仓日</th><th>方向</th><th>开仓价</th><th>平仓价</th><th>盈亏</th><th>收益率</th><th>离场原因</th><th>触碰次数</th></tr></thead>
          <tbody>${tradeRows}</tbody>
        </table>` : `<div class="empty-note">这组配置在回测区间内没有产生任何交易${r.abandoned ? '（' + r.abandoned + ' 次信号因开盘价不在可接受区间被放弃）' : ''}。</div>`}
      </div>
    </div>`;

  el.innerHTML = `
    <div class="detail-head">
      <h2>${TICKER_LABEL[ticker]} · 验证 ${touches} 次 · ${VARIANT_LABEL[variant]}</h2>
      <div class="sub">已放弃 ${r.abandoned} 笔 · 保证金不足拒绝 ${r.rejected} 笔</div>
    </div>
    ${tiles}
    ${chartHtml}
    ${priceHtml}
    ${tableHtml}
  `;
}

buildMatrix();
renderDetail();
</script>
'''


def render_html(data: dict, targets, touches_list, capital) -> str:
    ticker_label = {t[0]: t[5] for t in targets}
    ccy = {t[0]: t[6] for t in targets}
    html = TEMPLATE
    html = html.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("__TICKER_LABEL_JSON__", json.dumps(ticker_label, ensure_ascii=False))
    html = html.replace("__CCY_JSON__", json.dumps(ccy, ensure_ascii=False))
    html = html.replace("__TOUCHES_JSON__", json.dumps(touches_list))
    html = html.replace("__N_TICKERS__", str(len(targets)))
    html = html.replace("__N_TOUCHES__", str(len(touches_list)))
    html = html.replace("__N_COMBOS__", str(len(targets) * len(touches_list) * len(VARIANTS)))
    html = html.replace("__TICKER_LIST__", " · ".join(ticker_label.values()))
    html = html.replace("__CAPITAL__", f"{capital:,.0f}")
    return html


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tickers", default=None,
                  help="逗号分隔的标的列表，缺省用内置的 6 个默认标的（不能与 --start/--end/--source/--futures 混用自定义单标的）")
    p.add_argument("--start", default=None, help="仅在 --tickers 指定单个标的时生效")
    p.add_argument("--end", default=None, help="仅在 --tickers 指定单个标的时生效")
    p.add_argument("--source", default="yf", choices=["yf", "ak"], help="仅在自定义单标的时生效")
    p.add_argument("--futures", action="store_true", help="仅在自定义单标的时生效")
    p.add_argument("--touches", default="2,3", help="验证次数门槛，逗号分隔 (默认 2,3)")
    p.add_argument("--capital", default=100_000, type=float, help="初始资金 (默认 100000)")
    p.add_argument("--max-points", default=250, type=int, help="每条曲线的降采样点数上限 (默认 250)")
    p.add_argument("--out", default="backtest/plots/key_zone_dashboard.html", help="输出 HTML 路径")
    return p.parse_args()


def main():
    args = parse_args()
    touches_list = [int(x) for x in args.touches.split(",")]

    if args.tickers:
        tickers = args.tickers.split(",")
        if len(tickers) == 1 and args.start and args.end:
            targets = [(tickers[0], args.source, args.futures, args.start, args.end,
                       tickers[0], "¥" if args.futures else "$")]
        else:
            by_ticker = {t[0]: t for t in DEFAULT_TARGETS}
            targets = [by_ticker[t] for t in tickers if t in by_ticker]
            missing = [t for t in tickers if t not in by_ticker]
            if missing:
                raise SystemExit(f"这些标的不在默认列表里，单独跑时请配合 --start/--end/--source: {missing}")
    else:
        targets = DEFAULT_TARGETS

    data = build_data(targets, touches_list, args.capital, args.max_points)
    html = render_html(data, targets, touches_list, args.capital)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"\n已写入 {out_path} ({len(html)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
