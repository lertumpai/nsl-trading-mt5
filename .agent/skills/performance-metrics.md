# Skill: Performance Metrics & Evaluation

> Use this skill to compute and interpret EA performance.
> Language: **TypeScript**. All metrics must be calculated on out-of-sample data.

---

## Full Metrics Calculator (`src/metrics.ts`)

```typescript
import type { Trade } from './types.js';

export interface PerformanceMetrics {
  // Sample
  nTrades: number;
  tradesPerMonth: number;
  testPeriodDays: number;

  // Returns
  totalPnlUsd: number;
  totalReturnPct: number;
  annualizedReturnPct: number;

  // Win/Loss
  winRate: number;
  avgWinUsd: number;
  avgLossUsd: number;
  payoffRatio: number;
  profitFactor: number;
  expectancyUsd: number;

  // Risk
  maxDrawdownPct: number;
  maxDdDurationBars: number;

  // Risk-Adjusted
  sharpeRatio: number;
  sortinoRatio: number;
  calmarRatio: number;

  // Consistency
  maxConsecWins: number;
  maxConsecLosses: number;
  monthlyPositivePct: number;

  // Acceptance
  passesAcceptance: AcceptanceResult;
}

export interface AcceptanceResult {
  sharpeOk: boolean;
  ddOk: boolean;
  pfOk: boolean;
  sampleOk: boolean;
  allPass: boolean;
}

const TRADING_DAYS_PER_YEAR = 252;

export function computeAllMetrics(
  trades: Trade[],
  initialCapital = 10_000,
): PerformanceMetrics {
  if (trades.length < 5) {
    return emptyMetrics(trades.length);
  }

  const sorted = [...trades].sort((a, b) => a.entryTime.getTime() - b.entryTime.getTime());
  const pnls = sorted.map(t => t.pnlUsd);

  // ---- Core P&L ----
  const totalPnl = pnls.reduce((a, b) => a + b, 0);
  const winners  = sorted.filter(t => t.pnlUsd > 0);
  const losers   = sorted.filter(t => t.pnlUsd < 0);
  const nTrades  = sorted.length;
  const winRate  = winners.length / nTrades;

  const avgWin  = winners.length > 0 ? winners.reduce((s, t) => s + t.pnlUsd, 0) / winners.length : 0;
  const avgLoss = losers.length  > 0 ? losers.reduce((s, t)  => s + t.pnlUsd, 0) / losers.length  : 0;

  const grossWin  = winners.reduce((s, t) => s + t.pnlUsd, 0);
  const grossLoss = Math.abs(losers.reduce((s, t) => s + t.pnlUsd, 0));
  const profitFactor = grossLoss > 0 ? grossWin / grossLoss : Infinity;
  const expectancyUsd = totalPnl / nTrades;

  // ---- Equity Curve ----
  const equity: number[] = [];
  let running = initialCapital;
  for (const pnl of pnls) { running += pnl; equity.push(running); }

  let peak = initialCapital;
  let maxDd = 0;
  let maxDdDuration = 0;
  let ddDuration = 0;
  for (const eq of equity) {
    peak = Math.max(peak, eq);
    const dd = (eq - peak) / peak;
    maxDd = Math.min(maxDd, dd);
    if (eq < peak) { ddDuration++; maxDdDuration = Math.max(maxDdDuration, ddDuration); }
    else { ddDuration = 0; }
  }

  // ---- Daily Returns ----
  const dailyMap = new Map<string, number>();
  for (const t of sorted) {
    const key = t.exitTime.toISOString().slice(0, 10);
    dailyMap.set(key, (dailyMap.get(key) ?? 0) + t.pnlUsd);
  }
  const dailyPnls = [...dailyMap.values()];
  const dailyReturns = dailyPnls.map(p => p / initialCapital);
  const meanDailyReturn = dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length;
  const dailyStd = stdDev(dailyReturns);
  const sharpeRatio = dailyStd > 0
    ? (meanDailyReturn / dailyStd) * Math.sqrt(TRADING_DAYS_PER_YEAR)
    : 0;

  // ---- Sortino ----
  const downside = dailyReturns.filter(r => r < 0);
  const downsideStd = stdDev(downside);
  const sortinoRatio = downsideStd > 0
    ? (meanDailyReturn / downsideStd) * Math.sqrt(TRADING_DAYS_PER_YEAR)
    : 0;

  // ---- Calmar ----
  const testDays = (sorted.at(-1)!.exitTime.getTime() - sorted[0].entryTime.getTime()) / 86_400_000;
  const annualizedReturn = Math.pow(equity.at(-1)! / initialCapital, TRADING_DAYS_PER_YEAR / (testDays || 1)) - 1;
  const calmarRatio = maxDd !== 0 ? annualizedReturn / Math.abs(maxDd) : 0;

  // ---- Consecutive stats ----
  const [maxConsecWins, maxConsecLosses] = consecutiveStats(sorted.map(t => t.pnlUsd > 0));

  // ---- Monthly stats ----
  const monthlyMap = new Map<string, number>();
  for (const t of sorted) {
    const key = t.exitTime.toISOString().slice(0, 7); // YYYY-MM
    monthlyMap.set(key, (monthlyMap.get(key) ?? 0) + t.pnlUsd);
  }
  const monthlyPnls = [...monthlyMap.values()];
  const monthlyPositivePct = monthlyPnls.filter(p => p > 0).length / (monthlyPnls.length || 1) * 100;

  const tradesPerMonth = nTrades / (testDays / 30.44);

  return {
    nTrades,
    tradesPerMonth: +tradesPerMonth.toFixed(1),
    testPeriodDays: Math.round(testDays),
    totalPnlUsd: +totalPnl.toFixed(2),
    totalReturnPct: +(totalPnl / initialCapital * 100).toFixed(2),
    annualizedReturnPct: +(annualizedReturn * 100).toFixed(2),
    winRate: +winRate.toFixed(3),
    avgWinUsd: +avgWin.toFixed(2),
    avgLossUsd: +avgLoss.toFixed(2),
    payoffRatio: +(avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : 0).toFixed(2),
    profitFactor: +profitFactor.toFixed(2),
    expectancyUsd: +expectancyUsd.toFixed(2),
    maxDrawdownPct: +(maxDd * 100).toFixed(2),
    maxDdDurationBars: maxDdDuration,
    sharpeRatio: +sharpeRatio.toFixed(3),
    sortinoRatio: +sortinoRatio.toFixed(3),
    calmarRatio: +calmarRatio.toFixed(3),
    maxConsecWins,
    maxConsecLosses,
    monthlyPositivePct: +monthlyPositivePct.toFixed(1),
    passesAcceptance: checkAcceptance(sharpeRatio, maxDd, profitFactor, nTrades),
  };
}

function stdDev(values: number[]): number {
  if (values.length === 0) return 0;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  return Math.sqrt(values.map(v => (v - mean) ** 2).reduce((a, b) => a + b, 0) / values.length);
}

function consecutiveStats(wins: boolean[]): [number, number] {
  let maxW = 0, maxL = 0, curW = 0, curL = 0;
  for (const w of wins) {
    if (w) { curW++; curL = 0; } else { curL++; curW = 0; }
    maxW = Math.max(maxW, curW);
    maxL = Math.max(maxL, curL);
  }
  return [maxW, maxL];
}

function checkAcceptance(sharpe: number, maxDd: number, pf: number, nTrades: number): AcceptanceResult {
  const sharpeOk = sharpe >= 1.0;
  const ddOk     = maxDd >= -0.15;
  const pfOk     = pf >= 1.4;
  const sampleOk = nTrades >= 50;
  return { sharpeOk, ddOk, pfOk, sampleOk, allPass: sharpeOk && ddOk && pfOk && sampleOk };
}

function emptyMetrics(n: number): PerformanceMetrics {
  return {
    nTrades: n, tradesPerMonth: 0, testPeriodDays: 0,
    totalPnlUsd: 0, totalReturnPct: 0, annualizedReturnPct: 0,
    winRate: 0, avgWinUsd: 0, avgLossUsd: 0, payoffRatio: 0,
    profitFactor: 0, expectancyUsd: 0, maxDrawdownPct: 0,
    maxDdDurationBars: 0, sharpeRatio: 0, sortinoRatio: 0, calmarRatio: 0,
    maxConsecWins: 0, maxConsecLosses: 0, monthlyPositivePct: 0,
    passesAcceptance: { sharpeOk: false, ddOk: false, pfOk: false, sampleOk: false, allPass: false },
  };
}
```

