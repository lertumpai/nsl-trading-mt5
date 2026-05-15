---
description: Validate a XAUUSD data file and print a quality report. Usage: /validate <filepath>
---

Validate a XAUUSD OHLCV data file and produce a quality report.

**File path provided:** $ARGUMENTS

Steps:
1. Read `.agent/skills/data-analysis.md` to understand the validation rules.
2. If no argument is given, list all files in `data/raw/` and ask which to validate.
3. Read the first 20 rows and the last 20 rows of the specified CSV file to understand its format (column names, date format, delimiter).
4. Write a self-contained TypeScript validation script to a temp file `data/validate_temp.ts`:
   - Parse the CSV using `csv-parse`
   - Run all checks from the skill: duplicates, OHLC violations, price range, zero-volume bars, gap detection
   - Detect the timeframe automatically from the median gap between consecutive bars
   - Print a structured report
5. Run it with `bun data/validate_temp.ts` (or `npx tsx data/validate_temp.ts` if Bun is not available).
6. Delete the temp file after running.
7. Print the validation report. If issues are found, suggest specific fixes.

Expected report format:
```
=== Data Validation: {filename} ===
Rows:          {n}
Date range:    {start} — {end}
Detected TF:   {M1 / M5 / M15 / H1 / H4 / D1}
Quality:       PASS / FAIL

Issues:
  - DUPLICATE ROWS: X        (or "None")
  - OHLC VIOLATIONS: X       (or "None")
  - PRICE OUT OF RANGE: X    (or "None")
  - ZERO VOLUME BARS: X      (or "None")
  - DATA GAPS: X gaps         (or "None")

Recommendation: {clean command or "Ready to use"}
```
