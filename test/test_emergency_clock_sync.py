#!/usr/bin/env python3
"""Run the production clock schedulers against emergency mode transitions."""
from pathlib import Path
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]

MESH = r'''
#pragma once
#include <cstdint>
struct FILESYSTEM {};
namespace mesh {
class Identity {};
class Packet {
public:
  bool flood = true;
  uint8_t path_id[8] = {};
  bool isRouteFlood() const { return flood; }
};
class Radio {
public:
  uint32_t received = 0;
  uint32_t getLastRecvMillis() const { return received; }
};
class MillisecondClock {
public:
  uint32_t now = 0;
  uint32_t getMillis() const { return now; }
};
class RTCClock {
public:
  uint32_t epoch = 1800000000;
  unsigned writes = 0, resets = 0;
  uint32_t getCurrentTime() const { return epoch; }
  void setCurrentTime(uint32_t value) { epoch = value; ++writes; }
  void resetUniqueTime(uint32_t) { ++resets; }
};
}
'''

ACL = r'''
#pragma once
#include <cstdint>
struct ClientInfo { uint32_t last_activity = 0; };
class ClientACL {
public:
  ClientInfo client;
  int getNumClients() const { return 1; }
  ClientInfo* getClientByIdx(int) { return &client; }
};
'''

SENSORS = r'''
#pragma once
class LocationProvider {
public:
  bool applied = false;
  bool consumeTimeSyncApplied() { bool result = applied; applied = false; return result; }
};
class SensorManager {
public:
  LocationProvider location;
  LocationProvider* getLocationProvider() { return &location; }
};
'''


def production_methods(source, signatures):
    return "\n\n".join(extract_braced(source, signature) for signature in signatures)


class EmergencyClockSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-emergency-clock-")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        (work / "helpers").mkdir()
        (work / "Mesh.h").write_text(MESH, encoding="ascii")
        (work / "helpers/ClientACL.h").write_text(ACL, encoding="ascii")
        (work / "helpers/SensorManager.h").write_text(SENSORS, encoding="ascii")
        (work / "helpers/IdentityStore.h").write_text("#pragma once\n", encoding="ascii")

        shared = (ROOT / "src/helpers/MeshClockSync.cpp").read_text(encoding="ascii")
        repeater = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="utf-8")
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text(encoding="utf-8")
        fixture = (ROOT / "test/fixtures/emergency_clock_sync.cpp").read_text(encoding="ascii")
        # Use production constants and the actual shared class declaration; the
        # fixtures replace only hardware, transport timing, and unrelated UI.
        fixture = fixture.replace("@REPEATER_DEFINES@", "\n".join(re.findall(
            r"^\s*#define CLOCK_SYNC_[^\n]+", header, re.MULTILINE)))
        fixture = fixture.replace("@REPEATER_ENUMS@", ";\n".join(
            extract_braced(repeater, signature) for signature in (
                "enum ClockSyncSource :", "enum ClockSyncResult :")) + ";")
        fixture = fixture.replace("@REPEATER_SAMPLE@", extract_braced(
            header, "struct ClockSyncSample {") + ";")
        fields = header[header.index("  ClockSyncSample clock_sync_samples["):
                        header.index("  bool region_load_active;")]
        fixture = fixture.replace("@REPEATER_FIELDS@", fields)
        fixture = fixture.replace("@REPEATER_METHODS@", production_methods(repeater, (
            "void MyMesh::resetClockSyncAttempt(",
            "void MyMesh::suppressMeshClockSyncForBoot(",
            "void MyMesh::onManualClockSet(",
            "void MyMesh::checkGpsClockSyncOverride(",
            "uint64_t MyMesh::nextClockSyncAttemptUptime(",
            "bool MyMesh::isClockSyncCollectionActive(",
            "void MyMesh::recordClockSyncSample(",
            "bool MyMesh::estimateMeshClock(",
            "static uint32_t rebaseClockTimestamp(",
            "bool MyMesh::applyClockEstimate(",
            "void MyMesh::checkClockSync(",
            "void MyMesh::formatClockSyncStatus(")))
        constants = [re.search(r"^\s*#define " + name + r"[^\n]+", shared,
                               re.MULTILINE).group() for name in (
                                   "MESH_CLOCK_SYNC_REQUIRED_SAMPLES_DEFAULT",
                                   "MESH_CLOCK_SYNC_STARTUP_DELAY_MILLIS")]
        for name in ("REQUIRED_SAMPLES_DEFAULT", "STARTUP_DELAY_MILLIS", "RETRY_INTERVAL_MILLIS",
                     "SAMPLE_MAX_AGE_MILLIS", "CONSENSUS_WINDOW_SECONDS"):
            constants.append(re.search(
                r"constexpr [^;\n]+ " + name + r"\s*=[^;]+;", shared).group())
        fixture = fixture.replace("@SHARED_CONSTANTS@", "\n".join(constants))
        fixture = fixture.replace("@SHARED_METHODS@", production_methods(shared, (
            "MeshClockSync::MeshClockSync(",
            "void MeshClockSync::resetDefaults(",
            "void MeshClockSync::resetAttempt(",
            "void MeshClockSync::suppressForBoot(",
            "void MeshClockSync::onManualClockSet(",
            "void MeshClockSync::onInternetClockSet(",
            "void MeshClockSync::checkGpsOverride(",
            "uint64_t MeshClockSync::nextAttemptUptime(",
            "bool MeshClockSync::collectionActive(",
            "void MeshClockSync::recordSample(",
            "bool MeshClockSync::estimate(",
            "uint32_t rebaseTimestamp(",
            "bool MeshClockSync::applyEstimate(",
            "void MeshClockSync::checkClock(",
            "void MeshClockSync::loop(",
            "void MeshClockSync::formatStatus(")))
        for role, path in (("ROOM", "examples/simple_room_server/MyMesh.h"),
                           ("SENSOR", "examples/simple_sensor/SensorMesh.h")):
            role_header = (ROOT / path).read_text(encoding="utf-8")
            fixture = fixture.replace("@" + role + "_CALLBACK@", extract_braced(
                role_header, "bool clockSyncEmergencyActive() const override"))
        if re.search(r"@[A-Z_]+@", fixture):
            raise AssertionError("unexpanded fixture placeholder")
        source = work / "clock.cpp"
        source.write_text(fixture, encoding="ascii")
        cls.binary = work / "clock"
        command = shlex.split(os.environ.get("CXX", "g++")) + [
            "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
            "-DWITH_MQTT_BRIDGE", "-DMAX_NEIGHBOURS=0",
            *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []),
            "-I", str(work), "-I", str(ROOT / "src"), str(source), "-o", str(cls.binary)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if built.returncode:
            raise RuntimeError(built.stdout + built.stderr)

    def scenario(self, name, roles=("repeater", "room", "sensor")):
        for role in roles:
            with self.subTest(role=role):
                result = subprocess.run([str(self.binary), role, name],
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_emergency_entry_exit_and_each_success_preserve_the_correct_anchor(self):
        self.scenario("transitions")

    def test_collection_due_check_and_status_use_the_same_effective_deadline(self):
        self.scenario("consumers")

    def test_bootstrap_retry_and_new_quorum_keep_their_existing_timers(self):
        self.scenario("retry")

    def test_emergency_rechecks_still_require_fresh_consensus_and_apply_drift_test(self):
        self.scenario("consensus")

    def test_cli_gps_internet_authority_and_disabled_mesh_remain_respected(self):
        self.scenario("authority")

    def test_internet_success_keeps_seven_days_and_mesh_recheck_keeps_internet_priority(self):
        self.scenario("internet", roles=("repeater",))

    def test_default_or_absent_callbacks_keep_normal_cadence(self):
        self.scenario("callbacks", roles=("room",))

    def test_emergency_sleep_windows_remain_fifteen_minutes_awake_and_forty_five_asleep(self):
        self.scenario("sleep")


if __name__ == "__main__":
    unittest.main()
