"""Numerical aggregation and missing-data safeguards for batch validation."""
import unittest
import numpy as np
import pandas as pd

from validate_all_physionet import agreement_summary, process_finite_segments
from validate_physionet import COMPARISON_METRICS
from halo_ppg import demo_data


class BatchValidationTests(unittest.TestCase):
    def test_error_sign_weighting_and_quality_subset(self):
        rows = []
        for record, difference, accepted, comparable in (
            ('s1_sit', 1, True, True), ('s1_sit', 3, False, True),
            ('s2_sit', -6, True, True), ('s2_sit', 1000, False, False)):
            row = dict(record=record, subject=record.split('_')[0], activity='sit',
                       accepted=accepted, comparable=comparable)
            for metric in COMPARISON_METRICS:
                row['difference_' + metric] = difference
                row['abs_error_' + metric] = abs(difference)
            rows.append(row)
        result = agreement_summary(pd.DataFrame(rows))
        full = result[(result.activity == 'sit') & (result.subset == 'unfiltered') & (result.metric == 'rmssd_ms')].iloc[0]
        gated = result[(result.activity == 'sit') & (result.subset == 'accepted') & (result.metric == 'rmssd_ms')].iloc[0]
        self.assertEqual(full.windows, 3)
        self.assertAlmostEqual(full.mae, 10 / 3)
        self.assertAlmostEqual(full.bias, -2 / 3)
        self.assertEqual(full.mean_record_mae, 4)
        self.assertEqual(gated.windows, 2)
        self.assertEqual(gated.mae, 3.5)
        self.assertEqual(gated.bias, -2.5)
        self.assertTrue(result[(result.activity == 'run')].mae.isna().all())

    def test_missing_data_is_not_interpolated_or_bridged(self):
        fs = 100
        raw = demo_data(fs, 35).ir.to_numpy()
        raw[1600:1800] = np.nan
        peaks, clean, quality, ok, messages = process_finite_segments(raw, fs)
        self.assertFalse(ok[1600:1800].any())
        self.assertTrue(np.isnan(clean[1600:1800]).all())
        self.assertTrue(np.isnan(quality[1600:1800]).all())
        self.assertFalse(((peaks >= 1600) & (peaks < 1800)).any())
        self.assertTrue(ok[:1600].all())
        self.assertTrue(ok[1800:].all())


if __name__ == '__main__':
    unittest.main()
