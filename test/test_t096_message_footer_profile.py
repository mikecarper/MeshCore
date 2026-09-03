#!/usr/bin/env python3

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "variants" / "heltec_t096" / "platformio.ini"
V4_PROFILE = ROOT / "variants" / "heltec_v4" / "platformio.ini"
UI = ROOT / "examples" / "companion_radio" / "ui-new" / "UITask.cpp"
DISPLAY_FLAGS = ROOT / "src" / "helpers" / "ui" / "DisplayBuildFlags.h"


class T096MessageFooterProfileTest(unittest.TestCase):
    def preprocessor_macros(self, display_class):
        result = subprocess.run(
            [
                "c++",
                "-E",
                "-dM",
                "-x",
                "c++",
                f"-DDISPLAY_CLASS={display_class}",
                "-include",
                str(DISPLAY_FLAGS),
                "-",
            ],
            input="",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_t096_disables_noninteractive_message_channel_footer(self):
        profile = PROFILE.read_text(encoding="utf-8")
        base_profile = profile.split("[Heltec_t096]", 1)[1].split(
            "[env:", 1
        )[0]
        build_flags = base_profile.split("build_flags =", 1)[1].split(
            "build_src_filter =", 1
        )[0]
        active_flags = {
            line.strip()
            for line in build_flags.splitlines()
            if line.strip() and not line.lstrip().startswith(";")
        }
        self.assertIn("-D UI_MESSAGE_CHANNEL_FOOTER=0", active_flags)

    def test_compact_display_drivers_disable_the_footer_by_default(self):
        flags = DISPLAY_FLAGS.read_text(encoding="utf-8")
        for display_class in (
            "SH1106Display",
            "SSD1306Display",
            "ST7735Display",
            "U8g2Display",
        ):
            self.assertIn(
                f"#define MESHCORE_SMALL_DISPLAY_CLASS_{display_class} 1",
                flags,
            )
        for display_class in (
            "E213Display",
            "E290Display",
            "GxEPDDisplay",
            "NV3001BDisplay",
            "SCIndicatorDisplay",
            "ST7789Display",
            "ST7789LCDDisplay",
        ):
            self.assertNotIn(
                f"MESHCORE_SMALL_DISPLAY_CLASS_{display_class}", flags
            )
        self.assertIn("#define MESHCORE_HAS_SMALL_DISPLAY 1", flags)

        ui = UI.read_text(encoding="utf-8")
        self.assertIn(
            "#ifndef UI_MESSAGE_CHANNEL_FOOTER\n"
            "  #if defined(MESHCORE_HAS_SMALL_DISPLAY)\n"
            "    #define UI_MESSAGE_CHANNEL_FOOTER 0\n"
            "  #else\n"
            "    #define UI_MESSAGE_CHANNEL_FOOTER 1\n"
            "  #endif\n"
            "#endif",
            ui,
        )
        footer_start = ui.index(
            "  void renderChannelFilter(DisplayDriver& display) const {"
        )
        footer_end = ui.index("\n  }", footer_start)
        footer = ui[footer_start:footer_end]
        enabled, disabled = footer.split("#else", 1)
        disabled = disabled.split("#endif", 1)[0]
        self.assertIn("#if UI_MESSAGE_CHANNEL_FOOTER == 1", enabled)
        self.assertIn("display.fillRect", enabled)
        self.assertIn("display.drawTextCentered", enabled)
        self.assertNotIn("display.", disabled)
        self.assertIn("(void)display;", disabled)

    def test_small_display_token_classification_preprocesses_exactly(self):
        for display_class in (
            "SH1106Display",
            "SSD1306Display",
            "ST7735Display",
            "U8g2Display",
        ):
            macros = self.preprocessor_macros(display_class)
            self.assertIn("#define MESHCORE_HAS_REAL_DISPLAY 1", macros)
            self.assertIn("#define MESHCORE_HAS_SMALL_DISPLAY 1", macros)

        for display_class in (
            "E213Display",
            "E290Display",
            "GxEPDDisplay",
            "NV3001BDisplay",
            "SCIndicatorDisplay",
            "ST7789Display",
            "ST7789LCDDisplay",
        ):
            macros = self.preprocessor_macros(display_class)
            self.assertIn("#define MESHCORE_HAS_REAL_DISPLAY 1", macros)
            self.assertNotIn("MESHCORE_HAS_SMALL_DISPLAY", macros)

        null_macros = self.preprocessor_macros("NullDisplayDriver")
        self.assertNotIn("MESHCORE_HAS_REAL_DISPLAY", null_macros)
        self.assertNotIn("MESHCORE_HAS_SMALL_DISPLAY", null_macros)

    def test_v4_oled_is_compact_but_v4_tft_is_not(self):
        profile = V4_PROFILE.read_text(encoding="utf-8")
        usb = profile.split(
            "[env:heltec_v4_companion_radio_usb_femon]", 1
        )[1].split("[", 1)[0]
        tft_usb = profile.split(
            "[env:heltec_v4_tft_companion_radio_usb_femon]", 1
        )[1].split("[", 1)[0]
        self.assertIn("extends = heltec_v4_oled", usb)
        self.assertIn("DISPLAY_CLASS=SSD1306Display", usb)
        self.assertIn("extends = heltec_v4_tft", tft_usb)
        self.assertIn("DISPLAY_CLASS=ST7789LCDDisplay", tft_usb)


if __name__ == "__main__":
    unittest.main()
