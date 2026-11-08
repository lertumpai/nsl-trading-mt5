"""Train a small causal trade-quality filter and embed it in one MT5 source file.

Run from the repository root: python3 tools/train_ea.py
Only train outcomes fit coefficients; validation selects regularization/threshold.
The test set is scored once after freezing every model. No MT5 orders are sent.
"""
from __future__ import annotations
import argparse, csv, hashlib, html, io, json, platform
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'dataset'
OUT = ROOT / 'release'
PAIRS = ['EURUSD', 'GBPUSD', 'USDJPY']
TFS = ['M1', 'M5', 'M15']
FIELDS = ['body_signed_atr', 'range_atr', 'close_ema21_atr',
          'ema9_ema21_atr', 'rsi_centered', 'volume_ratio']
FEATURES = [tf + '_' + f for tf in TFS for f in FIELDS] + ['spread_atr']
VERSION = 'nsl_pa_filter_v1'
# Explicit hypothetical cost stress, beyond the spread already in gross labels.
COST_PIPS = 1.0
THRESHOLDS = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
LAMBDAS = [0.01, 0.1, 1.0]


def read_last(path):
    """Read only a causal window's final row, preserving its published numbers."""
    with path.open('rb') as f:
        header = f.readline().decode('utf-8-sig').strip()
        f.seek(max(len(header) + 2, path.stat().st_size - 8192))
        line = f.read().splitlines()[-1].decode()
    return next(csv.DictReader(io.StringIO(header + '\n' + line)))


def vector(rows, point):
    x = []
    for r in rows:
        r = {k: (v if v != '' else 'nan') for k, v in r.items()}
        a = float(r['atr_14_price'])
        x.extend([(float(r['close']) - float(r['open'])) / a,
                  (float(r['high']) - float(r['low'])) / a,
                  (float(r['close']) - float(r['ema_21_price'])) / a,
                  (float(r['ema_9_price']) - float(r['ema_21_price'])) / a,
                  (float(r['rsi_14']) - 50) / 50,
                  float(r['tick_volume_ratio_20'])])
    x.append(float(rows[0]['bar_spread_points'] or 'nan') * point / float(rows[0]['atr_14_price']))
    return x


def extract():
    manifest = pd.read_csv(DATA / 'manifest.csv', keep_default_na=False)
    snapshot = json.loads((DATA / 'audit/collection_snapshot.json').read_text())
    records = []
    for i, m in enumerate(manifest.to_dict('records')):
        if m['quality_status'] != 'valid' or m['split'] == 'excluded':
            continue
        p = DATA / m['relative_event_path']
        # Only these three approved numeric bar rows become model inputs.
        rows = [read_last(p / (tf + '.csv')) for tf in TFS]
        point = snapshot['symbol_metadata_at_export'][m['pair']]['point']
        values = vector(rows, point)
        assert all(r['close_time_server'] <= m['decision_time_server'] for r in rows)
        # Plans and outcomes below are targets/evaluation metadata only.
        with (p / 'outcome.csv').open() as f:
            outcome = next(csv.DictReader(f))
        with (p / 'plan.csv').open() as f:
            plan = next(csv.DictReader(f))
        r = {k: m[k] for k in ['event_id', 'pair', 'split', 'decision_time_server',
                              'input_start_server', 'label_end_server', 'pattern_label', 'side_label']}
        r.update(zip(FEATURES, values))
        r['model_features_valid'] = bool(np.isfinite(values).all())
        for key in ['label_status', 'gross_r', 'gross_pnl_pips', 'initial_risk_pips',
                    'entry_fill_time_server', 'exit_time_server', 'exit_interval_end_server', 'exit_reason']:
            r[key] = outcome.get(key, '')
        r['entry_reference_price'] = plan['entry_reference_price']
        r['stop_loss_price'] = plan['stop_loss_price']
        r['take_profit_price'] = plan['take_profit_price']
        records.append(r)
        if i % 3000 == 0:
            print(f'Extracted {i:,}/{len(manifest):,}', flush=True)
    frame = pd.DataFrame(records)
    frame.to_csv(OUT / 'event_features.csv', index=False)
    return frame


def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -35, 35)))


