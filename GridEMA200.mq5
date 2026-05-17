//+------------------------------------------------------------------+
//|                                              GridEMA200.mq5       |
//|                Grid Trading EA with EMA 200 Directional Bias      |
//|                                                                    |
//|  Logic:                                                            |
//|  - Opens both BUY and SELL simultaneously on start                |
//|  - EMA 200 bias: price > EMA → BUY 3x lot, SELL 1x lot           |
//|                  price < EMA → SELL 3x lot, BUY 1x lot           |
//|  - Adds new BUY+SELL together every PipStep pips (either dir)    |
//|  - Closes all when total profit >= ProfitTarget                   |
//|  - Stops trading when loss >= LossPercent% of balance             |
//+------------------------------------------------------------------+
#property copyright "NSL Trading"
#property version   "1.00"
#property description "Grid EA with EMA 200 bias"

#include <Trade\Trade.mqh>

//--- Input Parameters
input group "=== Grid Settings ==="
input double InpStartLot      = 0.01;   // Base lot size
input int    InpPipStep       = 50;     // Grid step (pips)
input double InpLotMultiplier = 1.5;    // Lot multiplier per new level
input int    InpEMAPeriod     = 200;    // EMA period

input group "=== Exit Settings ==="
input double InpProfitTarget  = 50.0;   // Profit target to close all ($)
input double InpLossPercent   = 20.0;   // Max loss % of balance before stop

input group "=== EA Settings ==="
input int    InpMagicNumber   = 202506; // Magic number (unique per EA instance)
input int    InpSlippage      = 20;     // Slippage in points

//--- Globals
CTrade   trade;
int      emaHandle   = INVALID_HANDLE;
double   pipSize     = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   emaHandle = iMA(_Symbol, PERIOD_CURRENT, InpEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(emaHandle == INVALID_HANDLE)
   {
      Alert("GridEMA200: Failed to create EMA indicator!");
      return INIT_FAILED;
   }

   // Handle 3/5-digit brokers (EURUSD, USDJPY)
   pipSize = _Point * ((_Digits == 5 || _Digits == 3) ? 10 : 1);

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippage);

   Print("GridEMA200 initialized | Symbol=", _Symbol,
         " | PipSize=", pipSize,
         " | Step=", InpPipStep, " pips",
         " | Magic=", InpMagicNumber);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(emaHandle != INVALID_HANDLE)
      IndicatorRelease(emaHandle);
}

//+------------------------------------------------------------------+
void OnTick()
{
   // Get current EMA value
   double ema[1];
   if(CopyBuffer(emaHandle, 0, 0, 1, ema) != 1) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double emaVal = ema[0];

   int posCount = CountPositions();

   //--- No positions: open initial grid
   if(posCount == 0)
   {
      OpenInitialGrid(ask, bid, emaVal);
      return;
   }

   //--- Check total profit / loss
   double totalProfit = GetTotalProfit();
   double balance     = AccountInfoDouble(ACCOUNT_BALANCE);

   if(totalProfit >= InpProfitTarget)
   {
      CloseAll(StringFormat("Profit target hit: $%.2f", totalProfit));
      return;
   }

   double maxLoss = balance * InpLossPercent / 100.0;
   if(totalProfit <= -maxLoss)
   {
      CloseAll(StringFormat("Loss limit hit: $%.2f (limit: $%.2f)", totalProfit, -maxLoss));
      Print("GridEMA200: Loss limit hit. Closed all. Will reopen on next tick.");
      return;
   }

   //--- Expand grid if price moved far enough
   ExpandGrid(ask, bid);
}

