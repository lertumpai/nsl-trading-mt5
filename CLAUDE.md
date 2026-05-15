# NSL Trading MT5 — Master Agent Instructions

> **ALL AI AGENTS MUST READ THIS FILE BEFORE STARTING ANY WORK IN THIS REPOSITORY.**

---

## Project Overview

This repository is the full R&D workspace for building, testing, and deploying **Expert Advisors (EAs) for XAUUSD (Gold/USD)** on MetaTrader 5. The goal is a rigorous, data-driven research loop that produces profitable, robust, and risk-controlled algorithmic trading strategies.

---

## Repository Structure

```
nsl-trading-mt5/
├── CLAUDE.md                    ← YOU ARE HERE — read before everything
├── .agent/
│   ├── SKILLS.md                ← Index of all agent skills
│   └── skills/                  ← Individual skill files
├── docs/
│   ├── RND_LIFECYCLE.md         ← Full R&D phase-by-phase flow
│   └── DATASET_REQUIREMENTS.md ← Exact data specs to request/use
├── data/
│   ├── raw/                     ← Original price data (never modify)
│   └── processed/               ← Feature-engineered, cleaned data
├── research/
│   ├── hypothesis/              ← Written hypothesis docs before coding
│   └── experiments/             ← Experiment logs and results
├── src/                         ← Shared TypeScript types and utilities
├── strategies/                  ← Strategy logic in TypeScript (.ts)
├── backtests/                   ← Backtest result files and reports
└── ea/                          ← Final MQL5 EA source code
```

---

## Non-Negotiable Rules for All Agents

### Research Rules
1. **Hypothesis first, code second.** Every strategy must have a written hypothesis in `research/hypothesis/` before any code is written.
2. **No data leakage.** Test sets must never be seen during design or parameter search.
3. **Walk-forward is mandatory.** No strategy ships without walk-forward validation.
4. **Document every experiment.** Results go to `research/experiments/` with timestamp, parameters, and metrics.

### Data Rules
5. **Raw data is immutable.** Never modify files under `data/raw/`. Transformations go to `data/processed/`.
6. **Always state which dataset** (timeframe, date range, source) you used for any analysis.
7. **Preferred timeframes for XAUUSD:** M1, M5, M15, H1, H4, D1. See `docs/DATASET_REQUIREMENTS.md`.

### Code Rules
8. **MQL5 code lives in `ea/` only.** Research/backtest logic uses **TypeScript (Node.js / Bun)**.
9. **All EA parameters must be externally configurable** (`input` variables in MQL5).
10. **Every EA must implement** the mandatory risk controls defined in `.agent/skills/risk-management.md`.

### Performance Rules
11. **Minimum acceptance bar** before any strategy moves to forward test:
    - Sharpe Ratio ≥ 1.5 (annualized, out-of-sample)
    - Max Drawdown ≤ 15%
    - Profit Factor ≥ 1.4
    - Win Rate × RR ≥ 0.3 (expectancy positive)
    - Minimum 200 trades in backtest
12. **No curve-fitting.** If in-sample Sharpe > out-of-sample Sharpe × 1.5, the strategy is rejected.

---

## Agent Skill Index

All skills are in `.agent/skills/`. Read the relevant skill before starting a task.

| Task | Skill File |
|------|-----------|
| Load and validate price data | `.agent/skills/data-analysis.md` |
| Design a new trading strategy | `.agent/skills/strategy-design.md` |
| Run and interpret a backtest | `.agent/skills/backtesting.md` |
| Optimize parameters safely | `.agent/skills/optimization.md` |
| Calculate risk and position size | `.agent/skills/risk-management.md` |
| Write MQL5 EA code | `.agent/skills/mql5-coding.md` |
| Evaluate EA performance | `.agent/skills/performance-metrics.md` |

---

## XAUUSD Context (Critical Background)

- **Instrument:** XAUUSD — Spot Gold vs US Dollar
- **Volatility:** High. Average daily range 15–30 USD. Can spike 50+ USD on news.
- **Key drivers:** DXY (inverse), US10Y yields (inverse), inflation data, geopolitical risk, Fed policy
- **Active sessions:** London (08:00–16:00 UTC), New York (13:00–21:00 UTC). Overlap 13:00–16:00 UTC is highest volume.
- **Dead zones:** Asian session (22:00–07:00 UTC) — low volume, choppy, wider spreads
- **Spread:** Typically 0.2–0.5 USD on ECN; widens to 2–5 USD on high-impact news
- **Contract:** 1 lot = 100 oz. 1 pip = $1 per 0.01 lot. Margin ~$1,000/lot at 1:100

---

## Current Phase

> Update this section each time the project advances a phase.

**Active Phase:** Phase 0 — Framework Setup  
**Next Action:** Provide dataset (see `docs/DATASET_REQUIREMENTS.md`), then begin Phase 1 EDA.
