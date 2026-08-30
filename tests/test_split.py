"""训练集/验证集切分。"""
import pandas as pd
import pytest

from backtest.split import resolve_split, _MIN_BARS_PER_SPLIT
from conftest import make_ohlc


FLAT_BAR = [100, 101, 99, 100]


def _patch_load(monkeypatch, df):
    """resolve_split 内部调用 data.loader.load；用固定数据替身，不联网。"""
    def fake_load(ticker, start, end, interval="1d", source="yf"):
        return df.loc[start:end]
    monkeypatch.setattr("backtest.split.load", fake_load)


# ── 比例切分（默认路径）─────────────────────────────────────────────────────

def test_default_ratio_is_four_to_one_by_bar_count(monkeypatch):
    df = make_ohlc([FLAT_BAR] * 100)
    _patch_load(monkeypatch, df)
    r = resolve_split("T", str(df.index[0].date()), str(df.index[-1].date()), "yf", "1d")
    assert len(r.train_df) == 80
    assert len(r.test_df) == 20
    assert not r.explicit


def test_train_set_is_the_earlier_half(monkeypatch):
    """离当前更远（更早）的一段是训练集。"""
    df = make_ohlc([FLAT_BAR] * 20)
    _patch_load(monkeypatch, df)
    r = resolve_split("T", str(df.index[0].date()), str(df.index[-1].date()), "yf", "1d")
    assert r.train_df.index[-1] < r.test_df.index[0]


def test_custom_ratio_changes_the_split_point(monkeypatch):
    df = make_ohlc([FLAT_BAR] * 100)
    _patch_load(monkeypatch, df)
    r = resolve_split("T", str(df.index[0].date()), str(df.index[-1].date()),
                      "yf", "1d", ratio=0.5)
    assert len(r.train_df) == 50
    assert len(r.test_df) == 50


def test_ratio_out_of_range_raises(monkeypatch):
    df = make_ohlc([FLAT_BAR] * 20)
    _patch_load(monkeypatch, df)
    with pytest.raises(ValueError, match=r"\(0,1\)"):
        resolve_split("T", "a", "b", "yf", "1d", ratio=1.5)


def test_too_few_bars_raises(monkeypatch):
    df = make_ohlc([FLAT_BAR] * (_MIN_BARS_PER_SPLIT))  # 总数不够切两半
    _patch_load(monkeypatch, df)
    with pytest.raises(ValueError, match="不足以切"):
        resolve_split("T", str(df.index[0].date()), str(df.index[-1].date()), "yf", "1d")


def test_split_is_a_partition_no_gap_no_overlap(monkeypatch):
    df = make_ohlc([FLAT_BAR] * 37)   # 不是整除的长度
    _patch_load(monkeypatch, df)
    r = resolve_split("T", str(df.index[0].date()), str(df.index[-1].date()), "yf", "1d")
    assert len(r.train_df) + len(r.test_df) == len(df)
    assert pd.concat([r.train_df, r.test_df]).equals(df)


# ── 显式区间（另一种路径）───────────────────────────────────────────────────

def test_explicit_range_mode(monkeypatch):
    df = make_ohlc([FLAT_BAR] * 100)
    _patch_load(monkeypatch, df)
    r = resolve_split("T", None, None, "yf", "1d",
                      train_start="2024-01-01", train_end="2024-02-01",
                      test_start="2024-02-02", test_end="2024-03-01")
    assert r.explicit
    assert r.train_range == ("2024-01-01", "2024-02-01")
    assert r.test_range == ("2024-02-02", "2024-03-01")


def test_explicit_mode_requires_all_four_dates(monkeypatch):
    df = make_ohlc([FLAT_BAR] * 20)
    _patch_load(monkeypatch, df)
    with pytest.raises(ValueError, match="缺少"):
        resolve_split("T", None, None, "yf", "1d", train_start="2024-01-01")
