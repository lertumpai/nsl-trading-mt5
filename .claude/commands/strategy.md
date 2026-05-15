---
description: Scaffold a new strategy TypeScript file. Usage: /strategy <HYP-NNN> <short-name>
---

Scaffold a new strategy TypeScript file for the XAUUSD EA project.

**Arguments provided:** $ARGUMENTS

Parse arguments as: first token = hypothesis ID (e.g. HYP-001), rest = strategy name.

Steps:
1. Read the referenced hypothesis file from `research/hypothesis/` to extract: statement, timeframe, session, entry condition.
2. Read `.agent/skills/strategy-design.md` to understand the 4-layer structure.
3. Find the highest existing STR number in `strategies/` and use the next one.
4. Create `strategies/STR-{NNN}_{slug}.ts` with this structure:

```typescript
/**
 * Strategy: STR-{NNN} — {Name}
 * Hypothesis: {HYP-ID}
 * Timeframe LTF: {from hypothesis}
 * Timeframe HTF: {from hypothesis}
 * Session: {from hypothesis}
 * Created: {today}
 * Status: Research
 *
 * Hypothesis Statement:
 * {paste statement from hypothesis file}
 */

import type { BarWithFeatures, Signal, StrategyParams } from '../src/types.js';
import { volatilityFilter, sessionFilter, weekendFilter } from '../src/filters.js';
import { emaBias } from '../src/bias.js';
import { atrStop, fixedRTarget, validateRR } from '../src/management.js';

// ---- Parameters ----
// TODO: tune these values — they are placeholders
export const DEFAULT_PARAMS: StrategyParams = {
  emaFast: 50,
  emaSlow: 200,
  atrSlMult: 1.5,
  atrTpMult: 3.0,
  minAtr: 8.0,
  maxAtr: 35.0,
};

// ---- Signal generation ----
export function generateSignals(
  barsLtf: BarWithFeatures[],
  barsHtf: BarWithFeatures[],
  params: StrategyParams = DEFAULT_PARAMS,
): Signal[] {
  const signals: Signal[] = [];

  for (let i = 10; i < barsLtf.length; i++) {
    const bar = barsLtf[i];

    // Layer 1: Regime filter
    if (!volatilityFilter(bar, params.minAtr as number, params.maxAtr as number)) continue;
    if (!sessionFilter(bar, [/* TODO: add sessions from hypothesis */])) continue;
    if (!weekendFilter(bar.datetime)) continue;

    // Layer 2: HTF directional bias
    const htfIdx = barsHtf.findLastIndex(b => b.datetime <= bar.datetime);
    if (htfIdx < 10) continue;
    const bias = emaBias(barsHtf, htfIdx, params.emaFast as number, params.emaSlow as number);

    // Layer 3: Entry trigger
    // TODO: implement entry logic from hypothesis
    const triggerDir = 0; // replace with actual signal
    if (triggerDir === 0 || triggerDir !== bias) continue;

    // Layer 4: Trade management
    const entry  = bar.close;
    const stop   = atrStop(entry, triggerDir as 1 | -1, bar.atr14, params.atrSlMult as number);
    const target = fixedRTarget(entry, stop, params.atrTpMult as number / (params.atrSlMult as number));

    if (!validateRR(entry, stop, target, triggerDir as 1 | -1)) continue;

    signals.push({ direction: triggerDir as 1 | -1, stopPrice: stop, targetPrice: target, barIndex: i });
  }

  return signals;
}
```

5. Also create a matching runner script at `strategies/STR-{NNN}_{slug}.run.ts`:

```typescript
/**
 * Run backtest for STR-{NNN}.
 * Usage: bun strategies/STR-{NNN}_{slug}.run.ts
 */

import { loadOHLCV } from '../src/dataLoader.js';
import { cleanOHLCV } from '../src/cleaner.js';
import { addFeatures } from '../src/features.js';
import { runBacktest } from '../src/backtest.js';
import { computeAllMetrics, printReport, saveResults } from '../src/metrics.js';
import { generateSignals, DEFAULT_PARAMS } from './STR-{NNN}_{slug}.js';
import { writeFileSync } from 'fs';

// ---- Load data ----
// TODO: update file paths to match your actual data files
const rawLtf = await loadOHLCV('data/raw/XAUUSD/XAUUSD_H1_*.csv');
const rawHtf = await loadOHLCV('data/raw/XAUUSD/XAUUSD_H4_*.csv');

const barsLtf = addFeatures(cleanOHLCV(rawLtf));
const barsHtf = addFeatures(cleanOHLCV(rawHtf));

// ---- Train/test split (70% in-sample) ----
const splitIdx = Math.floor(barsLtf.length * 0.7);
const isLtf = barsLtf.slice(0, splitIdx);
const ooLtf = barsLtf.slice(splitIdx);
const isHtf = barsHtf.filter(b => b.datetime <= isLtf.at(-1)!.datetime);
const ooHtf = barsHtf.filter(b => b.datetime >  isLtf.at(-1)!.datetime);

// ---- In-sample ----
const isSignals = generateSignals(isLtf, isHtf, DEFAULT_PARAMS);
const isTrades  = runBacktest(isLtf, isSignals);
printReport(computeAllMetrics(isTrades), 'STR-{NNN} In-Sample');

// ---- Out-of-sample ----
const oosSignals = generateSignals(ooLtf, ooHtf, DEFAULT_PARAMS);
const oosTrades  = runBacktest(ooLtf, oosSignals);
const oosMetrics = computeAllMetrics(oosTrades);
printReport(oosMetrics, 'STR-{NNN} Out-of-Sample');

saveResults(oosMetrics, oosTrades, 'STR-{NNN}');
```

6. Print summary:
```
Created:
  strategies/STR-{NNN}_{slug}.ts       ← strategy logic (fill in TODOs)
  strategies/STR-{NNN}_{slug}.run.ts   ← backtest runner

Next: implement the entry trigger in generateSignals(), then run:
  bun strategies/STR-{NNN}_{slug}.run.ts
```
