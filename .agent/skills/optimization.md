# Skill: Parameter Optimization (Anti-Overfitting)

> Use this skill when searching for strategy parameters.
> Language: **TypeScript**. Overfitting is the #1 cause of live strategy failure.

---

## Rules (Mandatory)

1. **Maximum 3 free parameters per optimization run.**
2. **Optimize only on in-sample data** (first 70%). Never look at OOS during search.
3. **Prefer parameters from flat/broad zones**, not isolated peaks.
4. **Sensitivity test:** chosen params must produce Sharpe ≥ 0.7× of peak across ±20% range.
5. **Walk-forward must pass** after optimization.
6. **Log every run** in `research/experiments/OPT_XXX_YYYYMMDD.md`.

---

## Types

```typescript
export interface ParamGrid {
  [key: string]: number[];
}

export interface GridResult {
  params: Record<string, number>;
  sharpe: number;
  profitFactor: number;
  maxDrawdownPct: number;
  nTrades: number;
}
```

---

## Grid Search (`src/optimization.ts`)

```typescript
import type { BarWithFeatures, Trade } from './types.js';
import type { ParamGrid, GridResult } from './optimization.js';
import { computeAllMetrics } from './metrics.js';

type StrategyFn = (bars: BarWithFeatures[], params: Record<string, number>) => Trade[];

export function gridSearch(
  barsTrain: BarWithFeatures[],
  strategyFn: StrategyFn,
  paramGrid: ParamGrid,
  metric: keyof Pick<GridResult, 'sharpe' | 'profitFactor'> = 'sharpe',
): GridResult[] {
  const keys = Object.keys(paramGrid);
  const combinations = cartesian(Object.values(paramGrid));
  const results: GridResult[] = [];

  for (const combo of combinations) {
    const params = Object.fromEntries(keys.map((k, i) => [k, combo[i]]));

    try {
      const trades = strategyFn(barsTrain, params);
      if (trades.length < 30) continue; // skip undersampled combos

      const m = computeAllMetrics(trades);
      results.push({
        params,
        sharpe: m.sharpeRatio,
        profitFactor: m.profitFactor,
        maxDrawdownPct: m.maxDrawdownPct,
        nTrades: m.nTrades,
      });
    } catch {
      // skip invalid param combos
    }
  }

  return results.sort((a, b) => b[metric] - a[metric]);
}

/** Generate all combinations (Cartesian product) of arrays. */
function cartesian(arrays: number[][]): number[][] {
  return arrays.reduce<number[][]>(
    (acc, arr) => acc.flatMap(prev => arr.map(val => [...prev, val])),
    [[]]
  );
}
```

---

## Sensitivity Test (`src/optimization.ts` continued)

```typescript
export interface SensitivityResult {
  allRobust: boolean;
  byParam: Record<string, {
    baseSharpe: number;
    minSharpeInRange: number;
    robust: boolean;
    scoresByFactor: number[]; // [0.8x, 0.9x, 1.0x, 1.1x, 1.2x]
  }>;
}

export function sensitivityTest(
  barsTrain: BarWithFeatures[],
  strategyFn: StrategyFn,
  bestParams: Record<string, number>,
  perturbation = 0.20,
  robustThreshold = 0.70,
): SensitivityResult {
  const baseTrades = strategyFn(barsTrain, bestParams);
  const baseSharpe = computeAllMetrics(baseTrades).sharpeRatio;

  const byParam: SensitivityResult['byParam'] = {};

  for (const [param, value] of Object.entries(bestParams)) {
    const factors = [0.8, 0.9, 1.0, 1.1, 1.2];
    const scores: number[] = [];

    for (const factor of factors) {
      const testParams = { ...bestParams, [param]: value * factor };
      try {
        const trades = strategyFn(barsTrain, testParams);
        scores.push(computeAllMetrics(trades).sharpeRatio);
      } catch {
        scores.push(0);
      }
    }

    const minScore = Math.min(...scores);
    byParam[param] = {
      baseSharpe,
      minSharpeInRange: +minScore.toFixed(3),
      robust: minScore >= baseSharpe * robustThreshold,
      scoresByFactor: scores.map(s => +s.toFixed(3)),
    };
  }

  return { allRobust: Object.values(byParam).every(v => v.robust), byParam };
}
```

