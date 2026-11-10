"""SEN0518 processed-output protocol tests; never derive PRV from BPM polls."""
import json
from pathlib import Path
import tempfile
import unittest

from record_gravity import CaptureError, SimulatedGravity, parse_reading, record


class BadGravity(SimulatedGravity):
    def __init__(self, fault):
        super().__init__()
        self.fault = fault

    def readline(self):
        if self.state == 'stopping' and self.fault == 'stop_error':
            return b'# ERROR I2C_STOP_FAILED\n'
        line = super().readline()
        if line.startswith(b'# READY') and self.fault == 'wrong_firmware':
            return line.replace(b'"prv_supported":false', b'"prv_supported":true')
        if line.startswith(b'R,1,'):
            if self.fault == 'gap':
                return line.replace(b'R,1,', b'R,3,')
            if self.fault == 'clock':
                return line.replace(b',8000,', b',1000,')
            if self.fault == 'device_error':
                return b'# ERROR I2C_MEASUREMENT_READ_FAILED\n'
            if self.fault == 'reset':
                return b'# READY {}\n'
        return line


class GravityTests(unittest.TestCase):
    def test_capture_keeps_missing_values_and_never_invents_prv(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            summary = record(SimulatedGravity(), out, duration=60, simulated=True,
                             progress=lambda text: None)
            data = pd.read_csv(out / 'heart_rate_readings.csv')
            self.assertEqual(len(data), 15)
            self.assertTrue(data.heart_rate_bpm.iloc[:2].isna().all())
            self.assertEqual(summary['hr_readings_present'], 13)
            self.assertEqual(summary['capture_status'], 'complete')
            self.assertTrue(summary['simulated'])
            for key in ('prv_rmssd_ms', 'prv_sdnn_ms', 'prv_pnn50_pct'):
                self.assertIsNone(summary[key])
            self.assertIn('unavailable', summary['prv_status'])
            meta = json.loads((out / 'capture_metadata.json').read_text())
            self.assertFalse(meta['individual_pulse_intervals_available'])

    def test_connection_and_protocol_faults_are_saved_as_failed(self):
        for fault in ('wrong_firmware', 'gap', 'clock', 'device_error', 'reset', 'stop_error'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                with self.assertRaises(CaptureError):
                    record(BadGravity(fault), out, duration=12, simulated=True,
                           progress=lambda text: None)
                summary = json.loads((out / 'summary.json').read_text())
                self.assertEqual(summary['capture_status'], 'failed')
                self.assertIsNone(summary['prv_rmssd_ms'])
                self.assertTrue((out / 'heart_rate_readings.csv').exists())

    def test_invalid_values_and_flags_are_rejected(self):
        for line in ('R,0,4000,72,98,2,1', 'R,0,4000,0,98,1,1',
                     'R,0,4000,72,101,1,1', 'R,0,4000,72,98,0,1',
                     'R,-1,4000,72,98,1,1', 'R,0,4000,nan,98,1,1'):
            with self.subTest(line=line), self.assertRaises(CaptureError):
                parse_reading(line)


if __name__ == '__main__':
    unittest.main()
