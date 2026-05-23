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
//  MARTINGALE LOT SIZING
//  ──────────────────────────────────────────────────────────
//  Lot multiplies by InpTierMultiplier for each successive grid level.
//  When ALL positions close (cycle ends), lot resets to InpBaseLot.
//  Example  BaseLot=0.01  LotMultiplier=2.0 :
//    Level 1 → 0.01   Level 2 → 0.02   Level 3 → 0.04 …
//  Cap: InpMaxLot hard-limits the lot regardless of level.
//
//  CIRCUIT BREAKERS
//  ────────────────
//  • InpGroupTPMoney  : close ALL if total floating profit  ≥ $X
//  • InpGroupSLMoney  : close ALL if total floating loss    ≥ $X (hard stop)
//  • InpMaxDDPct      : stop adding levels if equity drawdown ≥ X%
//
//  CSV REPORT
//  ──────────
//  File: <Common>\EA5_DynGrid_<Symbol>_<Magic>.csv
//  One row is written for each OPEN and each CLOSE event.
//  Columns: Event, DateTime, Ticket, PositionID, Symbol, Direction, Lot,
//           OpenTime, OpenPrice, TP, CloseTime, ClosePrice,
//           Profit, Swap, Commission, NetPnL, Pips,
//           GridLevel, ATRStep, CloseReason
//  Trade comment format embedded by EA: "EA5_B_L2_S8.64"
//   └ B/S = direction,  L2 = grid level,  S8.64 = ATR step at open
//
//+------------------------------------------------------------------+
#property copyright "NSL Trading"
#property version   "5.00"
#property description "ATR-adaptive grid with smart martingale recovery + CSV reporting"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//=== Inputs =========================================================

// TUNED defaults — derived from 25,915 bars of GOLDM# H1 (Jan 2022 – May 2026)
//   ATR Multiplier 1.2x  : 70% TP hit rate within 20 bars; p99 max depth = 2
//   MaxLevels 5          : p99 depth never exceeds 2, so 5 covers extreme outliers only
//   AllowSell false      : GOLDM# averages +0.88 pts per 20 bars even below EMA → sell grid loses edge
//   TP Multiplier 1.5x   : higher total pips than 1.0x over full dataset
//   ATR volatility range : 1.5 (2022 quiet) → 28.4 (2026 volatile) — dynamic step handles this automatically

input group "=== Grid Core ==="
input int    InpATRPeriod      = 14;           // ATR period (dynamic step base)
input double InpATRMultiplier  = 1.2;          // Grid step = avg_ATR × this  [data: 1.2x best TP/depth trade-off]
input double InpTPMultiplier   = 1.5;          // TP per position = gridStep × this  [data: 1.5x > 1.0x total pips]
input int    InpMaxLevels      = 5;            // Max open positions per direction  [data: p99 depth = 2, 5 = safe buffer]
input bool   InpAllowBuy       = true;         // Enable BUY grid
input bool   InpAllowSell      = false;        // Enable SELL grid  [data: GOLDM# bullish even below EMA — sell loses edge]

input group "=== Martingale Lot Sizing ==="
// Martingale: lot multiplies per grid level within a cycle.
// When ALL positions close (cycle ends), lot resets to BaseLot for the next cycle.
// Example  BaseLot=0.01  LotMultiplier=2.0 :
//   Level 1 → 0.01   Level 2 → 0.02   Level 3 → 0.04   Level 4 → 0.08 …
// WARNING: lot grows exponentially with levels — keep MaxLevels low.
input double InpBaseLot        = 0.01;         // Starting lot size (reset every new cycle)
input double InpTierMultiplier = 2.0;          // Lot multiplier per grid level (martingale)
input double InpMaxLot         = 5.0;          // Hard cap on lot per position

