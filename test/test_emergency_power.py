"""Exercise the emergency scheduler and actual CommonCLI boundary without hardware."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
POLICY_PREFIX = r'''
#include <helpers/EmergencyPowerPolicy.h>
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <vector>
using namespace mesh::emergencypower;
static constexpr uint32_t epoch = PlausibleEpoch;
'''


class EmergencyPowerTests(unittest.TestCase):
    def exercise(self, body, prefix=POLICY_PREFIX, flags=()):
        with tempfile.TemporaryDirectory(prefix="emergency-power-") as folder:
            work = Path(folder)
            source, binary = work / "main.cpp", work / "check"
            source.write_text(prefix + "\nint main() {\n" + body + "\n}\n", encoding="ascii")
            command = [os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-g",
                       "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                       "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-fno-omit-frame-pointer", "-fno-pie", "-no-pie", *flags,
                       "-I", str(ROOT / "src"), str(source), "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_low_boundary_hysteresis_invalid_readings_and_reply_hold(self):
        self.exercise(r'''
  for (unsigned pct = 0; pct <= 100; ++pct) {
    State state;
    state.begin(Settings{}, 100);
    assert(!state.active() && state.due(100));
    state.sample(100, 3000, uint8_t(pct), true);
    assert(state.active() == (pct <= 5));
    assert(!state.due(30099) && state.due(30100));
  }
  State state;
  state.begin(Settings{}, 0);
  state.sample(0, 3060, 5, true);
  assert(state.active() && state.holdRemaining(0) == 5000);
  assert(state.sleepMilliseconds(4999, epoch + 1800) == 0);
  assert(state.sleepMilliseconds(5000, epoch + 1800) == 1800000);
  state.sample(5001, 3072, 6, true);
  assert(state.active() && state.sleepMilliseconds(5001, epoch + 1800) == 1800000);
  for (uint16_t mv : {uint16_t(0), uint16_t(1999), uint16_t(20001), uint16_t(65535)}) {
    state.sample(5002, mv, 100, true);
    assert(!state.valid && state.active());
  }
  state.sample(5003, 4200, 255, true);
  assert(!state.valid && state.active());
  state.sample(5004, 4200, 100, false);
  assert(!state.valid && state.active());
  state.sample(5005, 3084, 7, true);
  assert(!state.active() && state.sleepMilliseconds(5005, epoch + 1800) == 0);
  state.sample(5006, 0, 0, true);
  assert(!state.active());
  state.sample(5007, 3000, 0, false);
  assert(!state.active());
  state.sample(5008, 3060, 5, true);
  assert(state.active() && state.holdRemaining(5008) == 5000);
  state.sample(5009, 3060, 5, true);
  assert(state.holdRemaining(5009) == 4999); // Sampling never renews an existing activation.
''')

    def test_hour_boundaries_clock_corrections_and_boot_relative_wrap(self):
        self.exercise(r'''
  Settings manual; manual.manual = true;
  State state;
  state.begin(manual, 0);
  assert(state.sleepMilliseconds(5000, epoch + 899) == 0);
  assert(state.sleepMilliseconds(5001, epoch + 900) == 2700000);
  assert(state.sleepMilliseconds(5002, epoch + 3599) == 1000);
  assert(state.sleepMilliseconds(5003, epoch + 3600) == 0);
  assert(state.sleepMilliseconds(5004, epoch + 7200 + 1800) == 1800000);
  assert(state.sleepMilliseconds(5005, epoch + 600) == 0); // RTC rollback selects the real hour.
  assert(state.sleepMilliseconds(900000, 0) == 2700000);
  assert(state.sleepMilliseconds(3599999, PlausibleEpoch - 1) == 1);
  assert(state.sleepMilliseconds(3600000, 0) == 0);
  assert(State::usesRtc(epoch) && !State::usesRtc(epoch - 1));

  const uint32_t boot = 0xfffff000U;
  state.begin(manual, boot);
  uint32_t now = boot;
  uint32_t expected = boot % HourMs;
  for (uint32_t delta : {uint32_t(5000), uint32_t(899999), uint32_t(3600000),
                          uint32_t(0xf0000000), uint32_t(0x10000100)}) {
    now += delta;
    expected = (expected + delta % HourMs) % HourMs;
    const uint32_t want = expected < AwakeWindowMs ? 0 : HourMs - expected;
    assert(state.sleepMilliseconds(now, 0) == want);
    assert(state.phaseMs == expected);
  }
''')

    def test_manual_transitions_awake_lease_renew_cancel_limits_and_wrap(self):
        self.exercise(r'''
  State state;
  state.begin(Settings{}, 0);
  state.setManual(true, 100);
  assert(state.active() && state.holdRemaining(100) == 5000);
  state.setManual(true, 101);
  assert(state.holdRemaining(101) == 4999);
  assert(state.sleepMilliseconds(5100, epoch + 1800) == 1800000);
  assert(state.stayAwake(1, 5101));
  assert(state.sleepMilliseconds(65100, epoch + 3599) == 0);
  assert(state.sleepMilliseconds(65101, epoch + 1800) == 1800000);
  assert(!state.awakeDuration);
  assert(state.sleepMilliseconds(5101, epoch + 1800) == 1800000); // Expired lease stays expired.
  assert(state.stayAwake(10080, 0xfffffff0U));
  assert(state.holdRemaining(0xfffffff0U) == 604800000);
  assert(!state.stayAwake(10081, 100) && state.awakeDuration == 604800000);
  assert(state.sleepMilliseconds(uint32_t(0xfffffff0U + 604799999U), epoch + 1800) == 0);
  assert(state.sleepMilliseconds(uint32_t(0xfffffff0U + 604800000U), epoch + 1800) == 1800000);
  assert(state.stayAwake(2, 200));
  assert(state.stayAwake(1, 300));
  assert(state.holdRemaining(400) == 59900);
  assert(state.stayAwake(0, 400) && state.holdRemaining(400) == 0);
  state.sample(401, 3060, 5, true);
  state.setManual(false, 402); // Auto still protects the low battery, with time to send this reply.
  assert(state.active() && state.holdRemaining(402) == 5000);
  state.sample(403, 3084, 7, true);
  assert(!state.active());
  state.setManual(true, 404);
  state.sample(405, 4200, 100, true);
  assert(state.active());
  state.setManual(false, 406);
  assert(!state.active());
''')

    def test_checked_record_roundtrip_all_bit_flips_lengths_and_semantics(self):
        self.exercise(r'''
  for (bool manual : {false, true}) {
    Settings expected; expected.manual = manual;
    uint8_t bytes[RecordSize]; encode(bytes, expected);
    Settings actual;
    assert(decode(bytes, sizeof(bytes), actual) && actual.manual == manual);
    for (size_t n = 0; n < RecordSize; ++n) {
      for (unsigned bit = 0; bit < 8; ++bit) {
        uint8_t corrupted[RecordSize]; memcpy(corrupted, bytes, sizeof(bytes));
        corrupted[n] ^= uint8_t(1U << bit);
        actual.manual = true;
        assert(!decode(corrupted, sizeof(corrupted), actual) && !actual.manual);
      }
      assert(!decode(bytes, n, actual) && !actual.manual);
    }
    std::vector<uint8_t> oversized(bytes, bytes + RecordSize); oversized.push_back(0);
    assert(!decode(oversized.data(), oversized.size(), actual));
    assert(!decode(nullptr, RecordSize, actual));
    for (size_t n : {size_t(3), size_t(4), size_t(5)}) {
      uint8_t invalid[RecordSize]; memcpy(invalid, bytes, sizeof(bytes)); invalid[n] = 2;
      const uint16_t crc = checksum(invalid, 6);
      invalid[6] = uint8_t(crc); invalid[7] = uint8_t(crc >> 8);
      assert(!decode(invalid, sizeof(invalid), actual) && !actual.manual);
    }
  }
''')

    def runtime_prefix(self):
        header = (ROOT / "src/helpers/CommonCLI.h").read_text(encoding="ascii")
        def api_block(marker):
            start = header.index(marker)
            return header[start:header.index("#endif", start) + len("#endif")]
        private = api_block("#if MESH_ENABLE_EMERGENCY_POWER\n  FILESYSTEM* _emergency_power_fs")
        private += "\n" + api_block("#if MESH_ENABLE_EMERGENCY_POWER\n  bool _emergency_radio_sleeping")
        public = api_block("#if MESH_ENABLE_EMERGENCY_POWER\n  uint32_t emergencySleepMilliseconds")
        implementation = (ROOT / "src/helpers/CommonCLI_Emergency.cpp").read_text(encoding="ascii")
        implementation = re.sub(r"^#include[^\n]*\n", "", implementation, flags=re.MULTILINE)
        battery = (ROOT / "src/helpers/CommonCLI_Battery.cpp").read_text(encoding="ascii")
        battery_methods = "\n".join(extract_braced(battery, signature) for signature in (
            "uint16_t CommonCLI::batteryEmptyMv(", "uint16_t CommonCLI::batteryFullMv(",
            "uint8_t CommonCLI::batteryPercent("))
        checked = (ROOT / "src/helpers/CheckedSettingsFile.h").read_text(encoding="ascii")
        checked_write = extract_braced(checked, "MESH_SETTINGS_FILE_SHARED inline AtomicFileWriter::Outcome writeCheckedSettingsFile(")
        prefix = POLICY_PREFIX + r'''
#include <helpers/BatteryForwarding.h>
#include <helpers/OperatingSchedule.h>
#include <helpers/FleetLocation.h>
#include <cstdio>
#include <map>
#include <string>
static uint32_t nowMs;
static uint32_t millis() { return nowMs; }
struct FILESYSTEM {
  std::map<std::string, std::vector<uint8_t>> files;
  bool shortWrite = false, failCommit = false, badRead = false, corruptCommit = false;
  bool recoverOk = true;
  std::string blockedPath;
  unsigned commits = 0, writes = 0;
};
namespace mesh {
class AtomicFileWriter {
  FILESYSTEM* fs;
  std::string path;
  std::vector<uint8_t> bytes;
public:
  enum class Outcome { NotCommitted, Committed, Indeterminate };
  AtomicFileWriter(FILESYSTEM* f, const char* p) : fs(f), path(p) {}
  explicit operator bool() const { return fs != nullptr; }
  size_t write(const uint8_t* p, size_t n) {
    ++fs->writes;
    const size_t written = fs->shortWrite ? n - 1 : n;
    bytes.assign(p, p + written); return written;
  }
  bool commit() {
    ++fs->commits;
    if (fs->failCommit) return false;
    fs->files[path] = bytes;
    if (fs->corruptCommit) fs->files[path][0] ^= 1;
    return true;
  }
  Outcome outcome() const { return Outcome::NotCommitted; }
};
class ContactFileTransaction : public AtomicFileWriter {
public:
  using AtomicFileWriter::AtomicFileWriter;
  static bool recover(FILESYSTEM* fs, const char* path) {
    return fs && fs->recoverOk && fs->blockedPath != path;
  }
};
namespace storage {
static bool readFixedSettingsFile(FILESYSTEM* fs, const char* path, uint8_t* bytes, size_t n) {
  if (!fs || fs->badRead) return false;
  auto found = fs->files.find(path);
  if (found == fs->files.end() || found->second.size() != n) return false;
  memcpy(bytes, found->second.data(), n); return true;
}
#define MESH_SETTINGS_FILE_SHARED
''' + checked_write + r'''
#undef MESH_SETTINGS_FILE_SHARED
}}
struct Board {
  uint16_t mv = 4200;
  unsigned reads = 0;
  bool emergencySupported = true;
  uint16_t getBattMilliVolts() { ++reads; return mv; }
  bool supportsEmergencySleep() const { return emergencySupported; }
};
struct Clock {
  uint32_t time = epoch + 1800;
  uint32_t getCurrentTime() { return time; }
};
struct Prefs {
  double node_lat = 0, node_lon = 0;
};
struct Sensors {
  bool cached = false;
  double latitude = 0, longitude = 0;
  mutable unsigned reads = 0;
  bool getCachedGpsPosition(double& lat, double& lon) const {
    ++reads;
    lat = latitude; lon = longitude;
    return cached;
  }
};
static Prefs defaultPrefs;
static Sensors defaultSensors;
class CommonCLI {
public:
  Board* _board;
  Clock* _rtc;
  Prefs* _prefs;
  Sensors* _sensors;
  mesh::batteryforward::Settings _battery_forward_settings;
''' + private + "\n" + public + r'''
  uint16_t batteryEmptyMv() const;
  uint16_t batteryFullMv() const;
  uint8_t batteryPercent(uint16_t) const;
  CommonCLI(Board& board, Clock& rtc, Prefs& prefs = defaultPrefs,
            Sensors& sensors = defaultSensors)
      : _board(&board), _rtc(&rtc), _prefs(&prefs), _sensors(&sensors) {}
};
''' + battery_methods + "\n" + implementation
        return prefix

    def test_actual_cli_sampling_independent_status_and_runtime_lease(self):
        self.exercise(r'''
  Board board; Clock clock; FILESYSTEM fs;
  CommonCLI cli(board, clock);
  nowMs = 0; board.mv = 3060;
  cli.loadEmergencyPower(&fs);
  assert(cli._battery_forward_settings.enabled == 0);
  assert(cli.emergencySleepMilliseconds() == 0 && cli.emergencyActive() && board.reads == 1);
  nowMs = 5000;
  assert(cli.emergencySleepMilliseconds() == 1800000 && board.reads == 1);
  char reply[160] = {};
  cli.setEmergencyRadioSleeping(false);
  assert(cli.handleEmergencyPowerCommand("get power.emergency", reply));
  assert(strstr(reply, "mode=auto active=on battery=5% clock=rtc window=sleep radio=awake"));
  assert(strstr(reply, "wake=1800s hold=0s"));
  cli.setEmergencyRadioSleeping(true);
  assert(cli.handleEmergencyPowerCommand("get power.emergency \t", reply));
  assert(strstr(reply, "radio=sleep"));
  nowMs = 5001;
  assert(cli.handleEmergencyPowerCommand("get power.emergency", reply));
  assert(cli._emergency_power.holdRemaining(nowMs) == 0); // Polls do not postpone sleep.
  assert(cli.handleEmergencyPowerCommand("set power.awake 1 \t", reply));
  assert(!strcmp(reply, "OK - awake for 1 minutes") && fs.writes == 0);
  assert(cli.emergencySleepMilliseconds() == 0);
  nowMs = 65000;
  assert(cli.emergencySleepMilliseconds() == 0);
  nowMs = 65001;
  assert(cli.emergencySleepMilliseconds() == 1800000);
  board.mv = 0; nowMs = 95001;
  assert(cli.emergencySleepMilliseconds() == 1800000 && cli.emergencyActive());
  clock.time = 0;
  assert(cli.handleEmergencyPowerCommand("get power.emergency", reply));
  assert(strstr(reply, "battery=unknown clock=uptime"));
  board.mv = 3084; nowMs += 30000;
  assert(cli.emergencySleepMilliseconds() == 0 && !cli.emergencyActive());
  cli._battery_forward_settings.emptyMv = 6000;
  cli._battery_forward_settings.fullMv = 8400;
  board.mv = 6120; nowMs += 30000;
  assert(cli.emergencySleepMilliseconds() == 0 && cli.emergencyActive());
  assert(cli._emergency_power.percentage == 5);
  assert(cli.handleEmergencyPowerCommand("set power.awake 10080", reply));
  assert(cli._emergency_power.awakeDuration == 604800000);
  assert(cli.handleEmergencyPowerCommand("set power.awake 0", reply));
  assert(!cli._emergency_power.awakeDuration);
''', self.runtime_prefix(), flags=("-DESP32_PLATFORM", "-DMESH_ENABLE_EMERGENCY_POWER=1"))

    def test_actual_cli_checked_save_reboot_fallback_and_failure_keeps_candidate_inactive(self):
        body = r'''
  Board board; Clock clock; FILESYSTEM fs;
  nowMs = 100;
  CommonCLI cli(board, clock); cli.loadEmergencyPower(&fs);
  char reply[160];
  assert(cli.handleEmergencyPowerCommand("set power.emergency on", reply));
  assert(!strcmp(reply, "OK - emergency on") && cli.emergencyActive());
  assert(fs.commits == 1 && cli._emergency_power.settings.manual);
  nowMs += 5000;
  assert(cli.emergencySleepMilliseconds() == 1800000);
  assert(cli.handleEmergencyPowerCommand("set power.emergency on", reply));
  assert(fs.commits == 1 && cli._emergency_power.holdRemaining(nowMs) == 0);
  CommonCLI reboot(board, clock); reboot.loadEmergencyPower(&fs);
  assert(reboot.emergencyActive() && reboot.emergencySleepMilliseconds() == 0);
  assert(!reboot._emergency_power.awakeDuration); // Awake lease is deliberately runtime-only.
  fs.files["/power_emergency.bak"] = fs.files["/power_emergency"];
  fs.files["/power_emergency"][0] ^= 1;
  CommonCLI backup(board, clock); backup.loadEmergencyPower(&fs);
  assert(backup.emergencyActive());
  fs.files["/power_emergency.bak"][0] ^= 1;
  CommonCLI corrupt(board, clock); corrupt.loadEmergencyPower(&fs);
  assert(!corrupt.emergencyActive());
  fs.files.clear();
  for (unsigned failure = 0; failure < 4; ++failure) {
    fs.shortWrite = failure == 0; fs.failCommit = failure == 1;
    fs.badRead = failure == 2; fs.corruptCommit = failure == 3;
    assert(cli.handleEmergencyPowerCommand("set power.emergency auto", reply));
    assert(strstr(reply, "save unconfirmed") && cli._emergency_power.settings.manual);
    assert(cli._emergency_power.holdRemaining(nowMs) == 0);
  }
  fs.shortWrite = fs.failCommit = fs.badRead = fs.corruptCommit = false;
  assert(cli.handleEmergencyPowerCommand("set power.emergency auto\t", reply));
  assert(!strcmp(reply, "OK - emergency auto") && !cli.emergencyActive());
  CommonCLI fresh(board, clock); fresh.loadEmergencyPower(&fs);
  assert(!fresh._emergency_power.settings.manual);
  unsigned writes = fs.writes;
  board.emergencySupported = false;
  assert(fresh.handleEmergencyPowerCommand("set power.emergency on", reply));
  assert(strstr(reply, "unsupported") && !fresh.emergencyActive() && fs.writes == writes);
  board.emergencySupported = true;
  for (const char* command : {"set power.emergency off", "set power.emergency", "set power.emergency on extra",
       "get power.emergency x", "set power.awake", "set power.awake -1", "set power.awake +1",
       "set power.awake 10081", "set power.awake 99999999999999", "set power.awake 1.0",
       "set power.awake 1 2", "set power.awake 1min", "set power.awake \n"}) {
    assert(fresh.handleEmergencyPowerCommand(command, reply));
    assert(!strncmp(reply, "Error:", 6) && fs.writes == writes && !fresh.emergencyActive());
  }
  for (const char* command : {"get power.emergencyx", "set power.emergencyx on", "set power.awakex 1",
                             "get power.awake", "set unrelated on", ""}) {
    strcpy(reply, "unchanged");
    assert(!fresh.handleEmergencyPowerCommand(command, reply) && !strcmp(reply, "unchanged"));
  }
  assert(!fresh.handleEmergencyPowerCommand(nullptr, reply));
  assert(!fresh.handleEmergencyPowerCommand("get power.emergency", nullptr));
'''
        for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM", "STM32_PLATFORM"):
            with self.subTest(platform=platform):
                self.exercise(body, self.runtime_prefix(), flags=("-D" + platform,
                                                                 "-DMESH_ENABLE_EMERGENCY_POWER=1"))

    def test_disabled_actual_declarations_and_source_retain_no_state_or_io(self):
        prefix = self.runtime_prefix()
        # A missing implementation at link time would reveal a retained runtime
        # call; the public disabled API is entirely inline and stateless.
        self.exercise(r'''
  Board board; Clock clock; FILESYSTEM fs;
  CommonCLI cli(board, clock);
  cli.setEmergencyRadioSleeping(true);
  assert(!cli.emergencyActive() && cli.emergencySleepMilliseconds() == 0);
  assert(board.reads == 0 && fs.writes == 0);
  static_assert(sizeof(CommonCLI) == sizeof(Board*) + sizeof(Clock*)
                + sizeof(Prefs*) + sizeof(Sensors*) + 8,
                "disabled emergency policy must not retain its scheduler or filesystem");
''', prefix, flags=("-DSTM32_PLATFORM", "-DMESH_ENABLE_EMERGENCY_POWER=0"))


if __name__ == "__main__":
    unittest.main()
