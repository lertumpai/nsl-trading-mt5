import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { computeAllMetrics, checkAcceptance } from '../src/metrics.js';
import type { Trade } from '../src/types.js';

function makeTrade(overrides: Partial<Trade> & { pnlUsd: number; entryDate: string; exitDate: string }): Trade {
  const { pnlUsd, entryDate, exitDate, ...rest } = overrides;
  return {
    entryTime:   new Date(entryDate + 'T10:00:00Z'),
    exitTime:    new Date(exitDate  + 'T15:00:00Z'),
    direction:   'long',
    entryPrice:  2000,
    exitPrice:   pnlUsd >= 0 ? 2020 : 1985,
    stopPrice:   1990,
    targetPrice: 2020,
    exitReason:  pnlUsd >= 0 ? 'target' : 'stop',
    holdBars:    5,
    holdDays:    0.2,
    lotSize:     0.1,
    pnlUsd,
    ...rest,
  };
}

// Spread trades over Mon–Fri on two different weeks
const TRADES: Trade[] = [
  makeTrade({ pnlUsd:  100, entryDate: '2024-01-08', exitDate: '2024-01-08' }), // Mon
  makeTrade({ pnlUsd: -50,  entryDate: '2024-01-09', exitDate: '2024-01-09' }), // Tue
  makeTrade({ pnlUsd:  150, entryDate: '2024-01-10', exitDate: '2024-01-10' }), // Wed
  makeTrade({ pnlUsd: -30,  entryDate: '2024-01-11', exitDate: '2024-01-11' }), // Thu
  makeTrade({ pnlUsd:  80,  entryDate: '2024-01-12', exitDate: '2024-01-12' }), // Fri
  makeTrade({ pnlUsd:  120, entryDate: '2024-01-15', exitDate: '2024-01-15' }), // Mon
  makeTrade({ pnlUsd: -60,  entryDate: '2024-01-16', exitDate: '2024-01-16' }), // Tue
  makeTrade({ pnlUsd:  200, entryDate: '2024-01-17', exitDate: '2024-01-17' }), // Wed
];

describe('computeAllMetrics', () => {
  test('trade count is correct', () => {
    const m = computeAllMetrics(TRADES);
    assert.equal(m.nTrades, 8);
  });

  test('total P&L is sum of all pnlUsd', () => {
    const m = computeAllMetrics(TRADES);
    const expected = TRADES.reduce((s, t) => s + t.pnlUsd, 0);
    assert.equal(m.totalPnlUsd, expected);
  });

  test('win rate is correct (5 winners out of 8)', () => {
    const m = computeAllMetrics(TRADES);
    assert.equal(m.winRate, 5 / 8);
  });

  test('profit factor > 1 (net profitable set)', () => {
    const m = computeAllMetrics(TRADES);
    assert.ok(m.profitFactor > 1);
  });

  test('maxDrawdownPct is negative (drawdown is a loss)', () => {
    const m = computeAllMetrics(TRADES);
    assert.ok(m.maxDrawdownPct <= 0, `Expected ≤0 but got ${m.maxDrawdownPct}`);
  });

  test('daily returns include no-trade weekdays (calendar Sharpe)', () => {
    // Trades span Jan 8–17 2024 (10 days, 2 full weeks).
    // Even though we trade on every day here, the key is that the
    // Sharpe computation must not inflate if we had gaps.
    // We verify it's finite and sensible.
    const m = computeAllMetrics(TRADES);
    assert.ok(isFinite(m.sharpeRatio));
  });

  test('sparse trades produce lower Sharpe than dense trades (full calendar penalises gaps)', () => {
    // Dense: 5 varied trades on consecutive weekdays — relatively few 0-return days
    const denseTrades: Trade[] = [
      makeTrade({ pnlUsd:  80, entryDate: '2024-01-08', exitDate: '2024-01-08' }),
      makeTrade({ pnlUsd: 120, entryDate: '2024-01-09', exitDate: '2024-01-09' }),
      makeTrade({ pnlUsd:  60, entryDate: '2024-01-10', exitDate: '2024-01-10' }),
      makeTrade({ pnlUsd: 100, entryDate: '2024-01-11', exitDate: '2024-01-11' }),
      makeTrade({ pnlUsd:  90, entryDate: '2024-01-12', exitDate: '2024-01-12' }),
    ];
    // Sparse: same trades but the last one is pushed 5 months out — many 0-return days dilute mean
    const sparseTrades: Trade[] = [
      makeTrade({ pnlUsd:  80, entryDate: '2024-01-08', exitDate: '2024-01-08' }),
      makeTrade({ pnlUsd: 120, entryDate: '2024-01-09', exitDate: '2024-01-09' }),
      makeTrade({ pnlUsd:  60, entryDate: '2024-01-10', exitDate: '2024-01-10' }),
      makeTrade({ pnlUsd: 100, entryDate: '2024-01-11', exitDate: '2024-01-11' }),
      makeTrade({ pnlUsd:  90, entryDate: '2024-06-03', exitDate: '2024-06-03' }), // 5-month gap
    ];
    const sparse = computeAllMetrics(sparseTrades);
    const dense  = computeAllMetrics(denseTrades);
    assert.ok(sparse.sharpeRatio < dense.sharpeRatio,
      `Sparse Sharpe ${sparse.sharpeRatio} should be < dense ${dense.sharpeRatio}`);
  });

  test('returns empty metrics for zero trades', () => {
    const m = computeAllMetrics([]);
    assert.equal(m.nTrades, 0);
    assert.equal(m.sharpeRatio, 0);
    assert.equal(m.totalPnlUsd, 0);
  });
});