input group "=== Trend Filter ==="
input bool               InpUseTrend  = true;        // Use EMA trend filter
input int                InpEMAPeriod = 200;          // EMA period  [data: confirmed effective]
input ENUM_TIMEFRAMES    InpTrendTF   = PERIOD_H4;    // Trend timeframe
// Trend filter logic:
//   price > EMA×(1+0.1%) → uptrend  → BUY  grid only
//   price < EMA×(1-0.1%) → downtrend → SELL grid only (only if AllowSell=true)
//   else                  → neutral  → both active

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
string        g_csvFile   = "";               // set in OnInit

// Keep filename safe across brokers/symbol formats (e.g. BTC/USD, XAUUSD.r)
string SanitizeFileToken(string s)
{
   StringReplace(s, "#", "");
   StringReplace(s, ".", "");
   StringReplace(s, " ", "");
   StringReplace(s, "/", "_");
   StringReplace(s, "\\", "_");
   StringReplace(s, ":", "_");
   StringReplace(s, "*", "_");
   StringReplace(s, "?", "_");
   StringReplace(s, "\"", "_");
   StringReplace(s, "<", "_");
   StringReplace(s, ">", "_");
   StringReplace(s, "|", "_");
   if(StringLen(s) == 0)
      s = "SYMBOL";
   return s;
}

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

   // Build CSV filename: EA5_DynGrid_GOLDM_54321.csv
   string sym = SanitizeFileToken(_Symbol);
   g_csvFile = StringFormat("EA5_DynGrid_%s_%d.csv", sym, InpMagicNumber);
   InitCSV();

   // Print the full CSV path to Experts log so you can always find it
   string dataPath = TerminalInfoString(TERMINAL_DATA_PATH);
   Print("EA5 DynGrid ready | ", _Symbol,
         " | ATR(", InpATRPeriod, ")×", InpATRMultiplier,
         " | MaxLevels=", InpMaxLevels,
         " | Magic=", InpMagicNumber);
   Print("EA5 CSV full path: ", dataPath, "\\MQL5\\Files\\", g_csvFile);
   if((bool)MQLInfoInteger(MQL_TESTER))
      Print("EA5 note: running in Strategy Tester, file is under the tester agent data folder shown above.");
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
//  CSV REPORTING
//====================================================================

#define CSV_HEADER "Event,DateTime,Ticket,PositionID,Symbol,Direction,Lot,"\
                   "OpenTime,OpenPrice,TP,"\
                   "CloseTime,ClosePrice,"\
                   "Profit,Swap,Commission,NetPnL,Pips,"\
                   "GridLevel,ATRStep,CloseReason"

// Ensure the CSV file exists with a valid header.
// Called from OnInit — also callable from WriteCSVRow as a safety net.
void InitCSV()
{
   string dataPath = TerminalInfoString(TERMINAL_DATA_PATH);
   string fullPath = dataPath + "\\MQL5\\Files\\" + g_csvFile;

   // --- Try open for read (safest existence check) ---
   int h = FileOpen(g_csvFile, FILE_READ | FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      // File exists — just confirm and exit
      FileClose(h);
      Print("EA5 CSV ready (exists): ", fullPath);
      return;
   }

   // --- File does not exist — create it ---
   h = FileOpen(g_csvFile, FILE_WRITE | FILE_ANSI);
   if(h == INVALID_HANDLE)
   {
      int err = GetLastError();
      Print("EA5 CSV ERROR: cannot create file"
            " | path=", fullPath,
            " | error=", err,
            " | hint: check MQL5\\Files\\ folder permissions");
      Alert("EA5: CSV file could not be created. See Experts log. Error=" + IntegerToString(err));
      return;
   }

   FileWriteString(h, CSV_HEADER + "\n");
   FileClose(h);
   Print("EA5 CSV created: ", fullPath);
}

