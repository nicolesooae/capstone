#!/usr/bin/env python3
"""Record the HALO ESP32 firmware over USB, then analyze resting PPG variability."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo


BAUD = 460800
FS = 400
RAW_FIELDS = ['time_s', 'ir', 'red', 'sample_id', 'device_read_us', 'host_receive_elapsed_s']


class CaptureError(ValueError):
    """Acquisition is incomplete or timing cannot be trusted for PRV."""


def parse_sample(line):
    fields = line.split(',')
    if len(fields) != 5 or fields[0] != 'D':
        raise CaptureError(f'Unexpected data line: {line[:100]!r}')
    try:
        sample_id, read_us, red, ir = map(int, fields[1:])
    except ValueError as exc:
        raise CaptureError('Noninteger sample field') from exc
    if sample_id < 0 or read_us < 0 or not (0 <= red <= 262143 and 0 <= ir <= 262143):
        raise CaptureError('Sample ID, device time or ADC count outside allowed range')
    return sample_id, read_us, red, ir


def read_line(stream, log, started):
    raw = stream.readline()
    if not raw:
        return None
    log.write(f'{time.monotonic() - started:.6f}\t{raw.decode("ascii", errors="replace").rstrip()}\n')
    if not raw.endswith(b'\n') or len(raw) > 2048:
        raise CaptureError('Truncated or excessively long serial line')
    try:
        return raw.decode('ascii').strip()
    except UnicodeDecodeError as exc:
        raise CaptureError('Invalid serial bytes; check firmware and baud rate') from exc


def fail_on_device_error(line):
    if line.startswith('# ERROR '):
        raise CaptureError(line.removeprefix('# ERROR '))


def wait_ready(stream, log, started, timeout=15):
    deadline, next_hello = time.monotonic() + timeout, 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_hello:
            stream.write(b'HELLO\n')
            next_hello = now + 1
        try:
            line = read_line(stream, log, started)
        except CaptureError:
            # ESP32 boot ROM text may be emitted at a different baud rate.
            continue
        if not line:
            continue
        fail_on_device_error(line)
        if line.startswith('# READY '):
            try:
                config = json.loads(line.removeprefix('# READY '))
            except json.JSONDecodeError as exc:
                raise CaptureError('Invalid firmware configuration') from exc
            expected = dict(protocol=1, sensor='MAX30102', fs_hz=FS,
                            sample_average=1, adc_bits=18, pulse_width_us=411,
                            adc_range_na=16384)
            if any(config.get(key) != value for key, value in expected.items()):
                raise CaptureError(f'Firmware configuration does not match this recorder: {config}')
            if config.get('channels') != ['red', 'ir']:
                raise CaptureError('Expected red and IR channels, in that order')
            return config
    raise CaptureError('No HALO firmware handshake; check port, upload, cable and closed Serial Monitor')


def wait_started(stream, log, started, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = read_line(stream, log, started)
        if not line:
            continue
        fail_on_device_error(line)
        if line == '# STARTED':
            return
        if not line.startswith('# READY '):
            raise CaptureError(f'Unexpected start response: {line!r}')
    raise CaptureError('Sensor did not acknowledge START')


def capture(stream, out, duration=300, warmup=10, simulated=False, progress=print):
    """Record exactly duration*FS contiguous samples after warmup, fail closed.

    STOP is acknowledged and trailing device errors are checked before declaring
    success. Raw samples remain on disk even if acquisition fails. Host arrival
    times and FIFO read times are audit fields, not beat-timing timestamps.
    """
    started = time.monotonic()
    metadata = dict(status='capturing', simulated=simulated, sensor='MAX30102',
        started_utc=datetime.now(timezone.utc).isoformat(),
        duration_requested_s=duration, warmup_s=warmup, nominal_fs_hz=FS,
        time_basis='sample counter / nominal sensor rate; not host arrival or FIFO read time',
        analysis_permitted=False, samples_saved=0)
    meta_path = out / 'capture_metadata.json'
    meta_path.write_text(json.dumps(metadata, indent=2) + '\n')
    first_us = last_us = None
    expected_id = saved = 0
    target = (duration + warmup) * FS
    skip = warmup * FS
    try:
        with (out / 'serial_log.txt').open('w') as log, (out / 'raw_ppg.csv').open('w', newline='') as raw_file:
            writer = csv.writer(raw_file)
            writer.writerow(RAW_FIELDS)
            config = wait_ready(stream, log, started)
            metadata['firmware_config'] = config
            stream.write(b'START\n')
            wait_started(stream, log, started)
            progress(f'Sensor started: {FS} Hz, red + IR. Keep your finger still; settling for {warmup} s.')
            last_received = time.monotonic()
            stop_sent, stop_deadline = False, None
            while True:
                line = read_line(stream, log, started)
                now = time.monotonic()
                if not line:
                    if stop_sent and now >= stop_deadline:
                        raise CaptureError('STOP acknowledgement timed out; capture integrity unconfirmed')
                    if now - last_received > 3:
                        raise CaptureError('Serial stream stalled or sensor disconnected')
                    continue
                fail_on_device_error(line)
                if line == '# STOPPED':
                    if not stop_sent or saved != duration * FS:
                        raise CaptureError('Sensor stopped before the requested recording was complete')
                    break
                if line.startswith('#'):
                    raise CaptureError(f'Unexpected device reset/control message: {line[:100]}')
                sample_id, read_us, red, ir = parse_sample(line)
                if sample_id != expected_id:
                    raise CaptureError(f'Dropped/duplicated/reset sample counter: expected {expected_id}, got {sample_id}')
                if last_us is not None and (read_us <= last_us or read_us - last_us > 50000):
                    raise CaptureError('Nonmonotonic device clock or >50 ms acquisition stall')
                first_us = read_us if first_us is None else first_us
                last_us = read_us
                expected_id += 1
                last_received = now
                if not stop_sent and sample_id >= skip:
                    writer.writerow([(sample_id - skip) / FS, ir, red, sample_id,
                                     read_us, round(now - started, 6)])
                    saved += 1
                    if saved == 1 or saved % (5 * FS) == 0:
                        raw_file.flush()
                        progress(f'Recorded {saved / FS:.1f}/{duration} s — red={red}, IR={ir}')
                if not stop_sent and expected_id == target:
                    # Audit sensor cadence independently of the nominal timeline.
                    observed = (expected_id - 1) * 1e6 / (last_us - first_us)
                    metadata['observed_rate_from_device_reads_hz'] = observed
                    if abs(observed - FS) / FS > .02:
                        raise CaptureError(f'Device sample cadence {observed:.2f} Hz differs from {FS} Hz by >2%')
                    stream.write(b'STOP\n')
                    stop_sent, stop_deadline = True, now + 5
                if stop_sent and now >= stop_deadline:
                    raise CaptureError('Sensor did not stop; capture integrity unconfirmed')
            metadata.update(status='complete', analysis_permitted=True)
    except BaseException as exc:
        metadata.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                        error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        # Also request shutdown after errors/Ctrl-C, without hiding the original error.
        try:
            stream.write(b'STOP\n')
        except Exception:
            pass
        metadata.update(samples_saved=saved, samples_received_including_warmup=expected_id,
                        finished_utc=datetime.now(timezone.utc).isoformat())
        meta_path.write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


class SimulatedSensor:
    """Protocol-compatible synthetic source; never a hardware accuracy test."""
    def __init__(self, seconds):
        from halo_ppg import demo_data
        data = demo_data(FS, seconds + 1)
        self.red = data.red.round().astype(int).to_numpy()
        self.ir = data.ir.round().astype(int).to_numpy()
        self.state = 'idle'
        self.sample_id = 0

    def write(self, command):
        if command == b'HELLO\n' and self.state == 'idle':
            self.state = 'ready'
        elif command == b'START\n':
            self.state = 'starting'
            self.sample_id = 0
        elif command == b'STOP\n':
            self.state = 'stopping'

    def readline(self):
        if self.state == 'ready':
            self.state = 'idle'
            config = dict(protocol=1, firmware='SIMULATED', sensor='MAX30102', fs_hz=FS,
                sample_average=1, adc_bits=18, pulse_width_us=411, adc_range_na=16384,
                led_current_code=32, channels=['red', 'ir'])
            return ('# READY ' + json.dumps(config) + '\n').encode()
        if self.state == 'starting':
            self.state = 'streaming'
            return b'# STARTED\n'
        if self.state == 'stopping':
            self.state = 'idle'
            return b'# STOPPED\n'
        if self.state == 'streaming':
            i = self.sample_id
            self.sample_id += 1
            return f'D,{i},{(i + 1) * 2500},{self.red[i]},{self.ir[i]}\n'.encode()
        return b''

    def close(self):
        pass


def analyze(out, channel, hrv_window):
    command = [sys.executable, str(Path(__file__).with_name('halo_ppg.py')),
        '--csv', str(out / 'raw_ppg.csv'), '--fs', str(FS), '--channel', channel,
        '--signal-format', 'adc', '--window', '60', '--hrv-window', str(hrv_window),
        '--skip-hemodynamics', '--out', str(out / 'analysis')]
    subprocess.run(command, check=True)
    capture_metadata = json.loads((out / 'capture_metadata.json').read_text())
    analysis_metadata_path = out / 'analysis' / 'analysis_metadata.json'
    analysis_metadata = json.loads(analysis_metadata_path.read_text())
    analysis_metadata.update(acquisition_simulated=capture_metadata['simulated'],
                             acquisition_metadata=str((out / 'capture_metadata.json').resolve()))
    analysis_metadata_path.write_text(json.dumps(analysis_metadata, indent=2) + '\n')
    import pandas as pd
    features = pd.read_csv(out / 'analysis' / 'hrv_features.csv')
    features['simulated'] = capture_metadata['simulated']
    features.to_csv(out / 'analysis' / 'hrv_features.csv', index=False)
    print('\nResting PPG-derived PRV (preliminary quality checks):')
    print(features[['start_s', 'end_s', 'accepted', 'mean_hr_bpm',
                    'prv_rmssd_ms', 'prv_sdnn_ms', 'prv_pnn50_pct', 'reason']].to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list-ports', action='store_true', help='List USB serial devices and exit')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--port', help='ESP32 USB port, e.g. /dev/cu.usbserial-...')
    source.add_argument('--simulate', action='store_true', help='Synthetic protocol smoke test; no sensor needed')
    parser.add_argument('--duration', type=int, default=300, help='Recording seconds AFTER warmup (default 300)')
    parser.add_argument('--warmup', type=int, default=10, help='Initial settling seconds to discard (default 10)')
    parser.add_argument('--channel', choices=['red', 'ir'], default='red', help='Analysis channel; both are saved')
    parser.add_argument('--hrv-window', type=int, choices=[60, 300], default=60)
    parser.add_argument('--out', type=Path, help='New session directory; must not already exist')
    parser.add_argument('--no-analyze', action='store_true', help='Capture only')
    args = parser.parse_args()
    if args.list_ports:
        try:
            from serial.tools import list_ports
        except ImportError:
            parser.error('Install requirements-live.txt in this Python environment first')
        ports = list(list_ports.comports())
        for port in ports:
            print(f'{port.device}\t{port.description}')
        if not ports:
            print('No serial ports found. Connect the ESP32 using a USB data cable.')
        return
    if not args.port and not args.simulate:
        parser.error('Choose --port or --simulate (or use --list-ports)')
    if args.duration < args.hrv_window or args.warmup < 0:
        parser.error('Require duration >= hrv-window and warmup >= 0')
    out = args.out or Path(__file__).resolve().parent / 'recordings' / datetime.now(
        ZoneInfo('America/Toronto')).strftime('%Y%m%d_%H%M%S')
    if out.exists():
        parser.error(f'Session directory already exists; choose a new --out: {out}')
    if args.simulate:
        print('SIMULATION: synthetic data only; this does not test the ESP32 or sensor.')
        stream = SimulatedSensor(args.duration + args.warmup)
    else:
        try:
            import serial
        except ImportError:
            parser.error('Install requirements-live.txt in this Python environment first')
        try:
            stream = serial.Serial(args.port, BAUD, timeout=1, write_timeout=2, exclusive=True)
        except (serial.SerialException, OSError) as exc:
            parser.error(f'Cannot open {args.port}: {exc}. Close Serial Monitor and check the USB cable.')
    out.mkdir(parents=True, exist_ok=False)
    try:
        metadata = capture(stream, out, args.duration, args.warmup, args.simulate)
    except (CaptureError, OSError, KeyboardInterrupt) as exc:
        print(f'\nRecording incomplete: {exc or "interrupted"}. Raw data kept in {out.resolve()}.', file=sys.stderr)
        print('Automatic PRV analysis skipped. Fix the acquisition issue and start a new session.', file=sys.stderr)
        sys.exit(1)
    finally:
        stream.close()
        metadata_path = out / 'capture_metadata.json'
        if metadata_path.exists():
            audit = json.loads(metadata_path.read_text())
            audit.update(serial_port=args.port, baud=BAUD, analysis_channel=args.channel,
                         variability_window_s=args.hrv_window)
            metadata_path.write_text(json.dumps(audit, indent=2) + '\n')
    print(f'\nCapture complete: {metadata["samples_saved"]} samples. Files: {out.resolve()}')
    if not args.no_analyze:
        try:
            sys.stdout.flush()
            analyze(out, args.channel, args.hrv_window)
        except (subprocess.CalledProcessError, ValueError) as exc:
            print(f'PRV analysis failed: {exc}. Raw recording preserved for review.', file=sys.stderr)
            sys.exit(1)
    if args.simulate:
        print('SIMULATION ONLY: all results above came from an artificial waveform.')


if __name__ == '__main__':
    main()
