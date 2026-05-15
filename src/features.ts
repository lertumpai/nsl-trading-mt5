import type { Bar, BarWithFeatures, Session, VolRegime } from './types.js';

/** Add all derived features to a cleaned OHLCV array. */
export function addFeatures(bars: Bar[]): BarWithFeatures[] {
  const result: BarWithFeatures[] = [];

  for (let i = 0; i < bars.length; i++) {
    const b         = bars[i]!;
    const prevClose = i > 0 ? bars[i - 1]!.close : b.close;
    const logReturn = Math.log(b.close / prevClose);
    const atr14     = calcATR(bars, i, 14);
    const atr20     = calcATR(bars, i, 20);

    const withFeatures: BarWithFeatures = {
      ...b,
      atr14,
      atr20,
      logReturn,
      session:    getSession(b.datetime),
      volRegime:  getVolRegime(result, i),
      dayOfWeek:  (b.datetime.getUTCDay() + 6) % 7,  // 0=Mon, 4=Fri
    };

    result.push(withFeatures);
  }

  return result;
}

/**
 * ATR using Wilder's smoothing (matches MT5 default).
 * Returns the ATR value for bar at `idx`.
 */
export function calcATR(bars: Bar[], idx: number, period: number): number {
  if (idx === 0) return bars[0]!.high - bars[0]!.low;

  // Build True Range series from start to idx
  const trs: number[] = [];
  for (let i = 1; i <= idx; i++) {
    const curr = bars[i]!;
    const prev = bars[i - 1]!;
    trs.push(Math.max(
      curr.high - curr.low,
      Math.abs(curr.high - prev.close),
      Math.abs(curr.low  - prev.close),
    ));
  }

  if (trs.length < period) {
    return trs.reduce((s, v) => s + v, 0) / trs.length;
  }

  // Wilder's smoothing: SMA for first value, then EMA with alpha=1/period
  let atr = trs.slice(0, period).reduce((s, v) => s + v, 0) / period;
  for (let i = period; i < trs.length; i++) {
    atr = (atr * (period - 1) + trs[i]!) / period;
  }
  return atr;
}

function getSession(dt: Date): Session {
  const h = dt.getUTCHours();
  // Overlap takes priority — it's the highest-volume subset of London+NY
  if (h >= 13 && h < 16) return 'overlap';
  if (h >= 8  && h < 16) return 'london';
  if (h >= 13 && h < 21) return 'new_york';
  return 'asian';
}

/** Classify volatility regime based on rolling 20-bar annualised log-return std. */
function getVolRegime(bars: BarWithFeatures[], idx: number): VolRegime {
  const window = 20;
  if (idx < window) return 'medium';

  const returns = bars.slice(idx - window, idx).map(b => b.logReturn);
  const mean    = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.map(r => (r - mean) ** 2).reduce((a, b) => a + b, 0) / returns.length;
  const annVol  = Math.sqrt(variance) * Math.sqrt(252 * 24); // assume H1 bars

  if (annVol < 0.10) return 'low';
  if (annVol < 0.18) return 'medium';
  return 'high';
}
