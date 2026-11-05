#!/usr/bin/env python3
"""Execute Companion's actual pending reload and five-minute station recovery."""
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


class CompanionAccessPointOwnershipTest(unittest.TestCase):
    def fixture(self):
        main = (ROOT / "examples/companion_radio/main.cpp").read_text(encoding="ascii")
        reload = method(main, "  static void serviceCompanionWiFiCredentialReload(")
        start_wifi = method(main, "  static void startCompanionWiFi(")
        power_guards = "\n".join(method(main, signature) for signature in (
            "  static bool companionWiFiAutomaticRecoveryAllowed(",
        ))
        auto_webconfig = method(main, "  static void startCompanionWiFiAutoWebConfig(")
        start = main.index("  const unsigned long wifi_now = millis();")
        end = main.index("#ifdef WITH_MQTT_BRIDGE", start)
        station_loop = main[start:end]
        policy = (ROOT / "src/helpers/esp32/WiFiRadioPolicy.h").read_text(encoding="ascii")
        guard = method(policy, "inline bool stationMutationAllowed(")
        fixture = (ROOT / "test/fixtures/companion_ap_ownership.cpp").read_text(encoding="ascii")
        return (fixture.replace("@STATION_GUARD@", guard).replace("@RELOAD@", reload)
                .replace("@POWER_GUARDS@", power_guards)
                .replace("@AUTO_WEBCONFIG@", auto_webconfig)
                .replace("@START_WIFI@", start_wifi).replace("@STATION_LOOP@", station_loop))

    def run_fixture(self, scenario, source=None, webconfig=True):
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="companion-ap-ownership-") as directory:
            path = Path(directory)
            (path / "test.cpp").write_text(source or self.fixture(), encoding="ascii")
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DCOMPANION_RADIO_FULL=1", "-DENABLE_OTA=1",
                *(["-DWITH_WEBCONFIG=1"] if webconfig else []),
                "-I", str(ROOT / "src"), str(path / "test.cpp"), "-o", str(path / "test")],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(path / "test"), scenario],
                capture_output=True, text=True, timeout=15)
            if result.returncode:
                raise FixtureFailure(result.stdout + result.stderr)

    def test_due_saved_ssid_retry_waits_for_sdk_ap_teardown(self):
        self.run_fixture("retry")
        self.run_fixture("retry", webconfig=False)

    def test_pending_credential_reload_is_retained_until_ap_teardown(self):
        self.run_fixture("reload")
        self.run_fixture("reload", webconfig=False)

    def test_credential_reload_waits_for_webconfig_teardown_after_sdk_ap_is_gone(self):
        self.run_fixture("transition_reload")

    def test_starting_wifi_services_defers_without_consuming_requested_intent(self):
        self.run_fixture("start_wifi")
        self.run_fixture("start_wifi", webconfig=False)

    def test_failed_transport_registration_does_not_start_unserviced_socket(self):
        self.run_fixture("registration")
        self.run_fixture("registration", webconfig=False)

    def test_deliberate_recovery_setup_handoff_remains_available(self):
        self.run_fixture("setup_handoff")
        self.run_fixture("setup_handoff", webconfig=False)
        self.run_fixture("unrelated_setup")

    def test_reverted_retry_guard_removes_the_ota_ap(self):
        source = self.fixture().replace(
            "if (mesh::wifi::stationMutationAllowed(setup_station_handoff)) {", "if (true) {")
        with self.assertRaises(FixtureFailure):
            self.run_fixture("retry", source)

    def test_reverted_reload_guard_consumes_pending_intent_and_removes_ap(self):
        source = self.fixture().replace(
            "    if (!mesh::wifi::stationMutationAllowed()) return;", "")
        with self.assertRaises(FixtureFailure):
            self.run_fixture("reload", source)

    def test_ota_cannot_inherit_setup_handoff_permission(self):
        source = self.fixture().replace("      !board.isOTAUpdateRunning() &&", "")
        with self.assertRaises(FixtureFailure):
            self.run_fixture("setup_handoff", source)

    def test_reverted_wifi_start_guard_removes_existing_ota_ap(self):
        source = self.fixture()
        start = source.index("  static void startCompanionWiFi(")
        before, startup = source[:start], source[start:]
        reverted = before + startup.replace(
            "    if (!mesh::wifi::stationMutationAllowed()) return;", "", 1)
        with self.assertRaises(FixtureFailure):
            self.run_fixture("start_wifi", reverted)


if __name__ == "__main__":
    unittest.main()
