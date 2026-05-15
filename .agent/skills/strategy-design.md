# Skill: Strategy Design

> Use this skill when designing a new trading strategy from a hypothesis.
> Language: **TypeScript**. Read `.agent/skills/data-analysis.md` first for type definitions.

---

## Strategy Architecture for XAUUSD

Every strategy has four layers. Design all four before writing a single backtest line.

```
Layer 1: MARKET REGIME FILTER    ← Should we be trading at all right now?
Layer 2: DIRECTIONAL BIAS (HTF)  ← What direction does the higher timeframe favor?
Layer 3: ENTRY TRIGGER (LTF)     ← What is the precise entry condition?
Layer 4: TRADE MANAGEMENT        ← Where is stop, target, and how is it managed?
```

---

## Core Types (`src/types.ts` additions)

```typescript
export type Direction = 1 | -1 | 0; // 1=long, -1=short, 0=flat

export interface Signal {
  direction: Direction;
  stopPrice: number;
  targetPrice: number;
  barIndex: number;
}

export interface StrategyParams {
  [key: string]: number | boolean | string;
}
```

---

## Layer 1: Market Regime Filters (`src/filters.ts`)

```typescript
import type { BarWithFeatures } from './types.js';

/** Skip trading in extreme volatility (news spikes) or dead chop. */
export function volatilityFilter(bar: BarWithFeatures, minAtr = 5.0, maxAtr = 40.0): boolean {
  return bar.atr14 >= minAtr && bar.atr14 <= maxAtr;
}

/** Choppiness Index < 38.2 = trending, > 61.8 = ranging/choppy. */
export function choppinessIndex(bars: BarWithFeatures[], idx: number, period = 14): number {
  if (idx < period) return 50;
  const slice = bars.slice(idx - period, idx);
  const atrSum = slice.reduce((s, b) => s + b.atr14, 0);
  const highMax = Math.max(...slice.map(b => b.high));
  const lowMin  = Math.min(...slice.map(b => b.low));
  const range = highMax - lowMin;
  if (range === 0) return 61.8;
  return (100 * Math.log10(atrSum / range)) / Math.log10(period);
}

/** Allow trading only in specified sessions. */
export function sessionFilter(bar: BarWithFeatures, allowedSessions: string[]): boolean {
  return allowedSessions.includes(bar.session);
}

/** Return true if current time is safe from high-impact news (±N minutes). */
export function newsFilter(
  datetime: Date,
  eventTimes: Date[],
  blackoutMinutes = 30,
): boolean {
  const ms = blackoutMinutes * 60_000;
  return !eventTimes.some(
    t => Math.abs(datetime.getTime() - t.getTime()) <= ms
  );
}

/** Avoid Friday after 18:00 UTC and full weekend. */
export function weekendFilter(datetime: Date): boolean {
  const dow  = datetime.getUTCDay(); // 0=Sun, 6=Sat
  const hour = datetime.getUTCHours();
  if (dow === 5 && hour >= 18) return false; // Friday close
  if (dow === 6 || dow === 0) return false;   // Weekend
  if (dow === 1 && hour < 2)  return false;   // Monday gap zone
  return true;
}
```

---

## Layer 2: Directional Bias (`src/bias.ts`)

