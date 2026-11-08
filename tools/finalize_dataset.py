"""Verify persisted CSV artifacts and produce Thai review/AI handoff documentation."""
from __future__ import annotations
import csv, hashlib, json, shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from collect_history import ROOT, DATA, AUDIT, PAIRS, FIELDS, atomic_json, csv_write
from build_events import BAR_FIELDS, WINDOWS, VERSION

def read_csv(p):
    with p.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def one(p):
    rows=read_csv(p)
    if len(rows)!=1:raise RuntimeError('Expected one row: '+str(p))
    return rows[0]
def require(ok,msg):
    if not ok:raise RuntimeError(msg)
def table(headers,rows):
    return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+''.join('| '+' | '.join(str(v).replace('|','/') for v in row)+' |\n' for row in rows)

def reciprocal_audit(cutoff):
    result=[]
    for pair in PAIRS:
        def times(tf):
            fs=sorted((DATA/pair/'warmup').glob('*/'+tf+'.csv'))+sorted((DATA/pair/'history').glob('*/'+tf+'.csv'))
            return pd.to_datetime(pd.concat([pd.read_csv(f,usecols=['open_time_server']) for f in fs],ignore_index=True).open_time_server)
        m1=times('M1')
        for tf,n in [('M5',5),('M15',15)]:
            expected=pd.DatetimeIndex(m1.dt.floor(str(n)+'min').unique())
            expected=expected[expected+pd.Timedelta(minutes=n)<=pd.Timestamp(cutoff)]
            missing=expected.difference(pd.DatetimeIndex(times(tf)))
            primary=missing[missing>=pd.Timestamp('2016-11-08')]
            require(len(primary)==0,'Missing native higher-timeframe bars in requested archive: '+pair+' '+tf)
            result.append({'pair':pair,'timeframe':tf,'missing_native_bars_with_observed_M1':len(missing),'missing_in_requested_ten_years':len(primary),
                'by_month':pd.Series(missing).dt.strftime('%Y-%m').value_counts().to_dict()})
    atomic_json(AUDIT/'reciprocal_tf_audit.json',result)
    return result

