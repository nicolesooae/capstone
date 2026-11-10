"""Deterministic numerical and quality-rejection regression tests."""
import unittest
from types import SimpleNamespace

import numpy as np

from halo_metrics import interval_variability, calculate_svri, hemodynamics_from_args
from halo_ppg import variability_report


class VariabilityTests(unittest.TestCase):
    def test_known_intervals(self):
        result = interval_variability([800, 820, 880, 800])
        self.assertAlmostEqual(result['prv_rmssd_ms'], np.sqrt(10400 / 3))
        self.assertAlmostEqual(result['prv_sdnn_ms'], np.sqrt(4300 / 3))
        self.assertAlmostEqual(result['prv_sdsd_ms'], np.std([20, 60, -80], ddof=1))
        self.assertAlmostEqual(result['prv_pnn20_pct'], 200 / 3)
        self.assertAlmostEqual(result['prv_pnn50_pct'], 200 / 3)
        self.assertAlmostEqual(result['prv_cvnn'], result['prv_sdnn_ms'] / 825)
        self.assertAlmostEqual(result['prv_cvsd'], result['prv_rmssd_ms'] / 825)
        self.assertAlmostEqual(result['prv_lnrmssd'], np.log(result['prv_rmssd_ms']))

    def test_constant_intervals_and_strict_thresholds(self):
        result = interval_variability([800] * 5)
        self.assertEqual(result['prv_rmssd_ms'], 0)
        self.assertEqual(result['prv_sdnn_ms'], 0)
        self.assertTrue(np.isnan(result['prv_lnrmssd']))
        self.assertEqual(interval_variability([800, 850, 800])['prv_pnn50_pct'], 0)
        self.assertEqual(interval_variability([800, 850 + 1e-12, 800])['prv_pnn50_pct'], 0)
        self.assertEqual(interval_variability([800, 850.001, 800])['prv_pnn50_pct'], 100)

    def test_invalid_intervals(self):
        for rr in ([], [800, 800], [800, 0, 800], [800, np.nan, 800], [[800] * 3]):
            with self.subTest(rr=rr), self.assertRaises(ValueError):
                interval_variability(rr)

    def inputs(self):
        fs = 100
        t = np.arange(6000) / fs
        raw = 100000 + 1000 * np.sin(2 * np.pi * t)
        return dict(fs=fs, peaks=np.arange(50, 6000, 100), quality=np.ones(6000),
                    raw=raw, cleaned=raw - 100000, min_quality=.8, signal_format='adc')

    def test_regular_window(self):
        report = variability_report(window_s=60, **self.inputs())
        self.assertTrue(report.iloc[0]['accepted'])
        self.assertEqual(report.iloc[0]['interval_count'], 59)
        self.assertEqual(report.iloc[0]['prv_rmssd_ms'], 0)

    def test_rejected_windows_never_produce_variability(self):
        for failure in ('low_quality', 'missed_beat', 'edge_gap', 'short', 'local_quality', 'clipped', 'flat'):
            inputs = self.inputs()
            if failure == 'low_quality':
                inputs['quality'][:] = .2
            elif failure == 'missed_beat':
                inputs['peaks'] = np.delete(inputs['peaks'], 10)
            elif failure == 'edge_gap':
                inputs['peaks'] = inputs['peaks'][3:]
            elif failure == 'short':
                inputs['raw'] = inputs['raw'][:3000]
                inputs['cleaned'] = inputs['cleaned'][:3000]
                inputs['quality'] = inputs['quality'][:3000]
            elif failure == 'local_quality':
                inputs['quality'][2000:2400] = .1
            elif failure == 'clipped':
                inputs['raw'][:100] = 262143
            elif failure == 'flat':
                inputs['raw'][:] = 100000
            with self.subTest(failure=failure):
                row = variability_report(window_s=60, **inputs).iloc[0]
                self.assertFalse(row['accepted'])
                self.assertTrue(np.isnan(row['prv_rmssd_ms']))
                self.assertTrue(np.isnan(row['prv_pnn50_pct']))
                self.assertTrue(row['reason'])

    def test_windows_do_not_bridge_rejected_section(self):
        inputs = self.inputs()
        inputs['raw'] = np.tile(inputs['raw'], 3)
        inputs['cleaned'] = np.tile(inputs['cleaned'], 3)
        inputs['quality'] = np.ones(18000)
        inputs['quality'][6000:12000] = .1
        inputs['peaks'] = np.arange(50, 18000, 100)
        report = variability_report(window_s=60, **inputs)
        self.assertEqual(report['accepted'].tolist(), [True, False, True])
        self.assertTrue(np.isnan(report.iloc[1]['prv_sdnn_ms']))


class HemodynamicsTests(unittest.TestCase):
    def test_known_svri_and_co_conversion(self):
        self.assertEqual(calculate_svri(map_mmhg=90, cvp_mmhg=5,
                                       cardiac_index_l_min_m2=2.5), 2720)
        self.assertEqual(calculate_svri(map_mmhg=90, cvp_mmhg=5,
                                       cardiac_output_l_min=5, bsa_m2=2), 2720)

    def test_missing_measurements_are_explicitly_unavailable(self):
        args = SimpleNamespace(map_mmhg=None, cvp_mmhg=None, cardiac_index=None,
                               cardiac_output=None, bsa=None)
        result = hemodynamics_from_args(args)
        self.assertTrue(np.isnan(result['svri_dyn_s_cm5_m2']))
        self.assertIn('unavailable', result['svri_status'])
        args.map_mmhg = 90
        with self.assertRaises(ValueError):
            hemodynamics_from_args(args)

    def test_invalid_or_conflicting_measurements(self):
        base = dict(map_mmhg=90, cvp_mmhg=5, cardiac_index_l_min_m2=2.5)
        for change in ({'map_mmhg': np.nan}, {'map_mmhg': -1},
                       {'cvp_mmhg': np.inf}, {'cvp_mmhg': 90},
                       {'cardiac_index_l_min_m2': 0}, {'cardiac_index_l_min_m2': np.inf},
                       {'cardiac_output_l_min': 5}, {'bsa_m2': 2},
                       {'cardiac_index_l_min_m2': None},
                       {'cardiac_index_l_min_m2': None, 'cardiac_output_l_min': 5},
                       {'cardiac_index_l_min_m2': None, 'cardiac_output_l_min': 5, 'bsa_m2': 0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                calculate_svri(**(base | change))

    def test_cli_inputs_keep_units_and_derived_index(self):
        args = SimpleNamespace(map_mmhg=90, cvp_mmhg=5, cardiac_index=None,
                               cardiac_output=5, bsa=2)
        result = hemodynamics_from_args(args)
        self.assertEqual(result['cardiac_index_l_min_m2'], 2.5)
        self.assertEqual(result['svri_dyn_s_cm5_m2'], 2720)
        self.assertEqual(result['svri_units'], 'dyn*s*cm^-5*m^2')

    def test_negative_measured_cvp_is_supported(self):
        self.assertEqual(calculate_svri(map_mmhg=90, cvp_mmhg=-2,
                                       cardiac_index_l_min_m2=2), 3680)


if __name__ == '__main__':
    unittest.main()
