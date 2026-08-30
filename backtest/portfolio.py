"""多标的组合回测。

把资金按权重切成若干「子仓」（sleeve），每个子仓独立跑一个策略，
再把资金曲线相加得到组合曲线。

**范围说明**：这是「资金分配 + 独立执行 + 结果聚合」，不是截面选股，
也不做子仓之间的风险预算或再平衡。每个子仓拿到自己的那份钱，各跑各的，
互不调拨。要做截面轮动或风险平价，需要一个能在每根 bar 上跨标的比较信号
的调度器，那是另一层东西。

日历对齐：美股和国内期货的交易日不同。取所有子仓日期的并集，各自
前向填充；子仓开始交易之前的日期按其初始资金计入（钱在账上没动）。

用法：
    from backtest.portfolio import run_portfolio, report_portfolio

    sleeves = {
        "AAPL": SingleSignalStrategy(df_aapl, engulfing),
        "RB0":  SingleSignalStrategy(df_rb, engulfing, futures=True, ticker="RB0"),
    }
    summary = run_portfolio(sleeves, capital=200_000)
    print(report_portfolio(summary))
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import engine
from .engine import _curve_metrics, _trade_metrics, _pad, RESULTS_DIR, PLOTS_DIR

_SLEEVE_COLUMNS = ["sleeve", "capital", "weight", "total_return", "annual_return",
                   "sharpe", "max_drawdown", "n_trades", "win_rate",
                   "n_margin_calls", "n_rejected", "bust"]


def _as_items(sleeves):
    if isinstance(sleeves, dict):
        return list(sleeves.items())
    return [(str(name), strat) for name, strat in sleeves]


def _align(curve: pd.Series, index, fill_before: float) -> pd.Series:
    """把子仓曲线对齐到组合日历：区间内前向填充，开始交易之前按初始资金计。"""
    out = curve.reindex(index)
    out = out.ffill()
    return out.fillna(fill_before)


def run_portfolio(sleeves, capital: float = None, weights=None,
                  rf: float = 0.0, start: str = None, end: str = None) -> dict:
    """
    Args:
        sleeves: {名称: 已实例化的策略} 或 [(名称, 策略), ...]
        capital: 组合总资金。给了就按 weights 切分并**覆盖**各策略的
                 initial_capital；不给则沿用各策略自带的资金，总额为其之和。
        weights: {名称: 权重}，不给则等权。会自动归一化。
        rf:      年化无风险利率，用于夏普。
    """
    items = _as_items(sleeves)
    if not items:
        raise ValueError("sleeves 为空，至少需要一个子仓")

    names = [n for n, _ in items]
    if weights is None:
        w = {n: 1.0 / len(items) for n in names}
    else:
        total_w = float(sum(weights[n] for n in names))
        if total_w <= 0:
            raise ValueError("权重之和必须为正")
        w = {n: weights[n] / total_w for n in names}

    if capital is not None:
        for name, strat in items:
            strat.initial_capital = float(capital) * w[name]
    total_capital = float(sum(s.initial_capital for _, s in items))

    # 逐子仓回测
    results = {}
    for name, strat in items:
        results[name] = engine.run(name, start, end, strat, rf=rf)

    # 组合日历 = 所有子仓日期的并集
    index = None
    for r in results.values():
        idx = r["equity_curve"].index
        index = idx if index is None else index.union(idx)
    index = index.sort_values()

    equity = pd.Series(0.0, index=index)
    bench = pd.Series(0.0, index=index)
    for name, strat in items:
        r = results[name]
        cap = float(strat.initial_capital)
        equity += _align(r["equity_curve"], index, cap)
        bench += _align(r["benchmark_curve"], index, cap)
    equity.name, bench.name = "equity", "benchmark"

    # 合并流水，加上 sleeve 列
    frames = []
    for name in names:
        t = results[name]["trades"]
        if not t.empty:
            frames.append(t.assign(sleeve=name))
    trades = (pd.concat(frames, ignore_index=True).sort_values("entry_dt")
              .reset_index(drop=True)) if frames else pd.DataFrame()

    # 子仓明细
    rows = []
    for name, strat in items:
        r = results[name]
        rows.append({
            "sleeve": name, "capital": float(strat.initial_capital), "weight": w[name],
            "total_return": r["total_return"], "annual_return": r["annual_return"],
            "sharpe": r["sharpe"], "max_drawdown": r["max_drawdown"],
            "n_trades": r["n_trades"], "win_rate": r["win_rate"],
            "n_margin_calls": r["n_margin_calls"], "n_rejected": r["n_rejected"],
            "bust": r["bust"],
        })
    sleeve_table = pd.DataFrame(rows)[_SLEEVE_COLUMNS]

    strat_m = _curve_metrics(equity, rf)
    bench_m = _curve_metrics(bench, rf)

    return {
        "ticker": "PORTFOLIO",
        "start": start, "end": end,
        "initial_capital": total_capital,
        "final_equity": float(equity.iloc[-1]),
        **strat_m,
        **{f"bench_{k}": v for k, v in bench_m.items()},
        "excess_return": strat_m["total_return"] - bench_m["total_return"],
        **_trade_metrics(trades),
        "n_margin_calls": int(sleeve_table["n_margin_calls"].sum()),
        "n_rejected": int(sleeve_table["n_rejected"].sum()),
        # 组合层不给单一的"一手保证金 vs 可用资金"数字：不同子仓是不同合约，
        # 把 A 子仓的保证金和 B 子仓的资金放在一起比较是没有意义的。
        # 留空让 report() 改用汇总口径，具体金额看子仓明细。
        "min_margin_per_lot": 0.0,
        "max_reject_budget": 0.0,
        "bust": bool(sleeve_table["bust"].any()),
        "equity_curve": equity,
        "benchmark_curve": bench,
        "trades": trades,
        "sleeves": sleeve_table,
        "results": results,
    }


def report_portfolio(summary: dict) -> str:
    """组合层汇总 + 子仓明细。"""
    lines = [engine.report(summary), "", "子仓明细:"]
    s = summary["sleeves"]
    head = (_pad("子仓", 10) + _pad("资金", 12, True) + _pad("收益%", 10, True)
            + _pad("夏普", 8, True) + _pad("回撤%", 10, True) + _pad("交易", 7, True)
            + _pad("胜率%", 8, True) + "  " + "备注")
    lines += [head, "-" * 70]
    for _, r in s.iterrows():
        note = []
        if r["n_margin_calls"]:
            note.append(f"强平{int(r['n_margin_calls'])}")
        if r["n_rejected"]:
            note.append(f"资金不足{int(r['n_rejected'])}")
        if r["bust"]:
            note.append("爆仓")
        lines.append(
            _pad(str(r["sleeve"]), 10)
            + _pad(f"{r['capital']:,.0f}", 12, True)
            + _pad(f"{r['total_return']:.2f}", 10, True)
            + _pad(f"{r['sharpe']:.2f}", 8, True)
            + _pad(f"{r['max_drawdown']:.2f}", 10, True)
            + _pad(f"{int(r['n_trades'])}", 7, True)
            + _pad(f"{r['win_rate']:.1f}", 8, True)
            + "  " + ",".join(note))
    return "\n".join(lines)


def plot_portfolio(summary: dict, name: str = "portfolio"):
    import matplotlib.pyplot as plt

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                  gridspec_kw={"height_ratios": [2, 1]})
    summary["equity_curve"].plot(ax=ax, label="Portfolio", lw=1.6, color="#1f77b4")
    summary["benchmark_curve"].plot(ax=ax, label="Buy & Hold", lw=1.2,
                                    color="#999999", ls="--")
    ax.set_title(f"Portfolio ({len(summary['sleeves'])} sleeves)  |  "
                 f"Return: {summary['total_return']:.1f}% "
                 f"(B&H {summary['bench_total_return']:.1f}%)  "
                 f"Sharpe: {summary['sharpe']:.2f}  "
                 f"MDD: {summary['max_drawdown']:.1f}%  "
                 f"Trades: {summary['n_trades']}")
    ax.set_ylabel("Portfolio Value")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)

    # 各子仓归一化曲线，看谁在拖后腿
    for sleeve, r in summary["results"].items():
        c = r["equity_curve"]
        (c / c.iloc[0] * 100).plot(ax=ax2, lw=1.0, label=sleeve)
    ax2.axhline(100, color="#999999", lw=0.8, ls="--")
    ax2.set_ylabel("Sleeve (=100)")
    ax2.legend(loc="upper left", ncol=4, fontsize=8)
    ax2.grid(alpha=0.25)

    path = PLOTS_DIR / f"{name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Plot saved: {path}")


def save_portfolio(summary: dict, name: str = "portfolio"):
    row = {k: summary[k] for k in engine._METRIC_KEYS if k in summary}
    pd.DataFrame([row]).to_csv(RESULTS_DIR / f"{name}_metrics.csv", index=False)
    summary["sleeves"].to_csv(RESULTS_DIR / f"{name}_sleeves.csv", index=False)
    summary["trades"].to_csv(RESULTS_DIR / f"{name}_trades.csv", index=False)
    print(f"Portfolio results saved: {RESULTS_DIR / (name + '_*.csv')}  "
          f"({summary['n_trades']} 笔, {len(summary['sleeves'])} 个子仓)")