def main():
    coverage=json.loads((AUDIT/'history_coverage.json').read_text());snapshot=coverage['snapshot']
    reports={p:json.loads((AUDIT/(p+'_events_report.json')).read_text()) for p in PAIRS}
    histories=read_csv(DATA/'history_manifest.csv');warmup=read_csv(DATA/'warmup_manifest.csv')
    require(len(histories)==1089,'Expected 121 months × 9 series')
    for cp in (AUDIT/'checkpoints').glob('*.json'):
        d=json.loads(cp.read_text());require(d.get('method')=='monthly_stable_v2','Old checkpoint still present '+str(cp))
    for r in histories+warmup:
        p=DATA/r['path'];require(p.exists(),'Missing archive '+str(p))
        require(hashlib.sha256(p.read_bytes()).hexdigest()==r['sha256'],'Archive hash mismatch '+str(p))
    manifest=read_csv(DATA/'manifest.csv');require(len(manifest)==sum(x['events'] for x in reports.values()),'Event manifest count mismatch')
    require(len({r['event_id'] for r in manifest})==len(manifest),'Duplicate event IDs')
    reciprocal=reciprocal_audit(snapshot['cutoff_server'])
    expected_folders={r['relative_event_path'] for r in manifest};quarantined=[]
    for pair in PAIRS:
        for folder in (DATA/pair/'events').glob('*/*'):
            if folder.is_dir() and str(folder.relative_to(DATA)) not in expected_folders:
                destination=AUDIT/'superseded_events'/folder.relative_to(DATA)
                destination.parent.mkdir(parents=True,exist_ok=True)
                require(not destination.exists(),'Duplicate superseded event destination')
                shutil.move(str(folder),str(destination));quarantined.append(str(destination.relative_to(DATA)))
    atomic_json(AUDIT/'superseded_event_folders.json',quarantined)
    checked_bars=0;patterns=Counter();outcomes=Counter();splits=Counter();checks=[]
    event_names=['M1.csv','M5.csv','M15.csv','event.csv','levels.csv','plan.csv','outcome.csv']
    for number,r in enumerate(manifest,1):
        folder=DATA/r['relative_event_path'];decision=r['decision_time_server'];hasher=hashlib.sha256()
        for name in sorted(event_names):
            p=folder/name;require(p.exists(),'Missing event file '+str(p));b=p.read_bytes();hasher.update(name.encode()+b'\0'+b+b'\0')
            if name in ['M1.csv','M5.csv','M15.csv']:
                lines=b.decode().splitlines();tf=name[:-4]
                require(next(csv.reader([lines[0]]))==BAR_FIELDS,'Wrong candle schema '+str(p))
                require(len(lines)-1==WINDOWS[tf],'Incorrect event row count '+str(p))
                first=next(csv.reader([lines[1]]));last=next(csv.reader([lines[-1]]))
                require(first[0]==r['event_id']==last[0] and first[1]==tf==last[1],'Wrong event identity '+str(p))
                require(first[2]==str(-WINDOWS[tf]+1) and last[2]=='0','Wrong relative bar indices '+str(p))
                require(last[4]<=decision,'Future candle leaked into '+str(p))
                checked_bars+=WINDOWS[tf]
        require(hasher.hexdigest()==r['event_content_sha256'],'Event hash mismatch '+r['event_id'])
        event=one(folder/'event.csv');plan=one(folder/'plan.csv');outcome=one(folder/'outcome.csv')
        require(event['event_id']==r['event_id']==plan['event_id']==outcome['event_id'],'Broken CSV join '+r['event_id'])
        require(event['decision_time_server']==decision,'Wrong decision cutoff')
        level_rows=read_csv(folder/'levels.csv');level_ids={x['level_id'] for x in level_rows}
        for level in level_rows:
            require(level['known_at_server']<=decision,'Future pivot confirmation '+r['event_id'])
            require(level['event_id']==r['event_id'],'Broken level join')
        for key in ['stop_basis_level_id','target_basis_level_id']:
            if plan[key]:require(plan[key] in level_ids,'Unresolved plan-to-level reference '+key+' '+r['event_id'])
        if plan['side']!='no_trade':
            en,sl,tp=map(float,[plan['entry_reference_price'],plan['stop_loss_price'],plan['take_profit_price']])
            require(sl<en<tp if plan['side']=='buy' else tp<en<sl,'Invalid entry/SL/TP geometry '+r['event_id'])
            require(plan['plan_status']=='complete','Incomplete positive plan '+r['event_id'])
        require(outcome['outcome_source'] in ['simulated_bars','not_evaluated'],'Unexpected execution provenance')
        require(outcome['net_r']=='' and outcome['net_pnl_pips']=='','Unknown costs must not produce net returns')
        if outcome['label_status']=='complete':
            sign=1 if plan['side']=='buy' else -1
            gross=sign*(float(outcome['exit_fill_price'])-float(outcome['entry_fill_price']))/float(event['pip_size'])
            require(abs(gross-float(outcome['gross_pnl_pips']))<1e-6,'Incorrect gross P&L arithmetic')
        patterns[event['pattern_label']]+=1;outcomes[outcome['label_status']]+=1;splits[r['split']]+=1
        if number%2000==0:print(f'VERIFIED {number:,}/{len(manifest):,} events',flush=True)
    for a,b in [('train','validation'),('validation','test')]:
        aa=[r['label_end_server'] for r in manifest if r['split']==a];bb=[r['input_start_server'] for r in manifest if r['split']==b]
        require(aa and bb,'Empty chronological split')
        require(max(aa)<min(bb),'Cross-split information interval overlap')
        checks.append({'earlier_split':a,'latest_label_end':max(aa),'later_split':b,'earliest_input_start':min(bb),'non_overlap':'passed'})
    require(not list(DATA.glob('*/events/*/*/*.partial')),'Incomplete event files remain')
    validation={'status':'passed','verified_at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'history_files':len(histories),'history_rows':sum(int(r['rows']) for r in histories),'warmup_files':len(warmup),
        'event_count':len(manifest),'event_csv_files':len(manifest)*7,'event_input_rows':checked_bars,
        'patterns':dict(patterns),'outcome_statuses':dict(outcomes),'split_counts':dict(splits),'chronological_interval_checks':checks,'reciprocal_tf_checks':reciprocal,
        'superseded_folders_quarantined':len(quarantined),
        'checks':['Archive SHA-256','Event SHA-256','Expected CSV files and headers','All event candle row counts','Event/plan/outcome/level joins',
          'No future candle at cutoff','No future pivot confirmation','Positive entry/SL/TP geometry','Gross P&L arithmetic','Unknown net costs stay blank','Full-interval cross-split separation'],
        'limits':['Native source gaps are preserved, not repaired.','Real tick execution is not verified.','Automated candidates are not human-reviewed aesthetic labels.','UTC conversion and historical commissions are unavailable.']}
    atomic_json(AUDIT/'verification_report.json',validation)
    actual_schema(coverage,manifest)
    docs(coverage,reports,manifest,validation)
    atomic_json(DATA/'dataset_spec.json',{'version':'1.1.0','status':'collection_and_event_export_complete_with_documented_source_gaps',
        'pairs':list(PAIRS),'timeframes':list(WINDOWS),'requested_start_server':'2016-11-08T00:00:00','cutoff_server':snapshot['cutoff_server'],
        'schema':'schema.json','rules':'config/selection_rules_v1.json','history_manifest':'history_manifest.csv','event_manifest':'manifest.csv',
        'verification':'audit/verification_report.json','readme':'README.md','review':'REVIEW_REPORT.md','handoff':'AI_HANDOFF.md',
        'no_ea_created':True,'no_trades_sent':True,'models_trained':False})
    print(json.dumps(validation,ensure_ascii=False),flush=True)

