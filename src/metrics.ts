import { writeFileSync } from 'fs';
import type { Trade } from './types.js';

export interface AcceptanceResult {
  // Stage A — In-Sample
  isSharpOk:  boolean;
  isDdOk:     boolean;
  isPfOk:     boolean;
  isSampleOk: boolean;
  stageAPass: boolean;

  // Stage B — Walk-Forward / OOS
  oosSharpOk:   boolean;
  wfEffOk:      boolean;
  mcDdOk:       boolean;
  oosSampleOk:  boolean;
  stageBPass:   boolean;
}

export interface PerformanceMetrics {
  nTrades:            number;
  tradesPerMonth:     number;
  testPeriodDays:     number;

  totalPnlUsd:        number;
  totalReturnPct:     number;
  annualizedReturnPct: number;

  winRate:            number;
  avgWinUsd:          number;
  avgLossUsd:         number;
  payoffRatio:        number;
  profitFactor:       number;
  expectancyUsd:      number;

  maxDrawdownPct:     number;
  maxDdDurationDays:  number;

  sharpeRatio:        number;
  sortinoRatio:       number;
  calmarRatio:        number;

  maxConsecWins:      number;
  maxConsecLosses:    number;
  monthlyPositivePct: number;
}

const TRADING_DAYS_PER_YEAR = 252;
const INITIAL_CAPITAL_DEFAULT = 10_000;

export function computeAllMetrics(
  trades: Trade[],
  initialCapital = INITIAL_CAPITAL_DEFAULT,
): PerformanceMetrics {
  if (trades.length === 0) return emptyMetrics();

  const sorted = [...trades].sort((a, b) => a.entryTime.getTime() - b.entryTime.getTime());
  const pnls   = sorted.map(t => t.pnlUsd);

  // ── P&L ──────────────────────────────────────────────────────────────────
  const totalPnl   = pnls.reduce((a, b) => a + b, 0);
  const winners    = sorted.filter(t => t.pnlUsd > 0);
  const losers     = sorted.filter(t => t.pnlUsd < 0);
  const winRate    = winners.length / sorted.length;
  const avgWin     = winners.length > 0 ? winners.reduce((s, t) => s + t.pnlUsd, 0) / winners.length : 0;
  const avgLoss    = losers.length  > 0 ? losers.reduce((s, t)  => s + t.pnlUsd, 0) / losers.length  : 0;
  const grossWin   = winners.reduce((s, t) => s + t.pnlUsd, 0);
  const grossLoss  = Math.abs(losers.reduce((s, t) => s + t.pnlUsd, 0));
  const profitFactor = grossLoss > 0 ? grossWin / grossLoss : Infinity;

  // ── Equity Curve ─────────────────────────────────────────────────────────
  let running = initialCapital;
  let peak    = initialCapital;
  let maxDd   = 0;
  let ddDuration = 0, maxDdDuration = 0;

  for (const pnl of pnls) {
    running += pnl;
    peak     = Math.max(peak, running);
    const dd = (running - peak) / peak;
    maxDd    = Math.min(maxDd, dd);
    ddDuration = running < peak ? ddDuration + 1 : 0;
    maxDdDuration = Math.max(maxDdDuration, ddDuration);
  }
  const finalEquity = running;

  // ── Daily Returns — built over FULL calendar, not just trade-active days ──
  // This prevents inflated Sharpe from sparse strategies.
  const firstDate = sorted[0]!.entryTime;
  const lastDate  = sorted.at(-1)!.exitTime;
  const testDays  = (lastDate.getTime() - firstDate.getTime()) / 86_400_000;

  // Map each trade's exit to its UTC date
  const dailyPnlMap = new Map<string, number>();
  for (const t of sorted) {
    const key = t.exitTime.toISOString().slice(0, 10);
    dailyPnlMap.set(key, (dailyPnlMap.get(key) ?? 0) + t.pnlUsd);
  }

  // Fill every weekday between first and last date with 0 for no-trade days
  const dailyReturns: number[] = [];
  const cursor = new Date(firstDate);
  cursor.setUTCHours(0, 0, 0, 0);
  const endMs = lastDate.getTime();

  while (cursor.getTime() <= endMs) {
    const dow = cursor.getUTCDay();
    if (dow >= 1 && dow <= 5) {  // Mon–Fri only
      const key    = cursor.toISOString().slice(0, 10);
      const dayPnl = dailyPnlMap.get(key) ?? 0;
      dailyReturns.push(dayPnl / initialCapital);
    }
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }

  const meanDailyRet = dailyReturns.reduce((a, b) => a + b, 0) / (dailyReturns.length || 1);
  const dailyStd     = stdDev(dailyReturns);
  const sharpeRatio  = dailyStd > 0
    ? (meanDailyRet / dailyStd) * Math.sqrt(TRADING_DAYS_PER_YEAR) : 0;

  const downside    = dailyReturns.filter(r => r < 0);
  const downStd     = stdDev(downside);
  const sortinoRatio = downStd > 0
    ? (meanDailyRet / downStd) * Math.sqrt(TRADING_DAYS_PER_YEAR) : 0;

  const tradingDays = dailyReturns.length || 1;
  const annReturn   = Math.pow(finalEquity / initialCapital, TRADING_DAYS_PER_YEAR / tradingDays) - 1;
  const calmarRatio = maxDd !== 0 ? annReturn / Math.abs(maxDd) : 0;

  // ── Consistency ───────────────────────────────────────────────────────────
  const [maxConsecWins, maxConsecLosses] = consecutiveStats(pnls.map(p => p > 0));

  const monthlyMap = new Map<string, number>();
  for (const t of sorted) {
    const key = t.exitTime.toISOString().slice(0, 7);
    monthlyMap.set(key, (monthlyMap.get(key) ?? 0) + t.pnlUsd);
  }
  const monthlyPnls      = [...monthlyMap.values()];
  const monthlyPositivePct = monthlyPnls.length > 0
    ? monthlyPnls.filter(p => p > 0).length / monthlyPnls.length * 100 : 0;

  const tradesPerMonth = testDays > 0 ? sorted.length / (testDays / 30.44) : 0;

  return {
    nTrades:              sorted.length,
    tradesPerMonth:       +tradesPerMonth.toFixed(1),
    testPeriodDays:       Math.round(testDays),
    totalPnlUsd:          +totalPnl.toFixed(2),
    totalReturnPct:       +(totalPnl / initialCapital * 100).toFixed(2),
    annualizedReturnPct:  +(annReturn * 100).toFixed(2),
    winRate:              +winRate.toFixed(3),
    avgWinUsd:            +avgWin.toFixed(2),
    avgLossUsd:           +avgLoss.toFixed(2),
    payoffRatio:          +(avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : 0).toFixed(2),
    profitFactor:         +profitFactor.toFixed(2),
    expectancyUsd:        +(totalPnl / sorted.length).toFixed(2),
    maxDrawdownPct:       +(maxDd * 100).toFixed(2),
    maxDdDurationDays:    maxDdDuration,
    sharpeRatio:          +sharpeRatio.toFixed(3),
    sortinoRatio:         +sortinoRatio.toFixed(3),
    calmarRatio:          +calmarRatio.toFixed(3),
    maxConsecWins,
    maxConsecLosses,
    monthlyPositivePct:   +monthlyPositivePct.toFixed(1),
  };
}

