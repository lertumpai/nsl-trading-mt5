import type { BarWithFeatures, Trade } from './types.js';
import { computeAllMetrics } from './metrics.js';
import { writeFileSync } from 'fs';

export interface ParamGrid {
  [key: string]: number[];
}

export interface GridResult {
  params:         Record<string, number>;
  sharpe:         number;
  profitFactor:   number;
  maxDrawdownPct: number;
  nTrades:        number;
}

export interface SensitivityResult {
  allRobust: boolean;
  byParam: Record<string, {
    baseSharpe:        number;
    minSharpeInRange:  number;
    robust:            boolean;
    scoresByFactor:    number[];   // [×0.8, ×0.9, ×1.0, ×1.1, ×1.2]
  }>;
}

type StrategyFn = (bars: BarWithFeatures[], params: Record<string, number>) => Trade[];

/**
 * Grid search over a parameter space.
 * Optimize on IS data only — never pass OOS bars here.
 */
export function gridSearch(
  barsTrain:   BarWithFeatures[],
  strategyFn:  StrategyFn,
  paramGrid:   ParamGrid,
  metric:      'sharpe' | 'profitFactor' = 'sharpe',
): GridResult[] {
  const keys         = Object.keys(paramGrid);
  const combinations = cartesian(Object.values(paramGrid));
  const results: GridResult[] = [];

  for (const combo of combinations) {
    const params = Object.fromEntries(keys.map((k, i) => [k, combo[i]!]));
    try {
      const trades = strategyFn(barsTrain, params);
      if (trades.length < 30) continue;
      const m = computeAllMetrics(trades);
      results.push({ params, sharpe: m.sharpeRatio, profitFactor: m.profitFactor,
                     maxDrawdownPct: m.maxDrawdownPct, nTrades: m.nTrades });
    } catch { /* skip invalid combinations */ }
  }

  return results.sort((a, b) => b[metric] - a[metric]);
}

/**
 * Test robustness of chosen parameters by perturbing each ±20%.
 * A parameter is robust if Sharpe stays ≥ 70% of base across the perturbation range.
 */
export function sensitivityTest(
  barsTrain:       BarWithFeatures[],
  strategyFn:      StrategyFn,
  bestParams:      Record<string, number>,
  perturbation     = 0.20,
  robustThreshold  = 0.70,
): SensitivityResult {
  const baseTrades = strategyFn(barsTrain, bestParams);
  const baseSharpe = computeAllMetrics(baseTrades).sharpeRatio;
  const byParam: SensitivityResult['byParam'] = {};

  for (const [param, value] of Object.entries(bestParams)) {
    const factors = [0.8, 0.9, 1.0, 1.1, 1.2];
    const scores: number[] = [];
    for (const f of factors) {
      try {
        const trades = strategyFn(barsTrain, { ...bestParams, [param]: value * f });
        scores.push(computeAllMetrics(trades).sharpeRatio);
      } catch { scores.push(0); }
    }
    const minScore = Math.min(...scores);
    byParam[param] = {
      baseSharpe,
      minSharpeInRange: +minScore.toFixed(3),
      robust:           minScore >= baseSharpe * robustThreshold,
      scoresByFactor:   scores.map(s => +s.toFixed(3)),
    };
  }

  return { allRobust: Object.values(byParam).every(v => v.robust), byParam };
}

/**
 * Find the best robust parameter set from grid results.
 * Tries candidates in rank order until one passes the sensitivity test.
 * Returns null if no candidate is robust (strategy should be rejected).
 */
export function selectRobustParams(
  results:     GridResult[],
  barsTrain:   BarWithFeatures[],
  strategyFn:  StrategyFn,
): { params: Record<string, number>; sensitivity: SensitivityResult } | null {
  for (const result of results.slice(0, 20)) {   // check top 20 candidates
    const sensitivity = sensitivityTest(barsTrain, strategyFn, result.params);
    if (sensitivity.allRobust) return { params: result.params, sensitivity };
  }
  return null;
}

/** Save 2D heatmap data as JSON for external visualization. */
export function saveHeatmapData(
  results:    GridResult[],
  paramX:     string,
  paramY:     string,
  outputPath: string,
  metric:     'sharpe' | 'profitFactor' = 'sharpe',
): void {
  const data = results.map(r => ({ x: r.params[paramX], y: r.params[paramY], value: r[metric] }));
  writeFileSync(outputPath, JSON.stringify(data, null, 2));
}

// Cartesian product of arrays
function cartesian(arrays: number[][]): number[][] {
  return arrays.reduce<number[][]>(
    (acc, arr) => acc.flatMap(prev => arr.map(val => [...prev, val])),
    [[]]
  );
}
