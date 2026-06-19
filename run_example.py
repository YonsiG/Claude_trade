"""
主入口：指定数据、策略、时间范围，执行回测并输出结果。

用法示例：
  python run_example.py
  python run_example.py --ticker TSLA --start 2021-01-01 --end 2023-12-31
  python run_example.py --strategy single_signal --capital 50000
"""
import argparse

from data.loader import load
from signals.pattern import engulfing
from signals.pattern import umbrella
from strategies.single_signal import SingleSignalStrategy
from backtest import engine


# ── 策略注册表 ──────────────────────────────────────────────────────────────
def _build_registry(df, capital):
    return {
        "single_signal": SingleSignalStrategy(
            df,
            signal_fn=engulfing,
            trail_pct=0.30,
            sl_pct=0.10,
            initial_capital=capital,
        ),
        # "breakout":    BreakoutStrategy(df, initial_capital=capital, ...),
        # "mean_revert": MeanRevertStrategy(df, initial_capital=capital, ...),
    }
# ────────────────────────────────────────────────────────────────────────────


def parse_args():
    p = argparse.ArgumentParser(description="回测主入口")
    p.add_argument("--ticker",   default="AAPL",          help="标的代码 (默认: AAPL)")
    p.add_argument("--start",    default="2020-01-01",    help="起始日期 YYYY-MM-DD")
    p.add_argument("--end",      default="2026-06-19",    help="结束日期 YYYY-MM-DD")
    p.add_argument("--interval", default="1d",            help="K线周期 (默认: 1d)")
    p.add_argument("--source",   default="yf",            choices=["yf", "ak"],
                   help="数据源 (默认: yf)")
    p.add_argument("--strategy", default="single_signal",
                   help="策略名称，需在注册表中 (默认: single_signal)")
    p.add_argument("--capital",  default=100_000,         type=float,
                   help="初始资金 (默认: 100000)")
    return p.parse_args()


def main():
    args = parse_args()

    # 1. 读取数据
    df = load(args.ticker, args.start, args.end,
              interval=args.interval, source=args.source)

    # 2. 选择策略
    registry = _build_registry(df, args.capital)
    if args.strategy not in registry:
        raise ValueError(f"未知策略: {args.strategy!r}，可选: {list(registry)}")
    strategy = registry[args.strategy]

    # 3. 回测
    summary = engine.run(args.ticker, args.start, args.end, strategy)

    # 4. 输出结果（保存到 backtest/results/ 和 backtest/plots/）
    print(f"Initial Capital: ${args.capital:,.0f}")
    print(f"Total Return   : {summary['total_return']:.2f}%")
    print(f"Sharpe Ratio   : {summary['sharpe']:.2f}")
    print(f"Max Drawdown   : {summary['max_drawdown']:.2f}%")
    engine.plot(summary, args.strategy)
    engine.save_results(summary, args.strategy)


if __name__ == "__main__":
    main()
