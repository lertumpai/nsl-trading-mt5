# Skill: MQL5 EA Coding Standards

> Use this skill when writing or reviewing MQL5 code. TypeScript research → MQL5 production is a one-way translation. Only translate after the strategy passes full walk-forward validation.

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
//| STR-001 — London Breakout EA                                     |
//| Hypothesis: HYP-001                                              |
//| Backtest Result: Sharpe 1.72, DD 8.4%, PF 1.65 (OOS WF)         |
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
input int    EMA_Fast        = 50;       // Fast EMA period (H4 bias)
input int    EMA_Slow        = 200;      // Slow EMA period (H4 bias)

input group "Risk Management"
input double RiskPerTrade_Pct  = 1.0;    // Risk per trade (% of balance)
input double MaxDailyLoss_Pct  = 2.0;    // Daily loss limit (% of balance)
input double MaxDrawdown_Pct   = 10.0;   // Drawdown pause threshold (%)
input double KillDrawdown_Pct  = 15.0;   // Drawdown hard-stop threshold (%)
input double Max_Spread_USD    = 1.5;    // Skip trade if spread > this (USD)

input group "Session Filter"
input bool   Trade_London    = true;     // Trade London session
input bool   Trade_NewYork   = true;     // Trade New York session
input bool   Trade_Overlap   = true;     // Trade overlap session
input bool   Trade_Asian     = false;    // Trade Asian session (usually false)

//--- Indicator handles — created once in OnInit, released in OnDeinit
int g_atr_handle;
int g_ema_fast_handle;
int g_ema_slow_handle;

//--- Global state
CTrade trade;
double gPeakEquity;
double gDayStartBalance;

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
    // Create all indicator handles here — never inside OnTick
    g_atr_handle      = iATR(_Symbol, PERIOD_H1, ATR_Period);
    g_ema_fast_handle = iMA(_Symbol, PERIOD_H4, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
    g_ema_slow_handle = iMA(_Symbol, PERIOD_H4, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);

    if (g_atr_handle == INVALID_HANDLE ||
        g_ema_fast_handle == INVALID_HANDLE ||
        g_ema_slow_handle == INVALID_HANDLE)
    {
        Print("ERROR: Failed to create indicator handles");
        return INIT_FAILED;
    }

    trade.SetExpertMagicNumber(20260101);
    trade.SetDeviationInPoints(30);
    trade.SetTypeFilling(ORDER_FILLING_IOC);

    gPeakEquity      = AccountInfoDouble(ACCOUNT_EQUITY);
    gDayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);

    Print("EA Initialized. Balance: ", gDayStartBalance);
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization — release all handles                    |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    IndicatorRelease(g_atr_handle);
    IndicatorRelease(g_ema_fast_handle);
    IndicatorRelease(g_ema_slow_handle);
}

