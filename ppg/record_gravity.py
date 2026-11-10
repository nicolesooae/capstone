#!/usr/bin/env python3
"""Live heart-rate recorder for Nano ESP32 + Gravity SEN0518. PRV is unavailable."""
import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
import time
from zoneinfo import ZoneInfo

from record_max30102 import CaptureError, fail_on_device_error, read_line, wait_started

UNAVAILABLE = 'unavailable: SEN0518 provides processed HR, not raw PPG or individual pulse intervals'
FIELDS = ['time_s', 'heart_rate_bpm', 'spo2_percent', 'hr_value_present',
          'spo2_value_present', 'sequence', 'host_elapsed_s', 'simulated']


def parse_reading(line):
    fields = line.split(',')
    if len(fields) != 7 or fields[0] != 'R':
        raise CaptureError(f'Unexpected measurement line: {line[:100]!r}')
    try:
        sequence, ms, hr, spo2, has_hr, has_spo2 = map(int, fields[1:])
    except ValueError as exc:
        raise CaptureError('Measurement fields must be integers') from exc
    if sequence < 0 or ms < 0 or has_hr not in (0, 1) or has_spo2 not in (0, 1):
        raise CaptureError('Invalid counter, clock or presence flags')
    if (has_hr and not 1 <= hr <= 300) or (not has_hr and hr != -1):
        raise CaptureError('Heart-rate value conflicts with its presence flag')
    if (has_spo2 and not 1 <= spo2 <= 100) or (not has_spo2 and spo2 != -1):
        raise CaptureError('SpO2 value conflicts with its presence flag')
    return sequence, ms, hr if has_hr else None, spo2 if has_spo2 else None


def handshake(stream, log, started, timeout=15):
    deadline, next_hello = time.monotonic() + timeout, 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_hello:
            stream.write(b'HELLO\n')
            next_hello = now + 1
        try:
            line = read_line(stream, log, started)
        except CaptureError:
            continue  # Boot messages may use another encoding/baud.
        if not line:
            continue
        fail_on_device_error(line)
        if line.startswith('# READY '):
            try:
                config = json.loads(line.removeprefix('# READY '))
            except json.JSONDecodeError as exc:
                raise CaptureError('Invalid firmware handshake JSON') from exc
            expected = dict(protocol=1, device='Gravity SEN0518', data_type='processed_hr_spo2',
                            poll_interval_ms=4000, raw_ppg=False, prv_supported=False)
            if any(config.get(key) != value for key, value in expected.items()):
                raise CaptureError('Wrong firmware: upload nano_gravity_sen0518.ino for this recorder')
            return config
    raise CaptureError('No Gravity firmware handshake; check port, upload and closed Serial Monitor')