// Append one CSV line and force it to disk immediately.
// Re-creates the file if it was deleted while EA is running.
void WriteCSVRow(const string &line)
{
   string dataPath = TerminalInfoString(TERMINAL_DATA_PATH);
   string fullPath = dataPath + "\\MQL5\\Files\\" + g_csvFile;

   // Safety net: if file was manually deleted, recreate it
   int chk = FileOpen(g_csvFile, FILE_READ | FILE_ANSI);
   if(chk == INVALID_HANDLE)
   {
      Print("EA5 CSV: file missing — recreating | path=", fullPath);
      InitCSV();
   }
   else
      FileClose(chk);

   // Append
   int h = FileOpen(g_csvFile, FILE_READ | FILE_WRITE | FILE_ANSI);
   if(h == INVALID_HANDLE)
   {
      Print("EA5 CSV: cannot open for append | path=", fullPath, " | error=", GetLastError());
      return;
   }
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, line + "\n");
   FileFlush(h);  // ensure row is persisted immediately (especially for CLOSE events)
   FileClose(h);
}

// Parse "_Ln" and "_Ss.ss" tokens from a trade comment string
// Comment format example: "EA5_B_L2_S8.64"
void ParseComment(const string &comment, int &level, double &step)
{
   level = 0;
   step  = 0.0;

   int lIdx = StringFind(comment, "_L");
   int sIdx = StringFind(comment, "_S");

   // _L must come before _S
   if(lIdx >= 0 && sIdx > lIdx)
   {
      level = (int)StringToInteger(StringSubstr(comment, lIdx + 2, sIdx - lIdx - 2));
      step  = StringToDouble(StringSubstr(comment, sIdx + 2));
   }
}

