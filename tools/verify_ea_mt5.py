"""Compile the frozen EA and run calendar-period MT5 research backtests.

Uses the existing credential through mt5_mcp.Client; never prints credentials,
attaches an EA to a live chart, or invokes trade_* tools. Tester orders only.
"""
from pathlib import Path
import argparse, hashlib, json, time
from mt5_mcp import Client
from collect_history import decode

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'release'
REMOTE = r'C:\Program Files\MetaTrader 5\MQL5'


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pair', choices=['EURUSD', 'GBPUSD', 'USDJPY'])
    parser.add_argument('--split', choices=['train', 'test'])
    args = parser.parse_args()
    runs = OUT / 'mt5_runs'
    runs.mkdir(exist_ok=True)
    terminal = Client()
    for pair in ([args.pair] if args.pair else ['EURUSD', 'GBPUSD', 'USDJPY']):
        for split in ([args.split] if args.split else ['train', 'test']):
            name = f'{pair}_{split}'
            report_path = runs / (name + '_report.json')
            if report_path.exists():
                print('SAVED ' + name, flush=True)
                continue
            config = REMOTE + '\\Profiles\\Tester\\NSL_' + name + '.ini'
            inputs = REMOTE + '\\Profiles\\Tester\\NSL_' + name + '.set'
            dates = ('2016-11-08T00:00:00', '2024-01-01T00:00:00') if split == 'train' else ('2025-01-08T00:00:00', '2026-11-07T00:00:00')
            prepared = decode(terminal.call('tester_prepare_config', dict(
                mql5_program_path=REMOTE + '\\Experts\\NSL_ShotTrader.ex5',
                symbol=pair + '#', model='m1 ohlc', timeframe='M1',
                optimization=False, from_date=dates[0], to_date=dates[1],
                deposit=10000, deposit_currency='USD', leverage=100,
                execution_delay=0, profit_in_pips=False, output_path=config)))
            save(runs / (name + '_config.json'), prepared)
            prepared_inputs = decode(terminal.call('tester_prepare_inputs', dict(
                mql5_program_path=REMOTE + '\\Experts\\NSL_ShotTrader.ex5',
                parameters=['EnableOrders=true', 'AllowLiveAccount=false', 'RiskPercent=0.25',
                            'ThresholdOverride=-1', 'MagicNumber=2601108', 'MaxQuoteAgeSeconds=10'], output_path=inputs)))
            save(runs / (name + '_inputs.json'), prepared_inputs)
            save(runs / (name + '_provenance.json'), dict(
                ex5_sha256=hashlib.sha256((OUT / 'NSL_ShotTrader.ex5').read_bytes()).hexdigest(),
                symbol=pair+'#', model='M1 OHLC', from_date=dates[0], to_date_exclusive=dates[1],
                risk_percent=0.25, initial_deposit_usd=10000, leverage=100,
                enable_orders=True, live_account_allowed=False, execution_delay_ms=0))
            launch = decode(terminal.call('tester_run_backtest', dict(config_path=config, inputs_path=inputs, wait=False)))
            save(runs / (name + '_launch.json'), launch)
            print('START ' + name + ' ' + json.dumps(launch), flush=True)
            run_id = launch.get('run_id')
            if not run_id:
                raise RuntimeError('Tester did not provide run_id; inspect saved response')
            while True:
                status = decode(terminal.call('tester_get_status', {'run_id': run_id}))
                save(runs / (name + '_status.json'), status)
                print('STATUS ' + name + ' ' + json.dumps(status), flush=True)
                state = str(status.get('tester_status', status.get('status', status.get('state', '')))).lower()
                if state in ['completed', 'complete', 'finished', 'done', 'stopped'] or status.get('finished') is True:
                    break
                if state in ['error', 'failed', 'canceled', 'cancelled']:
                    raise RuntimeError('Tester failed: ' + json.dumps(status))
                # The tool wait is bounded; keep progress visible to the parent.
                terminal.call('tester_wait', {'run_id': run_id, 'timeout_sec': 30})
            report = decode(terminal.call('tester_get_report', {'run_id': run_id, 'file_format': 'json'}))
            save(report_path, report)
            print('DONE ' + name, flush=True)


if __name__ == '__main__':
    main()
