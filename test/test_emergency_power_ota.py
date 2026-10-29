"""Exercise the real role guards and OTA work hold without allocating OTA storage."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]


class EmergencyOtaTests(unittest.TestCase):
    def test_actual_three_role_guards_active_transfers_and_bounded_server_hold(self):
        enum_source = (ROOT / "src/helpers/ota/OtaManager.h").read_text(encoding="ascii")
        enum = extract_braced(enum_source, "enum FetchState : uint8_t") + ";"
        prefix = r'''
#include <helpers/EmergencyOtaWork.h>
#include <cassert>
#include <cstdint>
#include <initializer_list>
static uint32_t nowMs;
static uint32_t millis() { return nowMs; }
namespace mesh { namespace ota {
struct Manager {
  @ENUM@
  FetchState state = IDLE;
  uint8_t serveJobs = 0, manifestJobs = 0;
  uint32_t retryMs = 5000;
  FetchState fetchState() const { return state; }
  uint8_t pendingServeJobs() const { return serveJobs; }
  uint8_t pendingManifestJobs() const { return manifestJobs; }
  uint32_t fetchRetryTimeoutMs() const { return retryMs; }
};
struct Context {
  Manager manager;
  bool sdFetching = false;
  bool serving = true;
  uint32_t stagedSize = 100000;
  bool sdCacheFetching() const { return sdFetching; }
};
static Context* active = nullptr;
static Context* ota_context_if_active() { return active; }
Context& ota_ctx() = delete; // Merely checking power must never allocate a dynamic context.
}}
struct CLI {
  bool radio = false, gpio = false;
  bool hasPendingRadioSettings() const { return radio; }
  bool hasActiveUserGpioTimer() const { return gpio; }
};
struct Peer {
  bool waiting = false;
  bool awaitingPeer() const { return waiting; }
};
'''.replace("@ENUM@", enum)
        for index, path in enumerate(("examples/simple_repeater/MyMesh.h",
                                      "examples/simple_room_server/MyMesh.h",
                                      "examples/simple_sensor/SensorMesh.h")):
            method = extract_braced((ROOT / path).read_text(encoding="ascii"),
                                    "bool emergencyPowerBusy() const")
            prefix += r'''
struct Role@INDEX@ {
  mesh::power::EmergencyOtaWork _emergency_ota_work;
  CLI _cli;
  Peer direct_ota_admin;
  bool apply = false, saved_radio_apply_pending = false, temp_radio_applied = false;
  uint32_t dirty_contacts_expiry = 0, _ota_update_at = 0;
  bool hasPendingOtaApply() const { return apply; }
  @METHOD@
};
'''.replace("@INDEX@", str(index)).replace("@METHOD@", method)
        prefix += r'''
template <typename Role> void exercise() {
  using namespace mesh::ota;
  Role role;
  active = nullptr; nowMs = 0;
  assert(!role.emergencyPowerBusy());
  role.apply = true; assert(role.emergencyPowerBusy()); role.apply = false;
  role._cli.radio = true; assert(role.emergencyPowerBusy()); role._cli.radio = false;
  role._cli.gpio = true; assert(role.emergencyPowerBusy()); role._cli.gpio = false;
  role.dirty_contacts_expiry = 1; assert(role.emergencyPowerBusy()); role.dirty_contacts_expiry = 0;
  role.saved_radio_apply_pending = true;
  assert(role.emergencyPowerBusy());
  role.temp_radio_applied = true; assert(!role.emergencyPowerBusy());
  role.saved_radio_apply_pending = role.temp_radio_applied = false;
#ifdef ENABLE_OTA
  Context context; active = &context;
  for (auto state : {Manager::IDLE, Manager::COMPLETE, Manager::FAILED, Manager::PAUSED}) {
    context.manager.state = state;
    assert(!role.emergencyPowerBusy()); // Existing images, idle serving and paused sessions are not activity.
  }
  for (auto state : {Manager::WANT_MANIFEST, Manager::WANT_LEAVES,
                     Manager::VERIFYING_STAGED, Manager::FETCHING}) {
    context.manager.state = state;
    assert(role.emergencyPowerBusy());
    context.manager.state = Manager::COMPLETE;
    assert(!role.emergencyPowerBusy());
  }
#ifdef OTA_SD_STORE
  context.sdFetching = true;
  assert(role.emergencyPowerBusy()); // Let serviceSdCache finalize/reset its completed receive owner.
  context.sdFetching = false; assert(!role.emergencyPowerBusy());
#endif
  context.manager.serveJobs = 1;
  assert(role.emergencyPowerBusy()); // Activity at millis()==0 must arm a real hold.
  context.manager.serveJobs = 0; nowMs = 29999;
  assert(role.emergencyPowerBusy());
  nowMs = 30000; assert(!role.emergencyPowerBusy());
  nowMs = 0; assert(!role.emergencyPowerBusy()); // Retired hold cannot resurrect after wrap.
  context.manager.manifestJobs = 1; context.manager.retryMs = 60000;
  nowMs = 0xfffffff0U;
  assert(role.emergencyPowerBusy());
  nowMs += 100;
  assert(role.emergencyPowerBusy()); // Actual queued manifest work renews the hold.
  context.manager.manifestJobs = 0;
  nowMs += 59999; assert(role.emergencyPowerBusy());
  ++nowMs; assert(!role.emergencyPowerBusy());
  context.manager.serveJobs = 1;
  assert(role.emergencyPowerBusy());
  context.manager.serveJobs = 0; active = nullptr;
  nowMs += 60000; assert(!role.emergencyPowerBusy()); // Released dynamic context is never reacquired.
#endif
}
int main() {
  exercise<Role0>(); exercise<Role1>(); exercise<Role2>();
  mesh::ota::active = nullptr;
  Role0 repeater; Role1 room;
  repeater._ota_update_at = 1; room._ota_update_at = 1;
  assert(repeater.emergencyPowerBusy() && room.emergencyPowerBusy());
  repeater._ota_update_at = 0;
#if MESH_ENABLE_DIRECT_OTA_ADMIN
  repeater.direct_ota_admin.waiting = true; assert(repeater.emergencyPowerBusy());
#endif
}
'''
        for flags in (("-DENABLE_OTA", "-DOTA_SD_STORE", "-DMESH_ENABLE_DIRECT_OTA_ADMIN=1"),
                      ("-DENABLE_OTA", "-DMESH_ENABLE_DIRECT_OTA_ADMIN=0"),
                      ("-DMESH_ENABLE_DIRECT_OTA_ADMIN=0",)):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory(prefix="emergency-ota-") as folder:
                source, binary = Path(folder) / "main.cpp", Path(folder) / "check"
                source.write_text(prefix, encoding="ascii")
                result = subprocess.run([
                    os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-function", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                    "-fno-omit-frame-pointer", "-fno-pie", "-no-pie", "-DMESH_ENABLE_EMERGENCY_POWER=1",
                    *flags, "-I", str(ROOT / "src"), str(source), "-o", str(binary)],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
