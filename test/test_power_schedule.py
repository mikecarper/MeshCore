"""Run the saved operating schedule and actual CLI against clock/location changes."""
from pathlib import Path
import unittest

import test_emergency_power as emergency


ROOT = Path(__file__).resolve().parents[1]
POLICY_PREFIX = r'''
#include <helpers/OperatingSchedule.h>
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <vector>
using namespace mesh::powerschedule;
static constexpr uint32_t epoch = 1577836800; // 2020-01-01, midnight UTC
'''


class PowerScheduleTests(unittest.TestCase):
    def exercise(self, body, prefix=POLICY_PREFIX, flags=()):
        emergency.EmergencyPowerTests().exercise(body, prefix, flags)

    def cli(self, body, platform="ESP32_PLATFORM"):
        self.exercise(body, emergency.EmergencyPowerTests().runtime_prefix(),
                      flags=("-D" + platform, "-DMESH_ENABLE_EMERGENCY_POWER=1"))

    def test_daily_window_exact_minute_and_second_boundaries(self):
        self.exercise(r'''
  Settings settings;
  State state; state.begin(settings);
  for (unsigned hour = 0; hour < 24; ++hour)
    assert(state.sleepMilliseconds(epoch + hour * 3600, false, 0, 0) == 0);
  settings.hours = true; settings.startMinute = 8 * 60; settings.endMinute = 18 * 60;
  state.begin(settings);
  assert(state.sleepMilliseconds(epoch + 8 * 3600 - 1, false, 0, 0) == 1000);
  assert(state.sleepMilliseconds(epoch + 8 * 3600, false, 0, 0) == 0);
  assert(state.sleepMilliseconds(epoch + 18 * 3600 - 1, false, 0, 0) == 0);
  assert(state.sleepMilliseconds(epoch + 18 * 3600, false, 0, 0) == 14UL * 3600000);
  for (unsigned minute = 0; minute < 1440; ++minute) {
    const bool expected_awake = minute >= 480 && minute < 1080;
    assert((state.sleepMilliseconds(epoch + minute * 60, false, 0, 0) == 0)
        == expected_awake);
  }
  // Explicit 24:00 permits the whole day without a same-start/end ambiguity.
  settings.startMinute = 0; settings.endMinute = 1440; state.begin(settings);
  assert(state.sleepMilliseconds(epoch, false, 0, 0) == 0);
  assert(state.sleepMilliseconds(epoch + 86399, false, 0, 0) == 0);
''')

    def test_overnight_windows_fixed_offsets_and_clock_corrections(self):
        self.exercise(r'''
  Settings settings; settings.hours = true;
  settings.startMinute = 22 * 60; settings.endMinute = 6 * 60;
  State state; state.begin(settings);
  for (unsigned minute = 0; minute < 1440; ++minute) {
    const bool expected_awake = minute < 360 || minute >= 1320;
    assert((state.sleepMilliseconds(epoch + minute * 60, false, 0, 0) == 0)
        == expected_awake);
  }
  assert(state.sleepMilliseconds(epoch + 6 * 3600, false, 0, 0) == 16UL * 3600000);
  settings.startMinute = 8 * 60; settings.endMinute = 18 * 60;
  for (int offset : {-720, -420, 330, 840}) {
    settings.offsetMinutes = int16_t(offset); state.begin(settings);
    for (unsigned minute = 0; minute < 1440; ++minute) {
      const int local = (int(minute) + offset + 1440) % 1440;
      const bool expected_awake = local >= 480 && local < 1080;
      assert((state.sleepMilliseconds(epoch + minute * 60, false, 0, 0) == 0)
          == expected_awake);
    }
  }
  settings.offsetMinutes = 0; state.begin(settings);
  assert(state.sleepMilliseconds(epoch + 20 * 3600, false, 0, 0) > 0);
  assert(state.sleepMilliseconds(epoch + 10 * 3600, false, 0, 0) == 0); // RTC corrected backward.
  assert(state.sleepMilliseconds(epoch + 7 * 3600, false, 0, 0) > 0);
  assert(state.sleepMilliseconds(epoch + 9 * 3600, false, 0, 0) == 0);
  assert(state.sleepMilliseconds(0, false, 0, 0) == 0);
  assert(state.sleepMilliseconds(epoch - 1, false, 0, 0) == 0);
  assert(state.sleepMilliseconds(UINT32_MAX, false, 0, 0) <= 86400000UL);
''')

    def test_daylight_and_hour_limits_intersect_and_missing_inputs_fail_open(self):
        self.exercise(r'''
  const uint32_t june = 1782000000; // 2026-06-21, midnight UTC
  Settings settings; settings.daylight = true;
  State state; state.begin(settings);
  assert(state.sleepMilliseconds(june + 20 * 3600, true, 47600000, -122300000) == 0);
  assert(state.sleepMilliseconds(june + 8 * 3600, true, 47600000, -122300000) > 0);
  // Known coordinates are cached for at most one UTC second, preserving scarce node RAM.
  assert(state.sleepMilliseconds(june, true, 85000000, 0) == 0);
  assert(state.sleepMilliseconds(june, true, -85000000, 0) == 0);
  assert(state.sleepMilliseconds(june + 1, true, -85000000, 0) > 0);
  assert(state.sleepMilliseconds(june + 1, false, 0, 0) == 0); // Validity loss is immediate.
  assert(state.sleepMilliseconds(june + 3, true, 91000000, 0) == 0);
  assert(state.sleepMilliseconds(0, true, -85000000, 0) == 0);

  settings.hours = true; settings.startMinute = 0; settings.endMinute = 12 * 60;
  state.begin(settings);
  assert(state.sleepMilliseconds(june + 8 * 3600, true, 47600000, -122300000) > 0); // Night.
  assert(state.sleepMilliseconds(june + 20 * 3600, true, 47600000, -122300000) > 0); // Hours closed.
  settings.startMinute = 19 * 60; settings.endMinute = 22 * 60; state.begin(settings);
  assert(state.sleepMilliseconds(june + 20 * 3600, true, 47600000, -122300000) == 0);
  assert(state.sleepMilliseconds(june + 8 * 3600, false, 0, 0) > 0); // Missing GPS does not bypass hours.
  assert(state.sleepMilliseconds(june + 20 * 3600, false, 0, 0) == 0);
  assert(state.sleepMilliseconds(0, false, 0, 0) == 0); // No civil-hour guesses from uptime.
''')

    def test_checked_record_roundtrip_corruption_reserved_values_and_old_record_independence(self):
        self.exercise(r'''
  static_assert(RecordSize == 14, "Schedule stays independent of the legacy emergency record");
  static_assert(mesh::emergencypower::RecordSize == 8, "Existing emergency images remain readable");
  for (bool daylight : {false, true}) for (bool hours : {false, true}) {
    Settings expected;
    expected.daylight = daylight; expected.hours = hours;
    expected.startMinute = 22 * 60; expected.endMinute = 6 * 60;
    expected.offsetMinutes = -420;
    uint8_t bytes[RecordSize]; encode(bytes, expected);
    Settings actual;
    assert(decode(bytes, sizeof(bytes), actual));
    assert(actual.daylight == daylight && actual.hours == hours);
    assert(actual.startMinute == 1320 && actual.endMinute == 360 && actual.offsetMinutes == -420);
    for (size_t byte = 0; byte < RecordSize; ++byte) {
      for (unsigned bit = 0; bit < 8; ++bit) {
        uint8_t corrupted[RecordSize]; memcpy(corrupted, bytes, sizeof(bytes));
        corrupted[byte] ^= uint8_t(1U << bit);
        assert(!decode(corrupted, sizeof(corrupted), actual));
        assert(!actual.daylight && !actual.hours);
      }
      assert(!decode(bytes, byte, actual));
    }
    std::vector<uint8_t> oversized(bytes, bytes + RecordSize); oversized.push_back(0);
    assert(!decode(oversized.data(), oversized.size(), actual));
    assert(!decode(nullptr, RecordSize, actual));
  }
  for (unsigned bad = 0; bad < 5; ++bad) {
    Settings invalid; invalid.hours = true; invalid.startMinute = 480; invalid.endMinute = 1080;
    if (bad == 0) invalid.startMinute = 1440;
    if (bad == 1) invalid.endMinute = 1441;
    if (bad == 2) invalid.endMinute = invalid.startMinute;
    if (bad == 3) invalid.offsetMinutes = -721;
    if (bad == 4) invalid.offsetMinutes = 841;
    uint8_t bytes[RecordSize]; encode(bytes, invalid);
    Settings actual; assert(!decode(bytes, sizeof(bytes), actual));
  }
  for (unsigned field : {0U, 3U, 4U, 5U}) {
    Settings good;
    uint8_t bytes[RecordSize]; encode(bytes, good);
    bytes[field] = field == 0 ? 'X' : field == 4 ? 4 : 2;
    const uint16_t crc = mesh::emergencypower::checksum(bytes, 12);
    bytes[12] = uint8_t(crc); bytes[13] = uint8_t(crc >> 8);
    Settings actual;
    assert(!decode(bytes, sizeof(bytes), actual) && !actual.daylight && !actual.hours);
  }
  for (int offset : {-720, 0, 330, 840}) {
    Settings expected; expected.offsetMinutes = int16_t(offset);
    uint8_t bytes[RecordSize]; encode(bytes, expected);
    Settings actual;
    assert(decode(bytes, sizeof(bytes), actual) && actual.offsetMinutes == offset);
  }
  // Validate raw wire ranges before assigning compact11-bit in-memory fields.
  for (unsigned field : {6U, 8U}) for (uint16_t bad : {uint16_t(2048), uint16_t(65535)}) {
    Settings good; good.hours = true; good.startMinute = 480; good.endMinute = 1080;
    uint8_t bytes[RecordSize]; encode(bytes, good);
    bytes[field] = uint8_t(bad); bytes[field + 1] = uint8_t(bad >> 8);
    const uint16_t crc = mesh::emergencypower::checksum(bytes, 12);
    bytes[12] = uint8_t(crc); bytes[13] = uint8_t(crc >> 8);
    Settings actual;
    assert(!decode(bytes, sizeof(bytes), actual) && !actual.daylight && !actual.hours);
  }
''')

    def test_clock_rollback_across_dawn_inside_one_cached_minute(self):
        self.exercise(r'''
  const uint32_t night = 1782000000 + 8 * 3600;
  Settings settings; settings.daylight = true;
  State state;
  bool exercised = false;
  for (int32_t longitude : {-122300000, -122250000, -122200000}) {
    const auto solar = mesh::solar::evaluate(night, 47600000, longitude);
    assert(solar.state == mesh::solar::State::Night && solar.nextChangeUtc > night);
    const uint32_t dawn = solar.nextChangeUtc;
    if ((dawn - 1) / 60 != (dawn + 1) / 60) continue;
    state.begin(settings);
    assert(state.sleepMilliseconds(dawn + 1, true, 47600000, longitude) == 0);
    assert(state.sleepMilliseconds(dawn - 1, true, 47600000, longitude) > 0);
    exercised = true;
  }
  assert(exercised);
''')

    def test_invalid_clock_then_uint32_ceiling_recomputes_daylight(self):
        self.exercise(r'''
  Settings settings; settings.daylight = true;
  State state; state.begin(settings);
  assert(mesh::solar::evaluate(UINT32_MAX, 47600000, -122300000).state
      == mesh::solar::State::Night);
  assert(state.sleepMilliseconds(UINT32_MAX, true, 47600000, -122300000) > 0);
  assert(state.sleepMilliseconds(0, true, 47600000, -122300000) == 0);
  assert(state.sleepMilliseconds(UINT32_MAX, true, 47600000, -122300000) > 0);
''')

    def test_actual_cli_standalone_hour_schedule_reply_grace_awake_override_and_persistence(self):
        body = r'''
  Board board; Clock clock; FILESYSTEM fs; Prefs prefs; Sensors sensors;
  CommonCLI cli(board, clock, prefs, sensors);
  nowMs = 100; clock.time = epoch + 20 * 3600;
  cli.loadEmergencyPower(&fs);
  assert(!cli.emergencyActive() && cli.emergencySleepMilliseconds() == 0);
  char reply[160] = {};
  assert(cli.handleEmergencyPowerCommand("set power.hours 08:00-18:00", reply));
  assert(!strncmp(reply, "OK", 2));
  assert(fs.files["/power_schedule"].size() == 14);
  assert(fs.files.count("/power_emergency") == 0);
  assert(!cli.emergencyActive() && cli.emergencySleepMilliseconds() == 0); // Reply grace.
  nowMs += 5000;
  assert(!cli.emergencyActive() && cli.emergencySleepMilliseconds() > 0);
  assert(cli.handleEmergencyPowerCommand("get power.hours", reply));
  assert(strstr(reply, "08:00-18:00"));
  assert(cli.handleEmergencyPowerCommand("get power.schedule", reply));
  assert(strstr(reply, "sleep") && strlen(reply) < 157);
  const unsigned writes = fs.writes;
  assert(cli.handleEmergencyPowerCommand("set power.hours 08:00-18:00", reply));
  assert(fs.writes == writes && cli.emergencySleepMilliseconds() > 0); // No poll/idempotent lease.
  assert(cli.handleEmergencyPowerCommand("set power.awake 1", reply));
  assert(cli.emergencySleepMilliseconds() == 0);
  nowMs += 60000;
  assert(cli.emergencySleepMilliseconds() > 0);
  assert(cli.handleEmergencyPowerCommand("set power.hours.offset -420", reply));
  assert(!strncmp(reply, "OK", 2));
  nowMs += 5000;
  assert(cli.emergencySleepMilliseconds() == 0); // 20:00 UTC is 13:00 at this fixed offset.
  CommonCLI reboot(board, clock, prefs, sensors); reboot.loadEmergencyPower(&fs);
  nowMs += 5000;
  assert(!reboot.emergencyActive() && reboot.emergencySleepMilliseconds() == 0);
  assert(reboot.handleEmergencyPowerCommand("get power.hours.offset", reply));
  assert(strstr(reply, "-420"));
  clock.time = epoch + 3 * 3600;
  assert(reboot.emergencySleepMilliseconds() > 0);
  assert(reboot.handleEmergencyPowerCommand("set power.schedule off", reply));
  assert(!strncmp(reply, "OK", 2) && reboot.emergencySleepMilliseconds() == 0);
'''
        for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM", "STM32_PLATFORM"):
            with self.subTest(platform=platform):
                self.cli(body, platform)

    def test_actual_cli_daylight_emergency_intersection_and_location_priority(self):
        self.cli(r'''
  Board board; Clock clock; FILESYSTEM fs; Prefs prefs; Sensors sensors;
  prefs.node_lat = 47.6; prefs.node_lon = -122.3;
  sensors.cached = true; sensors.latitude = -85; sensors.longitude = 0;
  CommonCLI cli(board, clock, prefs, sensors);
  nowMs = 0; clock.time = 1782000000 + 20 * 3600;
  cli.loadEmergencyPower(&fs);
  char reply[160];
  assert(cli.handleEmergencyPowerCommand("set power.daylight on", reply));
  assert(!strncmp(reply, "OK", 2));
  nowMs += 5000;
  assert(!cli.emergencyActive() && cli.emergencySleepMilliseconds() == 0);
  assert(sensors.reads == 0); // Fixed location takes priority; no GPS activation/read needed.
  assert(cli.handleEmergencyPowerCommand("set power.emergency on", reply));
  nowMs += 5000;
  clock.time += 10 * 60;
  assert(cli.emergencyActive() && cli.emergencySleepMilliseconds() == 0);
  clock.time += 5 * 60;
  assert(cli.emergencySleepMilliseconds() > 0); // First15min restriction still applies in daytime.
  clock.time = 1782000000 + 8 * 3600 + 10 * 60;
  assert(cli.emergencySleepMilliseconds() > 0); // Night suppresses even an emergency awake slot.
  assert(cli.handleEmergencyPowerCommand("set power.awake 1", reply));
  assert(cli.emergencySleepMilliseconds() == 0);
  assert(cli.handleEmergencyPowerCommand("set power.awake 0", reply));
  assert(cli.emergencySleepMilliseconds() > 0);
  assert(cli.handleEmergencyPowerCommand("set power.emergency auto", reply));
  nowMs += 5000;
  assert(!cli.emergencyActive() && cli.emergencySleepMilliseconds() > 0); // Independent setting survives.
  assert(cli.handleEmergencyPowerCommand("get power.daylight", reply));
  assert(strstr(reply, "on"));
  prefs.node_lat = prefs.node_lon = 0;
  sensors.latitude = 85; sensors.longitude = 0;
  ++clock.time; // New known coordinates take effect by the next UTC second.
  assert(cli.emergencySleepMilliseconds() == 0); // Cached polar-day GPS, without starting receiver.
  assert(sensors.reads > 0);
  sensors.cached = false;
  assert(cli.emergencySleepMilliseconds() == 0);
  assert(cli.handleEmergencyPowerCommand("get power.schedule", reply));
  assert(strstr(reply, "unknown"));
  assert(cli.handleEmergencyPowerCommand("set power.hours 19:00-22:00", reply));
  nowMs += 5000;
  assert(cli.emergencySleepMilliseconds() > 0); // Missing GPS cannot open a closed civil-hour gate.
  clock.time = 0;
  assert(cli.emergencySleepMilliseconds() == 0); // Missing time fails open for standalone schedules.
''')

    def test_actual_cli_recovery_failure_is_isolated_to_its_settings_path(self):
        self.cli(r'''
  for (bool bad_emergency : {false, true}) {
    Board board; Clock clock; FILESYSTEM fs;
    fs.blockedPath = bad_emergency ? "/power_emergency" : "/power_schedule";
    CommonCLI cli(board, clock); nowMs = 0; cli.loadEmergencyPower(&fs);
    char reply[160];
    const char* blocked = bad_emergency ? "set power.emergency on" : "set power.daylight on";
    assert(cli.handleEmergencyPowerCommand(blocked, reply));
    assert(strstr(reply, "save unconfirmed") && fs.writes == 0);
    const char* allowed = bad_emergency ? "set power.hours 08:00-18:00" : "set power.emergency on";
    assert(cli.handleEmergencyPowerCommand(allowed, reply));
    assert(!strncmp(reply, "OK", 2) && fs.commits == 1);
    assert(cli.emergencyActive() != bad_emergency);
    assert(fs.files.count(bad_emergency ? "/power_schedule" : "/power_emergency") == 1);
    assert(fs.files.count(bad_emergency ? "/power_emergency" : "/power_schedule") == 0);
    CommonCLI reboot(board, clock); reboot.loadEmergencyPower(&fs);
    assert(reboot.emergencyActive() != bad_emergency);
    assert(reboot.handleEmergencyPowerCommand("get power.hours", reply));
    assert(strstr(reply, bad_emergency ? "08:00-18:00" : "off"));
    assert(reboot.handleEmergencyPowerCommand("get power.daylight", reply));
    assert(strstr(reply, "off"));
  }
''')

    def test_actual_cli_preserves_legacy_manual_state_and_atomic_failure_leaves_schedule_inactive(self):
        self.cli(r'''
  Board board; Clock clock; FILESYSTEM fs;
  mesh::emergencypower::Settings legacy; legacy.manual = true;
  uint8_t bytes[mesh::emergencypower::RecordSize]; mesh::emergencypower::encode(bytes, legacy);
  const std::vector<uint8_t> original(bytes, bytes + sizeof(bytes));
  fs.files["/power_emergency"] = original;
  CommonCLI cli(board, clock); nowMs = 100; cli.loadEmergencyPower(&fs);
  char reply[160]; nowMs += 5000;
  assert(cli.emergencyActive());
  for (unsigned failure = 0; failure < 4; ++failure) {
    fs.shortWrite = failure == 0; fs.failCommit = failure == 1;
    fs.badRead = failure == 2; fs.corruptCommit = failure == 3;
    assert(cli.handleEmergencyPowerCommand("set power.daylight on", reply));
    assert(strstr(reply, "Error:") && cli.emergencyActive());
    assert(cli.handleEmergencyPowerCommand("get power.daylight", reply));
    assert(strstr(reply, "off"));
    assert(fs.files["/power_emergency"] == original);
  }
  fs.shortWrite = fs.failCommit = fs.badRead = fs.corruptCommit = false;
  assert(cli.handleEmergencyPowerCommand("set power.hours 22:00-06:00", reply));
  assert(!strncmp(reply, "OK", 2));
  assert(fs.files["/power_emergency"] == original);
  fs.files["/power_schedule.bak"] = fs.files["/power_schedule"];
  fs.files["/power_schedule"][0] ^= 1;
  CommonCLI backup(board, clock); backup.loadEmergencyPower(&fs);
  assert(backup.handleEmergencyPowerCommand("get power.hours", reply));
  assert(strstr(reply, "22:00-06:00"));
  fs.files["/power_schedule.bak"][0] ^= 1;
  CommonCLI corrupt(board, clock); corrupt.loadEmergencyPower(&fs);
  assert(corrupt.handleEmergencyPowerCommand("get power.hours", reply));
  assert(strstr(reply, "off") && corrupt.emergencyActive());
''')

    def test_actual_cli_strict_parsing_unsupported_board_and_no_accidental_prefix_match(self):
        self.cli(r'''
  Board board; Clock clock; FILESYSTEM fs;
  CommonCLI cli(board, clock); nowMs = 0; cli.loadEmergencyPower(&fs);
  char reply[160];
  for (const char* command : {"set power.daylight", "set power.daylight AUTO", "set power.daylight on extra",
      "get power.daylight x", "set power.hours", "set power.hours 8:00-18:00", "set power.hours 08:00",
      "set power.hours 24:00-06:00", "set power.hours 08:60-18:00", "set power.hours 08:00-24:01",
      "set power.hours 08:00-08:00", "set power.hours 08:00-18:00 extra", "get power.hours x",
      "set power.hours.offset", "set power.hours.offset -721", "set power.hours.offset 841",
      "set power.hours.offset 3.5", "set power.hours.offset 60x", "set power.hours.offset --1",
      "get power.hours.offset x", "set power.schedule on", "get power.schedule x"}) {
    const unsigned writes = fs.writes;
    assert(cli.handleEmergencyPowerCommand(command, reply));
    assert(!strncmp(reply, "Error:", 6) && fs.writes == writes);
  }
  for (const char* command : {"get power.daylightx", "set power.hoursx off", "set power.hours.offsetx 0",
      "get power.schedulex", "get power.hour", "set unrelated on"}) {
    strcpy(reply, "unchanged");
    assert(!cli.handleEmergencyPowerCommand(command, reply) && !strcmp(reply, "unchanged"));
  }
  board.emergencySupported = false;
  for (const char* command : {"set power.daylight on", "set power.hours 08:00-18:00"}) {
    const unsigned writes = fs.writes;
    assert(cli.handleEmergencyPowerCommand(command, reply));
    assert(strstr(reply, "unsupported") && fs.writes == writes);
  }
''')

    def test_actual_cli_boundary_values_whitespace_and_complete_bounded_status(self):
        self.cli(r'''
  Board board; Clock clock; FILESYSTEM fs;
  CommonCLI cli(board, clock); nowMs = 0; cli.loadEmergencyPower(&fs);
  char reply[160];
  for (const char* command : {"set power.daylight on \t", "set power.hours 00:00-24:00 \t",
      "set power.hours.offset +330\t", "set power.hours.offset -720 \t",
      "set power.awake 10080"}) {
    memset(reply, 'Z', sizeof(reply));
    assert(cli.handleEmergencyPowerCommand(command, reply) && !strncmp(reply, "OK", 2));
    assert(reply[157] == 'Z' && reply[158] == 'Z' && reply[159] == 'Z');
  }
  clock.time = 0;
  memset(reply, 'Z', sizeof(reply));
  assert(cli.handleEmergencyPowerCommand("get power.schedule \t", reply));
  for (const char* token : {"daylight=on", "state=unknown", "hours=00:00-24:00", "offset=-720",
      "clock=unknown", "emergency=off", "window=awake", "radio=awake", "hold=604800s"})
    assert(strstr(reply, token));
  assert(strlen(reply) < 157 && reply[157] == 'Z' && reply[158] == 'Z' && reply[159] == 'Z');
  assert(!cli.emergencyActive() && cli.emergencySleepMilliseconds() == 0);
''')


if __name__ == "__main__":
    unittest.main()
