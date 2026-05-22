//+------------------------------------------------------------------+
//|                                                  EA5_DynGrid.mq5  |
//|              Dynamic Grid EA v5 — ATR Adaptive + Smart Martingale |
//|                              NSL Trading                           |
//+------------------------------------------------------------------+
//
//  GRID LOGIC OVERVIEW
//  ───────────────────
//  • Grid step  = avg(ATR[1..3]) × InpATRMultiplier   (dynamic, not fixed)
//  • BUY  grid  : opens as price drops; each new level is gridStep below the lowest open buy
//  • SELL grid  : opens as price rises; each new level is gridStep above the highest open sell
//  • TP per pos : entry ± gridStep × InpTPMultiplier   (set at open, not moved)
//
//  SMART MARTINGALE
//  ────────────────
//  When InpUseSmartMart=true, each new lot is sized to recover ALL existing
//  floating losses of that direction in ONE gridStep move, plus a profit buffer.
//    required_lot = (|floating_loss| + buffer) / (gridStep × pointValue)
//  This self-adjusts to the actual market: large ATR → larger step → smaller lot
//  needed; deep drawdown → larger lot to recover faster.
//  Cap: InpMaxLot prevents runaway sizing.
//
//  CIRCUIT BREAKERS
//  ────────────────
//  • InpGroupTPMoney  : close ALL if total floating profit  ≥ $X
//  • InpGroupSLMoney  : close ALL if total floating loss    ≥ $X (hard stop)
//  • InpMaxDrawdownPct: stop adding levels if equity drawdown ≥ X%
//
//+------------------------------------------------------------------+
#property copyright "NSL Trading"
#property version   "5.00"
#property description "ATR-adaptive grid with smart martingale recovery"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//=== Inputs =========================================================

input group "=== Grid Core ==="
input int    InpATRPeriod      = 14;           // ATR period (dynamic step base)
input double InpATRMultiplier  = 1.5;          // Grid step = avg_ATR × this
input double InpTPMultiplier   = 1.0;          // TP per position = gridStep × this
input int    InpMaxLevels      = 8;            // Max open positions per direction
input bool   InpAllowBuy       = true;         // Enable BUY grid
input bool   InpAllowSell      = true;         // Enable SELL grid

input group "=== Martingale Lot Sizing ==="
input bool   InpUseSmartMart   = true;         // Smart martingale (auto-calc recovery lot)
input double InpBaseLot        = 0.01;         // Base lot (level 1 and fallback)
input double InpLotMultiplier  = 1.5;          // Fixed-multiplier per level (smart=false only)
input double InpMaxLot         = 5.0;          // Hard cap on lot per position
input double InpProfitBuffer   = 1.0;          // Extra profit on recovery (× base lot value)

input group "=== Trend Filter ==="
input bool               InpUseTrend  = true;        // Use EMA trend filter
input int                InpEMAPeriod = 200;          // EMA period
input ENUM_TIMEFRAMES    InpTrendTF   = PERIOD_H4;    // Trend timeframe
// Trend filter logic:
//   price > EMA×(1+0.1%) → uptrend  → BUY  grid only
//   price < EMA×(1-0.1%) → downtrend → SELL grid only
//   else                  → neutral  → both grids active

input group "=== Entry Quality Filter ==="
input bool   InpUseRSI        = false;         // RSI filter for FIRST position only
input int    InpRSIPeriod     = 14;            // RSI period
input double InpRSIOversold   = 40.0;          // Buy first pos: RSI must be below this
input double InpRSIOverbought = 60.0;          // Sell first pos: RSI must be above this

input group "=== Group Risk Management ==="
input double InpGroupTPMoney  = 50.0;          // Close ALL positions if total P&L ≥ $X
input double InpGroupSLMoney  = 200.0;         // Close ALL positions if total loss ≥ $X
input double InpMaxDDPct      = 20.0;          // Pause new levels if equity DD ≥ X%

input group "=== EA Settings ==="
input int    InpMagicNumber   = 54321;         // Magic number
input int    InpSlippage      = 30;            // Max slippage (points)
input string InpComment       = "EA5";         // Order comment prefix

//=== Globals ========================================================
CTrade        g_trade;
CPositionInfo g_pos;
int           g_atrHandle = INVALID_HANDLE;
int           g_emaHandle = INVALID_HANDLE;
int           g_rsiHandle = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
{
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(InpSlippage);
   g_trade.SetTypeFilling(ORDER_FILLING_IOC);

   g_atrHandle = iATR(_Symbol, PERIOD_H1, InpATRPeriod);
   g_emaHandle = iMA(_Symbol, InpTrendTF, InpEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   g_rsiHandle = iRSI(_Symbol, PERIOD_H1, InpRSIPeriod, PRICE_CLOSE);

   if(g_atrHandle == INVALID_HANDLE || g_emaHandle == INVALID_HANDLE || g_rsiHandle == INVALID_HANDLE)
   {
      Alert("EA5: Failed to create indicator handles. Check symbol/timeframe.");
      return INIT_FAILED;
   }

   Print("EA5 DynGrid ready | ", _Symbol,
         " | ATR(", InpATRPeriod, ")×", InpATRMultiplier,
         " | MaxLevels=", InpMaxLevels,
         " | Magic=", InpMagicNumber);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(g_atrHandle);
   IndicatorRelease(g_emaHandle);
   IndicatorRelease(g_rsiHandle);
   Comment("");
}

//====================================================================
//  INDICATOR HELPERS
//====================================================================

// Dynamic grid step: smoothed over last 3 bars to avoid single-bar ATR spikes
double GetGridStep()
{
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(g_atrHandle, 0, 1, 3, buf) < 3)
      return 5.0;
   return ((buf[0] + buf[1] + buf[2]) / 3.0) * InpATRMultiplier;
}