//+------------------------------------------------------------------+
//  OnTradeTransaction — fires for every server-side deal event.
//  We intercept DEAL_ADD for our magic number and write to CSV.
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest     &request,
                        const MqlTradeResult      &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;

   // Load deal into history context
   if(!HistoryDealSelect(trans.deal)) return;

   // Filter: only our EA on this symbol
   if((long)HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != InpMagicNumber) return;
   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol) return;

   ENUM_DEAL_ENTRY dealEntry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(dealEntry != DEAL_ENTRY_IN &&
      dealEntry != DEAL_ENTRY_OUT &&
      dealEntry != DEAL_ENTRY_OUT_BY) return;

   // --- Common deal fields ---
   ulong          ticket     = (ulong)HistoryDealGetInteger(trans.deal, DEAL_TICKET);
   ulong          posId      = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
   ENUM_DEAL_TYPE dealType   = (ENUM_DEAL_TYPE)HistoryDealGetInteger(trans.deal, DEAL_TYPE);
   double         lot        = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
   double         price      = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
   double         profit     = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
   double         swap       = HistoryDealGetDouble(trans.deal, DEAL_SWAP);
   double         commission = HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
   datetime       dt         = (datetime)HistoryDealGetInteger(trans.deal, DEAL_TIME);
   string         comment    = HistoryDealGetString(trans.deal, DEAL_COMMENT);

   int    gridLevel = 0;
   double atrStep   = 0.0;
   ParseComment(comment, gridLevel, atrStep);

   //------------------------------------------------------------------
   //  OPEN event  (DEAL_ENTRY_IN)
   //------------------------------------------------------------------
   if(dealEntry == DEAL_ENTRY_IN)
   {
      // Direction of the position that was just opened
      string dirStr = (dealType == DEAL_TYPE_BUY) ? "BUY" : "SELL";

      // Read TP from the live position (it exists now)
      double tpPrice = 0.0;
      if(PositionSelectByTicket(posId))
         tpPrice = PositionGetDouble(POSITION_TP);

      string row = StringFormat(
         "OPEN,%s,%llu,%llu,%s,%s,%.2f,"   // event…lot
         "%s,%.5f,%.5f,"                    // openTime, openPrice, TP
         ",,"                               // closeTime, closePrice (empty)
         ",,,,"                             // profit, swap, commission, netPnL (empty)
         ","                                // pips (empty)
         "%d,%.4f,",                        // gridLevel, atrStep, closeReason (empty)
         TimeToString(dt, TIME_DATE | TIME_SECONDS),
         ticket, posId,
         _Symbol, dirStr, lot,
         TimeToString(dt, TIME_DATE | TIME_SECONDS), price, tpPrice,
         gridLevel, atrStep
      );
      WriteCSVRow(row);
      return;
   }

   //------------------------------------------------------------------
   //  CLOSE event  (DEAL_ENTRY_OUT / DEAL_ENTRY_OUT_BY)
   //------------------------------------------------------------------

   // Direction of the original (now-closed) position:
   //   closing a BUY position → deal type is SELL → original dir is BUY
   string dirStr = (dealType == DEAL_TYPE_SELL) ? "BUY" : "SELL";

   // Find the matching OPEN deal to get entry price, open time, level, step
   datetime openTime  = dt;
   double   openPrice = price;
   double   openLot   = lot;
   int      openLevel = gridLevel;
   double   openStep  = atrStep;
   double   openTP    = 0.0;

   HistorySelect(0, TimeCurrent());
   int totalDeals = HistoryDealsTotal();
   for(int i = 0; i < totalDeals; i++)
   {
      ulong d = HistoryDealGetTicket(i);
      if((ulong)HistoryDealGetInteger(d, DEAL_POSITION_ID) != posId) continue;
      if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;

      openTime  = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
      openPrice = HistoryDealGetDouble(d, DEAL_PRICE);
      openLot   = HistoryDealGetDouble(d, DEAL_VOLUME);

      // Level and step always come from the OPEN deal comment
      string oc = HistoryDealGetString(d, DEAL_COMMENT);
      ParseComment(oc, openLevel, openStep);
      break;
   }

   // Pips: positive = profitable
   double pips = (dirStr == "BUY") ? (price - openPrice) : (openPrice - price);

   // Net P&L
   double netPnL = profit + swap + commission;

   // Close reason — infer from the closing deal comment
   string closeReason = "manual";
   {
      string lc = comment;
      StringToLower(lc);
      if(StringFind(lc, "[tp]")  >= 0 || StringFind(lc, "tp") >= 0)  closeReason = "TP";
      else if(StringFind(lc, "[sl]") >= 0 || StringFind(lc, "sl") >= 0) closeReason = "SL";
      else if(StringFind(lc, "group") >= 0)                           closeReason = "GroupClose";
      else if(StringFind(lc, "so") >= 0 || StringFind(lc, "margin") >= 0) closeReason = "StopOut";
   }

   string row = StringFormat(
      "CLOSE,%s,%llu,%llu,%s,%s,%.2f,"     // event…lot
      "%s,%.5f,%.5f,"                       // openTime, openPrice, TP (blank — already in OPEN row)
      "%s,%.5f,"                            // closeTime, closePrice
      "%.2f,%.2f,%.2f,%.2f,%.4f,"           // profit, swap, commission, netPnL, pips
      "%d,%.4f,%s",                         // gridLevel, atrStep, closeReason
      TimeToString(dt, TIME_DATE | TIME_SECONDS),
      ticket, posId,
      _Symbol, dirStr, openLot,
      TimeToString(openTime, TIME_DATE | TIME_SECONDS), openPrice, openTP,
      TimeToString(dt, TIME_DATE | TIME_SECONDS), price,
      profit, swap, commission, netPnL, pips,
      openLevel, openStep, closeReason
   );
   WriteCSVRow(row);
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


//====================================================================
//  LOT SIZING  —  Martingale per level, reset on new cycle
//====================================================================

double NormalizeLot(double raw)
{
   double mn   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx   = MathMin(InpMaxLot, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX));
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   return MathMax(mn, MathMin(mx, MathFloor(raw / step) * step));
}

