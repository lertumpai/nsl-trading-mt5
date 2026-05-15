# Skill: Data Analysis & Validation

> Use this skill when loading, validating, cleaning, or exploring XAUUSD price data.
> Language: **TypeScript** (Node.js or Bun runtime).

---

## Project Setup

```bash
# Recommended: Bun (runs TypeScript natively, no compile step)
bun init
bun add csv-parse simple-statistics

# Or Node.js
npm init -y
npm install csv-parse simple-statistics
npm install --save-dev typescript tsx @types/node
```

`tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "outDir": "dist"
  }
}
```

---

## Core Types (`src/types.ts`)

```typescript
export interface Bar {
  datetime: Date;
  open: number;
  high: number;
  low: number;
  close: number;
  tickVolume: number;
}

export type Session = 'asian' | 'london' | 'new_york' | 'overlap';
export type VolRegime = 'low' | 'medium' | 'high';

export interface BarWithFeatures extends Bar {
  atr14: number;
  atr20: number;
  logReturn: number;
  session: Session;
  volRegime: VolRegime;
  dayOfWeek: number; // 0=Mon, 4=Fri
}
```

---

## Step 1: Load Data (`src/dataLoader.ts`)

```typescript
import { createReadStream } from 'fs';
import { parse } from 'csv-parse';
import type { Bar } from './types.js';

/** Load XAUUSD CSV exported from MT5.
 *  Handles both MT5 History Center format and MetaTrader5 Python lib format.
 */
export async function loadOHLCV(filePath: string): Promise<Bar[]> {
  return new Promise((resolve, reject) => {
    const bars: Bar[] = [];

    createReadStream(filePath)
      .pipe(parse({ columns: true, trim: true }))
      .on('data', (row: Record<string, string>) => {
        // Normalize column names across MT5 export formats
        const dt = row['datetime'] ?? row['time'] ?? `${row['<DATE>']} ${row['<TIME>']}`;
        const open  = parseFloat(row['open']  ?? row['<OPEN>']);
        const high  = parseFloat(row['high']  ?? row['<HIGH>']);
        const low   = parseFloat(row['low']   ?? row['<LOW>']);
        const close = parseFloat(row['close'] ?? row['<CLOSE>']);
        const vol   = parseFloat(row['tick_volume'] ?? row['<TICKVOL>'] ?? row['<VOL>'] ?? '0');

        if (isNaN(open) || isNaN(close)) return; // skip bad rows

        bars.push({
          datetime: new Date(dt + (dt.includes('Z') ? '' : 'Z')), // force UTC
          open, high, low, close,
          tickVolume: vol,
        });
      })
      .on('end', () => resolve(bars.sort((a, b) => a.datetime.getTime() - b.datetime.getTime())))
      .on('error', reject);
  });
}
```

---

## Step 2: Validate Data (`src/validator.ts`)