//+------------------------------------------------------------------+
//| Open initial BUY + SELL based on EMA 200 bias                    |
//+------------------------------------------------------------------+
void OpenInitialGrid(double ask, double bid, double ema)
{
   double buyLot, sellLot;

   // Use bid (chart price) for EMA comparison — ask inflates the comparison
   if(bid > ema)
   {
      // Price above EMA → uptrend bias → buy 3x, sell 1x
      buyLot  = NormalizeLot(InpStartLot * 3.0);
      sellLot = NormalizeLot(InpStartLot * 1.0);
   }
   else
   {
      // Price below EMA → downtrend bias → sell 3x, buy 1x
      buyLot  = NormalizeLot(InpStartLot * 1.0);
      sellLot = NormalizeLot(InpStartLot * 3.0);
   }

   if(trade.Buy(buyLot, _Symbol, ask, 0, 0, "Grid-Buy"))
      Print("Initial BUY: lot=", buyLot, " @ ", ask,
            " | EMA=", ema, " | bias=", (bid > ema ? "LONG" : "SHORT"));
   else
      Print("Initial BUY failed: ", trade.ResultComment());

   if(trade.Sell(sellLot, _Symbol, bid, 0, 0, "Grid-Sell"))
      Print("Initial SELL: lot=", sellLot, " @ ", bid);
   else
      Print("Initial SELL failed: ", trade.ResultComment());
}

//+------------------------------------------------------------------+
//| Open new grid level (BUY + SELL) when price moves PipStep away  |
//+------------------------------------------------------------------+
void ExpandGrid(double ask, double bid)
{
   double lastBuyLot   = 0;
   double lastSellLot  = 0;
   double lastBuyPrice = 0;
   datetime lastBuyTime  = 0;
   datetime lastSellTime = 0;

   // Find the most recently opened buy and sell lots + last buy open price
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      double   lot      = PositionGetDouble(POSITION_VOLUME);
      double   price    = PositionGetDouble(POSITION_PRICE_OPEN);
      long     posType  = PositionGetInteger(POSITION_TYPE);

      if(posType == POSITION_TYPE_BUY && openTime >= lastBuyTime)
      {
         lastBuyTime  = openTime;
         lastBuyLot   = lot;
         lastBuyPrice = price;
      }
      else if(posType == POSITION_TYPE_SELL && openTime >= lastSellTime)
      {
         lastSellTime = openTime;
         lastSellLot  = lot;
      }
   }

   if(lastBuyPrice == 0) return;

   double mid      = (ask + bid) / 2.0;
   double stepSize = InpPipStep * pipSize;

   // Open new BUY + SELL together when price moves stepSize from last level
   if(MathAbs(mid - lastBuyPrice) >= stepSize)
   {
      double newBuyLot  = NormalizeLot(lastBuyLot  * InpLotMultiplier);
      double newSellLot = NormalizeLot(lastSellLot * InpLotMultiplier);

      if(trade.Buy(newBuyLot, _Symbol, ask, 0, 0, "Grid-Buy"))
         Print("Grid BUY added: lot=", newBuyLot, " @ ", ask,
               " | prev=", lastBuyPrice);
      else
         Print("Grid BUY failed: ", trade.ResultComment());

      if(trade.Sell(newSellLot, _Symbol, bid, 0, 0, "Grid-Sell"))
         Print("Grid SELL added: lot=", newSellLot, " @ ", bid,
               " | prev=", lastBuyPrice);
      else
         Print("Grid SELL failed: ", trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
//| Close all positions opened by this EA                            |
//+------------------------------------------------------------------+
void CloseAll(string reason)
{
   Print("CloseAll triggered: ", reason);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      if(!trade.PositionClose(ticket))
         Print("Close failed for ticket ", ticket, ": ", trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
//| Sum profit + swap for all EA positions                           |
//+------------------------------------------------------------------+
double GetTotalProfit()
{
   double profit = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      profit += PositionGetDouble(POSITION_PROFIT);
      profit += PositionGetDouble(POSITION_SWAP);
   }
   return profit;
}

//+------------------------------------------------------------------+
//| Count open positions belonging to this EA                        |
//+------------------------------------------------------------------+
int CountPositions()
{
   int count = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Normalize lot to broker constraints                              |
//+------------------------------------------------------------------+
double NormalizeLot(double lot)
{
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   lot = MathFloor(lot / lotStep) * lotStep;
   return MathMax(minLot, MathMin(maxLot, lot));
}
//+------------------------------------------------------------------+
