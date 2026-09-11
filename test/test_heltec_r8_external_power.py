"""Run the real R8 board's power detector with simulated USB, ADC and time."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

ARDUINO = r'''
#pragma once
#include <cstdint>
#define PIN_VEXT_EN 40
#define PIN_VEXT_EN_ACTIVE 0
#define PIN_VBAT_READ 1
#define P_LORA_DIO_1 14
#define P_LORA_NSS 8
#define P_LORA_TX_LED 46
#define P_LORA_PA_POWER 7
#define HIGH 1
#define LOW 0
#define BD_STARTUP_RX_PACKET 1
inline uint32_t test_now = 0;
inline uint32_t millis() { return test_now; }
inline void digitalWrite(int, int) {}
inline void analogReadResolution(int) {}
inline uint32_t analogReadMilliVolts(int) { return 830; }
'''

BASE_BOARD = r'''
#pragma once
#include <Arduino.h>
class ESP32Board {
protected:
  uint8_t startup_reason = 0;
public:
  void begin() {}
  virtual ~ESP32Board() = default;
  virtual void onBeforeTransmit() {}
  virtual void onAfterTransmit() {}
  virtual void powerOff() {}
  virtual bool setLoRaFemLnaEnabled(bool) { return false; }
  virtual bool canControlLoRaFemLna() const { return false; }
  virtual bool isLoRaFemLnaEnabled() const { return false; }
  virtual uint16_t getBattMilliVolts() { return 0; }
  virtual bool isExternalPowered() { return false; }
  virtual bool isUsbHostConnected() { return false; }
  virtual bool setAdcMultiplier(float) { return false; }
  virtual float getAdcMultiplier() const { return 0; }
  virtual const char* getManufacturerName() const { return "test"; }
};
'''

TEST = r'''
#include <cassert>
#include "HeltecV4R8Board.h"
#include <helpers/ui/DisplayPowerPolicy.h>
void LoRaFEMControl::init() {}
void LoRaFEMControl::setSleepModeEnable() {}
void LoRaFEMControl::setTxModeEnable() {}
void LoRaFEMControl::setRxModeEnable() {}
void LoRaFEMControl::setLNAEnable(bool enabled) { lna_enabled = enabled; }

class TestBoard : public HeltecV4R8Board {
public:
  bool host = false;
  uint16_t voltage = 3700;
  unsigned reads = 0;
  bool isUsbHostConnected() override { return host; }
  uint16_t getBattMilliVolts() override { ++reads; return voltage; }
};

int main() {
  TestBoard board;
  assert(!board.isExternalPowered());
  assert(board.reads == 1); // Sample immediately, including at millis() == 0.
  board.voltage = 4230;
  test_now = 4999;
  assert(!board.isExternalPowered());
  assert(board.reads == 1); // Frequent UI polling must not repeatedly use ADC.
  test_now = 5000;
  assert(board.isExternalPowered());
  assert(board.reads == 2);
  board.voltage = 4205;
  test_now = 10000;
  assert(board.isExternalPowered()); // Noise inside the margin retains state.
  board.voltage = 4200;
  test_now = 15000;
  assert(!board.isExternalPowered());
  board.voltage = 4210;
  test_now = 20000;
  assert(!board.isExternalPowered()); // Exactly 4.21 V is not above threshold.
  board.voltage = 4211;
  test_now = 25000;
  assert(board.isExternalPowered());
  board.voltage = 0;
  test_now = 30000;
  assert(!board.isExternalPowered()); // An absent/invalid low reading clears it.

  // Host detection overrides a cached negative voltage result immediately,
  // including an empty battery; removing the host must not latch external power.
  const unsigned reads = board.reads;
  board.host = true;
  assert(board.isExternalPowered());
  assert(board.reads == reads);
  board.host = false;
  assert(!board.isExternalPowered());

  // A long host connection must not preserve stale high battery voltage.
  board.voltage = 4250;
  test_now = 35000;
  assert(board.isExternalPowered());
  board.host = true;
  board.voltage = 3700;
  test_now = 50000;
  assert(board.isExternalPowered());
  board.host = false;
  assert(!board.isExternalPowered());

  TestBoard wrapped;
  test_now = UINT32_MAX - 1000;
  assert(!wrapped.isExternalPowered());
  wrapped.voltage = 4230;
  test_now = 3998;
  assert(!wrapped.isExternalPowered());
  test_now = 3999;
  assert(wrapped.isExternalPowered()); // Five-second sampling survives wrap.

  // Both sources select the actual USB display profile, returning to battery
  // when neither source is detected. No additional display setting is needed.
  mesh::ui::DisplayPowerPrefs prefs;
  prefs.battery.mode = mesh::ui::DisplayMode::Off;
  prefs.usb.mode = mesh::ui::DisplayMode::On;
  mesh::ui::DisplayPowerPolicy display;
  TestBoard source;
  test_now = 0;
  const auto update = [&]() {
    display.update(prefs, source.isExternalPowered() || source.isUsbHostConnected(),
                   false, false, test_now);
  };
  update(); assert(!display.on());
  source.host = true;
  update(); assert(display.on());
  source.host = false;
  update(); assert(!display.on());
  source.voltage = 4230;
  test_now = 5000;
  update(); assert(display.on());
  source.voltage = 4100;
  test_now = 10000;
  update(); assert(!display.on());

  // The estimate uses the real calibrated ADC path, including user calibration.
  HeltecV4R8Board calibrated;
  calibrated.setAdcMultiplier(5.1f);
  assert(calibrated.isExternalPowered()); // 830 mV * 5.1 = 4233 mV.
  calibrated.setAdcMultiplier(5.0f);
  test_now += 5000;
  assert(!calibrated.isExternalPowered()); // Same raw sample becomes 4150 mV.
}
'''


class R8ExternalPowerTest(unittest.TestCase):
    def test_host_voltage_and_display_profile(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="mesh-r8-power-") as directory:
            work = Path(directory)
            files = {
                "Arduino.h": ARDUINO,
                "helpers/ESP32Board.h": BASE_BOARD,
                "helpers/RefCountedDigitalPin.h": """#pragma once
class RefCountedDigitalPin {
public:
  RefCountedDigitalPin(int, int) {}
  void begin() {}
  void claim() {}
  void release() {}
};
""",
                "driver/rtc_io.h": """#pragma once
using gpio_num_t = int;
using esp_reset_reason_t = int;
constexpr int ESP_RST_DEEPSLEEP = 1;
inline int esp_reset_reason() { return 0; }
inline long esp_sleep_get_ext1_wakeup_status() { return 0; }
inline void rtc_gpio_hold_dis(int) {}
inline void rtc_gpio_hold_en(int) {}
inline void rtc_gpio_deinit(int) {}
""",
                "test.cpp": TEST,
            }
            for path, content in files.items():
                target = work / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            executable = work / "r8-power.exe"
            variant = ROOT / "variants/heltec_v4_r8"
            result = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-I" + str(work), "-I" + str(variant), "-I" + str(ROOT / "src"),
                str(work / "test.cpp"), str(variant / "HeltecV4R8Board.cpp"),
                "-o", str(executable),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
