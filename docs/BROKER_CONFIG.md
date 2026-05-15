# Broker Configuration Template

Use this template when setting up costs for a new broker or account type.
Copy the relevant section into your strategy runner and adjust values to match
your real account statement.

---

## How to Measure Your Actual Costs

| Cost | How to measure |
|---|---|
| **Spread** | Open MT5 → right-click XAUUSD → Specification. Or watch bid/ask spread during London hours. |
| **Commission** | Check your account statement: commission charged per round trip on a 1-lot trade. |
| **Slippage** | Compare ordered price vs filled price over 20+ trades on real account. Typically 0.10–0.50 USD on XAUUSD. |
| **Swap long** | MT5 → Market Watch → right-click XAUUSD → Specification → Swap long (per lot per day). |
| **Swap short** | Same panel → Swap short (per lot per day). |

---

## Cost Profiles

### Raw/ECN Account (typical)
```typescript
import type { BrokerCosts } from '../src/types.js';

export const RAW_ECN_COSTS: BrokerCosts = {
  spreadUsd:        0.20,   // ~$0.20 per lot during London (tight raw spread)
  commissionPerLot: 7.00,   // $7 round-trip per lot (common for ECN/raw accounts)
  slippageUsd:      0.15,   // conservative slippage estimate
  swapLongPerDay:  -3.50,   // check your broker — negative = you pay
  swapShortPerDay:  1.20,   // positive = you receive
};
```

### Standard Account (no commission, wider spread)
```typescript
export const STANDARD_COSTS: BrokerCosts = {
  spreadUsd:        1.50,   // spread baked in (~1.5 USD per trade direction)
  commissionPerLot: 0.00,   // no explicit commission
  slippageUsd:      0.25,
  swapLongPerDay:  -3.50,
  swapShortPerDay:  1.20,
};
```

### Conservative / Worst-Case (stress test)
```typescript
export const STRESS_COSTS: BrokerCosts = {
  spreadUsd:        0.50,   // wider spread during news
  commissionPerLot: 9.00,   // higher commission
  slippageUsd:      0.50,   // more slippage on fast markets
  swapLongPerDay:  -4.50,
  swapShortPerDay:  0.80,
};
```

---

## Usage in Runners

```typescript
import { RAW_ECN_COSTS } from '../docs/brokerCosts.js';
import { runBacktest } from '../src/backtest.js';

const trades = runBacktest(bars, signals, lotSize, RAW_ECN_COSTS);
```

Always run your final backtest with **STRESS_COSTS** as a sanity check.
If the strategy fails under stress costs, it is too fragile for live trading.

---

## Swap Note for Overnight Positions

XAUUSD swaps are typically unfavorable for longs (you pay ~$3–5/lot/day).
Strategies that hold overnight should account for this explicitly.

For overnight trades in the backtest, swap is already applied in `applyTradeCosts()`
via `holdDays × swapPerDay × lotSize`.

---

## Broker-Specific Presets

Populate this table as you test with different brokers:

| Broker | Account | Spread (USD) | Comm/lot | Swap L | Swap S | Verified |
|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — |
