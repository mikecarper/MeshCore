"""Optional-SD room Ethernet must use the guarded, opt-in runtime."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_rak4631_ethernet_runtime import FIXTURE, SCENARIOS
from test_rak_sd_receiver import method, run_cpp
from test_serial_wifi_sessions import SANITIZER_FLAGS


ROOT = Path(__file__).resolve().parents[1]


class RakSdRoomEthernetTest(unittest.TestCase):
    def test_actual_role_callbacks_reserve_only_after_storage_release(self):
        source = (ROOT / "examples/simple_room_server/main.cpp").read_text()
        callbacks = "\n".join(method(source, signature) for signature in (
            "static bool prepareSdEthernet(",
            "static bool canReleaseSdEthernet(",
            "static bool releaseSdEthernet(",
        ))
        harness = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include "src/helpers/nrf52/Rak4631SharedSpi.h"
namespace mesh { namespace ota {
static bool fitted = false, prepare_ok = true, release_ok = true;
static unsigned prepares = 0, releases = 0;
struct Context {
  bool prepareRakSdForEthernet() {
    ++prepares;
    assert(!rak4631_ethernet_owns_spi());
    return prepare_ok;
  }
  bool canReleaseRakSdForEthernet() { return release_ok; }
  bool releasedRakSdEthernet() {
    ++releases;
    assert(rak4631_ethernet_owns_spi()); // Keep reservation until callback completes.
    return release_ok;
  }
};
static Context context;
static Context* active = nullptr;
Context* ota_context_if_active() { return active; }
bool ota_rak15002_sd_card_present() { return fitted; }
} }
''' + callbacks + r'''
int main() {
  using namespace mesh::ota;
  char error[160];
  assert(!prepareSdEthernet(nullptr, error, sizeof(error)));
  assert(strstr(error, "not ready") && prepares == 0);
  active = &context;
  fitted = true;
  assert(!prepareSdEthernet(nullptr, error, sizeof(error)));
  assert(strstr(error, "SD card fitted") && prepares == 0);
  fitted = false; prepare_ok = false;
  assert(!prepareSdEthernet(nullptr, error, sizeof(error)));
  assert(rak4631_spi_owner() == RakSharedSpiOwner::None);
  prepare_ok = true;
  assert(rak4631_try_acquire_spi(RakSharedSpiOwner::Nor));
  assert(!prepareSdEthernet(nullptr, error, sizeof(error)));
  assert(rak4631_spi_owner() == RakSharedSpiOwner::Nor); // Never steal uncertain NOR.
  rak4631_release_spi(RakSharedSpiOwner::Nor);
  assert(prepareSdEthernet(nullptr, error, sizeof(error)));
  assert(rak4631_ethernet_owns_spi());
  release_ok = false;
  assert(!canReleaseSdEthernet(nullptr));
  assert(!releaseSdEthernet(nullptr) && rak4631_ethernet_owns_spi());
  active = nullptr;
  assert(!releaseSdEthernet(nullptr) && rak4631_ethernet_owns_spi());
  active = &context; release_ok = true;
  assert(canReleaseSdEthernet(nullptr) && releaseSdEthernet(nullptr));
  assert(rak4631_spi_owner() == RakSharedSpiOwner::None && releases == 2);
}
'''
        run_cpp(harness, ("OTA_RAK15002_SD_STORE",))
        repeater = (ROOT / "examples/simple_repeater/main.cpp").read_text()
        combined = "\n".join(method(repeater, signature) for signature in (
            "static bool prepareCombinedEthernet(",
            "static bool canReleaseCombinedEthernet(",
            "static bool releaseCombinedEthernet(",
        ))
        for original, replacement in (
            ("prepareCombinedEthernet", "prepareSdEthernet"),
            ("canReleaseCombinedEthernet", "canReleaseSdEthernet"),
            ("releaseCombinedEthernet", "releaseSdEthernet"),
        ):
            combined = combined.replace(original, replacement)
        # No legacy NOR hooks exist in this mock. A wrong branch selected
        # under both flags fails compilation before the ownership assertions.
        run_cpp(harness.replace(callbacks, combined),
                ("OTA_RAK15002_SD_STORE", "RAK4631_COMBINED_ETHERNET"))

    def test_actual_runtime_without_legacy_combined_flag(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="rak-sd-room-ethernet-") as directory:
            binary = Path(directory) / "runtime"
            fixture = (FIXTURE / "test.cpp").read_text()
            fixture = fixture.replace("#include <helpers/nrf52/EthernetCLI.h>",
                "static std::atomic<bool> mock_sd_fitted{false};\n"
                "#include <helpers/nrf52/EthernetCLI.h>")
            fixture = fixture.replace("  hooks.context = &context;",
                "  hooks.context = &context;\n"
                "  hooks.storage_conflict = [](void*) { return mock_sd_fitted.load(); };")
            cases = r'''
    case 35: { // Card insertion gates main loop and retains owner until explicit OFF.
      start_online(); accept_connection();
      EthernetSpiAccess expected = EthernetSpiAccess::Idle;
      wait_until([&] { expected = EthernetSpiAccess::Idle;
        return ethernet_spi_access.compare_exchange_strong(expected, EthernetSpiAccess::Client); });
      const int before = hardware_operations.load();
      mock_sd_fitted.store(true);
      ethernet_loop_maintain();
      ethernet_spi_access.store(EthernetSpiAccess::Idle);
      wait_until([] { return ethernet_worker_stopped.load(); });
      assert(ethernet_state.load() == EthernetCliState::Faulted);
      assert(!spi_active.load() && bus_owner.load() && ethernet_get_enabled());
      assert(hardware_operations.load() == before + 1); // SPI.end only: no socket CLOSE/SEND.
      ethernet_loop_maintain();
      assert(bus_owner.load() && context.releases == 0);
      assert(command("eth on").find("off first") != std::string::npos);
      context.off_allowed = false;
      assert(command("eth off").find("locked") != std::string::npos);
      assert(bus_owner.load());
      context.off_allowed = true;
      finish_off();
      break;
    }
    case 36: { // An entered vendor DHCP call completes; no further vendor IO follows insertion.
      Ethernet.begin_blocked.store(true);
      assert(command("eth on").find("starting") != std::string::npos);
      wait_until([] { return Ethernet.begin_count.load() == 1; });
      const int before = hardware_operations.load();
      mock_sd_fitted.store(true);
      unblock(Ethernet.begin_blocked);
      wait_until([] { return ethernet_worker_stopped.load(); });
      assert(hardware_operations.load() == before + 1);
      assert(!spi_active.load() && bus_owner.load());
      finish_off();
      break;
    }
    case 37: { // Retained Stream admission detects insertion before any controller register access.
      start_online(); accept_connection();
      EthernetSpiAccess expected = EthernetSpiAccess::Idle;
      wait_until([&] { expected = EthernetSpiAccess::Idle;
        return ethernet_spi_access.compare_exchange_strong(expected, EthernetSpiAccess::Client); });
      const int before = hardware_operations.load();
      mock_sd_fitted.store(true);
      assert_busy_facade();
      ethernet_spi_access.store(EthernetSpiAccess::Idle);
      wait_until([] { return ethernet_worker_stopped.load(); });
      assert(hardware_operations.load() == before + 1 && bus_owner.load());
      finish_off();
      break;
    }
    case 38: { // Card inserted after reservation but before worker scheduling: no hardware init.
      defer_task_start.store(true);
      assert(command("eth on").find("starting") != std::string::npos);
      mock_sd_fitted.store(true);
      unblock(defer_task_start);
      wait_until([] { return ethernet_worker_stopped.load(); });
      assert(hardware_operations.load() == 0 && !spi_active.load() && bus_owner.load());
      finish_off();
      break;
    }
'''
            fixture = fixture.replace("    default: assert(false);", cases + "    default: assert(false);")
            fixture_path = Path(directory) / "fixture.cpp"
            fixture_path.write_text(fixture, encoding="utf-8")
            compiled = subprocess.run([
                compiler, "-std=c++17", "-pthread", "-Wall", "-Wextra", "-Werror",
                *SANITIZER_FLAGS, "-DETHERNET_ENABLED=1", "-DOTA_RAK15002_SD_STORE=1",
                f"-I{FIXTURE / 'mocks'}", f"-I{ROOT / 'src'}",
                str(fixture_path), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            descriptions = (*SCENARIOS, "online SD insertion", "SD insertion during DHCP",
                            "retained Stream SD insertion", "SD insertion before worker start")
            for scenario, description in enumerate(descriptions):
                with self.subTest(scenario=description):
                    checked = subprocess.run([str(binary), str(scenario)],
                                             capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn(f"PASS: RAK4631 Ethernet runtime scenario {scenario}", checked.stdout)

    def test_room_wires_default_off_after_mesh_begin_and_keeps_renewal_stream(self):
        source = (ROOT / "examples/simple_room_server/main.cpp").read_text()
        setup = method(source, "void setup()")
        configure = setup.index("ethernet_configure(hooks)")
        self.assertLess(setup.index("the_mesh.begin(fs)"), configure)
        self.assertLess(configure, setup.index("ethernet_start_task()"))
        self.assertNotIn("ethernet_configure(hooks, true)", setup)
        for assignment in ("hooks.prepare = prepareSdEthernet",
                           "hooks.can_release = canReleaseSdEthernet",
                           "hooks.released = releaseSdEthernet",
                           "hooks.storage_conflict ="):
            self.assertIn(assignment, setup)
        self.assertIn("#if defined(OTA_RAK15002_SD_STORE)\n"
                      "  if (ethernet_take_session_reset() || !ethernet_get_enabled())", source)


if __name__ == "__main__":
    unittest.main()
