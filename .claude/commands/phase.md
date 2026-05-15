---
description: Show current R&D phase status, checklist, and what's needed to advance. Usage: /phase
---

Show the current R&D phase status for this XAUUSD EA project.

Steps:
1. Read `CLAUDE.md` — find the "Current Phase" section to get the active phase name and number.
2. Read `docs/RND_LIFECYCLE.md` — find that phase's section and extract its checklist and exit criteria.
3. Scan the repository to determine what has actually been completed:
   - Check `research/hypothesis/` for hypothesis files (count and list them with status)
   - Check `strategies/` for strategy `.ts` files
   - Check `backtests/` for result JSON files
   - Check `data/raw/` and `data/processed/` for data files (list what's present)
   - Check `research/experiments/` for EDA and experiment logs
4. For the current phase, show which checklist items are done (based on files present) and which are still pending.
5. Show the exit criteria and whether each is met.
6. Recommend the single most important next action in one sentence.

Output format:
```
=== Phase X — {Name} ===

Checklist:
  [x] Item already done (file/evidence found)
  [ ] Item still pending

Exit Criteria:
  [x] Met — ...
  [ ] Not yet — ...

Assets Present:
  Data:        H1 (X years), D1 (X years), ...   OR  "No data yet"
  Hypotheses:  HYP-001 (Draft), ...               OR  "None yet"
  Strategies:  STR-001, ...                        OR  "None yet"
  Backtests:   BT_STR-001_..., ...                 OR  "None yet"

NEXT ACTION: [one clear sentence]
```
