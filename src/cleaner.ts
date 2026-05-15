import type { Bar } from './types.js';

export interface CleaningAudit {
  removedDuplicates: number;
  removedSundayBars: number;
  removedZeroVolume: number;
  quarantinedOHLC: number;    // rows with OHLC violations — quarantined, not silently fixed
  totalRemoved: number;
  outputRows: number;
}

export interface CleaningResult {
  bars: Bar[];
  audit: CleaningAudit;
}

/**
 * Clean OHLCV data.
 * - Removes duplicates (keeps last occurrence)
 * - Removes Sunday partial bars
 * - Removes zero-volume bars during active session
 * - QUARANTINES (removes with audit trail) rows with OHLC violations
 *
 * DOES NOT silently fix or clamp bad data — fixing market data without logging
 * masks source problems. The audit tells you what was removed.
 */
export function cleanOHLCV(bars: Bar[]): CleaningResult {
  const audit: CleaningAudit = {
    removedDuplicates: 0,
    removedSundayBars: 0,
    removedZeroVolume: 0,
    quarantinedOHLC:   0,
    totalRemoved:      0,
    outputRows:        0,
  };

  // 1. Remove duplicates — keep last occurrence (latest data from broker is more reliable)
  const seen   = new Map<number, Bar>();
  let dupeCount = 0;
  for (const b of bars) {
    if (seen.has(b.datetime.getTime())) dupeCount++;
    seen.set(b.datetime.getTime(), b);
  }
  audit.removedDuplicates = dupeCount;
  let clean = [...seen.values()].sort((a, b) => a.datetime.getTime() - b.datetime.getTime());

  // 2. Remove Sunday bars (MT5 often generates partial candles for Sunday open)
  const beforeSunday = clean.length;
  clean = clean.filter(b => b.datetime.getUTCDay() !== 0);
  audit.removedSundayBars = beforeSunday - clean.length;

  // 3. Quarantine OHLC violations — remove with audit trail, do NOT fix silently
  const beforeOHLC  = clean.length;
  clean = clean.filter(b => {
    const valid = b.high >= b.open && b.high >= b.close && b.low <= b.open && b.low <= b.close;
    return valid;
  });
  audit.quarantinedOHLC = beforeOHLC - clean.length;

  // 4. Remove zero-volume bars during active session (07:00–21:00 UTC, Mon–Fri)
  const beforeZeroVol = clean.length;
  clean = clean.filter(b => {
    const dow  = b.datetime.getUTCDay();
    const hour = b.datetime.getUTCHours();
    const activeSession = dow >= 1 && dow <= 5 && hour >= 7 && hour < 21;
    return !(activeSession && b.tickVolume === 0);
  });
  audit.removedZeroVolume = beforeZeroVol - clean.length;

  audit.totalRemoved = bars.length - clean.length;
  audit.outputRows   = clean.length;

  return { bars: clean, audit };
}
