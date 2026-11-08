# AI / Developer Handoff

## Objective and current state

User requested a ten-year research dataset for EURUSD, USDJPY, GBPUSD, with M1/M5/M15 grouped per price-action event and explicit entry/exit plans. Collection and event export are complete. This task did not authorize creating an EA or sending trades; none were created/sent. No model was trained.

Start with `README.md`, `REVIEW_REPORT.md`, `schema.json`, `data_dictionary.csv`, then `config/selection_rules_v1.json`. Actual event count: 31,104. Native history rows: 13,771,322. Full verification results are in `audit/verification_report.json`.

## Authoritative files

- `history_manifest.csv`: native monthly archive paths, counts, first/last times, SHA-256.
- `manifest.csv`: all events and temporal splits. Use this to locate event folders, not directory guessing.
- `PAIR/event_manifest.csv`: per-pair view.
- `PAIR/candidate_rejections.csv`: causal shape candidates rejected for missing pre-known target or excessive bar-spread proxy.
- `audit/collection_snapshot.json`: immutable collection cutoff and metadata. Do not silently move the cutoff on resume.
- `audit/checkpoints/`: stable monthly extraction progress, native gaps, OHLC checks and file hashes.
- `audit/PAIR_cross_tf*.json/csv`: native OHLC/volume reconciliation with observed M1.
- `schema_v1/`: obsolete draft templates, not the authoritative actual export schema.

## Entry/exit and labels

Read the exact registered rules rather than reinterpreting the pattern names. Swings require two already-closed bars on the right. Breakout retests happen strictly after the M5 breakout close. Entries are pending stop plans, expire after 180 seconds, and use a 600-second time stop after fill. Targets are already-known opposing structure; no RR or future-return threshold selects events.

The bar simulator is deliberately labeled `simulated_bars`. Its per-bar spread is a proxy. Exact intrabar entry/exit order and timestamps are unavailable. Time values are minute interval proxies. Ambiguous/censored/not_evaluated are meaningful statuses, not losses or zeros. Net costs/returns and MFE/MAE remain blank when not established. Do not call gross returns profitable live results.

## Features and leakage prevention

- Default model input is approved causal numeric columns from event M1/M5/M15, plus verified as-of context only.
- Keep IDs, paths, reviewer fields, split labels and selection provenance out of feature tensors.
- `pattern_label`, `side_label`, planned order/SL/TP prices are supervision targets for learning setup/plan decisions, not features that answer their own task.
- Do not feed `outcome.csv`, `gross_r`, `net_r`, future ticks, holding duration or outcome availability/status into a decision-time model.
- Preserve all real raw prices. Normalize price distances by decision-time ATR or another train-defined causal scale. Fit scalers/imputers only on train.
- All UTC fields intentionally stay blank until a historical broker-time mapping is verified. Do not append Z to server strings.
- Do not backdate pivot recognition. Re-run prefix-invariance tests after feature changes.
- Controls have a recorded sampling probability and signals are deduplicated. Offline event prevalence differs from full-market prevalence. Time-separated evaluation should ultimately replay the raw archive.
- For entry/SL/TP regression, mask out no-trade controls rather than replacing their blank price targets with zero. Missing numeric inputs need explicit masks or a training-only imputer.

Example of reading inputs and separate supervision, without any EA or training code:

```python
from pathlib import Path
import pandas as pd

root = Path("dataset")
manifest = pd.read_csv(root / "manifest.csv")
record = manifest.query("split == 'train' and quality_status == 'valid'").iloc[0]
folder = root / record["relative_event_path"]
columns = ["open", "high", "low", "close", "tick_volume", "bar_spread_points",
           "body_price", "upper_wick_price", "lower_wick_price", "body_to_range",
           "return_1bar", "atr_14_price", "range_atr_14", "ema_9_price",
           "ema_21_price", "ema_200_price", "rsi_14", "tick_volume_ratio_20"]
inputs = dict((tf, pd.read_csv(folder / (tf + ".csv"))[columns])
              for tf in ["M1", "M5", "M15"])
input_missing_masks = dict((tf, frame.isna()) for tf, frame in inputs.items())
decision_targets = pd.read_csv(folder / "event.csv")[["pattern_label", "side_label"]]
plan_targets = pd.read_csv(folder / "plan.csv")
# outcome.csv is intentionally absent from the decision-time inputs.
```

## Splits and evaluation

Existing splits are global chronological splits across all pairs: train before 2024, validation in 2024, test from 2025, with seven-day embargo and full input-to-outcome interval purging. `split=excluded` remains excluded. Keep all variants of an event/overlapping context on the same side of each boundary. Never shuffle windows into random train/test splits.

For setup classification the causal pattern/side labels can be used with their machine-review provenance. For return prediction, complete OHLC-only labels remain provisional; obtain real Bid/Ask ticks and historical costs before execution-quality claims. Do not tune rules on test results and then report the same test as unseen performance.

## Tools and reproducibility

All scripts live in the parent project's `tools/` directory:

```bash
python3 tools/collect_history.py             # native history, with per-year resume/checksums
python3 tools/collect_history.py --warmup    # separate pre-period context
python3 tools/build_events.py --dry-run     # inspect counts first; needs pandas/numpy
python3 tools/build_events.py               # regenerates events/manifests
python3 tools/test_dataset_logic.py         # synthetic logic fixtures only; no fixture data exported
python3 tools/finalize_dataset.py           # validates saved files and rebuilds reports
```

Use a Python environment with pandas 2.2.3 and numpy 2.3.5. This project used the Codex bundled Python runtime. The collector uses only standard-library modules and the local MT5 MCP helper. Its client rejects `trade_*` tools.

MT5 must be open for collection, but event construction/review is offline. The terminal was temporarily started with a dedicated `MaxBars=8000000` export configuration because the previous 100,000-bar setting prevented ten-year M1 retrieval. For future re-collection check the active history limit and restart through the documented MT5 settings if needed. Do not assume the native terminal's oldest reported history date guarantees accessible M1 data.

`mt5-mcp.txt` is a local credential outside dataset/. It is excluded from git. Never print it, copy it into documentation, commit it or include it in dataset archives. The temporary restart script was removed after collection; no EA needs to be attached.

## Changes require new lineage

If selection thresholds, swing confirmation, feature smoothing, time mapping, exit policies or source data change, create a new rule/dataset version, regenerate affected artifacts and rerun verification. Do not mix old and new event files/manifests. If the source cutoff changes, use a new version/snapshot rather than silently overwriting frozen evaluation data.