//+------------------------------------------------------------------+
//| Expert tick                                                      |
//+------------------------------------------------------------------+
void OnTick()
{
    if (!IsNewBar()) return;

    UpdateDayReference();

    if (!CheckCircuitBreakers()) return;
    if (!IsSessionActive())      return;
    if (!IsSpreadAcceptable())   return;

    int signal = GetSignal();
    if (signal == 0 || HasOpenTrade()) return;

    double atr    = GetBufferValue(g_atr_handle, PERIOD_H1, 1);   // shift 1 = last closed bar
    double entry  = SymbolInfoDouble(_Symbol, signal == 1 ? SYMBOL_ASK : SYMBOL_BID);
    double stop   = entry - signal * atr * ATR_SL_Mult;
    double target = entry + signal * atr * ATR_TP_Mult;
    double lots   = CalculateLotSize(entry, stop);

    if (lots <= 0.0) return;   // risk cap exceeded — skip, do not force min lot

    bool ok = (signal == 1)
        ? trade.Buy(lots,  _Symbol, entry, stop, target, "STR001")
        : trade.Sell(lots, _Symbol, entry, stop, target, "STR001");

    if (!ok)
        Print("Order failed: ", trade.ResultRetcode(), " — ", trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
//| Signal Generation                                                |
//+------------------------------------------------------------------+
int GetSignal()
{
    // --- Regime: ATR within tradeable range ---
    double atr = GetBufferValue(g_atr_handle, PERIOD_H1, 1);
    if (atr < 5.0 || atr > 40.0) return 0;

    // --- HTF bias: last closed H4 EMA values (shift 1) ---
    double ema_fast = GetBufferValue(g_ema_fast_handle, PERIOD_H4, 1);
    double ema_slow = GetBufferValue(g_ema_slow_handle, PERIOD_H4, 1);
    int htf_bias = (ema_fast > ema_slow) ? 1 : -1;

    // --- Entry: breakout of last 4 closed H1 bars (shift 1..4) ---
    double high_n = iHigh(_Symbol, PERIOD_H1, iHighest(_Symbol, PERIOD_H1, MODE_HIGH, 4, 1));
    double low_n  = iLow (_Symbol, PERIOD_H1, iLowest (_Symbol, PERIOD_H1, MODE_LOW,  4, 1));
    double close  = iClose(_Symbol, PERIOD_H1, 1);   // last closed bar

    if (close > high_n && htf_bias == 1)  return  1;
    if (close < low_n  && htf_bias == -1) return -1;

    return 0;
}

//+------------------------------------------------------------------+
//| Read one value from an indicator buffer (last closed bar)        |
//+------------------------------------------------------------------+
double GetBufferValue(int handle, ENUM_TIMEFRAMES tf, int shift)
{
    double buf[];
    ArraySetAsSeries(buf, true);
    if (CopyBuffer(handle, 0, shift, 1, buf) != 1)
    {
        Print("CopyBuffer failed for handle ", handle);
        return 0.0;
    }
    return buf[0];
}

//+------------------------------------------------------------------+
//| Circuit Breakers                                                 |
//+------------------------------------------------------------------+
bool CheckCircuitBreakers()
{
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);

    gPeakEquity = MathMax(gPeakEquity, equity);
    double dd_pct        = (gPeakEquity - equity) / gPeakEquity * 100;
    double daily_loss    = (gDayStartBalance - equity) / gDayStartBalance * 100;

    if (dd_pct >= KillDrawdown_Pct)
    {
        Print("KILL SWITCH: drawdown ", DoubleToString(dd_pct, 1), "% >= ",
              KillDrawdown_Pct, "%. EA permanently halted.");
        ExpertRemove();   // remove EA from chart — requires manual restart
        return false;
    }
    if (dd_pct >= MaxDrawdown_Pct)
    {
        Print("PAUSE: drawdown ", DoubleToString(dd_pct, 1), "% >= ", MaxDrawdown_Pct, "%.");
        return false;
    }
    if (daily_loss >= MaxDailyLoss_Pct)
    {
        Print("DAILY LIMIT: loss ", DoubleToString(daily_loss, 1), "%. No new trades today.");
        return false;
    }
    return true;
}

//+------------------------------------------------------------------+
//| Position Sizing — returns 0 if risk cap would be breached        |
//+------------------------------------------------------------------+
double CalculateLotSize(double entry, double stop)
{
    double risk_usd    = AccountInfoDouble(ACCOUNT_BALANCE) * RiskPerTrade_Pct / 100.0;
    double price_risk  = MathAbs(entry - stop);
    double tick_value  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tick_size   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

    if (price_risk == 0 || tick_size == 0) return 0.0;

    double value_per_lot = (price_risk / tick_size) * tick_value;
    if (value_per_lot == 0) return 0.0;

    double lots     = risk_usd / value_per_lot;
    double min_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    double max_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
    double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

    lots = MathFloor(lots / lot_step) * lot_step;

    // If correct size is below minimum, skip the trade — do NOT round up
    // (rounding up to min_lot would exceed the risk cap)
    if (lots < min_lot) return 0.0;

    return MathMin(lots, max_lot);
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
bool IsSpreadAcceptable()
{
    double spread_usd = SymbolInfoDouble(_Symbol, SYMBOL_SPREAD)
                      * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE)
                      / SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    return spread_usd <= Max_Spread_USD;
}

bool IsSessionActive()
{
    MqlDateTime dt;
    TimeToStruct(TimeGMT(), dt);
    int h = dt.hour;

    if (Trade_Overlap  && h >= 13 && h < 16) return true;
    if (Trade_London   && h >= 8  && h < 16) return true;
    if (Trade_NewYork  && h >= 13 && h < 21) return true;
    if (Trade_Asian    && (h >= 22 || h < 7)) return true;
    return false;
}

bool IsNewBar()
{
    static datetime last_bar_time = 0;
    datetime current = iTime(_Symbol, PERIOD_H1, 0);
    if (current != last_bar_time) { last_bar_time = current; return true; }
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

## TypeScript → MQL5 Translation Checklist

Before translating a TypeScript strategy to MQL5:

- [ ] TypeScript strategy passes all Phase 4 (IS) and Phase 6 (WF) criteria
- [ ] Every indicator in TypeScript has an exact MQL5 equivalent
- [ ] All indicator handles created in `OnInit()`, released in `OnDeinit()`
- [ ] All indicator values read with `CopyBuffer()` at shift 1 (last closed bar)
- [ ] ATR smoothing matches TypeScript: MT5 uses Wilder's EMA by default
- [ ] Session logic is UTC-based — use `TimeGMT()`, not `TimeCurrent()`
- [ ] Position sizing verified: `CalculateLotSize()` returns 0 (not min_lot) when risk cap exceeded
- [ ] All three circuit breakers implemented: daily loss, drawdown pause, kill-switch
- [ ] `KillDrawdown_Pct` wired up and calls `ExpertRemove()`
- [ ] Spread filter implemented
- [ ] `CheckCircuitBreakers()` called before every signal evaluation
- [ ] All `trade.Buy()`/`trade.Sell()` return values checked and logged
- [ ] EA compiles with 0 errors in MetaEditor (F7)
- [ ] EA tested on MT5 Strategy Tester with "Every Tick Based on Real Ticks" mode, actual spread
- [ ] Strategy Tester results compared to TypeScript backtest — accept if within 15% Sharpe variance

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
