import type { BarWithFeatures, Trade, Signal, BrokerCosts } from './types.js';
import { DEFAULT_COSTS, applyTradeCosts } from './costs.js';

/**
 * Vectorized backtest engine.
 *
 * Execution model:
 *   - Signal fires on bar[i] CLOSE  → entry on bar[i+1] OPEN
 *   - Stop and target checked intrabar on bar[i+1] and beyond
 *   - Reversal exits happen at bar[i+1] OPEN (same next-bar rule)
 *   - One trade at a time per strategy instance
 */
export function runBacktest(
  bars:     BarWithFeatures[],
  signals:  Signal[],
  lotSize   = 0.10,
  costs:    BrokerCosts = DEFAULT_COSTS,
): Trade[] {
  const trades: Trade[] = [];
  // Index signals by the bar they were generated on (bar close idx)
  const signalMap = new Map<number, Signal>(signals.map(s => [s.barIndex, s]));

  type ActiveTrade = {
    entryBarIdx:  number;
    entryTime:    Date;
    entryPrice:   number;
    direction:    1 | -1;
    stopPrice:    number;
    targetPrice:  number;
  };

  let active: ActiveTrade | null = null;

  for (let i = 1; i < bars.length; i++) {
    const bar = bars[i]!;

    // ── ENTRY ──────────────────────────────────────────────
    // Enter on bar[i] open when a signal was generated at bar[i-1] close
    if (!active) {
      const sig = signalMap.get(i - 1);
      if (sig && sig.direction !== 0) {
        const dir   = sig.direction as 1 | -1;
        const slip  = costs.slippageUsd * dir;
        active = {
          entryBarIdx:  i,
          entryTime:    bar.datetime,
          entryPrice:   bar.open + slip,
          direction:    dir,
          stopPrice:    sig.stopPrice,
          targetPrice:  sig.targetPrice,
        };
      }
      continue;
    }

    // ── EXIT ───────────────────────────────────────────────
    const { entryBarIdx, entryTime, entryPrice, direction, stopPrice, targetPrice } = active;
    let exitPrice:  number | null = null;
    let exitReason: Trade['exitReason'] | null = null;

    // Stop hit intrabar (worst case: filled at stop price, slippage goes against)
    if (direction === 1  && bar.low  <= stopPrice) {
      exitPrice  = stopPrice - costs.slippageUsd;
      exitReason = 'stop';
    } else if (direction === -1 && bar.high >= stopPrice) {
      exitPrice  = stopPrice + costs.slippageUsd;
      exitReason = 'stop';
    }
    // Target hit intrabar (only if stop not triggered)
    else if (direction === 1  && bar.high >= targetPrice) {
      exitPrice  = targetPrice - costs.slippageUsd;
      exitReason = 'target';
    } else if (direction === -1 && bar.low  <= targetPrice) {
      exitPrice  = targetPrice + costs.slippageUsd;
      exitReason = 'target';
    }
    // Opposite signal: exit at THIS bar's open (signal was generated at bar[i-1])
    // This respects next-bar execution symmetry for both entries and exits.
    else {
      const sig = signalMap.get(i - 1);
      if (sig && sig.direction !== 0 && sig.direction !== direction) {
        exitPrice  = bar.open;
        exitReason = 'signal_reverse';
      }
    }

    if (exitPrice !== null && exitReason !== null) {
      const holdMs   = bar.datetime.getTime() - entryTime.getTime();
      const holdDays = holdMs / 86_400_000;
      const holdBars = i - entryBarIdx;

      trades.push({
        entryTime,
        exitTime:   bar.datetime,
        direction:  direction === 1 ? 'long' : 'short',
        entryPrice,
        exitPrice,
        stopPrice,
        targetPrice,
        exitReason,
        holdBars,
        holdDays,
        lotSize,
        pnlUsd: applyTradeCosts(entryPrice, exitPrice, direction, lotSize, holdDays, costs),
      });
      active = null;
    }
  }

  return trades;
}
