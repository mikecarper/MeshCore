"""Execute the X1 battery sampling method without hardware or PlatformIO.

The existing board contract drives its dedicated divider gate HIGH to sample
and LOW at shutdown. Verify balanced sampling without changing that polarity,
the existing 10 ms settling interval, or the global ADC defaults.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "variants/meshtracker_x1/MeshTrackerX1Board.h"


def sampling_source(mutation=None):
    method = extract_braced(HEADER.read_text(), "uint16_t getBattMilliVolts() override")
    if mutation == "gate-left-powered":
        method = method.replace("digitalWrite(PIN_BAT_ADC_EN, LOW);", "// negative control")
    if mutation == "invalid-return-before-cleanup":
        method = method.replace("const int raw = analogRead(BATTERY_PIN);",
                                "const int raw = analogRead(BATTERY_PIN);\n"
                                "    if (raw < 0 || raw > 4095) return 0;")
    method = method.replace(" override", "")
    return r'''
#include <cassert>
#include <cstdint>
#include <string>
#include <vector>
#define LOW 0
#define HIGH 1
#define AR_DEFAULT 10
#define AR_INTERNAL_3_0 30
#define ADC_MULTIPLIER 2.0f
#define AREF_VOLTAGE 3.0f
struct Event { std::string name; int value; };
static std::vector<Event> events;
static int level=LOW,reference=AR_DEFAULT,resolution=10,raw=0;
void digitalWrite(int pin,int value) {
  assert(pin==38);level=value;events.push_back({"gate",value});
}
void analogReference(int value) {
  reference=value;events.push_back({"reference",value});
}
void analogReadResolution(int value) {
  resolution=value;events.push_back({"resolution",value});
}
void delay(unsigned ms) {
  assert(ms==10);events.push_back({"settle",static_cast<int>(ms)});
}
int analogRead(int pin) {
  assert(pin==2 && reference==AR_INTERNAL_3_0 && resolution==12);
#ifdef PIN_BAT_ADC_EN
  assert(level==HIGH);
#endif
  assert(!events.empty() && events.back().name=="settle");
  events.push_back({"sample",raw});return raw;
}
class Board { public: @METHOD@ };
int main() {
  Board board;
#ifdef BATTERY_PIN
  for(int value:{0,1,2048,2731,3000,4095,-1,4096,65535}) {
    events.clear();raw=value;level=LOW;reference=AR_DEFAULT;resolution=10;
    const uint16_t result=board.getBattMilliVolts();
    assert(reference==AR_DEFAULT && resolution==10);
    const uint16_t expected=(value<0 || value>4095)?0:
        static_cast<uint16_t>((value*ADC_MULTIPLIER*AREF_VOLTAGE)/4096*1000);
    assert(result==expected);
#ifdef PIN_BAT_ADC_EN
    assert(level==LOW && events.size()==8);
    assert(events[0].name=="gate" && events[0].value==HIGH);
    assert(events[5].name=="gate" && events[5].value==LOW);
#else
    assert(events.size()==6);
    for(const Event& event:events) assert(event.name!="gate");
#endif
    assert(events[events.size()-2].name=="reference");
    assert(events.back().name=="resolution");
  }
#else
  assert(board.getBattMilliVolts()==0 && events.empty());
#endif
}
'''.replace("@METHOD@", method)


class MeshTrackerX1BatteryTests(unittest.TestCase):
    def run_case(self, defines, mutation=None, expected_success=True):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++11 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshtracker-x1-battery-") as directory:
            cpp = Path(directory) / "test.cpp"
            binary = Path(directory) / ("test.exe" if os.name == "nt" else "test")
            cpp.write_text(sampling_source(mutation), encoding="utf-8")
            sanitizer = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            build = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                *sanitizer, *["-D"+flag for flag in defines], str(cpp), "-o", str(binary)],
                text=True, capture_output=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stdout+build.stderr)
            run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
            if expected_success:
                self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            else:
                self.assertNotEqual(run.returncode, 0, "negative control unexpectedly passed")

    def test_balanced_gate_valid_invalid_repeated_samples_and_adc_defaults(self):
        self.run_case(("BATTERY_PIN=2", "PIN_BAT_ADC_EN=38"))

    def test_optional_gate_absent_still_preserves_adc_defaults(self):
        self.run_case(("BATTERY_PIN=2",))

    def test_no_battery_pin_has_no_adc_or_gpio_side_effects(self):
        self.run_case(())

    def test_old_always_powered_gate_negative_control(self):
        self.run_case(("BATTERY_PIN=2", "PIN_BAT_ADC_EN=38"),
                      mutation="gate-left-powered", expected_success=False)

    def test_invalid_adc_early_return_negative_control(self):
        self.run_case(("BATTERY_PIN=2", "PIN_BAT_ADC_EN=38"),
                      mutation="invalid-return-before-cleanup", expected_success=False)


if __name__ == "__main__":
    unittest.main()