// ★ MAIN LOT FUNCTION ★
//
// Martingale: lot = BaseLot × LotMultiplier ^ openCount
//   openCount=0 (new cycle)  → BaseLot × 2^0 = 0.01  ← resets here
//   openCount=1 (level 2)    → BaseLot × 2^1 = 0.02
//   openCount=2 (level 3)    → BaseLot × 2^2 = 0.04
//   openCount=3 (level 4)    → BaseLot × 2^3 = 0.08
//
// When all positions close, openCount drops to 0 → next open always starts at BaseLot.
//
double GetCycleLot(ENUM_POSITION_TYPE type)
{
   int openCount = CountPositions(type);
   return NormalizeLot(InpBaseLot * MathPow(InpTierMultiplier, openCount));
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
   string tr       = (trend == 1) ? "UP ↑" : (trend == -1) ? "DOWN ↓" : "NEUTRAL ↔";
   int    buyLvl   = CountPositions(POSITION_TYPE_BUY);
   int    sellLvl  = CountPositions(POSITION_TYPE_SELL);
   // Current lot = what the NEXT open would use (level after the existing ones)
   double buyLot   = GetCycleLot(POSITION_TYPE_BUY);
   double sellLot  = GetCycleLot(POSITION_TYPE_SELL);

   Comment(StringFormat(
      "──── EA5 Dynamic Grid ────\n"
      "Symbol   : %s\n"
      "Grid Step: %.2f  (ATR×%.1f)\n"
      "Trend    : %s  (EMA%d %s)\n"
      "RSI(14)  : %.1f\n"
      "BUY  lvl : %d / %d  next lot=%.2f\n"
      "SELL lvl : %d / %d  next lot=%.2f\n"
      "Total PnL: $%.2f\n"
      "Group TP : $%.2f   SL: $%.2f\n"
      "── Martingale ──\n"
      "Base Lot : %.2f  Multiplier: %.1f\n"
      "L1=%.2f  L2=%.2f  L3=%.2f  L4=%.2f\n"
      "CSV      : %s",
      _Symbol,
      step, InpATRMultiplier,
      tr, InpEMAPeriod, EnumToString(InpTrendTF),
      rsi,
      buyLvl,  InpMaxLevels, buyLot,
      sellLvl, InpMaxLevels, sellLot,
      GetTotalPnL(),
      InpGroupTPMoney, InpGroupSLMoney,
      InpBaseLot, InpTierMultiplier,
      NormalizeLot(InpBaseLot * MathPow(InpTierMultiplier, 0)),
      NormalizeLot(InpBaseLot * MathPow(InpTierMultiplier, 1)),
      NormalizeLot(InpBaseLot * MathPow(InpTierMultiplier, 2)),
      NormalizeLot(InpBaseLot * MathPow(InpTierMultiplier, 3)),
      g_csvFile
   ));
}

//====================================================================
//  BUILD TRADE COMMENT
//  Encodes direction, grid level, and ATR step so OnTradeTransaction
//  can read them back without needing extra global state.
//  Format: "EA5_B_L2_S8.64"
//====================================================================
string BuildComment(string dir, int level, double step)
{
   return StringFormat("%s_%s_L%d_S%.2f", InpComment, dir, level, step);
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
         double tp      = NormalizeDouble(ask + gridStep * InpTPMultiplier, _Digits);
         double lot     = GetCycleLot(POSITION_TYPE_BUY);
         string cmt     = BuildComment("B", buys + 1, gridStep);  // "EA5_B_L2_S8.64"

         if(g_trade.Buy(lot, _Symbol, ask, 0, tp, cmt))
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
         double tp      = NormalizeDouble(bid - gridStep * InpTPMultiplier, _Digits);
         double lot     = GetCycleLot(POSITION_TYPE_SELL);
         string cmt     = BuildComment("S", sells + 1, gridStep);  // "EA5_S_L1_S12.30"

         if(g_trade.Sell(lot, _Symbol, bid, 0, tp, cmt))
            PrintFormat("EA5 SELL L%d | bid=%.2f tp=%.2f lot=%.2f step=%.2f",
                        sells + 1, bid, tp, lot, gridStep);
      }
   }
}
