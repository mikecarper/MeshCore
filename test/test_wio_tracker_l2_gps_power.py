#!/usr/bin/env python3
"""Host-test Wio L2's real GNSS provider, expander operations and GPS lifecycle.

Only the UART/I2C/ESP32 hardware boundaries and NMEA library are mocked. No
physical power/current claims are made by these tests.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_gps_upstream_adaptations import CAYENNE, MESH, NMEA, RTC
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/wio-tracker-l2"

ARDUINO = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <cassert>
#include <deque>
#include <string>
#include <vector>
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT 0
struct Event { std::string name; int a; int b; };
inline std::vector<Event> events;
inline int modes[64] = {};
inline uint32_t now_ms = 1;
inline void event(const char* name, int a=0, int b=0) { events.push_back({name,a,b}); }
inline uint32_t millis() { return now_ms; }
inline void delay(uint32_t ms) { now_ms += ms; }
inline void yield() { ++now_ms; }
inline void pinMode(int pin,int mode) { assert(pin>=0&&pin<64);modes[pin]=mode;event("pin",pin,mode); }
inline void digitalWrite(int,int) {}
inline int digitalRead(int) { return HIGH; }
class Stream {
public:
 virtual ~Stream() = default;
 virtual int available() { return 0; }
 virtual int peek() { return -1; }
 virtual int read() { return -1; }
 virtual void flush() {}
 virtual size_t write(uint8_t) { return 1; }
 virtual size_t write(const uint8_t*,size_t size) { return size; }
};
class HardwareSerial : public Stream {
public:
 bool installed=false;
 explicit operator bool() const { return installed; }
 unsigned begins=0, ends=0;
 int rx=-1,tx=-1;
 std::deque<char> incoming;
 void setPins(int r,int t) { rx=r;tx=t;event("uart-pins",r,t); }
 void begin(unsigned baud) {
   assert(rx>=0&&tx>=0);installed=true;++begins;
   pinMode(rx,INPUT);pinMode(tx,OUTPUT);event("uart-begin",baud);
 }
 void end() { installed=false;++ends;incoming.clear();event("uart-end"); }
 int available() override { return installed ? int(incoming.size()) : 0; }
 int peek() override { return available() ? incoming.front() : -1; }
 int read() override { if(!available())return -1;int c=incoming.front();incoming.pop_front();return c; }
 void feed(const char* text) { while(*text)incoming.push_back(*text++); }
};
inline HardwareSerial Serial1, Serial;
using std::isnan;
'''

WIRE = r'''
#pragma once
#include <Arduino.h>
class TwoWire {
 std::vector<uint8_t> sent;
 uint8_t address=0;
public:
 bool present=true;
 unsigned transactions=0;
 uint8_t regs[256];
 TwoWire() { memset(regs,0xff,sizeof(regs)); }
 bool gnssOn() const { return !(regs[7]&(1<<5)) && (regs[3]&(1<<5)); }
 void beginTransmission(uint8_t a) { address=a;sent.clear();++transactions; }
 size_t write(uint8_t byte) { sent.push_back(byte);return 1; }
 uint8_t endTransmission() {
   if(!present)return 4;
   if(address==0x21&&sent.size()==2) {
     const bool before=gnssOn();regs[sent[0]]=sent[1];
     if(before!=gnssOn()) {
       // Both enabling and disabling the receiver happen with UART pads
       // released, never an idle-high MCU TX into an unpowered GNSS RX.
       assert(!Serial1.installed&&modes[17]==INPUT&&modes[18]==INPUT);
       event(gnssOn()?"gnss-on":"gnss-off");
     }
     event("i2c-write",sent[0],sent[1]);
   }
   return 0;
 }
 int requestFrom(int,int size) { return size; }
 int read() { return 0; }
};
inline TwoWire Wire;
'''

ESP_BOARD = r'''
#pragma once
#include <Arduino.h>
class ESP32Board {
public:
 virtual void begin() {}
 virtual uint16_t getBattMilliVolts() { return 0; }
 virtual const char* getManufacturerName() const { return "host"; }
 virtual void onBeforeTransmit() {}
 virtual void onAfterTransmit() {}
 virtual bool isExternalPowered() { return false; }
 virtual void shutdownPeripherals();
};
'''


def harness(mutation=None):
    board = (VARIANT / "WioTrackerL2Board.cpp").read_text()
    if mutation == "boot-rail-on":
        board = board.replace("expSetOutput(EXP_PIN_GNSS_EN, LOW);",
                              "expSetOutput(EXP_PIN_GNSS_EN, HIGH);", 1)
    constants = "\n".join(line for line in board.splitlines()
                          if re.match(r"static const uint8_t TCA_REG_", line))
    expander = "\n".join(extract_braced(board, signature) for signature in (
        "bool WioTrackerL2Board::expWriteReg(",
        "void WioTrackerL2Board::expSetOutput(",
        "void WioTrackerL2Board::expSetInput(",
        "void WioTrackerL2Board::expWritePin(",
        "bool WioTrackerL2Board::initExpander()",
        "int WioTrackerL2Board::expReadInputs()",
    ))
    manager = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
    lifecycle = "\n".join(extract_braced(manager, signature) for signature in (
        "bool EnvironmentSensorManager::setSettingValue(",
        "void EnvironmentSensorManager::setPowerSavingEnabled(",
        "void EnvironmentSensorManager::finishBasicGpsDiscovery(",
        "void EnvironmentSensorManager::serviceBasicGpsDiscovery()",
        "void EnvironmentSensorManager::initBasicGPS()",
        "void EnvironmentSensorManager::armGpsPowerSavingCycle()",
        "void EnvironmentSensorManager::startGpsSerialTransport()",
        "void EnvironmentSensorManager::stopGpsSerialTransport()",
        "void EnvironmentSensorManager::start_gps()",
        "void EnvironmentSensorManager::stop_gps()",
        "void EnvironmentSensorManager::loop()",
    ))
    shutdown = extract_braced((ROOT / "src/helpers/ESP32Board.cpp").read_text(),
                              "void ESP32Board::shutdownPeripherals()")
    fixture = (ROOT / "test/fixtures/wio_tracker_l2_gps_power.cpp").read_text()
    return (fixture.replace("@EXPANDER@", constants + "\n" + expander)
            .replace("@LIFECYCLE@", lifecycle).replace("@SHUTDOWN@", shutdown))


class WioTrackerL2GpsPowerTests(unittest.TestCase):
    def compile_run(self, mode, mutation=None, fail=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="wio-l2-gps-power-") as directory:
            work = Path(directory)
            files = {
                "Arduino.h": ARDUINO, "Wire.h": WIRE, "Mesh.h": MESH,
                "MicroNMEA.h": NMEA, "RTClib.h": RTC, "CayenneLPP.h": CAYENNE,
                "helpers/ESP32Board.h": ESP_BOARD,
            }
            provider = (VARIANT / "WioTrackerL2LocationProvider.h").read_text()
            if mutation == "never-power-off":
                provider = provider.replace("_board.setGnssPower(false);", "// negative control")
            # Copy the production adaptation unchanged except explicit negative
            # controls; quoted includes still resolve the real board header.
            files["WioTrackerL2LocationProvider.h"] = provider
            for name, contents in files.items():
                path = work / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
            source = work / "case.cpp"
            source.write_text(harness(mutation))
            binary = work / "case"
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                       "-Wno-unused-parameter", "-DENV_INCLUDE_GPS=1",
                       "-DESP32_PLATFORM=1", "-DENV_SKIP_GPS_DETECT=1",
                       "-DPIN_GPS_TX=18", "-DPIN_GPS_RX=17", "-DGPS_BAUD_RATE=9600",
                       "-I", str(work), "-I", str(VARIANT),
                       "-I", str(ROOT / "src"), str(source),
                       str(ROOT / "src/helpers/SensorManager.cpp"), "-o", str(binary)]
            if sys.platform.startswith("linux"):
                command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                               "-fno-pie", "-no-pie"]
            built = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary), mode], text=True, capture_output=True)
            if fail:
                self.assertNotEqual(ran.returncode, 0, "negative control did not fail")
            else:
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_boot_gnss_is_off_without_global_constructor_io(self):
        self.compile_run("boot")

    def test_begin_stop_are_idempotent_and_order_uart_before_rail(self):
        self.compile_run("cycles")

    def test_failed_expander_leaves_uart_released_and_provider_disabled(self):
        self.compile_run("failed")

    def test_actual_manager_saved_gps_off_and_scheduled_sleep_wake(self):
        self.compile_run("manager")

    def test_gps_tap_parser_and_actual_esp32_shutdown_are_preserved(self):
        self.compile_run("tapshutdown")

    def test_negative_controls_detect_boot_on_and_missing_rail_stop(self):
        self.compile_run("boot", "boot-rail-on", fail=True)
        self.compile_run("cycles", "never-power-off", fail=True)

    def test_target_uses_board_provider_without_fake_enable_gpio(self):
        target = (VARIANT / "target.cpp").read_text()
        self.assertIn("GpsTapStream gps_tap(Serial1);", target)
        self.assertIn("WioTrackerL2LocationProvider gps(board, gps_tap, &rtc_clock);", target)
        self.assertIn("EnvironmentSensorManager sensors(gps);", target)
        provider = (VARIANT / "WioTrackerL2LocationProvider.h").read_text()
        self.assertIn("MicroNMEALocationProvider(stream, clock, -1, -1)", provider)
        self.assertNotIn("getPinEn()", provider)


if __name__ == "__main__":
    unittest.main()
