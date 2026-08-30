from enum import IntEnum
from dataclasses import dataclass
import pandas as pd
from typing import Protocol, Optional


class TrendType(IntEnum):
    UPTREND     = 1
    DOWNTREND   = 2
    OSCILLATING = 3
    FLAT        = 4
    OTHER       = 5
    ABNORMAL    = 6


class TrendIntensity(IntEnum):
    NORMAL  = 1
    STRONG  = 2
    EXTREME = 3


@dataclass
class TrendResult:
    trend:       TrendType
    confidence:  float
    description: str
    intensity:   TrendIntensity = TrendIntensity.NORMAL
    base_trend:  Optional[TrendType] = None
    duration:    int = 0  # consecutive bars covered; 0 = not computed

    @property
    def is_abnormal(self) -> bool:
        """窗口内存在不可交易的价格断裂（换月/涨跌停/坏数据），读数不可信。"""
        return self.trend == TrendType.ABNORMAL

    @property
    def is_extreme(self) -> bool:
        """趋势非常陡峭。这是**真实行情**，不是数据异常——别和 is_abnormal 混用。"""
        return self.intensity == TrendIntensity.EXTREME

    @property
    def direction(self) -> TrendType:
        """方向读数：ABNORMAL 时回落到断裂前的基础趋势（若有）。"""
        if self.trend == TrendType.ABNORMAL and self.base_trend is not None:
            return self.base_trend
        return self.trend


class TrendDetector(Protocol):
    def detect(self, bars: pd.DataFrame) -> TrendResult: ...
