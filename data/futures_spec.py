"""期货合约规格表：合约乘数 + 保证金率。

**每个品种一手对应的乘数完全不同**——黄金 AU 是 1000 克/手、白银 AG 是 15 千克/手、
螺纹钢 RB 是 10 吨/手、生猪 LH 是 16 吨/手、铜 CU 是 5 吨/手、10 年期国债 T 是
10000 元/点。用一个统一的 `multiplier=1.0` 去回测期货，每个品种都是错的，
而且错得没有规律（AU 的仓位会被低估 1000 倍，CU 只低估 5 倍）。

保证金同理：按全额资金开仓等于把期货当现货，杠杆和爆仓风险被完全抹掉。

数据来源（两个独立来源交叉校验）：
  1. `ak.futures_rule(date)`      交易所公布的规则表——交易保证金比例、合约乘数、
                                  最小变动价位。这是交易所**下限**。
  2. `ak.futures_fees_info()`     券商实际执行的逐合约保证金率。券商会在交易所
                                  基础上加收，所以通常更高，但并非总是。

保守取值：`margin_rate = max(交易所比例, 券商比例)`。两个来源互有高低，
取 max 才安全——实测 RB 交易所 11% / 券商 20%，AG 交易所 30% / 券商 22%。

`margin_peak` 另存交易所「特殊合约参数调整」里的最高值（临近交割月会大幅提高，
铜可到 27%）。主力连续永远不持有临交割合约，所以**不计入** `margin_rate`，
仅作参考；需要极端保守可以自行改用这一列。

用法：
    from data.futures_spec import spec, load_spec

    s = spec("RB0")          # -> FuturesSpec(code='RB', multiplier=10, margin_rate=0.20, ...)
    df = load_spec()         # 完整表格

刷新离线表（需要联网）：
    python data/futures_spec.py --refresh
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import NamedTuple, Optional

import pandas as pd

SPEC_PATH = Path(__file__).parent / "futures_spec.csv"

_COLUMNS = ["code", "name", "exchange", "multiplier", "margin_rate",
            "margin_exchange", "margin_broker", "margin_peak", "tick_size", "asof"]

# 交易所「特殊合约参数调整」文本里的保证金比例，形如 "CU2608合约交易保证金比例为27.0%"
_PEAK_RE = re.compile(r"保证金比例为\s*(\d+(?:\.\d+)?)\s*%")

_EXCHANGE_EN = {
    "上期所": "SHFE", "能源中心": "INE", "大商所": "DCE",
    "郑商所": "CZCE", "中金所": "CFFEX", "广期所": "GFEX",
}


class FuturesSpec(NamedTuple):
    code: str
    name: str
    exchange: str
    multiplier: float     # 一手对应的合约单位数（吨/克/千克/点…）
    margin_rate: float    # 保守保证金率，[0, 1]
    tick_size: float
    margin_exchange: float
    margin_broker: float
    margin_peak: float

    @property
    def notional(self):
        """给定价格算一手名义价值的便捷函数。"""
        return lambda price: price * self.multiplier

    def margin_per_lot(self, price: float) -> float:
        return price * self.multiplier * self.margin_rate


# ---------------------------------------------------------------------------
# 代码归一化
# ---------------------------------------------------------------------------

def normalize_code(ticker: str) -> str:
    """
    把各种写法的合约代码归一到品种代码。

        "RB0" / "rb2405" / "RB" -> "RB"
        "M0"  / "m2509"         -> "M"
        "AU0" / "au2412"        -> "AU"

    注意 yfinance 的美盘期货（"GC=F"）不在这张表里，会归一成 "GC=F" 并查不到。
    """
    t = str(ticker).strip().upper()
    m = re.match(r"^([A-Z]+)\d*$", t)
    return m.group(1) if m else t


# ---------------------------------------------------------------------------
# 构建 / 读取
# ---------------------------------------------------------------------------

def _fetch_rule(ak, date: Optional[str], lookback: int, verbose: bool):
    """规则表只在交易日发布，周末/节假日无数据——往前回溯找最近的交易日。"""
    start = pd.Timestamp.today() if date is None else pd.Timestamp(date)
    last_err = None
    for i in range(lookback + 1):
        d = (start - pd.Timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = ak.futures_rule(date=d)
            if df is not None and not df.empty:
                if i and verbose:
                    print(f"  {start.strftime('%Y%m%d')} 无规则表（非交易日），回溯到 {d}")
                return df, d
        except Exception as exc:                              # noqa: BLE001
            last_err = exc
    raise RuntimeError(
        f"回溯 {lookback} 天仍未取到交易所规则表（起点 {start.date()}）。"
        f"最后一次错误: {type(last_err).__name__}: {last_err}"
    )


def build_spec(date: Optional[str] = None, verbose: bool = True,
               lookback: int = 10) -> pd.DataFrame:
    """联网拉取并合并两个来源，返回规格表。需要 akshare。"""
    import akshare as ak

    rule, date = _fetch_rule(ak, date, lookback, verbose)
    rule = rule.rename(columns={
        "交易所": "exchange_cn", "品种": "name", "代码": "code",
        "交易保证金比例": "margin_exchange_pct", "合约乘数": "multiplier",
        "最小变动价位": "tick_size", "特殊合约参数调整": "special",
    })
    rule["code"] = rule["code"].astype(str).str.strip().str.upper()
    rule = rule[~rule["code"].str.contains("_")]          # 剔除期权行（CU_O 等）
    rule = rule.dropna(subset=["multiplier"])

    rule["margin_exchange"] = pd.to_numeric(
        rule["margin_exchange_pct"], errors="coerce") / 100.0
    rule["margin_peak"] = rule["special"].fillna("").map(
        lambda s: max([float(x) for x in _PEAK_RE.findall(s)], default=float("nan")) / 100.0)
    rule["exchange"] = rule["exchange_cn"].map(_EXCHANGE_EN).fillna(rule["exchange_cn"])

    # 券商实际保证金：逐合约，取该品种最高的一档
    try:
        fees = ak.futures_fees_info()
        fees["code"] = fees["品种代码"].astype(str).str.strip().str.upper()
        broker = fees.groupby("code").agg(
            margin_broker=("做多保证金率", "max"),
            margin_broker_short=("做空保证金率", "max"),
            multiplier_broker=("合约乘数", "max"),
        )
        broker["margin_broker"] = broker[["margin_broker", "margin_broker_short"]].max(axis=1)
        broker = broker.drop(columns=["margin_broker_short"])
    except Exception as exc:                                  # noqa: BLE001
        if verbose:
            print(f"  警告: 券商保证金表拉取失败（{type(exc).__name__}），"
                  f"仅使用交易所规则表，保守性下降。")
        broker = pd.DataFrame(columns=["margin_broker", "multiplier_broker"])

    df = rule.merge(broker, left_on="code", right_index=True, how="left")

    # 乘数交叉校验：两个来源应当一致，不一致时取大者并告警
    if "multiplier_broker" in df.columns and verbose:
        mism = df.dropna(subset=["multiplier_broker"])
        mism = mism[mism["multiplier"] != mism["multiplier_broker"]]
        for _, r in mism.iterrows():
            print(f"  警告: {r['code']} 乘数不一致 "
                  f"(规则表 {r['multiplier']} vs 券商表 {r['multiplier_broker']})，取大者")
    if "multiplier_broker" in df.columns:
        df["multiplier"] = df[["multiplier", "multiplier_broker"]].max(axis=1)

    # 保守取值：两个来源取 max
    df["margin_rate"] = df[["margin_exchange", "margin_broker"]].max(axis=1)
    df["margin_rate"] = df["margin_rate"].fillna(df["margin_exchange"])
    df["asof"] = date

    out = (df[_COLUMNS].dropna(subset=["multiplier", "margin_rate"])
           .drop_duplicates("code").sort_values("code").reset_index(drop=True))
    return out


def save_spec(df: pd.DataFrame, path: Path = SPEC_PATH) -> Path:
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def load_spec(path: Path = SPEC_PATH) -> pd.DataFrame:
    """读取离线规格表，不联网。"""
    if not path.exists():
        raise FileNotFoundError(
            f"未找到期货规格表 {path}。运行以下命令生成（需要联网）：\n"
            f"    python data/futures_spec.py --refresh"
        )
    return pd.read_csv(path, encoding="utf-8-sig")


_CACHE: Optional[dict] = None


def spec(ticker: str, path: Path = SPEC_PATH) -> FuturesSpec:
    """
    查一个品种的规格。ticker 可以是 "RB0" / "rb2405" / "RB"。

    Raises:
        KeyError: 表里没有该品种（例如 yfinance 的美盘期货 "GC=F"），
                  此时必须在策略里手工传 multiplier 和 margin_rate。
    """
    global _CACHE
    if _CACHE is None:
        df = load_spec(path)
        _CACHE = {r["code"]: r for r in df.to_dict("records")}

    code = normalize_code(ticker)
    if code not in _CACHE:
        raise KeyError(
            f"期货规格表中没有品种 {code!r}（来自 ticker {ticker!r}）。"
            f"美盘期货（GC=F 等）不在这张表里，请在策略中显式传入 "
            f"multiplier 和 margin_rate。"
        )
    r = _CACHE[code]
    peak = r.get("margin_peak")
    return FuturesSpec(
        code=r["code"], name=r["name"], exchange=r["exchange"],
        multiplier=float(r["multiplier"]), margin_rate=float(r["margin_rate"]),
        tick_size=float(r["tick_size"]),
        margin_exchange=float(r["margin_exchange"]),
        margin_broker=float(r["margin_broker"]) if pd.notna(r.get("margin_broker")) else float("nan"),
        margin_peak=float(peak) if pd.notna(peak) else float("nan"),
    )


def clear_cache() -> None:
    global _CACHE
    _CACHE = None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="生成/查看期货合约规格离线表")
    p.add_argument("--refresh", action="store_true", help="联网重新拉取并覆盖离线表")
    p.add_argument("--date", default=None, help="规则表日期 YYYYMMDD（默认今天）")
    p.add_argument("--show", nargs="*", metavar="TICKER",
                   help="查看指定品种，不给参数则打印全表")
    args = p.parse_args()

    if args.refresh:
        df = build_spec(args.date)
        path = save_spec(df)
        clear_cache()
        print(f"已写入 {path}  ({len(df)} 个品种, asof={df['asof'].iloc[0]})")
    else:
        df = load_spec()

    if args.show is not None:
        pd.set_option("display.width", 200)
        if args.show:
            rows = [spec(t)._asdict() for t in args.show]
            print(pd.DataFrame(rows).to_string(index=False))
        else:
            print(df.to_string(index=False))


if __name__ == "__main__":
    main()