def actual_schema(coverage,manifest):
    draft=json.loads((DATA/'schema_v1/schema.json').read_text());tables={}
    extra={
      'gross_r':('float','future_label','Gross executable-price P&L / initial risk. No commission or measured slippage deduction.'),
      'execution_model':('string','audit','Explicit bar simulation assumptions; not actual trades.'),
      'commission_status':('enum:unknown','audit','Historical round-trip commission has not been established.'),
      'slippage_status':('enum:not_measured','audit','Actual slippage was not collected.'),
      'ticks_status':('enum:not_requested','audit','Real tick history was not requested for this collection.'),
      'execution_time_resolution':('enum:minute_bar_interval_proxy','audit','Fill/exit time is a minute interval boundary proxy, not an observed exact tick.'),
      'entry_interval_end_server':('datetime','future_label','Exclusive end of the minute within which the simulated entry happened.'),
      'exit_interval_end_server':('datetime','future_label','Exclusive end of exit interval, or the boundary used for a time stop.'),
      'entry_exit_same_bar_ambiguous':('bool','audit','Entry and possible exit share a candle and order cannot be resolved.'),
    }
    for file,t in draft['tables'].items():
        if file.startswith('event_template/'):
            new='events/'+file.removeprefix('event_template/')
            tables[new]={'fields':t['fields'],'optional':file.endswith('ticks.csv')}
    for n,(typ,role,meaning) in extra.items():tables['events/outcome.csv']['fields'].append({'name':n,'type':typ,'role':role,'required':False,'meaning':meaning})
    for f in tables['events/outcome.csv']['fields']:
        if f['name'] in ['entry_fill_time_server','exit_time_server']:f['meaning']='Minute-bar boundary proxy for the simulated execution interval; not an observed exact tick timestamp. See execution_time_resolution and interval_end fields.'
        if f['name']=='holding_seconds':f['meaning']='Elapsed seconds between simulated minute-bar boundary proxies; approximate, not an observed tick holding duration.'
    for f in tables['events/levels.csv']['fields']:
        if f['name']=='level_type':f['type']='enum:swing_high|swing_low|signal_high|signal_low|pullback_high|pullback_low|breakout_zone|range_midpoint'
        if f['name']=='level_id':f['meaning']='Unique within event; includes latest/previous confirmed swings, signal extremes, observed pullback extremes and derived targets. Referenced by plan.csv.'
    raw=[];bar_map={f['name']:f for f in tables['events/M1.csv']['fields']}
    for n in FIELDS:
        raw.append(bar_map.get(n,{'name':n,'type':'string','role':'context' if n in ['source_symbol','pair','timeframe'] else 'audit','required':True,'meaning':n}))
    for tf in WINDOWS:tables['history/'+tf+'.csv']={'fields':raw}
    mf=draft['tables']['manifest.csv']['fields'].copy();existing={r['name'] for r in mf}
    for n in manifest[0]:
        if n not in existing:mf.append({'name':n,'type':'string','role':'audit' if n in ['plan_status','outcome_label_status'] else 'target','required':True,'meaning':n+' copied from the event metadata/plan/outcome; not a default model input.'})
    tables['manifest.csv']={'fields':mf}
    schema={'version':'1.1.0','status':'actual_export','timeframes':list(WINDOWS),'pairs':list(PAIRS),'broker_server':coverage['snapshot']['broker_server'],
        'requested_start_server':'2016-11-08T00:00:00','cutoff_server':coverage['snapshot']['cutoff_server'],
        'history_layout':'{pair}/history/{YYYY-MM}/{TF}.csv','event_layout':'{pair}/events/{YYYY-MM-DD}/{HH-mm-ss}_{pattern}_{side}/',
        'warmup_layout':'{pair}/warmup/{YYYY-MM}/{TF}.csv','csv':draft['csv'],'indicator_definitions':draft['indicators'],
        'selection_rule_file':'config/selection_rules_v1.json','tables':tables,
        'time_mapping_status':'unverified broker chart time, UTC fields blank','real_ticks_collected':False,
        'source_notes':'Native MT5 bars preserved. real_volume is blank because the history MCP response does not expose it. Per-bar spread is not the actual entry/exit quote path.',
        'model_input_default':'Only approved causal numeric columns in events/M1.csv, M5.csv and M15.csv. Plan/side/pattern are targets; outcome values are future labels.',
        'quality_gate':'Event windows exclude native OHLC/tick-volume discrepancies relative to observed M1 aggregation. Raw history keeps original values.'}
    atomic_json(DATA/'schema.json',schema)
    rows=[]
    for file,t in tables.items():
        for f in t['fields']:rows.append({'file':file,'column':f['name'],'type':f['type'],'role':f['role'],'required':int(f['required']),'definition':f['meaning']})
    csv_write(DATA/'data_dictionary.csv',['file','column','type','role','required','definition'],rows)
    parts=['# คำอธิบายคอลัมน์ CSV ที่ส่งออกจริง\n\nSchema version 1.1.0 อ้างอิงไฟล์ `schema.json` ส่วน `schema_v1/` เป็นแบบร่างก่อนดึงข้อมูล\n',
        'ค่าที่ไม่มีข้อมูลใช้ช่องว่าง ไม่แทนด้วย 0 ตัวเลขทศนิยมใช้จุด Boolean ใช้ 0/1 เวลาเซิร์ฟเวอร์ไม่ใส่ offset ที่ยังไม่ยืนยัน\n',
        'บทบาท `input` และ `input_optional` เป็นข้อมูลที่ทราบ ณ เวลานั้น `target` เป็นคำตอบเชิงแผน `future_label` เป็นผลภายหลัง ส่วน `audit`, `split_control`, `join_key` ไม่ใช่ feature ตามค่าเริ่มต้น\n']
    for file,t in tables.items():parts.append('\n## '+file+'\n\n'+table(['Column','Type','Role','Meaning'],[[f['name'],f['type'],f['role'],f['meaning']] for f in t['fields']]))
    (DATA/'DATA_DICTIONARY.md').write_text('\n'.join(parts),encoding='utf-8')

