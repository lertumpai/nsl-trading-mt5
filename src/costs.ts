import type { BrokerCosts } from './types.js';

const CONTRACT_SIZE = 100; // 1 standard lot = 100 troy oz

export const DEFAULT_COSTS: BrokerCosts = {
  spreadUsd:        0.35,
  commissionPerLot: 7.0,
  slippageUsd:      0.20,
  swapLongPerDay:  -3.5,
  swapShortPerDay:  1.2,
};

/**
 * Net P&L for a single trade after all broker costs.
 * direction: 1 = long, -1 = short.
 */
export function applyTradeCosts(
  entryPrice:  number,
  exitPrice:   number,
  direction:   1 | -1,
  lotSize:     number,
  holdDays:    number,
  costs:       BrokerCosts = DEFAULT_COSTS,
): number {
  const grossPnl    = (exitPrice - entryPrice) * direction * CONTRACT_SIZE * lotSize;
  const spreadCost  = costs.spreadUsd * 2 * lotSize;       // paid on entry + exit
  const commission  = costs.commissionPerLot * lotSize;
  const slippage    = costs.slippageUsd * 2 * lotSize;
  const swap        = direction === 1
    ? costs.swapLongPerDay  * holdDays * lotSize
    : costs.swapShortPerDay * holdDays * lotSize;

  return grossPnl - spreadCost - commission - slippage + swap;
}
