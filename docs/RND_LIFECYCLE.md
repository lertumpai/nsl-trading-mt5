# XAUUSD EA — R&D Lifecycle

> All agents must follow this lifecycle in order. Do not skip phases. Each phase has defined inputs, outputs, and exit criteria.

---

## Overview

```
Phase 0: Framework Setup
    ↓
Phase 1: Data Collection & EDA
    ↓
Phase 2: Hypothesis Formation
    ↓
Phase 3: Strategy Design & Prototyping   (TypeScript)
    ↓
Phase 4: In-Sample Backtesting
    ↓
Phase 5: Parameter Optimization (guarded, IS data only)
    ↓
Phase 6: Walk-Forward Validation
    ↓
Phase 7: Risk & Portfolio Fit Analysis
    ↓
Phase 8: Forward Test (Demo/Paper)
    ↓
Phase 9: Production Deployment           (MQL5)
    ↓
Phase 10: Live Monitoring & Periodic Review
```

---

## Phase 0 — Framework Setup

**Goal:** Ensure environment, data pipelines, and tooling are ready.

**Tasks:**
- [ ] Confirm MT5 terminal is installed and connected to broker
- [ ] Confirm TypeScript runtime: `bun --version` or `node --version` (≥ 20)
- [ ] Run `bun install` — confirm dependencies install cleanly
- [ ] Confirm data export pipeline from MT5 to `data/raw/`
- [ ] Create `docs/BROKER_CONFIG.md` with spread, commission, swap, leverage
- [ ] Define trading capital and max risk parameters

**Output:** `docs/BROKER_CONFIG.md` with broker cost details.

**Exit Criteria:** `bun run check` passes (0 TypeScript errors) and at least one data file can be loaded.

---

## Phase 1 — Data Collection & EDA

**Goal:** Deeply understand XAUUSD price behavior before forming any strategy idea.

**Inputs:** Raw OHLCV data per `docs/DATASET_REQUIREMENTS.md`

**Tasks:**

### 1.1 Data Validation
- Check for gaps, duplicates, outliers in each timeframe
- Document data quality issues
- Cross-validate M1 bars roll up to M5, H1, etc.
- Verify timestamps are UTC and session-aligned

### 1.2 Distribution & Volatility Analysis
- Plot return distribution (daily, weekly)
- Calculate ATR at multiple timeframes (14, 20, 50 periods)
- Identify volatility regimes (low/medium/high) using rolling std
- Measure kurtosis and skewness — XAUUSD has fat tails

### 1.3 Session Analysis
- Segment data by session: Asian, London, NY, Overlap
- Compare: average range, directional bias, mean reversion vs trend behavior per session
- Identify which session has the highest alpha potential

### 1.4 Pattern & Structure Analysis
- Day-of-week return bias
- Monthly seasonality
- Opening range breakout characteristics (London open, NY open)
- Key price levels: round numbers, weekly/monthly pivots

### 1.5 Correlation Study
- XAUUSD vs DXY: rolling correlation
- XAUUSD vs US10Y yield
- XAUUSD vs Silver (XAGUSD)
- XAUUSD vs VIX
- XAUUSD vs Oil (XTIUSD)

### 1.6 Multi-Timeframe Structure
- HTF (D1/H4) trend identification methods
- LTF (M15/M5/M1) entry zone behavior
- How LTF price behaves at HTF key levels

**Output:** `research/experiments/eda_YYYYMMDD.md` with charts, findings, and anomalies.

**Exit Criteria:** Minimum 10 documented price behavior observations that could lead to a tradeable edge.

---

## Phase 2 — Hypothesis Formation

**Goal:** Convert EDA observations into falsifiable trading hypotheses.

**Template for each hypothesis** (saved to `research/hypothesis/HYP_XXX_name.md`):

```markdown
# Hypothesis HYP-001: [Short Name]

## Statement
[One sentence: "When X occurs, Y tends to happen within Z bars/time"]

## Reasoning
[Why does this edge exist? What market mechanism causes it?]

## Key Variables
- Entry condition: ...
- Timeframe: ...
- Session filter: ...
- Volatility regime: ...

## Measurable Prediction
- Expected win rate: ~X%
- Expected average R multiple: ~X
- Expected frequency: ~X trades/month

## Invalidation Criteria
[What result would falsify this hypothesis?]
```

