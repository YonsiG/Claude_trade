from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

from tools.trade import buy, margin_call, force_close

RESULTS_DIR = Path(__file__).parent / "results"
PLOTS_DIR = Path(__file__).parent / "plots"
RESULTS_DIR.mkdir(exist_ok=True)
PLOTS_DIR.mkdir(exist_ok=True)

_TRADING_DAYS = 252

# CSV 中除 equity_curve / benchmark_curve / trades 外的标量指标
_METRIC_KEYS = [
    "ticker", "start", "end", "initial_capital", "final_equity",
    "total_return", "annual_return", "sharpe", "max_drawdown", "calmar",
    "bench_total_return", "bench_annual_return", "bench_sharpe", "bench_max_drawdown",
    "excess_return", "n_trades", "win_rate", "profit_factor",
    "avg_bars_held", "avg_win", "avg_loss", "n_margin_calls",
    "n_rejected", "min_margin_per_lot", "max_reject_budget", "bust",
]


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------

def _years(index) -> float:
    if len(index) < 2:
        return 0.0
    days = (index[-1] - index[0]).days
    return days / 365.25 if days > 0 else 0.0


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = ((equity - peak) / peak.replace(0, np.nan))
    return float(dd.min() * 100) if len(dd.dropna()) else 0.0


def _curve_metrics(equity: pd.Series, rf: float = 0.0) -> dict:
    """资金曲线的通用指标。rf 为年化无风险利率（0.04 = 4%）。"""
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return {"total_return": 0.0, "annual_return": 0.0,
                "sharpe": 0.0, "max_drawdown": 0.0, "calmar": 0.0}

    returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100

    years = _years(equity.index)
    if years > 0 and equity.iloc[-1] > 0:
        annual_return = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100
    else:
        annual_return = 0.0

    excess = returns - rf / _TRADING_DAYS
    sd = excess.std()
    sharpe = float(excess.mean() / sd * np.sqrt(_TRADING_DAYS)) if sd > 0 else 0.0

    mdd = _max_drawdown(equity)
    calmar = annual_return / abs(mdd) if mdd < 0 else 0.0

    return {"total_return": float(total_return), "annual_return": float(annual_return),
            "sharpe": sharpe, "max_drawdown": mdd, "calmar": float(calmar)}


def _trade_metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "avg_bars_held": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
    pnl = trades["pnl"].dropna()
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gross_loss = abs(losses.sum())
    return {
        "n_trades": int(len(trades)),
        "win_rate": float(len(wins) / len(pnl) * 100) if len(pnl) else 0.0,
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf"),
        "avg_bars_held": float(trades["bars_held"].mean()),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
    }


# ---------------------------------------------------------------------------
# 基准
# ---------------------------------------------------------------------------

