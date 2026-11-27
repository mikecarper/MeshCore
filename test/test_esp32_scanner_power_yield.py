"""Execute all four production ESP32 idle-yield gates with actual conditions."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class ScannerPowerYieldTests(unittest.TestCase):
    def test_v4_paces_housekeeping_without_delaying_dispatch(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        source = (ROOT / "examples/simple_repeater/main.cpp").read_text()
        constants = "\n".join(line for line in source.splitlines() if line.startswith("static constexpr uint32_t V4"))
        start = source.index("  static uint32_t last_housekeeping_at = 0;")
        end = source.index("\n#endif\n\n#if MESH_ENABLE_EMERGENCY_POWER", start)
        harness = r'''
#include <cassert>
#include <cstdint>
@CONSTANTS@
static uint32_t now = 0;
static unsigned housekeeping = 0, radio_polls = 0;
uint32_t millis() { return now; }
void delay(unsigned ms) { assert(ms == V4IdleYieldMs); }
struct Radio {
  bool rx=true, irq=false, rxps=false;
  bool isInRecvMode() const { return rx; }
  bool hasPendingRadioInterrupt() const { return irq; }
  bool isRxPowerSavingEnabled() const { return rxps; }
  void loop() { ++radio_polls; }
} radio_driver;
struct Mesh {
  bool dual=false, work=false, powersaving_enabled=false;
  bool isDualRadioActive() const { return dual; }
  bool hasV4RepeaterWorkDue() const { return work; }
  Mesh* getNodePrefs() { return this; }
} the_mesh;
struct Board {
  bool isUsbDataConnected() const { return false; }
  bool isOTAUpdateRunning() const { return false; }
  bool isRadioTestActive() const { return false; }
} board;
namespace mesh { bool morseTimingActive(Radio*) { return false; } }
void run() {
  @GATE@
  ++housekeeping;
}
int main() {
  now = V4HousekeepingIntervalMs; run(); assert(housekeeping == 1);
  now += V4IdleYieldMs; run(); assert(radio_polls == 1 && housekeeping == 1);
  radio_driver.irq = true; run(); assert(housekeeping == 2); radio_driver.irq = false;
  radio_driver.rx = false; run(); assert(housekeeping == 3); radio_driver.rx = true;
  the_mesh.work = true; run(); assert(housekeeping == 4); the_mesh.work = false;
  the_mesh.dual = true; run(); assert(housekeeping == 5); the_mesh.dual = false;
  radio_driver.rxps = true; run(); assert(housekeeping == 6); radio_driver.rxps = false;
  the_mesh.powersaving_enabled = true; run(); assert(housekeeping == 7);
  the_mesh.powersaving_enabled = false;
  now = 2 * V4HousekeepingIntervalMs; run(); assert(housekeeping == 8 && radio_polls == 1);
}
'''.replace("@CONSTANTS@", constants).replace("@GATE@", source[start:end])
        with tempfile.TemporaryDirectory(prefix="v4-repeater-housekeeping-") as directory:
            cpp, binary = Path(directory) / "test.cpp", Path(directory) / "test"
            cpp.write_text(harness)
            compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", str(cpp),
                                       "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_each_role_keeps_idle_yield_but_not_live_dual_profile_delay(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        roles = ("companion_radio", "simple_repeater", "simple_room_server", "simple_sensor")
        cases = [(role, False) for role in roles] + [("simple_repeater", True)]
        for role, v4 in cases:
            with self.subTest(role=role, v4=v4), tempfile.TemporaryDirectory(prefix="scanner-power-yield-") as directory:
                source = (ROOT / "examples" / role / "main.cpp").read_text()
                marker = "else if (!morse_timing && !the_mesh.isDualRadioActive()" if role == "companion_radio" else "if (!morse_timing && !can_power_save && !the_mesh.isDualRadioActive()"
                if v4:
                    marker = "if (!morse_timing && !single_radio_sleep_requested && !the_mesh.isDualRadioActive()"
                gate = body(source, marker).removeprefix("else ")
                idle_delay = next(
                    line for line in source.splitlines() if line.startswith("static constexpr uint32_t V4IdleYieldMs ")
                ) if v4 else ""
                harness = r'''
#include <cassert>
#include <cstdint>
@IDLE_DELAY@
static unsigned yields=0, last_delay_ms=0;
void delay(unsigned ms) { last_delay_ms=ms; ++yields; }
struct Prefs { bool powersaving_enabled=true; } prefs;
struct Mesh {
  bool dual=false, tracker=false;
  Prefs* getNodePrefs() { return &prefs; }
  bool isDualRadioActive() const { return dual; }
  bool isTrackerModeEnabled() const { return tracker; }
} the_mesh;
void run(bool morse_timing=false, bool can_power_save=false, bool single_radio_sleep_requested=false) {
  (void)can_power_save;
  (void)single_radio_sleep_requested;
  @GATE@
}
#if defined(HELTEC_LORA_V4)
int main() {
  run(); assert(yields==1 && last_delay_ms==8);
  prefs.powersaving_enabled=false; run(); assert(yields==2 && last_delay_ms==8);
  run(false, true); assert(yields==3); // Idle sleep allowed, but no sleep requested yet.
  run(false, true, true); assert(yields==3);
  run(true); assert(yields==3);
  the_mesh.dual=true; run(); assert(yields==3);
}
#else
int main() {
  run(); assert(yields==1 && last_delay_ms==1); // USB/logging/button holds keep CPU idle available.
  the_mesh.dual=true; run(); assert(yields==1); // Radio2 scanner: no1ms delay.
  run(true); assert(yields==1); // No precise Morse timing delay either.
  the_mesh.dual=false; run(true); assert(yields==1);
  prefs.powersaving_enabled=false; run(); assert(yields==1);
  @TRACKER@
  prefs.powersaving_enabled=true; the_mesh.tracker=false; the_mesh.dual=false; run(); assert(yields==2);
}
#endif
'''.replace("@IDLE_DELAY@", idle_delay).replace("@GATE@", gate).replace("@TRACKER@", "the_mesh.tracker=true; the_mesh.dual=true; run(); assert(yields==1);" if role == "simple_sensor" else "")
                work = Path(directory)
                cpp, binary = work / "test.cpp", work / "test"
                for broken in (False, True):
                    cpp.write_text(harness.replace("!the_mesh.isDualRadioActive()", "true") if broken else harness)
                    flags = ["-DHELTEC_LORA_V4"] if v4 else []
                    compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", *flags, str(cpp), "-o", str(binary)], capture_output=True, text=True)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True)
                    if broken:
                        self.assertNotEqual(ran.returncode, 0, "negative control concealed delayed scanner")
                    else:
                        self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
