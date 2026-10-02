#!/usr/bin/env python3
"""Startup ordering and cooperative radio entropy/display integration."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class StartupScreenTest(unittest.TestCase):
    def test_screen_precedes_radio_keygen_and_settle_delays(self):
        for role in ("companion_radio", "simple_repeater", "simple_room_server", "simple_sensor"):
            with self.subTest(role=role):
                main = (ROOT / "examples" / role / "main.cpp").read_text()
                setup = extract_braced(main, "void setup()")
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