```typescript
import type { Bar } from './types.js';

export interface ValidationReport {
  rows: number;
  dateRange: string;
  issues: string[];
  quality: 'PASS' | 'FAIL';
}

export function validateOHLCV(bars: Bar[], timeframeMinutes: number): ValidationReport {
  const issues: string[] = [];

  // Duplicate timestamps
  const times = bars.map(b => b.datetime.getTime());
  const dupes = times.length - new Set(times).size;
  if (dupes > 0) issues.push(`DUPLICATE ROWS: ${dupes}`);

  // OHLC violations
  const violations = bars.filter(
    b => b.high < b.open || b.high < b.close || b.low > b.open || b.low > b.close
  ).length;
  if (violations > 0) issues.push(`OHLC VIOLATIONS: ${violations}`);

  // Price sanity (XAUUSD should be between 500–5000 USD)
  const outOfRange = bars.filter(b => b.close < 500 || b.close > 5000).length;
  if (outOfRange > 0) issues.push(`PRICE OUT OF RANGE: ${outOfRange} rows`);

  // Zero volume during active session (07:00–21:00 UTC on weekdays)
  const zeroVol = bars.filter(b => {
    const h = b.datetime.getUTCHours();
    const dow = b.datetime.getUTCDay(); // 0=Sun, 6=Sat
    const weekday = dow >= 1 && dow <= 5;
    const activeSession = h >= 7 && h < 21;
    return weekday && activeSession && b.tickVolume === 0;
  }).length;
  if (zeroVol > 0) issues.push(`ZERO VOLUME BARS: ${zeroVol}`);

  // Gap detection — count expected bars vs actual during active hours
  const tfMs = timeframeMinutes * 60 * 1000;
  let gapCount = 0;
  for (let i = 1; i < bars.length; i++) {
    const gap = bars[i].datetime.getTime() - bars[i - 1].datetime.getTime();
    const h = bars[i].datetime.getUTCHours();
    const dow = bars[i].datetime.getUTCDay();
    if (dow >= 1 && dow <= 5 && h >= 7 && h < 21 && gap > tfMs * 2) {
      gapCount++;
    }
  }
  if (gapCount > bars.length * 0.01) issues.push(`DATA GAPS: ${gapCount} gaps detected`);

  return {
    rows: bars.length,
    dateRange: `${bars[0]?.datetime.toISOString()} — ${bars.at(-1)?.datetime.toISOString()}`,
    issues,
    quality: issues.length === 0 ? 'PASS' : 'FAIL',
  };
}
```

---

## Step 3: Clean Data (`src/cleaner.ts`)

```typescript
import type { Bar } from './types.js';

export function cleanOHLCV(bars: Bar[]): Bar[] {
  // Remove duplicates (keep last)
  const seen = new Map<number, Bar>();
  for (const b of bars) seen.set(b.datetime.getTime(), b);
  let clean = [...seen.values()].sort((a, b) => a.datetime.getTime() - b.datetime.getTime());

  // Remove Sunday candles (partial/gap bars from broker weekend)
  clean = clean.filter(b => b.datetime.getUTCDay() !== 0);

  // Remove zero-volume bars during active session
  clean = clean.filter(b => {
    const h = b.datetime.getUTCHours();
    const active = h >= 7 && h < 21;
    return !(active && b.tickVolume === 0);
  });

  // Fix OHLC violations (rare data errors — clamp high/low)
  return clean.map(b => ({
    ...b,
    high: Math.max(b.open, b.high, b.low, b.close),
    low:  Math.min(b.open, b.high, b.low, b.close),
  }));
}
```

---

## Step 4: Feature Engineering (`src/features.ts`)

```typescript
import type { Bar, BarWithFeatures, Session, VolRegime } from './types.js';

export function addFeatures(bars: Bar[]): BarWithFeatures[] {
  const result: BarWithFeatures[] = [];

  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    const atr14 = calcATR(bars, i, 14);
    const atr20 = calcATR(bars, i, 20);
    const prevClose = i > 0 ? bars[i - 1].close : b.close;
    const logReturn = Math.log(b.close / prevClose);

    result.push({
      ...b,
      atr14,
      atr20,
      logReturn,
      session: getSession(b.datetime),
      volRegime: getVolRegime(result, i),
      dayOfWeek: (b.datetime.getUTCDay() + 6) % 7, // 0=Mon, 4=Fri
    });
  }

  return result;
}

function calcATR(bars: Bar[], idx: number, period: number): number {
  if (idx === 0) return bars[0].high - bars[0].low;
  const trs: number[] = [];
  for (let i = Math.max(1, idx - period + 1); i <= idx; i++) {
    const tr = Math.max(
      bars[i].high - bars[i].low,
      Math.abs(bars[i].high - bars[i - 1].close),
      Math.abs(bars[i].low  - bars[i - 1].close),
    );
    trs.push(tr);
  }
  return trs.reduce((a, b) => a + b, 0) / trs.length;
}

function getSession(dt: Date): Session {
  const h = dt.getUTCHours();
  if (h >= 13 && h < 16) return 'overlap';
  if (h >= 13 && h < 21) return 'new_york';
  if (h >= 8  && h < 16) return 'london';
  return 'asian';
}

function getVolRegime(bars: BarWithFeatures[], idx: number): VolRegime {
  const window = 20;
  if (idx < window) return 'medium';
  const recent = bars.slice(idx - window, idx).map(b => b.logReturn);
  const mean = recent.reduce((a, b) => a + b, 0) / recent.length;
  const std = Math.sqrt(recent.map(r => (r - mean) ** 2).reduce((a, b) => a + b, 0) / recent.length);
  const annualizedVol = std * Math.sqrt(252);
  if (annualizedVol < 0.12) return 'low';
  if (annualizedVol < 0.20) return 'medium';
  return 'high';
}
```

