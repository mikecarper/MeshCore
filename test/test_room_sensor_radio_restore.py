"""Fault-inject actual Room/Sensor saved-radio startup, retry and TempRadio paths."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
ROLES = {
    "room": ("examples/simple_room_server/MyMesh.cpp", "MyMesh"),
    "sensor": ("examples/simple_sensor/SensorMesh.cpp", "SensorMesh"),
}


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
// Negative controls must fail without opening a Windows CRT abort dialog.
#undef assert
#define assert(condition) do { if (!(condition)) { \
  std::fprintf(stderr, "fixture assertion line %u: %s\n", unsigned(__LINE__), #condition); \
  std::exit(91); } } while (false)
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define MESH_ENABLE_TX_BEACONS 0
#define MESH_ENABLE_CARRIER_TOOLS 0
#define MESH_ENABLE_MORSE_RX 0
#define RADIOLIB_ERR_NONE 0
namespace mesh { enum class RadioParamApplyResult { APPLIED, BUSY, FAILED }; }
using Apply = mesh::RadioParamApplyResult;
static uint32_t now_ms = 100;
template<typename T> T next(std::deque<T>& values, T fallback) {
  if (values.empty()) return fallback;
  T value = values.front(); values.pop_front(); return value;
}
struct Chip { int standby() { return 0; } } chip;
struct Driver {
  enum { STATE_IDLE, STATE_RX, STATE_TX_WAIT };
  uint8_t state = STATE_IDLE;
  bool receiving = false, pending_irq = false, busy = false, supported = true;
  bool _rx_ps_armed = false, _cw_active = false;
  bool _dbm_valid = true, _rx_boosted_gain_valid = false;
  int8_t _cur_dbm = 7;
  bool _cur_rx_boosted_gain = false;
  unsigned power_commands = 0, gain_commands = 0;
  std::deque<int16_t> power_results;
  std::deque<bool> gain_results;
  Chip* _radio = &chip;
  bool hasPendingRadioInterrupt() { return pending_irq; }
  bool isChipBusy() { return busy; }
  bool isReceivingPacket() { return receiving; }
  void stopReceiveDutyCycle() { _rx_ps_armed = false; }
  void startRecv() { state = STATE_RX; }
  void recalibrateNoiseFloor() {}
  int16_t applyCachedTxPower(int8_t) {
    ++power_commands; return next(power_results, int16_t(0));
  }
#if ROOM_ROLE
  bool supportsRxBoostedGainMode() { return supported; }
  bool applyRxBoostedGainMode(bool) {
    assert(supported); ++gain_commands; return next(gain_results, true);
  }
  bool setRxBoostedGainMode(bool);
#endif
  bool supportsRxPowerSaving() { return true; }
  uint8_t beginReconfigure();
  void endReconfigure(bool);
  bool setTxPower(int8_t);
} radio_driver;
@WRAPPER_METHODS@
struct Prefs {
  float freq = 915.125f, bw = 250;
  uint8_t sf = 9, cr = 6;
  int8_t tx_power_dbm = 17;
  bool rx_boosted_gain = true, rx_powersaving_enabled = false;
  uint8_t rx_ps_level = 0, rx_ps_preamble = 32;
  uint32_t rx_ps_rx_us = 1234, rx_ps_sleep_us = 5678;
};
struct Profiles {
  float live_freq = 906.5f, live_bw = 125;
  uint8_t live_sf = 7, live_cr = 5;
  unsigned calls = 0;
  bool receive_after_tuple = false, had_timings = false;
  uint32_t rx_us = 0, sleep_us = 0;
  std::deque<Apply> results;
  uint16_t primaryPreamble() { return 32; }
  Apply applyPrimary(float freq, float bw, uint8_t sf, uint8_t cr,
                     bool, uint16_t, const uint32_t* timings) {
    ++calls;
    const Apply result = next(results, Apply::APPLIED);
    if (result != Apply::APPLIED) return result;
    const uint8_t resume = radio_driver.beginReconfigure();
    if (resume > 1) return Apply::BUSY;
    live_freq = freq; live_bw = bw; live_sf = sf; live_cr = cr;
    had_timings = timings != nullptr;
    if (timings) { rx_us = timings[0]; sleep_us = timings[1]; }
    radio_driver.endReconfigure(resume);
    // A packet can start after tuple application resumes an existing receiver.
    if (receive_after_tuple && radio_driver.state == Driver::STATE_RX)
      radio_driver.receiving = true;
    return Apply::APPLIED;
  }
};
struct CommonCLI {
  Profiles profiles;
  Profiles& radioProfiles() { return profiles; }
  static bool calculateRxPowerSavingLevel(uint8_t, uint8_t, float,
      uint32_t, uint32_t* rx, uint32_t* sleep) { *rx = 2222; *sleep = 3333; return true; }
};
@RETRY_DELAY@
struct @CLASS@ {
  Prefs _prefs;
  CommonCLI _cli;
  uint8_t active_cr = 5;
  bool saved_radio_apply_pending = false, temp_radio_applied = false, outbound = false;
  uint32_t set_radio_at = 0, revert_radio_at = 0, radio_apply_retry_at = 0;
  uint8_t radio_apply_failures = 0;
  float pending_freq = 916.5f, pending_bw = 125;
  uint8_t pending_sf = 8, pending_cr = 7, pending_preamble = 16;
  @CLASS@() { now_ms = 100; radio_driver = Driver(); }
  bool hasOutbound() { return outbound; }
  bool millisHasNowPassed(uint32_t at) { return int32_t(now_ms - at) > 0; }
  uint32_t futureMillis(uint32_t delay) { return now_ms + delay; }
  uint32_t rxPowerSavingPreambleForParams(uint8_t, float) { return 32; }
  bool applySavedRadioParams();
  void startup() { @STARTUP@ }
  void service() { @SERVICE@ }
};
@APPLY@
using Node = @CLASS@;
static void restored(const Node& node) {
  assert(node._cli.profiles.live_freq == node._prefs.freq);
  assert(node._cli.profiles.live_bw == node._prefs.bw);
  assert(node._cli.profiles.live_sf == node._prefs.sf);
  assert(node.active_cr == node._prefs.cr);
  assert(radio_driver._cur_dbm == node._prefs.tx_power_dbm);
#if ROOM_ROLE
  if (radio_driver.supported)
    assert(radio_driver._cur_rx_boosted_gain == node._prefs.rx_boosted_gain);
#endif
}
static unsigned calls(const Node& node) {
  return node._cli.profiles.calls + radio_driver.power_commands + radio_driver.gain_commands;
}
static void startupAndRetry() {
  for (unsigned fault = 0; fault < 3; ++fault) {
#if !ROOM_ROLE
    if (fault == 0) continue; // Sensor has no RX-gain feature.
#endif
    Node node;
    node._prefs.rx_powersaving_enabled = true;
#if ROOM_ROLE
    if (fault == 0) radio_driver.gain_results = {false, false, true};
#endif
    if (fault == 1) node._cli.profiles.results = {Apply::BUSY, Apply::FAILED, Apply::APPLIED};
    if (fault == 2) radio_driver.power_results = {-1, -1, 0};
    node.startup();
    assert(node.saved_radio_apply_pending && "startup must retain incomplete saved radio work");
    node.service();
    assert(node.saved_radio_apply_pending && node.radio_apply_failures == 1);
    assert(node.radio_apply_retry_at > now_ms);
    const unsigned before = calls(node);
    node.service();
    assert(calls(node) == before); // No retry before the existing backoff.
    now_ms = node.radio_apply_retry_at + 1;
    node.outbound = true; node.service();
    assert(calls(node) == before && node.saved_radio_apply_pending);
    node.outbound = false; node.service();
    assert(!node.saved_radio_apply_pending && !node.radio_apply_retry_at && !node.radio_apply_failures);
    restored(node);
    assert(node._cli.profiles.had_timings);
    assert(node._cli.profiles.rx_us == 1234 && node._cli.profiles.sleep_us == 5678);
  }
  {
    Node node; node.startup(); restored(node);
    assert(!node.saved_radio_apply_pending && radio_driver.power_commands == 1);
#if ROOM_ROLE
    assert(radio_driver.gain_commands == 1); // No unchecked caller duplicates.
#endif
  }
#if ROOM_ROLE
  for (bool desired : {false, true}) {
    Node node; node._prefs.rx_boosted_gain = desired;
    radio_driver._cur_rx_boosted_gain = !desired;
    node.startup(); restored(node);
    assert(!node.saved_radio_apply_pending);
  }
  {
    Node node; radio_driver.supported = false; radio_driver.gain_results = {false};
    node.startup(); restored(node);
    assert(!node.saved_radio_apply_pending && !radio_driver.gain_commands);
  }
#endif
}
static void receiveBetweenTupleAndPower() {
  Node node;
  radio_driver.state = Driver::STATE_RX;
  node.saved_radio_apply_pending = true;
  node._cli.profiles.receive_after_tuple = true;
  node.service();
  assert(node.saved_radio_apply_pending && "RX after tuple must defer saved TX power restoration");
  assert(radio_driver.receiving && radio_driver._cur_dbm == 7 && !radio_driver.power_commands);
  assert(node.radio_apply_failures == 1 && node.radio_apply_retry_at > now_ms);
  const unsigned before = calls(node);
  node.service(); assert(calls(node) == before);
  radio_driver.receiving = false; node._cli.profiles.receive_after_tuple = false;
  now_ms = node.radio_apply_retry_at + 1; node.service();
  assert(!node.saved_radio_apply_pending && !node.radio_apply_failures);
  restored(node);
}
static void tempRadioRestoration() {
  for (bool pending : {false, true}) for (unsigned fault = 0; fault < 3; ++fault) {
#if !ROOM_ROLE
    if (fault == 0) continue;
#endif
    Node node; node.saved_radio_apply_pending = pending;
    node.temp_radio_applied = true; node.revert_radio_at = now_ms - 1;
#if ROOM_ROLE
    if (fault == 0) radio_driver.gain_results = {false, true};
#endif
    if (fault == 1) node._cli.profiles.results = {Apply::BUSY, Apply::APPLIED};
    if (fault == 2) radio_driver.power_results = {-1, 0};
    node.service();
    assert(node.temp_radio_applied && node.revert_radio_at && "failed TempRadio restoration must retain its lease");
    assert(node.saved_radio_apply_pending == pending);
    assert(node.radio_apply_retry_at > now_ms && node.radio_apply_failures == 1);
    const unsigned before = calls(node);
    node.service(); assert(calls(node) == before);
    now_ms = node.radio_apply_retry_at + 1;
    node.outbound = true; node.service(); assert(calls(node) == before);
    node.outbound = false; node.service();
    assert(!node.temp_radio_applied && !node.revert_radio_at && !node.set_radio_at);
    assert(!node.saved_radio_apply_pending && !node.radio_apply_retry_at && !node.radio_apply_failures);
    restored(node);
  }
  {
    Node node; node.saved_radio_apply_pending = true;
    node.temp_radio_applied = true; node.revert_radio_at = now_ms + 10000;
    node.service(); assert(!calls(node) && node.saved_radio_apply_pending);
    // Do not apply an expired temporary tuple that never acquired its lease.
    node.saved_radio_apply_pending = false; node.temp_radio_applied = false;
    node.set_radio_at = now_ms - 2; node.revert_radio_at = now_ms - 1;
    node.service(); assert(!calls(node) && !node.set_radio_at && !node.revert_radio_at);
  }
}
int main() {
  startupAndRetry(); receiveBetweenTupleAndPower(); tempRadioRestoration();
  std::puts("PASS: production saved radio startup/retry/TempRadio restoration");
}
'''


def harness(role, mutation=None):
    path, name = ROLES[role]
    source = (ROOT / path).read_text()
    apply = body(source, f"bool {name}::applySavedRadioParams()")
    if mutation == "unchecked-power":
        checked = "return radio_driver.setTxPower(_prefs.tx_power_dbm);"
        assert checked in apply
        apply = apply.replace(checked, "radio_driver.setTxPower(_prefs.tx_power_dbm); return true;")
    elif mutation == "unchecked-gain":
        checked = "&& !radio_driver.setRxBoostedGainMode(_prefs.rx_boosted_gain)) return false;"
        assert checked in apply
        apply = apply.replace(checked, ") (void)radio_driver.setRxBoostedGainMode(_prefs.rx_boosted_gain);")
    begin = body(source, f"void {name}::begin(")
    start = begin.index("  saved_radio_apply_pending = !applySavedRadioParams();")
    startup = begin[start:begin.index("  const bool fem_gain_changed", start)]
    start = source.index("  const bool revert_radio_due = revert_radio_at")
    failed = source.index("  if (radio_apply_failed) {", start)
    service = source[start:failed] + body(source[failed:], "  if (radio_apply_failed) {")
    wrapper = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
    signatures = ["uint8_t RadioLibWrapper::beginReconfigure()",
                  "void RadioLibWrapper::endReconfigure(bool resume_rx)",
                  "bool RadioLibWrapper::setTxPower(int8_t dbm)"]
    if role == "room":
        signatures.append("bool RadioLibWrapper::setRxBoostedGainMode(bool enabled)")
    methods = "\n".join(body(wrapper, signature) for signature in signatures)
    methods = methods.replace("RadioLibWrapper::", "Driver::")
    values = {
        "@CLASS@": name, "@APPLY@": apply, "@STARTUP@": startup,
        "@SERVICE@": service, "@WRAPPER_METHODS@": methods,
        "@RETRY_DELAY@": body(source, "static uint32_t nextRadioApplyRetryDelay("),
    }
    result = HARNESS
    for marker, value in values.items():
        result = result.replace(marker, value)
    return result


class RoomSensorRadioRestoreTests(unittest.TestCase):
    def run_case(self, role, mutation=None):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="room-sensor-radio-restore-") as folder:
            cpp = Path(folder) / "test.cpp"
            binary = Path(folder) / ("test.exe" if os.name == "nt" else "test")
            cpp.write_text(harness(role, mutation))
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-pie", "-no-pie"]
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    f"-DROOM_ROLE={int(role == 'room')}", *flags,
                                    str(cpp), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=45)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            if mutation:
                self.assertNotEqual(ran.returncode, 0, "unchecked setter negative control escaped")
                self.assertIn("startup must retain incomplete saved radio work", ran.stderr)
            else:
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_production_room_startup_retry_and_temp_restore(self):
        self.run_case("room")

    def test_production_sensor_startup_retry_and_temp_restore(self):
        self.run_case("sensor")

    def test_old_unchecked_power_cannot_complete_either_role(self):
        for role in ROLES:
            with self.subTest(role=role):
                self.run_case(role, "unchecked-power")

    def test_old_unchecked_room_gain_cannot_complete(self):
        self.run_case("room", "unchecked-gain")


if __name__ == "__main__":
    unittest.main()
