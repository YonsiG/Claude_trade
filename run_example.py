"""
主入口：指定数据、策略、时间范围，执行回测并输出结果。

单标的：
  python run_example.py
  python run_example.py --ticker TSLA --start 2021-01-01 --end 2023-12-31
  python run_example.py --strategy engulfing --capital 50000

期货（自动查合约乘数与保证金率）：
  python run_example.py --ticker RB0 --source ak --futures

平台突破（价格位止损 + 按验证次数分级的风险仓位，详见 strategies/platform_breakout.py）：
  python run_example.py --strategy platform_breakout --ticker AAPL --start 2018-01-01

关键区突破（platform_breakout 的对照组：正向状态机 + 独立验证 + 次日开盘执行
带可接受区间，详见 strategies/key_zone_breakout.py）：
  python run_example.py --strategy key_zone_breakout --ticker AAPL --start 2018-01-01
  python run_example.py --strategy key_zone_breakout --kz-variant baseline --kz-touches 2 \\
                        --ticker RB0 --source ak --futures

组合（资金等权切分到多个标的，各自独立执行）：
  python run_example.py --tickers AAPL,NVDA,TSLA --capital 300000
  python run_example.py --tickers RB0,M0,AU0 --source ak --futures --capital 600000

训练集/验证集切分（先在训练集上调参，只在验证集上跑一次做最终检验）：
  python run_example.py --split                                    # 4:1，较早一段为训练集
  python run_example.py --split --split-ratio 0.7
  python run_example.py --tickers RB0,M0 --source ak --futures --split
  python run_example.py --train-start 2021-01-01 --train-end 2023-12-31 \\
                        --test-start  2024-01-01 --test-end  2026-07-31
"""
import argparse

from data.loader import load
from signals.pattern import engulfing
from signals.pattern import umbrella
from strategies.single_signal import SingleSignalStrategy
from strategies.platform_breakout import PlatformBreakoutStrategy
from strategies.key_zone_breakout import KeyZoneBreakoutStrategy
from backtest import engine
from backtest import portfolio as pf
from backtest import split as sp


# ── 策略注册表 ──────────────────────────────────────────────────────────────
# SingleSignalStrategy 系：signal_fn(df)->Signal，止损止盈都是固定比例
_SIGNALS = {
    "single_signal": umbrella,
    "engulfing": engulfing,
    # "breakout":  my_breakout_signal,
}
# 参数形状和 SingleSignalStrategy 不一样（价格位止损、按风险定仓位）的策略，
# 在 build_strategy() 里单独分支，不塞进 _SIGNALS。
_OTHER_STRATEGIES = ["platform_breakout", "key_zone_breakout"]

# 关键区突破的三个止盈/追踪对照版本，见 strategies/key_zone_breakout.py 类
# docstring 里的对照表：trail_start_r（追踪止盈激活门槛，以初始风险倍数计）
# 和 fixed_take_profit（固定止盈上限，None 表示不设上限）。
_KZ_VARIANTS = {
    "original": {"trail_start_r": 0.0, "fixed_take_profit": 0.30},   # 原始版
    "baseline": {"trail_start_r": 2.0, "fixed_take_profit": 0.30},   # 建议基准版
    "trend":    {"trail_start_r": 2.0, "fixed_take_profit": None},   # 趋势对照版
}


def build_strategy(name, df, capital, args, ticker):
    """按名称构造策略。新增 Signal 型策略在 _SIGNALS 里注册；参数形状不同的
    （比如按风险定仓位、价格位止损）在这里加一个分支。"""
    if name == "platform_breakout":
        return PlatformBreakoutStrategy(
            df,
            execution=args.execution,
            futures=args.futures,
            ticker=ticker if args.futures else None,
            fee_rate=args.fee,
            initial_capital=capital,
        )
    if name == "key_zone_breakout":
        from functools import partial
        from signals.key_zone import key_zone_breakout
        variant = _KZ_VARIANTS[args.kz_variant]
        return KeyZoneBreakoutStrategy(
            df,
            signal_fn=partial(key_zone_breakout, required_touches=args.kz_touches),
            futures=args.futures,
            ticker=ticker if args.futures else None,
            fee_rate=args.fee,
            initial_capital=capital,
            **variant,
        )
    if name not in _SIGNALS:
        raise ValueError(f"未知策略: {name!r}，可选: {list(_SIGNALS) + _OTHER_STRATEGIES}")
    return SingleSignalStrategy(
        df,
        signal_fn=_SIGNALS[name],
        trail_pct=args.trail,
        sl_pct=args.stop,
        execution=args.execution,
        futures=args.futures,
        ticker=ticker if args.futures else None,
        fee_rate=args.fee,
        initial_capital=capital,
    )
