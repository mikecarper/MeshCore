"""Execute all four production ESP32 idle-yield gates with actual conditions."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class ScannerPowerYieldTests(unittest.TestCase):
    def test_each_role_keeps_idle_yield_but_not_live_dual_profile_delay(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        roles = ("companion_radio", "simple_repeater", "simple_room_server", "simple_sensor")
        cases = [(role, []) for role in roles] + [("simple_repeater", ["-DHELTEC_LORA_V4"])]
        for role, flags in cases:
            with self.subTest(role=role, flags=flags), tempfile.TemporaryDirectory(prefix="scanner-power-yield-") as directory:
                source = (ROOT / "examples" / role / "main.cpp").read_text()
                marker = "else if (!morse_timing && !the_mesh.isDualRadioActive()" if role == "companion_radio" else "if (!morse_timing && !can_power_save && !the_mesh.isDualRadioActive()"
                gate = body(source, marker).removeprefix("else ")
                harness = r'''
#include <cassert>
#include <cstdint>
static unsigned yields=0, last_delay_ms=0;
void delay(unsigned ms) { last_delay_ms=ms; ++yields; }
struct Prefs { bool powersaving_enabled=true; } prefs;
struct Mesh {
  bool dual=false, tracker=false;
  Prefs* getNodePrefs() { return &prefs; }
  bool isDualRadioActive() const { return dual; }
  bool isTrackerModeEnabled() const { return tracker; }
} the_mesh;
#if defined(HELTEC_LORA_V4)
struct Board {
  bool usb_host=false;
  bool isUsbHostConnected() { return usb_host; }
} board;
#endif
void run(bool morse_timing=false, bool can_power_save=false) { (void)can_power_save; @GATE@ }
int main() {
  run(); assert(yields==1 && last_delay_ms==1); // USB/logging/button holds keep CPU idle available.
  the_mesh.dual=true; run(); assert(yields==1); // Radio2 scanner: no1ms delay.
  run(true); assert(yields==1); // No precise Morse timing delay either.
  the_mesh.dual=false; run(true); assert(yields==1);
  prefs.powersaving_enabled=false; run(); assert(yields==1);
  @TRACKER@
  prefs.powersaving_enabled=true; the_mesh.tracker=false; the_mesh.dual=false; run(); assert(yields==2);
#if defined(HELTEC_LORA_V4)
  prefs.powersaving_enabled=false;
  board.usb_host=true;
  run(); assert(yields==3 && last_delay_ms==8);
  run(false, true); assert(yields==3); // Let the actual sleep path handle idle.
  run(true); assert(yields==3);
  the_mesh.dual=true; run(); assert(yields==3);
  the_mesh.dual=false; board.usb_host=false; run(); assert(yields==3);
#endif
}
'''.replace("@GATE@", gate).replace("@TRACKER@", "the_mesh.tracker=true; the_mesh.dual=true; run(); assert(yields==1);" if role == "simple_sensor" else "")
                work = Path(directory)
                cpp, binary = work / "test.cpp", work / "test"
                for broken in (False, True):
                    cpp.write_text(harness.replace("!the_mesh.isDualRadioActive()", "true") if broken else harness)
                    compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", *flags, str(cpp), "-o", str(binary)], capture_output=True, text=True)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True)
                    if broken:
                        self.assertNotEqual(ran.returncode, 0, "negative control concealed delayed scanner")
                    else:
                        self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
