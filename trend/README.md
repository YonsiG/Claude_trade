# trend/

Market regime detection module. Classifies the last N bars of OHLCV data into a single trend type, independent of any buy/sell signal.

## Trend Types

| Value | Name | Description |
|-------|------|-------------|
| 1 | UPTREND | Price rising (linear regression slope > 0) |
| 2 | DOWNTREND | Price falling (linear regression slope < 0) |
| 3 | OSCILLATING | Price oscillating around mean with meaningful ATR |
| 4 | FLAT | Very tight range, low volatility |
| 5 | OTHER | No dominant trend detected |
| 6 | ABNORMAL | Any trend that reaches EXTREME intensity |

## Intensity

Each trend (except FLAT and OTHER) carries an intensity level:

| Value | Name | Uptrend/Downtrend | Oscillating |
|-------|------|-------------------|-------------|
| 1 | NORMAL | slope < 0.3%/bar | ATR/price < 3% |
| 2 | STRONG | slope 0.3%–0.8%/bar | ATR/price 3%–6% |
| 3 | EXTREME | slope > 0.8%/bar | ATR/price > 6% |

When intensity hits EXTREME, the trend is automatically promoted to ABNORMAL (type 6).
result.base_trend preserves the original trend type (e.g. UPTREND) for downstream use.

## Usage

```python
from trend import detect_trend, TrendType, TrendIntensity

result = detect_trend(bars, n=30)               # classify last 30 bars
result = detect_trend(bars, compute_duration=False)  # skip duration for speed

int(result.trend)        # 1–6
result.trend.name        # "UPTREND", "ABNORMAL", etc.
result.intensity.name    # "NORMAL", "STRONG", "EXTREME"
result.confidence        # 0.0–1.0, fit quality of the detector
result.is_abnormal       # True when intensity == EXTREME
result.base_trend        # original trend when ABNORMAL, else None
result.duration          # consecutive bars going backwards this trend holds; 0 = not computed
result.description       # human-readable detail (slope, ATR ratio, etc.)
```

## File Layout

| File | Role |
|------|------|
| base.py | TrendType, TrendIntensity, TrendResult dataclass, TrendDetector protocol |
| uptrend.py | Linear-regression slope detector (positive slope) |
| downtrend.py | Linear-regression slope detector (negative slope) |
| oscillating.py | Mean-crossing + ATR ratio detector |
| flat.py | Tight price-range detector |
| other.py | Fallback when no detector claims the trend |
| abnormal.py | wrap_as_abnormal() helper |
| detector.py | detect_trend() — runs the pipeline, returns best match |

## Extending

To add a new trend type (e.g. BREAKOUT = 7):

1. Add the value to TrendType in base.py.
2. Create trend/breakout.py with a detect_breakout(bars) -> TrendResult function.
3. Append (detect_breakout, {}) to _DEFAULT_PIPELINE in detector.py.

To pass a custom pipeline at call time:

```python
from trend.uptrend import detect_uptrend
result = detect_trend(bars, pipeline=[(detect_uptrend, {"slope_min": 0.001})])
```
