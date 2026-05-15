import type { BarWithFeatures } from './types.js';

/** Pass only when ATR is within a tradeable volatility band. */
export function volatilityFilter(bar: BarWithFeatures, minAtr = 5.0, maxAtr = 40.0): boolean {
  return bar.atr14 >= minAtr && bar.atr14 <= maxAtr;
}

/** Pass only when the bar belongs to an allowed session. */
export function sessionFilter(bar: BarWithFeatures, allowedSessions: string[]): boolean {
  return allowedSessions.includes(bar.session);
}

/** Pass only when current time is safe from high-impact news (±blackoutMinutes). */
export function newsFilter(datetime: Date, eventTimes: Date[], blackoutMinutes = 30): boolean {
  const ms = blackoutMinutes * 60_000;
  return !eventTimes.some(e => Math.abs(datetime.getTime() - e.getTime()) <= ms);
}

/**
 * Pass only during weekday active hours.
 * Blocks: Fri after 18:00 UTC, full weekend, Mon before 02:00 UTC (gap zone).
 */
export function weekendFilter(datetime: Date): boolean {
  const dow  = datetime.getUTCDay();   // 0=Sun, 6=Sat
  const hour = datetime.getUTCHours();
  if (dow === 5 && hour >= 18) return false;
  if (dow === 6 || dow === 0)  return false;
  if (dow === 1 && hour < 2)   return false;
  return true;
}

/**
 * Choppiness Index: < 38.2 = trending, > 61.8 = choppy.
 * Returns 50 if insufficient data.
 */
export function choppinessIndex(bars: BarWithFeatures[], idx: number, period = 14): number {
  if (idx < period) return 50;
  const slice   = bars.slice(idx - period, idx);
  const atrSum  = slice.reduce((s, b) => s + b.atr14, 0);
  const highMax = Math.max(...slice.map(b => b.high));
  const lowMin  = Math.min(...slice.map(b => b.low));
  const range   = highMax - lowMin;
  if (range === 0) return 61.8;
  return (100 * Math.log10(atrSum / range)) / Math.log10(period);
}
