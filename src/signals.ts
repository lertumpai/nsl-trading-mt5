import type { BarWithFeatures, Direction } from './types.js';

/** Opening range breakout: long above N-bar high, short below N-bar low. */
export function openingRangeBreakout(
  bars:      BarWithFeatures[],
  idx:       number,
  openHour:  number,    // UTC hour of session open (e.g. 8 for London)
  rangeBars  = 4,
): Direction {
  if (idx < rangeBars + 1) return 0;

  // Find the N bars immediately after the most recent session open
  const sessionStarts = bars
    .slice(0, idx)
    .map((b, i) => ({ b, i }))
    .filter(({ b }) => b.datetime.getUTCHours() === openHour);

  if (sessionStarts.length === 0) return 0;

  const lastStart = sessionStarts.at(-1)!.i;
  const rangeSlice = bars.slice(lastStart, lastStart + rangeBars);
  if (rangeSlice.length < rangeBars) return 0;

  const rangeHigh = Math.max(...rangeSlice.map(b => b.high));
  const rangeLow  = Math.min(...rangeSlice.map(b => b.low));

  // Signal fires when PREVIOUS bar's close (bar[idx-1]) breaks the range —
  // entry will be on bar[idx] open (handled by the backtest engine).
  const prevClose = bars[idx - 1]!.close;
  if (prevClose > rangeHigh) return 1;
  if (prevClose < rangeLow)  return -1;
  return 0;
}

/** RSI extremes: fade overbought/oversold. */
export function rsiSignal(
  bars:       BarWithFeatures[],
  idx:        number,
  period      = 14,
  oversold    = 30,
  overbought  = 70,
): Direction {
  const r = rsi(bars, idx, period);
  if (r < oversold)   return 1;
  if (r > overbought) return -1;
  return 0;
}

export function rsi(bars: BarWithFeatures[], idx: number, period: number): number {
  if (idx < period) return 50;
  let avgGain = 0, avgLoss = 0;
  for (let i = idx - period + 1; i <= idx; i++) {
    const delta = bars[i]!.close - bars[i - 1]!.close;
    if (delta > 0) avgGain += delta; else avgLoss -= delta;
  }
  avgGain /= period;
  avgLoss /= period;
  if (avgLoss === 0) return 100;
  return 100 - (100 / (1 + avgGain / avgLoss));
}

/** Bollinger Band bounce: price touches band and the previous bar has closed back inside. */
export function bollingerBounce(
  bars:    BarWithFeatures[],
  idx:     number,
  period   = 20,
  stdDev   = 2.0,
): Direction {
  if (idx < period + 1) return 0;

  const [upper, lower] = bbBands(bars, idx - 1, period, stdDev);  // bands at previous bar
  const prevClose = bars[idx - 1]!.close;
  const currClose = bars[idx]!.close;

  // Long: prev bar closed below lower band, current bar closed back above it
  if (prevClose <= lower && currClose > lower) return 1;
  // Short: prev bar closed above upper band, current bar closed back below it
  if (prevClose >= upper && currClose < upper) return -1;
  return 0;
}

function bbBands(bars: BarWithFeatures[], idx: number, period: number, mult: number): [number, number] {
  const closes = bars.slice(idx - period + 1, idx + 1).map(b => b.close);
  const mean   = closes.reduce((a, b) => a + b, 0) / period;
  const std    = Math.sqrt(closes.map(c => (c - mean) ** 2).reduce((a, b) => a + b, 0) / period);
  return [mean + mult * std, mean - mult * std];
}

/**
 * Fair Value Gap (FVG) / imbalance.
 * Bullish FVG: bar[i-2].high < bar[i].low — price gapped up leaving an unfilled zone.
 * Bearish FVG: bar[i-2].low  > bar[i].high.
 * Signal is based on bars fully closed before `idx`.
 */
export function fairValueGap(bars: BarWithFeatures[], idx: number): Direction {
  if (idx < 2) return 0;
  if (bars[idx - 2]!.high < bars[idx]!.low)   return 1;
  if (bars[idx - 2]!.low  > bars[idx]!.high)  return -1;
  return 0;
}
