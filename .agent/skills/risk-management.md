# Skill: Risk Management & Position Sizing

> Every EA deployed on a live account MUST implement all rules in this file.
> Language: **TypeScript** (research) + **MQL5** (live EA).

---

## Risk Philosophy

XAUUSD can move 30–80 USD in a day. A single mismanaged trade can wipe a week of gains.

**Rule of thumb:** Preserve capital first, generate returns second.

---

## Position Sizing (`src/riskManagement.ts`)

```typescript
const CONTRACT_SIZE = 100; // 1 lot = 100 oz

/** Fixed fractional position sizing — the only acceptable method. */
export function calculateLotSize(
  accountBalance: number,
  riskPct: number,           // e.g., 0.01 = 1%
  entryPrice: number,
  stopLossPrice: number,
  minLot = 0.01,
  maxLot = 10.0,
  lotStep = 0.01,
): number {
  const riskUsd = accountBalance * riskPct;
  const priceRisk = Math.abs(entryPrice - stopLossPrice);

  if (priceRisk === 0) throw new Error('Stop loss cannot equal entry price');

  // 1 lot = 100 oz → $1 price move = $100 per lot
  const rawLot = riskUsd / (priceRisk * CONTRACT_SIZE);

  // Round down to lot step, then clamp to [min, max]
  const stepped = Math.floor(rawLot / lotStep) * lotStep;
  return Math.max(minLot, Math.min(maxLot, +stepped.toFixed(2)));
}

// Example:
// balance=$10,000, risk=1%, entry=2350, stop=2335 → price_risk=15
// lot = (10000*0.01) / (15*100) = 100/1500 = 0.067 → 0.06 lots
```

### Risk Per Trade Limits

| Account Stage | Max Risk/Trade | Max Concurrent | Total Exposure |
|---|---|---|---|
| Demo / Forward test | 1.0% | 1 | 1.0% |
| Live ramp-up (weeks 1–4) | 0.5% | 1 | 0.5% |
| Live normal | 1.0% | 2 | 2.0% |
| Live maximum | 1.5% | 2 | 3.0% |

---

## Stop Loss Validation

```typescript
export interface StopValidation {
  valid: boolean;
  priceRisk: number;
  atrMultiples: number;
  reason: 'ok' | 'wrong_side' | 'too_tight' | 'too_wide';
}

export function validateStopLoss(
  entry: number,
  stop: number,
  direction: 1 | -1,
  atr: number,
  minAtrMult = 1.0,
  maxAtrMult = 4.0,
): StopValidation {
  const priceRisk = Math.abs(entry - stop);
  const atrMultiples = priceRisk / atr;

  const wrongSide = (direction === 1 && stop >= entry) || (direction === -1 && stop <= entry);
  const tooTight  = atrMultiples < minAtrMult;
  const tooWide   = atrMultiples > maxAtrMult;

  return {
    valid: !wrongSide && !tooTight && !tooWide,
    priceRisk: +priceRisk.toFixed(3),
    atrMultiples: +atrMultiples.toFixed(2),
    reason: wrongSide ? 'wrong_side' : tooTight ? 'too_tight' : tooWide ? 'too_wide' : 'ok',
  };
}
```

**Golden Rule:** Stop loss must be placed where the trade thesis is invalidated — beyond a structure level, beyond the noise band, never at an arbitrary fixed distance.

---

## Circuit Breakers (`src/riskManagement.ts` continued)

