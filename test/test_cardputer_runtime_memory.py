"""Bound custom Cardputer runtime owners and truncated display text."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_firmware_ram import requirements
from test_replay_reset_integration import extract_braced


class CardputerRuntimeMemoryTests(unittest.TestCase):
    def test_full_retains_custom_ui_allowances_and_selected_wireless_stack(self):
        full = {"ESP32_PLATFORM": "1", "BLE_PIN_CODE": "123456",
                "WIFI_SSID": '"WIFI_SSID"', "WIFI_OTA_SEEDER": "1",
                "ENABLE_OTA": "1", "OTA_SEEDER_ONLY": "1",
                "COMPANION_RADIO_FULL": "1", "CARDPUTER_ADV_UI": "1",
                "COMPANION_EXCLUSIVE_WIFI_BLE": "1",
                "MESH_CONTACT_CACHE": "1", "MAX_CONTACTS": "350",
                "MAX_GROUP_CHANNELS": "40", "OFFLINE_QUEUE_SIZE": "256"}
        measured = requirements("ESP32_PLATFORM", full,
                                "M5Stack_Cardputer_Adv_companion_radio_full")
        legacy = requirements("ESP32_PLATFORM", {"ESP32_PLATFORM": "1",
            "BLE_PIN_CODE": "123456", "CARDPUTER_ADV_UI": "1"},
            "M5Stack_Cardputer_Adv_companion_radio_ble")
        for key, value in legacy["components"].items():
            if key.startswith("cardputer_"):
                self.assertEqual(measured["components"][key], value)
        self.assertEqual(measured["components"]["wireless_stacks"], max(49152, 32768))
        self.assertIn("ota_source_scratch", measured["components"])
        self.assertIn("ota_generic_source_buffers", measured["components"])
        self.assertGreater(measured["required_heap_bytes"], legacy["required_heap_bytes"])

    def test_custom_ui_owners_are_additive_and_fonts_fit_their_allowance(self):
        base = {"ESP32_PLATFORM": "1", "BLE_PIN_CODE": "123456"}
        target = "M5Stack_Cardputer_Adv_companion_radio_ble"
        ordinary = requirements("ESP32_PLATFORM", base, target)
        custom = requirements("ESP32_PLATFORM", dict(base, CARDPUTER_ADV_UI="1"), target)
        self.assertEqual(ordinary["components"], {
            key: value for key, value in custom["components"].items()
            if not key.startswith("cardputer_")})
        self.assertGreater(custom["required_heap_bytes"], ordinary["required_heap_bytes"])
        fonts = ROOT / "variants/m5stack_cardputer_adv/companion_radio/ui-cardputer"
        for name in ("font.h", "font_16.h", "font_19.h"):
            data = (fonts / name).read_text().split("{", 1)[1]
            prefix = bytes(int(byte, 16) for byte in re.findall(r"0x([0-9a-fA-F]{2})", data)[:4])
            glyphs = int.from_bytes(prefix, "big")
            self.assertGreater(glyphs, 0)
            self.assertLessEqual(glyphs, 716)
            # Five VLW arrays use 4+2+1+1+1 bytes/glyph. Also allow
            # allocation metadata and the runtime font object itself.
            self.assertLessEqual(glyphs * 9 + 1024,
                                 custom["components"]["cardputer_font_metadata"])
        disabled = requirements("ESP32_PLATFORM", dict(base, CARDPUTER_ADV_UI="0"), target)
        self.assertEqual(disabled, ordinary)
        with self.assertRaisesRegex(ValueError, "ESP32 Companion"):
            requirements("NRF52_PLATFORM", {"CARDPUTER_ADV_UI": "1"}, target)

    def test_scroll_keeps_truncated_utf8_inside_the_input_buffer(self):
        driver = (ROOT / "variants/m5stack_cardputer_adv/hw/CardputerDisplay.cpp").read_text()
        method = extract_braced(driver, "void CardputerDisplay::drawTextLeftAlignWithScroll(")
        prelude = r'''
#include <cassert>
#include <cstring>
class CardputerDisplay {
public:
  const char* begin = nullptr;
  const char* end = nullptr;
  int getTextWidth(const char* text) { return strlen(text); }
  void drawTextLeftAlign(int, int, const char* text) {
    assert(text >= begin && text <= end);
    (void)strlen(text);
  }
  void drawTextLeftAlignWithScroll(int, int, int, const char*);
};
int main() {
  CardputerDisplay display;
  for (unsigned lead : {0xC2U, 0xE2U, 0xF0U}) {
    char text[2] = {static_cast<char>(lead), 0};
    display.begin = text; display.end = text + 1;
    display.drawTextLeftAlignWithScroll(0, 0, 0, text);
  }
}
'''
        prelude = '#include <initializer_list>\n' + prelude
        for legacy in (False, True):
            body = method.replace("      offset = text_len;\n", "") if legacy else method
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                cpp, binary = work / "scroll.cpp", work / "scroll"
                cpp.write_text(prelude + body)
                build = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                    str(cpp), "-o", str(binary)], capture_output=True, text=True)
                self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                run = subprocess.run([str(binary)], capture_output=True, text=True)
                if legacy:
                    self.assertNotEqual(run.returncode, 0, "Unclamped scroll unexpectedly passed")
                else:
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