# ────────────────────────────────────────────────────────────────────────────


def parse_args():
    p = argparse.ArgumentParser(description="回测主入口")
    p.add_argument("--ticker",   default="AAPL",          help="标的代码 (默认: AAPL)")
    p.add_argument("--tickers",  default=None,
                   help="逗号分隔的多个标的，给了就跑组合回测")
    p.add_argument("--start",    default="2020-01-01",    help="起始日期 YYYY-MM-DD")
    p.add_argument("--end",      default="2026-06-19",    help="结束日期 YYYY-MM-DD")
    p.add_argument("--interval", default="1d",            help="K线周期 (默认: 1d)")
    p.add_argument("--source",   default="yf",            choices=["yf", "ak"],
                   help="数据源 (默认: yf)")
    p.add_argument("--strategy", default="single_signal",
                   help=f"策略名称，可选: {list(_SIGNALS) + _OTHER_STRATEGIES}")
    p.add_argument("--capital",  default=100_000,         type=float,
                   help="初始资金 (组合模式下为总资金)")
    p.add_argument("--futures",  action="store_true",
                   help="按期货回测：自动查合约乘数与保证金率")
    p.add_argument("--trail",    default=0.30, type=float, help="移动止盈比例")
    p.add_argument("--stop",     default=0.10, type=float, help="固定止损比例")
    p.add_argument("--fee",      default=0.0,  type=float, help="手续费率")
    p.add_argument("--execution", default="close", choices=["close", "next_open"],
                   help="信号成交时点 (默认: close，key_zone_breakout 固定次日开盘不受此影响)")
    p.add_argument("--kz-variant", default="baseline",
                   choices=list(_KZ_VARIANTS),
                   help="key_zone_breakout 的止盈/追踪版本 (默认: baseline，见类 docstring)")
    p.add_argument("--kz-touches", default=3, type=int,
                   help="key_zone_breakout 的最低验证次数门槛 (默认: 3)")
    p.add_argument("--rf",       default=0.0,  type=float,
                   help="年化无风险利率，用于夏普 (0.04 = 4%%)")

    # ── 训练集/验证集切分 ──
    p.add_argument("--split", action="store_true",
                   help="按训练/验证集切分回测（在 --start/--end 内按 --split-ratio 自动切）")
    p.add_argument("--split-ratio", default=0.8, type=float,
                   help="训练集占比，按 bar 数（默认 0.8 = 4:1，较早一段为训练集）")
    p.add_argument("--train-start", default=None, help="训练集起始日期（需与另外三个一起给出）")
    p.add_argument("--train-end",   default=None, help="训练集结束日期")
    p.add_argument("--test-start",  default=None, help="验证集起始日期")
    p.add_argument("--test-end",    default=None, help="验证集结束日期")
    return p.parse_args()


def tickers_of(args):
    if args.tickers:
        return [t.strip() for t in args.tickers.split(",") if t.strip()]
    return [args.ticker]


# ── 单次回测 + 输出（单标的 / 组合复用）────────────────────────────────────

def _report_single(ticker, start, end, strategy, args, name_suffix="", label=None):
    summary = engine.run(ticker, start, end, strategy, rf=args.rf)
    header = f"{ticker}  {start} → {end}   初始资金 ${strategy.initial_capital:,.0f}"
    if label:
        header = f"[{label}] " + header
    print(f"\n{header}\n")
    print(engine.report(summary))
    print()
    name = f"{args.strategy}{name_suffix}"
    engine.plot(summary, name)
    engine.save_results(summary, name)
    return summary


