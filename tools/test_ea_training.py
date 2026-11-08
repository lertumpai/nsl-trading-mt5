"""Verify training-only reproducibility and the exported model contract."""
import json, unittest
import numpy as np
import pandas as pd
from train_ea import OUT, FEATURES, PAIRS, COST_PIPS, fit, sigmoid, metrics


class FrozenTrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = json.loads((OUT / 'training_results.json').read_text())
        cls.frame = pd.read_csv(OUT / 'event_features.csv')

    def test_only_approved_features(self):
        self.assertEqual(len(FEATURES), 19)
        self.assertFalse(set(FEATURES) & {'pattern_label', 'side_label', 'split', 'gross_r', 'entry_reference_price'})
        self.assertEqual(self.result['feature_names'], FEATURES)

    def test_reproduce_models_from_training_only(self):
        for pair in PAIRS:
            m = self.result['models'][pair]
            t = self.frame[(self.frame.pair == pair) & (self.frame.split == 'train') &
                           (self.frame.label_status == 'complete') & self.frame.model_features_valid]
            x = t[FEATURES].to_numpy(float)
            mean, scale = x.mean(axis=0), np.maximum(x.std(axis=0), 1e-8)
            np.testing.assert_allclose(mean, m['mean'], rtol=0, atol=1e-12)
            np.testing.assert_allclose(scale, m['scale'], rtol=0, atol=1e-12)
            design = np.c_[np.ones(len(x)), np.clip((x - mean) / scale, -8, 8)]
            y = (t.gross_r - COST_PIPS / t.initial_risk_pips > 0).to_numpy(float)
            beta = fit(design, y, m['penalty'])
            np.testing.assert_allclose(beta, m['coefficients'], rtol=0, atol=1e-10)
            self.assertTrue(np.isfinite(sigmoid(design @ beta)).all())

    def test_global_temporal_isolation(self):
        for a, b in [('train', 'validation'), ('validation', 'test')]:
            self.assertLess(self.frame.loc[self.frame.split == a, 'label_end_server'].max(),
                            self.frame.loc[self.frame.split == b, 'input_start_server'].min())

    def test_validation_tuning_status(self):
        for pair in PAIRS:
            m = self.result['models'][pair]
            valid = [g for g in m['validation_grid'] if g['evaluation']['assumed_cost_complete']['trades'] >= 30]
            if valid:
                selected = max(valid, key=lambda g: g['objective'])
                self.assertEqual((m['penalty'], m['threshold']), (selected['penalty'], selected['threshold']))
            else:
                self.assertIn('failed_validation_sample_minimum', m['tuning_status'])

    def test_drawdown_counts_initial_loss(self):
        self.assertAlmostEqual(metrics([-1, 0.5, -1])['max_drawdown_r'], 1.5)
        self.assertIsNone(metrics([])['mean_r'])

    def test_export_has_no_external_model(self):
        source = (OUT / 'NSL_ShotTrader.mq5').read_text()
        self.assertNotIn('@COEFFICIENTS@', source)
        self.assertNotIn('WebRequest', source)
        self.assertIn('input bool EnableOrders=false', source)
        self.assertIn('input bool AllowLiveAccount=false', source)


if __name__ == '__main__':
    unittest.main()