/** Check acceptance against both IS and OOS/WF thresholds per CLAUDE.md. */
export function checkAcceptance(
  isMetrics:  PerformanceMetrics,
  oosMetrics: PerformanceMetrics,
  wfEfficiency: number,
  mcDd95th:   number,
): AcceptanceResult {
  // Stage A — In-Sample
  const isSharpOk  = isMetrics.sharpeRatio   >= 1.5;
  const isDdOk     = isMetrics.maxDrawdownPct >= -15;
  const isPfOk     = isMetrics.profitFactor   >= 1.4;
  const isSampleOk = isMetrics.nTrades        >= 200;
  const stageAPass = isSharpOk && isDdOk && isPfOk && isSampleOk;

  // Stage B — Walk-Forward / OOS
  const oosSharpOk  = oosMetrics.sharpeRatio   >= 1.0;
  const wfEffOk     = wfEfficiency              >= 0.60;
  const mcDdOk      = mcDd95th                 >= -25;   // negative = drawdown
  const oosSampleOk = oosMetrics.nTrades        >= 50;
  const stageBPass  = oosSharpOk && wfEffOk && mcDdOk && oosSampleOk;

  return { isSharpOk, isDdOk, isPfOk, isSampleOk, stageAPass,
           oosSharpOk, wfEffOk, mcDdOk, oosSampleOk, stageBPass };
}

export function printReport(m: PerformanceMetrics, label = 'Backtest'): void {
  const line = '═'.repeat(55);
  console.log(`\n${line}`);
  console.log(`  ${label}`);
  console.log(line);
  console.log(`  Trades:           ${m.nTrades} (${m.tradesPerMonth}/mo)  Period: ${m.testPeriodDays}d`);
  console.log(`  Total P&L:        $${m.totalPnlUsd.toLocaleString()} (${m.totalReturnPct}%)  Ann: ${m.annualizedReturnPct}%`);
  console.log(`  Win Rate:         ${(m.winRate * 100).toFixed(1)}%  Payoff: ${m.payoffRatio}  PF: ${m.profitFactor}`);
  console.log(`  Avg Win/Loss:     $${m.avgWinUsd} / $${m.avgLossUsd}  Exp: $${m.expectancyUsd}`);
  console.log(`  Max DD:           ${m.maxDrawdownPct}%  DD Dur: ${m.maxDdDurationDays}d  Consec Loss: ${m.maxConsecLosses}`);
  console.log(`  Sharpe:           ${m.sharpeRatio}  Sortino: ${m.sortinoRatio}  Calmar: ${m.calmarRatio}`);
  console.log(`  Monthly+:         ${m.monthlyPositivePct}%`);
  console.log(line);
}

export function saveResults(
  metrics: PerformanceMetrics,
  trades:  Trade[],
  label:   string,
): void {
  const ts   = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const file = `backtests/BT-${label}_${ts}.json`;
  writeFileSync(file, JSON.stringify({ metrics, trades }, null, 2));
  console.log(`Results saved: ${file}`);
}

// ── Helpers ──────────────────────────────────────────────────────────────────

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

function emptyMetrics(): PerformanceMetrics {
  return {
    nTrades: 0, tradesPerMonth: 0, testPeriodDays: 0,
    totalPnlUsd: 0, totalReturnPct: 0, annualizedReturnPct: 0,
    winRate: 0, avgWinUsd: 0, avgLossUsd: 0, payoffRatio: 0,
    profitFactor: 0, expectancyUsd: 0, maxDrawdownPct: 0,
    maxDdDurationDays: 0, sharpeRatio: 0, sortinoRatio: 0, calmarRatio: 0,
    maxConsecWins: 0, maxConsecLosses: 0, monthlyPositivePct: 0,
  };
}
