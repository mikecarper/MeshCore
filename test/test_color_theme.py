#!/usr/bin/env python3
"""Host checks for the default colour-display theme contract."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def rgb565(red: int, green: int, blue: int) -> int:
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


def relative_luminance(color: tuple[int, int, int]) -> float:
    def linear(channel: int) -> float:
        value = channel / 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in color)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    lighter, darker = sorted((relative_luminance(left), relative_luminance(right)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class ColorThemeTest(unittest.TestCase):
    def test_dark_palette_has_contrasting_semantic_slots(self) -> None:
        source = (ROOT / "src/helpers/ui/ColorTheme.h").read_text()
        expected = {
            "WINDOW_BACKGROUND": rgb565(10, 12, 16),
            "TITLE_BACKGROUND": rgb565(18, 38, 74),
            "TEXT": rgb565(232, 238, 246),
            "SECONDARY_TEXT": rgb565(122, 134, 150),
            "WARNING_TEXT": rgb565(248, 176, 72),
            "POPUP_BACKGROUND": rgb565(20, 48, 70),
            "ACCENT": rgb565(64, 176, 240),
        }
        for name, value in expected.items():
            match = re.search(
                rf"constexpr ColorVal {name} = rgb565\((\d+), (\d+), (\d+)\);",
                source,
            )
            self.assertIsNotNone(match, name)
            self.assertEqual(value, rgb565(*(int(part) for part in match.groups())))
        self.assertNotEqual(expected["WINDOW_BACKGROUND"], expected["TEXT"])
        self.assertNotEqual(expected["TITLE_BACKGROUND"], expected["POPUP_BACKGROUND"])
        background = (10, 12, 16)
        self.assertGreaterEqual(contrast(background, (232, 238, 246)), 7.0)
        self.assertGreaterEqual(contrast(background, (122, 134, 150)), 4.5)
        self.assertGreaterEqual(contrast(background, (248, 176, 72)), 4.5)
        self.assertGreaterEqual(contrast(background, (64, 176, 240)), 7.0)

    def test_every_light_color_driver_uses_shared_dark_default(self) -> None:
        for relative in (
            "src/helpers/ui/LGFXDisplay.cpp",
            "src/helpers/ui/NV3001BDisplay.cpp",
            "src/helpers/ui/ST7789LCDDisplay.cpp",
        ):
            source = (ROOT / relative).read_text()
            self.assertIn('#include "ColorTheme.h"', source, relative)
            self.assertIn(
                "UIColor::window_bkg = mesh::ui::color_theme::WINDOW_BACKGROUND;",
                source,
                relative,
            )
            self.assertIn(
                "UIColor::primary_txt = mesh::ui::color_theme::TEXT;",
                source,
                relative,
            )

    def test_indexed_indicator_palette_maps_background_to_dark_entry(self) -> None:
        source = (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text()
        self.assertIn(
            "if (color == UIColor::window_bkg) return INDEX_BACKGROUND;", source
        )
        palette_start = source.index("static const uint32_t UI_PALETTE[16]")
        palette_end = source.index("};", palette_start)
        palette = source[palette_start:palette_end]
        semantic_order = (
            "WINDOW_BACKGROUND",
            "TEXT",
            "TITLE_BACKGROUND",
            "SECONDARY_TEXT",
            "WARNING_TEXT",
            "POPUP_BACKGROUND",
            "ACCENT",
        )
        positions = [
            palette.index(f"rgb888(mesh::ui::color_theme::{name})")
            for name in semantic_order
        ]
        self.assertEqual(positions, sorted(positions))

    def test_indicator_qr_avoids_transparent_palette_slot(self) -> None:
        source = (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text()
        start = source.index("bool LGFXDisplay::drawQrCode")
        end = source.index("uint16_t LGFXDisplay::getTextWidth", start)
        qr = source[start:end]
        self.assertIn("renderColor(UIColor::primary_txt)", qr)
        self.assertIn("renderColor(UIColor::window_bkg)", qr)
        self.assertNotIn("TRANSPARENT_EMOJI_PALETTE_INDEX", qr)
        self.assertNotIn("buffer.qrcode", qr)

    def test_pagination_uses_readable_accent_on_color_displays(self) -> None:
        source = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        branch = re.search(
            r"if \(UIColor::title_bkg == UIColor::window_bkg\) \{"
            r".*?setColor\(UIColor::title_txt\);.*?\} else \{"
            r".*?setColor\(UIColor::corp_blue\);",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(branch)

    def test_monochrome_and_eink_palettes_are_not_inverted(self) -> None:
        contracts = {
            "src/helpers/ui/SSD1306Display.cpp": (
                "UIColor::window_bkg = SSD1306_BLACK;",
                "UIColor::primary_txt = SSD1306_WHITE;",
            ),
            "src/helpers/ui/SH1106Display.cpp": (
                "UIColor::window_bkg = SH110X_BLACK;",
                "UIColor::primary_txt = SH110X_WHITE;",
            ),
            "src/helpers/ui/GxEPDDisplay.cpp": (
                "UIColor::window_bkg = GxEPD_WHITE;",
                "UIColor::primary_txt = GxEPD_BLACK;",
            ),
            "src/helpers/ui/E213Display.cpp": (
                "UIColor::window_bkg = WHITE;",
                "UIColor::primary_txt = BLACK;",
            ),
            "src/helpers/ui/E290Display.cpp": (
                "UIColor::window_bkg = WHITE;",
                "UIColor::primary_txt = BLACK;",
            ),
        }
        for relative, required in contracts.items():
            source = (ROOT / relative).read_text()
            for line in required:
                self.assertIn(line, source, relative)

    def test_color_hardware_profiles_select_a_dark_default_driver(self) -> None:
        contracts = {
            "variants/sensecap_indicator-espnow/platformio.ini": "LGFXDisplay.cpp",
            "variants/heltec_rc32/platformio.ini": "NV3001BDisplay.cpp",
            "variants/heltec_v4/platformio.ini": "ST7789LCDDisplay.cpp",
            "variants/heltec_v4_r8/platformio.ini": "ST7789LCDDisplay.cpp",
            "variants/lilygo_tdeck/platformio.ini": "ST7789LCDDisplay.cpp",
            # These colour families were dark already and remain so.
            "variants/heltec_tracker/platformio.ini": "ST7735Display.cpp",
            "variants/heltec_tracker_v2/platformio.ini": "ST7735Display.cpp",
            "variants/heltec_t096/platformio.ini": "ST7735Display.cpp",
            "variants/heltec_t1/platformio.ini": "ST7735Display.cpp",
        }
        for relative, driver in contracts.items():
            source = (ROOT / relative).read_text()
            self.assertIn(driver, source, relative)


if __name__ == "__main__":
    unittest.main()