---

## Step 5: EDA Utilities (`src/eda.ts`)

```typescript
import type { BarWithFeatures } from './types.js';

/** Session stats: avg range, bull%, avg volume. */
export function sessionAnalysis(bars: BarWithFeatures[]) {
  const sessions = ['asian', 'london', 'new_york', 'overlap'] as const;
  return sessions.map(session => {
    const subset = bars.filter(b => b.session === session);
    if (subset.length === 0) return { session, count: 0 };
    const avgRange = subset.reduce((s, b) => s + (b.high - b.low), 0) / subset.length;
    const bullPct  = subset.filter(b => b.close > b.open).length / subset.length;
    const avgVol   = subset.reduce((s, b) => s + b.tickVolume, 0) / subset.length;
    return { session, count: subset.length, avgRange: +avgRange.toFixed(3), bullPct: +bullPct.toFixed(3), avgVol: +avgVol.toFixed(0) };
  });
}

/** Return distribution stats — check for fat tails. */
export function returnStats(bars: BarWithFeatures[]) {
  const returns = bars.map(b => b.logReturn).filter(r => isFinite(r));
  const n = returns.length;
  const mean = returns.reduce((a, b) => a + b, 0) / n;
  const variance = returns.map(r => (r - mean) ** 2).reduce((a, b) => a + b, 0) / n;
  const std = Math.sqrt(variance);
  const skewness = returns.map(r => ((r - mean) / std) ** 3).reduce((a, b) => a + b, 0) / n;
  const kurtosis = returns.map(r => ((r - mean) / std) ** 4).reduce((a, b) => a + b, 0) / n - 3;

  return { n, mean: +mean.toFixed(6), std: +std.toFixed(6), skewness: +skewness.toFixed(3), kurtosis: +kurtosis.toFixed(3) };
}

/** Day-of-week return bias. */
export function dowBias(bars: BarWithFeatures[]) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];
  return days.map((day, i) => {
    const subset = bars.filter(b => b.dayOfWeek === i);
    const avg = subset.reduce((s, b) => s + b.logReturn, 0) / (subset.length || 1);
    return { day, count: subset.length, avgReturn: +(avg * 100).toFixed(4) };
  });
}
```

---

## Data Storage Convention

- Raw CSV: `data/raw/XAUUSD/XAUUSD_{TF}_{start}_{end}.csv`
- Cleaned JSON: `data/processed/XAUUSD_{TF}_clean.json`
- Feature JSON: `data/processed/XAUUSD_{TF}_features.json`

Save processed data as JSON (preserves types, fast to parse with `JSON.parse`).

```typescript
import { writeFileSync } from 'fs';
writeFileSync('data/processed/XAUUSD_H1_features.json', JSON.stringify(bars));
```

---

## XAUUSD-Specific Warnings

- **Spread spikes on news:** XAUUSD spread widens to 5–10 USD during FOMC/NFP. Filter or model these bars.
- **Monday gap:** XAUUSD often gaps on Monday open. First bar of the week needs special handling.
- **MT5 tick volume ≠ real volume.** It counts price ticks. Use as a relative proxy only.
- **Rollover/Swap:** Positions held past 22:00 UTC incur swap. Factor into multi-day trade P&L.
- **Dates are UTC in all code.** MT5 server time may differ — always normalize to UTC on load.