def docs(coverage,reports,manifest,v):
    s=coverage['snapshot'];rows=coverage['series'];total=v['history_rows'];events=v['event_count'];controls=v['patterns'].get('no_setup',0)
    perpair=[[p,reports[p]['events'],reports[p]['patterns'].get('no_setup',0),reports[p]['splits'].get('train',0),reports[p]['splits'].get('validation',0),reports[p]['splits'].get('test',0),reports[p]['splits'].get('excluded',0)] for p in PAIRS]
    coverage_table=table(['Pair','TF','First bar open (server)','Last bar open (server)','Rows','Gap intervals incl. closures'],[[r['pair'],r['timeframe'],r['first_exported'],r['last_exported'],f"{r['rows']:,}",r['gap_intervals_including_market_closures']] for r in rows])
    event_table=table(['Pair','Events incl. controls','No-setup controls','Train','Validation','Test','Excluded'],perpair)
    pattern_table=table(['Pattern','Count'],[[k,f'{n:,}'] for k,n in sorted(v['patterns'].items())])
    outcome_table=table(['Label status','Count'],[[k,f'{n:,}'] for k,n in sorted(v['outcome_statuses'].items())])
    cross=[]
    for p,r in reports.items():
        for x in r['cross_tf_audit']:cross.append([p,x['timeframe'],x['no_observed_m1_rows'],x['ohlc_mismatches'],x['tick_volume_mismatches']])
    gaps=[]
    for cp in (AUDIT/'checkpoints').glob('*_M1_*.json'):
        q=json.loads(cp.read_text())
        for g in q['gaps']:
            if g['missing_elapsed_seconds']>7*86400:gaps.append([q['pair'],g['after_server'],g['before_server'],round(g['missing_elapsed_seconds']/86400,2)])
    gaps.sort(key=lambda x:x[-1],reverse=True)
    gap_table=table(['Pair','After bar','Before bar','Elapsed gap days'],gaps[:25]) if gaps else 'ไม่พบช่องว่างระหว่างแท่ง M1 ยาวเกิน 7 วัน\n'
    review=f'''# รายงานตรวจชุดข้อมูล

สถานะ: ดึงประวัติและส่งออกช็อตเสร็จแล้ว โดยเก็บช่องว่างของข้อมูลต้นทางและข้อจำกัดตามจริง

## ขอบเขตที่ทำเสร็จ

- เริ่มช่วงที่ร้องขอ: 2016-11-08T00:00:00 ตามเวลาบนกราฟเซิร์ฟเวอร์
- ตัดยอดข้อมูลที่แท่งปิดไม่เกิน: {s['cutoff_server']} ตามเวลาเซิร์ฟเวอร์
- โบรกเกอร์เซิร์ฟเวอร์: {s['broker_server']}
- สัญลักษณ์จริง: EURUSD#, USDJPY#, GBPUSD#
- ประวัติ: {total:,} แท่งใน {v['history_files']:,} CSV รายเดือน ครบ 121 เดือนปฏิทินที่เกี่ยวข้อง × 3 คู่ × 3 TF
- ช็อตและตัวอย่างควรไม่เข้า: {events:,} เหตุการณ์ ({controls:,} controls) รวม {v['event_csv_files']:,} CSV และ {v['event_input_rows']:,} แถวข้อมูลแท่งในหน้าต่างช็อต
- ข้อมูล warmup แยกก่อนช่วงเริ่มต้นตั้งแต่ตุลาคม 2016 ไม่รวมในยอดประวัติ 10 ปี
- ไม่มี EA ไม่มีการส่งคำสั่งซื้อขาย และไม่มีการฝึกโมเดลในงานนี้

## ประวัติที่ส่งออกจริง

{coverage_table}
เวลาสิ้นสุดในตารางคือเวลาเปิดแท่ง ไม่ใช่เวลาปิด M5 และ M15 จึงลงท้ายเร็วกกว่า M1 ตามขอบเขตแท่งที่ปิดแล้ว Gap intervals รวมช่วงปิดตลาดตามปกติและยังไม่ใช่จำนวนข้อผิดพลาดของข้อมูล

## จำนวนช็อตและการแบ่งเวลา

{event_table}
{pattern_table}
ทุกช็อตได้รับการเลือกด้วยกฎราคาอัตโนมัติ ยังไม่ได้รับการรับรองความสวยจากผู้ใช้หรือการรีวิวด้วยสายตาทุกช็อต รูปแบบและเหตุผลอยู่ใน `event.csv` และกฎจริงอยู่ใน `config/selection_rules_v1.json` ไม่มีการใช้กำไร R:R หรือผลภายหลังคัดเลือก

## คุณภาพต้นทางและความสอดคล้องของ TF

{table(['Pair','TF','Native bars without observed M1','OHLC differences','Tick-volume differences'],cross)}
จำนวนนี้รวมข้อมูล warmup ด้วย ความต่างที่พบถูกเก็บใน `audit/PAIR_cross_tf_discrepancies.csv` ไม่แก้ราคาหรือปริมาณในประวัติดิบให้ดูตรงกัน หน้าต่างช็อตต้องผ่านเกณฑ์ความสอดคล้องจึงนำเข้า event dataset

ช่วงที่ไม่มีแท่งอาจเป็นวันหยุด ช่วงไม่มี quote หรือช่องว่างของ feed ไม่ควรเรียกทุกช่วงว่า missing data โดยไม่มีปฏิทิน session ยืนยัน ต่อไปนี้คือช่องว่าง M1 ที่ยาวเกิน 7 วัน โดยคงข้อมูลตามที่ MT5 ส่งกลับ:

{gap_table}
รายการเต็มอยู่ใน checkpoint รายปี และจำนวนช่องว่างรวมอยู่ใน `audit/history_coverage.json` จึงไม่ควรอ้างว่ามีข้อมูลทุกนาทีตลอด 10 ปีแม้คำขอครบช่วงแล้ว

## แผนและผลหลังเข้า

ทุกช็อตเชิงบวกมีแผนเข้าแบบ stop, SL, TP, ยกเลิกถ้ายังไม่เข้าใน 180 วินาที และ time stop 600 วินาทีหลัง fill ตามค่า research defaults ไม่มีการปรับค่าจากผลกำไรย้อนหลัง

{outcome_table}
`outcome.csv` เป็นการจำลองจาก M1 OHLC กับ spread ประจำแท่งคงที่ ไม่ใช่เทรดจริง ไม่ได้เก็บ real ticks สำหรับช็อตทั้งหมด ถ้า entry กับ exit แตะในแท่งเดียว หรือลำดับ SL/TP ไม่ชัด จะติดป้าย ambiguous ถ้าช่วงอนาคตขาดจะเป็น censored

เวลา fill/exit เป็นขอบเขตช่วงแท่งระดับนาที (`minute_bar_interval_proxy`) ไม่ใช่ timestamp tick ที่สังเกตจริง ให้อ่านคู่กับ `entry_interval_end_server` และ `exit_interval_end_server` ค่า holding_seconds จึงเป็นค่าประมาณตามขอบเขตดังกล่าว MFE/MAE เว้นว่างเพราะไม่ทราบลำดับราคาภายในแท่ง

เก็บ gross P&L และ gross R เท่าที่จำลองได้ ส่วน net P&L/net R เว้นว่างทั้งหมด เพราะไม่มี commission ย้อนหลังและ slippage จริง ห้ามนำ gross R ไปอ้างเป็นผลกำไรสุทธิที่ทำได้จริง

## การตรวจที่ผ่าน

- ยืนยันคำตอบ MT5 ต่อเดือนตรงกันสองครั้งก่อนบันทึก เพื่อลดปัญหา partial cache ระหว่าง sync
- OHLC ของประวัติดิบไม่มีแถวที่ละเมิดขอบเขตราคา ตาม checkpoint
- SHA-256 ของไฟล์ประวัติและทุกชุดช็อตตรงกับ manifest
- ตรวจไฟล์บังคับ join key จำนวนแท่ง ขอบเขตเวลา และราคาเข้า/SL/TP ของทุกช็อต
- จุด swing ใช้แท่งขวายืนยัน 2 แท่ง และไม่ย้อนเวลาการรับรู้จุด swing
- ทดสอบ prefix invariance ของ EMA/RSI/ATR และระดับโครงสร้าง เมื่อเพิ่มข้อมูลอนาคต ค่าที่มีอยู่ก่อนหน้าไม่เปลี่ยน
- แบ่ง train ก่อนปี 2024, validation ปี 2024, test ตั้งแต่ปี 2025 ใช้ embargo 7 วัน และ purge เหตุการณ์ที่ช่วง input–outcome ทับขอบเขต
- ตรวจแยกตามเวลารวมทุกคู่เงินแล้วว่าไม่มีช่วงข้อมูล train–validation หรือ validation–test ทับกัน
- ตรวจย้อนจาก M1 ไปยัง M5/M15 แล้วว่าในช่วง 10 ปีไม่มีกรอบเวลาที่มี M1 แต่ขาดไฟล์แท่ง native ของ TF ใหญ่ที่ควรมี
- ตรวจว่า stop_basis_level_id และ target_basis_level_id อ้างอิงระดับราคาที่มีอยู่จริงใน levels.csv ทุกช็อต

ผลตรวจแบบเครื่องอ่านได้: `audit/verification_report.json` การตรวจเหล่านี้ไม่ใช่หลักฐานว่ากลยุทธ์มีกำไร
'''
    (DATA/'REVIEW_REPORT.md').write_text(review,encoding='utf-8')
    readme=f'''# Price Action Dataset: EURUSD, USDJPY, GBPUSD

รวบรวมช่วง 8 พฤศจิกายน 2016 ถึงแท่งล่าสุดที่ปิดแล้ว ณ {s['cutoff_server']} เวลาเซิร์ฟเวอร์ MT5 เสร็จแล้ว: ประวัติ {total:,} แท่ง และช็อต/controls {events:,} เหตุการณ์ ข้อมูลต้นทางบางช่วงมีช่องว่าง ให้ดูรายงานก่อนใช้ฝึกโมเดล

## เริ่มอ่าน

1. [รายงานผลจริงและข้อจำกัด](REVIEW_REPORT.md)
2. [คำอธิบายคอลัมน์](DATA_DICTIONARY.md) หรือ [CSV dictionary](data_dictionary.csv)
3. [แนวทางให้ AI/นักพัฒนาทำงานต่อ](AI_HANDOFF.md)
4. [Schema ที่ส่งออกจริง](schema.json) และ [กฎคัดช็อต](config/selection_rules_v1.json)

## โครงสร้าง

```text
EURUSD/                         # USDJPY และ GBPUSD ใช้รูปแบบเดียวกัน
  history/2016-11/M1.csv         # M5.csv และ M15.csv อยู่โฟลเดอร์เดียวกัน
  warmup/2016-10/                # บริบทก่อนเริ่มช่วงหลัก แยกจากยอด 10 ปี
  events/YYYY-MM-DD/HH-mm-ss_pattern_side/
    M1.csv                      # 240 แท่งที่ปิดแล้ว
    M5.csv                      # 288 แท่งที่ปิดแล้ว
    M15.csv                     # 480 แท่งที่ปิดแล้ว
    event.csv                   # บริบท ประเภทรูปแบบ เหตุผลเลือก และ split
    levels.csv                  # ระดับราคาที่รู้แล้วพร้อมเวลายืนยัน
    plan.csv                    # แผน entry/SL/TP/expiry/time stop
    outcome.csv                 # ผลจำลองภายหลัง แยกจาก model inputs
  event_manifest.csv
  candidate_rejections.csv
history_manifest.csv
warmup_manifest.csv
manifest.csv
audit/
```

ทุก TF ในช็อตใช้เวลาตัดสินใจเดียวกัน แต่ความยาวบริบทย้อนหลังต่างกัน ใช้เฉพาะแท่งปิด เช่น 14:32 ใช้ M1 ปิดถึง 14:32 และ M5/M15 ปิดถึง 14:30

## การใช้ข้อมูล

- `history` เป็นราคาดิบ native จาก MT5 แยกคู่และเดือน เก็บค่าเดิมพร้อม checksum ไม่เติมแท่งปลอม
- `events` เป็นหน้าต่างที่คัดจาก 4 รูปแบบราคาและ no-setup controls ไม่เลือกจากผลตอบแทน
- `review_status=unreviewed` หมายถึงยังไม่มีคนตรวจความสวยของทุกช็อต ตัวคัดเป็นกฎอัตโนมัติที่ทำซ้ำได้
- ไม่มี UTC ที่เดาเอง `*_server` เป็นเวลาบนกราฟโบรกเกอร์ ช่อง UTC/offset ที่ยังไม่ยืนยันเว้นว่าง
- `tick_volume` ไม่ใช่ปริมาณรวมทั้งตลาด FX และ `real_volume` ว่างเพราะ MCP history ไม่ส่งฟิลด์นี้
- `outcome.csv` ใช้จำลอง M1 OHLC และ spread ของแท่ง ขาดข้อมูล tick/cost จริง จึงไม่ใช่ผลการซื้อขายที่ยืนยันแล้ว
- ข้อมูล `pattern_label`, `side_label`, `plan.csv` เป็นคำตอบสำหรับ supervised learning ส่วน `outcome.csv` เป็นข้อมูลอนาคต ห้ามอ่านรวมทุก CSV เป็น features โดยอัตโนมัติ
- Controls ถูกสุ่มด้วยกฎ hash ที่อัตรา 0.0005 และสัญญาณถูก deduplicate อัตราส่วนชนะ/แพ้หรือความถี่ใน event dataset จึงไม่ใช่ความถี่ของทุกนาทีในตลาด ต้องประเมินแบบ replay บน raw history ภายหลัง
- `schema_v1/` และ ZIP แบบร่างเป็นเอกสารออกแบบก่อน extraction ให้ใช้ schema/dictionary ที่ root เป็นหลัก

## สถานะงาน

เสร็จ: ดึงประวัติครบคำขอ 9 ชุด ตรวจ source gaps สร้าง features/ช็อต แผนเข้าออก ผลจำลองที่แยก provenance ตรวจไฟล์และเวลา และเขียนเอกสาร

ยังไม่ได้ทำ: รีวิวทุกช็อตด้วยสายตา, เก็บ real ticks ของทุกช็อต, ยืนยัน historical UTC/costs, ฝึกโมเดล, ทดสอบกำไรสุทธิ, สร้าง EA หรือส่งคำสั่งเทรด

## แหล่งอ้างอิง MT5

- [MqlRates](https://www.mql5.com/en/docs/constants/structures/mqlrates)
- [CopyRates และข้อจำกัดประวัติ](https://www.mql5.com/en/docs/series/copyrates)
- [Real and Generated Ticks](https://www.metatrader5.com/en/terminal/help/algotrading/tick_generation)
- [การตั้งค่า Max bars](https://www.metatrader5.com/en/terminal/help/startworking/settings)
'''
    (DATA/'README.md').write_text(readme,encoding='utf-8')
    handoff=f'''# AI / Developer Handoff

## Objective and current state

User requested a ten-year research dataset for EURUSD, USDJPY, GBPUSD, with M1/M5/M15 grouped per price-action event and explicit entry/exit plans. Collection and event export are complete. This task did not authorize creating an EA or sending trades; none were created/sent. No model was trained.

Start with `README.md`, `REVIEW_REPORT.md`, `schema.json`, `data_dictionary.csv`, then `config/selection_rules_v1.json`. Actual event count: {events:,}. Native history rows: {total:,}. Full verification results are in `audit/verification_report.json`.

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
'''
    (DATA/'AI_HANDOFF.md').write_text(handoff,encoding='utf-8')

if __name__=='__main__':main()
