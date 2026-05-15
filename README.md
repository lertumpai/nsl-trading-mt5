# NSL Trading MT5 — XAUUSD EA R&D Framework

A structured, AI-assisted research framework for building Expert Advisors (EAs) for **XAUUSD (Gold/USD)** on MetaTrader 5.

Research and strategy code is written in **TypeScript**. The final EA is deployed in **MQL5**.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [Project Structure](#3-project-structure)
4. [Custom Commands](#4-custom-commands)
5. [R&D Workflow — Step by Step](#5-rd-workflow--step-by-step)
   - [Phase 0: Setup](#phase-0-setup)
   - [Phase 1: Export & Validate Data](#phase-1-export--validate-data)
   - [Phase 2: Exploratory Data Analysis](#phase-2-exploratory-data-analysis)
   - [Phase 3: Write a Hypothesis](#phase-3-write-a-hypothesis)
   - [Phase 4: Design a Strategy](#phase-4-design-a-strategy)
   - [Phase 5: Run a Backtest](#phase-5-run-a-backtest)
   - [Phase 6: Optimize Parameters](#phase-6-optimize-parameters)
   - [Phase 7: Walk-Forward Validation](#phase-7-walk-forward-validation)
   - [Phase 8: Forward Test on Demo](#phase-8-forward-test-on-demo)
   - [Phase 9: Deploy to Live MT5](#phase-9-deploy-to-live-mt5)
6. [Acceptance Criteria](#6-acceptance-criteria)
7. [Key Documents](#7-key-documents)

---

## 1. Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| [MetaTrader 5](https://www.metatrader5.com/en/download) | Latest | Data export, EA deployment |
| [Bun](https://bun.sh) | ≥ 1.1 | Run TypeScript natively (recommended) |
| [Node.js](https://nodejs.org) | ≥ 20 LTS | Alternative to Bun |
| [Claude Code](https://claude.ai/code) | Latest | AI-assisted R&D via slash commands |

> **Bun vs Node:** Bun runs `.ts` files directly without a compile step (`bun file.ts`). If you use Node, prefix every run command with `npx tsx` instead of `bun`.

---

## 2. Installation

```bash
# Clone the repo
git clone <your-repo-url>
cd nsl-trading-mt5

# Install TypeScript dependencies
bun install
# or: npm install

# Verify TypeScript compiles cleanly
bun run check
# or: npx tsc --noEmit
```

---

## 3. Project Structure

```
nsl-trading-mt5/
├── CLAUDE.md                        ← Master AI agent instructions (read this first)
├── README.md                        ← This file
├── package.json
├── tsconfig.json
│
├── .claude/
│   └── commands/                    ← Custom slash commands for Claude Code
│       ├── hypothesis.md            → /hypothesis
│       ├── strategy.md              → /strategy
│       ├── phase.md                 → /phase
│       ├── validate.md              → /validate
│       ├── backtest.md              → /backtest
│       └── experiment.md            → /experiment
│
├── .agent/
│   ├── SKILLS.md                    ← Skill index (AI agents read this)
│   └── skills/
│       ├── data-analysis.md
│       ├── strategy-design.md
│       ├── backtesting.md
│       ├── optimization.md
│       ├── risk-management.md
│       ├── mql5-coding.md
│       └── performance-metrics.md
│
├── docs/
│   ├── RND_LIFECYCLE.md             ← Full 10-phase lifecycle (phase-by-phase guide)
│   └── DATASET_REQUIREMENTS.md     ← Exact data specs and export instructions
│
├── src/                             ← Shared TypeScript types and utilities
│   └── types.ts
│
├── data/
│   ├── raw/                         ← CSV files exported from MT5 (never modify)
│   │   └── XAUUSD/
│   └── processed/                   ← Cleaned + feature-engineered JSON files
│
├── research/
│   ├── hypothesis/                  ← HYP-NNN_name.md — one file per hypothesis
│   └── experiments/                 ← EXP-NNN_name.md — one file per experiment run
│
├── strategies/                      ← TypeScript strategy files
│   ├── STR-001_name.ts              ← Strategy logic
│   └── STR-001_name.run.ts          ← Backtest runner
│
├── backtests/                       ← JSON result files from backtest runs
│
└── ea/                              ← Final MQL5 EA source code (.mq5 files)
```

---

## 4. Custom Commands

Open this project in **Claude Code** to use the slash commands below. Type them in the Claude Code chat.

| Command | Usage | What it does |
|---------|-------|--------------|
| `/phase` | `/phase` | Shows current R&D phase, checklist status, and what's needed to advance |
| `/hypothesis` | `/hypothesis London Open Breakout` | Creates a new hypothesis file from template in `research/hypothesis/` |
| `/strategy` | `/strategy HYP-001 London Breakout` | Scaffolds a new TypeScript strategy + runner file in `strategies/` |
| `/validate` | `/validate data/raw/XAUUSD/XAUUSD_H1.csv` | Validates a data file and prints a quality report |
| `/backtest` | `/backtest STR-001` | Runs the strategy's backtest runner and prints a full performance report |
| `/experiment` | `/experiment H1 session bias EDA` | Creates a new experiment log entry in `research/experiments/` |

### Tips for using commands

- Run `/phase` at the start of every session to orient yourself.
- Always run `/validate` on data before any analysis.
- Run `/hypothesis` before writing any strategy code — hypothesis first, always.
- Run `/backtest` after completing a strategy to get the verdict automatically.

---

## 5. R&D Workflow — Step by Step

Follow the phases below in order. Never skip a phase.

---

### Phase 0: Setup

**Goal:** Confirm tools, broker config, and data pipeline are ready.

```bash
# 1. Verify Bun works
bun --version

# 2. Install dependencies
bun install

# 3. Check TypeScript
bun run check
```

Create `docs/BROKER_CONFIG.md` with your broker's actual values:

```markdown
# Broker Configuration

- Broker: [your broker name]
- Account type: ECN / Standard
- XAUUSD Spread (average): X.XX USD
- XAUUSD Commission: $X per lot round-trip
- Swap Long per lot/day: -$X.XX
- Swap Short per lot/day: +$X.XX
- Leverage: 1:X
- Min lot: 0.01
- Lot step: 0.01
```

**Done when:** You can load the project in Claude Code and run `/phase` successfully.

---

### Phase 1: Export & Validate Data

**Goal:** Get clean XAUUSD OHLCV data into `data/raw/`.

#### Step 1.1 — Export from MT5

Open MetaEditor in MT5, create a new Script, paste the export script from [`docs/DATASET_REQUIREMENTS.md`](docs/DATASET_REQUIREMENTS.md), and run it on a chart.

Export these timeframes (priority order):

| Timeframe | Min History | File |
|-----------|-------------|------|
| H1 | 10 years | `XAUUSD_H1_YYYYMMDD_YYYYMMDD.csv` |
| D1 | 15 years | `XAUUSD_D1_YYYYMMDD_YYYYMMDD.csv` |
| H4 | 10 years | `XAUUSD_H4_YYYYMMDD_YYYYMMDD.csv` |
| M15 | 5 years | `XAUUSD_M15_YYYYMMDD_YYYYMMDD.csv` |
| M5 | 3 years | `XAUUSD_M5_YYYYMMDD_YYYYMMDD.csv` |
| M1 | 2 years | `XAUUSD_M1_YYYYMMDD_YYYYMMDD.csv` |

Move the exported files into `data/raw/XAUUSD/`.

#### Step 1.2 — Validate each file

```
/validate data/raw/XAUUSD/XAUUSD_H1_20150101_20260515.csv
```

All files must show `Quality: PASS` before proceeding. Fix any issues reported.

---

### Phase 2: Exploratory Data Analysis

**Goal:** Understand XAUUSD price behavior deeply before forming any strategy ideas.

Ask Claude Code to run EDA, referencing the skill:

```
Read .agent/skills/data-analysis.md and run a full EDA on data/raw/XAUUSD/XAUUSD_H1_*.csv.
Cover: session analysis, return distribution, day-of-week bias, volatility regimes.
Save findings to research/experiments/EXP-001_eda_h1.md
```

Or log the experiment with the command:
```
/experiment H1 full EDA — session bias, return distribution, volatility regimes
```

**Minimum output from EDA:**
- Session stats table (avg range, bull%, volume per session)
- Return distribution stats (mean, std, kurtosis, skewness)
- Day-of-week return heatmap (saved as JSON to `data/processed/`)
- At least 5 documented price behavior observations

**Done when:** You have 1+ experiment log files in `research/experiments/` with concrete observations.

---

### Phase 3: Write a Hypothesis

**Goal:** Turn EDA observations into a falsifiable hypothesis before writing any code.

```
/hypothesis London Open Momentum
```

This creates `research/hypothesis/HYP-001_london-open-momentum.md` from template.

Open the file and fill in every section:
- **Statement** — one precise sentence
- **Reasoning** — the market mechanism
- **Measurable Prediction** — expected win rate, R multiple, frequency
- **Invalidation Criteria** — what result kills this idea

> Rule: If you cannot state the invalidation criteria, the hypothesis is not specific enough.

**Done when:** At least 1 hypothesis file is complete with all sections filled in.

---

### Phase 4: Design a Strategy

**Goal:** Translate the hypothesis into executable TypeScript rules.

```
/strategy HYP-001 London Open Momentum
```

This creates:
- `strategies/STR-001_london-open-momentum.ts` — the strategy logic (with TODOs)
- `strategies/STR-001_london-open-momentum.run.ts` — the backtest runner

Open `STR-001_london-open-momentum.ts` and implement the 4 layers:

```
Layer 1: Regime filter   — volatility range, session, weekend
Layer 2: HTF bias        — EMA trend direction on H4
Layer 3: Entry trigger   — implement the actual entry condition
Layer 4: Trade mgmt      — ATR stop + fixed-R target
```

Read `.agent/skills/strategy-design.md` for the full signal catalog (breakout, RSI, Bollinger, FVG).

**Done when:** `generateSignals()` returns signals on the in-sample data (check with a quick `console.log`).

---

### Phase 5: Run a Backtest

**Goal:** Test the strategy on historical data with realistic costs.

```
/backtest STR-001
```

Claude Code will run `strategies/STR-001_*.run.ts`, print the full report, and save results to `backtests/`.

You can also run it directly:
```bash
bun strategies/STR-001_london-open-momentum.run.ts
```

**Minimum acceptance bar (in-sample):**

| Metric | Required |
|--------|----------|
| Sharpe (annualized) | ≥ 1.5 |
| Max Drawdown | ≤ 15% |
| Profit Factor | ≥ 1.4 |
| Trades | ≥ 200 |
| Positive in every 2-year sub-period | Yes |

If the strategy fails, return to Phase 3 or Phase 2. **Do not try to fix it by re-optimizing.**

**Done when:** Strategy passes all in-sample acceptance criteria.

---

### Phase 6: Optimize Parameters

**Goal:** Find robust parameter ranges — not the single best-fit value.

Read `.agent/skills/optimization.md`, then ask Claude Code:

```
Run parameter optimization for STR-001.
Search grid: atrSlMult [1.0,1.5,2.0,2.5], atrTpMult [2.0,2.5,3.0,3.5,4.0].
Show heatmap data and select the most robust parameters.
Log results to research/experiments/.
```

Rules that must be followed:
- Optimize on in-sample data only (70%)
- Run sensitivity test (±20%) on chosen parameters
- Chosen params must be from a **flat zone**, not a sharp peak
- After selecting params, re-run the full walk-forward (Phase 7) with those params

**Done when:** Parameters selected, sensitivity test shows `allRobust: true`, logged in `research/experiments/OPT-001_*.md`.

---

### Phase 7: Walk-Forward Validation

**Goal:** Prove the strategy generalizes to unseen data.

Ask Claude Code:
```
Run 5-window expanding walk-forward analysis for STR-001 with the optimized parameters.
Run Monte Carlo simulation (1000 shuffles) on the combined OOS trades.
Compare OOS vs IS Sharpe — compute WF efficiency.
```

**Exit criteria:**

| Metric | Required |
|--------|----------|
| OOS Sharpe | ≥ 1.0 |
| WF Efficiency (OOS Sharpe / IS Sharpe) | ≥ 0.60 |
| Monte Carlo 95th-percentile drawdown | ≤ 25% |
| OOS trades | ≥ 50 |

If WF efficiency < 0.60, the strategy is overfit. Return to Phase 3.

**Done when:** Walk-forward passes all criteria. Results saved to `backtests/BT_STR-001_walkforward_*.json`.

---

### Phase 8: Forward Test on Demo

**Goal:** Validate the strategy in live market conditions without real capital.

#### Step 8.1 — Compile the EA

Ask Claude Code to translate the validated strategy to MQL5:
```
Read .agent/skills/mql5-coding.md and translate STR-001 to MQL5.
Save to ea/STR-001_LondonMomentum/STR-001_LondonMomentum.mq5
```

#### Step 8.2 — Deploy on demo

1. Open MetaEditor in MT5
2. Open the generated `.mq5` file from `ea/`
3. Press F7 (Compile) — must show 0 errors
4. In MT5: Navigator → Expert Advisors → drag onto a XAUUSD H1 chart
5. Enable "Allow Algo Trading" button in the toolbar
6. Set all `input` parameters to match your TypeScript backtest parameters

#### Step 8.3 — Monitor for minimum 4 weeks

Log every week:
```
/experiment STR-001 forward test week 1 — X trades, PnL $X, notes
```

**Exit criteria:**
- Live Sharpe (annualized) ≥ 0.8 over the test period
- No critical bugs or unexpected EA behavior
- Fill quality within ±20% of backtest assumptions

---

### Phase 9: Deploy to Live MT5

**Goal:** Go live with controlled risk.

1. Start at **10% of intended lot size** (ramp-up period — 2 weeks)
2. Set all circuit breakers in the EA:
   - `MaxDailyLoss_Pct = 2.0`
   - `MaxDrawdown_Pct = 10.0`
   - `KillDrawdown_Pct = 15.0`
3. Monitor daily. Weekly review against backtest expectations.
4. After 2 weeks with no critical issues, scale to 50% → 100% lot size.

> If live drawdown hits 10%, stop the EA and investigate before resuming.

---

## 6. Acceptance Criteria

A strategy must pass all of these before going live:

| Stage | Metric | Threshold |
|-------|--------|-----------|
| In-sample | Sharpe (annualized) | ≥ 1.5 |
| In-sample | Max Drawdown | ≤ 15% |
| In-sample | Profit Factor | ≥ 1.4 |
| In-sample | Trades | ≥ 200 |
| Walk-forward | OOS Sharpe | ≥ 1.0 |
| Walk-forward | WF Efficiency | ≥ 0.60 |
| Walk-forward | Monte Carlo 95th DD | ≤ 25% |
| Forward test | Live Sharpe (annualized) | ≥ 0.8 |
| Live ramp-up | Max risk per trade | ≤ 0.5% |
| Live normal | Max risk per trade | ≤ 1.0% |

---

## 7. Key Documents

| Document | Purpose |
|----------|---------|
| [`CLAUDE.md`](CLAUDE.md) | Master rules for all AI agents — read first |
| [`docs/RND_LIFECYCLE.md`](docs/RND_LIFECYCLE.md) | Detailed 10-phase lifecycle with checklists |
| [`docs/DATASET_REQUIREMENTS.md`](docs/DATASET_REQUIREMENTS.md) | Data specs and MT5 export instructions |
| [`.agent/SKILLS.md`](.agent/SKILLS.md) | Index of all AI agent skills |
| [`.agent/skills/data-analysis.md`](.agent/skills/data-analysis.md) | TypeScript data loading, validation, EDA |
| [`.agent/skills/strategy-design.md`](.agent/skills/strategy-design.md) | 4-layer strategy architecture, signal catalog |
| [`.agent/skills/backtesting.md`](.agent/skills/backtesting.md) | Backtest engine, walk-forward, Monte Carlo |
| [`.agent/skills/optimization.md`](.agent/skills/optimization.md) | Anti-overfitting grid search, sensitivity test |
| [`.agent/skills/risk-management.md`](.agent/skills/risk-management.md) | Position sizing, circuit breakers, news filter |
| [`.agent/skills/mql5-coding.md`](.agent/skills/mql5-coding.md) | MQL5 EA template and coding standards |
| [`.agent/skills/performance-metrics.md`](.agent/skills/performance-metrics.md) | Full metrics calculator and report format |

---

## Current Status

**Active Phase:** Phase 0 — Framework Setup
**Next Action:** Create `docs/BROKER_CONFIG.md`, then export H1 and D1 data from MT5.
