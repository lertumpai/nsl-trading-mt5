"""Export broker-native chart history to month-grouped CSV, with resumable audits.

This is an offline data collector, not an Expert Advisor. It cannot place orders.
"""
from __future__ import annotations
import argparse, configparser, csv, hashlib, json, os, sys, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from mt5_mcp import Client

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'dataset'
AUDIT=DATA/'audit'
PAIRS={'EURUSD':'EURUSD#','USDJPY':'USDJPY#','GBPUSD':'GBPUSD#'}
TFS={'M1':60,'M5':300,'M15':900}
REQUESTED_START=datetime(2016,11,8)
FIELDS=['source_symbol','pair','timeframe','open_time_server','close_time_server',
        'open','high','low','close','tick_volume','real_volume','bar_spread_points',
        'gap_before_seconds','gap_class']

def decode(result):
    if result.get('isError'): raise RuntimeError(str(result.get('content','Tool error'))[:500])
    if isinstance(result.get('structuredContent'),dict):return result['structuredContent']
    for c in result.get('content',[]):
        if c.get('type')=='text':
            try:return json.loads(c['text'])
            except ValueError:pass
    raise RuntimeError('No structured JSON in MT5 response')

def iso(d):return d.isoformat(timespec='seconds')
def parse(s):return datetime.fromisoformat(s.replace('Z','+00:00')).replace(tzinfo=None)
def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def atomic_json(p,d):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.partial');tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');tmp.replace(p)
def csv_write(p,fields,rows):
    p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.csv.partial')
    with tmp.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='raise');w.writeheader();w.writerows(rows)
    tmp.replace(p)
    return hashlib.sha256(p.read_bytes()).hexdigest()

