---
description: Create a new trading hypothesis file from template. Usage: /hypothesis <short-name>
---

Create a new hypothesis file for the XAUUSD EA project.

**Argument provided:** $ARGUMENTS

Steps:
1. Read `research/hypothesis/` to find the highest existing HYP number (e.g. HYP-003), then use the next number.
2. Convert the argument into a slug (e.g. "London Breakout" → `london-breakout`).
3. Create the file at `research/hypothesis/HYP-{NNN}_{slug}.md` using exactly this template:

```
# Hypothesis HYP-{NNN}: {Full Name from argument}

**Created:** {today's date}
**Status:** Draft
**Conviction:** Medium  <!-- Low / Medium / High -->

---

## Statement
<!-- One sentence: "When X occurs on XAUUSD, Y tends to happen within Z bars/time" -->

## Reasoning
<!-- Why does this edge exist? What market mechanism or participant behavior causes it? -->

## Key Variables
- **Entry condition:**
- **Timeframe (LTF):**
- **Bias timeframe (HTF):**
- **Session filter:**
- **Volatility regime:**
- **Additional filters:**

## Measurable Prediction
- Expected win rate: ~X%
- Expected average R multiple: ~X
- Expected trade frequency: ~X trades/month
- Expected Sharpe (rough): ~X

## Supporting EDA Observations
<!-- Reference specific findings from research/experiments/eda_*.md -->
-

## Invalidation Criteria
<!-- What backtest result would prove this hypothesis wrong? -->
- If win rate < X% over ≥ 100 trades
- If Profit Factor < 1.2 over ≥ 100 trades
- If the edge disappears when excluding [specific year/condition]

## Related Hypotheses
<!-- Link to similar or conflicting hypotheses -->
-

## Next Step
Proceed to Phase 3: create `strategies/STR-{NNN}_{slug}.ts`
```

4. After creating the file, print a one-line summary:
   `Created: research/hypothesis/HYP-{NNN}_{slug}.md`
