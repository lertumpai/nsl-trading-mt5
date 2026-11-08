"""Small synthetic logic tests; fixture prices are never exported to dataset/."""
import unittest
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from build_events import features, simulate, split_for, prepare_window_writer

class DatasetLogicTests(unittest.TestCase):
    def test_features_do_not_change_when_future_is_added(self):
        rng=np.random.default_rng(183);n=1800
        close=100+np.cumsum(rng.normal(0,0.05,n));op=np.r_[close[0],close[:-1]]
        t=pd.date_range('2020-01-01',periods=n,freq='min')
        d=pd.DataFrame({'open_time_server':t,'close_time_server':t+pd.Timedelta(minutes=1),'open':op,'high':np.maximum(op,close)+.03,'low':np.minimum(op,close)-.03,'close':close,'tick_volume':20})
        full=features(d,'M1');prefix=features(d.iloc[:1400],'M1')
        for c in ['ema_200_price','rsi_14','atr_14_price','_high','_low','_prev_high','_bo_up_level']:
            np.testing.assert_allclose(prefix[c],full[c].iloc[:1400],equal_nan=True)

    def make_source(self,rows,gap=False):
        start=pd.Timestamp('2020-01-01T12:00:00')
        t=[start+pd.Timedelta(minutes=i) for i in range(len(rows))]
        if gap:t[1]+=pd.Timedelta(minutes=2)
        return pd.DataFrame([dict(zip(['open','high','low','close'],r),open_time_server=t[i],close_time_server=t[i]+pd.Timedelta(minutes=1),bar_spread_points=0) for i,r in enumerate(rows)])

    def plan(self):
        return {'plan_id':'test_plan','side':'buy','plan_status':'complete','entry_reference_price':102.,'stop_loss_price':99.,'take_profit_price':105.,'max_spread_points':100}

    def test_intrabar_entry_and_exit_is_ambiguous(self):
        d=self.make_source([(100,101,99.5,100),(101,106,100,105)])
        result=simulate('test',self.plan(),d,0,1.,1.,pd.Timestamp('2020-01-01T13:00:00'),[])
        self.assertEqual(result['label_status'],'ambiguous')
        self.assertEqual(result['entry_exit_same_bar_ambiguous'],1)

    def test_separate_bar_take_profit_does_not_invent_net_profit(self):
        d=self.make_source([(100,101,99.5,100),(101,103,100,102),(103,106,102,105)])
        result=simulate('test',self.plan(),d,0,1.,1.,pd.Timestamp('2020-01-01T13:00:00'),['net_r'])
        self.assertEqual(result['label_status'],'complete');self.assertEqual(result['exit_reason'],'tp')
        self.assertEqual(result['gross_pnl_pips'],3.);self.assertEqual(result['net_r'],'')

    def test_missing_future_interval_is_censored(self):
        d=self.make_source([(100,101,99.5,100),(101,103,100,102)],gap=True)
        result=simulate('test',self.plan(),d,0,1.,1.,pd.Timestamp('2020-01-01T13:00:00'),[])
        self.assertEqual(result['label_status'],'censored')

    def test_full_window_and_label_purge(self):
        self.assertEqual(split_for(pd.Timestamp('2023-12-28'),pd.Timestamp('2024-01-09'),pd.Timestamp('2024-01-09T00:10')),'excluded')
        self.assertEqual(split_for(pd.Timestamp('2024-01-02'),pd.Timestamp('2024-01-09'),pd.Timestamp('2024-01-09T00:10')),'validation')
        self.assertEqual(split_for(pd.Timestamp('2023-12-25'),pd.Timestamp('2023-12-31T23:59'),pd.Timestamp('2024-01-01T00:05')),'excluded')

    def test_fast_csv_writer_preserves_prices_and_cutoff(self):
        n=300;t=pd.date_range('2020-01-01',periods=n,freq='min');c=1.1+np.sin(np.arange(n)/10)*0.001
        d=pd.DataFrame({'open_time_server':t,'close_time_server':t+pd.Timedelta(minutes=1),'open':c,'high':c+0.0002,'low':c-0.0001,'close':c+0.00005,
            'tick_volume':10,'real_volume':np.nan,'bar_spread_points':8,'source_m1_count':1,'gap_before_seconds':0,'gap_class':'none'})
        d=features(d,'M1');writer=prepare_window_writer(d,'M1')
        with tempfile.TemporaryDirectory() as td:
            writer(Path(td),'test_event',299,d.close_time_server.iloc[299])
            actual=pd.read_csv(Path(td)/'M1.csv')
            self.assertEqual(len(actual),240);self.assertEqual(actual.bar_index.iloc[0],-239);self.assertEqual(actual.bar_index.iloc[-1],0)
            for col in ['open','high','low','close','ema_200_price','atr_14_price','rsi_14']:
                np.testing.assert_allclose(actual[col],d[col].iloc[-240:],rtol=1e-10,equal_nan=True)
            self.assertEqual(actual.close_time_server.iloc[-1],d.close_time_server.iloc[-1].isoformat(timespec='seconds'))

if __name__=='__main__':unittest.main()