**Tasks:**
- Generate minimum 5 hypotheses from EDA findings
- Rank by conviction level (High / Medium / Low)
- Start with the 2–3 highest conviction hypotheses
- Each hypothesis must reference specific EDA observations

**Output:** At least 3 hypothesis files in `research/hypothesis/`

**Exit Criteria:** At least 1 hypothesis with clear measurable predictions and an invalidation criterion.

---

## Phase 3 — Strategy Design & Prototyping

**Goal:** Translate hypothesis into a concrete, executable trading rule set.

**Tasks:**

### 3.1 Define Entry Logic
- Primary signal (indicator/pattern/level)
- Confirmation filter (reduces false signals)
- Session/time filter
- Volatility filter (e.g., ATR > threshold)
- Trend alignment filter (HTF direction)

### 3.2 Define Exit Logic
- Take Profit: fixed R, ATR-based, or structure-based
- Stop Loss: structure-based or ATR-based (never arbitrary)
- Trailing stop rules (if any)
- Time-based exit (close before weekend, before high-impact news)

### 3.3 Define Trade Management
- Can the trade be scaled in or out?
- Partial close rules
- Break-even move rules

### 3.4 Prototype in TypeScript
- Use `/strategy HYP-NNN name` to scaffold the strategy file
- Implement `generateSignals()` with the 4-layer structure (see `.agent/skills/strategy-design.md`)
- Signals are valid only after bar close; entry is on next bar open — no look-ahead
- Count trade frequency in the in-sample period — must be ≥ 5/month to be testable

**Output:** `strategies/STR-NNN_name.ts` + `strategies/STR-NNN_name.run.ts`

**Exit Criteria:** Strategy compiles (`bun run check`), generates ≥ 10 signals on in-sample data, equity curve is visually non-random.

---

## Phase 4 — In-Sample Backtesting

**Goal:** Rigorously test the strategy on the in-sample period with realistic assumptions.

**Realistic Assumptions (mandatory):**
- Spread: use actual broker spread from `docs/BROKER_CONFIG.md`
- Commission: per-lot commission (ECN style)
- Slippage: add 0.5–1.0 pip slippage to every fill
- Swap: apply overnight swap costs for multi-day holds
- Execution: next-bar open (no bar-in-bar execution on signal bar)

**Tasks:**
- Run backtest on in-sample period (first 70% of data)
- Record all metrics (see `.agent/skills/performance-metrics.md`)
- Plot equity curve, drawdown curve, monthly returns heatmap
- Analyze losing streaks: max consecutive losses, recovery time
- Check for seasonal biases (does it work in all years?)

**Output:** `backtests/BT_XXX_insample_YYYYMMDD.json` + summary report.

**Exit Criteria (all must pass):**
- Sharpe ≥ 1.5
- Max Drawdown ≤ 15%
- Profit Factor ≥ 1.4
- ≥ 200 trades
- Positive in every 2-year sub-period

---

## Phase 5 — Parameter Optimization (Guarded)

**Goal:** Find robust parameter ranges — NOT the single best-fit parameters.
Optimization runs on in-sample data only, then the selected parameters are validated in Phase 6.

**Anti-Overfitting Rules (mandatory):**
- Never optimize more than 3 free parameters at once
- Accept a parameter only if it's robust in a ±20% neighborhood (sensitivity test)
- Use grid search on in-sample only (first 70%) — never look at OOS during optimization
- Prefer flat parameter landscapes over sharp peaks
- Document every optimization run

**Tasks:**
- Define parameter ranges with step sizes
- Run grid search on in-sample period using `src/optimization.ts`
- Plot 2D heatmaps (save JSON to `research/experiments/`) for each parameter pair
- Run sensitivity test — require `allRobust: true` before accepting params
- Update `DEFAULT_PARAMS` in the strategy file with the chosen values

**Output:** `research/experiments/OPT-NNN_name_YYYYMMDD.md` with search space, top results, sensitivity test, and chosen values.

