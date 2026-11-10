// HALO raw MAX30102 acquisition. No beat detection, averaging or interpolation.
// Arduino IDE: select your ESP32 board. Only the ESP32 Arduino core is required.
// Uses board-defined I2C pins: Nano ESP32 A4/A5, classic ESP32 GPIO21/GPIO22.
#include <Arduino.h>
#include <Wire.h>
#include <esp_timer.h>

constexpr int SDA_PIN = SDA;
constexpr int SCL_PIN = SCL;
constexpr uint8_t SENSOR_ADDRESS = 0x57;
constexpr uint32_t BAUD = 460800;
constexpr uint32_t SAMPLE_RATE = 400;
constexpr uint8_t LED_CURRENT = 0x20;  // Starting setting; keep fixed per recording.
constexpr uint8_t SPO2_CONFIG = 0x6F;  // 16384 nA range, 400 Hz, 411 us / 18 bits.
constexpr uint8_t FIFO_CONFIG = 0x0F;  // Average=1, rollover disabled, almost-full=17.

bool streaming = false;
uint32_t sequence = 0;
int64_t startedUs = 0;
int64_t lastSampleUs = 0;
char command[32];
size_t commandLength = 0;

bool readBytes(uint8_t reg, uint8_t *data, size_t count) {
  Wire.beginTransmission(SENSOR_ADDRESS);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(SENSOR_ADDRESS, count, true) != count) return false;
  for (size_t i = 0; i < count; ++i) data[i] = Wire.read();
  return true;
}

bool writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(SENSOR_ADDRESS);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

void fail(const char *reason) {
  streaming = false;
  writeRegister(0x09, 0x83);  // Shut down optical conversion.
  Serial.print("# ERROR ");
  Serial.println(reason);
}

void ready() {
  Serial.printf("# READY {\"protocol\":1,\"firmware\":\"halo-esp32-1\",\"sensor\":\"MAX30102\",\"fs_hz\":%lu,\"sample_average\":1,\"adc_bits\":18,\"pulse_width_us\":411,\"adc_range_na\":16384,\"led_current_code\":%u,\"sda_pin\":%d,\"scl_pin\":%d,\"channels\":[\"red\",\"ir\"]}\n",
    (unsigned long)SAMPLE_RATE, (unsigned int)LED_CURRENT, SDA_PIN, SCL_PIN);
}

bool configureSensor() {
  uint8_t value;
  if (!readBytes(0xFF, &value, 1) || value != 0x15) {
    fail("SENSOR_NOT_FOUND check address 0x57, wiring and breakout supply");
    return false;
  }
  // 0x15 is shared with other MAX3010x parts; verify your module is MAX30102.
  if (!writeRegister(0x09, 0x40)) {
    fail("I2C_RESET_FAILED");
    return false;
  }
  const int64_t deadline = esp_timer_get_time() + 100000;
  do {
    if (!readBytes(0x09, &value, 1)) {
      fail("I2C_RESET_READ_FAILED");
      return false;
    }
    if (!(value & 0x40)) break;
    delay(1);
  } while (esp_timer_get_time() < deadline);
  if (value & 0x40) {
    fail("SENSOR_RESET_TIMEOUT");
    return false;
  }
  // Configure while shut down, clear all FIFO state before each fresh session.
  const uint8_t registers[][2] = {
    {0x09, 0x83}, {0x02, 0x00}, {0x03, 0x00},
    {0x04, 0x00}, {0x05, 0x00}, {0x06, 0x00},
    {0x08, FIFO_CONFIG}, {0x0A, SPO2_CONFIG},
    {0x0C, LED_CURRENT}, {0x0D, LED_CURRENT}
  };
  for (const auto &setting : registers) {
    if (!writeRegister(setting[0], setting[1])) {
      fail("I2C_CONFIG_WRITE_FAILED");
      return false;
    }
  }
  for (const auto &setting : registers) {
    if (!readBytes(setting[0], &value, 1) || value != setting[1]) {
      fail("SENSOR_CONFIG_READBACK_FAILED");
      return false;
    }
  }
  // Clear startup status before conversion; subsequent PWR_RDY means brownout.
  uint8_t status[2];
  if (!readBytes(0x00, status, 2)) {
    fail("I2C_STATUS_FAILED");
    return false;
  }
  return true;
}

