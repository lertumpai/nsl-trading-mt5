//+------------------------------------------------------------------+
//|                                              GridEMA200.mq5       |
//|                Grid Trading EA with EMA 200 Directional Bias      |
//|                                                                    |
//|  Logic:                                                            |
//|  - Opens both BUY and SELL simultaneously on start                |
//|  - EMA 200 bias: price > EMA → BUY 3x lot, SELL 1x lot           |
//|                  price < EMA → SELL 3x lot, BUY 1x lot           |
//|  - Adds new grid orders every PipStep pips in each direction      |
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
bool     tradingStopped = false;  // set true when loss limit hit, requires manual reset

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
   if(tradingStopped) return;

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
      tradingStopped = true;
      Alert("GridEMA200: Loss limit reached. EA stopped. Reset tradingStopped manually.");
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

   if(ask > ema)
   {
      // Price above EMA → trend up → heavy buy, light sell
      buyLot  = NormalizeLot(InpStartLot * 3.0);
      sellLot = NormalizeLot(InpStartLot * 1.0);
   }
   else
   {
      // Price below EMA → trend down → heavy sell, light buy
      buyLot  = NormalizeLot(InpStartLot * 1.0);
      sellLot = NormalizeLot(InpStartLot * 3.0);
   }

   if(trade.Buy(buyLot, _Symbol, ask, 0, 0, "Grid-Buy"))
      Print("Initial BUY: lot=", buyLot, " @ ", ask,
            " | EMA=", ema, " | bias=", (ask > ema ? "LONG" : "SHORT"));
   else
      Print("Initial BUY failed: ", trade.ResultComment());

   if(trade.Sell(sellLot, _Symbol, bid, 0, 0, "Grid-Sell"))
      Print("Initial SELL: lot=", sellLot, " @ ", bid);
   else
      Print("Initial SELL failed: ", trade.ResultComment());
}

//+------------------------------------------------------------------+
//| Add new grid levels if price has moved PipStep pips              |
//+------------------------------------------------------------------+
void ExpandGrid(double ask, double bid)
{
   double maxBuyPrice  = 0;
   double maxBuyLot    = 0;
   double minSellPrice = DBL_MAX;
   double minSellLot   = 0;

   // Scan positions to find the frontier levels
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double lot       = PositionGetDouble(POSITION_VOLUME);
      long   posType   = PositionGetInteger(POSITION_TYPE);

      if(posType == POSITION_TYPE_BUY)
      {
         if(openPrice > maxBuyPrice)
         {
            maxBuyPrice = openPrice;
            maxBuyLot   = lot;
         }
      }
      else if(posType == POSITION_TYPE_SELL)
      {
         if(openPrice < minSellPrice)
         {
            minSellPrice = openPrice;
            minSellLot   = lot;
         }
      }
   }

   double stepSize = InpPipStep * pipSize;

   // Expand BUY grid upward
   if(maxBuyPrice > 0 && ask >= maxBuyPrice + stepSize)
   {
      double newLot = NormalizeLot(maxBuyLot * InpLotMultiplier);
      if(trade.Buy(newLot, _Symbol, ask, 0, 0, "Grid-Buy"))
         Print("Grid BUY added: lot=", newLot, " @ ", ask,
               " | prev level=", maxBuyPrice);
      else
         Print("Grid BUY failed: ", trade.ResultComment());
   }

   // Expand SELL grid downward
   if(minSellPrice < DBL_MAX && bid <= minSellPrice - stepSize)
   {
      double newLot = NormalizeLot(minSellLot * InpLotMultiplier);
      if(trade.Sell(newLot, _Symbol, bid, 0, 0, "Grid-Sell"))
         Print("Grid SELL added: lot=", newLot, " @ ", bid,
               " | prev level=", minSellPrice);
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
