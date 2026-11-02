#!/usr/bin/env python3
"""Execute the actual MQTT initializer, startup wait and stop acknowledgement.

The SDK/facade model covers AP ownership without network, hardware or PIO.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


class FixtureFailure(AssertionError):
    pass


class MQTTAccessPointOwnershipTest(unittest.TestCase):
    def fixture(self):
        source = (ROOT / "src/helpers/bridges/MQTTBridge.cpp").read_text(encoding="ascii")
        initialize = "\n".join(method(source, signature) for signature in (
            "static bool mqttStationWiFiMutationAllowed(",
            "static wifi_ps_type_t mqttWiFiPowerSaveMode(",
            "void MQTTBridge::beginWiFiStation(",
            "bool MQTTBridge::initializeWiFiInTask(",
            "bool MQTTBridge::handleWiFiConnection(",
        ))
        wait = method(source, "bool MQTTBridge::waitUnlessStopping(")
        task = method(source, "void MQTTBridge::mqttTaskLoop(")
        startup = task[task.index("{") + 1:task.index("  // Main task loop")]
        stop = method(task, "    if (_stop_requested)")
        fixture = (ROOT / "test/fixtures/mqtt_ap_ownership.cpp").read_text(encoding="ascii")
        policy = (ROOT / "src/helpers/esp32/WiFiRadioPolicy.h").read_text(encoding="ascii")
        guard = method(policy, "inline bool stationMutationAllowed(")
        power = (ROOT / "src/helpers/esp32/WiFiStationPower.h").read_text(encoding="ascii")
        power_methods = "\n".join(method(power, signature) for signature in (
            "inline wifi_power_t stationTransmitPower(",
            "inline esp_err_t applyStationPowerSave(",
        ))
        return (fixture.replace("@STATION_GUARD@", guard)
                .replace("@STATION_POWER@", power_methods)
                .replace("@INITIALIZE@", initialize).replace("@WAIT@", wait)
                .replace("@TASK_START@", startup).replace("@TASK_STOP@", stop))

    def run_fixture(self, scenario, source=None, *flags):
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="mqtt-ap-ownership-") as directory:
            path = Path(directory)
            (path / "test.cpp").write_text(source or self.fixture(), encoding="ascii")
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DESP_PLATFORM=1", "-I", str(ROOT / "src"),
                *flags, str(path / "test.cpp"), "-o", str(path / "test")],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(path / "test"), scenario],
                capture_output=True, text=True, timeout=15)
            if result.returncode:
                raise FixtureFailure(result.stdout + result.stderr)

    def test_sdk_ap_and_apsta_defer_all_wifi_mutations_for_shared_roles(self):
        # Both an infrastructure manager and Companion's external WiFi owner
        # run the same production initializer; Arduino 3 removed auto-connect.
        self.run_fixture("ap")
        self.run_fixture("ap", None, "-DESP_ARDUINO_VERSION_MAJOR=3")

    def test_stop_while_ap_waiting_acknowledges_within_one_sleep_quantum(self):
        self.run_fixture("stop_waiting")
        self.run_fixture("stop_before_start")

    def test_ap_teardown_admits_station_initialization_and_slot_work(self):
        self.run_fixture("handoff")
        self.run_fixture("companion_handoff")

    def test_cold_start_and_repeated_restart_register_events_once(self):
        self.run_fixture("cold_and_restart")

    def test_changed_credentials_replace_connected_station_after_ownership_release(self):
        self.run_fixture("changed_credentials")

    def test_sdk_read_and_station_enable_failures_defer_and_retry(self):
        self.run_fixture("failures")

    def test_ap_seen_by_additive_sta_api_is_preserved_and_deferred(self):
        self.run_fixture("race")

    def test_initialized_worker_defers_recovery_and_power_changes_during_ap(self):
        self.run_fixture("recovery")
        self.run_fixture("companion_recovery")

    def test_first_join_and_reconnect_restore_transmit_power_from_saved_mode(self):
        self.run_fixture("startup_power")

    def test_startup_cannot_leave_a_previous_low_transmit_power_limit(self):
        source = self.fixture()
        start = source.index("void MQTTBridge::beginWiFiStation(")
        end = source.index("bool MQTTBridge::initializeWiFiInTask(", start)
        stale = source[:start] + source[start:end].replace(
            "  mesh::wifi::applyStationPowerSave(mode);", "", 1) + source[end:]
        self.assertNotEqual(stale, source)
        with self.assertRaises(FixtureFailure):
            self.run_fixture("startup_power", stale)

    def test_reverted_ap_guard_drops_the_owned_access_point(self):
        source = self.fixture()
        begin = source.index("  if (!mqttStationWiFiMutationAllowed()) return false;",
                             source.index("bool MQTTBridge::initializeWiFiInTask("))
        end = source.index('  MQTT_DEBUG_PRINTLN("Initializing WiFi in MQTT task...");', begin)
        reverted = source[:begin] + "  WiFi.mode(WIFI_STA);\n" + source[end:]
        with self.assertRaises(FixtureFailure):
            self.run_fixture("ap", reverted)

    def test_reverted_task_wait_runs_slots_before_ap_teardown(self):
        source = self.fixture()
        start = source.index("  while (!_stop_requested && !initializeWiFiInTask())")
        end = source.index("  // Wait a bit for WiFi to start connecting", start)
        reverted = source[:start] + "  initializeWiFiInTask();\n" + source[end:]
        with self.assertRaises(FixtureFailure):
            self.run_fixture("handoff", reverted)

    def test_reverted_sta_only_change_drops_a_concurrently_started_ap(self):
        source = self.fixture().replace("if (!WiFi.enableSTA(true)) return false;",
            "if (race_ap) sdk_mode |= WIFI_AP;\n  if (!WiFi.mode(WIFI_STA)) return false;")
        with self.assertRaises(FixtureFailure):
            self.run_fixture("race", source)

    def test_reverted_stop_aware_wait_delays_shutdown_acknowledgement(self):
        source = self.fixture().replace("  while (!_stop_requested) {", "  while (true) {")
        with self.assertRaises(FixtureFailure):
            self.run_fixture("stop_waiting", source)

    def test_reverted_runtime_guard_reconnects_while_ap_owns_the_driver(self):
        source = self.fixture()
        start = source.index("bool MQTTBridge::handleWiFiConnection(")
        before, recovery = source[:start], source[start:]
        reverted = before + recovery.replace(
            "  if (!mqttStationWiFiMutationAllowed()) return false;", "", 1)
        with self.assertRaises(FixtureFailure):
            self.run_fixture("recovery", reverted)


if __name__ == "__main__":
    unittest.main()
