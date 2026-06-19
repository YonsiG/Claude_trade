import pandas as pd
import numpy as np
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
PLOTS_DIR = Path(__file__).parent / "plots"
RESULTS_DIR.mkdir(exist_ok=True)
PLOTS_DIR.mkdir(exist_ok=True)


def run(ticker: str, start: str, end: str, strategy) -> dict:
    """
    Execute a strategy and return performance metrics.
    strategy must be an instantiated BaseStrategy with a run() method.
    """
    equity = strategy.run()
    returns = equity.pct_change().dropna()

    summary = {
        "ticker": ticker,
        "total_return": (equity.iloc[-1] / equity.iloc[0] - 1) * 100,
        "sharpe": returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0,
        "max_drawdown": _max_drawdown(equity),
        "equity_curve": equity,
    }
    return summary


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return ((equity - peak) / peak).min() * 100


def plot(summary: dict, strategy_name: str):
    import matplotlib.pyplot as plt

    ticker = summary["ticker"]
    fig, ax = plt.subplots(figsize=(12, 5))
    summary["equity_curve"].plot(ax=ax)
    ax.set_title(f"{strategy_name} — {ticker}  |  "
                 f"Return: {summary['total_return']:.1f}%  "
                 f"Sharpe: {summary['sharpe']:.2f}  "
                 f"MDD: {summary['max_drawdown']:.1f}%")
    ax.set_ylabel("Portfolio Value ($)")
    path = PLOTS_DIR / f"{ticker}_{strategy_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Plot saved: {path}")


def save_results(summary: dict, strategy_name: str):
    row = {k: v for k, v in summary.items() if k != "equity_curve"}
    pd.DataFrame([row]).to_csv(
        RESULTS_DIR / f"{summary['ticker']}_{strategy_name}.csv", index=False
    )
