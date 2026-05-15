# Skill: MQL5 EA Coding Standards

> Use this skill when writing or reviewing MQL5 code. Python research → MQL5 production is a one-way translation. Get it right in Python first.

---

## File Structure Convention

```
ea/
├── STR_001_LondonBreakout/
│   ├── STR_001_LondonBreakout.mq5     ← Main EA file
│   ├── STR_001_LondonBreakout.ex5     ← Compiled (auto-generated)
│   └── include/
│       ├── RiskManager.mqh            ← Risk management include
│       └── Signals.mqh               ← Signal generation include
```

---

## EA Template

```mql5
//+------------------------------------------------------------------+
//| STR_001 — London Breakout EA                                     |
//| Hypothesis: HYP-001                                              |
//| Backtest Result: Sharpe 1.72, DD 8.4%, PF 1.65 (OOS)            |
//| Last Updated: YYYY-MM-DD                                         |
//+------------------------------------------------------------------+
#property copyright "NSL Trading"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//--- Input Parameters (all configurable externally)
input group "Strategy Parameters"
input int    ATR_Period       = 14;       // ATR period
input double ATR_SL_Mult     = 1.5;      // Stop loss ATR multiplier
input double ATR_TP_Mult     = 3.0;      // Take profit ATR multiplier
input int    EMA_Fast        = 50;       // Fast EMA period
input int    EMA_Slow        = 200;      // Slow EMA period

input group "Risk Management"
input double RiskPerTrade_Pct = 1.0;     // Risk per trade (% of balance)
input double MaxDailyLoss_Pct = 2.0;     // Daily loss limit (% of balance)
input double MaxDrawdown_Pct  = 10.0;    // Max drawdown before halt (%)
input double Max_Spread_USD   = 1.5;     // Skip trade if spread > this (USD)

input group "Session Filter"
input bool   Trade_London    = true;     // Trade London session
input bool   Trade_NewYork   = true;     // Trade New York session
input bool   Trade_Overlap   = true;     // Trade overlap session
input bool   Trade_Asian     = false;    // Trade Asian session (usually false)

input group "News Filter"
input bool   Filter_News     = true;     // Enable news blackout filter
input int    News_Before_Min = 30;       // Minutes before event to avoid
input int    News_After_Min  = 30;       // Minutes after event to avoid

//--- Global Objects
CTrade trade;
double gPeakEquity;
double gDayStartBalance;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
    trade.SetExpertMagicNumber(20260101);
    trade.SetDeviationInPoints(30);   // max slippage 3 pips
    trade.SetTypeFilling(ORDER_FILLING_IOC);
    
    gPeakEquity = AccountInfoDouble(ACCOUNT_EQUITY);
    gDayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
    
    Print("EA Initialized. Balance: ", AccountInfoDouble(ACCOUNT_BALANCE));
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
    if (!IsNewBar()) return;          // Process only on new bar open
    
    if (!CheckCircuitBreakers()) return;
    if (!IsSessionActive()) return;
    if (!IsSpreadAcceptable()) return;
    
    // Update daily balance reference at day start
    UpdateDayReference();
    
    // Generate signal
    int signal = GetSignal();
    
    if (signal != 0 && !HasOpenTrade())
    {
        double entry = SymbolInfoDouble(_Symbol, signal == 1 ? SYMBOL_ASK : SYMBOL_BID);
        double atr = GetATR();
        double stop = entry - signal * atr * ATR_SL_Mult;
        double target = entry + signal * atr * ATR_TP_Mult;
        double lots = CalculateLotSize(entry, stop);
        
        if (lots < 0.01) return;
        
        if (signal == 1)
            trade.Buy(lots, _Symbol, entry, stop, target, "STR001");
        else
            trade.Sell(lots, _Symbol, entry, stop, target, "STR001");
    }
}

//+------------------------------------------------------------------+
//| Signal Generation                                                |
//+------------------------------------------------------------------+
int GetSignal()
{
    // --- Regime filter ---
    double atr = GetATR();
    if (atr < 5.0 || atr > 40.0) return 0;   // volatility out of range
    
    // --- HTF Bias (use H4 EMA) ---
    double ema_fast_h4 = iMA(_Symbol, PERIOD_H4, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
    double ema_slow_h4 = iMA(_Symbol, PERIOD_H4, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
    int htf_bias = (ema_fast_h4 > ema_slow_h4) ? 1 : -1;
    
    // --- Entry trigger (implement strategy-specific logic here) ---
    // Example placeholder: breakout of N-bar high/low
    double high_n = iHigh(_Symbol, PERIOD_H1, iHighest(_Symbol, PERIOD_H1, MODE_HIGH, 4, 1));
    double low_n  = iLow(_Symbol, PERIOD_H1, iLowest(_Symbol, PERIOD_H1, MODE_LOW, 4, 1));
    double close  = iClose(_Symbol, PERIOD_H1, 0);
    
    if (close > high_n && htf_bias == 1)  return 1;
    if (close < low_n  && htf_bias == -1) return -1;
    
    return 0;
}

//+------------------------------------------------------------------+
//| Risk & Circuit Breakers                                          |
//+------------------------------------------------------------------+
bool CheckCircuitBreakers()
{
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
    
    // Drawdown from peak
    gPeakEquity = MathMax(gPeakEquity, equity);
    double dd_pct = (gPeakEquity - equity) / gPeakEquity * 100;
    if (dd_pct >= MaxDrawdown_Pct)
    {
        Print("CIRCUIT BREAKER: Max drawdown ", dd_pct, "%. EA halted.");
        return false;
    }
    
    // Daily loss
    double daily_loss_pct = (gDayStartBalance - equity) / gDayStartBalance * 100;
    if (daily_loss_pct >= MaxDailyLoss_Pct)
    {
        Print("CIRCUIT BREAKER: Daily loss limit hit. No new trades today.");
        return false;
    }
    
    return true;
}

double CalculateLotSize(double entry, double stop)
{
    double risk_usd  = AccountInfoDouble(ACCOUNT_BALANCE) * RiskPerTrade_Pct / 100.0;
    double price_risk = MathAbs(entry - stop);
    double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    
    double value_per_lot = price_risk / tick_size * tick_value;
    double lots = (value_per_lot > 0) ? risk_usd / value_per_lot : 0.01;
    
    double min_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    double max_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
    double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
    
    lots = MathFloor(lots / lot_step) * lot_step;
    lots = MathMax(min_lot, MathMin(max_lot, lots));
    
    return lots;
}

bool IsSpreadAcceptable()
{
    double spread_usd = SymbolInfoDouble(_Symbol, SYMBOL_SPREAD) *
                        SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE) /
                        SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    return spread_usd <= Max_Spread_USD;
}

bool IsSessionActive()
{
    MqlDateTime dt;
    TimeToStruct(TimeGMT(), dt);
    int h = dt.hour;
    
    bool london   = (h >= 8  && h < 16) && Trade_London;
    bool new_york = (h >= 13 && h < 21) && Trade_NewYork;
    bool overlap  = (h >= 13 && h < 16) && Trade_Overlap;
    bool asian    = (h >= 22 || h < 7)  && Trade_Asian;
    
    return london || new_york || overlap || asian;
}

bool IsNewBar()
{
    static datetime last_bar_time = 0;
    datetime current_bar_time = iTime(_Symbol, PERIOD_H1, 0);
    if (current_bar_time != last_bar_time)
    {
        last_bar_time = current_bar_time;
        return true;
    }
    return false;
}

bool HasOpenTrade()
{
    for (int i = 0; i < PositionsTotal(); i++)
        if (PositionGetSymbol(i) == _Symbol &&
            PositionGetInteger(POSITION_MAGIC) == 20260101)
            return true;
    return false;
}

double GetATR()
{
    int atr_handle = iATR(_Symbol, PERIOD_H1, ATR_Period);
    double atr_buf[];
    CopyBuffer(atr_handle, 0, 0, 1, atr_buf);
    return atr_buf[0];
}

void UpdateDayReference()
{
    static int last_day = -1;
    MqlDateTime dt;
    TimeToStruct(TimeGMT(), dt);
    if (dt.day != last_day)
    {
        gDayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
        last_day = dt.day;
    }
}
```