def record(stream, out, duration=300, simulated=False, progress=print):
    started = time.monotonic()
    readings = []
    metadata = dict(status='recording', device='DFRobot Gravity SEN0518',
        board='Arduino Nano ESP32', simulated=simulated,
        started_utc=datetime.now(timezone.utc).isoformat(), requested_duration_s=duration,
        poll_interval_s=4, data_type='processed HR and SpO2; not raw PPG',
        individual_pulse_intervals_available=False, prv_status=UNAVAILABLE,
        prv_rmssd_ms=None, prv_sdnn_ms=None, prv_pnn50_pct=None,
        freshness='No per-measurement freshness indicator; consecutive identical values are retained',
        validity='Presence/range checks only; not physiological accuracy validation')
    (out / 'capture_metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    last_ms = None
    expected = 0
    try:
        with (out / 'serial_log.txt').open('w') as log, (out / 'heart_rate_readings.csv').open('w', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            writer.writeheader()
            metadata['firmware_config'] = handshake(stream, log, started)
            stream.write(b'START\n')
            wait_started(stream, log, started)
            progress('Connected. Keep your finger still; first readings may be unavailable while the sensor acquires.')
            progress('HRV/PRV: unavailable — this module does not provide individual pulse intervals.')
            last_received = time.monotonic()
            stop_sent, stop_deadline = False, None
            while True:
                line = read_line(stream, log, started)
                now = time.monotonic()
                if not line:
                    if stop_sent and now >= stop_deadline:
                        raise CaptureError('STOP acknowledgement timed out')
                    if now - last_received > 10:
                        raise CaptureError('No measurements for 10 seconds; check USB and sensor')
                    continue
                fail_on_device_error(line)
                if line == '# STOPPED':
                    if not stop_sent:
                        raise CaptureError('Sensor stopped unexpectedly')
                    break
                if line.startswith('#'):
                    raise CaptureError(f'Unexpected device reset/control message: {line[:100]}')
                sequence, ms, hr, spo2 = parse_reading(line)
                if sequence != expected:
                    raise CaptureError(f'Dropped/reset measurement counter: expected {expected}, got {sequence}')
                if last_ms is not None and not 3500 <= ms - last_ms <= 6000:
                    raise CaptureError('Device clock reset or unexpected polling cadence')
                if last_ms is None and not 3500 <= ms <= 6000:
                    raise CaptureError('Unexpected first-measurement time')
                expected += 1
                last_ms, last_received = ms, now
                if not stop_sent:
                    row = dict(time_s=ms / 1000, heart_rate_bpm=hr, spo2_percent=spo2,
                        hr_value_present=hr is not None, spo2_value_present=spo2 is not None,
                        sequence=sequence, host_elapsed_s=round(now - started, 6), simulated=simulated)
                    readings.append(row)
                    writer.writerow(row)
                    file.flush()
                    progress(f'{ms / 1000:6.1f} s | HR: {str(hr) + " bpm" if hr is not None else "not available"}'
                             f' | SpO2: {str(spo2) + "%" if spo2 is not None else "not available"}')
                if not stop_sent and ms >= duration * 1000:
                    stream.write(b'STOP\n')
                    stop_sent, stop_deadline = True, now + 5
                if stop_sent and now >= stop_deadline:
                    raise CaptureError('Sensor did not acknowledge STOP')
            metadata['status'] = 'complete'
    except BaseException as exc:
        metadata.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                        error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        try:
            stream.write(b'STOP\n')
        except Exception:
            pass
        valid_hr = [row['heart_rate_bpm'] for row in readings if row['hr_value_present']]
        metadata.update(finished_utc=datetime.now(timezone.utc).isoformat(), readings_saved=len(readings),
                        hr_readings_present=len(valid_hr))
        summary = dict(capture_status=metadata['status'], simulated=simulated,
            readings_saved=len(readings), hr_readings_present=len(valid_hr),
            mean_reported_hr_bpm=statistics.mean(valid_hr) if valid_hr else None,
            min_reported_hr_bpm=min(valid_hr) if valid_hr else None,
            max_reported_hr_bpm=max(valid_hr) if valid_hr else None,
            prv_status=UNAVAILABLE, prv_rmssd_ms=None, prv_sdnn_ms=None, prv_pnn50_pct=None)
        (out / 'capture_metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


class SimulatedGravity:
    """Synthetic processed values for testing software; no physiological evidence."""
    def __init__(self):
        self.state = 'idle'
        self.sequence = 0

    def write(self, command):
        if command == b'HELLO\n' and self.state == 'idle':
            self.state = 'ready'
        elif command == b'START\n':
            self.state, self.sequence = 'starting', 0
        elif command == b'STOP\n':
            self.state = 'stopping'

    def readline(self):
        if self.state == 'ready':
            self.state = 'idle'
            return b'# READY {"protocol":1,"device":"Gravity SEN0518","data_type":"processed_hr_spo2","poll_interval_ms":4000,"raw_ppg":false,"prv_supported":false}\n'
        if self.state == 'starting':
            self.state = 'streaming'
            return b'# STARTED\n'
        if self.state == 'stopping':
            self.state = 'idle'
            return b'# STOPPED\n'
        if self.state == 'streaming':
            i = self.sequence
            self.sequence += 1
            hr = round(72 + 2 * math.sin(i / 3)) if i >= 2 else -1
            spo2 = 98 if i >= 2 else -1
            return f'R,{i},{(i + 1) * 4000},{hr},{spo2},{int(i >= 2)},{int(i >= 2)}\n'.encode()
        return b''

    def close(self):
        pass


def plot_readings(out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import pandas as pd
    data = pd.read_csv(out / 'heart_rate_readings.csv')
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(data.time_s, data.heart_rate_bpm, 'o-', markersize=3)
    title = 'SEN0518 reported heart rate — four-second polling, not beat-to-beat'
    if data.simulated.any():
        title = 'SIMULATED DATA — ' + title
    ax.set(xlabel='Device elapsed time (s)', ylabel='Reported heart rate (bpm)', title=title)
    ax.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(out / 'heart_rate.png', dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list-ports', action='store_true')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--port', help='Nano USB serial port; close Arduino Serial Monitor first')
    source.add_argument('--simulate', action='store_true', help='Synthetic software check, no hardware')
    parser.add_argument('--duration', type=int, default=300, help='Seconds, multiple of 4 (default 300)')
    parser.add_argument('--out', type=Path, help='New directory; existing sessions are never overwritten')
    args = parser.parse_args()
    if args.list_ports:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        for port in ports:
            print(f'{port.device}\t{port.description}')
        if not ports:
            print('No serial ports found. Connect the Nano with a USB data cable.')
        return
    if not args.port and not args.simulate:
        parser.error('Choose --port or --simulate, or use --list-ports')
    if not 4 <= args.duration <= 3600 or args.duration % 4:
        parser.error('duration must be a multiple of 4 between 4 and 3600 seconds')
    out = args.out or Path(__file__).resolve().parent / 'recordings' / (
        'gravity_' + datetime.now(ZoneInfo('America/Toronto')).strftime('%Y%m%d_%H%M%S'))
    if out.exists():
        parser.error(f'Output directory exists; choose a new session: {out}')
    if args.simulate:
        print('SIMULATED readings — software check only.')
        stream = SimulatedGravity()
    else:
        try:
            import serial
            stream = serial.Serial(args.port, 115200, timeout=1, write_timeout=2, exclusive=True)
        except ImportError:
            parser.error('Install requirements-live.txt using the project Python environment')
        except (OSError, serial.SerialException) as exc:
            parser.error(f'Cannot open port: {exc}')
    out.mkdir(parents=True, exist_ok=False)
    try:
        summary = record(stream, out, args.duration, args.simulate)
    except (CaptureError, OSError, KeyboardInterrupt) as exc:
        print(f'Recording incomplete: {exc or "interrupted"}. Saved data: {out.resolve()}', file=sys.stderr)
        sys.exit(1)
    finally:
        stream.close()
        path = out / 'capture_metadata.json'
        if path.exists():
            meta = json.loads(path.read_text())
            meta.update(serial_port=args.port, baud=115200)
            path.write_text(json.dumps(meta, indent=2) + '\n')
    plot_readings(out)
    print(f'\nSaved {summary["readings_saved"]} readings to {out.resolve()}')
    if summary['mean_reported_hr_bpm'] is not None:
        print(f'Mean reported heart rate: {summary["mean_reported_hr_bpm"]:.1f} bpm')
    else:
        print('No usable heart-rate values; check finger positioning and review the log.')
    print('HRV/PRV: ' + UNAVAILABLE)
    if args.simulate:
        print('SIMULATION ONLY — no sensor was measured.')


if __name__ == '__main__':
    main()
