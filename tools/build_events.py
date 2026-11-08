"""Offline, causal price-action event extraction. No MT5 orders and no EA.

Run with the bundled Python described in dataset/README.md.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math, time
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from collect_history import DATA, AUDIT, PAIRS, TFS, REQUESTED_START, atomic_json, csv_write, iso

VERSION='price_action_v1.0'
WINDOWS={'M1':240,'M5':288,'M15':480}
BAR_FIELDS=['event_id','timeframe','bar_index','open_time_server','close_time_server','open_time_utc','open','high','low','close','tick_volume','real_volume','bar_spread_points','source_m1_count','gap_before_seconds','gap_class','feature_valid','body_price','upper_wick_price','lower_wick_price','body_to_range','return_1bar','atr_14_price','range_atr_14','ema_9_price','ema_21_price','ema_200_price','rsi_14','tick_volume_ratio_20']
RULES={
 'version':VERSION,'requested_start_server':iso(REQUESTED_START),'window_rows':WINDOWS,
 'input_cutoff':'Completed candles only; all context and levels are as-of decision time.',
 'swing':{'left':2,'right_closed_confirmation':2,'strict_extrema':True,'reject_five_bar_window_with_gap':True},
 'warmup_bars':1000,'m1_recent_contiguous_bars':24,
 'quality_gate':'Every exported input window must have valid warmed-up features and agree with observed M1 aggregation for native higher timeframes. Maximum elapsed window spans: M1=3 calendar days, M5=5 days, M15=14 days.',
 'min_signal_body_to_range':0.55,'signal_range_atr_min':0.8,'signal_range_atr_max':2.5,
 'level_buffer_atr_m1':0.1,'minimum_buffer_points':1,
 'signal_entry':'Stop order above signal high (buy) or below signal low (sell), plus buffer. Buy level also includes the observed bar-spread proxy; validity needs actual quotes.',
 'entry_expiry_seconds':180,'max_hold_seconds':600,
 'spread_filter':'Native bar spread <= max(10 points, 0.4 * M1 ATR14 in points). This is a historical proxy, not an actual quote spread.',
 'target':'Nearest already-confirmed opposing M5/M15 swing beyond entry; range rejection uses the known range midpoint. No future extrema or RR threshold.',
 'cooldown_seconds_per_pattern_side':900,'control_sampling_probability':0.0005,
 'deduplication':'One signal per confirmed setup anchor and side, plus 15-minute cooldown. Keep rejection counts; no outcome-based filtering.',
 'control_sampling':'Deterministic SHA256 pseudo-random sampling of eligible times without any setup; independent of future outcomes.',
 'patterns':{
  'trend_pullback':'M15 and M5 each have higher last two confirmed highs and lows (or lower both for sell). Last 3 completed M5 bars contain a counter-direction candle and retrace to within [-0.1,+0.3] M5 ATR of the known M5 swing low/high. M1 closes beyond prior M1 high/low in the trend direction.',
  'breakout_retest':'M5 closed across a previously confirmed swing plus 0.1 M5 ATR, with body/range >=0.6 and range/ATR >=1. After the breakout close and within 15 minutes, M1 retests the fixed level, penetrates no more than 0.25 M1 ATR and closes back beyond prior M1 high/low.',
  'sweep_reclaim':'Previous M1 close is inside a confirmed M5 boundary. Current M1 exceeds it by 0.15 ATR, reclaims by 0.1 ATR and confirms beyond the previous M1 high/low; rejection wick >=0.25 of range. Level crossing does not prove actual liquidity or stops.',
  'range_rejection':'Latest two M15 highs differ by <=0.3 M15 ATR and latest two lows by <=0.3 ATR; width >=2 ATR. M1 touches a boundary within 0.15 M15 ATR, closes inside by 0.05 M15 ATR, and confirms beyond prior M1 high/low.',
 },
 'review':'Machine-selected candidates, not human-reviewed examples. No future profit/outcome selection.',
 'execution_model':'Conservative M1-OHLC simulation with per-bar spread held constant. No measured slippage, latency or historical commission. Unresolvable order of intrabar touches is ambiguous; incomplete horizons censored.',
 'split':{'train_end_exclusive':'2024-01-01T00:00:00','validation_end_exclusive':'2025-01-01T00:00:00','test_start':'2025-01-01T00:00:00','embargo_days':7,'purge':'Exclude any full input-to-outcome interval crossing a split boundary; group across all pairs.'},
}

def smooth(series,n,alpha):
    a=series.to_numpy(dtype=float,copy=True);valid=np.flatnonzero(np.isfinite(a))
    if len(valid)<n:return pd.Series(np.nan,index=series.index)
    first=valid[0];seed=first+n-1
    a[:seed]=np.nan;a[seed]=np.mean(a[first:seed+1])
    return pd.Series(a,index=series.index).ewm(alpha=alpha,adjust=False).mean()

def features(d,tf):
    d=d.copy();sec=TFS[tf];c=d.close;h=d.high;l=d.low;o=d.open
    d['body_price']=(c-o).abs();d['upper_wick_price']=h-pd.concat([o,c],axis=1).max(axis=1)
    d['lower_wick_price']=pd.concat([o,c],axis=1).min(axis=1)-l
    d['body_to_range']=d.body_price/(h-l).replace(0,np.nan)
    d['return_1bar']=c.pct_change(fill_method=None)
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    d['atr_14_price']=smooth(tr,14,1/14);d['range_atr_14']=(h-l)/d.atr_14_price.replace(0,np.nan)
    for n in [9,21,200]:d[f'ema_{n}_price']=smooth(c,n,2/(n+1))
    delta=c.diff();g=smooth(delta.clip(lower=0),14,1/14);loss=smooth((-delta).clip(lower=0),14,1/14)
    d['rsi_14']=100-100/(1+g/loss.replace(0,np.nan));d.loc[(loss==0)&(g>0),'rsi_14']=100;d.loc[(loss==0)&(g==0),'rsi_14']=50
    d['tick_volume_ratio_20']=d.tick_volume/d.tick_volume.shift().rolling(20).mean().replace(0,np.nan)
    d['feature_valid']=((np.arange(len(d))>=999)&d.ema_200_price.notna()&d.rsi_14.notna()).astype(int)
    continuous=(d.open_time_server.diff().dt.total_seconds().fillna(sec)==sec)
    five_contig=continuous.rolling(4).sum()==4
    for side,s,op in [('high',h,lambda x,y:x>y),('low',l,lambda x,y:x<y)]:
        pivot=op(s,s.shift(1))&op(s,s.shift(2))&op(s,s.shift(-1))&op(s,s.shift(-2))
        # Only row i+2 can observe the pivot at i. All confirmations below are shifted forward.
        confirmed=pivot.shift(2,fill_value=False)&five_contig
        vals=s.shift(2).where(confirmed)
        d['_'+side]=vals.ffill()
        # Last two *confirmed* pivots, with the previous value available only after a new confirmation.
        d['_prev_'+side]=d['_'+side].shift().where(confirmed).ffill()
        d['_'+side+'_origin']=d.open_time_server.shift(2).where(confirmed).ffill()
        d['_'+side+'_known']=d.close_time_server.where(confirmed).ffill()
        d['_prev_'+side+'_origin']=d['_'+side+'_origin'].shift().where(confirmed).ffill()
        d['_prev_'+side+'_known']=d['_'+side+'_known'].shift().where(confirmed).ffill()
    d['_up']=(d._high>d._prev_high)&(d._low>d._prev_low)
    d['_down']=(d._high<d._prev_high)&(d._low<d._prev_low)
    d['_recent_contig']=continuous.rolling(24).sum()==24
    d['_pull_low']=l.rolling(3).min();d['_pull_high']=h.rolling(3).max()
    d['_bear_recent']=(c<o).rolling(3).max().fillna(0).astype(bool)
    d['_bull_recent']=(c>o).rolling(3).max().fillna(0).astype(bool)
    old_hi=d._high.shift();old_lo=d._low.shift();buf=0.1*d.atr_14_price
    strong=(d.body_to_range>=0.6)&(d.range_atr_14>=1)
    bo_up=(c>old_hi+buf)&(c.shift()<=old_hi+buf)&strong
    bo_dn=(c<old_lo-buf)&(c.shift()>=old_lo-buf)&strong
    d['_bo_up_level']=old_hi.where(bo_up).ffill();d['_bo_dn_level']=old_lo.where(bo_dn).ffill()
    d['_bo_up_known']=d.close_time_server.where(bo_up).ffill();d['_bo_dn_known']=d.close_time_server.where(bo_dn).ffill()
    d['_bo_up_origin']=d._high_origin.shift().where(bo_up).ffill();d['_bo_dn_origin']=d._low_origin.shift().where(bo_dn).ffill()
    return d

def load_pair(pair):
    ds={}
    for tf in TFS:
        files=sorted((DATA/pair/'warmup').glob(f'*/{tf}.csv'))+sorted((DATA/pair/'history').glob(f'*/{tf}.csv'))
        if not files:raise RuntimeError('No source CSV for '+pair+' '+tf)
        d=pd.concat([pd.read_csv(f) for f in files],ignore_index=True)
        d['open_time_server']=pd.to_datetime(d.open_time_server);d['close_time_server']=pd.to_datetime(d.close_time_server)
        d=d.sort_values('open_time_server').reset_index(drop=True)
        if d.open_time_server.duplicated().any():raise RuntimeError('Duplicate source bars '+pair+' '+tf)
        d['gap_before_seconds']=(d.open_time_server.diff().dt.total_seconds()-TFS[tf]).clip(lower=0).fillna(0).astype(int)
        d['gap_class']=np.where(d.gap_before_seconds>0,'unknown','none')
        ds[tf]=features(d,tf)
    m1=ds['M1'].open_time_server.to_numpy()
    for tf,d in ds.items():
        d['source_m1_count']=np.searchsorted(m1,d.close_time_server.to_numpy())-np.searchsorted(m1,d.open_time_server.to_numpy())
    return ds

def cross_tf_audit(ds,pair,point):
    summary=[];bad_rows=[];base=ds['M1']
    base['_source_consistent']=True
    for tf in ['M5','M15']:
        d=ds[tf]
        ag=base[['open','high','low','close','tick_volume']].groupby(base.open_time_server.dt.floor('5min' if tf=='M5' else '15min'),sort=False).agg({'open':'first','high':'max','low':'min','close':'last','tick_volume':'sum'})
        aligned=ag.reindex(d.open_time_server).reset_index(drop=True)
        missing=(d.source_m1_count==0).to_numpy()
        deviation=np.abs(d[['open','high','low','close']].to_numpy()-aligned[['open','high','low','close']].to_numpy())
        price_bad=(deviation>point/2).any(axis=1)&~missing
        volume_bad=(d.tick_volume.to_numpy()!=aligned.tick_volume.to_numpy())&~missing
        consistent=~(missing|price_bad|volume_bad)
        d['_source_consistent']=consistent
        summary.append({'pair':pair,'timeframe':tf,'native_rows':len(d),'no_observed_m1_rows':int(missing.sum()),'ohlc_mismatches':int(price_bad.sum()),'tick_volume_mismatches':int(volume_bad.sum()),'consistent_rows':int(consistent.sum()),'price_tolerance':point/2})
        for i in np.flatnonzero(~consistent):
            bad_rows.append({'pair':pair,'timeframe':tf,'open_time_server':dt(d.open_time_server.iloc[i]),'observed_m1_count':int(d.source_m1_count.iloc[i]),
                'no_m1':int(missing[i]),'ohlc_mismatch':int(price_bad[i]),'tick_volume_mismatch':int(volume_bad[i])})
    for tf,d in ds.items():
        d['feature_valid']=(d.feature_valid.astype(bool)&d._source_consistent).astype(int)
        valid=d.feature_valid.rolling(WINDOWS[tf]).min()==1
        span=d.close_time_server-d.open_time_server.shift(WINDOWS[tf]-1)
        d['_valid_window']=valid&(span<=pd.Timedelta(days={'M1':3,'M5':5,'M15':14}[tf]))
    atomic_json(AUDIT/(pair+'_cross_tf.json'),summary)
    csv_write(AUDIT/(pair+'_cross_tf_discrepancies.csv'),['pair','timeframe','open_time_server','observed_m1_count','no_m1','ohlc_mismatch','tick_volume_mismatch'],bad_rows)
    return summary

def causal_check(ds):
    checks=[]
    for tf,d in ds.items():
        n=min(len(d)-3,1600)
        if n<1001:raise RuntimeError('Insufficient history for causal check '+tf)
        source=d.iloc[:n].drop(columns=[c for c in d if c.startswith('_')])
        prefix=features(source,tf)
        for col in ['atr_14_price','ema_200_price','rsi_14','_high','_low','_prev_high','_prev_low','_bo_up_level','_bo_dn_level']:
            if not np.allclose(prefix[col].to_numpy(dtype=float),d[col].iloc[:n].to_numpy(dtype=float),equal_nan=True):raise RuntimeError('Prefix/future-leak check failed '+tf+' '+col)
        checks.append({'timeframe':tf,'prefix_rows':n,'prefix_invariance':'passed'})
    return checks

def candidate_arrays(ds,point):
    a=ds['M1'];b=ds['M5'];c=ds['M15'];t=a.close_time_server.to_numpy()
    j=np.searchsorted(b.close_time_server.to_numpy(),t,side='right')-1
    k=np.searchsorted(c.close_time_server.to_numpy(),t,side='right')-1
    jb=np.maximum(j,0);kc=np.maximum(k,0)
    get5=lambda col:b[col].to_numpy()[jb]
    get15=lambda col:c[col].to_numpy()[kc]
    eligible=(np.arange(len(a))>=WINDOWS['M1']-1)&(j>=max(999,WINDOWS['M5']-1))&(k>=max(999,WINDOWS['M15']-1))&a._valid_window.to_numpy()&get5('_valid_window')&get15('_valid_window')&a._recent_contig.to_numpy()&(t>=np.datetime64(REQUESTED_START))
    bull=(a.close>a.open).to_numpy();bear=(a.close<a.open).to_numpy()
    confirm_up=(a.close>a.high.shift()).to_numpy();confirm_dn=(a.close<a.low.shift()).to_numpy()
    shape=(a.body_to_range>=0.55).to_numpy()&(a.range_atr_14>=0.8).to_numpy()&(a.range_atr_14<=2.5).to_numpy()
    atr=a.atr_14_price.to_numpy();lo=a.low.to_numpy();hi=a.high.to_numpy();cl=a.close.to_numpy()
    rec_up=(t-get5('_bo_up_known'))/np.timedelta64(1,'s');rec_dn=(t-get5('_bo_dn_known'))/np.timedelta64(1,'s')
    range_hi=np.maximum(get15('_high'),get15('_prev_high'));range_lo=np.minimum(get15('_low'),get15('_prev_low'))
    range_ok=(np.abs(get15('_high')-get15('_prev_high'))<=0.3*get15('atr_14_price'))&(np.abs(get15('_low')-get15('_prev_low'))<=0.3*get15('atr_14_price'))&((range_hi-range_lo)>=2*get15('atr_14_price'))
    masks={
      ('trend_pullback','buy'):get15('_up')&get5('_up')&get5('_bear_recent')&(get5('_pull_low')<=get5('_low')+0.3*get5('atr_14_price'))&(get5('_pull_low')>=get5('_low')-0.1*get5('atr_14_price'))&(get5('close')>get5('_low'))&bull&confirm_up,
      ('trend_pullback','sell'):get15('_down')&get5('_down')&get5('_bull_recent')&(get5('_pull_high')>=get5('_high')-0.3*get5('atr_14_price'))&(get5('_pull_high')<=get5('_high')+0.1*get5('atr_14_price'))&(get5('close')<get5('_high'))&bear&confirm_dn,
      ('breakout_retest','buy'):(rec_up>0)&(rec_up<=900)&(lo<=get5('_bo_up_level')+0.1*atr)&(lo>=get5('_bo_up_level')-0.25*atr)&(cl>get5('_bo_up_level')+0.1*atr)&bull&confirm_up,
      ('breakout_retest','sell'):(rec_dn>0)&(rec_dn<=900)&(hi>=get5('_bo_dn_level')-0.1*atr)&(hi<=get5('_bo_dn_level')+0.25*atr)&(cl<get5('_bo_dn_level')-0.1*atr)&bear&confirm_dn,
      ('sweep_reclaim','buy'):(a.close.shift().to_numpy()>=get5('_low'))&(lo<get5('_low')-0.15*atr)&(cl>get5('_low')+0.1*atr)&(a.lower_wick_price.to_numpy()>=0.25*(hi-lo))&bull&confirm_up,
      ('sweep_reclaim','sell'):(a.close.shift().to_numpy()<=get5('_high'))&(hi>get5('_high')+0.15*atr)&(cl<get5('_high')-0.1*atr)&(a.upper_wick_price.to_numpy()>=0.25*(hi-lo))&bear&confirm_dn,
      ('range_rejection','buy'):range_ok&(lo<=range_lo+0.15*get15('atr_14_price'))&(cl>range_lo+0.05*get15('atr_14_price'))&bull&confirm_up,
      ('range_rejection','sell'):range_ok&(hi>=range_hi-0.15*get15('atr_14_price'))&(cl<range_hi-0.05*get15('atr_14_price'))&bear&confirm_dn,
    }
    events=[];any_setup=np.zeros(len(a),dtype=bool);counts={};rejections=[]
    spread=a.bar_spread_points.to_numpy();buf=np.maximum(point,0.1*atr)
    entries={'buy':hi+np.maximum(buf,np.maximum(0,spread)*point+point),'sell':lo-buf}
    spread_ok=np.isfinite(spread)&(spread<=np.maximum(10,0.4*atr/point))
    for (pattern,side),mask in masks.items():
        shape_ok=eligible&shape&mask
        if pattern=='range_rejection':target_ok=((range_hi+range_lo)/2>entries[side]) if side=='buy' else ((range_hi+range_lo)/2<entries[side])
        elif side=='buy':target_ok=(get5('_high')>entries[side])|(get15('_high')>entries[side])
        else:target_ok=(get5('_low')<entries[side])|(get15('_low')<entries[side])
        ok=shape_ok&spread_ok&target_ok;any_setup|=ok
        for ri in np.flatnonzero(shape_ok&~(spread_ok&target_ok)):
            rejections.append({'decision_time_server':dt(t[ri]),'pattern':pattern,'side':side,'reason':'spread_proxy_exceeds_limit' if not spread_ok[ri] else 'no_preknown_target_beyond_entry'})
        indices=np.flatnonzero(ok);last=None;selected=[];seen=set()
        if pattern=='breakout_retest':anchor=get5('_bo_up_known' if side=='buy' else '_bo_dn_known')
        elif pattern=='range_rejection':anchor=get15('_low_known' if side=='buy' else '_high_known')
        else:anchor=get5('_low_known' if side=='buy' else '_high_known')
        for i in indices:
            key=str(anchor[i])
            if key in seen:continue
            if last is None or (t[i]-last)/np.timedelta64(1,'s')>=900:selected.append(int(i));last=t[i];seen.add(key)
        counts[pattern+'_'+side]={'shape_candidates':int(shape_ok.sum()),'complete_geometry_before_dedup':len(indices),'selected':len(selected)}
        events.extend((i,pattern,side) for i in selected)
    for i in np.flatnonzero(eligible&~any_setup):
        stamp=str(t[i]);v=int.from_bytes(hashlib.sha256(stamp.encode()).digest()[:8],'big')/2**64
        if v<0.0005:events.append((int(i),'no_setup','no_trade'))
    events.sort();return events,j,k,{'eligible_m1_times':int(eligible.sum()),'pattern_counts':counts,'controls':sum(p=='no_setup' for _,p,_ in events)},rejections

def dt(s):return pd.Timestamp(s).isoformat(timespec='seconds')
def clean(v):
    if v is None:return ''
    if isinstance(v,(pd.Timestamp,np.datetime64)):return '' if pd.isna(v) else dt(v)
    if isinstance(v,(float,np.floating)):return '' if not math.isfinite(v) else float(v)
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.bool_,bool)):return int(v)
    return v

def levels_for(eid,ds,indices,decision):
    rows=[]
    def add(level_id,tf,typ,price,origin,known,pivot=True):
        if pd.isna(price) or pd.isna(origin) or pd.isna(known):return
        if known>decision:raise RuntimeError('Future level encountered')
        r=ds[tf].iloc[indices[tf]]
        if typ=='breakout_zone':broken=r.close<price if level_id.endswith('_up') else r.close>price
        elif typ=='range_midpoint':broken=False
        else:broken=r.close>price if typ in ['swing_high','signal_high','pullback_high'] else r.close<price
        rows.append({'event_id':eid,'level_id':level_id,'timeframe':tf,'level_type':typ,'price_low':float(price),'price_high':float(price),
            'origin_time_server':dt(origin),'known_at_server':dt(known),'detection_rule_version':VERSION,
            'left_bars':2 if pivot else '', 'right_confirmation_bars':2 if pivot else '', 'touch_count_asof':'','status_asof':'broken' if broken else 'active'})
    for tf in TFS:
        r=ds[tf].iloc[indices[tf]]
        for side in ['high','low']:
            for prefix,label in [('_',''),('_prev_','previous_')]:
                add(f'{tf}_{label}swing_{side}',tf,'swing_'+side,r[prefix+side],r[prefix+side+'_origin'],r[prefix+side+'_known'])
    a=ds['M1'].iloc[indices['M1']];b=ds['M5'].iloc[indices['M5']];c=ds['M15'].iloc[indices['M15']]
    for side in ['high','low']:
        add('M1_signal_'+side,'M1','signal_'+side,a[side],a.open_time_server,decision,False)
        window=ds['M5'].iloc[max(0,indices['M5']-2):indices['M5']+1]
        ix=window[side].idxmin() if side=='low' else window[side].idxmax()
        add('M5_pullback_'+side,'M5','pullback_'+side,b['_pull_'+side],ds['M5'].loc[ix,'open_time_server'],b.close_time_server,False)
    for direction in ['up','dn']:
        add('M5_breakout_'+direction,'M5','breakout_zone',b['_bo_'+direction+'_level'],b['_bo_'+direction+'_origin'],b['_bo_'+direction+'_known'],False)
    if all(pd.notna(c[n]) for n in ['_high','_prev_high','_low','_prev_low']) and abs(c._high-c._prev_high)<=0.3*c.atr_14_price and abs(c._low-c._prev_low)<=0.3*c.atr_14_price and max(c._high,c._prev_high)-min(c._low,c._prev_low)>=2*c.atr_14_price:
        mid=(max(c._high,c._prev_high)+min(c._low,c._prev_low))/2
        add('M15_known_range_midpoint','M15','range_midpoint',mid,
            min(c[n] for n in ['_high_origin','_prev_high_origin','_low_origin','_prev_low_origin']),
            max(c[n] for n in ['_high_known','_prev_high_known','_low_known','_prev_low_known']),False)
    return rows

def make_plan(eid,side,pattern,a,b,c,levels,point):
    decision=a.close_time_server;buf=max(point,0.1*a.atr_14_price)
    plan={'event_id':eid,'plan_id':eid+'_primary','entry_rule_version':VERSION,'exit_rule_version':VERSION,'plan_status':'complete','side':side,
          'entry_order_type':'stop' if side!='no_trade' else 'none','trigger_time_server':dt(decision),'entry_reference_price':'',
          'entry_price_basis':'ask' if side=='buy' else 'bid' if side=='sell' else 'none','entry_condition':'','entry_expiry_server':dt(decision+pd.Timedelta(seconds=180)),
          'cancel_condition':'Cancel unfilled order after 180 seconds, on stop-side structural invalidation, or when quote spread exceeds the registered maximum.',
          'max_spread_points':max(10,0.4*a.atr_14_price/point),'stop_loss_price':'','stop_basis_level_id':'','stop_buffer_points':buf/point,
          'take_profit_price':'','target_basis_level_id':'','target_policy':'nearest pre-known opposing M5/M15 swing','max_hold_seconds':600,
          'early_exit_condition':'none','trailing_policy':'none','breakeven_policy':'none','estimated_commission_pips_roundtrip':'','slippage_assumption_points':'','execution_delay_ms':''}
    if side=='no_trade':plan.update(plan_status='no_trade',entry_condition='No accepted price-action setup at this control time.',target_policy='none');return plan
    spread=0 if pd.isna(a.bar_spread_points) else max(0,a.bar_spread_points*point)
    if side=='buy':entry=float(a.high+max(buf,spread+point));stop=float(min(a.low,b._pull_low) - buf)
    else:entry=float(a.low-buf);stop=float(max(a.high,b._pull_high)+spread+buf)
    targets=[r for r in levels if r['timeframe'] in ['M5','M15'] and 'previous_' not in r['level_id'] and ((r['level_type']=='swing_high' and r['price_low']>entry) if side=='buy' else (r['level_type']=='swing_low' and r['price_low']<entry))]
    target=None;target_id=''
    if pattern=='range_rejection':
        mid=(max(c._high,c._prev_high)+min(c._low,c._prev_low))/2
        if np.isfinite(mid) and (mid>entry if side=='buy' else mid<entry):target=float(mid);target_id='M15_known_range_midpoint';plan['target_policy']='midpoint of pre-known M15 range'
    elif targets:
        chosen=min(targets,key=lambda x:x['price_low']) if side=='buy' else max(targets,key=lambda x:x['price_low'])
        target=chosen['price_low'];target_id=chosen['level_id']
    plan.update(entry_reference_price=entry,stop_loss_price=stop,take_profit_price='' if target is None else target,target_basis_level_id=target_id,
                stop_basis_level_id=('M1_signal_low' if a.low<=b._pull_low else 'M5_pullback_low') if side=='buy' else ('M1_signal_high' if a.high>=b._pull_high else 'M5_pullback_high'),
                entry_condition=f'{side} stop at the registered entry_reference_price after signal close; require actual executable quotes to validate pending-order geometry. M1 simulation uses a bar-spread proxy.')
    if target is None:plan['plan_status']='incomplete'
    if pd.isna(a.bar_spread_points) or a.bar_spread_points>plan['max_spread_points']:plan['plan_status']='incomplete'
    return plan

def simulate(eid,plan,d,i,point,pip,cutoff,fields):
    out={k:'' for k in fields};out.update(event_id=eid,plan_id=plan['plan_id'],outcome_source='simulated_bars',label_status='not_evaluated',exit_reason='unknown',evaluation_rule_version=VERSION,
        execution_model='M1 OHLC; constant native bar-spread proxy; no measured latency/slippage; commission unknown',commission_status='unknown',slippage_status='not_measured',ticks_status='not_requested',execution_time_resolution='minute_bar_interval_proxy')
    decision=d.close_time_server.iloc[i];horizon=decision+pd.Timedelta(seconds=780)
    out['label_end_time_server']=dt(min(horizon,cutoff))
    if plan['side']=='no_trade':out.update(label_status='not_evaluated',outcome_source='not_evaluated',exit_reason='unknown');return out
    if plan['plan_status']!='complete':return out
    side=plan['side'];sign=1 if side=='buy' else -1;entry=plan['entry_reference_price'];sl=plan['stop_loss_price'];tp=plan['take_profit_price'];expiry=decision+pd.Timedelta(seconds=180)
    fill=None;fill_time=None;exit_price=None;exit_time=None;reason=None;mfe=0.;mae=0.;ambiguous=False;last_processed=decision
    for _,r in d.iloc[i+1:i+30].iterrows():
        if r.open_time_server>=horizon:break
        if r.open_time_server>last_processed:
            out['label_status']='censored';return out
        last_processed=r.close_time_server
        if pd.isna(r.bar_spread_points):out['label_status']='ambiguous';return out
        sp=max(0,float(r.bar_spread_points))*point
        if r.bar_spread_points>plan['max_spread_points'] and fill is None:out.update(label_status='canceled',exit_reason='canceled');return out
        if fill is None:
            if r.open_time_server>=expiry:out.update(label_status='unfilled',exit_reason='unfilled');return out
            touched=(r.high+sp>=entry) if side=='buy' else (r.low<=entry)
            invalid=(r.low<=sl) if side=='buy' else (r.high+sp>=sl)
            if touched and invalid:out.update(label_status='ambiguous',entry_exit_same_bar_ambiguous=1);return out
            if invalid:out.update(label_status='canceled',exit_reason='canceled');return out
            if not touched:continue
            fill=max(entry,r.open+sp) if side=='buy' else min(entry,r.open)
            fill_time=r.open_time_server # Interval boundary proxy, not an observed exact tick timestamp.
            out.update(entry_fill_price=fill,entry_fill_time_server=dt(fill_time),entry_interval_end_server=dt(r.close_time_server),initial_risk_pips=abs(fill-sl)/pip)
            if not ((sl<fill<tp) if side=='buy' else (tp<fill<sl)):out['label_status']='ambiguous';return out
            # Intrabar entry time/order is unknown. Any same-bar exit touch is unresolved.
            same_exit=(r.low<=sl or r.high>=tp) if side=='buy' else (r.high+sp>=sl or r.low+sp<=tp)
            if same_exit:out.update(label_status='ambiguous',entry_exit_same_bar_ambiguous=1);return out
            # The entry bar's pre-entry extremes are excluded from excursion measurements.
            continue
        fav=(r.high-fill)/pip if side=='buy' else (fill-(r.low+sp))/pip
        adv=(fill-r.low)/pip if side=='buy' else (r.high+sp-fill)/pip
        hit_sl=r.low<=sl if side=='buy' else r.high+sp>=sl
        hit_tp=r.high>=tp if side=='buy' else r.low+sp<=tp
        if hit_sl and hit_tp:out.update(label_status='ambiguous',same_bar_sl_tp=1);return out
        if hit_sl:
            exit_price=min(sl,r.open) if side=='buy' else max(sl,r.open+sp);reason='sl';exit_time=r.open_time_server;break
        if hit_tp:exit_price=tp;reason='tp';exit_time=r.open_time_server;break
        mfe=max(mfe,fav,0);mae=max(mae,adv,0)
        if r.close_time_server>=fill_time+pd.Timedelta(seconds=600):
            exit_price=r.close if side=='buy' else r.close+sp;reason='time_stop';exit_time=r.close_time_server;break
    if fill is None:out.update(label_status='unfilled' if cutoff>=expiry else 'censored',exit_reason='unfilled' if cutoff>=expiry else 'unknown');return out
    if exit_price is None:out['label_status']='censored';return out
    # Exact excursion order in the exit bar is unknowable; MFE/MAE remain blank in OHLC-only labels.
    risk=out['initial_risk_pips'];gross=sign*(exit_price-fill)/pip
    out.update(label_status='complete',exit_time_server=dt(exit_time),exit_interval_end_server=dt(exit_time+pd.Timedelta(seconds=60)) if reason in ['sl','tp'] else dt(exit_time),exit_fill_price=exit_price,exit_reason=reason,
        holding_seconds=int((exit_time-fill_time).total_seconds()),gross_pnl_pips=gross,gross_r=gross/risk if risk>0 else '',label_end_time_server=dt(exit_time),same_bar_sl_tp=0)
    return out

def split_for(start,decision,label_end):
    b1=pd.Timestamp('2024-01-01');b2=pd.Timestamp('2025-01-01');emb=pd.Timedelta(days=7)
    if decision<b1:return 'train' if label_end<b1 else 'excluded'
    if decision<b2:return 'validation' if start>=b1 and decision>=b1+emb and label_end<b2 else 'excluded'
    return 'test' if start>=b2 and decision>=b2+emb else 'excluded'

def write_event_csv(p,fields,row):return csv_write(p,fields,[{k:clean(row.get(k,'')) for k in fields}])

def prepare_window_writer(d,tf):
    """Cache immutable row bodies shared by overlapping windows; never cache event IDs/relative indexes."""
    static=BAR_FIELDS[3:]
    arrays={c:d[c].to_numpy() for c in static if c!='open_time_utc'}
    gaps=np.r_[0,np.cumsum((d.gap_class=='unknown').to_numpy(dtype=np.int64))]
    @lru_cache(maxsize=8192)
    def body(i):
        vals=[]
        for col in static:
            if col=='open_time_utc':vals.append('');continue
            v=arrays[col][i]
            if col in ['open_time_server','close_time_server']:vals.append(str(np.datetime_as_string(v,unit='s')))
            elif isinstance(v,(float,np.floating)):vals.append(format(float(v),'.12g') if np.isfinite(v) else '')
            elif isinstance(v,(int,np.integer,bool,np.bool_)):vals.append(str(int(v)))
            else:vals.append(str(v))
        return ','.join(vals)
    def write(folder,eid,end,decision):
        n=WINDOWS[tf];start=end-n+1
        if start<0 or arrays['close_time_server'][end]>np.datetime64(decision):raise RuntimeError('Invalid window cutoff '+eid)
        prefix=eid+','+tf+','
        text=','.join(BAR_FIELDS)+'\r\n'+'\r\n'.join(prefix+str(i-end)+','+body(i) for i in range(start,end+1))+'\r\n'
        p=folder/(tf+'.csv');tmp=p.with_suffix('.csv.partial');tmp.write_text(text,encoding='utf-8',newline='');tmp.replace(p)
        return pd.Timestamp(arrays['open_time_server'][start]),int(gaps[end+1]-gaps[start])
    return write

def build(pair,dry_run=False,metadata_only=False):
    begin=time.monotonic();snapshot=json.loads((AUDIT/'collection_snapshot.json').read_text());cutoff=pd.Timestamp(snapshot['cutoff_server'])
    schema=json.loads((DATA/'schema_v1/schema.json').read_text());fields=lambda n:[x['name'] for x in schema['tables']['event_template/'+n+'.csv']['fields']]
    outcome_fields=fields('outcome')+['gross_r','execution_model','commission_status','slippage_status','ticks_status','execution_time_resolution','entry_interval_end_server','exit_interval_end_server','entry_exit_same_bar_ambiguous']
    point=float(snapshot['symbol_metadata_at_export'][pair]['point']);pip=0.01 if pair=='USDJPY' else 0.0001
    ds=load_pair(pair);cross=cross_tf_audit(ds,pair,point);checks=causal_check(ds);events,j,k,pop,rejections=candidate_arrays(ds,point)
    event_fields=fields('event');plan_fields=fields('plan');level_fields=fields('levels');manifest=[];counts={};statuses={};splits={}
    print(f'{pair}: {len(events):,} selected event/control candidates; source M1 rows {len(ds["M1"]):,}',flush=True)
    if dry_run:
        print(json.dumps({'pair':pair,'events':len(events),'population':pop,'checks':checks}),flush=True)
        return
    window_writers={tf:prepare_window_writer(d,tf) for tf,d in ds.items()}
    for number,(i,pattern,side) in enumerate(events,1):
        indices={'M1':i,'M5':int(j[i]),'M15':int(k[i])};a=ds['M1'].iloc[i];b=ds['M5'].iloc[j[i]];c=ds['M15'].iloc[k[i]];decision=a.close_time_server
        eid=f'{pair}_{decision.strftime("%Y%m%dT%H%M%S")}_{pattern}_{side}'
        rel=Path(pair)/'events'/decision.strftime('%Y-%m-%d')/(decision.strftime('%H-%M-%S')+'_'+pattern+'_'+side)
        folder=DATA/rel;folder.mkdir(parents=True,exist_ok=True)
        levels=levels_for(eid,ds,indices,decision);plan=make_plan(eid,side,pattern,a,b,c,levels,point)
        outcome=simulate(eid,plan,ds['M1'],i,point,pip,cutoff,outcome_fields)
        starts=[];all_gaps=0
        for tf in ds:
            if metadata_only:
                require_file=folder/(tf+'.csv')
                if not require_file.exists():raise RuntimeError('Missing candle file in metadata-only rebuild: '+str(require_file))
                end=indices[tf];start=end-WINDOWS[tf]+1
                start_time=ds[tf].open_time_server.iloc[start];gaps=int((ds[tf].gap_class.iloc[start:end+1]=='unknown').sum())
            else:start_time,gaps=window_writers[tf](folder,eid,indices[tf],decision)
            starts.append(start_time);all_gaps+=gaps
        start=min(starts);label_end=pd.Timestamp(outcome['label_end_time_server']);split=split_for(start,decision,label_end)
        quality='valid' if plan['plan_status'] in ['complete','no_trade'] else 'needs_review'
        if quality!='valid':split='excluded'
        event={'event_id':eid,'schema_version':'1.1.0','pair':pair,'source_symbol':PAIRS[pair],'broker_server':snapshot['broker_server'],
          'decision_time_server':dt(decision),'decision_time_utc':'','server_utc_offset_minutes':'','time_mapping_source':'unverified broker chart time; no UTC conversion',
          'extracted_at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),'price_basis':'bid','digits':snapshot['symbol_metadata_at_export'][pair]['digits'],
          'point_size':point,'pip_size':pip,'real_volume_status':'not_provided','quote_bid_at_decision':'','quote_ask_at_decision':'','quote_time_server':'',
          'selection_rule_version':VERSION,'feature_rule_version':VERSION,'selection_mode':'causal_rule','selection_population_id':pair+'_eligible_M1_v1',
          'sampling_probability':0.0005 if pattern=='no_setup' else 1,'pattern_label':pattern,'side_label':side,'review_status':'unreviewed',
          'selection_reason':RULES['patterns'].get(pattern,'Eligible control timestamp without an accepted setup; deterministic independent sampling.'),
          'reviewer_id':VERSION,'overlap_group_id':pair+'_'+decision.floor('30min').strftime('%Y%m%dT%H%M'),
          'split':split,'quality_status':quality,'quality_notes':'Native spread is a bar proxy; historical UTC mapping and exact tick execution unverified.'+(' No valid pre-known target or spread constraint.' if quality!='valid' else ''),
          'news_context_status':'not_collected','minutes_to_next_scheduled_high_impact_event':''}
        write_event_csv(folder/'event.csv',event_fields,event);write_event_csv(folder/'plan.csv',plan_fields,plan)
        csv_write(folder/'levels.csv',level_fields,[{k:clean(r.get(k,'')) for k in level_fields} for r in levels])
        write_event_csv(folder/'outcome.csv',outcome_fields,outcome)
        hasher=hashlib.sha256()
        for f in sorted(folder.glob('*.csv')):hasher.update(f.name.encode()+b'\0'+f.read_bytes()+b'\0')
        manifest.append({'event_id':eid,'pair':pair,'source_symbol':PAIRS[pair],'broker_server':snapshot['broker_server'],'decision_time_server':dt(decision),
          'overlap_group_id':event['overlap_group_id'],'split':split,'quality_status':quality,'relative_event_path':str(rel),
          'input_start_server':dt(start),'input_end_server':dt(decision),'label_end_server':dt(label_end),
          'm1_rows':WINDOWS['M1'],'m5_rows':WINDOWS['M5'],'m15_rows':WINDOWS['M15'],'unexplained_gap_count':all_gaps,
          'real_ticks_status':'not_requested','event_content_sha256':hasher.hexdigest(),
          'pattern_label':pattern,'side_label':side,'plan_status':plan['plan_status'],'outcome_label_status':outcome['label_status']})
        counts[pattern]=counts.get(pattern,0)+1;statuses[outcome['label_status']]=statuses.get(outcome['label_status'],0)+1;splits[split]=splits.get(split,0)+1
        if number%250==0:print(f'{pair}: wrote {number:,}/{len(events):,} events',flush=True)
    mf=list(manifest[0]) if manifest else ['event_id','relative_event_path']
    csv_write(DATA/pair/'event_manifest.csv',mf,manifest)
    csv_write(DATA/pair/'candidate_rejections.csv',['decision_time_server','pattern','side','reason'],rejections)
    report={'pair':pair,'events':len(manifest),'patterns':counts,'outcome_statuses':statuses,'splits':splits,'population':pop,'causal_prefix_checks':checks,
      'source_rows_including_separate_warmup':{tf:len(d) for tf,d in ds.items()},'cross_tf_audit':cross,'elapsed_seconds':round(time.monotonic()-begin,2),'rule_version':VERSION,
      'human_reviewed':False,'real_tick_execution_verified':False,'commission_known':False}
    atomic_json(AUDIT/(pair+'_events_report.json'),report);print('DONE '+json.dumps(report),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--pair',choices=list(PAIRS));p.add_argument('--dry-run',action='store_true');p.add_argument('--metadata-only',action='store_true');args=p.parse_args()
    atomic_json(DATA/'config/selection_rules_v1.json',RULES)
    for pair in ([args.pair] if args.pair else list(PAIRS)):build(pair,args.dry_run,args.metadata_only)
    manifests=[pd.read_csv(DATA/p/'event_manifest.csv') for p in PAIRS if (DATA/p/'event_manifest.csv').exists()]
    if manifests:pd.concat(manifests,ignore_index=True).to_csv(DATA/'manifest.csv',index=False,lineterminator='\r\n')