---

## Report Printer

```typescript
export function printReport(m: PerformanceMetrics, label = 'Backtest'): void {
  const line = '='.repeat(55);
  console.log(`\n${line}`);
  console.log(`  ${label} Performance Report`);
  console.log(line);

  console.log(`\n  SAMPLE`);
  console.log(`  Trades:           ${m.nTrades} (${m.tradesPerMonth}/month)`);
  console.log(`  Period:           ${m.testPeriodDays} days`);

  console.log(`\n  RETURNS`);
  console.log(`  Total P&L:        $${m.totalPnlUsd.toLocaleString()} (${m.totalReturnPct}%)`);
  console.log(`  Annualized:       ${m.annualizedReturnPct}%`);

  console.log(`\n  WIN/LOSS`);
  console.log(`  Win Rate:         ${(m.winRate * 100).toFixed(1)}%`);
  console.log(`  Avg Win:          $${m.avgWinUsd}`);
  console.log(`  Avg Loss:         $${m.avgLossUsd}`);
  console.log(`  Payoff Ratio:     ${m.payoffRatio}`);
  console.log(`  Profit Factor:    ${m.profitFactor}`);
  console.log(`  Expectancy:       $${m.expectancyUsd}/trade`);

  console.log(`\n  RISK`);
  console.log(`  Max Drawdown:     ${m.maxDrawdownPct}%`);
  console.log(`  Max DD Duration:  ${m.maxDdDurationBars} bars`);
  console.log(`  Max Consec Loss:  ${m.maxConsecLosses}`);

  console.log(`\n  RISK-ADJUSTED`);
  console.log(`  Sharpe:           ${m.sharpeRatio}`);
  console.log(`  Sortino:          ${m.sortinoRatio}`);
  console.log(`  Calmar:           ${m.calmarRatio}`);

  console.log(`\n  CONSISTENCY`);
  console.log(`  Monthly Positive: ${m.monthlyPositivePct}%`);

  const acc = m.passesAcceptance;
  console.log(`\n  ACCEPTANCE: ${acc.allPass ? 'PASS' : 'FAIL'}`);
  if (!acc.allPass) {
    if (!acc.sharpeOk) console.log(`    - Sharpe ${m.sharpeRatio} < 1.0 required`);
    if (!acc.ddOk)     console.log(`    - Drawdown ${m.maxDrawdownPct}% > 15% limit`);
    if (!acc.pfOk)     console.log(`    - Profit Factor ${m.profitFactor} < 1.4 required`);
    if (!acc.sampleOk) console.log(`    - Only ${m.nTrades} trades < 50 minimum`);
  }
  console.log(`${line}\n`);
}
```

---

## Save Results to JSON

```typescript
import { writeFileSync } from 'fs';

export function saveResults(metrics: PerformanceMetrics, trades: Trade[], label: string): void {
  const ts = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const path = `backtests/BT_${label}_${ts}.json`;
  writeFileSync(path, JSON.stringify({ metrics, trades }, null, 2));
  console.log(`Results saved to ${path}`);
}
```

---

## Benchmark Comparison

| Benchmark | Sharpe | Ann. Return | Max DD |
|---|---|---|---|
| Buy & Hold XAUUSD (10yr) | ~0.6 | ~8% | ~45% |
| SPY Buy & Hold | ~0.9 | ~10% | ~34% |
| **Our Minimum Bar** | **1.0** | **> 0%** | **< 15%** |
| **Our Target** | **1.5** | **15–25%** | **< 10%** |

---

## Red Flags

| Pattern | Diagnosis |
|---|---|
| Profit Factor > 3 with < 100 trades | Not statistically significant |
| Win Rate > 80%, Payoff < 0.3 | Grid/martingale — hidden tail risk |
| All profit in one year | Regime-specific, not robust |
| Max DD in first month of OOS | Strategy failed immediately |
| Negative expectancy in any single year | Edge is conditional, not durable |
