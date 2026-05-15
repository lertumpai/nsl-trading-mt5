# Skill: Backtesting

> Use this skill when implementing or interpreting a backtest.
> Language: **TypeScript**. Read `.agent/skills/strategy-design.md` first.

---

## Core Rules

1. **Next-bar execution only.** Signal on bar[i] close → entry on bar[i+1] open. No look-ahead.
2. **One trade at a time** per strategy unless the strategy explicitly defines a portfolio model.
3. **Apply all costs:** spread + commission + slippage + swap.
4. **Train/test split is sacred.** In-sample = first 70%. Out-of-sample = last 30%. Never optimize on OOS.

---

## Types (`src/types.ts` additions)

```typescript
export interface BrokerCosts {
  spreadUsd: number;       // USD per trade (both entry and exit)
  commissionPerLot: number; // USD round-trip per lot
  slippageUsd: number;     // extra USD per fill
  swapLongPerDay: number;  // USD per lot per day (negative = cost)
  swapShortPerDay: number; // USD per lot per day
}

export interface Trade {
  entryTime: Date;
  exitTime: Date;
  direction: 'long' | 'short';
  entryPrice: number;
  exitPrice: number;
  stopPrice: number;
  targetPrice: number;
  exitReason: 'stop' | 'target' | 'signal_reverse' | 'time_stop';
  holdBars: number;
  holdDays: number;
  lotSize: number;
  pnlUsd: number;
}

export const DEFAULT_COSTS: BrokerCosts = {
  spreadUsd: 0.35,
  commissionPerLot: 7.0,
  slippageUsd: 0.20,
  swapLongPerDay: -3.5,
  swapShortPerDay: 1.2,
};
```

---

## Cost Model (`src/costs.ts`)

```typescript
import type { BrokerCosts } from './types.js';

const CONTRACT_SIZE = 100; // 1 lot = 100 oz for XAUUSD

export function applyTradeCosts(
  entryPrice: number,
  exitPrice: number,
  direction: 1 | -1,
  lotSize: number,
  holdDays: number,
  costs: BrokerCosts,
): number {
  const grossPnl = (exitPrice - entryPrice) * direction * CONTRACT_SIZE * lotSize;
  const spreadCost = costs.spreadUsd * 2 * lotSize;
  const commission  = costs.commissionPerLot * lotSize;
  const slippage    = costs.slippageUsd * 2 * lotSize;
  const swap = direction === 1
    ? costs.swapLongPerDay  * holdDays * lotSize
    : costs.swapShortPerDay * holdDays * lotSize;

  return grossPnl - spreadCost - commission - slippage + swap;
}
```

---

## Vectorized Backtest Engine (`src/backtest.ts`)

```typescript
import type { BarWithFeatures, Trade, Signal, BrokerCosts } from './types.js';
import { DEFAULT_COSTS, applyTradeCosts } from './costs.js';

export function runBacktest(
  bars: BarWithFeatures[],
  signals: Signal[],
  lotSize = 0.10,
  costs: BrokerCosts = DEFAULT_COSTS,
): Trade[] {
  const trades: Trade[] = [];
  // Build a fast index: barIndex → Signal
  const signalMap = new Map(signals.map(s => [s.barIndex, s]));

  let activeTrade: {
    entryBarIdx: number;
    entryTime: Date;
    entryPrice: number;
    direction: 1 | -1;
    stopPrice: number;
    targetPrice: number;
  } | null = null;

  for (let i = 1; i < bars.length; i++) {
    const bar = bars[i];
    const prevSignal = signalMap.get(i - 1);

    // --- Entry ---
    if (!activeTrade && prevSignal && prevSignal.direction !== 0) {
      const dir = prevSignal.direction as 1 | -1;
      const slip = costs.slippageUsd * dir;
      activeTrade = {
        entryBarIdx: i,
        entryTime: bar.datetime,
        entryPrice: bar.open + slip,
        direction: dir,
        stopPrice: prevSignal.stopPrice,
        targetPrice: prevSignal.targetPrice,
      };
      continue;
    }

    // --- Exit ---
    if (activeTrade) {
      const { entryBarIdx, entryTime, entryPrice, direction, stopPrice, targetPrice } = activeTrade;
      let exitPrice: number | null = null;
      let exitReason: Trade['exitReason'] | null = null;

      // Intrabar stop check
      if (direction === 1  && bar.low  <= stopPrice) { exitPrice = stopPrice - costs.slippageUsd; exitReason = 'stop'; }
      if (direction === -1 && bar.high >= stopPrice) { exitPrice = stopPrice + costs.slippageUsd; exitReason = 'stop'; }

      // Intrabar target check (only if stop not hit)
      if (!exitReason) {
        if (direction === 1  && bar.high >= targetPrice) { exitPrice = targetPrice - costs.slippageUsd; exitReason = 'target'; }
        if (direction === -1 && bar.low  <= targetPrice) { exitPrice = targetPrice + costs.slippageUsd; exitReason = 'target'; }
      }

      // Opposite signal reversal
      const currentSignal = signalMap.get(i);
      if (!exitReason && currentSignal && currentSignal.direction !== 0 && currentSignal.direction !== direction) {
        exitPrice = bar.open;
        exitReason = 'signal_reverse';
      }

      if (exitPrice !== null && exitReason) {
        const holdMs   = bar.datetime.getTime() - entryTime.getTime();
        const holdDays = holdMs / 86_400_000;
        const holdBars = i - entryBarIdx;

        trades.push({
          entryTime,
          exitTime: bar.datetime,
          direction: direction === 1 ? 'long' : 'short',
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
        activeTrade = null;
      }
    }
  }

  return trades;
}
```

