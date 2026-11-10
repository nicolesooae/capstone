// Arduino Nano ESP32 + DFRobot Gravity SEN0518 processed measurements.
// NOT the direct MAX30102 register map. No raw PPG / pulse intervals / HRV.
// Register layout and encoding verified against DFRobot_BloodOxygen_S.cpp:
// https://github.com/DFRobot/DFRobot_BloodOxygen_S
#include <Arduino.h>
#include <Wire.h>

constexpr uint8_t ADDRESS = 0x57;
constexpr uint32_t POLL_MS = 4000;
bool running = false;
uint32_t sequence = 0;
uint32_t lastPoll = 0;
uint32_t startMs = 0;
char command[24];
size_t commandLength = 0;

bool collect(bool start) {
  Wire.beginTransmission(ADDRESS);
  Wire.write(uint8_t(0x20));
  Wire.write(uint8_t(0));
  Wire.write(uint8_t(start ? 1 : 2));
  return Wire.endTransmission() == 0;
}

void ready() {
  Serial.println("# READY {\"protocol\":1,\"device\":\"Gravity SEN0518\",\"data_type\":\"processed_hr_spo2\",\"poll_interval_ms\":4000,\"raw_ppg\":false,\"prv_supported\":false}");
}

void error(const char *message) {
  running = false;
  collect(false);
  Serial.print("# ERROR ");
  Serial.println(message);
}

void handle(const char *message) {
  if (strcmp(message, "HELLO") == 0) {
    if (!running) ready();
  } else if (strcmp(message, "START") == 0) {
    if (running) {
      error("ALREADY_RUNNING");
      return;
    }
    Wire.beginTransmission(ADDRESS);
    if (Wire.endTransmission() != 0 || !collect(true)) {
      error("SENSOR_NOT_FOUND check I2C switch, 3V3, GND, A4 and A5");
      return;
    }
    sequence = 0;
    startMs = lastPoll = millis();
    running = true;
    Serial.println("# STARTED");
  } else if (strcmp(message, "STOP") == 0) {
    running = false;
    if (!collect(false)) {
      error("I2C_STOP_FAILED");
      return;
    }
    Serial.println("# STOPPED");
  } else {
    error("UNKNOWN_COMMAND");
  }
}

void setup() {
  Serial.begin(115200);
  // Board aliases work with either Nano pin-numbering mode: A4=SDA, A5=SCL.
  Wire.begin(SDA, SCL, 100000);
  Wire.setTimeOut(100);
  delay(300);
  ready();
}

void loop() {
  while (Serial.available()) {
    const char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      command[commandLength] = '\0';
      if (commandLength) handle(command);
      commandLength = 0;
    } else if (commandLength < sizeof(command) - 1) {
      command[commandLength++] = ch;
    } else {
      commandLength = 0;
      error("COMMAND_TOO_LONG");
    }
  }
  if (!running || uint32_t(millis() - lastPoll) < POLL_MS) {
    delay(1);
    return;
  }
  lastPoll = millis();
  Wire.beginTransmission(ADDRESS);
  Wire.write(uint8_t(0x0C));
  if (Wire.endTransmission() != 0 || Wire.requestFrom(ADDRESS, size_t(8), true) != 8) {
    error("I2C_MEASUREMENT_READ_FAILED");
    return;
  }
  uint8_t bytes[8];
  for (int i = 0; i < 8; ++i) bytes[i] = Wire.read();
  const uint32_t hr = (uint32_t(bytes[2]) << 24) | (uint32_t(bytes[3]) << 16)
                    | (uint32_t(bytes[4]) << 8) | uint32_t(bytes[5]);
  const bool hrPresent = hr > 0 && hr <= 300;
  const bool spo2Present = bytes[0] > 0 && bytes[0] <= 100;
  // Presence/range flags do NOT establish accuracy or distinguish stale values.
  Serial.printf("R,%lu,%lu,%ld,%d,%d,%d\n", (unsigned long)sequence++,
    (unsigned long)(millis() - startMs), hrPresent ? (long)hr : -1L,
    spo2Present ? int(bytes[0]) : -1, int(hrPresent), int(spo2Present));
}
