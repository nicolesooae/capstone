"""Acquisition integrity checks; does not require a USB device."""
import json
from pathlib import Path
import tempfile
import unittest

from record_max30102 import CaptureError, FS, SimulatedSensor, capture, parse_sample


class FaultSensor(SimulatedSensor):
    def __init__(self, fault):
        super().__init__(3)
        self.fault = fault

    def readline(self):
        if self.state == 'stopping' and self.fault == 'trailing_error':
            return b'# ERROR FIFO_BACKLOG_OR_OVERFLOW\n'
        line = super().readline()
        if line.startswith(b'# READY ') and self.fault == 'wrong_rate':
            return line.replace(b'"fs_hz": 400', b'"fs_hz": 100')
        if not line.startswith(b'D,'):
            return line
        fields = line.decode().strip().split(',')
        index = int(fields[1])
        if self.fault == 'cadence':
            fields[2] = str(int(fields[2]) * 2)
        if index == 200:
            if self.fault == 'dropped':
                fields[1] = str(index + 1)
            elif self.fault == 'duplicate':
                fields[1] = str(index - 1)
            elif self.fault == 'clock':
                fields[2] = '0'
            elif self.fault == 'truncated':
                return b'D,200,123'
            elif self.fault == 'overflow':
                return b'# ERROR FIFO_BACKLOG_OR_OVERFLOW\n'
            elif self.fault == 'reset':
                return b'# READY {"protocol":1}\n'
            elif self.fault == 'interrupt':
                raise KeyboardInterrupt()
        return (','.join(fields) + '\n').encode()


class RecorderTests(unittest.TestCase):
    def test_valid_capture_discards_warmup_and_keeps_both_channels(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            metadata = capture(SimulatedSensor(3), out, duration=1, warmup=1,
                               simulated=True, progress=lambda text: None)
            raw = pd.read_csv(out / 'raw_ppg.csv')
            self.assertEqual(len(raw), FS)
            self.assertEqual(raw.iloc[0].sample_id, FS)
            self.assertEqual(raw.iloc[0].time_s, 0)
            self.assertAlmostEqual(raw.iloc[-1].time_s, (FS - 1) / FS)
            self.assertTrue(raw.ir.notna().all() and raw.red.notna().all())
            self.assertEqual(metadata['observed_rate_from_device_reads_hz'], FS)
            self.assertEqual(metadata['status'], 'complete')
            self.assertTrue(metadata['analysis_permitted'])
            self.assertTrue(metadata['simulated'])

    def test_acquisition_faults_preserve_data_but_forbid_analysis(self):
        for fault in ('wrong_rate', 'dropped', 'duplicate', 'clock', 'truncated',
                      'overflow', 'reset', 'cadence', 'trailing_error', 'interrupt'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                expected_exception = KeyboardInterrupt if fault == 'interrupt' else CaptureError
                with self.assertRaises(expected_exception):
                    capture(FaultSensor(fault), out, duration=1, warmup=0,
                            simulated=True, progress=lambda text: None)
                metadata = json.loads((out / 'capture_metadata.json').read_text())
                self.assertFalse(metadata['analysis_permitted'])
                self.assertIn(metadata['status'], ['failed', 'interrupted'])
                self.assertTrue((out / 'raw_ppg.csv').exists())
                self.assertTrue((out / 'serial_log.txt').exists())
                self.assertFalse((out / 'analysis').exists())

    def test_parser_rejects_bad_adc_and_sample_fields(self):
        for line in ('D,0,2500,262144,10', 'D,-1,2500,10,10', 'D,0,-1,10,10',
                     'D,0,2500,10,nan', '0,2500,10,10', 'D,0,2500,10,10,99'):
            with self.subTest(line=line), self.assertRaises(CaptureError):
                parse_sample(line)


if __name__ == '__main__':
    unittest.main()
