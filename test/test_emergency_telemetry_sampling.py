"""Exercise the real emergency telemetry sampler with fake board sensors."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
PREFIX = r'''
#include <helpers/TelemetryHistory.h>
#include <cassert>
#include <cstdint>
#include <cmath>
#include <limits>
using std::isfinite;

struct Clock {
  uint32_t time = 1800000000;
  uint32_t getCurrentTime() const { return time; }
};
struct Board {
  unsigned temperatures = 0, batteries = 0;
  float temperature = 21;
  uint16_t voltage = 3800;
  float getMCUTemperature() { ++temperatures; return temperature; }
  uint16_t getBattMilliVolts() { ++batteries; return voltage; }
};
struct CLI {
  Board board;
  Board* getBoard() { return &board; }
};
struct LocationProvider {
  bool isEnabled() { return true; }
  bool isValid() { return true; }
  long getLatitude() { return 47000000; }
  long getLongitude() { return -122000000; }
};
struct SensorManager {
  struct VoltageSensorReading { uint8_t channel; float voltage; bool valid; };
  unsigned gpsQueries = 0, voltageQueries = 0;
  LocationProvider location;
  LocationProvider* getLocationProvider() { ++gpsQueries; return &location; }
  uint8_t queryVoltageSensors(VoltageSensorReading*, uint8_t) { ++voltageQueries; return 0; }
};
static SensorManager sensors;
namespace mesh {
struct ExternalVoltageHistory {
  static constexpr uint8_t MAX_CHANNELS = 4;
  struct Reading { uint8_t channel; float voltage; bool valid; };
  unsigned records = 0;
  void record(uint32_t, const Reading*, uint8_t) { ++records; }
};
}
struct MyMesh {
  Clock rtc_clock;
  Clock* getRTCClock() { return &rtc_clock; }
  CLI _cli;
  mesh::TelemetryHistory telemetry_history;
  mesh::ExternalVoltageHistory external_voltage_history;
  void sampleTelemetryHistory(bool emergency_only = false);
};
'''
MAIN = r'''
int main() {
  MyMesh mesh;
  constexpr uint32_t interval = mesh::TelemetryHistory::SAMPLE_INTERVAL_SECONDS;
  // Start just inside a UTC bucket, as when an emergency period begins.
  mesh.rtc_clock.time -= mesh.rtc_clock.time % interval;
  mesh.rtc_clock.time += 5;
  mesh.sampleTelemetryHistory(true);
  assert(mesh._cli.board.temperatures == 1 && mesh._cli.board.batteries == 1);
  assert(mesh.telemetry_history.voltageSampleCount() == 1);
  assert(sensors.gpsQueries == 0 && sensors.voltageQueries == 0);
  assert(mesh.external_voltage_history.records == 0);

  // Ten-second maintenance returns do not generate extra measurements.
  for (int i = 0; i < 100; ++i) {
    mesh.rtc_clock.time += 10;
    mesh.sampleTelemetryHistory(true);
  }
  assert(mesh._cli.board.temperatures == 1 && mesh._cli.board.batteries == 1);
  assert(mesh.telemetry_history.voltageSampleCount() == 1);
  mesh.rtc_clock.time += 800;
  mesh.sampleTelemetryHistory(true);
  assert(mesh._cli.board.temperatures == 2 && mesh._cli.board.batteries == 2);
  assert(mesh.telemetry_history.voltageSampleCount() == 2);
  assert(sensors.gpsQueries == 0 && sensors.voltageQueries == 0);
  assert(mesh.external_voltage_history.records == 0);
  const uint8_t source[8] = {};
  uint8_t snapshot[32] = {};
  assert(mesh.telemetry_history.formatTemperatureBinarySnapshot(source, snapshot, sizeof(snapshot)) == 21);
  assert(snapshot[19] == 74 && snapshot[20] == 74); // 21 C in the existing wire encoding.
  assert(mesh.telemetry_history.formatVoltageBinarySnapshot(source, snapshot, sizeof(snapshot)) == 21);
  assert(snapshot[19] == 194 && snapshot[20] == 194); // 3.80 V.

  // On returning to normal work the existing per-bucket guard still prevents
  // duplicate samples. The next bucket restores ordinary sensor queries.
  mesh.sampleTelemetryHistory(false);
  assert(mesh._cli.board.temperatures == 2 && sensors.voltageQueries == 0);
  mesh.rtc_clock.time += interval;
  mesh.sampleTelemetryHistory(false);
  assert(mesh._cli.board.temperatures == 3 && mesh._cli.board.batteries == 3);
  assert(mesh.telemetry_history.voltageSampleCount() == 3);
  assert(sensors.gpsQueries == 1 && sensors.voltageQueries == 1);
  assert(mesh.external_voltage_history.records == 1);

  // Unsupported/missing board readings remain missing, not invented values.
  mesh.rtc_clock.time += interval;
  mesh._cli.board.temperature = std::numeric_limits<float>::quiet_NaN();
  mesh._cli.board.voltage = 0;
  mesh.sampleTelemetryHistory(true);
  assert(mesh.telemetry_history.formatTemperatureBinarySnapshot(source, snapshot, sizeof(snapshot)) == 23);
  assert(snapshot[22] == 0);
  assert(mesh.telemetry_history.formatVoltageBinarySnapshot(source, snapshot, sizeof(snapshot)) == 23);
  assert(snapshot[22] == 0);
}
'''

GATE_PREFIX = r'''
#include <helpers/EmergencyPowerRuntime.h>
#include <cassert>
using namespace mesh::wireless;
void delay(uint32_t) {}
struct BackendStub : Backend {
  uint8_t active = All;
  uint8_t available() const override { return All; }
  uint8_t enabled() const override { return active; }
  uint8_t clients() const override { return Independent; }
  Result set(uint8_t bit, bool on) override {
    if (on) active |= bit; else active &= ~bit;
    return Result::Done;
  }
};
struct CLI {
  uint32_t budget = 1800000;
  bool active = true, radioSleeping = false;
  uint32_t emergencySleepMilliseconds() const { return budget; }
  bool emergencyActive() const { return active; }
  void setEmergencyRadioSleeping(bool value) { radioSleeping = value; }
};
struct Mesh {
  bool available = true, outbound = false;
  int samples = 0, normal = 0;
  CLI cli;
  bool hasOutbound() const { return outbound; }
  bool emergencyPowerBusy() const { return false; }
  CLI& getPowerCLI() { return cli; }
  void sampleEmergencyTelemetry() { assert(!available && cli.radioSleeping); ++samples; }
protected:
  friend class mesh::power::EmergencyPowerRuntime;
  bool isRadioAvailable() const { return available; }
  void setRadioAvailable(bool value) { available = value; }
};
struct Radio {
  bool sleeping = false;
  bool isMorseTxActive() const { return false; }
  bool isMorseRxActive() const { return false; }
  bool setTrackerSleep(bool value) { sleeping = value; return true; }
  bool recoverRadio(bool) { return true; }
};
struct Board {
  int sleeps = 0;
  bool supportsEmergencySleep() const { return true; }
  bool isOTAUpdateRunning() const { return false; }
  bool isRadioTestActive() const { return false; }
  void setEmergencyRadioSleeping(bool) {}
  bool emergencySleepMilliseconds(uint32_t budget) { assert(budget <= 10000); ++sleeps; return true; }
};
struct Fixture {
  BackendStub wireless;
  Mesh the_mesh;
  Radio radio_driver;
  Board board;
  mesh::power::EmergencyPowerRuntime emergency_power;
  bool emergency_busy = false;
  Fixture() { control().begin(wireless); }
  void loop();
};
'''
GATE_MAIN = r'''
int main() {
  {
    Fixture f;
    f.loop();
    assert(f.board.sleeps == 1 && f.radio_driver.sleeping && !f.the_mesh.available);
    assert(f.the_mesh.samples == 1 && f.the_mesh.normal == 0 && f.wireless.active == 0);
    f.loop();
    assert(f.board.sleeps == 2 && f.the_mesh.samples == 2);
    f.the_mesh.cli.budget = 0;
    f.loop();
    assert(f.the_mesh.available && !f.radio_driver.sleeping && f.the_mesh.normal == 1);
    assert(f.the_mesh.samples == 2 && f.wireless.active == All);
  }
  {
    // A standalone schedule sleeps but must not take emergency samples.
    Fixture f;
    f.the_mesh.cli.active = false;
    f.loop();
    assert(f.board.sleeps == 1 && !f.the_mesh.available && f.radio_driver.sleeping);
    assert(f.the_mesh.samples == 0 && f.the_mesh.normal == 0);
  }
  {
    // Live RF work prevents entry and proceeds through the normal loop.
    Fixture f;
    f.the_mesh.outbound = true;
    f.loop();
    assert(f.board.sleeps == 0 && f.the_mesh.available && !f.radio_driver.sleeping);
    assert(f.the_mesh.samples == 0 && f.the_mesh.normal == 1);
  }
}
'''


class EmergencyTelemetrySamplingTests(unittest.TestCase):
    def test_real_role_samplers_record_once_per_bucket_without_optional_sensors(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as folder:
                work = Path(folder)
                source = (ROOT / "examples" / role / "MyMesh.cpp").read_text(encoding="utf-8")
                actual = extract_braced(source, "void MyMesh::sampleTelemetryHistory(")
                (work / "main.cpp").write_text(PREFIX + actual + MAIN, encoding="ascii")
                flags = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                    "-fno-sanitize-recover=all", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
                exe = work / "check"
                build = subprocess.run([compiler, "-std=c++17", "-O1", "-Wall", "-Wextra",
                    "-Werror", "-DENV_INCLUDE_GPS=1", *flags, "-I", str(ROOT / "src"),
                    str(work / "main.cpp"), "-o", str(exe)], capture_output=True, text=True)
                self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                result = subprocess.run([str(exe)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_role_loop_gate_keeps_radio_asleep_and_respects_schedule(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as folder:
                work = Path(folder)
                source = (ROOT / "examples" / role / "main.cpp").read_text(encoding="utf-8")
                gate = extract_braced(source, "if (emergency_power.service(")
                code = GATE_PREFIX + "\nvoid Fixture::loop() {\n" + gate + "\n++the_mesh.normal;\n}\n" + GATE_MAIN
                (work / "Arduino.h").write_text("#pragma once\n#include <stdint.h>\nvoid delay(uint32_t);\n")
                (work / "main.cpp").write_text(code, encoding="ascii")
                flags = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                    "-fno-sanitize-recover=all", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
                exe = work / "check"
                build = subprocess.run([compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                    "-DMESH_ENABLE_EMERGENCY_POWER=1", *flags, "-I", str(work), "-I", str(ROOT / "src"),
                    str(work / "main.cpp"), "-o", str(exe)], capture_output=True, text=True)
                self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                result = subprocess.run([str(exe)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