**Exit Criteria:** Chosen parameters pass the sensitivity test. Strategy re-compiles with updated params.

---

## Phase 6 — Walk-Forward Validation

**Goal:** Prove the optimized strategy generalizes to unseen data.

**Walk-Forward Method:**
- Expanding window: train on growing in-sample, test on fixed next window (~6 months)
- Minimum 5 OOS windows
- Combine all OOS windows into one equity curve

**WF Efficiency = OOS Sharpe ÷ IS Sharpe** (must be ≥ 0.60)

**Tasks:**
- Run walk-forward using `src/walkForward.ts`
- Calculate combined OOS metrics across all windows
- Compute WF Efficiency = OOS Sharpe / IS Sharpe
- Run Monte Carlo simulation (1,000 shuffles) on combined OOS trades

**Output:** `backtests/BT-NNN_walkforward_YYYYMMDD.json`

**Exit Criteria:**
- OOS Sharpe ≥ 1.0
- WF Efficiency ≥ 0.60
- Monte Carlo 95th-percentile drawdown ≤ 25%
- OOS trade count ≥ 50

**If fails:** Return to Phase 2 or Phase 3. Do NOT re-optimize to fix a failing walk-forward.

---

## Phase 7 — Risk & Portfolio Fit Analysis

**Goal:** Size the strategy correctly and confirm it fits within portfolio risk limits.

**Tasks:**
- Calculate optimal position size using fixed fractional (1–2% risk per trade)
- Stress test with doubled spread/slippage
- Test on 2008, 2020, 2022 high-volatility periods specifically
- Confirm maximum concurrent open trades and correlation with any other live EA
- Set hard circuit-breakers: daily loss limit, weekly loss limit, max drawdown kill-switch

**Output:** `research/experiments/RISK_XXX_YYYYMMDD.md`

**Exit Criteria:** Strategy survives stress tests. Kill-switch thresholds defined.

---

## Phase 8 — Forward Test (Demo Account)

**Goal:** Validate the EA in live market conditions without real capital.

**Duration:** Minimum 4 weeks, target 8 weeks.

**Tasks:**
- Compile and deploy EA in MT5 demo account
- Log every signal, entry, exit with timestamp
- Compare actual fills vs backtest assumptions (slippage, spread)
- Monitor for technical issues: reconnections, spread spikes, news events
- Weekly review: performance vs expectations

**Output:** Weekly trade logs in `research/experiments/FWDTEST_XXX_weekNN.csv`

**Exit Criteria:**
- Live Sharpe (annualized) ≥ 0.8 over forward test period
- No critical bugs or unexpected behavior
- Fill quality matches backtest assumptions within ±20%

---

## Phase 9 — Production Deployment

**Goal:** Go live with controlled risk.

**Tasks:**
- Set initial lot size to 10% of intended final size (ramp-up phase)
- Configure monitoring alerts (daily PnL, drawdown, connectivity)
- Create runbook: what to do if drawdown hits 5%, 10%, 15%
- Schedule weekly performance review

**Output:** `ea/LIVE_XXX_v1.0.mq5` — final compiled EA.

**Exit Criteria:** 2 weeks live at 10% size without critical issues.

---

## Phase 10 — Live Monitoring & Periodic Review

**Goal:** Sustain edge and detect regime changes early.

**Cadence:**
- Daily: check PnL, drawdown, open positions
- Weekly: review trade log vs expectations
- Monthly: full performance report vs backtest benchmarks
- Quarterly: re-run walk-forward on updated data — if Sharpe drops below 0.5, pause EA and investigate

**Regime Change Detection:**
- Rolling 3-month Sharpe dropping below 0.3 → yellow flag
- Rolling 3-month Sharpe negative → red flag, pause EA
- Drawdown exceeding 10% live → pause, investigate

---

## Phase Exit Checklist

Before moving from any phase to the next, answer YES to all:

- [ ] All outputs from this phase are saved to the correct folder
- [ ] Results are documented with date, data range, and parameters used
- [ ] Exit criteria are explicitly verified (not just "looks good")
- [ ] CLAUDE.md "Current Phase" section is updated
