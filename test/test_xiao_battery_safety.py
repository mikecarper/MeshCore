"""Run the real XIAO/Ikoka startup and shutdown GPIO sequences.

Seeed warns against READ_BAT_ENABLE HIGH while charging. Start with an unsafe
latched level so enabling the output before preloading LOW is observable too.
The battery divider is about 3 uA, not a reason to overvoltage the ADC input.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
BOARDS = (
    ("xiao_nrf52", "XiaoNrf52Board"),
    ("ikoka_stick_nrf", "IkokaStickNRFBoard"),
    ("ikoka_nano_nrf", "IkokaNanoNRFBoard"),
    ("ikoka_handheld_nrf", "IkokaNrf52Board"),
)


def source(mutation=None):
    methods = []
    calls = []
    for variant, board in BOARDS:
        directory = ROOT / "variants" / variant
        init = extract_braced((directory / "variant.cpp").read_text(), "void initVariant()")
        init = init.replace("initVariant()", "init_" + board + "()", 1)
        begin = extract_braced((directory / (board + ".cpp")).read_text(),
                               "void " + board + "::begin()")
        if mutation == "old-begin-high" and variant != "xiao_nrf52":
            begin = begin.replace("digitalWrite(VBAT_ENABLE, LOW);",
                                  "digitalWrite(VBAT_ENABLE, HIGH);")
        if mutation == "output-before-safe-latch":
            init = init.replace("digitalWrite(VBAT_ENABLE, LOW);\n", "")
            init = init.replace("pinMode(VBAT_ENABLE, OUTPUT);",
                                "pinMode(VBAT_ENABLE, OUTPUT);\n"
                                "digitalWrite(VBAT_ENABLE, LOW);")
        methods += [init, "class " + board + " { public: void begin(); };", begin]
        calls += ["reset(); init_" + board + "(); check();",
                  "reset(); " + board + "().begin(); check();",
                  "reset(); init_" + board + "(); " + board + "().begin(); check();"]
    shutdown = extract_braced((ROOT / "variants/xiao_nrf52/XiaoNrf52Board.cpp").read_text(),
                               "void XiaoNrf52Board::initiateShutdown(uint8_t reason)")
    shutdown = shutdown.replace("XiaoNrf52Board::initiateShutdown", "shutdown")
    if mutation == "old-user-shutdown-high":
        shutdown = shutdown.replace("digitalWrite(VBAT_ENABLE, LOW);",
                                    "digitalWrite(VBAT_ENABLE, enable_lpcomp ? LOW : HIGH);")
    return r'''
#include <cassert>
#include <cstdint>
#define LOW 0
#define HIGH 1
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define VBAT_ENABLE 14
#define PIN_VBAT 31
#define PIN_CHARGING_CURRENT 13
#define PIN_QSPI_CS 25
#define LED_RED 26
#define LED_GREEN 27
#define LED_BLUE 28
#define AR_INTERNAL_3_0 30
#define SHUTDOWN_REASON_LOW_VOLTAGE 1
#define SHUTDOWN_REASON_BOOT_PROTECT 2
#define SHUTDOWN_REASON_USER 3
#ifdef OPTIONAL_PINS
#define PIN_USER_BTN 3
#define PIN_WIRE_SDA 4
#define PIN_WIRE_SCL 5
#define P_LORA_TX_LED 6
#define DISPLAY_CLASS FakeDisplay
#define DISPLAY_ROTATION 0
#endif
static int latch=HIGH, mode=INPUT, writes=0, wakes=0, reason_seen=0;
void digitalWrite(int pin, int value) {
  if(pin==VBAT_ENABLE) { assert(value==LOW); latch=value; ++writes; }
}
void pinMode(int pin,int value) {
  if(pin==VBAT_ENABLE) {
    assert(value==OUTPUT && latch==LOW); mode=value;
  }
}
void delay(unsigned) {}
void analogReadResolution(int) {}
void analogReference(int) {}
struct FakeWire { void setPins(int,int) {} void begin() {} } Wire;
struct NRF52Board { static void begin() {} };
struct NRF52BoardDCDC { static void begin() {} };
struct Config { int lpcomp_ain_channel; int lpcomp_refsel; };
static const Config power_config={7,3};
void checkBootVoltage(const Config*) {}
void configureVoltageWake(int channel,int reference) {
  assert(channel==7 && reference==3 && latch==LOW && mode==OUTPUT); ++wakes;
}
void enterSystemOff(uint8_t reason) {
  assert(latch==LOW && mode==OUTPUT); reason_seen=reason;
}
void reset() { latch=HIGH; mode=INPUT; writes=wakes=reason_seen=0; }
void check() { assert(writes>0 && latch==LOW && mode==OUTPUT); }
@METHODS@
@SHUTDOWN@
int main() {
  // Repeated startup, optional peripherals and USB/charging status must not
  // create a HIGH pulse; the production code deliberately does not infer USB.
  for(int repeat=0;repeat<3;++repeat) { @CALLS@ }
  for(uint8_t reason : {SHUTDOWN_REASON_LOW_VOLTAGE, SHUTDOWN_REASON_BOOT_PROTECT,
                       SHUTDOWN_REASON_USER, 0}) {
    reset(); shutdown(reason); check();
    assert(reason_seen==reason);
    assert(wakes==(reason==SHUTDOWN_REASON_LOW_VOLTAGE ||
                   reason==SHUTDOWN_REASON_BOOT_PROTECT ? 1 : 0));
  }
}
'''.replace("#include <cstdint>", "#include <cstdint>\n#include <initializer_list>").replace(
        "@METHODS@", "\n".join(methods)).replace("@SHUTDOWN@", shutdown).replace(
        "@CALLS@", "\n".join(calls))


class XiaoBatterySafetyTests(unittest.TestCase):
    def run_case(self, optional=False, mutation=None):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++11 compiler is required")
        with tempfile.TemporaryDirectory(prefix="xiao-battery-safety-") as directory:
            cpp = Path(directory) / "test.cpp"
            binary = Path(directory) / ("test.exe" if os.name == "nt" else "test")
            cpp.write_text(source(mutation), encoding="utf-8")
            sanitizer = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            flags = ["-DNRF52_POWER_MANAGEMENT"]
            if optional:
                flags.append("-DOPTIONAL_PINS")
            build = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                *sanitizer, *flags, str(cpp), "-o", str(binary)],
                text=True, capture_output=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
            if mutation:
                self.assertNotEqual(run.returncode, 0, "negative control unexpectedly passed")
            else:
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_startup_and_all_shutdown_reasons(self):
        self.run_case()

    def test_optional_button_i2c_display_led_startup(self):
        self.run_case(optional=True)

    def test_old_ikoka_high_startup_is_rejected(self):
        self.run_case(mutation="old-begin-high")

    def test_output_before_safe_latch_is_rejected(self):
        self.run_case(mutation="output-before-safe-latch")

    def test_old_user_shutdown_high_is_rejected(self):
        self.run_case(mutation="old-user-shutdown-high")


if __name__ == "__main__":
    unittest.main()