---

## Select Robust Parameters

```typescript
export function selectRobustParams(
  results: GridResult[],
  barsTrain: BarWithFeatures[],
  strategyFn: StrategyFn,
): { params: Record<string, number>; sensitivity: SensitivityResult } | null {
  for (const result of results) {
    const sensitivity = sensitivityTest(barsTrain, strategyFn, result.params);
    if (sensitivity.allRobust) {
      return { params: result.params, sensitivity };
    }
  }
  return null; // no robust params found — reject strategy
}
```

---

## Heatmap Output (JSON for Visualization)

```typescript
import { writeFileSync } from 'fs';

export function saveHeatmapData(
  results: GridResult[],
  paramX: string,
  paramY: string,
  metric: keyof GridResult = 'sharpe',
  outputPath: string,
): void {
  const data = results.map(r => ({
    x: r.params[paramX],
    y: r.params[paramY],
    value: r[metric],
  }));
  writeFileSync(outputPath, JSON.stringify(data, null, 2));
  console.log(`Heatmap data saved to ${outputPath}`);
}
```

Load this JSON into any charting tool (browser + Chart.js, Observable, etc.) to visualize the parameter landscape.

---

## Walk-Forward With Optimization (`src/optimization.ts` continued)

```typescript
export function walkForwardWithOptimization(
  bars: BarWithFeatures[],
  strategyFn: StrategyFn,
  paramGrid: ParamGrid,
  nWindows = 5,
  trainPct = 0.7,
) {
  const initialTrainEnd = Math.floor(bars.length * trainPct);
  const windowSize = Math.floor((bars.length - initialTrainEnd) / nWindows);

  const allOosTrades: Trade[] = [];
  const paramsPerWindow: Array<{ window: number; params: Record<string, number> }> = [];

  for (let i = 0; i < nWindows; i++) {
    const trainEnd = initialTrainEnd + i * windowSize;
    const oosEnd   = trainEnd + windowSize;
    const barsTrain = bars.slice(0, trainEnd);
    const barsOos   = bars.slice(trainEnd, oosEnd);

    // Optimize on train, pick robust params
    const results = gridSearch(barsTrain, strategyFn, paramGrid);
    const chosen  = selectRobustParams(results, barsTrain, strategyFn);

    if (!chosen) {
      console.warn(`Window ${i}: no robust params found — skipping`);
      continue;
    }

    paramsPerWindow.push({ window: i, params: chosen.params });
    allOosTrades.push(...strategyFn(barsOos, chosen.params));
  }

  return { allOosTrades, paramsPerWindow };
}
```

---

## Over-Optimization Warning Signs

| Warning Sign | Meaning |
|---|---|
| IS Sharpe > 3.0 | Almost certainly overfit for XAUUSD |
| OOS Sharpe < 0.5 × IS Sharpe | Params don't generalize |
| Best params at edge of search grid | Wrong search space |
| Single isolated peak in heatmap | Curve-fit, not robust |
| Adding one param improves Sharpe > 0.5 | Fitting noise |
| < 50 trades in IS period | Insufficient sample — not optimizable |

---

## Optimization Log Template

Save to `research/experiments/OPT_XXX_YYYYMMDD.md`:

```markdown
# Optimization Run: OPT-001
Date: YYYY-MM-DD
Strategy: STR-001
Data: XAUUSD H1, 2015–2022 (in-sample)

## Search Space
- atrSlMult: [1.0, 1.5, 2.0, 2.5, 3.0]
- atrTpMult: [1.5, 2.0, 2.5, 3.0, 3.5]

## Top 3 Results
| atrSlMult | atrTpMult | Sharpe | PF  | DD%  | Trades |
|-----------|-----------|--------|-----|------|--------|
| 1.5       | 3.0       | 1.82   | 1.7 | 9.2  | 312    |

## Chosen Parameters
{ atrSlMult: 1.5, atrTpMult: 3.0 }

## Sensitivity Test
allRobust: true
- atrSlMult: scores [1.61, 1.74, 1.82, 1.78, 1.69] — flat plateau

## Notes
Wide profitable zone between slMult 1.3–1.8 and tpMult 2.5–3.5. Selected mid-plateau.
```
