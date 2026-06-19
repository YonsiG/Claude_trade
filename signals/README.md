# Signals Module

## Status

`price_signals.py` 中的三个函数（`is_increase`, `is_decrease`, `is_consolidation`）待删除——它们描述的是 trend，不是 signal。

## Roadmap

### Pattern-based（K线形态）— 进行中
- [ ] 单根K线形态：锤子线、射击之星、十字星、大阳/阴线
- [ ] 两根K线形态：吞噬形态（Engulfing）、孕线（Harami）、穿刺/乌云盖顶
- [ ] 三根K线形态：早晨/黄昏之星、三白兵/三黑鸦

### 技术指标类 — 待做
- [ ] MA crossover
- [ ] RSI 超买超卖
- [ ] MACD
- [ ] 布林带突破
- [ ] Volume spike

### 统计类 — 待做
- [ ] Z-score 偏离
- [ ] 均值回归