```typescript
export interface RiskState {
  peakEquity: number;
  dayStartBalance: number;
  consecutiveLosses: number;
  lotReductionFactor: number;
}

export interface CircuitBreakerResult {
  canTrade: boolean;
  reason?: string;
  lotFactor: number;
}

export interface CircuitBreakerThresholds {
  maxDailyLossPct: number;    // default 2%
  maxWeeklyLossPct: number;   // default 5%
  maxDrawdownPct: number;     // default 10% — pause
  killDrawdownPct: number;    // default 15% — hard stop
}

export const DEFAULT_THRESHOLDS: CircuitBreakerThresholds = {
  maxDailyLossPct:  2.0,
  maxWeeklyLossPct: 5.0,
  maxDrawdownPct:   10.0,
  killDrawdownPct:  15.0,
};

export function checkCircuitBreakers(
  currentEquity: number,
  state: RiskState,
  thresholds: CircuitBreakerThresholds = DEFAULT_THRESHOLDS,
): CircuitBreakerResult {
  // Update peak equity
  state.peakEquity = Math.max(state.peakEquity, currentEquity);

  const ddPct = (state.peakEquity - currentEquity) / state.peakEquity * 100;
  const dailyLossPct = (state.dayStartBalance - currentEquity) / state.dayStartBalance * 100;

  if (ddPct >= thresholds.killDrawdownPct) {
    return { canTrade: false, reason: `Kill-switch: drawdown ${ddPct.toFixed(1)}% >= ${thresholds.killDrawdownPct}%`, lotFactor: 0 };
  }
  if (ddPct >= thresholds.maxDrawdownPct) {
    return { canTrade: false, reason: `Pause: drawdown ${ddPct.toFixed(1)}% >= ${thresholds.maxDrawdownPct}%`, lotFactor: 0 };
  }
  if (dailyLossPct >= thresholds.maxDailyLossPct) {
    return { canTrade: false, reason: `Daily loss limit: ${dailyLossPct.toFixed(1)}%`, lotFactor: 0 };
  }

  // Progressive lot reduction under drawdown
  let lotFactor = 1.0;
  if (ddPct >= 10)      lotFactor = 0.50;
  else if (ddPct >= 5)  lotFactor = 0.75;

  // Reduce after consecutive losses
  if (state.consecutiveLosses >= 5) lotFactor *= 0.5;

  return { canTrade: true, lotFactor };
}
```

---

## R:R Validation

```typescript
export function validateRR(
  entry: number,
  stop: number,
  target: number,
  direction: 1 | -1,
  minRR = 1.5,
): boolean {
  const risk   = Math.abs(entry - stop);
  const reward = Math.abs(target - entry);
  const correctSide = direction === 1 ? target > entry : target < entry;
  return correctSide && risk > 0 && reward / risk >= minRR;
}
```

**Minimum R:R = 1.5:1.** Preferred = 2:1 or better.

---

## News Risk Filter

```typescript
export const HIGH_IMPACT_EVENTS = ['FOMC', 'NFP', 'CPI', 'PPI', 'GDP', 'FedSpeak'];
const BLACKOUT_BEFORE_MS = 30 * 60_000;
const BLACKOUT_AFTER_MS  = 30 * 60_000;

export function isNearHighImpactNews(
  currentTime: Date,
  eventTimes: Date[],
): boolean {
  const t = currentTime.getTime();
  return eventTimes.some(
    e => t >= e.getTime() - BLACKOUT_BEFORE_MS && t <= e.getTime() + BLACKOUT_AFTER_MS
  );
}
```

---

## Weekend & Time Safety

```typescript
export function isSafeTradingTime(dt: Date): boolean {
  const dow  = dt.getUTCDay(); // 0=Sun, 6=Sat
  const hour = dt.getUTCHours();

  if (dow === 5 && hour >= 18) return false; // Friday close
  if (dow === 6 || dow === 0)  return false; // Weekend
  if (dow === 1 && hour < 2)   return false; // Monday gap zone
  return true;
}
```

---

## MQL5 Circuit Breaker (for live EA)

```mql5
input double MaxDailyLoss_Pct  = 2.0;
input double MaxDrawdown_Pct   = 10.0;
input double KillDrawdown_Pct  = 15.0;

double g_PeakEquity;
double g_DayStartBalance;

bool CheckCircuitBreakers()
{
    double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
    double balance  = AccountInfoDouble(ACCOUNT_BALANCE);

    g_PeakEquity = MathMax(g_PeakEquity, equity);
    double ddPct       = (g_PeakEquity - equity) / g_PeakEquity * 100;
    double dailyLossPct = (g_DayStartBalance - equity) / g_DayStartBalance * 100;

    if (ddPct >= KillDrawdown_Pct)  { Print("KILL SWITCH triggered"); return false; }
    if (ddPct >= MaxDrawdown_Pct)   { Print("Max DD pause");          return false; }
    if (dailyLossPct >= MaxDailyLoss_Pct) { Print("Daily loss limit"); return false; }
    return true;
}
```

---

## Pre-Trade Checklist

Before any live trade, all must be YES:

- [ ] Risk per trade ≤ 1% of balance
- [ ] Stop loss is ≥ 1× ATR from entry (on correct side)
- [ ] R:R ratio ≥ 1.5:1
- [ ] Not within ±30 min of high-impact news
- [ ] Not Friday after 18:00 UTC or weekend
- [ ] Daily loss limit not hit
- [ ] Drawdown circuit breaker not triggered
- [ ] No correlated position already open (XAGUSD, XAUEUR)
