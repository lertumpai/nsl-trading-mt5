import type { BarWithFeatures, Trade } from './types.js';
import { computeAllMetrics, type PerformanceMetrics } from './metrics.js';

type StrategyFn = (bars: BarWithFeatures[]) => Trade[];

export interface WalkForwardResult {
  combinedOosTrades:  Trade[];
  combinedOosMetrics: PerformanceMetrics;
  isMetrics:          PerformanceMetrics;
  /** OOS Sharpe ÷ IS Sharpe — the key generalization indicator. */
  wfEfficiency:       number;
  windows: Array<{
    window:     number;
    trainBars:  number;
    oosBars:    number;
    oosMetrics: PerformanceMetrics;
  }>;
}

/**
 * Expanding-window walk-forward validation.
 *
 * WF Efficiency = OOS Sharpe ÷ IS Sharpe (not profit factor).
 * A value ≥ 0.60 indicates acceptable generalization.
 *
 * @param nWindows  Number of OOS windows (minimum 5 recommended)
 * @param trainPct  Fraction of data used for the first IS window
 */
export function walkForwardAnalysis(
  bars:      BarWithFeatures[],
  strategyFn: StrategyFn,
  nWindows    = 5,
  trainPct    = 0.7,
): WalkForwardResult {
  const initialTrainEnd = Math.floor(bars.length * trainPct);
  const windowSize      = Math.floor((bars.length - initialTrainEnd) / nWindows);

  if (windowSize < 10) throw new Error('Too few bars for the requested number of WF windows');

  const allOosTrades: Trade[] = [];
  const windows: WalkForwardResult['windows'] = [];

  for (let w = 0; w < nWindows; w++) {
    const trainEnd = initialTrainEnd + w * windowSize;
    const oosEnd   = Math.min(trainEnd + windowSize, bars.length);

    const oosBars   = bars.slice(trainEnd, oosEnd);
    const oosTrades = strategyFn(oosBars);
    const oosMetrics = computeAllMetrics(oosTrades);

    allOosTrades.push(...oosTrades);
    windows.push({ window: w, trainBars: trainEnd, oosBars: oosBars.length, oosMetrics });
  }

  const combinedOosMetrics = computeAllMetrics(allOosTrades);

  // IS metrics — run on the full initial in-sample period
  const isTrades  = strategyFn(bars.slice(0, initialTrainEnd));
  const isMetrics = computeAllMetrics(isTrades);

  // WF efficiency: OOS Sharpe ÷ IS Sharpe
  const wfEfficiency = isMetrics.sharpeRatio > 0
    ? combinedOosMetrics.sharpeRatio / isMetrics.sharpeRatio
    : 0;

  return { combinedOosTrades: allOosTrades, combinedOosMetrics, isMetrics, wfEfficiency, windows };
}