def benchmark_curve(strategy, index) -> pd.Series:
    """
    买入持有基准：首根 bar 收盘价满仓买入，持有到最后。

    使用策略自身的手续费/合约/保证金参数，保证可比（基准只付一次开仓费用）。

    期货基准**沿用策略的保证金率**，即同样的杠杆。这是必须的：黄金 AU 一手
    名义价值 = 价格 × 1000，十万本金按全额根本买不起一手，无杠杆基准会退化成
    一条平线。同杠杆对比才能把「择时的贡献」和「杠杆的贡献」分开。

    基准同样受强平约束：杠杆持有一旦权益跌破所需保证金就被平掉，之后保持空仓。
    否则杠杆下的"买入持有"会算出负权益（-118% 之类的数字），夏普和回撤全部失真。
    被强平意味着裸持有这个品种在该区间是活不下来的——这本身就是有效信息。
    """
    close = strategy.df["close"].reindex(index).astype(float)
    state = {"cash": float(strategy.initial_capital), "shares": 0.0}
    futures = getattr(strategy, "futures", False)
    multiplier = getattr(strategy, "multiplier", 1.0)
    margin_rate = getattr(strategy, "margin_rate", 1.0)
    fee_rate = getattr(strategy, "fee_rate", 0.0)
    fee_per_lot = getattr(strategy, "fee_per_lot", 0.0)

    buy(state, float(close.iloc[0]), futures=futures, multiplier=multiplier,
        fee_rate=fee_rate, fee_per_lot=fee_per_lot, margin_rate=margin_rate)

    factor = multiplier if futures else 1.0
    values = []
    for dt, px in close.items():
        if state["shares"] != 0:
            margin_call(state, float(px), dt, margin_rate, futures=futures,
                        multiplier=multiplier,
                        maintenance=getattr(strategy, "maintenance", 1.0),
                        fee_rate=fee_rate, fee_per_lot=fee_per_lot)
            force_close(state, float(px), dt, futures=futures, multiplier=multiplier)
        # 单根 bar 跳空（涨跌停）可能一次性击穿保证金线，强平时权益已经是负的——
        # 钱追不回来。资金曲线按 0 封底：本金亏光就是 -100%，欠券商的部分是另一回事，
        # 不封底会算出 -200% 这种数字，夏普和回撤全部失真。
        values.append(max(0.0, state["cash"] + state["shares"] * px * factor))
    return pd.Series(values, index=index, name="benchmark")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run(ticker: str, start: str, end: str, strategy, rf: float = 0.0) -> dict:
    """
    执行策略，返回绩效指标 + 资金曲线 + 基准曲线 + 交易流水。

    Args:
        ticker/start/end: 仅用于结果标注。
        strategy: 已实例化的 BaseStrategy。
        rf:       年化无风险利率，用于夏普比率（0.04 = 4%）。默认 0。
    """
    equity = strategy.run()
    trades = strategy.trade_log()
    bench = benchmark_curve(strategy, equity.index)

    strat_m = _curve_metrics(equity, rf)
    bench_m = _curve_metrics(bench, rf)

    summary = {
        "ticker": ticker,
        "start": start,
        "end": end,
        "initial_capital": float(strategy.initial_capital),
        "final_equity": float(equity.iloc[-1]),
        **strat_m,
        **{f"bench_{k}": v for k, v in bench_m.items()},
        "excess_return": strat_m["total_return"] - bench_m["total_return"],
        **_trade_metrics(trades),
        "n_margin_calls": len(getattr(strategy, "margin_calls", [])),
        "n_rejected": len(getattr(strategy, "rejected", [])),
        "min_margin_per_lot": min([r["margin_per_lot"] for r in
                                   getattr(strategy, "rejected", [])], default=0.0),
        "max_reject_budget": max([r["budget"] for r in
                                  getattr(strategy, "rejected", [])], default=0.0),
        "bust": strategy.bust is not None if hasattr(strategy, "bust") else False,
        "equity_curve": equity,
        "benchmark_curve": bench,
        "trades": trades,
    }
    return summary