---

## Walk-Forward Analysis (`src/walkForward.ts`)

```typescript
import type { BarWithFeatures, Trade } from './types.js';
import { computeAllMetrics } from './metrics.js';

type StrategyFn = (bars: BarWithFeatures[]) => Trade[];

export function walkForwardAnalysis(
  bars: BarWithFeatures[],
  strategyFn: StrategyFn,
  nWindows = 5,
  trainPct = 0.7,
) {
  const initialTrainEnd = Math.floor(bars.length * trainPct);
  const windowSize = Math.floor((bars.length - initialTrainEnd) / nWindows);

  const allOosTrades: Trade[] = [];
  const windowResults: Array<{ window: number; oosMetrics: ReturnType<typeof computeAllMetrics> }> = [];

  for (let i = 0; i < nWindows; i++) {
    const trainEnd = initialTrainEnd + i * windowSize;
    const oosEnd   = trainEnd + windowSize;

    const oosBar   = bars.slice(trainEnd, oosEnd);
    const oosTrades = strategyFn(oosBar);

    allOosTrades.push(...oosTrades);
    windowResults.push({ window: i, oosMetrics: computeAllMetrics(oosTrades) });
  }

  const combinedOos = computeAllMetrics(allOosTrades);
  const isTrades    = strategyFn(bars.slice(0, initialTrainEnd));
  const isMetrics   = computeAllMetrics(isTrades);

  const wfEfficiency = isMetrics.profitFactor > 0
    ? combinedOos.profitFactor / isMetrics.profitFactor
    : 0;

  return { combinedOos, isMetrics, wfEfficiency, windowResults, allOosTrades };
}
```

---

## Monte Carlo Simulation (`src/monteCarlo.ts`)

```typescript
import type { Trade } from './types.js';

export function monteCarlo(
  trades: Trade[],
  nSimulations = 1000,
  initialCapital = 10_000,
): { ddMedian: number; dd95th: number; dd99th: number; positiveOutcomePct: number } {
  const pnls = trades.map(t => t.pnlUsd);
  const maxDrawdowns: number[] = [];
  const finalEquities: number[] = [];

  for (let s = 0; s < nSimulations; s++) {
    // Fisher-Yates shuffle
    const shuffled = [...pnls];
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }

    let equity = initialCapital;
    let peak = initialCapital;
    let maxDd = 0;

    for (const pnl of shuffled) {
      equity += pnl;
      peak = Math.max(peak, equity);
      maxDd = Math.min(maxDd, (equity - peak) / peak);
    }

    maxDrawdowns.push(maxDd * 100);
    finalEquities.push(equity);
  }

  maxDrawdowns.sort((a, b) => a - b);
  const dd95th = maxDrawdowns[Math.floor(nSimulations * 0.05)];
  const dd99th = maxDrawdowns[Math.floor(nSimulations * 0.01)];
  const ddMedian = maxDrawdowns[Math.floor(nSimulations * 0.5)];
  const positiveOutcomePct = finalEquities.filter(e => e > initialCapital).length / nSimulations * 100;

  return { ddMedian, dd95th, dd99th, positiveOutcomePct };
}
```

---

## Acceptance Thresholds

| Metric | Minimum | Good | Excellent |
|--------|---------|------|-----------|
| Sharpe (annualized, OOS) | 1.0 | 1.5 | 2.0+ |
| Max Drawdown | < 15% | < 10% | < 7% |
| Profit Factor | 1.4 | 1.6 | 2.0+ |
| WF Efficiency | 0.60 | 0.75 | 0.90+ |
| MC 95th DD | < 25% | < 20% | < 15% |
| Min Trades (OOS) | 50 | 100 | 200+ |

---

## Common Backtest Errors to Avoid

- **Look-ahead bias:** Using `bars[i + 1]` in signal generation, or computing rolling stats that include the current bar
- **In-bar execution:** Assuming you fill at the exact signal-bar open when the signal fires on that bar's close
- **Repainting indicators:** Any indicator that changes past values — use only fixed-lag lookback windows
- **No cost model:** Even 0.35 USD spread matters — at 300 trades/year it's $105 drag on a $10k account
- **`Date` timezone bugs:** Always use UTC methods (`getUTCHours`, `getUTCDay`) — never local time methods
