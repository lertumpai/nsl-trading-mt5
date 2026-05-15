---
description: Run a full backtest for a strategy and print a performance report. Usage: /backtest <STR-NNN>
---

Run and report on a full backtest for the specified strategy.

**Strategy ID provided:** $ARGUMENTS

Steps:
1. Find the strategy files: `strategies/STR-{NNN}_*.ts` and `strategies/STR-{NNN}_*.run.ts`.
   If not found, list all strategies in `strategies/` and ask the user to pick one.
2. Read `.agent/skills/backtesting.md` and `.agent/skills/performance-metrics.md` for the evaluation criteria.
3. Check that the required data files exist in `data/raw/XAUUSD/` (look for H1, H4, or whatever timeframes the strategy uses).
   If data is missing, stop and tell the user exactly which files are needed.
4. Run the strategy's `.run.ts` file:
   ```
   bun strategies/{run-file}.ts
   ```
5. If `src/` utility files referenced in the strategy don't exist yet, generate the missing ones first (using the code from the skill files), then re-run.
6. After the run completes, parse the output and saved JSON from `backtests/`.
7. Print a full performance report using the format from `.agent/skills/performance-metrics.md`.
8. Evaluate against acceptance thresholds:
   - Sharpe ≥ 1.0 (OOS)
   - Max DD ≤ 15%
   - Profit Factor ≥ 1.4
   - ≥ 50 trades (OOS)
9. Give a clear verdict: PASS → proceed to walk-forward | FAIL → explain which metric failed and suggest what to investigate.
10. Save a summary to `research/experiments/BT_{STR-NNN}_{date}.md`.