def _w(text: str) -> int:
    """字符串的终端显示宽度（CJK 字符占两格）。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, right: bool = False) -> str:
    fill = " " * max(0, width - _w(text))
    return fill + text if right else text + fill


def report(summary: dict) -> str:
    """把 summary 排成一段可直接 print 的文本。"""
    s = summary
    L, C = 18, 14
    sep = "-" * (L + C * 2)

    def row(label, a, b=None, fmt="{:.2f}"):
        line = _pad(label, L) + _pad(fmt.format(a), C, right=True)
        if b is not None:
            line += _pad(fmt.format(b), C, right=True)
        return line

    lines = [
        _pad("", L) + _pad("策略", C, right=True) + _pad("买入持有", C, right=True),
        sep,
        row("总收益率 (%)",  s["total_return"],   s["bench_total_return"]),
        row("年化收益 (%)",  s["annual_return"],  s["bench_annual_return"]),
        row("夏普比率",      s["sharpe"],         s["bench_sharpe"]),
        row("最大回撤 (%)",  s["max_drawdown"],   s["bench_max_drawdown"]),
        row("Calmar",        s["calmar"],         s["bench_calmar"]),
        sep,
        row("交易次数",       s["n_trades"], fmt="{:d}"),
        row("胜率 (%)",       s["win_rate"], fmt="{:.1f}"),
        row("盈亏比",         s["profit_factor"]),
        row("平均持仓 (bar)", s["avg_bars_held"], fmt="{:.1f}"),
        row("平均盈利 / 亏损", s["avg_win"], s["avg_loss"], fmt="{:.0f}"),
    ]
    if s.get("n_margin_calls"):
        lines.append(f"!! 保证金不足被强平 {s['n_margin_calls']} 次")
    if s.get("bust"):
        lines.append("!! 权益归零，彻底出局")
    if s.get("n_rejected"):
        if s.get("min_margin_per_lot"):
            lines.append(
                f"!! 资金不足以开出一手，{s['n_rejected']} 次开仓被拒 —— "
                f"一手最低需保证金 {s['min_margin_per_lot']:,.0f}，"
                f"而信号给出的最大可用资金只有 {s['max_reject_budget']:,.0f}"
                f"（本金 {s['initial_capital']:,.0f} × 仓位比例）")
        else:
            # 组合层：各子仓合约不同，单一金额无法对比，指向子仓明细
            lines.append(f"!! 资金不足以开出一手，{s['n_rejected']} 次开仓被拒 "
                         f"—— 具体品种与金额见子仓明细")
    if s["n_trades"] == 0 and not s.get("n_rejected"):
        lines.append("!! 零成交：信号从未触发，指标无意义")
    return "\n".join(lines)


def plot(summary: dict, strategy_name: str):
    import matplotlib.pyplot as plt

    ticker = summary["ticker"]
    equity, bench, trades = summary["equity_curve"], summary["benchmark_curve"], summary["trades"]

    fig, ax = plt.subplots(figsize=(12, 5))
    equity.plot(ax=ax, label=strategy_name, lw=1.4, color="#1f77b4", zorder=3)
    bench.plot(ax=ax, label="Buy & Hold", lw=1.2, color="#999999", ls="--", zorder=2)

    # 交易笔数不多时标出进出场点
    if 0 < len(trades) <= 200:
        for _, t in trades.iterrows():
            if t["entry_dt"] in equity.index:
                ax.scatter(t["entry_dt"], equity.loc[t["entry_dt"]], s=22, zorder=4,
                           marker="^" if t["direction"] == "long" else "v",
                           color="#2ca02c" if t["direction"] == "long" else "#d62728")
            if pd.notna(t["exit_dt"]) and t["exit_dt"] in equity.index:
                ax.scatter(t["exit_dt"], equity.loc[t["exit_dt"]], s=18, zorder=4,
                           marker="x", color="#444444")

    ax.set_title(f"{strategy_name} — {ticker}  |  "
                 f"Return: {summary['total_return']:.1f}% (B&H {summary['bench_total_return']:.1f}%)  "
                 f"Sharpe: {summary['sharpe']:.2f}  "
                 f"MDD: {summary['max_drawdown']:.1f}%  "
                 f"Trades: {summary['n_trades']}")
    ax.set_ylabel("Portfolio Value ($)")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)

    path = PLOTS_DIR / f"{ticker}_{strategy_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Plot saved: {path}")


def save_results(summary: dict, strategy_name: str):
    """写出两个文件：指标汇总 和 交易流水。"""
    ticker = summary["ticker"]

    row = {k: summary[k] for k in _METRIC_KEYS if k in summary}
    row["strategy"] = strategy_name
    metrics_path = RESULTS_DIR / f"{ticker}_{strategy_name}_metrics.csv"
    pd.DataFrame([row]).to_csv(metrics_path, index=False)

    trades_path = RESULTS_DIR / f"{ticker}_{strategy_name}_trades.csv"
    summary["trades"].to_csv(trades_path, index=False)

    print(f"Metrics saved: {metrics_path}")
    print(f"Trades saved : {trades_path}  ({summary['n_trades']} 笔)")