def fit(x, y, penalty):
    # Deterministic convex logistic regression; L2 only on slopes.
    b = np.zeros(x.shape[1])
    regularizer = np.r_[0., np.full(x.shape[1] - 1, penalty)]
    for _ in range(80):
        p = sigmoid(x @ b)
        g = x.T @ (p - y) / len(y) + regularizer * b
        h = (x.T * (p * (1 - p))) @ x / len(y) + np.diag(regularizer + 1e-9)
        step = np.linalg.solve(h, g)
        b -= step
        if np.max(np.abs(step)) < 1e-9:
            break
    return b


def metrics(r):
    a = np.asarray(r, dtype=float)
    if len(a) == 0:
        return dict(trades=0, win_rate=None, sum_r=0., mean_r=None, profit_factor=None, max_drawdown_r=0.)
    equity = np.r_[0., np.cumsum(a)]
    losses = -a[a < 0].sum()
    return dict(trades=len(a), win_rate=float((a > 0).mean()), sum_r=float(a.sum()),
                mean_r=float(a.mean()), profit_factor=float(a[a > 0].sum() / losses) if losses else None,
                max_drawdown_r=float((np.maximum.accumulate(equity) - equity).max()))


def evaluate(frame, threshold):
    """Scenario stream with one reserved order/position per symbol.

    Reserve ambiguous/censored cases through the maximum 13-minute horizon;
    keep their returns unknown. Report an explicit -1R stress separately.
    This does not resolve intrabar execution or replace full-market MT5 replay.
    """
    rows = frame.sort_values(['decision_time_server', 'pattern_label', 'side_label'])
    busy = pd.Timestamp.min
    accepted, skipped = [], 0
    for r in rows.to_dict('records'):
        if r['side_label'] == 'no_trade' or r['score'] < threshold:
            continue
        decision = pd.Timestamp(r['decision_time_server'])
        if decision < busy:
            skipped += 1
            continue
        status = r['label_status']
        if status == 'complete':
            end = r['exit_interval_end_server'] or r['exit_time_server']
            busy = max(decision + pd.Timedelta(minutes=1), pd.Timestamp(end))
        else:
            busy = decision + pd.Timedelta(minutes=13 if status in ['ambiguous', 'censored'] else 3)
        accepted.append(r)
    a = pd.DataFrame(accepted, columns=rows.columns)
    c = a[a.label_status == 'complete']
    gross = pd.to_numeric(c.gross_r).to_numpy()
    risk = pd.to_numeric(c.initial_risk_pips).to_numpy()
    stressed = gross - COST_PIPS / risk
    unknown = a.label_status.isin(['ambiguous', 'censored'])
    # Chronological stress includes unresolved fills at -1R minus assumed cost.
    stress = []
    for r in a.to_dict('records'):
        if r['label_status'] == 'complete':
            stress.append(float(r['gross_r']) - COST_PIPS / float(r['initial_risk_pips']))
        elif r['label_status'] in ['ambiguous', 'censored']:
            pip = 0.01 if r['pair'] == 'USDJPY' else 0.0001
            risk = abs(float(r['entry_reference_price']) - float(r['stop_loss_price'])) / pip
            stress.append(-1 - COST_PIPS / risk)
    return dict(accepted=len(a), overlap_skipped=skipped,
                statuses={str(k): int(v) for k, v in a.label_status.value_counts().items()},
                unresolved=int(unknown.sum()), gross_complete=metrics(gross),
                assumed_cost_complete=metrics(stressed), unresolved_minus_1r_stress=metrics(stress)), a


