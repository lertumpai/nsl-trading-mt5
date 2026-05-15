import type { Bar } from './types.js';

export interface ValidationReport {
  rows: number;
  dateRange: string;
  detectedTimeframeMinutes: number;
  issues: string[];
  quality: 'PASS' | 'FAIL';
}

/**
 * Validate an OHLCV array for common data problems.
 * Does NOT modify the data — call cleanOHLCV() separately.
 */
export function validateOHLCV(bars: Bar[]): ValidationReport {
  if (bars.length === 0) {
    return { rows: 0, dateRange: 'empty', detectedTimeframeMinutes: 0, issues: ['EMPTY DATASET'], quality: 'FAIL' };
  }

  const issues: string[] = [];

  // ---- Duplicate timestamps ----
  const times = bars.map(b => b.datetime.getTime());
  const dupes  = times.length - new Set(times).size;
  if (dupes > 0) issues.push(`DUPLICATE TIMESTAMPS: ${dupes}`);

  // ---- OHLC violations ----
  const ohlcViolations = bars.filter(
    b => b.high < b.open || b.high < b.close || b.low > b.open || b.low > b.close
  );
  if (ohlcViolations.length > 0)
    issues.push(`OHLC VIOLATIONS: ${ohlcViolations.length} rows (first: ${ohlcViolations[0]!.datetime.toISOString()})`);

  // ---- Price sanity (XAUUSD must be 500–5000 USD) ----
  const outOfRange = bars.filter(b => b.close < 500 || b.close > 5_000);
  if (outOfRange.length > 0)
    issues.push(`PRICE OUT OF RANGE: ${outOfRange.length} rows outside 500–5000`);

  // ---- Invalid/NaN prices ----
  const nanRows = bars.filter(b => !isFinite(b.open) || !isFinite(b.high) || !isFinite(b.low) || !isFinite(b.close));
  if (nanRows.length > 0)
    issues.push(`NON-FINITE PRICES: ${nanRows.length} rows`);

  // ---- Detect timeframe from median gap ----
  const tfMinutes = detectTimeframeMinutes(bars);

  // ---- Gap detection during active session hours (07:00–21:00 UTC, Mon–Fri) ----
  const tfMs      = tfMinutes * 60_000;
  let gapCount    = 0;
  for (let i = 1; i < bars.length; i++) {
    const gap = bars[i]!.datetime.getTime() - bars[i - 1]!.datetime.getTime();
    const utcDay  = bars[i]!.datetime.getUTCDay();  // 0=Sun, 6=Sat
    const utcHour = bars[i]!.datetime.getUTCHours();
    const inSession = utcDay >= 1 && utcDay <= 5 && utcHour >= 7 && utcHour < 21;
    if (inSession && gap > tfMs * 2) gapCount++;
  }
  const gapPct = (gapCount / bars.length) * 100;
  if (gapPct > 1.0)
    issues.push(`DATA GAPS: ${gapCount} gaps (${gapPct.toFixed(1)}% of session bars)`);

  // ---- Zero volume during active session ----
  const zeroVol = bars.filter(b => {
    const dow  = b.datetime.getUTCDay();
    const hour = b.datetime.getUTCHours();
    return dow >= 1 && dow <= 5 && hour >= 7 && hour < 21 && b.tickVolume === 0;
  }).length;
  if (zeroVol > 0) issues.push(`ZERO VOLUME (active session): ${zeroVol} bars`);

  return {
    rows: bars.length,
    dateRange: `${bars[0]!.datetime.toISOString()} — ${bars.at(-1)!.datetime.toISOString()}`,
    detectedTimeframeMinutes: tfMinutes,
    issues,
    quality: issues.length === 0 ? 'PASS' : 'FAIL',
  };
}

/** Infer timeframe from the most common gap between consecutive bars. */
export function detectTimeframeMinutes(bars: Bar[]): number {
  if (bars.length < 2) return 0;

  const gapCounts = new Map<number, number>();
  for (let i = 1; i < bars.length; i++) {
    const gapMin = Math.round(
      (bars[i]!.datetime.getTime() - bars[i - 1]!.datetime.getTime()) / 60_000
    );
    gapCounts.set(gapMin, (gapCounts.get(gapMin) ?? 0) + 1);
  }

  let modeGap  = 1;
  let modeCount = 0;
  for (const [gap, count] of gapCounts) {
    if (count > modeCount) { modeCount = count; modeGap = gap; }
  }
  return modeGap;
}