void handleCommand(const char *text) {
  if (strcmp(text, "HELLO") == 0) {
    if (!streaming) ready();
  } else if (strcmp(text, "START") == 0) {
    if (streaming) {
      fail("ALREADY_STREAMING");
      return;
    }
    if (!configureSensor()) return;
    sequence = 0;
    Serial.println("# STARTED");
    Serial.flush();  // Finish handshake before enabling conversion.
    startedUs = esp_timer_get_time();
    lastSampleUs = startedUs;
    if (!writeRegister(0x09, 0x03)) {
      fail("I2C_START_FAILED");
      return;
    }
    streaming = true;
  } else if (strcmp(text, "STOP") == 0) {
    // A final FIFO/status check is performed by loop() before this acknowledgement.
    streaming = false;
    if (!writeRegister(0x09, 0x83)) {
      fail("I2C_STOP_FAILED");
      return;
    }
    Serial.println("# STOPPED");
  } else {
    fail("UNKNOWN_COMMAND");
  }
}

bool checkStatus(uint8_t &available) {
  uint8_t status[2], pointers[3];
  if (!readBytes(0x00, status, 2) || !readBytes(0x04, pointers, 3)) {
    fail("I2C_READ_FAILED");
    return false;
  }
  if (status[0] & 0x01) {
    fail("SENSOR_BROWNOUT");
    return false;
  }
  if (status[0] & 0x20) {
    fail("AMBIENT_LIGHT_OVERFLOW cover sensor from strong light");
    return false;
  }
  available = (pointers[0] - pointers[2]) & 0x1F;
  // Stop before full/empty pointer ambiguity or a lost-sample counter can reset.
  if ((pointers[1] & 0x1F) || available >= 16 || (status[0] & 0x80)) {
    fail("FIFO_BACKLOG_OR_OVERFLOW recording invalid; check USB speed and cable");
    return false;
  }
  return true;
}

void setup() {
  Serial.begin(BAUD);
  Wire.begin(SDA_PIN, SCL_PIN, 400000);
  Wire.setTimeOut(20);
  delay(300);
  ready();
}

void loop() {
  // Check before commands, including STOP, so a trailing acquisition fault
  // cannot be hidden by the computer reaching its requested sample count.
  uint8_t available = 0;
  if (streaming && !checkStatus(available)) return;
  while (Serial.available()) {
    const char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      command[commandLength] = '\0';
      if (commandLength) handleCommand(command);
      commandLength = 0;
    } else if (commandLength < sizeof(command) - 1) {
      command[commandLength++] = ch;
    } else {
      commandLength = 0;
      fail("COMMAND_TOO_LONG");
    }
  }
  if (!streaming) {
    delay(1);
    return;
  }
  if (available) {
    uint8_t bytes[6];
    if (!readBytes(0x07, bytes, sizeof(bytes))) {
      fail("I2C_FIFO_READ_FAILED");
      return;
    }
    const uint32_t red = ((uint32_t(bytes[0]) << 16) | (uint32_t(bytes[1]) << 8) | bytes[2]) & 0x3FFFF;
    const uint32_t ir = ((uint32_t(bytes[3]) << 16) | (uint32_t(bytes[4]) << 8) | bytes[5]) & 0x3FFFF;
    lastSampleUs = esp_timer_get_time();
    // read_us is a FIFO read time, NOT an ADC conversion timestamp. The sample
    // counter and nominal sensor rate establish the uniformly sampled timeline.
    Serial.printf("D,%lu,%llu,%lu,%lu\n", (unsigned long)sequence++,
      (unsigned long long)(lastSampleUs - startedUs), (unsigned long)red, (unsigned long)ir);
  } else if (esp_timer_get_time() - lastSampleUs > 1000000) {
    fail("SENSOR_STALLED no FIFO samples for 1 s");
  } else {
    delayMicroseconds(200);
  }
}
