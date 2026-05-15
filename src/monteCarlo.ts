import type { Trade } from './types.js';

export interface MonteCarloResult {
  ddMedian:            number;  // median max drawdown %
  dd95th:              number;  // 95th-percentile max drawdown % (worst 5%)
  dd99th:              number;  // 99th-percentile max drawdown %
  positiveOutcomePct:  number;  // % of simulations ending above initial capital
  finalEquityMedian:   number;  // median final equity
}

/**
 * Monte Carlo simulation via random trade-order shuffling.
 * Measures path-dependency and sequence risk.
 *
 * dd95th must be ≥ -25% (i.e. value returned is ≥ -25) to pass Stage B.
 */
export function monteCarlo(
  trades:          Trade[],
  nSimulations     = 1_000,
  initialCapital   = 10_000,
): MonteCarloResult {
  const pnls          = trades.map(t => t.pnlUsd);
  const maxDrawdowns: number[] = [];
  const finalEquities: number[] = [];

  for (let s = 0; s < nSimulations; s++) {
    // Fisher-Yates shuffle
    const shuffled = [...pnls];
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j]!, shuffled[i]!];
    }

    let equity = initialCapital;
    let peak   = initialCapital;
    let maxDd  = 0;

    for (const pnl of shuffled) {
      equity += pnl;
      peak    = Math.max(peak, equity);
      maxDd   = Math.min(maxDd, (equity - peak) / peak);
    }

    maxDrawdowns.push(maxDd * 100);
    finalEquities.push(equity);
  }

  maxDrawdowns.sort((a, b) => a - b);   // ascending (most negative first)
  finalEquities.sort((a, b) => a - b);

  const n = nSimulations;
  return {
    ddMedian:           maxDrawdowns[Math.floor(n * 0.50)]!,
    dd95th:             maxDrawdowns[Math.floor(n * 0.05)]!,   // worst 5%
    dd99th:             maxDrawdowns[Math.floor(n * 0.01)]!,
    positiveOutcomePct: finalEquities.filter(e => e > initialCapital).length / n * 100,
    finalEquityMedian:  finalEquities[Math.floor(n * 0.50)]!,
  };
}