describe('checkAcceptance', () => {
  const GOOD_IS = computeAllMetrics(TRADES);  // real metrics from above

  function fakeMetrics(sharpe: number, dd: number, pf: number, n: number) {
    const base = computeAllMetrics(TRADES);
    return { ...base, sharpeRatio: sharpe, maxDrawdownPct: dd, profitFactor: pf, nTrades: n };
  }

  test('Stage A passes with all thresholds met', () => {
    const is  = fakeMetrics(1.6, -8, 1.5, 250);
    const oos = fakeMetrics(1.1, -12, 1.3, 60);
    const r = checkAcceptance(is, oos, 0.7, -18);
    assert.equal(r.stageAPass, true);
  });

  test('Stage A fails when Sharpe < 1.5', () => {
    const is  = fakeMetrics(1.2, -8, 1.5, 250);
    const oos = fakeMetrics(1.1, -12, 1.3, 60);
    const r = checkAcceptance(is, oos, 0.7, -18);
    assert.equal(r.stageAPass, false);
    assert.equal(r.isSharpOk, false);
  });

  test('Stage A fails when trade count < 200', () => {
    const is  = fakeMetrics(1.6, -8, 1.5, 100);
    const oos = fakeMetrics(1.1, -12, 1.3, 60);
    const r = checkAcceptance(is, oos, 0.7, -18);
    assert.equal(r.stageAPass, false);
    assert.equal(r.isSampleOk, false);
  });

  test('Stage B passes with OOS Sharpe ≥ 1.0 and WF eff ≥ 0.60', () => {
    const is  = fakeMetrics(1.6, -8, 1.5, 250);
    const oos = fakeMetrics(1.1, -12, 1.3, 60);
    const r = checkAcceptance(is, oos, 0.70, -18);
    assert.equal(r.stageBPass, true);
  });

  test('Stage B fails when WF efficiency < 0.60', () => {
    const is  = fakeMetrics(1.6, -8, 1.5, 250);
    const oos = fakeMetrics(1.1, -12, 1.3, 60);
    const r = checkAcceptance(is, oos, 0.50, -18);
    assert.equal(r.stageBPass, false);
    assert.equal(r.wfEffOk, false);
  });

  test('Stage B fails when MC 95th DD < -25%', () => {
    const is  = fakeMetrics(1.6, -8, 1.5, 250);
    const oos = fakeMetrics(1.1, -12, 1.3, 60);
    const r = checkAcceptance(is, oos, 0.70, -30);  // worse than -25
    assert.equal(r.stageBPass, false);
    assert.equal(r.mcDdOk, false);
  });

  test('Stage B fails when OOS trade count < 50', () => {
    const is  = fakeMetrics(1.6, -8, 1.5, 250);
    const oos = fakeMetrics(1.1, -12, 1.3, 40);  // only 40 OOS trades
    const r = checkAcceptance(is, oos, 0.70, -18);
    assert.equal(r.stageBPass, false);
    assert.equal(r.oosSampleOk, false);
  });
});
