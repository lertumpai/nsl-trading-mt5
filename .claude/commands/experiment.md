---
description: Create a new experiment log entry. Usage: /experiment <description>
---

Create a new experiment log entry in `research/experiments/`.

**Description provided:** $ARGUMENTS

Steps:
1. List files in `research/experiments/` to find the highest existing EXP number.
2. Use the next number (EXP-001 if none exist).
3. Create `research/experiments/EXP-{NNN}_{slug}.md` where slug comes from the description argument.
4. Fill in the template below, pre-populating what can be inferred from context (today's date, existing strategy/hypothesis files):

```markdown
# Experiment EXP-{NNN}: {Description}

**Date:** {today}
**Phase:** {current phase from CLAUDE.md}
**Type:** <!-- EDA / Backtest / Optimization / WalkForward / ForwardTest / Other -->

---

## Objective
<!-- What question does this experiment answer? -->

## Setup
- **Strategy:** <!-- STR-NNN or N/A -->
- **Hypothesis:** <!-- HYP-NNN or N/A -->
- **Data:** <!-- XAUUSD TF, date range -->
- **Parameters:** <!-- key params used -->

## Method
<!-- How was this run? In-sample only / OOS / WF / Monte Carlo -->

## Results

### Key Metrics
| Metric | Value | Threshold | Pass? |
|--------|-------|-----------|-------|
| Sharpe (OOS) | | ≥ 1.0 | |
| Max DD | | ≤ 15% | |
| Profit Factor | | ≥ 1.4 | |
| Trades (OOS) | | ≥ 50 | |

### Observations
-

### Charts / Output Files
- <!-- path to backtest JSON, heatmap, etc. -->

## Verdict
<!-- PASS / FAIL / INCONCLUSIVE — one sentence explanation -->

## Next Step
<!-- What does this result mean for the next action? -->
```

5. Print: `Created: research/experiments/EXP-{NNN}_{slug}.md`