```typescript
import type { Bar } from './types.js';

/** EMA crossover bias: +1 = bullish, -1 = bearish. */
export function emaBias(bars: Bar[], idx: number, fastPeriod: number, slowPeriod: number): 1 | -1 {
  const emaFast = ema(bars, idx, fastPeriod);
  const emaSlow = ema(bars, idx, slowPeriod);
  return emaFast >= emaSlow ? 1 : -1;
}

export function ema(bars: Bar[], idx: number, period: number): number {
  const k = 2 / (period + 1);
  let value = bars[0].close;
  for (let i = 1; i <= idx; i++) {
    value = bars[i].close * k + value * (1 - k);
  }
  return value;
}

/** ADX strength — returns value; > 25 = trending environment. */
export function adx(bars: Bar[], idx: number, period = 14): number {
  if (idx < period * 2) return 0;
  const slice = bars.slice(idx - period * 2, idx + 1);
  let plusDM = 0, minusDM = 0, tr = 0;

  for (let i = 1; i < slice.length; i++) {
    const upMove   = slice[i].high - slice[i - 1].high;
    const downMove = slice[i - 1].low - slice[i].low;
    plusDM  += (upMove > downMove && upMove > 0) ? upMove : 0;
    minusDM += (downMove > upMove && downMove > 0) ? downMove : 0;
    tr += Math.max(
      slice[i].high - slice[i].low,
      Math.abs(slice[i].high - slice[i - 1].close),
      Math.abs(slice[i].low  - slice[i - 1].close),
    );
  }
  if (tr === 0) return 0;
  const diPlus  = (plusDM / tr) * 100;
  const diMinus = (minusDM / tr) * 100;
  const dx = Math.abs(diPlus - diMinus) / (diPlus + diMinus) * 100;
  return dx;
}
```

---

## Layer 3: Entry Triggers (`src/signals.ts`)

```typescript
import type { BarWithFeatures, Direction } from './types.js';
import { ema } from './bias.js';

/** Opening range breakout — long above N-bar high, short below N-bar low. */
export function openingRangeBreakout(
  bars: BarWithFeatures[],
  idx: number,
  openHour: number,   // UTC hour of session open (e.g., 8 for London)
  rangeBars = 4,
): Direction {
  if (idx < rangeBars + 1) return 0;

  // Find last session open
  const sessionBars = bars.slice(0, idx).filter(
    b => b.datetime.getUTCHours() === openHour
  );
  if (sessionBars.length < rangeBars) return 0;

  const recent = sessionBars.slice(-rangeBars);
  const rangeHigh = Math.max(...recent.map(b => b.high));
  const rangeLow  = Math.min(...recent.map(b => b.low));
  const close = bars[idx].close;

  if (close > rangeHigh) return 1;
  if (close < rangeLow)  return -1;
  return 0;
}

/** RSI mean reversion — fade extremes. */
export function rsiSignal(
  bars: BarWithFeatures[],
  idx: number,
  period = 14,
  oversold = 30,
  overbought = 70,
): Direction {
  if (idx < period) return 0;
  const rsiVal = rsi(bars, idx, period);
  if (rsiVal < oversold)  return 1;
  if (rsiVal > overbought) return -1;
  return 0;
}

export function rsi(bars: BarWithFeatures[], idx: number, period: number): number {
  const changes = bars.slice(idx - period, idx).map((b, i, arr) =>
    i === 0 ? 0 : b.close - arr[i - 1].close
  ).slice(1);
  const gains = changes.filter(c => c > 0);
  const losses = changes.filter(c => c < 0).map(Math.abs);
  const avgGain = gains.reduce((a, b) => a + b, 0) / period;
  const avgLoss = losses.reduce((a, b) => a + b, 0) / period;
  if (avgLoss === 0) return 100;
  return 100 - (100 / (1 + avgGain / avgLoss));
}

/** Bollinger Band bounce — price touches band and reverses. */
export function bollingerBounce(
  bars: BarWithFeatures[],
  idx: number,
  period = 20,
  stdDev = 2.0,
): Direction {
  if (idx < period) return 0;
  const closes = bars.slice(idx - period, idx).map(b => b.close);
  const mean = closes.reduce((a, b) => a + b, 0) / period;
  const std  = Math.sqrt(closes.map(c => (c - mean) ** 2).reduce((a, b) => a + b, 0) / period);
  const upper = mean + stdDev * std;
  const lower = mean - stdDev * std;
  const close = bars[idx].close;

  if (close <= lower) return 1;
  if (close >= upper) return -1;
  return 0;
}

/** Fair Value Gap (FVG) — imbalance in price action (Smart Money Concept). */
export function fairValueGap(bars: BarWithFeatures[], idx: number): Direction {
  if (idx < 2) return 0;
  // Bullish FVG: bar[i-2].high < bar[i].low → gap up
  if (bars[idx - 2].high < bars[idx].low) return 1;
  // Bearish FVG: bar[i-2].low > bar[i].high → gap down
  if (bars[idx - 2].low > bars[idx].high)  return -1;
  return 0;
}
```

