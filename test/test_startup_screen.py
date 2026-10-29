#!/usr/bin/env python3
"""Startup ordering and cooperative radio entropy/display integration."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class StartupScreenTest(unittest.TestCase):
    def test_infrastructure_first_install_window_follows_durable_identity_and_final_prefs(self):
        flags = []
        for role in ("simple_repeater", "simple_room_server", "simple_sensor"):
            source = (ROOT / "examples" / role / "main.cpp").read_text()
            setup = extract_braced(source, "void setup()")
            assignment = setup.index("displayPowerPrefs().first_install_boot")
            start = setup.rfind("#if defined(NRF52_PLATFORM)", 0, assignment)
            end = setup.index("\n#endif", assignment) + len("\n#endif")
            with self.subTest(role=role):
                self.assertLess(setup.index("store.saveWithRetry("), assignment)
                self.assertLess(setup.index("if (!identity_ready)"), assignment)
                self.assertLess(setup.index("the_mesh.begin(fs)"), assignment)
                self.assertLess(assignment, setup.index("ui_task.begin("))
                self.assertTrue(setup[:start].endswith("#ifdef DISPLAY_CLASS\n"))
            # Execute the actual conditional assignment, including the real
            # volatile-filesystem guard, for each infrastructure role.
            flags.append("void arm_" + role + r'''(IdentityLoadResult identity_load,
                                                   bool volatile_primary_fs) {
  (void)identity_load;
  (void)volatile_primary_fs;
#ifdef DISPLAY_CLASS
''' + setup[start:end] + "\n#endif\n}\n")
        program = r'''
#include <helpers/ui/DisplayPowerPolicy.h>
#include <cassert>
#include <initializer_list>
enum class IdentityLoadResult { Missing, Loaded, Unreadable };
@FLAGS@
int main() {
  for (auto arm : {arm_simple_repeater, arm_simple_room_server, arm_simple_sensor}) {
    for (auto identity : {IdentityLoadResult::Missing, IdentityLoadResult::Loaded,
                          IdentityLoadResult::Unreadable}) {
      for (bool volatile_fs : {false, true}) {
        mesh::ui::displayPowerPrefs() = mesh::ui::DisplayPowerPrefs();
        arm(identity, volatile_fs);
        bool expected = false;
#ifdef DISPLAY_CLASS
        expected = identity == IdentityLoadResult::Missing;
#if defined(NRF52_PLATFORM)
        expected = expected && !volatile_fs;
#endif
#endif
        assert(mesh::ui::displayPowerPrefs().first_install_boot == expected);
      }
    }
  }
}
'''.replace("@FLAGS@", "\n".join(flags))
        with tempfile.TemporaryDirectory(prefix="mesh-first-install-flags-") as directory:
            cpp, exe = Path(directory) / "test.cpp", Path(directory) / "test"
            cpp.write_text(program, encoding="ascii")
            for definitions in ([], ["NRF52_PLATFORM"], ["DISPLAY_CLASS=TestDisplay"],
                                ["NRF52_PLATFORM", "DISPLAY_CLASS=TestDisplay"]):
                with self.subTest(definitions=definitions):
                    built = subprocess.run([
                        "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        *["-D" + flag for flag in definitions],
                        "-I", str(ROOT / "src"), str(cpp), "-o", str(exe)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    checked = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_screen_precedes_radio_keygen_and_settle_delays(self):
        for role in ("companion_radio", "simple_repeater", "simple_room_server", "simple_sensor"):
            with self.subTest(role=role):
                main = (ROOT / "examples" / role / "main.cpp").read_text()
                setup = extract_braced(main, "void setup()")
                if role == "companion_radio":
                    # A failed required PSRAM allocation never reaches normal
                    # startup. Keep its terminal error loop out of the checks
                    # for delays on the successful display/radio startup path.
                    failure = extract_braced(
                        setup, "if (!initializeCompanionMeshStorage())")
                    body = failure[failure.index("{") + 1:-1]
                    self.assertEqual(re.sub(r"//[^\n]*|\s+", "", body),
                                     "for(;;)delay(1000);")
                    setup = setup.replace(failure, "", 1)
                frame = setup.index("startup_screen.begin(")
                self.assertLess(setup.index("board.begin()"), frame)
                self.assertLess(setup.index("loadDisplayPowerSettings("), frame)
                self.assertLess(setup.index("display.begin()"), frame)
                self.assertLess(frame, setup.index("radio_init()"))
                self.assertLess(frame, setup.index("the_mesh.begin("))
                for delay in ("delay(1000)", "delay(5000)"):
                    if delay in setup:
                        self.assertLess(frame, setup.index(delay))
                for mount in ("SPIFFS.begin(true)", "LittleFS.begin()", "beginInternalPrimaryFilesystemSafely("):
                    if mount in setup:
                        self.assertLess(setup.index(mount), frame)
                identity_source = setup
                if role == "companion_radio":
                    mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
                    identity_source = extract_braced(mesh, "void MyMesh::begin(")
                    self.assertIn(", &startup_screen", setup)
                    self.assertIn("&& !store.isVolatilePrimaryFS()) display_prefs_fs = nullptr;", setup)
                self.assertLess(identity_source.index("generatingKey()"),
                                identity_source.index("generateUsableLocalIdentity("))
                self.assertLess(identity_source.index("ScopedIdentityGenerationProgress progress("),
                                identity_source.index("generateUsableLocalIdentity("))
                self.assertLess(identity_source.index("generateUsableLocalIdentity("),
                                identity_source.index("startup_screen" + ("->" if role == "companion_radio" else ".") + "starting()"))
                save_call = "_store->saveMainIdentity(" if role == "companion_radio" else "store.saveWithRetry("
                self.assertLess(identity_source.index("savingIdentity()"),
                                identity_source.index(save_call))
                if role == "companion_radio":
                    self.assertLess(identity_source.index("loadingIdentity()"),
                                    identity_source.index("_store->loadMainIdentity("))

    def test_actual_entropy_loop_services_progress_without_changing_bytes(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.h").read_text()
        noise_listener = extract_braced(source, "class RadioNoiseListener") + ";\n"
        program = r'''
#include <helpers/IdentityGeneration.h>
#include <cassert>
#include <vector>
static unsigned random_calls = 0, progress_calls = 0;
static long random(long low, long high) {
  assert(low == 0 && high == 256);
  ++random_calls;
  return 0x55;
}
class PhysicalLayer {
public:
  unsigned calls = 0;
  uint8_t randomByte() { return uint8_t(++calls); }
};
namespace mesh {
static unsigned hardware_calls = 0;
static void mixCC310Random(uint8_t* bytes, size_t size) {
  ++hardware_calls;
  for (size_t i = 0; i < size; ++i) bytes[i] ^= 0x22;
}
static void mixESP32TrueRandom(uint8_t* bytes, size_t size) {
  ++hardware_calls;
  for (size_t i = 0; i < size; ++i) bytes[i] ^= 0x11;
}
}
@LISTENER@
int main() {
  PhysicalLayer radio;
  RadioNoiseListener rng(radio);
  uint8_t seed[32];
  {
    mesh::ScopedIdentityGenerationProgress progress([](void* context) {
      auto& radio = *static_cast<PhysicalLayer*>(context);
      ++progress_calls;
      // This happens between bytes, before the RNG has returned.
      assert(progress_calls == radio.calls && radio.calls <= 32);
    }, &radio);
    rng.random(seed, sizeof(seed));
  }
  assert(random_calls == 32 && radio.calls == 32 && progress_calls == 32);
  for (size_t i = 0; i < sizeof(seed); ++i) {
    uint8_t expected = uint8_t(i + 1) ^ 0x55;
#ifdef USE_CC310_HW_CRYPTO
    expected ^= 0x22;
#endif
#ifdef ESP32_PLATFORM
    expected ^= 0x11;
#endif
    assert(seed[i] == expected);
  }
  assert(mesh::identityGenerationProgress().callback == nullptr);
  rng.random(seed, sizeof(seed)); // No stale UI callback after key generation.
  assert(progress_calls == 32 && radio.calls == 64);
}
'''.replace("@LISTENER@", noise_listener)
        with tempfile.TemporaryDirectory(prefix="mesh-startup-entropy-") as directory:
            cpp, exe = Path(directory) / "test.cpp", Path(directory) / "test"
            cpp.write_text(program)
            for defines in ([], ["ESP32_PLATFORM"], ["USE_CC310_HW_CRYPTO"],
                            ["ESP32_PLATFORM", "USE_CC310_HW_CRYPTO"]):
                with self.subTest(defines=defines):
                    build = subprocess.run([
                        "c++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-function", "-Wno-unused-variable",
                        "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                        *["-D" + flag for flag in defines],
                        "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                        str(cpp), "-o", str(exe)], capture_output=True, text=True)
                    self.assertEqual(build.returncode, 0, build.stderr)
                    run = subprocess.run([str(exe)], capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
