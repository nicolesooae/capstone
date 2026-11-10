# Heart rate with your Nano ESP32 and Gravity SEN0518

This workflow is specifically for the **Arduino Nano ESP32 + DFRobot Gravity
SEN0518 V1.0** identified in your photos. It displays and saves the sensor's
processed heart rate and SpO2, polling every four seconds. It does not measure
beat-to-beat variability. RMSSD, SDNN and pNN50 are explicitly unavailable in
the summary because the documented SEN0518 interface supplies neither raw PPG
nor individual pulse intervals.

## 1. Wire the sensor with power disconnected

Set the sensor's slide switch to **I2C**. Connect:

| Sensor wire / label | Nano ESP32 |
| --- | --- |
| Red / VCC | 3V3 |
| Black / GND | GND |
| Green / D/T | A4 (SDA) |
| Blue / C/R | A5 (SCL) |

The SEN0518 supports 3.3 V input; use the Nano's 3V3 connection here. Connect
the Nano to the computer with a USB data cable. No other sensor is required.

## 2. Upload this firmware once

1. In Arduino IDE's Boards Manager, search for **Nano ESP32**, install
   **Arduino ESP32 Boards**, and select **Arduino Nano ESP32**.
2. Open `firmware/nano_gravity_sen0518/nano_gravity_sen0518.ino`.
3. Select the Nano's USB port and upload. Keep its native USB CDC interface enabled.
4. Close Serial Monitor before starting the Python recorder. Recheck the port
   after upload if it changes.

Only the ESP32 Arduino core is required; no extra sensor library is needed.
The sketch implements the processed-measurement I2C register format checked
against [DFRobot's driver](https://github.com/DFRobot/DFRobot_BloodOxygen_S).
It is separate from the earlier direct-MAX30102 raw-PPG sketch, which is not
compatible with your SEN0518.

The included PlatformIO configuration targets `arduino_nano_esp32` on platform
6.12.0. If using PlatformIO instead of Arduino IDE:

```sh
pio run --project-dir firmware/nano_gravity_sen0518
pio run --project-dir firmware/nano_gravity_sen0518 --target upload
```

## 3. Find the USB port and record

Run these commands from the `halo_ppg` directory. The dependency is already
installed in the workspace's virtual environment. For a fresh setup:

```sh
.venv/bin/python -m pip install -r requirements-live.txt
```

Find the port:

```sh
.venv/bin/python record_gravity.py --list-ports
```

Select the USB device that appears when you connect the Nano, usually
`/dev/cu.usbmodem...` on macOS. Do not select a Bluetooth device or debug console.
Support your hand and place your finger securely over the optical sensor, then
replace the placeholder with your actual port:

```sh
.venv/bin/python record_gravity.py --port /dev/cu.YOUR_NANO_PORT
```

The default session lasts approximately five minutes. For a short first check:

```sh
.venv/bin/python record_gravity.py --port /dev/cu.YOUR_NANO_PORT --duration 60
```

The terminal shows heart rate in bpm and SpO2 in percent when those values are
present. Initial readings may be unavailable while the sensor acquires. Values
are checked for presence/range, not physiological accuracy or fresh updates.
Unchanged readings are retained because the module does not expose a per-poll
freshness indicator through this interface. Polling faster would not provide
individual pulse timing.

## 4. Saved output

Each run creates a new `recordings/gravity_YYYYMMDD_HHMMSS` directory, using
America/Toronto time. Existing directories are never overwritten.

- `heart_rate_readings.csv`: device elapsed time, reported HR/SpO2, presence
  flags, serial sequence, host arrival time and simulation flag. Missing values
  remain blank; they are not replaced by zero or interpolated.
- `heart_rate.png`: reported heart rate over time, with gaps for missing values.
- `summary.json`: count, mean and range of reported HR values; PRV status and
  null RMSSD/SDNN/pNN50 fields. These HR statistics describe four-second polls,
  not normal-to-normal intervals or HRV.
- `capture_metadata.json`: device/protocol information, completion status and
  limitations. A complete capture can still contain no usable HR measurements.
- `serial_log.txt`: device messages for troubleshooting.

Ctrl-C, disconnects, device errors, clock/counter resets and unexpected polling
cadence mark the capture incomplete/failed and preserve readings already saved.
The recorder checks STOP acknowledgement. No PRV values are generated, including
from partial or simulated sessions.

## Software check without the sensor

```sh
.venv/bin/python record_gravity.py --simulate --duration 60 --out recordings/gravity_simulated
.venv/bin/python -m unittest -v test_record_gravity
```

Simulation runs in accelerated time and is labeled throughout the output. It
does not test the physical sensor, wiring or accuracy. Firmware compilation
and protocol checks do not replace the first physical connection test.

Manufacturer references: [SEN0518 specifications](https://wiki.dfrobot.com/sen0518/),
[four-second update example](https://wiki.dfrobot.com/sen0518/docs/19639),
[documented interface](https://wiki.dfrobot.com/sen0518/docs/19637).
