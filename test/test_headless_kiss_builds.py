"""Headless KISS compatibility across optional R8 and Wio L1 displays."""
import configparser
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PAIRS = (
    ("heltec_v4_r8_kiss_modem", "heltec_v4_r8_tft_kiss_modem"),
    ("WioTrackerL1_kiss_modem", "WioTrackerL1Eink_kiss_modem"),
)


class HeadlessKissBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configparser.ConfigParser(interpolation=None, strict=False,
                                              inline_comment_prefixes=(";",))
        cls.config.read([ROOT / "platformio.ini",
                         ROOT / "variants/heltec_v4_r8/platformio.ini",
                         ROOT / "variants/wio-tracker-l1/platformio.ini",
                         ROOT / "variants/wio-tracker-l1-eink/platformio.ini"])

    def option(self, section, key):
        options = self.config[section]
        if key not in options:
            for parent in options.get("extends", "").split(","):
                if parent.strip():
                    value = self.option(parent.strip(), key)
                    if value is not None:
                        return value
            return None
        return re.sub(r"\$\{([^}.]+)\.([^}]+)\}",
                      lambda m: self.option(m[1], m[2]) or "", options[key])

    def flags(self, name):
        return dict((m[1], m[2] or "1") for m in re.finditer(
            r"-D\s*([A-Za-z_][A-Za-z0-9_]*)(?:=([^\s]+))?",
            self.option("env:" + name, "build_flags")))

    def test_both_historical_names_remain_buildable_without_a_display(self):
        for pair in PAIRS:
            for name in pair:
                with self.subTest(name=name):
                    flags = self.flags(name)
                    self.assertEqual(flags["MESHCORE_HEADLESS_KISS_COMPAT"], "1")
                    self.assertEqual(flags["RADIO_CLASS"], "CustomSX1262")
                    self.assertNotIn("DISPLAY_CLASS", flags)
                    sources = self.option("env:" + name, "build_src_filter")
                    self.assertIn("+<../examples/kiss_modem/>", sources)
                    self.assertNotIn("../examples/companion_radio", sources)

    def test_pairs_keep_identical_radio_settings_and_boot_layouts(self):
        for shared, legacy in PAIRS:
            with self.subTest(shared=shared):
                shared_flags, legacy_flags = self.flags(shared), self.flags(legacy)
                for key in ("USE_SX1262", "RADIO_CLASS", "WRAPPER_CLASS", "LORA_TX_POWER",
                            "P_LORA_NSS", "P_LORA_RESET", "P_LORA_BUSY", "P_LORA_DIO_1",
                            "P_LORA_SCLK", "P_LORA_MOSI", "P_LORA_MISO",
                            "SX126X_DIO3_TCXO_VOLTAGE", "SX126X_CURRENT_LIMIT",
                            "ENV_INCLUDE_GPS", "ENV_PIN_SDA", "ENV_PIN_SCL"):
                    self.assertEqual(shared_flags.get(key), legacy_flags.get(key), key)
                for key in ("board", "board_build.ldscript", "board_upload.maximum_size"):
                    self.assertEqual(self.option("env:" + shared, key),
                                     self.option("env:" + legacy, key), key)

    def test_capability_evidence_is_retained_from_the_board_startup(self):
        for variant, board, kind, call in (
            ("heltec_v4_r8", "HeltecV4R8Board", "heltec_r8", "parkHeltecR8KissDisplayPins"),
            ("wio-tracker-l1", "WioTrackerL1Board", "wio_l1", "parkWioL1KissDisplayPins"),
        ):
            with self.subTest(variant=variant):
                source = (ROOT / "variants" / variant / (board + ".cpp")).read_text()
                self.assertIn("meshcore.capability.modem.headless." + kind + ".v1", source)
                begin = source.split("void " + board + "::begin()", 1)[1].split("\n}", 1)[0]
                self.assertIn("const volatile char* capability", begin)
                self.assertIn("(void)*capability", begin)
                self.assertIn(call + "();", begin)

    def test_real_pin_parking_deselects_displays_without_touching_radio(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="headless-kiss-pins-") as directory:
            work = Path(directory)
            (work / "WVariant.h").write_text("#pragma once\n", encoding="ascii")
            (work / "Arduino.h").write_text(r'''
#pragma once
#include <cassert>
#include <map>
#include <vector>
constexpr int LOW = 0, HIGH = 1, OUTPUT = 2;
struct Event { int pin, level; bool output; };
inline std::vector<Event> events;
inline std::map<int, int> levels;
inline void digitalWrite(int pin, int level) {
  events.push_back({pin, level, false}); levels[pin] = level;
}
inline void pinMode(int pin, int mode) {
  assert(mode == OUTPUT);
  assert(levels.count(pin));  // The intended level must be loaded first.
  events.push_back({pin, levels.at(pin), true});
}
''', encoding="ascii")
            source = work / "pins.cpp"
            source.write_text(r'''
#include <Arduino.h>
#include "variants/wio-tracker-l1/variant.h"
#include "variants/heltec_v4_r8/HeadlessKissPins.h"
#include "variants/wio-tracker-l1/HeadlessKissPins.h"
void check(const std::map<int, int>& expected) {
  assert(levels == expected);
  assert(events.size() == expected.size() * 2);
  for (std::size_t i = 0; i < events.size(); i += 2) {
    assert(!events[i].output && events[i+1].output);
    assert(events[i].pin == events[i+1].pin);
    assert(events[i].level == events[i+1].level);
  }
  events.clear(); levels.clear();
}
int main() {
  for (int pass = 0; pass < 2; ++pass) {
    parkHeltecR8KissDisplayPins();
    check({{44, LOW}, {47, HIGH}, {3, HIGH}, {21, LOW}});
    parkWioL1KissDisplayPins();
    check({{36, HIGH}, {32, LOW}});
  }
}
''', encoding="ascii")
            executable = work / ("pins.exe" if os.name == "nt" else "pins")
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                     "-I" + str(work), "-I" + str(ROOT),
                                     str(source), "-o", str(executable)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
