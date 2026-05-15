// Shared types for all XAUUSD R&D code.
// All skill files reference these definitions.

export interface Bar {
  datetime: Date;
  open: number;
  high: number;
  low: number;
  close: number;
  tickVolume: number;
}

export type Session = 'asian' | 'london' | 'new_york' | 'overlap';
export type VolRegime = 'low' | 'medium' | 'high';
export type Direction = 1 | -1 | 0;

export interface BarWithFeatures extends Bar {
  atr14: number;
  atr20: number;
  logReturn: number;
  session: Session;
  volRegime: VolRegime;
  dayOfWeek: number; // 0=Mon, 4=Fri
}

export interface Signal {
  direction: Direction;
  stopPrice: number;
  targetPrice: number;
  barIndex: number;
}

export interface StrategyParams {
  [key: string]: number | boolean | string;
}

export interface BrokerCosts {
  spreadUsd: number;
  commissionPerLot: number;
  slippageUsd: number;
  swapLongPerDay: number;
  swapShortPerDay: number;
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
