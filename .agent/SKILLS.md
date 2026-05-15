# Agent Skills Index

> All AI agents working in this repository MUST read `CLAUDE.md` first, then load the relevant skill(s) for their task from this index.

---

## Available Skills

| Skill | File | When to Use |
|---|---|---|
| Data Analysis | `skills/data-analysis.md` | Loading, validating, cleaning, exploring price data |
| Strategy Design | `skills/strategy-design.md` | Creating a new trading strategy from a hypothesis |
| Backtesting | `skills/backtesting.md` | Running and interpreting strategy backtests |
| Optimization | `skills/optimization.md` | Searching for parameters without overfitting |
| Risk Management | `skills/risk-management.md` | Position sizing, stops, circuit breakers |
| MQL5 Coding | `skills/mql5-coding.md` | Writing or reviewing MQL5 EA code |
| Performance Metrics | `skills/performance-metrics.md` | Evaluating and reporting EA performance |

---

## Skill Dependencies

Some skills depend on others. Load parent skills first:

```
Data Analysis
    ↓
Strategy Design
    ↓
Backtesting ←── Performance Metrics
    ↓
Optimization
    ↓
Risk Management
    ↓
MQL5 Coding
```

---

## Quick Task → Skill Map

| If you are asked to... | Load these skills |
|---|---|
| Analyze a new dataset | data-analysis |
| Design a strategy | data-analysis + strategy-design |
| Run a backtest | backtesting + performance-metrics |
| Optimize parameters | backtesting + optimization |
| Size a position | risk-management |
| Code the final EA | mql5-coding + risk-management |
| Do a full R&D cycle | ALL skills + `docs/RND_LIFECYCLE.md` |

---

## Skill Versioning

When a skill is updated, add a changelog comment at the top of the skill file:
```
<!-- v1.1 2026-05-20: Added FVG entry pattern -->
```