def render_report(result):
    rows = []
    model_rows = []
    for pair, m in result['models'].items():
        count = m['evaluation']['validation']['assumed_cost_complete']['trades']
        status = 'Insufficient validation sample' if 'failed_' in m['tuning_status'] else 'Validation cost-stress loss'
        model_rows.append(f'<tr><td>{pair}</td><td>{m["train_complete_fills"]}</td><td>{m["penalty"]}</td><td>{m["threshold"]}</td><td>{count}</td><td>{status}</td></tr>')
        for split, e in m['evaluation'].items():
            g, s = e['gross_complete'], e['assumed_cost_complete']
            fmt = lambda v: '—' if v is None else f'{v:.3f}'
            rows.append('<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in
                [pair, split, e['accepted'], g['trades'], e['unresolved'], fmt(g['sum_r']),
                 fmt(s['sum_r']), fmt(s['profit_factor']), fmt(s['max_drawdown_r']),
                 fmt(e['unresolved_minus_1r_stress']['sum_r'])]) + '</tr>')
    native_rows = []
    for pair in PAIRS:
        for split in ['train', 'test']:
            path = OUT / 'mt5_runs' / f'{pair}_{split}_report.json'
            if path.exists():
                r = json.loads(path.read_text())
                native_rows.append('<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in
                    [pair, split, r['trades'], f"{r['profit']:.2f}", f"{r['profit_factor']:.4f}",
                     f"{r['drawdown_percent_equity']:.2f}%", r['generated_bars']]) + '</tr>')
    native = ''
    if native_rows:
        native = '''<h2>Actual MT5 Strategy Tester runs</h2>
<p>These runs execute the frozen compiled EA against all available market bars in each calendar period, using MT5's M1 OHLC tick model, zero added execution delay, a 10,000 USD starting balance, 1:100 leverage, and 0.25% equity risk per order. Orders were enabled explicitly in the tester. Training period: 2016-11-08 through 2023-12-31. Held-out period: 2025-01-08 through 2026-11-06. No rules or weights were retuned using these reports.</p>
<table><tr><th>Pair</th><th>Period</th><th>Trades</th><th>Model P/L USD</th><th>Profit factor</th><th>Max equity DD</th><th>Generated M1 bars</th></tr>''' + ''.join(native_rows) + '''</table>
<p>MT5 model P/L uses the tester's spread and symbol/account settings. It does not establish measured real-tick fills, historical commission accuracy, latency, or live profitability. It does not include the separate 1-pip stress from the scenario table below. Calendar tester periods use continuous trading with the EA's own readiness gates; they do not duplicate manifest event-window exclusions. Detailed tester results/configuration evidence is in <code>mt5_runs/</code>.</p>'''
    text = '''<!doctype html><meta charset="utf-8"><title>NSL EA training and test report</title>
<style>body{font:16px system-ui;max-width:1280px;margin:48px auto;padding:0 24px;color:#17202b;background:#f4f7fa}h1{font-size:32px}table{border-collapse:collapse;background:white;width:100%;font-size:14px}td,th{padding:12px;border-bottom:1px solid #d9e1e8;text-align:right}td:first-child,td:nth-child(2){text-align:left}p{line-height:1.6}.note{background:#fff0ca;padding:20px;border-radius:10px}code{background:#e1e8ef;padding:2px 5px}</style>
<h1>NSL price-action EA: training and test report</h1>
<p>Single-file MT5 EA for EURUSD, GBPUSD and USDJPY. A separate 19-input logistic trade-quality filter per pair is embedded in the EA. Four causal setup rules construct stop-entry, stop-loss and structural target plans.</p>
<p class="note"><b>Research evaluation, not verified live performance.</b> The report distinguishes actual MT5 generated-tick backtests from selected dataset scenario replay. Minute-bar spread proxies are included in published scenario gross labels; historical commission/slippage are unknown. A separate hypothetical 1.0 pip round-trip cost is subtracted in the scenario stress columns. No live orders were sent. The EA compiled with 0 errors and 0 warnings.</p>
''' + native + '''<h2>Dataset scenario evaluation</h2><table><tr><th>Pair</th><th>Split</th><th>Accepted</th><th>Complete fills</th><th>Unresolved</th><th>Gross R</th><th>Cost-stress R</th><th>Cost-stress PF</th><th>Cost-stress DD R</th><th>Unresolved −1R stress</th></tr>''' + ''.join(rows) + '''</table>
<p>R is return divided by each trade's initial stop distance. R totals are not account-currency profit. DD is closed-trade drawdown in R, not intratrade equity drawdown. Canceled/unfilled plans contribute no trade return; ambiguous/censored results remain unknown. The final column assigns each unresolved result −1R plus assumed costs; it is a stress scenario, not a proven worst-case bound.</p>
<h2>Training and tuning protocol</h2>
<table><tr><th>Pair</th><th>Fitted training fills</th><th>L2</th><th>Score threshold</th><th>Validation fills</th><th>Decision</th></tr>''' + ''.join(model_rows) + '''</table>
<p>The original global chronological splits and exclusions are preserved: training before 2024, validation in 2024, test from 2025 through the frozen 2026-11-06 23:57 broker-server cutoff. Seven-day embargo and full input/outcome interval purging come from the original manifest. No timestamps are converted to UTC.</p>
<p>Features use only the final closed M1/M5/M15 bars: signed body/ATR, range/ATR, close-to-EMA21/ATR, EMA9-to-EMA21/ATR, centered RSI14, tick-volume ratio20, and M1 spread/ATR. No IDs, paths, split flags, pattern/side labels, planned prices or outcomes enter the model. Train-only means/scales standardize features, clipped to ±8 train standard deviations. Missing/non-finite input rows are flagged and rejected for fitting and entry; they are never assigned a fabricated return target.</p>
<p>The binary target is a complete filled trade with published gross R minus the hypothetical cost greater than zero. Only complete training fills fit coefficients. Controls have no return target and are not assigned a fake zero. Unfilled, canceled, ambiguous and censored examples do not train the return filter. This creates label-availability selection bias, reported through separate status counts and unresolved stress.</p>
<p>For each pair, validation alone screens three L2 penalties (0.01, 0.1, 1.0) and nine thresholds (0.45 to 0.85). The objective is complete-fill cost-stress total R minus half closed-trade drawdown R, subject to at least 30 complete validation fills. All 27 configurations are recorded. If none meets the sample minimum, the first grid candidate is retained for diagnostic scoring only and tuning is marked unsuccessful. Models are frozen before test scoring; no refit includes validation or test.</p>
<p class="note"><b>Validation failed.</b> EURUSD and GBPUSD have no candidate meeting the validation sample minimum. USDJPY meets the sample count but loses under the cost assumption. The resulting test scores also lose. Orders are disabled by default (<code>EnableOrders=false</code>). These are diagnostic candidates, not validated production strategies; deliberately set <code>EnableOrders=true</code> only for further tester/demo research.</p>
<h2>Practical limits and installation</h2>
<p>The underlying setups were machine selected, not human reviewed. Sample prevalence differs from all market minutes. The EA reproduces the causal signal/plan rules, but offline dataset quality gates include native-vs-M1 volume reconciliation over long windows. The EA checks closed-bar readiness, window spans and recent continuity; it does not re-audit every historical input window. This can materially change the number of trades, especially in training. Live quotes, broker constraints and feature seeding can differ. The supplied tester runs verify generated-tick operation on the source broker; they do not establish execution quality on another broker or real ticks.</p>
<p>Copy <code>NSL_ShotTrader.ex5</code> to MT5's <code>MQL5/Experts</code> folder, refresh Navigator, and attach the same file once to each desired EURUSD, GBPUSD and USDJPY M1 chart (broker suffixes supported). Models select automatically by pair. No Python, model file, DLL or network access is required by the EA. For deliberate tester/demo experiments, enable Algo Trading and set <code>EnableOrders=true</code>. Real-account entry also requires <code>AllowLiveAccount=true</code>. Source is included for audit and recompilation.</p>
<p>Default risk is 0.25% of equity per chart, one pending order or position per symbol. There is no martingale. Stops and targets use pre-known structure, pending plans expire after 180 seconds and positions have a 600-second time stop. Indicators are seeded from 1,200 closed bars on each timeframe. Restarting starts a 15-minute signal warm-up; terminal disconnection prevents EA-managed cancellations and time stops, while broker-side SL/TP remain subject to execution.</p>
<p>Reproduce with <code>python3 tools/train_ea.py</code>. Detailed coefficients, split counts, thresholds, validation grid, status counts and baseline evaluations are in <code>training_results.json</code>. Per-event scores and accepted scenarios are in CSV files beside this report. Compilation, reproducibility and tester evidence are in <code>verification.json</code>.</p>
<p>Official execution API references: <a href="https://www.mql5.com/en/docs/series/copyrates">CopyRates</a>, <a href="https://www.mql5.com/en/docs/standardlibrary/tradeclasses/ctrade/ctradebuystop">CTrade BuyStop</a>, <a href="https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties">order filling and lifetime properties</a>.</p>'''
    (OUT / 'TRAIN_TEST_REPORT.html').write_text(text)


