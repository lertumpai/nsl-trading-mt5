# Framework Review

Review date: 2026-05-15  
Scope: repository structure, TypeScript framework, agent skills, Claude commands, R&D lifecycle docs, MQL5 guidance.

## Summary

This is a strong early-stage R&D framework for XAUUSD MT5 EA development. The intent is clear: hypothesis first, data validation before strategy work, realistic costs, walk-forward validation, risk controls, and MQL5 deployment only after research passes.

The biggest weakness is consistency. Several documents disagree about the language, phase order, acceptance thresholds, and file naming. Some code templates also contain issues that would create look-ahead bias or fail in MQL5/TypeScript once generated. Before adding strategy logic, the framework should be normalized into one source of truth and the promised TypeScript modules should be implemented.

## Good

1. Clear research discipline
   - `CLAUDE.md` has the right non-negotiables: hypothesis before code, no data leakage, mandatory walk-forward validation, experiment logging, and immutable raw data.
   - The project avoids the common mistake of jumping straight into indicator code.

2. Good separation of responsibilities
   - `data/raw`, `data/processed`, `research/hypothesis`, `research/experiments`, `strategies`, `backtests`, and `ea` are well-separated.
   - TypeScript research and MQL5 production deployment are conceptually separated.

3. Useful agent skill coverage
   - The `.agent/skills` files cover the full lifecycle: data analysis, strategy design, backtesting, optimization, risk, performance metrics, and MQL5.
   - The skill index and command files give agents a practical operating model.

4. Strong trading-specific guardrails
   - XAUUSD session behavior, spread spikes, swap, news risk, weekend risk, ATR stops, and fixed fractional sizing are all explicitly called out.
   - The framework emphasizes realistic execution assumptions instead of optimistic backtests.

5. Type foundation is a good start
   - `src/types.ts` defines the core domain objects cleanly: bars, features, signals, costs, trades, and strategy params.
   - Shared types will help keep generated strategy/backtest code consistent.

## Bad / Risks

1. Documentation conflicts will confuse agents
   - `README.md` says research is TypeScript, but `docs/RND_LIFECYCLE.md` still says Phase 3 prototypes in Python and outputs `STR_XXX_name.py`.
   - `.agent/skills/mql5-coding.md` also says "Python research -> MQL5 production", which contradicts `CLAUDE.md` and the README.
   - `README.md` lists Phase 6 as optimization and Phase 7 as walk-forward validation, while `docs/RND_LIFECYCLE.md` puts walk-forward before optimization and uses Phase 7 for risk analysis.

2. Acceptance thresholds are inconsistent
   - `CLAUDE.md` requires Sharpe >= 1.5 out-of-sample and at least 200 trades.
   - `.agent/skills/performance-metrics.md` uses Sharpe >= 1.0 and at least 50 trades.
   - `.claude/commands/backtest.md` also uses the lower OOS thresholds.
   - This makes PASS/FAIL verdicts unstable depending on which file an agent reads.

3. Most promised TypeScript modules do not exist yet
   - Only `src/types.ts` exists.
   - The commands and skills reference `src/dataLoader.ts`, `src/cleaner.ts`, `src/features.ts`, `src/filters.ts`, `src/bias.ts`, `src/signals.ts`, `src/management.ts`, `src/costs.ts`, `src/backtest.ts`, `src/metrics.ts`, and more.
   - Generated strategy runners will not run until these modules are actually added.

4. The generated strategy runner cannot load glob paths
   - `.claude/commands/strategy.md` generates calls like `loadOHLCV('data/raw/XAUUSD/XAUUSD_H1_*.csv')`.
   - The documented `loadOHLCV(filePath: string)` uses `createReadStream(filePath)`, which reads one literal file path and does not expand globs.

5. There are look-ahead risks in templates
   - `.agent/skills/strategy-design.md` uses `barsHtf.findIndex(b => b.datetime >= bar.datetime)` for HTF bias. That can select a future or incomplete higher-timeframe bar.
   - `.agent/skills/backtesting.md` exits on an opposite signal at `bar.open` while reading `currentSignal = signalMap.get(i)`. If signals are produced from bar close data, this exits before the signal is knowable.
   - The MQL5 template reads `iClose(_Symbol, PERIOD_H1, 0)`, which is the current incomplete bar.

6. MQL5 template has correctness problems
   - `iMA(...)` returns an indicator handle in MQL5, not the EMA value. The template compares handles as if they were prices.
   - `GetATR()` creates a new ATR handle every call, while the same file warns that this leaks memory and handles should be created in `OnInit()`.
   - Buy/Sell calls do not check return values, despite the later error-handling standard.
   - `Filter_News`, `News_Before_Min`, and `News_After_Min` inputs are defined but not used.
   - README mentions `KillDrawdown_Pct`, but the MQL5 template only defines `MaxDrawdown_Pct`.

