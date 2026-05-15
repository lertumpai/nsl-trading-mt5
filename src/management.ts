/** ATR-based stop loss — placed beyond noise, not at an arbitrary distance. */
export function atrStop(entry: number, direction: 1 | -1, atr: number, multiplier = 1.5): number {
  return entry - direction * atr * multiplier;
}

/** Fixed R-multiple take profit. */
export function fixedRTarget(entry: number, stop: number, rMultiple = 2.0): number {
  const risk = Math.abs(entry - stop);
  return entry > stop
    ? entry + risk * rMultiple   // long
    : entry - risk * rMultiple;  // short
}

/** ATR-based take profit (alternative to fixed R). */
export function atrTarget(entry: number, direction: 1 | -1, atr: number, multiplier = 3.0): number {
  return entry + direction * atr * multiplier;
}

/** Validate R:R ratio before entering a trade. */
export function validateRR(
  entry:     number,
  stop:      number,
  target:    number,
  direction: 1 | -1,
  minRR      = 1.5,
): boolean {
  const risk   = Math.abs(entry - stop);
  const reward = Math.abs(target - entry);
  const correctSide = direction === 1 ? target > entry : target < entry;
  return correctSide && risk > 0 && reward / risk >= minRR;
}

/** Trailing stop — moves stop to lock in profit as price advances. */
export function trailingStop(
  currentStop: number,
  currentPrice: number,
  direction:    1 | -1,
  atr:          number,
  trailMult     = 1.5,
): number {
  const newStop = currentPrice - direction * atr * trailMult;
  // Only move in the direction of profit, never against
  return direction === 1
    ? Math.max(currentStop, newStop)
    : Math.min(currentStop, newStop);
}
