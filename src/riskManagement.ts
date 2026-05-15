const CONTRACT_SIZE = 100; // 1 standard lot = 100 troy oz (XAUUSD)

export interface RiskState {
  peakEquity:         number;
  dayStartBalance:    number;
  weekStartBalance:   number;
  consecutiveLosses:  number;
}

export interface CircuitBreakerThresholds {
  maxDailyLossPct:   number;   // default 2%
  maxWeeklyLossPct:  number;   // default 5%
  maxDrawdownPct:    number;   // default 10% — pause
  killDrawdownPct:   number;   // default 15% — hard stop
}

export interface CircuitBreakerResult {
  canTrade:    boolean;
  lotFactor:   number;   // 0 = no trade, 0.5–1.0 = reduced size
  reason?:     string;
}

export const DEFAULT_THRESHOLDS: CircuitBreakerThresholds = {
  maxDailyLossPct:  2.0,
  maxWeeklyLossPct: 5.0,
  maxDrawdownPct:   10.0,
  killDrawdownPct:  15.0,
};

/**
 * Fixed fractional position sizing.
 *
 * Returns 0 when the correct lot size is below the broker minimum.
 * Callers MUST skip the trade when 0 is returned — never round up to minimum,
 * as that would silently violate the risk cap.
 */
export function calculateLotSize(
  accountBalance: number,
  riskPct:        number,       // e.g. 0.01 = 1%
  entryPrice:     number,
  stopLossPrice:  number,
  minLot          = 0.01,
  maxLot          = 10.0,
  lotStep         = 0.01,
): number {
  const priceRisk = Math.abs(entryPrice - stopLossPrice);
  if (priceRisk === 0) return 0;

  const riskUsd      = accountBalance * riskPct;
  const valuePerUnit = priceRisk * CONTRACT_SIZE;   // USD risk per 1 lot
  const rawLot       = riskUsd / valuePerUnit;

  // Round DOWN to lot step
  const steppedLot = Math.floor(rawLot / lotStep) * lotStep;

  // If correct size is below broker minimum, skip the trade
  if (steppedLot < minLot) return 0;

  return Math.min(steppedLot, maxLot);
}

/**
 * Check all circuit breakers and return trading permission + lot reduction factor.
 * Mutates `state` to track running peak equity.
 */
export function checkCircuitBreakers(
  currentEquity: number,
  state:         RiskState,
  thresholds:    CircuitBreakerThresholds = DEFAULT_THRESHOLDS,
): CircuitBreakerResult {
  state.peakEquity = Math.max(state.peakEquity, currentEquity);

  const ddPct         = (state.peakEquity - currentEquity) / state.peakEquity * 100;
  const dailyLossPct  = (state.dayStartBalance  - currentEquity) / state.dayStartBalance  * 100;
  const weeklyLossPct = (state.weekStartBalance - currentEquity) / state.weekStartBalance * 100;

  if (ddPct >= thresholds.killDrawdownPct)
    return { canTrade: false, lotFactor: 0,
             reason: `Kill-switch: drawdown ${ddPct.toFixed(1)}% ≥ ${thresholds.killDrawdownPct}%` };

  if (ddPct >= thresholds.maxDrawdownPct)
    return { canTrade: false, lotFactor: 0,
             reason: `Pause: drawdown ${ddPct.toFixed(1)}% ≥ ${thresholds.maxDrawdownPct}%` };

  if (dailyLossPct >= thresholds.maxDailyLossPct)
    return { canTrade: false, lotFactor: 0,
             reason: `Daily loss limit: ${dailyLossPct.toFixed(1)}%` };

  if (weeklyLossPct >= thresholds.maxWeeklyLossPct)
    return { canTrade: false, lotFactor: 0,
             reason: `Weekly loss limit: ${weeklyLossPct.toFixed(1)}%` };

  // Progressive lot reduction under partial drawdown
  let lotFactor = 1.0;
  if (ddPct >= 10)     lotFactor = 0.50;
  else if (ddPct >= 5) lotFactor = 0.75;

  // Halve size after 5+ consecutive losses
  if (state.consecutiveLosses >= 5) lotFactor *= 0.5;

  return { canTrade: true, lotFactor };
}

/** Validate stop loss is on the correct side and within ATR range. */
export function validateStopLoss(
  entry:       number,
  stop:        number,
  direction:   1 | -1,
  atr:         number,
  minAtrMult   = 1.0,
  maxAtrMult   = 4.0,
): { valid: boolean; atrMultiples: number; reason: string } {
  const priceRisk    = Math.abs(entry - stop);
  const atrMultiples = atr > 0 ? priceRisk / atr : 0;
  const wrongSide    = (direction === 1 && stop >= entry) || (direction === -1 && stop <= entry);
  const tooTight     = atrMultiples < minAtrMult;
  const tooWide      = atrMultiples > maxAtrMult;

  return {
    valid:        !wrongSide && !tooTight && !tooWide,
    atrMultiples: +atrMultiples.toFixed(2),
    reason:       wrongSide ? 'wrong_side' : tooTight ? 'too_tight' : tooWide ? 'too_wide' : 'ok',
  };
}

/** Minimum R:R validation before accepting a trade. */
export function validateRR(
  entry:      number,
  stop:       number,
  target:     number,
  direction:  1 | -1,
  minRR       = 1.5,
): boolean {
  const risk   = Math.abs(entry - stop);
  const reward = Math.abs(target - entry);
  const side   = direction === 1 ? target > entry : target < entry;
  return side && risk > 0 && reward / risk >= minRR;
}

/** News blackout filter — returns false when within ±blackoutMin of any event. */
export function isNearHighImpactNews(
  currentTime:    Date,
  eventTimes:     Date[],
  blackoutMin     = 30,
): boolean {
  const ms = blackoutMin * 60_000;
  const t  = currentTime.getTime();
  return eventTimes.some(e => Math.abs(t - e.getTime()) <= ms);
}

/** Weekend and gap-zone filter. */
export function isSafeTradingTime(dt: Date): boolean {
  const dow  = dt.getUTCDay();
  const hour = dt.getUTCHours();
  if (dow === 5 && hour >= 18) return false;   // Friday close
  if (dow === 6 || dow === 0)  return false;   // Weekend
  if (dow === 1 && hour < 2)   return false;   // Monday gap zone
  return true;
}