---

## Coding Standards

### Naming Conventions
- Input parameters: `PascalCase_Unit` (e.g., `ATR_Period`, `Risk_Pct`)
- Functions: `PascalCase` (e.g., `GetSignal`, `CalculateLotSize`)
- Local variables: `camelCase`
- Constants: `ALL_CAPS`
- Magic numbers: use a unique 8-digit number per EA (e.g., `20260101`)

### Indicator Handles
Always create indicator handles in `OnInit()`, not in `OnTick()`. Creating handles on every tick leaks memory.

```mql5
// CORRECT — in OnInit()
int g_atr_handle;
int OnInit() {
    g_atr_handle = iATR(_Symbol, PERIOD_H1, 14);
    return INIT_SUCCEEDED;
}

// WRONG — in OnTick()
void OnTick() {
    int atr_h = iATR(_Symbol, PERIOD_H1, 14);  // memory leak
}
```

### Error Handling
```mql5
if (!trade.Buy(lots, _Symbol, 0, stop, target, comment))
{
    Print("Buy failed: ", trade.ResultRetcode(), " — ", trade.ResultRetcodeDescription());
}
```

### Timeframe Cross-Reference
When using multi-timeframe data, always use `iClose(_Symbol, PERIOD_H4, 1)` (bar index 1 = last closed bar). Never use bar 0 on a higher timeframe — it's incomplete.

---

## Python → MQL5 Translation Checklist

Before translating Python backtest to MQL5:

- [ ] Python strategy passes all backtest/walk-forward criteria
- [ ] Every indicator in Python has an exact MQL5 equivalent
- [ ] ATR calculation matches (MT5 uses Wilder's smoothing by default)
- [ ] Session logic is UTC-based (MT5 server time may differ — use `TimeGMT()`)
- [ ] Position sizing formula verified with MT5 tick value calculation
- [ ] Circuit breakers implemented
- [ ] Spread filter added
- [ ] News blackout logic added (manual or via external service)
- [ ] EA tested on MT5 Strategy Tester with "Every Tick Based on Real Ticks" mode
- [ ] Results compared to Python backtest — accept if within 10% Sharpe variance

---

## MT5 Strategy Tester Settings

For final validation before live deployment:

| Setting | Value |
|---|---|
| Model | Every Tick Based on Real Ticks |
| Spread | Use actual (not fixed) |
| Initial Deposit | 10,000 USD (for % calculations) |
| Leverage | Match your broker |
| Optimization | Disabled (parameters already chosen) |
| Date range | OOS period only |