class Collector:
    def __init__(self):
        AUDIT.mkdir(parents=True,exist_ok=True);self.c=Client();self.last_request=0
        self.log=AUDIT/'requests.jsonl'
    def request(self,name,args):
        for attempt in range(4):
            begin=time.monotonic()
            try:
                result=decode(self.c.call(name,args))
                if result.get('ok') is False:
                    raise RuntimeError(json.dumps(result)[:600])
                record={'at_utc':now(),'tool':name,'arguments':args,'elapsed_seconds':round(time.monotonic()-begin,3),
                        'ok':result.get('ok',True),'rows':len(result.get('history',[])),
                        'data_available_from':result.get('data_available_from')}
                with self.log.open('a') as f:f.write(json.dumps(record)+'\n')
                return result
            except Exception as exc:
                with self.log.open('a') as f:f.write(json.dumps({'at_utc':now(),'tool':name,'arguments':args,'attempt':attempt+1,'error':str(exc)[:600]})+'\n')
                if attempt==3:raise
                time.sleep(1+attempt)
                self.c=Client()
    def history(self,symbol,tf,a,b,limit=45000):
        return self.request('get_chart_history',{'symbol':symbol,'period':tf,'datetime_from':iso(a),'datetime_to':iso(b),'limit':limit})
    def warmup(self):
        records=[]
        for pair,symbol in PAIRS.items():
            for tf,seconds in TFS.items():
                prior=None
                for a,b in [(datetime(2016,10,1),datetime(2016,11,1)),(datetime(2016,11,1),REQUESTED_START)]:
                    previous=None
                    for attempt in range(5):
                        result=self.history(symbol,tf,a,b);rows=result.get('history',[])
                        if previous is not None and rows==previous:break
                        previous=rows
                    else:raise RuntimeError('Warmup history did not stabilize')
                    output=[]
                    for r in sorted(rows,key=lambda x:x['time']):
                        t=parse(r['time'])
                        if not a<=t<b:continue
                        gap=max(0,int((t-prior).total_seconds())-seconds) if prior else 0
                        output.append({'source_symbol':symbol,'pair':pair,'timeframe':tf,'open_time_server':iso(t),'close_time_server':iso(t+timedelta(seconds=seconds)),
                            'open':r['open'],'high':r['high'],'low':r['low'],'close':r['close'],'tick_volume':r.get('tick_volume',''),'real_volume':r.get('real_volume',''),
                            'bar_spread_points':r.get('spread',''),'gap_before_seconds':gap,'gap_class':'unknown' if gap else 'none'})
                        prior=t
                    rel=Path(pair)/'warmup'/a.strftime('%Y-%m')/(tf+'.csv');digest=csv_write(DATA/rel,FIELDS,output)
                    records.append({'pair':pair,'timeframe':tf,'path':str(rel),'rows':len(output),'first':output[0]['open_time_server'] if output else '',
                        'last':output[-1]['open_time_server'] if output else '', 'sha256':digest})
                print(f'WARMUP {pair} {tf}',flush=True)
        csv_write(DATA/'warmup_manifest.csv',['pair','timeframe','path','rows','first','last','sha256'],records)
    def initialize(self):
        p=AUDIT/'collection_snapshot.json'
        if p.exists():
            saved=json.loads(p.read_text())
            saved['requested_start_server']=iso(REQUESTED_START)
            atomic_json(p,saved)
            return saved
        ti=self.request('get_time_information',{})
        last=ti.get('trade_server_last_known_time')
        if not last:raise RuntimeError('MT5 has not supplied a broker server timestamp')
        cutoff=parse(last).replace(second=0,microsecond=0)
        config_path=Path('/Users/sorawit/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/config/common.ini')
        cb=config_path.read_bytes();config=configparser.ConfigParser(interpolation=None)
        config.read_string(cb.decode('utf-16') if cb.startswith(b'\xff\xfe') else cb.decode('utf-8'))
        server=config.get('Common','Server',fallback='unverified')
        snapshot={'created_at_utc':now(),'cutoff_server':iso(cutoff),'time_information_raw':ti,
                  'time_mapping_status':'unverified; preserve chart-server timestamps; do not infer historical UTC offset from local computer offset',
                  'broker_server':server,'source':'MT5 native get_chart_history','pairs':PAIRS,
                  'timeframes':TFS,'requested_start_server':iso(REQUESTED_START),'series':{},'symbol_metadata_at_export':{}}
        for pair,symbol in PAIRS.items():
            md=self.request('get_marketwatch_symbols',{'symbol':symbol,'include_hidden':True,'limit':1})
            symbols=md.get('symbols',[])
            if len(symbols)!=1:raise RuntimeError('Exact symbol unavailable: '+symbol)
            snapshot['symbol_metadata_at_export'][pair]={k:symbols[0][k] for k in ['symbol','description','digits','point','currency_base','currency_profit','contract_size'] if k in symbols[0]}
            for tf in TFS:
                probe=self.history(symbol,tf,datetime(1970,1,1),cutoff,1)
                first=probe.get('data_available_from')
                if not first:raise RuntimeError(f'No history start metadata for {symbol} {tf}')
                snapshot['series'][pair+'_'+tf]={'reported_available_from':first,'probe_last_rows':probe.get('history',[])}
        atomic_json(p,snapshot);return snapshot
    def run(self,only_pair=None):
        snapshot=self.initialize();cutoff=parse(snapshot['cutoff_server'])
        for pair,symbol in PAIRS.items():
            if only_pair and pair!=only_pair:continue
            for tf,seconds in TFS.items():
                info=snapshot['series'][pair+'_'+tf]
                first=max(parse(info['reported_available_from']),REQUESTED_START)
                prior_time=None
                for year in range(first.year,cutoff.year+1):
                    done=AUDIT/'checkpoints'/f'{pair}_{tf}_{year}.json'
                    if done.exists():
                        cp=json.loads(done.read_text())
                        if cp.get('status')=='complete' and cp.get('method')=='monthly_stable_v2' and cp.get('cutoff_server')==snapshot['cutoff_server'] and all((DATA/x['path']).exists() and hashlib.sha256((DATA/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in cp['files']):
                            if cp.get('last_time'):prior_time=parse(cp['last_time'])
                            print(f'RESUME {pair} {tf} {year}',flush=True);continue
                    a=max(datetime(year,1,1),first);b=min(datetime(year+1,1,1),cutoff)
                    if a>=b:continue
                    # MT5 can initially return a partial cache during synchronization even with ok=true.
                    # Request every month explicitly and require two identical successive responses.
                    rows=[];requested_months=[]
                    for month in range(1,13):
                        ma=max(datetime(year,month,1),a)
                        mb=min(datetime(year+1,1,1) if month==12 else datetime(year,month+1,1),b)
                        if ma>=mb:continue
                        requested_months.append(month);previous=None;stable=False
                        for sync_attempt in range(5):
                            part=self.history(symbol,tf,ma,mb);rr=part.get('history',[])
                            if len(rr)>=45000:raise RuntimeError(f'Month response limit reached: {symbol} {tf} {ma}')
                            if previous is not None and rr==previous:stable=True;break
                            previous=rr
                        if not stable:raise RuntimeError(f'History did not stabilize: {symbol} {tf} {ma}')
                        rows.extend(rr)
                    # Never rely on a terminal response being ordered, complete, or unique.
                    ordered={}
                    for row in rows:
                        t=parse(row['time'])
                        if not a<=t<b or t+timedelta(seconds=seconds)>cutoff:continue
                        key=iso(t)
                        if key in ordered and ordered[key]!=row:raise RuntimeError(f'Conflicting duplicate: {symbol} {tf} {key}')
                        ordered[key]=row
                    monthly={m:[] for m in requested_months};gaps=[];bad=[];deltas=[]
                    for key in sorted(ordered):
                        row=ordered[key];t=parse(key)
                        o,h,l,c=[float(row[k]) for k in ['open','high','low','close']]
                        if not (0<l<=min(o,c)<=max(o,c)<=h):bad.append(key)
                        gap=max(0,int((t-prior_time).total_seconds())-seconds) if prior_time else 0
                        if prior_time:deltas.append(int((t-prior_time).total_seconds()))
                        if gap:gaps.append({'after_server':iso(prior_time),'before_server':key,'missing_elapsed_seconds':gap,'classification':'unknown_includes_market_closures'})
                        monthly[t.month].append({'source_symbol':symbol,'pair':pair,'timeframe':tf,'open_time_server':key,'close_time_server':iso(t+timedelta(seconds=seconds)),
                            'open':row['open'],'high':row['high'],'low':row['low'],'close':row['close'],
                            'tick_volume':row.get('tick_volume',''),'real_volume':row.get('real_volume',''),'bar_spread_points':row.get('spread',''),
                            'gap_before_seconds':gap,'gap_class':'unknown' if gap else 'none'})
                        prior_time=t
                    files=[]
                    for month,rr in sorted(monthly.items()):
                        rel=Path(pair)/'history'/f'{year:04d}-{month:02d}'/f'{tf}.csv'
                        digest=csv_write(DATA/rel,FIELDS,rr)
                        files.append({'path':str(rel),'rows':len(rr),'first':rr[0]['open_time_server'] if rr else None,'last':rr[-1]['open_time_server'] if rr else None,'sha256':digest})
                    cp={'status':'complete','method':'monthly_stable_v2','pair':pair,'symbol':symbol,'timeframe':tf,'year':year,'cutoff_server':snapshot['cutoff_server'],
                        'requested_from':iso(a),'requested_to':iso(b),'reported_available_from':info['reported_available_from'],
                        'rows':len(ordered),'files':files,'gaps':gaps,'invalid_ohlc':bad,
                        'last_time':iso(prior_time) if prior_time else None,
                        'native_interval_count':sum(x==seconds for x in deltas),'observed_interval_count':len(deltas),
                        'notes':'Historical gaps include weekends, holidays and absent source bars; classification unverified. Native real_volume omitted by source is blank, never invented.'}
                    if bad:raise RuntimeError(f'Invalid OHLC rows: {pair} {tf} {year}: {len(bad)}')
                    atomic_json(done,cp)
                    print(f'EXPORTED {pair} {tf} {year}: {len(ordered):,} rows; {len(files)} months',flush=True)
        self.summarize(snapshot)
    def summarize(self,snapshot):
        records=[];coverage=[]
        for pair in PAIRS:
            for tf in TFS:
                cps=[json.loads(p.read_text()) for p in sorted((AUDIT/'checkpoints').glob(f'{pair}_{tf}_*.json'))]
                files=[f for cp in cps for f in cp['files']]
                records.extend({'pair':pair,'timeframe':tf,**f} for f in files)
                starts=[f['first'] for f in files if f['first']];ends=[f['last'] for f in files if f['last']]
                coverage.append({'pair':pair,'timeframe':tf,'source_symbol':PAIRS[pair],'reported_available_from':snapshot['series'][pair+'_'+tf]['reported_available_from'],
                    'first_exported':min(starts) if starts else None,'last_exported':max(ends) if ends else None,'rows':sum(cp['rows'] for cp in cps),
                    'monthly_files':len(files),'invalid_ohlc':sum(len(cp['invalid_ohlc']) for cp in cps),
                    'gap_intervals_including_market_closures':sum(len(cp['gaps']) for cp in cps),
                    'native_interval_count':sum(cp['native_interval_count'] for cp in cps),'observed_interval_count':sum(cp['observed_interval_count'] for cp in cps)})
        atomic_json(AUDIT/'history_coverage.json',{'snapshot':snapshot,'series':coverage,'created_at_utc':now()})
        csv_write(DATA/'history_manifest.csv',['pair','timeframe','path','rows','first','last','sha256'],records)
        print('COVERAGE '+json.dumps(coverage),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--probe',action='store_true');p.add_argument('--warmup',action='store_true');p.add_argument('--pair',choices=list(PAIRS));args=p.parse_args()
    c=Collector()
    if args.warmup:c.warmup()
    elif args.probe:print(json.dumps(c.initialize(),indent=2))
    else:c.run(args.pair)