// Returns  1 = uptrend (buy only),  -1 = downtrend (sell only),  0 = neutral (both)
int GetTrend()
{
   if(!InpUseTrend) return 0;
   double ema[];
   ArraySetAsSeries(ema, true);
   if(CopyBuffer(g_emaHandle, 0, 1, 1, ema) < 1) return 0;
   double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(price > ema[0] * 1.001)  return  1;
   if(price < ema[0] * 0.999)  return -1;
   return 0;
}

double GetRSI()
{
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(g_rsiHandle, 0, 1, 1, buf) < 1) return 50.0;
   return buf[0];
}

//====================================================================
//  POSITION HELPERS
//====================================================================

int CountPositions(ENUM_POSITION_TYPE type)
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(g_pos.SelectByIndex(i) && g_pos.Symbol() == _Symbol &&
         g_pos.Magic() == InpMagicNumber && g_pos.PositionType() == type)
         n++;
   return n;
}

double GetLowestBuyPrice()
{
   double v = DBL_MAX;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(g_pos.SelectByIndex(i) && g_pos.Symbol() == _Symbol &&
         g_pos.Magic() == InpMagicNumber && g_pos.PositionType() == POSITION_TYPE_BUY)
         if(g_pos.PriceOpen() < v) v = g_pos.PriceOpen();
   return (v == DBL_MAX) ? 0.0 : v;
}

double GetHighestSellPrice()
{
   double v = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(g_pos.SelectByIndex(i) && g_pos.Symbol() == _Symbol &&
         g_pos.Magic() == InpMagicNumber && g_pos.PositionType() == POSITION_TYPE_SELL)
         if(g_pos.PriceOpen() > v) v = g_pos.PriceOpen();
   return v;
}

// Total floating P&L (profit + swap) for all EA positions
double GetTotalPnL()
{
   double t = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(g_pos.SelectByIndex(i) && g_pos.Symbol() == _Symbol && g_pos.Magic() == InpMagicNumber)
         t += g_pos.Profit() + g_pos.Swap();
   return t;
}

// Total floating P&L for a specific direction
double GetTypePnL(ENUM_POSITION_TYPE type)
{
   double t = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(g_pos.SelectByIndex(i) && g_pos.Symbol() == _Symbol &&
         g_pos.Magic() == InpMagicNumber && g_pos.PositionType() == type)
         t += g_pos.Profit() + g_pos.Swap();
   return t;
}

//====================================================================
//  LOT SIZING
//====================================================================

double NormalizeLot(double raw)
{
   double mn   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx   = MathMin(InpMaxLot, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX));
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   return MathMax(mn, MathMin(mx, MathFloor(raw / step) * step));
}

//  Smart martingale:
//  Calculates the minimum lot that recovers all floating losses of a given direction
//  when price moves exactly ONE gridStep in the favourable direction, plus a profit buffer.
//
//  Formula:
//    value_per_lot = (gridStep / tickSize) × tickValue
//    required_lot  = (|floating_loss| + buffer_value) / value_per_lot
//
double CalcSmartLot(ENUM_POSITION_TYPE type, double gridStep)
{
   double loss = GetTypePnL(type);    // negative when losing
   if(loss >= 0.0) return InpBaseLot; // already profitable or flat → no boost needed

   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize <= 0.0 || tickVal <= 0.0) return InpBaseLot;

   double valuePerLot   = (gridStep / tickSize) * tickVal;
   double bufferValue   = InpBaseLot * valuePerLot * InpProfitBuffer;
   double requiredLot   = (MathAbs(loss) + bufferValue) / valuePerLot;

   return NormalizeLot(requiredLot);
}

// Fixed geometric multiplier (fallback / alternative to smart)
double CalcFixedLot(int level)
{
   return NormalizeLot(InpBaseLot * MathPow(InpLotMultiplier, level - 1));
}

//====================================================================
//  RISK CHECKS
//====================================================================

// Returns false when equity drawdown exceeds the configured limit
bool IsDrawdownOK()
{
   if(InpMaxDDPct <= 0.0) return true;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(bal <= 0.0) return true;
   return ((bal - eq) / bal * 100.0) < InpMaxDDPct;
}