def _report_portfolio(tickers, start, end, strategies, args, name_suffix="", label=None):
    # capital=args.capital 让 run_portfolio 按等权重新切分，构造策略时传的
    # per-sleeve 资金只是占位值，这里统一以总资金为准，训练/验证两组用同一总资金。
    summary = pf.run_portfolio(strategies, capital=args.capital, rf=args.rf, start=start, end=end)
    header = (f"组合 [{', '.join(tickers)}]  {start} → {end}   "
              f"总资金 ${summary['initial_capital']:,.0f}（等权切分）")
    if label:
        header = f"[{label}] " + header
    print(f"\n{header}\n")
    print(pf.report_portfolio(summary))
    print()
    name = f"portfolio_{args.strategy}{name_suffix}_{'-'.join(tickers)}"
    pf.plot_portfolio(summary, name)
    pf.save_portfolio(summary, name)
    return summary


# ── 三种运行模式 ────────────────────────────────────────────────────────────

def run_single(args):
    df = load(args.ticker, args.start, args.end,
              interval=args.interval, source=args.source)
    strategy = build_strategy(args.strategy, df, args.capital, args, args.ticker)
    _report_single(args.ticker, args.start, args.end, strategy, args)


def run_portfolio(args):
    tickers = tickers_of(args)
    strategies = {}
    for t in tickers:
        df = load(t, args.start, args.end, interval=args.interval, source=args.source)
        strategies[t] = build_strategy(args.strategy, df, args.capital / len(tickers), args, t)
    _report_portfolio(tickers, args.start, args.end, strategies, args)


def run_split(args):
    """
    训练/验证切分：同一策略、同一参数，分别在训练集和验证集上各跑一次独立回测
    （不是训练集打底接着跑验证集——两段各自从 initial_capital 起算），
    输出与保存都按两组分开，文件名分别带 _train / _test 后缀。

    调参应该只在训练集的结果上做；验证集只用来看训练集上选定的参数是否
    在没见过的数据上失效，理想情况下只跑一次，跑多次再回头调参就等于
    把验证集也变成了训练集的一部分。
    """
    tickers = tickers_of(args)

    splits = {}
    for t in tickers:
        splits[t] = sp.resolve_split(
            t, args.start, args.end, args.source, args.interval,
            ratio=args.split_ratio,
            train_start=args.train_start, train_end=args.train_end,
            test_start=args.test_start, test_end=args.test_end,
        )

    first = next(iter(splits.values()))
    mode = "显式区间" if first.explicit else \
        f"按 bar 数自动切分 {args.split_ratio:.0%} / {1 - args.split_ratio:.0%}"
    print(f"\n训练集/验证集切分（{mode}）")
    for t, r in splits.items():
        print(f"  {t:<8} 训练集 {r.train_range[0]} ~ {r.train_range[1]} "
              f"({len(r.train_df):>4} 根)   |   "
              f"验证集 {r.test_range[0]} ~ {r.test_range[1]} ({len(r.test_df):>4} 根)")

    n = len(tickers)
    train_strategies = {t: build_strategy(args.strategy, r.train_df, args.capital / n, args, t)
                        for t, r in splits.items()}
    test_strategies = {t: build_strategy(args.strategy, r.test_df, args.capital / n, args, t)
                       for t, r in splits.items()}

    if n == 1:
        t = tickers[0]
        _report_single(t, *splits[t].train_range, train_strategies[t], args,
                       name_suffix="_train", label="训练集/优化集")
        _report_single(t, *splits[t].test_range, test_strategies[t], args,
                       name_suffix="_test", label="验证集/测试集")
    else:
        _report_portfolio(tickers, first.train_range[0], first.train_range[1],
                          train_strategies, args, name_suffix="_train", label="训练集/优化集")
        _report_portfolio(tickers, first.test_range[0], first.test_range[1],
                          test_strategies, args, name_suffix="_test", label="验证集/测试集")


def main():
    args = parse_args()
    explicit_split = any([args.train_start, args.train_end, args.test_start, args.test_end])
    if args.split or explicit_split:
        run_split(args)
    elif args.tickers:
        run_portfolio(args)
    else:
        run_single(args)


if __name__ == "__main__":
    main()
