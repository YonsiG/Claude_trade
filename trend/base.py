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
        return self.intensity == TrendIntensity.EXTREME


class TrendDetector(Protocol):
    def detect(self, bars: pd.DataFrame) -> TrendResult: ...
