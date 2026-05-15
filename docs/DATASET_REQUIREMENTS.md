# Dataset Requirements — XAUUSD EA

> This document specifies exactly what data is needed, why, and the minimum quality standards. Provide this file to whoever is exporting data from MT5.

---

## Priority Tier 1 — Core (Required Before Any R&D Starts)

### XAUUSD OHLCV — All Key Timeframes

| Timeframe | Min History | Why |
|-----------|-------------|-----|
| **M1** (1-minute) | 3 years | Precise entry/exit simulation, spread analysis, micro-structure |
| **M5** (5-minute) | 5 years | Short-term signal generation, scalping strategies |
| **M15** (15-minute) | 7 years | Intraday momentum, session analysis |
| **H1** (1-hour) | 10 years | Primary trading timeframe, trend structure |
| **H4** (4-hour) | 10 years | Swing-level trend context |
| **D1** (Daily) | 15 years | HTF bias, major support/resistance, long-term regime |

### Per Row, Each File Must Contain
```
datetime (UTC), open, high, low, close, tick_volume
```
- `tick_volume` = MT5 tick count per bar (proxy for real volume)
- Datetime must be UTC, format: `YYYY-MM-DD HH:MM:SS`
- No Sunday candles (MT5 often generates empty/partial Sunday bars — strip them)
- Price in USD per troy ounce (standard XAUUSD quotation)

### File Format
- CSV files, comma-separated
- Filename convention: `XAUUSD_M1_20200101_20260515.csv`
- Place in: `data/raw/XAUUSD/`

---

## Priority Tier 2 — Session & Calendar Context (Required for Phase 1 EDA)

### Trading Session Timestamps
Not a price file — just a reference table:

| Session | Open UTC | Close UTC |
|---------|----------|-----------|
| Sydney | 22:00 | 07:00 |
| Tokyo | 00:00 | 09:00 |
| London | 08:00 | 16:00 |
| New York | 13:00 | 21:00 |
| NY-London Overlap | 13:00 | 16:00 |

This is static knowledge — no file needed, but agents must apply session filters.

### High-Impact Economic Events
- Source: Forex Factory calendar export or MT5 News feed
- Events that affect XAUUSD: FOMC, NFP, CPI, PPI, US GDP, Fed speeches, Geopolitical headlines
- Format: `datetime (UTC), event_name, currency, impact_level (High/Med/Low), actual, forecast, previous`
- Place in: `data/raw/calendar/economic_calendar_YYYY.csv`

---

## Priority Tier 3 — Correlation Assets (Required for Phase 1.5 Correlation Study)

| Symbol | Timeframe | Why |
|--------|-----------|-----|
| DXY (US Dollar Index) | D1, H1 | Primary inverse driver of gold |
| US10Y (10-Year Yield) | D1 | Real rates vs gold inverse relationship |
| XAGUSD (Silver) | H1, D1 | Silver/Gold ratio, risk-on/risk-off signal |
| XTIUSD (WTI Oil) | D1 | Inflation proxy, commodity correlation |
| US500 (S&P 500) | D1, H1 | Risk sentiment — gold as safe haven |
| VIX | D1 | Fear index — spikes correlate with gold surges |

Format: same CSV structure as XAUUSD  
Place in: `data/raw/corr_assets/`

---

## Priority Tier 4 — Tick Data (Required for Phase 4 Precision Backtesting)

| Symbol | Min History | Why |
|--------|-------------|-----|
| XAUUSD Ticks | 6 months (recent) | Validate slippage assumptions, spread distribution |

Format: `datetime (UTC), bid, ask`  
This is large data — export only if M1 backtests show slippage is a significant factor.

Place in: `data/raw/ticks/`

---

## Data Quality Checklist

Before any analysis, run these checks (see `.agent/skills/data-analysis.md` for code):

- [ ] No duplicate timestamps
- [ ] No gaps longer than 1 hour on M1 during active sessions (Mon–Fri)
- [ ] No OHLC violations: `high >= open, high >= close, low <= open, low <= close`
- [ ] Tick volume > 0 on all active session bars
- [ ] Price range sanity: XAUUSD should always be 1,000–4,000 USD range
- [ ] File covers the stated date range without major gaps
- [ ] Weekends excluded (Saturday 00:00 – Sunday 21:00 UTC)

---

## Recommended Data Export from MT5

**Option A — MT5 GUI (no code):**
Open MT5 → Tools → History Center → XAUUSD → Select timeframe → Export as CSV.

**Option B — MQL5 script (runs inside MT5 terminal):**

```mql5
// Save as a Script in MetaEditor, attach to any chart to export
void OnStart()
{
    string symbol    = "XAUUSD";
    ENUM_TIMEFRAMES tf = PERIOD_H1;
    datetime from    = D'2015.01.01';
    datetime to      = D'2026.05.15';

    MqlRates rates[];
    int count = CopyRates(symbol, tf, from, to, rates);

    int handle = FileOpen("XAUUSD_H1_export.csv",
                          FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
    FileWrite(handle, "datetime", "open", "high", "low", "close", "tick_volume");

    for (int i = 0; i < count; i++)
    {
        FileWrite(handle,
            TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES),
            rates[i].open, rates[i].high, rates[i].low,
            rates[i].close, rates[i].tick_volume);
    }
    FileClose(handle);
    Print("Exported ", count, " bars to Files/XAUUSD_H1_export.csv");
}
```

The file will be saved in `MT5/MQL5/Files/`. Move it to `data/raw/XAUUSD/`.

**Naming convention after moving:**
```
data/raw/XAUUSD/XAUUSD_H1_20150101_20260515.csv
data/raw/XAUUSD/XAUUSD_D1_20100101_20260515.csv
```

**Note:** R&D analysis is done in TypeScript. The export step only needs to produce a CSV — use whichever export tool is most convenient.

---

## What We Do NOT Need (to keep scope clean)

- Tick data for all history (only recent 6 months for slippage validation)
- Fundamental balance sheets, ETF flows — out of scope for EA
- Sentiment data — potentially useful but only in Phase 3+ as a filter
- Order book data — MT5 doesn't provide it; not needed
- M30 (30-minute) — M15 and H1 already bracket this; M30 adds noise

---

## Minimum Viable Dataset to Start

If you can only provide one batch right now, prioritize in this order:

1. XAUUSD H1 — 10 years (most important for strategy design)
2. XAUUSD D1 — 15 years (HTF context)
3. XAUUSD M15 — 5 years (LTF entry simulation)
4. XAUUSD H4 — 10 years (swing context)
5. XAUUSD M5 — 3 years (scalping research)
6. XAUUSD M1 — 2 years (precise backtesting)