7. Performance metrics can overstate quality
   - Sharpe is calculated from days with trades only, not every calendar/trading day in the test period. That can inflate Sharpe for sparse strategies.
   - Walk-forward efficiency in `.agent/skills/backtesting.md` is calculated using profit factor, while the docs say it should be OOS Sharpe / IS Sharpe.
   - The acceptance checker in metrics does not enforce the stricter project-level requirements.

8. Data handling is too forgiving
   - The loader silently skips bad rows when open/close are NaN.
   - The cleaner clamps OHLC violations instead of quarantining them or logging an audit trail.
   - Date parsing assumes appending `Z` is enough; MT5 exports can vary by broker/server and may require explicit parsing.

9. Risk management can exceed intended risk
   - `calculateLotSize()` clamps up to `minLot`. If the correct lot size is below minimum, the function should reject the trade or flag risk overflow, not force the minimum and violate the risk cap.
   - Weekly loss limits are required in lifecycle docs but not implemented in the TypeScript circuit breaker example.

10. Local verification currently fails
   - `npm run check` fails because `tsc` is not installed in `node_modules`.
   - There is no lockfile yet, so dependency versions are not pinned for reproducible agent runs.

## Improve

1. Create one source of truth for lifecycle and thresholds
   - Pick one phase order and update `README.md`, `docs/RND_LIFECYCLE.md`, `CLAUDE.md`, command files, and skill files to match.
   - Decide whether optimization happens before walk-forward, during each walk-forward window, or only after an initial in-sample pass.
   - Choose one acceptance standard. Recommended:
     - In-sample: Sharpe >= 1.5, max DD <= 15%, PF >= 1.4, trades >= 200.
     - Out-of-sample / walk-forward: Sharpe >= 1.0, WF efficiency >= 0.60, MC 95th DD <= 25%, OOS trades >= 50.
     - Promotion to forward test should require both sets.

2. Standardize on TypeScript everywhere
   - Replace Python references in `docs/RND_LIFECYCLE.md` and `.agent/skills/mql5-coding.md`.
   - Use one naming convention: `HYP-001_slug.md`, `STR-001_slug.ts`, `BT_STR-001_*.json`.

3. Implement the real TypeScript core before strategy generation
   - Add the modules currently only described in skills: data loader, validator, cleaner, features, filters, bias, signals, management, costs, backtest, metrics, walk-forward, optimization, risk management.
   - Add unit tests for loader, gap detection, ATR, signal timing, cost model, metrics, and lot sizing.

4. Fix signal timing and backtest execution
   - Treat all signals as known only after the signal bar closes.
   - Enter on next bar open.
   - For reversals, exit no earlier than the next bar open after the reversal signal.
   - Use last closed HTF bar only, never a future or open HTF bar.
   - Explicitly document same-bar stop/target policy.

5. Make data loading production-safe
   - Support globs intentionally, or require exact file paths.
   - Parse MT5 date formats explicitly.
   - Reject invalid dates and invalid OHLC rows in validation.
   - Keep an audit report for every cleaning operation.
   - Never silently "fix" market data without recording what changed.

6. Repair the MQL5 template before using it
   - Create indicator handles in `OnInit()`.
   - Use `CopyBuffer()` for EMA and ATR values.
   - Use closed bars, generally shift `1`, for signal calculations.
   - Implement and wire `KillDrawdown_Pct`.
   - Implement or remove the news filter inputs.
   - Check trade return values and log retcodes.
   - Release indicator handles in `OnDeinit()`.

7. Improve metric accuracy
   - Build a daily equity curve across the full test calendar, including no-trade days.
   - Calculate Sharpe/Sortino from daily equity returns, not trade-day-only PnL.
   - Compute WF efficiency as OOS Sharpe / IS Sharpe, matching the docs.
   - Make the metrics acceptance checker configurable by stage: in-sample, OOS, walk-forward, forward test.

8. Add reproducibility
   - Commit a lockfile after dependency install.
   - Add `test`, `lint`, and `format` scripts.
   - Add a small synthetic fixture CSV so validation and backtest tests can run without real broker data.
   - Add a `docs/BROKER_CONFIG.md` template and load costs from it instead of hardcoded defaults.

9. Make commands generate runnable code
   - The `/strategy` command should generate only code that compiles against existing modules.
   - The `/backtest` command should fail early with a clear missing-module or missing-data message.
   - The `/validate` command should avoid leaving temp files under `data/`; use a workspace temp path or remove reliably.

## Priority Order

1. Align docs and thresholds.
2. Implement missing TypeScript modules.
3. Fix timing/look-ahead issues in strategy and backtest templates.
4. Fix MQL5 indicator-handle/template issues.
5. Add tests and lockfile.
6. Add broker config template and data fixtures.

## Current Readiness

Framework concept: good.  
Documentation discipline: promising but inconsistent.  
Runnable research framework: not ready yet.  
Safe to generate live EA code: not yet.

The next best move is to turn the skill snippets into actual TypeScript modules with tests, while simultaneously normalizing the phase order and acceptance thresholds across all docs.