def render_ea(result):
    def arr(a):
        if isinstance(a[0], list):
            return '{' + ',\n'.join(arr(v) for v in a) + '}'
        return '{' + ','.join(format(v, '.17g') for v in a) + '}'
    code = (ROOT / 'tools/NSL_ShotTrader.template.mq5').read_text()
    for token, field in [('MEANS', 'mean'), ('SCALES', 'scale'), ('COEFFICIENTS', 'coefficients')]:
        code = code.replace('@' + token + '@', arr([result['models'][p][field] for p in PAIRS]))
    code = code.replace('@THRESHOLDS@', arr([result['models'][p]['threshold'] for p in PAIRS]))
    (OUT / 'NSL_ShotTrader.mq5').write_text(code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--render-only', action='store_true', help='Render frozen results without fitting or scoring again')
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    if args.render_only:
        result = json.loads((OUT / 'training_results.json').read_text())
        render_ea(result)
        render_report(result)
        return
    frame = extract()
    for col in ['gross_r', 'initial_risk_pips']:
        frame[col] = pd.to_numeric(frame[col], errors='coerce')
    # Verify chronological interval isolation globally, across symbols.
    for left, right in [('train', 'validation'), ('validation', 'test')]:
        assert frame.loc[frame.split == left, 'label_end_server'].max() < frame.loc[frame.split == right, 'input_start_server'].min()
    result = dict(version=VERSION, feature_names=FEATURES, cost_assumption_pips=COST_PIPS,
                  manifest_sha256=hashlib.sha256((DATA / 'manifest.csv').read_bytes()).hexdigest(),
                  event_features_sha256=hashlib.sha256((OUT / 'event_features.csv').read_bytes()).hexdigest(),
                  snapshot_sha256=hashlib.sha256((DATA / 'audit/collection_snapshot.json').read_bytes()).hexdigest(),
                  runtime=dict(python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__),
                  models={})
    pending = {}
    # Tune every symbol before evaluating any test result.
    for pair in PAIRS:
        f = frame[frame.pair == pair].copy()
        train = f[(f.split == 'train') & (f.label_status == 'complete') & f.model_features_valid]
        x = train[FEATURES].to_numpy(float)
        mean, scale = x.mean(axis=0), x.std(axis=0)
        scale = np.maximum(scale, 1e-8)
        design = lambda d: np.c_[np.ones(len(d)), np.clip(np.nan_to_num((d[FEATURES].to_numpy(float) - mean) / scale), -8, 8)]
        y = (train.gross_r - COST_PIPS / train.initial_risk_pips > 0).to_numpy(float)
        grid, best = [], None
        validation = f[f.split == 'validation'].copy()
        for penalty in LAMBDAS:
            b = fit(design(train), y, penalty)
            validation['score'] = sigmoid(design(validation) @ b)
            validation.loc[~validation.model_features_valid, 'score'] = -1
            for threshold in THRESHOLDS:
                ev, _ = evaluate(validation, threshold)
                m = ev['assumed_cost_complete']
                objective = m['sum_r'] - 0.5 * m['max_drawdown_r'] if m['trades'] >= 30 else -1e9
                grid.append(dict(penalty=penalty, threshold=threshold, objective=objective, evaluation=ev))
                if best is None or objective > best[0]:
                    best = (objective, penalty, threshold, b.copy())
        _, penalty, threshold, b = best
        result['models'][pair] = dict(mean=mean.tolist(), scale=scale.tolist(), coefficients=b.tolist(),
            penalty=penalty, threshold=threshold, train_complete_fills=len(train),
            tuning_status='selected_on_validation' if best[0] > -1e9 else 'failed_validation_sample_minimum_diagnostic_fallback',
            train_positive_rate=float(y.mean()), validation_grid=grid,
            split_counts=f.groupby(['split', 'label_status']).size().unstack(fill_value=0).to_dict('index'),
            evaluation={}, baseline={})
        pending[pair] = (f, design(f), b, threshold)
        print(f'FROZEN {pair}: penalty={penalty}, threshold={threshold}, train fills={len(train)}', flush=True)
    (OUT / 'frozen_models.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    all_scored, all_accepted = [], []
    for pair, (f, x, b, threshold) in pending.items():
        f['score'] = sigmoid(x @ b)
        f.loc[~f.model_features_valid, 'score'] = -1
        for split in ['train', 'validation', 'test']:
            subset = f[f.split == split]
            ev, accepted = evaluate(subset, threshold)
            result['models'][pair]['evaluation'][split] = ev
            result['models'][pair]['baseline'][split] = evaluate(subset, 0)[0]
            all_accepted.append(accepted)
        all_scored.append(f)
    pd.concat(all_scored).to_csv(OUT / 'event_predictions.csv', index=False)
    pd.concat(all_accepted).to_csv(OUT / 'accepted_scenarios.csv', index=False)
    (OUT / 'training_results.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    render_ea(result)
    render_report(result)
    print('Wrote trained EA and report to release/', flush=True)


if __name__ == '__main__':
    main()
