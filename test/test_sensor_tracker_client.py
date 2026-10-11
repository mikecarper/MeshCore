#!/usr/bin/env python3
"""Execute the actual Sensor tracker with real wire, route and durable storage."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_client_acl_infrastructure import room_mail_crypto_arguments
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures"
SANITIZERS = (["-fsanitize=address,undefined,float-cast-overflow", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])


def peer_methods(source):
    methods = ""
    for signature in ("int SensorMesh::searchPeersByHash(", "void SensorMesh::getPeerSharedSecret(",
                      "bool SensorMesh::allowPacketForward(", "void SensorMesh::sendSelfAdvertisement("):
        methods += extract_braced(source, signature) + "\n"
    # Compile the complete actual authenticated tracker dispatch, including its
    # full-owner fallback and ACL bounds. Ordinary application handling beyond
    # that boundary is represented by a counter, rather than a second tracker.
    for signature, ending in (("void SensorMesh::onPeerDataRecv(", "++ordinary_data;\n}"),
                              ("bool SensorMesh::onPeerPathRecv(", "++ordinary_paths; return false;\n}")):
        function = extract_braced(source, signature)
        boundary = "  ClientInfo* from = acl.getClientByIdx(i);"
        if boundary not in function:
            raise AssertionError("ordinary ACL callback boundary changed")
        methods += function[:function.index(boundary)] + ending + "\n"
    return methods


class SensorTrackerClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if cls.compiler is None:
            raise unittest.SkipTest("a host C++ compiler is required")
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-tracker-client-")
        cls.work = Path(cls.temp.name)
        cls.tracker = (ROOT / "examples/simple_sensor/Tracker.cpp").read_text(encoding="ascii")
        cls.sensor = (ROOT / "examples/simple_sensor/SensorMesh.cpp").read_text(encoding="ascii")
        cls.crypto = room_mail_crypto_arguments("room")
        cls.binary = cls.build("production", cls.tracker, cls.sensor)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def build(cls, name, tracker, sensor):
        work = cls.work / name
        work.mkdir()
        (work / "SensorMesh.h").write_text('#include "sensor_tracker_client.h"\n', encoding="ascii")
        (work / "helpers").mkdir()
        # The production watchdog header imports hardware identity/filesystem
        # dependencies. Its sole Tracker API is supplied by this radio adapter.
        (work / "helpers/UsbLoggingWatchdog.h").write_text(
            "#pragma once\nnamespace mesh { bool isUsbLoggingWatchdogArmed(); }\n",
            encoding="ascii")
        (work / "Tracker.cpp").write_text(tracker, encoding="ascii")
        fixture = (FIXTURE / "sensor_tracker_client.cpp").read_text(encoding="ascii")
        cpp = work / "client.cpp"
        cpp.write_text(fixture.replace("@PEER_METHODS@", peer_methods(sensor)), encoding="ascii")
        binary = work / "client"
        built = subprocess.run([
            cls.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
            *SANITIZERS, "-I", str(work), "-I", str(FIXTURE), "-I", str(ROOT / "src"),
            str(cpp), str(ROOT / "src/Packet.cpp"), *cls.crypto, "-o", str(binary),
        ], text=True, capture_output=True, timeout=60)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)
        return binary

    def run_group(self, name, binary=None, success=True):
        run = subprocess.run([str(binary or self.binary), name], text=True, capture_output=True, timeout=20)
        if success:
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("PASS: sensor tracker client " + name, run.stdout)
        else:
            self.assertNotEqual(run.returncode, 0, "regression mutation escaped the assertions")
            self.assertIn("Assertion", run.stderr)

    def test_local_commands_and_durable_settings(self):
        self.run_group("commands")

    def test_safe_lost_unknown_intervals_and_awake_lease(self):
        self.run_group("cadence")

    def test_reply_window_starts_after_transmission_and_wraps(self):
        self.run_group("timing")

    def test_full_owner_authentication_and_nonce_isolation(self):
        self.run_group("identity")

    def test_learn_paths_only_from_accepted_matched_responses(self):
        self.run_group("paths")

    def test_tracker_suppresses_normal_adverts_alerts_and_forwarding(self):
        self.run_group("suppression")

    def test_six_hour_flood_budget_repeater_postponement_and_four_day_lock(self):
        self.run_group("floods")

    def test_storage_faults_and_rebooted_reservation_never_flood(self):
        self.run_group("durability")

    def test_queue_and_inflight_cancellation_ownership(self):
        self.run_group("ownership")

    def test_gps_timeout_invalid_fixes_and_maintenance_awake_guards(self):
        self.run_group("gps")

    def test_reboot_preserves_nonce_floor_and_conservative_flood_wait(self):
        self.run_group("reboot")

    def test_idle_clock_rollback_and_response_crossing_four_day_cutoff(self):
        self.run_group("clock-cutoff")

    def test_failed_warm_and_hard_wakes_restore_dispatcher_liveness(self):
        self.run_group("wake")

    def test_zero_rtc_allows_gps_sync_without_radio_or_storage_fault(self):
        self.run_group("zero-clock")

    def test_forward_utc_correction_requires_real_six_hour_quiet_wait(self):
        self.run_group("forward-clock")

    def test_gps_manager_owns_acquisition_completion_and_final_fix(self):
        self.run_group("gps-handoff")

    def test_negative_control_original_normal_traffic_is_detected(self):
        guard = "  if (isTrackerModeEnabled()) return false;"
        advert = "  if (isTrackerModeEnabled()) return;"
        forward_function = extract_braced(self.sensor, "bool SensorMesh::allowPacketForward(")
        advert_function = extract_braced(self.sensor, "void SensorMesh::sendSelfAdvertisement(")
        self.assertEqual(forward_function.count(guard), 1)
        self.assertEqual(advert_function.count(advert), 1)
        mutated = self.sensor.replace(forward_function, forward_function.replace(guard, "", 1), 1)
        mutated = mutated.replace(advert_function, advert_function.replace(advert, "", 1), 1)
        self.run_group("suppression", self.build("no-suppression", self.tracker, mutated), success=False)

    def test_negative_control_missing_durability_gate_is_detected(self):
        guard = "if (!saveTrackerRecord(candidate)) return; // Reserve before any RF admission."
        self.assertEqual(self.tracker.count(guard), 1)
        mutated = self.tracker.replace(guard, "saveTrackerRecord(candidate);", 1)
        self.run_group("durability", self.build("no-durability", mutated, self.sensor), success=False)

    def test_negative_control_failed_wake_leaves_dispatcher_disabled(self):
        guard = "if (!_radio->setTrackerSleep(false)) _radio->recoverRadio(true);"
        self.assertEqual(self.tracker.count(guard), 1)
        mutated = self.tracker.replace(guard, "if (!_radio->setTrackerSleep(false)) return;", 1)
        self.run_group("wake", self.build("failed-wake-stuck", mutated, self.sensor), success=False)

    def test_negative_control_zero_clock_allocates_invalid_nonce(self):
        guard = extract_braced(self.tracker, "if (now == 0) {")
        mutated = self.tracker.replace(guard, "", 1)
        self.run_group("zero-clock", self.build("zero-clock-nonce", mutated, self.sensor), success=False)

    def test_negative_control_forward_clock_can_manufacture_flood_wait(self):
        guard = "    tracker_boot_anchor = now;"
        self.assertEqual(self.tracker.count(guard), 1)
        mutated = self.tracker.replace(guard, "", 1)
        self.run_group("forward-clock", self.build("forward-clock-flood", mutated, self.sensor), success=False)

    def test_negative_control_tracker_forces_early_manager_handoff(self):
        guard = "if (sensors.isTrackerGpsAcquisitionPending()) return;"
        self.assertEqual(self.tracker.count(guard), 1)
        early = "if (sensors.isTrackerGpsAcquisitionPending() && !millisHasNowPassed(sensors.acquisition_until)) return;"
        mutated = self.tracker.replace(guard, early, 1)
        self.run_group("gps-handoff", self.build("early-gps-handoff", mutated, self.sensor), success=False)


if __name__ == "__main__":
    unittest.main()
