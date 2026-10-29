#!/usr/bin/env python3
"""Execute the actual Companion identity-loading branch and its UI transitions."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

IDENTITY_STUB = r'''
#pragma once
#include <stdint.h>
namespace mesh {
struct Identity { uint8_t pub_key[32] = {}; };
struct LocalIdentity : Identity {};
}
'''

HARNESS = r'''
#include <helpers/IdentityGeneration.h>
#include <cassert>
#include <string>
#include <vector>
#define DISPLAY_CLASS TestDisplay
#define MESH_DEBUG_PRINTLN(...) do {} while (0)
#include <helpers/ui/DisplayPowerPolicy.h>
unsigned display_loads = 0;
namespace mesh { namespace ui {
bool loadDisplayPowerSettings(void*, bool, bool wake_on_boot) {
  ++display_loads;
  displayPowerPrefs() = DisplayPowerPrefs(); // No /display_prefs on either boot.
  displayPowerPrefs().wake_on_boot = wake_on_boot;
  return false;
}
struct StartupScreen {
  std::string status;
  std::vector<std::string> phases;
  void set(const char* next) { status = next; phases.push_back(status); }
  void starting() { set("Starting..."); }
  void loadingIdentity() { set("Loading identity"); }
  void generatingKey() { set("Generating key"); }
  void savingIdentity() { set("Saving identity"); }
  static void progress(void*) {}
};
}}
mesh::ui::StartupScreen screen;
struct Board {
  unsigned reboots = 0;
  void reboot() { ++reboots; }
} board;
struct Store {
  bool loaded = true, allowed = true, save_ok = true;
#if defined(NRF52_PLATFORM)
  bool volatile_fs = false;
#endif
  unsigned saves = 0;
  uint8_t prefix = 0x5a;
  bool loadMainIdentity(mesh::LocalIdentity& identity) {
    assert(screen.status == "Loading identity");
    identity.pub_key[0] = prefix;
    return loaded;
  }
  bool canCreateMainIdentity() const { return allowed; }
  void* getPrimaryFS() const { return nullptr; }
#if defined(NRF52_PLATFORM)
  bool isVolatilePrimaryFS() const { return volatile_fs; }
#endif
  bool saveMainIdentity(const mesh::LocalIdentity&) {
    assert(screen.status == "Saving identity");
    assert(mesh::identityGenerationProgress().callback == nullptr);
    ++saves;
    return save_ok;
  }
} store;
unsigned generated = 0;
bool valid_generated_key = true;
mesh::LocalIdentity radio_new_identity() {
  assert(screen.status == "Generating key");
  ++generated;
  mesh::LocalIdentity identity;
  identity.pub_key[0] = valid_generated_key ? 0x42 : 0xff;
  return identity;
}
mesh::LocalIdentity self_id;
static void bootIdentity() {
  Store* _store = &store;
  mesh::ui::StartupScreen* startup_screen = &screen;
  @IDENTITY_BRANCH@
  @DISPLAY_STARTUP@
}
static void reset() {
  screen = {};
  store = {};
  board = {};
  generated = 0;
  display_loads = 0;
  valid_generated_key = true;
  self_id = {};
  mesh::ui::displayPowerPrefs() = mesh::ui::DisplayPowerPrefs();
}
int main() {
  reset();
  store.allowed = false; // A valid saved identity never needs creation permission.
  bootIdentity();
  assert(generated == 0 && store.saves == 0 && board.reboots == 0);
  assert(self_id.pub_key[0] == 0x5a);
  assert((screen.phases == std::vector<std::string>{"Loading identity", "Starting..."}));
  assert(display_loads == 1 && !mesh::ui::displayPowerPrefs().first_install_boot);

  reset();
  store.loaded = false;
  bootIdentity();
  assert(generated == 1 && store.saves == 1 && board.reboots == 0);
  assert((screen.phases == std::vector<std::string>{
      "Loading identity", "Generating key", "Saving identity", "Starting..."}));
  assert(display_loads == 1 && mesh::ui::displayPowerPrefs().first_install_boot);
  assert(mesh::ui::displayPowerPrefs().battery.seconds == 15);
  assert(mesh::ui::displayPowerPrefs().usb.seconds == 15);

  // The newly saved identity retires the exception on the next boot, even
  // when no display preferences have ever been saved.
  store.loaded = true;
  store.prefix = self_id.pub_key[0];
  screen = {};
  bootIdentity();
  assert(generated == 1 && store.saves == 1 && board.reboots == 0);
  assert(display_loads == 2 && !mesh::ui::displayPowerPrefs().first_install_boot);

  reset();
  store.loaded = false;
  store.allowed = false; // Unreadable identity: fail closed, do not replace it.
  bootIdentity();
  assert(generated == 0 && store.saves == 0 && board.reboots == 1);
  assert((screen.phases == std::vector<std::string>{"Loading identity"}));
  assert(display_loads == 0 && !mesh::ui::displayPowerPrefs().first_install_boot);

  reset();
  store.prefix = 0xff; // Existing reserved-prefix policy is unchanged.
  bootIdentity();
  assert(generated == 1 && store.saves == 1 && self_id.pub_key[0] == 0x42);
  assert(display_loads == 1 && !mesh::ui::displayPowerPrefs().first_install_boot);

#if defined(NRF52_PLATFORM)
  reset();
  store.loaded = false;
  store.volatile_fs = true; // Recovery RAM cannot make startup once-only.
  bootIdentity();
  assert(generated == 1 && store.saves == 1 && board.reboots == 0);
  assert(display_loads == 1 && !mesh::ui::displayPowerPrefs().first_install_boot);
#endif

  reset();
  store.loaded = false;
  store.save_ok = false;
  bootIdentity();
  assert(generated == 1 && store.saves == 1 && board.reboots == 1);
  assert(screen.status == "Starting...");
  assert(display_loads == 0 && !mesh::ui::displayPowerPrefs().first_install_boot);

  reset();
  store.loaded = false;
  valid_generated_key = false;
  bootIdentity();
  assert(generated == mesh::MAX_LOCAL_IDENTITY_GENERATION_ATTEMPTS);
  assert(store.saves == 0 && board.reboots == 1 && screen.status == "Starting...");
  assert(mesh::identityGenerationProgress().callback == nullptr);
  assert(display_loads == 0 && !mesh::ui::displayPowerPrefs().first_install_boot);
}
'''


class CompanionIdentityStartupTests(unittest.TestCase):
    def test_saved_missing_unreadable_and_failed_identity_transitions(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        begin = source.index("void MyMesh::begin(")
        start = source.index("#ifdef DISPLAY_CLASS\n  if (startup_screen != nullptr) startup_screen->loadingIdentity();", begin)
        end = source.index("#if defined(ENABLE_OTA)\n  mesh::ota::ota_refresh_seeder_identity", start)
        program = HARNESS.replace("@IDENTITY_BRANCH@", source[start:end])
        display_start = source.index("  mesh::ui::loadDisplayPowerSettings(", end)
        display_end = source.index("\n#if COMPANION_FEATURE_NOTIFICATIONS", display_start)
        program = program.replace("@DISPLAY_STARTUP@", source[display_start:display_end])
        with tempfile.TemporaryDirectory(prefix="mesh-identity-startup-") as temp:
            cpp, binary = Path(temp) / "test.cpp", Path(temp) / "test"
            (Path(temp) / "Identity.h").write_text(IDENTITY_STUB)
            cpp.write_text(program)
            for definitions in ([], ["-DNRF52_PLATFORM"]):
                with self.subTest(definitions=definitions):
                    result = subprocess.run([
                        "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                        *definitions,
                        "-I", temp, "-I", str(ROOT / "src"), str(cpp), "-o", str(binary),
                    ], text=True, capture_output=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
