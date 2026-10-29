"""Run actual emergency RF/wireless ownership and restoration with fake hardware."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <helpers/EmergencyPowerRuntime.h>
#include <helpers/OperatingSchedule.h>
#include <cassert>
#include <cstdlib>
#include <initializer_list>
#include <string>
#include <vector>
using namespace mesh::wireless;
namespace power = mesh::power;
static std::vector<std::string> events;
void delay(uint32_t milliseconds) {
  events.push_back("backoff:" + std::to_string(milliseconds));
}
static std::string serviceName(uint8_t bit) {
  return bit == WiFi ? "wifi" : bit == EspNow ? "espnow" : "ble";
}
struct Wireless : Backend {
  uint8_t active = All, supported = All, sessions = Independent;
  uint8_t pendingOn = 0, pendingOff = 0, failedOn = 0, failedOff = 0;
  uint8_t available() const override { return supported; }
  uint8_t enabled() const override { return active; }
  uint8_t clients() const override { return sessions; }
  Result set(uint8_t bit, bool on) override {
    events.push_back(serviceName(bit) + (on ? ":on" : ":off"));
    if ((on ? pendingOn : pendingOff) & bit) return Result::Pending;
    if ((on ? failedOn : failedOff) & bit) return Result::Failed;
    if (on) active |= bit; else active &= ~bit;
    return Result::Done;
  }
};
struct Mesh {
  bool available = true, inFlight = false;
  unsigned queuedFuturePackets = 20;
  bool hasOutbound() const { return inFlight; }
protected:
  @RUNTIME_FRIEND@
  bool isRadioAvailable() const { return available; }
  void setRadioAvailable(bool value) {
    events.push_back(value ? "mesh:on" : "mesh:off"); available = value;
  }
};
struct Radio {
  bool morseTx = false, morseRx = false, failSleep = false, failWake = false;
  bool recoveryOk = true;
  bool isMorseTxActive() const { return morseTx; }
  bool isMorseRxActive() const { return morseRx; }
  bool setTrackerSleep(bool sleeping) {
    events.push_back(sleeping ? "radio:sleep" : "radio:wake");
    return !(sleeping ? failSleep : failWake);
  }
  bool recoverRadio(bool hard) {
    assert(hard); events.push_back("radio:recover"); return recoveryOk;
  }
};
struct Board {
  bool supported = true, ota = false, radioTest = false, sleepOk = true;
  unsigned sleeps = 0;
  uint32_t lastBudget = 0;
  bool supportsEmergencySleep() const { return supported; }
  bool isOTAUpdateRunning() const { return ota; }
  bool isRadioTestActive() const { return radioTest; }
  virtual void setEmergencyRadioSleeping(bool) {}
  bool emergencySleepMilliseconds(uint32_t budget) {
    events.push_back("cpu:" + std::to_string(budget));
    ++sleeps; lastBudget = budget; return sleepOk;
  }
};
static constexpr int HIGH = 1, LOW = 0;
static constexpr unsigned SX126X_POWER_EN = @FEM_PIN@, PIN_3V3_EN = @RAIL_PIN@;
static int levels[64] = {};
static int digitalRead(unsigned pin) {
  assert(pin == SX126X_POWER_EN);
  events.push_back("fem:read");
  return levels[pin];
}
static void digitalWrite(unsigned pin, int value) {
  assert(pin == SX126X_POWER_EN); // Shared IO2/storage rail must never change.
  levels[pin] = value;
  events.push_back(value ? "fem:on" : "fem:off");
}
struct RAK3401Board : Board {
  @RAK_FIELDS@
  void setEmergencyRadioSleeping(bool sleeping) override;
};
@RAK_FEM_METHOD@
struct CLI {
  uint32_t budget = 2700000;
  bool radioSleeping = false;
  uint32_t emergencySleepMilliseconds() const { return budget; }
  void setEmergencyRadioSleeping(bool value) {
    events.push_back(value ? "status:sleep" : "status:awake"); radioSleeping = value;
  }
};
struct ScheduleCLI {
  mesh::powerschedule::State schedule;
  uint32_t utc = 1577836800;
  bool location = false;
  int32_t latitude = 0, longitude = 0;
  bool radioSleeping = false;
  // Standalone scheduling is deliberately not an emergency activation.
  bool emergencyActive() const { return false; }
  uint32_t emergencySleepMilliseconds() {
    return schedule.sleepMilliseconds(utc, location, latitude, longitude);
  }
  void setEmergencyRadioSleeping(bool value) {
    events.push_back(value ? "status:sleep" : "status:awake"); radioSleeping = value;
  }
};
struct Fixture {
  Wireless wireless;
  Mesh mesh;
  Radio radio;
  Board board;
  CLI cli;
  mesh::power::EmergencyPowerRuntime runtime;
  Fixture() { control().begin(wireless); }
  bool service(bool entryBusy = false, bool workRequired = false) {
    return runtime.service(mesh, radio, board, cli, entryBusy, workRequired);
  }
};
static void command(Control& c, const char* text, uint32_t now = 100) {
  char reply[160] = {};
  assert(c.handle(text, reply, sizeof(reply), now, Independent));
  assert(strncmp(reply, "OK", 2) == 0);
  c.service(now + 250);
  assert(!c.pending());
}
static void refused(Control& c, const char* text) {
  char reply[160] = {};
  assert(c.handle(text, reply, sizeof(reply), 1000, Independent));
  assert(strstr(reply, "scheduled sleep") && !c.pending());
}
int main(int argc, char** argv) {
  assert(argc == 2);
  const int scenario = std::atoi(argv[1]);
  if (scenario == 0) {
    Fixture f;
    assert(f.service());
    assert((events == std::vector<std::string>{"wifi:off", "espnow:off", "ble:off",
        "radio:sleep", "mesh:off", "status:sleep", "cpu:10000"}));
    assert(!f.mesh.available && f.cli.radioSleeping && f.wireless.active == 0);
    events.clear(); f.cli.budget = 417;
    assert(f.service(true)); // Reply-drain/partial-input guard does not steal already-suspended RF.
    assert((events == std::vector<std::string>{"cpu:417"}));
    events.clear(); f.cli.budget = 0;
    assert(!f.service());
    assert((events == std::vector<std::string>{"radio:wake", "mesh:on", "status:awake",
        "espnow:on", "wifi:on", "ble:on"}));
    assert(f.mesh.available && !f.cli.radioSleeping && f.wireless.active == All);
    events.clear(); assert(!f.service() && events.empty());
  } else if (scenario == 1) {
    Fixture f;
    f.board.supported = false; assert(!f.service());
    f.board.supported = true;
    assert(!f.service(true));
    assert(!f.service(false, true));
    f.board.ota = true; assert(!f.service()); f.board.ota = false;
    f.board.radioTest = true; assert(!f.service()); f.board.radioTest = false;
    f.mesh.inFlight = true; assert(!f.service()); f.mesh.inFlight = false;
    f.radio.morseTx = true; assert(!f.service()); f.radio.morseTx = false;
    f.radio.morseRx = true; assert(!f.service()); f.radio.morseRx = false;
    assert(events.empty() && f.mesh.available && !f.cli.radioSleeping && !f.board.sleeps);
    // Queued future packets are intentionally not consulted: normal mesh work
    // stays suspended, so an off-hour backlog cannot prevent battery protection.
    assert(f.mesh.queuedFuturePackets == 20 && f.service());
    assert(f.board.sleeps == 1 && !f.mesh.available);
  } else if (scenario == 2) {
    Fixture f;
    f.radio.failSleep = true;
    assert(!f.service() && f.mesh.available && !f.cli.radioSleeping && !f.board.sleeps);
    assert(f.wireless.active == 0);
    f.radio.failSleep = false; events.clear();
    assert(f.service() && !f.mesh.available && f.board.sleeps == 1);
    f.radio.failWake = true; f.radio.recoveryOk = false;
    f.cli.budget = 0; events.clear();
    assert(!f.service());
    assert((events == std::vector<std::string>{"radio:wake", "radio:recover", "mesh:on",
        "status:awake", "espnow:on", "wifi:on", "ble:on"}));
    // Return ownership to Dispatcher even after hardware recovery reports an
    // error, so its ordinary watchdog is not permanently disabled.
    assert(f.mesh.available && !f.cli.radioSleeping);
  } else if (scenario == 3) {
    Fixture f;
    for (unsigned cause = 0; cause != 5; ++cause) {
      assert(f.service() && !f.mesh.available);
      const unsigned sleeps = f.board.sleeps;
      f.board.ota = cause == 0;
      f.board.radioTest = cause == 1;
      f.radio.morseTx = cause == 2;
      f.radio.morseRx = cause == 3;
      const bool work = cause == 4;
      assert(!f.service(false, work));
      assert(f.mesh.available && !f.cli.radioSleeping && f.wireless.active == All);
      assert(f.board.sleeps == sleeps);
      assert(!f.service(false, work));
      assert(f.board.sleeps == sleeps); // Active work also prevents immediate re-entry.
      f.board.ota = f.board.radioTest = f.radio.morseTx = f.radio.morseRx = false;
    }
  } else if (scenario == 4) {
    Wireless b; Control c; c.begin(b);
    assert(c.suspendForEmergency());
    assert(b.active == 0 && c.blocked(All));
    assert(!c.allowService(Bluetooth));
    assert(!c.allowService(WiFi | EspNow));
    assert(!c.allowWiFiForOTA());
    assert(c.blocked(All) && !c.masterOff());
    for (const char* text : {"set wifi on", "set espnow on", "set 2.4ghz on all",
                             "set 2.4ghz off force"}) refused(c, text);
    char reply[160];
    assert(c.handle("get 2.4ghz", reply, sizeof(reply), 1000, Independent));
    assert(strstr(reply, "wifi=off") && strstr(reply, "bluetooth=off"));
    assert(c.resumeAfterEmergency() && b.active == All && !c.blocked(All));
    assert(c.allowService(Bluetooth) && c.allowWiFiForOTA());
  } else if (scenario == 5) {
    for (uint8_t mask = 0; mask <= All; ++mask) {
      Wireless b; b.active = mask;
      Control c; c.begin(b);
      command(c, "set 2.4ghz off"); command(c, "set 2.4ghz on", 500);
      assert(b.active == mask && !c.masterOff());
      for (uint8_t bit : {uint8_t(WiFi), uint8_t(Bluetooth), uint8_t(EspNow)})
        assert(c.blocked(bit) == !(mask & bit));
      assert(c.suspendForEmergency() && b.active == 0 && c.blocked(All));
      assert(c.resumeAfterEmergency() && b.active == mask && !c.masterOff());
      for (uint8_t bit : {uint8_t(WiFi), uint8_t(Bluetooth), uint8_t(EspNow)})
        assert(c.blocked(bit) == !(mask & bit));
      const size_t calls = events.size();
      assert(c.resumeAfterEmergency() && events.size() == calls);
    }
    Wireless b; b.active = WiFi | Bluetooth;
    Control c; c.begin(b);
    command(c, "set 2.4ghz off");
    assert(c.masterOff() && b.active == 0);
    const size_t calls = events.size();
    assert(c.suspendForEmergency() && c.resumeAfterEmergency());
    assert(c.masterOff() && c.blocked(All) && b.active == 0 && events.size() == calls);
    char reply[160];
    assert(c.handle("set wifi on", reply, sizeof(reply), 500, Independent));
    assert(strstr(reply, "2.4ghz is off"));
    command(c, "set 2.4ghz on", 1000);
    assert(b.active == (WiFi | Bluetooth)); // Original ordinary restore snapshot survived.
  } else if (scenario == 6) {
    Fixture f;
    f.wireless.pendingOff = WiFi;
    assert(!f.service() && f.wireless.active == All && !f.board.sleeps && f.mesh.available);
    refused(control(), "set wifi on");
    assert(!control().allowService(WiFi) && !control().allowWiFiForOTA());
    f.wireless.pendingOff = 0;
    f.wireless.failedOff = EspNow;
    assert(!f.service() && f.wireless.active == (EspNow | Bluetooth));
    assert(f.mesh.available && !f.cli.radioSleeping && !f.board.sleeps);
    // The hour/lease can change before RF ever went to sleep. Restore services
    // already stopped by the incomplete suspend, then allow a later retry.
    f.wireless.failedOff = 0; f.cli.budget = 0;
    assert(!f.service() && f.wireless.active == All);
    f.cli.budget = 2700000; assert(f.service());
    f.cli.budget = 0; f.wireless.pendingOn = EspNow;
    assert(!f.service() && f.mesh.available && f.wireless.active == 0);
    refused(control(), "set 2.4ghz on all");
    assert(!control().allowService(Bluetooth));
    f.wireless.pendingOn = 0; f.wireless.failedOn = WiFi;
    assert(!f.service() && f.wireless.active == EspNow);
    refused(control(), "set wifi on");
    f.wireless.failedOn = 0;
    assert(!f.service() && f.wireless.active == All && !control().blocked(All));
  } else if (scenario == 7) {
    Fixture f;
    char reply[160];
    assert(control().handle("set wifi off", reply, sizeof(reply), 100, Independent));
    assert(control().pending());
    assert(!f.service() && events.empty() && f.mesh.available);
    control().service(350);
    assert(f.wireless.active == (Bluetooth | EspNow) && !control().pending());
    assert(f.service() && f.wireless.active == 0);
    f.cli.budget = 0;
    assert(!f.service() && f.wireless.active == (Bluetooth | EspNow));
    assert(control().blocked(WiFi) && !control().blocked(Bluetooth | EspNow));
  } else if (scenario == 8) {
    Fixture f;
    f.mesh.available = false; // Sensor Tracker already owns this radio's sleep.
    assert(f.service() && f.cli.radioSleeping && f.board.sleeps == 1);
    for (const auto& event : events)
      assert(event != "radio:sleep" && event != "mesh:off");
    events.clear(); f.cli.budget = 0;
    assert(!f.service() && !f.mesh.available);
    for (const auto& event : events)
      assert(event != "radio:wake" && event != "radio:recover" && event != "mesh:on");
    assert(f.wireless.active == All);
  } else if (scenario == 9) {
    Fixture f;
    f.board.sleepOk = false;
    assert(f.service() && !f.mesh.available && f.cli.radioSleeping);
    assert(events.back() == "backoff:10");
    events.clear();
    assert(f.service() && !f.mesh.available && f.cli.radioSleeping);
    assert((events == std::vector<std::string>{"cpu:10000", "backoff:10"}));
    // A later successful attempt keeps the existing suspension and skips backoff.
    events.clear(); f.board.sleepOk = true;
    assert(f.service());
    assert((events == std::vector<std::string>{"cpu:10000"}));
    // An awake lease still wakes RF after a failed CPU wait.
    events.clear(); f.cli.budget = 0;
    assert(!f.service() && f.mesh.available && !f.cli.radioSleeping);
    assert(f.wireless.active == All);
  } else if (scenario == 10) {
    for (int starting_level : {LOW, HIGH}) {
      Wireless wireless; control().begin(wireless);
      Mesh mesh; Radio radio; RAK3401Board board; CLI cli;
      mesh::power::EmergencyPowerRuntime runtime;
      levels[SX126X_POWER_EN] = starting_level; levels[PIN_3V3_EN] = HIGH;
      events.clear();
      // Failed warm sleep never captures/disables the FEM.
      radio.failSleep = true;
      assert(!runtime.service(mesh, radio, board, cli, false));
      assert(levels[SX126X_POWER_EN] == starting_level);
      assert(events.back() == "radio:sleep");
      radio.failSleep = false; events.clear();
      assert(runtime.service(mesh, radio, board, cli, false));
      assert((events == std::vector<std::string>{"wifi:off", "espnow:off", "ble:off",
          "radio:sleep", "mesh:off", "fem:read", "fem:off", "status:sleep", "cpu:10000"}));
      assert(levels[SX126X_POWER_EN] == LOW && levels[PIN_3V3_EN] == HIGH);
      events.clear();
      // Maintenance and duplicate suspension must not overwrite the snapshot.
      board.setEmergencyRadioSleeping(true);
      assert(runtime.service(mesh, radio, board, cli, false));
      assert((events == std::vector<std::string>{"cpu:10000"}));
      cli.budget = 0; events.clear();
      radio.failWake = true;
      assert(!runtime.service(mesh, radio, board, cli, false));
      std::vector<std::string> expected = {starting_level ? "fem:on" : "fem:off"};
      if (starting_level) expected.push_back("backoff:1");
      const std::vector<std::string> after = {"radio:wake", "radio:recover", "mesh:on",
          "status:awake", "espnow:on", "wifi:on", "ble:on"};
      expected.insert(expected.end(), after.begin(), after.end());
      assert(events == expected);
      assert(levels[SX126X_POWER_EN] == starting_level && levels[PIN_3V3_EN] == HIGH);
      events.clear(); board.setEmergencyRadioSleeping(false); assert(events.empty());
    }
    Wireless wireless; control().begin(wireless);
    Mesh mesh; mesh.available = false; // Tracker owns RF; its FEM is untouched.
    Radio radio; RAK3401Board board; CLI cli;
    mesh::power::EmergencyPowerRuntime runtime;
    levels[SX126X_POWER_EN] = HIGH; events.clear();
    assert(runtime.service(mesh, radio, board, cli, false));
    cli.budget = 0; assert(!runtime.service(mesh, radio, board, cli, false));
    for (const auto& event : events) assert(event.find("fem:") == std::string::npos);
    assert(levels[SX126X_POWER_EN] == HIGH);
  } else if (scenario == 11) {
    Wireless wireless; control().begin(wireless);
    Mesh mesh; Radio radio; Board board; ScheduleCLI cli;
    mesh::power::EmergencyPowerRuntime runtime;
    mesh::powerschedule::Settings settings;
    settings.hours = true; settings.startMinute = 8 * 60; settings.endMinute = 18 * 60;
    cli.schedule.begin(settings);
    cli.utc += 7 * 3600;
    assert(!cli.emergencyActive());
    assert(runtime.service(mesh, radio, board, cli, false));
    assert(!mesh.available && cli.radioSleeping && wireless.active == 0);
    assert(board.lastBudget == 10000);
    events.clear(); cli.utc += 3600;
    assert(!runtime.service(mesh, radio, board, cli, false));
    assert(mesh.available && !cli.radioSleeping && wireless.active == All);
    assert((events == std::vector<std::string>{"radio:wake", "mesh:on", "status:awake",
        "espnow:on", "wifi:on", "ble:on"}));
    cli.utc += 10 * 3600;
    events.clear();
    assert(!runtime.service(mesh, radio, board, cli, true)); // Live reply/TX gets to finish.
    assert(mesh.available && events.empty());
    assert(runtime.service(mesh, radio, board, cli, false));
    assert(!mesh.available && wireless.active == 0);
    board.ota = true;
    assert(!runtime.service(mesh, radio, board, cli, false));
    assert(mesh.available && wireless.active == All);
  } else if (scenario == 12) {
    Wireless wireless; control().begin(wireless);
    Mesh mesh; Radio radio; Board board; ScheduleCLI cli;
    mesh::power::EmergencyPowerRuntime runtime;
    mesh::powerschedule::Settings settings; settings.daylight = true;
    cli.schedule.begin(settings);
    cli.location = true; cli.latitude = 47600000; cli.longitude = -122300000;
    cli.utc = 1782000000 + 8 * 3600;
    assert(!cli.emergencyActive());
    assert(runtime.service(mesh, radio, board, cli, false));
    assert(!mesh.available && cli.radioSleeping && wireless.active == 0);
    events.clear(); cli.utc = 1782000000 + 20 * 3600;
    assert(!runtime.service(mesh, radio, board, cli, false));
    assert(mesh.available && !cli.radioSleeping && wireless.active == All);
    assert((events == std::vector<std::string>{"radio:wake", "mesh:on", "status:awake",
        "espnow:on", "wifi:on", "ble:on"}));
    cli.utc = 1782000000 + 8 * 3600;
    assert(runtime.service(mesh, radio, board, cli, false));
    cli.location = false;
    assert(!runtime.service(mesh, radio, board, cli, false)); // Unknown position leaves access available.
    assert(mesh.available && wireless.active == All);
  } else assert(false);
}
'''


class EmergencyPowerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="emergency-runtime-")
        work = Path(cls.folder.name)
        source, cls.binary = work / "main.cpp", work / "check"
        (work / "Arduino.h").write_text(
            "#pragma once\n#include <stdint.h>\nvoid delay(uint32_t);\n", encoding="ascii")
        board_header = (ROOT / "variants/rak3401/RAK3401Board.h").read_text()
        board_source = (ROOT / "variants/rak3401/RAK3401Board.cpp").read_text()
        dispatcher = (ROOT / "src/Dispatcher.h").read_text()
        variant = (ROOT / "variants/rak3401/variant.h").read_text()
        fields = re.search(r"class RAK3401Board[^\n]*\{\n#if MESH_ENABLE_EMERGENCY_POWER\n(.*?)#endif",
                           board_header, re.S).group(1)
        code = HARNESS.replace("@RAK_FIELDS@", fields)
        code = code.replace("@RAK_FEM_METHOD@", extract_braced(
            board_source, "void RAK3401Board::setEmergencyRadioSleeping("))
        code = code.replace("@RUNTIME_FRIEND@", re.search(
            r"friend class power::EmergencyPowerRuntime;", dispatcher).group(0))
        for marker, name, body in (("FEM_PIN", "SX126X_POWER_EN", variant),
                                   ("RAIL_PIN", "PIN_3V3_EN", board_header)):
            pin = re.search(rf"#define\s+{name}\s+\(?\s*(\d+)", body).group(1)
            code = code.replace("@" + marker + "@", pin)
        source.write_text(code, encoding="ascii")
        result = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer",
            "-fno-pie", "-no-pie", "-DMESH_ENABLE_EMERGENCY_POWER=1", "-I", str(work),
            "-I", str(ROOT / "src"),
            str(source), "-o", str(cls.binary)], capture_output=True, text=True, timeout=60)
        if result.returncode:
            cls.folder.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def scenario(self, number):
        result = subprocess.run([str(self.binary), str(number)], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_successful_sleep_wake_order_repeat_ownership_and_ten_second_budget(self):
        self.scenario(0)

    def test_live_work_prevents_entry_but_future_queue_does_not(self):
        self.scenario(1)

    def test_radio_refusal_and_failed_wake_return_dispatcher_watchdog_ownership(self):
        self.scenario(2)

    def test_critical_work_started_while_suspended_wakes_before_further_cpu_sleep(self):
        self.scenario(3)

    def test_all_wireless_start_paths_are_refused_during_emergency_ownership(self):
        self.scenario(4)

    def test_wireless_subset_blocked_mask_master_off_and_saved_snapshot_survive(self):
        self.scenario(5)

    def test_async_pending_errors_and_cancelled_partial_suspend_restore_correctly(self):
        self.scenario(6)

    def test_existing_wireless_request_finishes_before_emergency_ownership(self):
        self.scenario(7)

    def test_sensor_tracker_radio_ownership_is_not_stolen(self):
        self.scenario(8)

    def test_cpu_sleep_refusal_yields_without_resuming_normal_rf_work(self):
        self.scenario(9)

    def test_rak3401_actual_fem_pin_restores_before_radio_wake_without_cutting_shared_rail(self):
        self.scenario(10)

    def test_standalone_daily_hours_sleep_and_restore_rf_wireless_without_emergency_mode(self):
        self.scenario(11)

    def test_standalone_daylight_sleep_and_position_loss_restore_rf_wireless(self):
        self.scenario(12)

    def test_room_maintenance_runs_before_emergency_early_return(self):
        source = (ROOT / "examples/simple_room_server/main.cpp").read_text()
        loop = source[source.index("void loop() {"):]
        gate = loop.index("emergency_power.service(")
        for maintenance in ("mesh::power::serviceRegulatorSettings();",
                            "mesh::solar::service();", "board.loop();"):
            self.assertEqual(loop.count(maintenance), 1, maintenance)
            self.assertLess(loop.index(maintenance), gate, maintenance)

    def test_esp_uart_console_restore_stays_live_during_emergency_sleep(self):
        for role in ("simple_repeater", "simple_room_server", "simple_sensor"):
            source = (ROOT / f"examples/{role}/main.cpp").read_text()
            loop = source[source.index("void loop() {"):]
            gate = loop.index("emergency_power.service(")
            service = "board.servicePowerTestControls();"
            self.assertLess(loop.index(service), gate, role)
            self.assertIn("#if !MESH_ENABLE_EMERGENCY_POWER\n  " + service + "\n#endif",
                          loop[gate:], role)


if __name__ == "__main__":
    unittest.main()