---

## Layer 4: Trade Management (`src/management.ts`)

```typescript
/** ATR-based stop loss. Always place beyond noise — never arbitrary pips. */
export function atrStop(
  entryPrice: number,
  direction: 1 | -1,
  atr: number,
  multiplier = 1.5,
): number {
  return entryPrice - direction * atr * multiplier;
}

/** Fixed R-multiple take profit. */
export function fixedRTarget(
  entryPrice: number,
  stopPrice: number,
  rMultiple = 2.0,
): number {
  const risk = Math.abs(entryPrice - stopPrice);
  return entryPrice > stopPrice
    ? entryPrice + risk * rMultiple   // long
    : entryPrice - risk * rMultiple;  // short
}

/** Validate R:R before entering a trade. */
export function validateRR(
  entry: number,
  stop: number,
  target: number,
  minRR = 1.5,
): boolean {
  const risk   = Math.abs(entry - stop);
  const reward = Math.abs(target - entry);
  return risk > 0 && reward / risk >= minRR;
}
```

---

## Strategy File Template (`strategies/STR_001_LondonBreakout.ts`)

```typescript
/**
 * Strategy: STR-001 London Breakout
 * Hypothesis: HYP-001
 * Timeframe: H1 (entry), H4 (bias)
 * Session: London Open (08:00-12:00 UTC)
 * Created: YYYY-MM-DD
 */

import type { BarWithFeatures, Signal, StrategyParams } from '../src/types.js';
import { volatilityFilter, sessionFilter, weekendFilter } from '../src/filters.js';
import { emaBias } from '../src/bias.js';
import { openingRangeBreakout } from '../src/signals.js';
import { atrStop, fixedRTarget, validateRR } from '../src/management.js';

export const DEFAULT_PARAMS: StrategyParams = {
  emaFast: 50,
  emaSlow: 200,
  atrSlMult: 1.5,
  atrTpMult: 3.0,
  rangeBars: 4,
  minAtr: 8.0,
  maxAtr: 35.0,
};

export function generateSignals(
  barsLtf: BarWithFeatures[],
  barsHtf: BarWithFeatures[],
  params: StrategyParams = DEFAULT_PARAMS,
): Signal[] {
  const signals: Signal[] = [];

  for (let i = 10; i < barsLtf.length; i++) {
    const bar = barsLtf[i];

    // Layer 1: Regime
    if (!volatilityFilter(bar, params.minAtr as number, params.maxAtr as number)) continue;
    if (!sessionFilter(bar, ['london', 'overlap'])) continue;
    if (!weekendFilter(bar.datetime)) continue;

    // Layer 2: HTF bias (use closest H4 bar)
    const htfIdx = barsHtf.findIndex(b => b.datetime >= bar.datetime);
    if (htfIdx < 10) continue;
    const bias = emaBias(barsHtf, htfIdx, params.emaFast as number, params.emaSlow as number);

    // Layer 3: Entry trigger
    const triggerDir = openingRangeBreakout(barsLtf, i, 8, params.rangeBars as number);
    if (triggerDir === 0 || triggerDir !== bias) continue;

    // Layer 4: Management
    const entry = bar.close;
    const stop  = atrStop(entry, triggerDir, bar.atr14, params.atrSlMult as number);
    const target = fixedRTarget(entry, stop, params.atrTpMult as number / params.atrSlMult as number);

    if (!validateRR(entry, stop, target, 1.5)) continue;

    signals.push({ direction: triggerDir, stopPrice: stop, targetPrice: target, barIndex: i });
  }

  return signals;
}
```

---

## Strategy Anti-Patterns (Never Do These)

- **No regime filter** — Trading 24/7 on XAUUSD loses to spread in Asian session
- **Indicator soup** — More than 3 indicators = overfitting, not confluence
- **Fixed pip stops** — XAUUSD volatility varies 5× between regimes; always use ATR stops
- **Ignoring HTF** — Trading counter-trend on LTF alone is coin-flip + negative spread
- **`any` types** — Use proper TypeScript types; type safety catches logic errors early