void CloseAll(string reason)
{
   Print("EA5 CloseAll | ", reason, " | PnL=", DoubleToString(GetTotalPnL(), 2));
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(g_pos.SelectByIndex(i) && g_pos.Symbol() == _Symbol && g_pos.Magic() == InpMagicNumber)
         g_trade.PositionClose(g_pos.Ticket());
}

//====================================================================
//  DASHBOARD
//====================================================================

void UpdateDashboard(double step, int trend, double rsi)
{
   string tr = (trend == 1) ? "UP ↑" : (trend == -1) ? "DOWN ↓" : "NEUTRAL ↔";
   Comment(StringFormat(
      "──── EA5 Dynamic Grid ────\n"
      "Symbol   : %s\n"
      "Grid Step: %.2f  (ATR×%.1f)\n"
      "Trend    : %s  (EMA%d %s)\n"
      "RSI(14)  : %.1f\n"
      "BUY  lvl : %d / %d\n"
      "SELL lvl : %d / %d\n"
      "Total PnL: $%.2f\n"
      "Group TP : $%.2f   SL: $%.2f",
      _Symbol,
      step, InpATRMultiplier,
      tr, InpEMAPeriod, EnumToString(InpTrendTF),
      rsi,
      CountPositions(POSITION_TYPE_BUY),  InpMaxLevels,
      CountPositions(POSITION_TYPE_SELL), InpMaxLevels,
      GetTotalPnL(),
      InpGroupTPMoney, InpGroupSLMoney
   ));
}

//====================================================================
//  MAIN TICK
//====================================================================

void OnTick()
{
   double gridStep = GetGridStep();
   int    trend    = GetTrend();
   double rsi      = GetRSI();
   double ask      = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid      = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   UpdateDashboard(gridStep, trend, rsi);

   //--- Group TP / SL circuit breaker ---
   double totalPnL = GetTotalPnL();
   if(InpGroupTPMoney > 0.0 && totalPnL >= InpGroupTPMoney)
   {
      CloseAll("Group TP $" + DoubleToString(InpGroupTPMoney, 2));
      return;
   }
   if(InpGroupSLMoney > 0.0 && totalPnL <= -InpGroupSLMoney)
   {
      CloseAll("Group SL $" + DoubleToString(InpGroupSLMoney, 2));
      return;
   }

   bool ddOK = IsDrawdownOK();

   //------------------------------------------------------------------
   //  BUY GRID
   //  Allowed when: trend is up or neutral, AND InpAllowBuy = true
   //------------------------------------------------------------------
   if(InpAllowBuy && trend >= 0)
   {
      int    buys    = CountPositions(POSITION_TYPE_BUY);
      double lowestP = GetLowestBuyPrice();
      bool   doOpen  = false;

      if(buys == 0)
      {
         // First position — use RSI gate if enabled
         if(!InpUseRSI || rsi <= InpRSIOversold)
            doOpen = true;
      }
      else if(ddOK && buys < InpMaxLevels)
      {
         // Next level — price must fall one full gridStep below the current lowest buy
         // gridStep is recalculated each tick, so the required distance adapts to volatility
         if(ask <= lowestP - gridStep)
            doOpen = true;
      }

      if(doOpen)
      {
         double tp  = NormalizeDouble(ask + gridStep * InpTPMultiplier, _Digits);
         double lot = InpUseSmartMart
                      ? CalcSmartLot(POSITION_TYPE_BUY, gridStep)
                      : CalcFixedLot(buys + 1);

         if(g_trade.Buy(lot, _Symbol, ask, 0, tp, InpComment + "_B"))
            PrintFormat("EA5 BUY  L%d | ask=%.2f tp=%.2f lot=%.2f step=%.2f",
                        buys + 1, ask, tp, lot, gridStep);
      }
   }

   //------------------------------------------------------------------
   //  SELL GRID
   //  Allowed when: trend is down or neutral, AND InpAllowSell = true
   //------------------------------------------------------------------
   if(InpAllowSell && trend <= 0)
   {
      int    sells    = CountPositions(POSITION_TYPE_SELL);
      double highestP = GetHighestSellPrice();
      bool   doOpen   = false;

      if(sells == 0)
      {
         if(!InpUseRSI || rsi >= InpRSIOverbought)
            doOpen = true;
      }
      else if(ddOK && sells < InpMaxLevels)
      {
         if(bid >= highestP + gridStep)
            doOpen = true;
      }

      if(doOpen)
      {
         double tp  = NormalizeDouble(bid - gridStep * InpTPMultiplier, _Digits);
         double lot = InpUseSmartMart
                      ? CalcSmartLot(POSITION_TYPE_SELL, gridStep)
                      : CalcFixedLot(sells + 1);

         if(g_trade.Sell(lot, _Symbol, bid, 0, tp, InpComment + "_S"))
            PrintFormat("EA5 SELL L%d | bid=%.2f tp=%.2f lot=%.2f step=%.2f",
                        sells + 1, bid, tp, lot, gridStep);
      }
   }
}
