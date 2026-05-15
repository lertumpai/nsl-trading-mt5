import type { Bar } from './types.js';

/**
 * EMA crossover bias.
 * IMPORTANT: always pass the index of the LAST CLOSED bar on the HTF array.
 * Use `findLastClosedHTFIndex()` to get that index safely.
 */
export function emaBias(bars: Bar[], idx: number, fastPeriod: number, slowPeriod: number): 1 | -1 {
  return ema(bars, idx, fastPeriod) >= ema(bars, idx, slowPeriod) ? 1 : -1;
}

/** EMA at bar `idx` using standard exponential smoothing (k = 2/(n+1)). */
export function ema(bars: Bar[], idx: number, period: number): number {
  if (idx < 0) return bars[0]!.close;
  const k = 2 / (period + 1);
  let value = bars[0]!.close;
  for (let i = 1; i <= Math.min(idx, bars.length - 1); i++) {
    value = bars[i]!.close * k + value * (1 - k);
  }
  return value;
}

/** ADX approximation — > 25 indicates a trending environment. */
export function adx(bars: Bar[], idx: number, period = 14): number {
  const start = Math.max(1, idx - period * 2 + 1);
  let plusDM = 0, minusDM = 0, trSum = 0;

  for (let i = start; i <= idx; i++) {
    const curr   = bars[i]!;
    const prev   = bars[i - 1]!;
    const upMove = curr.high - prev.high;
    const dnMove = prev.low  - curr.low;
    plusDM  += (upMove > dnMove  && upMove  > 0) ? upMove  : 0;
    minusDM += (dnMove  > upMove && dnMove  > 0) ? dnMove  : 0;
    trSum   += Math.max(curr.high - curr.low, Math.abs(curr.high - prev.close), Math.abs(curr.low - prev.close));
  }
  if (trSum === 0) return 0;
  const diPlus  = (plusDM  / trSum) * 100;
  const diMinus = (minusDM / trSum) * 100;
  const diSum   = diPlus + diMinus;
  return diSum === 0 ? 0 : Math.abs(diPlus - diMinus) / diSum * 100;
}

/**
 * Find the index of the last fully-closed HTF bar whose open time is
 * strictly before `ltfBarDatetime`.
 *
 * Use this instead of findIndex() to avoid selecting a future or currently-open bar.
 * Returns -1 if no such bar exists.
 */
export function findLastClosedHTFIndex(htfBars: Bar[], ltfBarDatetime: Date): number {
  const t = ltfBarDatetime.getTime();
  for (let i = htfBars.length - 1; i >= 0; i--) {
    if (htfBars[i]!.datetime.getTime() < t) return i;
  }
  return -1;
}
