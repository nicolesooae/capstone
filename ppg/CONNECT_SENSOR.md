# Connect the MAX30102 through an ESP32

**For your confirmed Gravity SEN0518, use [USE_GRAVITY.md](USE_GRAVITY.md) and
`record_gravity.py` to capture reported heart rate and SpO2.** The raw-PPG
workflow below is separate and cannot provide PRV with this module's documented
processed-output interface.

The workflow is:

**MAX30102 → ESP32 → USB → computer → raw CSV → resting PRV analysis**

## Your confirmed hardware

The supplied photos identify an **Arduino Nano ESP32** (NORA-W106 module) and a
**DFRobot Gravity SEN0518, board marking V1.0**. The package explicitly says
SEN0518; this is not the legacy five-pin SEN0344 V1.0 raw-I2C breakout.

**The current raw-PPG recorder is not compatible with the SEN0518's documented
interface.** Its onboard MCU reports processed heart rate, SpO2 and temperature.
DFRobot's [Arduino example](https://wiki.dfrobot.com/sen0518/docs/19639) states
that results update every four seconds. Its [published protocol](https://wiki.dfrobot.com/sen0518/docs/19637)
does not document raw red/IR sample streaming or individual pulse timestamps.
Repeatedly polling those processed values cannot supply the pulse intervals
needed for RMSSD, SDNN and pNN50. This does not prove there is no undocumented
raw mode, but one has not been established.

For a sensor-specific HR/SpO2 connection test using DFRobot's own library, these
are the I2C connections matching the labels and wire colors visible in the photos:

| SEN0518 lead | Nano ESP32 pin |
| --- | --- |
| VCC — red | 3V3 |
| GND — black | GND |
| D/T — green | A4 / SDA |
| C/R — blue (clock/RX) | A5 / SCL |

Set the sensor's switch to **I2C** with power disconnected. The manufacturer
specifies 3.3 V or 5 V input; use 3.3 V with this Nano setup. These connections
do not turn the module into a raw-PPG source. Use the SEN0518-specific
`firmware/nano_gravity_sen0518/nano_gravity_sen0518.ino` with `record_gravity.py`
for processed measurements, not `record_max30102.py` or the direct-MAX30102 sketch.

Completing the live PRV workflow requires confirmed raw waveform access from
DFRobot or a direct-I2C raw-output sensor breakout. The following sections describe
the already prepared raw-output workflow, conditional on having such a module.

## Raw-output sensor compatibility

The firmware board target is **Arduino Nano ESP32**. The [legacy SEN0344 V1.0](https://wiki.dfrobot.com/sen0344/docs/19237)
is a five-pin direct-I2C breakout compatible with this approach. DFRobot's
[SEN0344 V2.0](https://wiki.dfrobot.com/sen0344/) and
[Gravity SEN0518](https://wiki.dfrobot.com/sen0518/) use a different onboard-MCU
interface; their documented heart-rate/SpO2 API is not a raw ADC stream and is
not supported by this firmware. A heart-rate reading alone cannot replace
consecutive pulse intervals for this PRV calculation. Do not assume all modules
marked MAX30102 are equivalent, or confuse the SEN0518 V1.0 marking with SEN0344 V1.0.

The ESP32 streams red and infrared ADC counts at **400 samples/second per
channel**, with no on-sensor averaging. The computer discards 10 seconds of
settling data, records five minutes, then analyzes complete one-minute windows.
Both channels are saved; red is analyzed by default to match the channel used
in the PhysioNet comparison. Use `--channel ir` to analyze infrared instead.

This is a recording-and-analysis workflow: the terminal shows recording progress
and ADC levels during capture, and heart rate/PRV results appear after capture.
SVRi is omitted. No SpO2 algorithm is included.

## 1. Connect your breakout to the ESP32

For an **Arduino Nano ESP32 with the direct-I2C five-pin SEN0344 V1.0**, connect
the following with power disconnected:

| DFRobot SEN0344 V1.0 | Arduino Nano ESP32 |
| --- | --- |
| GND | GND |
| SDA | A4 (SDA) |
| SCL | A5 (SCL) |
| VCC | 3V3 (the V1.0 breakout specifies 3.3–5 V input) |
| INT | Leave unconnected; firmware polls the FIFO |

Use a regulated, ESP32-compatible breakout with I2C pull-ups at an appropriate
logic voltage. **If its documentation specifies 3.3 V input, use the ESP32 3V3
pin.** Confirm the breakout's supply specification before connecting VIN: the
bare MAX30102 IC uses separate 1.8 V and LED supplies, and breakout regulation
and level shifting vary. The ESP32 I2C signals must not be pulled up to 5 V.
Do not connect a bare IC as though it were a regulated breakout.

The firmware now uses the board package's `SDA` and `SCL` aliases, so the
Nano ESP32's Arduino/GPIO pin-numbering modes are handled by its core. Nano A4/A5
map physically to GPIO11/GPIO12; they are not the classic ESP32 GPIO21/GPIO22.
For a classic ESP32 DevKit, the aliases retain its GPIO21/GPIO22 defaults.
For another board or custom wiring, edit `SDA_PIN` and `SCL_PIN` near the top of
`firmware/esp32_max30102/esp32_max30102.ino` to suitable board pin aliases.
Connect the ESP32 to the computer with a **USB data cable**.

## 2. Upload the ESP32 firmware

Using Arduino IDE:

1. Install Arduino IDE and the **esp32 by Espressif Systems** board package.
   The board-manager URL is
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`.
2. Open `firmware/esp32_max30102/esp32_max30102.ino`.
3. Select **Arduino Nano ESP32** and its USB port for the Arduino-branded Nano.
   Keep its native USB CDC interface enabled. Do not select ESP32 Dev Module for
   this board; that target is for a classic ESP32 DevKit.
4. Upload. Some classic boards require holding BOOT while upload connects.
5. Close Serial Monitor before running the Python recorder. Only one program
   should own the port. The firmware waits for the recorder's START command.

No SparkFun or other sensor library is needed: the sketch configures and reads
the MAX30102 directly using the ESP32 core's Wire library. The chip's reported
part ID is shared with other MAX3010x models, so verify the physical module label.

Alternatively, the included `platformio.ini` defaults to `nano_esp32` with the
`arduino_nano_esp32` board and pins Espressif32 to 6.12.0. With PlatformIO installed:

```sh
pio run --project-dir firmware/esp32_max30102 --environment nano_esp32
pio run --project-dir firmware/esp32_max30102 --environment nano_esp32 --target upload
```

An `esp32dev` environment is also retained for classic boards. Board builds use
Arduino core 2.0.17; other core versions need their own build/upload check.
The Nano ESP32 uses native USB, so its port may change after uploading/resetting.

## 3. Find the port and record

Open a terminal in the `halo_ppg` directory. The Python dependency for serial
capture is already installed in this workspace's virtual environment. On a fresh
installation, install it first:

```sh
.venv/bin/python -m pip install -r requirements-live.txt
```

List ports:

```sh
.venv/bin/python record_max30102.py --list-ports
```

On macOS, an ESP32 commonly appears as `/dev/cu.usbserial-...`,
`/dev/cu.SLAB_USBtoUART`, or `/dev/cu.usbmodem...`. Select the device that appears
when you connect the board; do not select a Bluetooth or debug-console port.

Sit quietly, support your hand, and place a finger securely over the optical
window without pressing hard. Position it **before** starting capture and keep
it still throughout. Then substitute your actual port:

```sh
.venv/bin/python record_max30102.py --port /dev/cu.YOUR_ESP32_PORT
```

The default recording lasts about **5 minutes 10 seconds**, including settling.
Results are written to a new `recordings/YYYYMMDD_HHMMSS` session directory
(America/Toronto time). The console prints each window's acceptance, heart rate,
RMSSD, SDNN and pNN50. Rejected windows have blank variability values and a reason.

For a one-minute connection check, saved separately:

```sh
.venv/bin/python record_max30102.py --port /dev/cu.YOUR_ESP32_PORT --duration 60 --out recordings/first_connection
```

Optional settings:

```sh
# Analyze a single five-minute variability window:
.venv/bin/python record_max30102.py --port /dev/cu.YOUR_ESP32_PORT --hrv-window 300

# Analyze infrared, retaining both optical channels in the raw file:
.venv/bin/python record_max30102.py --port /dev/cu.YOUR_ESP32_PORT --channel ir
```

Use a new output directory for each attempt. The recorder refuses to overwrite
an existing session. Ctrl-C preserves partial data and skips automatic analysis.

## 4. Review the session

| File | Contents |
| --- | --- |
| `raw_ppg.csv` | Saved red/IR counts and timing audit fields, after settling |
| `capture_metadata.json` | Completion status, firmware settings, nominal and observed cadence, simulation flag |
| `serial_log.txt` | Serial messages, including settling samples and any device errors |
| `analysis/hrv_features.csv` | Accepted/rejected resting PRV windows |
| `analysis/ppg_first_30s.png` | Waveform, detected peaks and template quality |
| `analysis/detected_peaks.csv` | Peak positions for manual review |
| `analysis/analysis_metadata.json` | Analysis settings and acquisition provenance |

The main pipeline's other signal/window files are saved under `analysis` too.
Automatic analysis uses only a complete capture. A failure leaves the raw file
and error metadata for inspection; it does not generate a PRV report.

You can reanalyze a **complete** session using the other channel without
recording again:

```sh
.venv/bin/python halo_ppg.py --csv recordings/YOUR_SESSION/raw_ppg.csv --fs 400 --channel ir --signal-format adc --skip-hemodynamics --out recordings/YOUR_SESSION/analysis_ir
```

Check `capture_metadata.json` before manually reanalyzing: a failed or interrupted
session may contain incomplete data and must not be treated as an intact capture.

## Acquisition integrity and troubleshooting

The firmware configures 400 Hz sampling, 411 us pulses / 18-bit output, a
16384 nA ADC range, one sample per FIFO entry (no averaging), and an initial
LED-current code of `0x20`. Settings are read back before acquisition. There is
no automatic LED-current adjustment during recording. If positioning or signal
levels need adjustment, stop and begin a fresh session. Persistent weak or
clipped signals may require tuning the LED-current setting for your breakout.

`time_s` is derived from the contiguous sample counter and the **nominal sensor
rate**, not USB arrival time. The sensor's FIFO is read in acquisition order.
`device_read_us` records ESP32 FIFO-read time, not an exact optical conversion
timestamp; it audits overall cadence. Serial buffering therefore does not change
beat intervals. The recorder checks contiguous IDs, monotonic device time,
stalls, and overall cadence within 2% of 400 Hz. These checks do not constitute
independent calibration of the sensor's sampling clock.

The firmware stops on excessive FIFO backlog/overflow, I2C failures, sensor
brownout, ambient-light cancellation overflow or a stalled sensor. The host also
checks the STOP acknowledgement so trailing faults cannot be hidden by reaching
the requested sample count. Missing data are never silently interpolated.

- **No port:** check the data cable, board power and the USB bridge driver required
  by your specific board (often CP210x or CH340).
- **No handshake:** check the uploaded sketch, selected port, board settings and
  that Serial Monitor is closed. Firmware/recorder baud is 460800.
- **SENSOR_NOT_FOUND:** verify breakout supply, common ground, SDA/SCL, and address
  `0x57`; check the pins for your ESP32 variant.
- **FIFO_BACKLOG_OR_OVERFLOW:** start a new session after checking serial settings,
  USB connection and competing applications. Do not lower only the host baud.
- **Clipped/flat or rejected PRV:** inspect the waveform and finger positioning,
  then repeat at rest. The program does not independently verify finger contact.

## Test without hardware

```sh
.venv/bin/python record_max30102.py --simulate --duration 60 --out recordings/simulated_check
.venv/bin/python -m unittest -v test_record_max30102 test_halo_metrics
```

Simulation exercises the serial protocol, file saving and analysis in accelerated
time. It is explicitly marked in capture metadata and variability results.
It is **not** a test of the sensor, wiring, firmware timing or physiological
accuracy. Hardware validation with ECG remains separate from software readiness.

References: [MAX30102 datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/max30102.pdf),
[Arduino Nano ESP32 pins](https://docs.arduino.cc/tutorials/nano-esp32/cheat-sheet/),
[Espressif I2C API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/i2c.html),
[Espressif Arduino installation](https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html),
[pySerial API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html).
