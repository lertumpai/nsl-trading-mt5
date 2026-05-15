import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import {
  calculateLotSize,
  checkCircuitBreakers,
  validateStopLoss,
  validateRR,
  type RiskState,
  DEFAULT_THRESHOLDS,
} from '../src/riskManagement.js';

describe('calculateLotSize', () => {
  test('returns correct lot for standard inputs', () => {
    // balance=$10000, risk=1%, entry=2350, stop=2335 → risk=$15, raw=100/1500=0.0667 → 0.06
    const lot = calculateLotSize(10_000, 0.01, 2350, 2335);
    assert.equal(lot, 0.06);
  });

  test('returns 0 when computed lot is below minLot — does NOT clamp up', () => {
    // Very tight stop: entry=2000, stop=1999.90 → risk=0.10, raw=100/(0.1*100)=10 → fine
    // Extremely tight stop: entry=2000, stop=1999.99 → risk=0.01, raw=100/1=100... too wide
    // Let's create a scenario where raw lot < 0.01:
    // balance=$500, risk=0.5%=$2.50, priceRisk=50 → raw=2.50/(50*100)=0.0005 → stepped=0 → return 0
    const lot = calculateLotSize(500, 0.005, 2000, 1950);
    assert.equal(lot, 0);
  });

  test('rounds DOWN to lot step — never violates risk cap', () => {
    // balance=$10000, risk=1%=$100, priceRisk=3 → raw=100/300=0.3333 → stepped=0.33
    const lot = calculateLotSize(10_000, 0.01, 2000, 1997);
    assert.equal(lot, 0.33);
  });

  test('caps at maxLot', () => {
    // balance=$10000000, risk=1%=$100000, priceRisk=5 → raw=100000/500=200 → capped at 10
    const lot = calculateLotSize(10_000_000, 0.01, 2000, 1995);
    assert.equal(lot, 10.0);
  });

  test('returns 0 when priceRisk is zero', () => {
    assert.equal(calculateLotSize(10_000, 0.01, 2000, 2000), 0);
  });

  test('entry == stop (priceRisk zero) returns 0', () => {
    assert.equal(calculateLotSize(10_000, 0.01, 1900, 1900), 0);
  });
});

describe('checkCircuitBreakers', () => {
  function makeState(overrides: Partial<RiskState> = {}): RiskState {
    return {
      peakEquity:        10_000,
      dayStartBalance:   10_000,
      weekStartBalance:  10_000,
      consecutiveLosses: 0,
      ...overrides,
    };
  }

  test('allows trading at starting equity', () => {
    const result = checkCircuitBreakers(10_000, makeState());
    assert.equal(result.canTrade, true);
    assert.equal(result.lotFactor, 1.0);
  });

  test('blocks on daily loss limit (≥2%)', () => {
    const state = makeState({ dayStartBalance: 10_000 });
    const result = checkCircuitBreakers(9_790, state);  // 2.1% daily loss
    assert.equal(result.canTrade, false);
    assert.ok(result.reason?.includes('Daily loss'));
  });

  test('does NOT block just below daily loss limit (<2%)', () => {
    const state = makeState({ dayStartBalance: 10_000 });
    const result = checkCircuitBreakers(9_810, state);  // 1.9% daily loss
    assert.equal(result.canTrade, true);
  });

  test('blocks on weekly loss limit (≥5%)', () => {
    const state = makeState({ weekStartBalance: 10_000, dayStartBalance: 9_450 });
    const result = checkCircuitBreakers(9_450, state);  // 5.5% weekly loss
    assert.equal(result.canTrade, false);
    assert.ok(result.reason?.includes('Weekly loss'));
  });

  test('blocks on max drawdown pause (≥10%)', () => {
    const state = makeState({ peakEquity: 10_000, dayStartBalance: 9_000, weekStartBalance: 9_000 });
    const result = checkCircuitBreakers(8_900, state);  // 11% drawdown from peak
    assert.equal(result.canTrade, false);
    assert.ok(result.reason?.includes('Pause'));
  });

  test('triggers kill-switch on ≥15% drawdown', () => {
    const state = makeState({ peakEquity: 10_000, dayStartBalance: 8_400, weekStartBalance: 8_400 });
    const result = checkCircuitBreakers(8_400, state);  // 16% drawdown
    assert.equal(result.canTrade, false);
    assert.ok(result.reason?.includes('Kill-switch'));
  });

  test('lotFactor = 0.75 between 5–10% drawdown', () => {
    const state = makeState({ peakEquity: 10_000, dayStartBalance: 9_400, weekStartBalance: 9_400 });
    const result = checkCircuitBreakers(9_400, state);  // 6% drawdown
    assert.equal(result.canTrade, true);
    assert.equal(result.lotFactor, 0.75);
  });

  test('consecutive losses ≥5 halves the lotFactor', () => {
    const state = makeState({ consecutiveLosses: 5 });
    const result = checkCircuitBreakers(10_000, state);
    assert.equal(result.canTrade, true);
    assert.equal(result.lotFactor, 0.5);   // 1.0 * 0.5
  });

  test('updates peakEquity when current equity is higher', () => {
    const state = makeState({ peakEquity: 10_000 });
    checkCircuitBreakers(11_000, state);
    assert.equal(state.peakEquity, 11_000);
  });
});

describe('validateStopLoss', () => {
  test('valid long stop below entry within ATR range', () => {
    const result = validateStopLoss(2000, 1985, 1, 10); // risk=15, 1.5× ATR
    assert.equal(result.valid, true);
    assert.equal(result.reason, 'ok');
  });

  test('wrong_side when long stop is above entry', () => {
    const result = validateStopLoss(2000, 2010, 1, 10);
    assert.equal(result.valid, false);
    assert.equal(result.reason, 'wrong_side');
  });

  test('too_tight when stop is within 1× ATR', () => {
    const result = validateStopLoss(2000, 1997, 1, 10); // risk=3, 0.3× ATR
    assert.equal(result.valid, false);
    assert.equal(result.reason, 'too_tight');
  });

  test('too_wide when stop exceeds 4× ATR', () => {
    const result = validateStopLoss(2000, 1950, 1, 10); // risk=50, 5× ATR
    assert.equal(result.valid, false);
    assert.equal(result.reason, 'too_wide');
  });
});

describe('validateRR', () => {
  test('valid long trade with 2:1 RR', () => {
    assert.equal(validateRR(2000, 1990, 2020, 1, 1.5), true);  // risk=10, reward=20
  });

  test('rejects long trade when target is below entry', () => {
    assert.equal(validateRR(2000, 1990, 1995, 1, 1.5), false);
  });

  test('rejects when RR is below minimum', () => {
    assert.equal(validateRR(2000, 1990, 2005, 1, 1.5), false); // RR=0.5
  });

  test('valid short trade', () => {
    assert.equal(validateRR(2000, 2010, 1975, -1, 1.5), true); // risk=10, reward=25
  });
});
