#!/usr/bin/env python3

import re
import struct
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "variants" / "sensecap_indicator-espnow" / "platformio.ini"
DISPLAY = ROOT / "src" / "helpers" / "ui" / "LGFXDisplay.cpp"
FONT = ROOT / "variants" / "sensecap_indicator-espnow" / "sd" / "ui-font.vlw"


def base_profile() -> str:
    text = PROFILE.read_text()
    match = re.search(
        r"^\[SenseCapIndicator-ESPNow\]\n(?P<body>.*?)(?=^\[)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError("SenseCap Indicator base profile is missing")
    return match.group("body")


def numeric_flag(name: str) -> float:
    match = re.search(
        rf"^\s*-D\s+{re.escape(name)}=(\S+)\s*$",
        base_profile(),
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError(f"{name} is missing from the Indicator profile")
    return float(match.group(1).removesuffix("f"))


class IndicatorDisplayProfileTest(unittest.TestCase):
    def test_preserves_logical_ui_and_fills_panel(self):
        panel_size = 480
        zoom = numeric_flag("UI_ZOOM")
        coordinate_scale = numeric_flag("UI_COORD_SCALE")

        logical_size = panel_size / (zoom * coordinate_scale)
        sprite_size = logical_size * coordinate_scale

        self.assertEqual(logical_size, 160)
        self.assertEqual(sprite_size, 320)
        self.assertEqual(sprite_size * zoom, panel_size)

    def test_internal_canvas_leaves_interface_headroom(self):
        panel_size = 480
        color_depth = numeric_flag("UI_BUFFER_COLOR_DEPTH")
        zoom = numeric_flag("UI_ZOOM")
        coordinate_scale = numeric_flag("UI_COORD_SCALE")
        logical_size = panel_size / (zoom * coordinate_scale)
        sprite_size = int(logical_size * coordinate_scale)

        old_canvas_bytes = panel_size * panel_size * int(color_depth) // 8
        canvas_bytes = sprite_size * sprite_size * int(color_depth) // 8

        self.assertEqual(canvas_bytes, 51_200)
        self.assertGreaterEqual(old_canvas_bytes - canvas_bytes, 64_000)
        self.assertIn("buffer.setPsram(false);", DISPLAY.read_text())

    def test_uses_dedicated_pairing_block(self):
        self.assertIn("-D UI_DEDICATED_PAIRING_BLOCK=1", base_profile())

        logical_height = 160
        block_height = 48
        block_center = logical_height * 4 // 5
        block_top = block_center - block_height // 2
        info_top = 40
        info_bottom = block_top - 12

        self.assertEqual(block_center, 128)
        self.assertEqual(block_top, 104)
        self.assertLess(info_top, info_bottom)
        self.assertLessEqual(info_bottom + 12, block_top)

    def test_recovered_font_pairing_values_fit_reserved_block(self):
        data = FONT.read_bytes()
        glyph_count = struct.unpack_from(">I", data, 0)[0]
        advances = {}
        heights = {}
        for index in range(glyph_count):
            offset = 24 + index * 28
            codepoint, height, _width, advance = struct.unpack_from(
                ">IIII", data, offset
            )
            advances[codepoint] = advance
            heights[codepoint] = height

        footer = data[-40:]
        self.assertEqual(b"MCEMAP2\0", footer[:8])
        atlas_offset = struct.unpack_from("<I", footer, 20)[0]
        native_scale = data[atlas_offset + 9]
        coordinate_scale = 2

        def dimensions(text, requested_size, coordinate_scale=2,
                       profile_scale=1.0):
            render_scale = (
                requested_size * coordinate_scale / native_scale
                * profile_scale
            )
            # Blank glyphs carry no bitmap record in the packed VLW. The
            # checked-in font is monospaced with the documented 18px cell, so
            # spaces still advance by one complete cell.
            width = sum(
                18 if char == " " else advances[ord(char)] for char in text
            )
            height = max(heights[ord(char)] for char in text if char != " ")
            return (
                int((width * render_scale + coordinate_scale - 1)
                    // coordinate_scale),
                int((height * render_scale + coordinate_scale - 1)
                    // coordinate_scale),
            )

        self.assertEqual((108, 24), dimensions("123456", 3))
        self.assertEqual((108, 16), dimensions("CONNECTED", 2))
        for width, height in (
            dimensions("123456", 3), dimensions("CONNECTED", 2)
        ):
            self.assertLessEqual(width, 144)
            self.assertLessEqual(height, 24)

        # This continuous-scale model rounds outward and is deliberately a
        # conservative fit bound; LovyanGFX applies fixed-point rounding while
        # laying out individual glyphs.
        native_pin = dimensions("123456", 3, 3, 1.2)
        native_connected = dimensions("CONNECTED", 2, 3, 1.2)
        for width, height in (native_pin, native_connected):
            self.assertLessEqual(width, 144)
            self.assertLessEqual(height, 30)

        # Native 480 reflows the spacious home page instead of globally
        # enlarging every dense UI row. The title, count, action, and pairing
        # state each occupy their own large row.
        native_title = dimensions("INBOX", 4, 3, 1.2)
        native_count = dimensions("9999", 4, 3, 1.2)
        native_count_fallback = dimensions("99999", 3, 3, 1.2)
        self.assertLessEqual(native_title[0], 156)
        self.assertLessEqual(native_title[1], 39)
        self.assertLessEqual(native_count[0], 156)
        self.assertLessEqual(native_count_fallback[0], 156)

        for text, requested_size, region_width in (
            ("TAP", 3, 144),
            ("OFF", 3, 144),
            ("PIN", 3, 144),
            ("LINKED", 3, 144),
            ("123456", 3, 144),
            ("IP: 192.168.100.200", 1, 144),
        ):
            width, height = dimensions(text, requested_size, 3, 1.2)
            self.assertLessEqual(width, region_width)
            self.assertLessEqual(height, 29 if requested_size == 3 else 10)

        # The two lower size-3 rows must not depend on blank pixels inside the
        # checked-in VLW: the built-in fallback font can fill its whole cell.
        pairing_label_y = 102
        pairing_value_y = 131
        self.assertLessEqual(pairing_label_y + 29, pairing_value_y)
        self.assertLessEqual(pairing_value_y + 29, 160)

        # Native setup mode intentionally uses most of the panel: a size-3
        # title and size-2 action/value rows replace the legacy size-1 page.
        for text, requested_size in (
            ("SETUP", 3),
            ("READY", 3),
            ("WIFI", 3),
            ("CONNECTING", 2),
            ("JOIN WIFI", 2),
            ("BROWSE", 2),
            ("192.168.4.1", 2),
        ):
            width, _height = dimensions(text, requested_size, 3, 1.2)
            self.assertLessEqual(width, 160)

        title_height = dimensions("SETUP", 3, 3, 1.2)[1]
        row_height = dimensions("JOIN WIFI", 2, 3, 1.2)[1]
        self.assertLessEqual(2 + title_height, 34)
        self.assertLessEqual(34 + row_height, 56)
        self.assertLessEqual(56 + row_height, 77)
        self.assertLessEqual(77 + row_height, 101)
        self.assertLessEqual(101 + row_height, 124)
        self.assertLessEqual(124 + row_height, 160)

        long_ssid = dimensions("ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", 1, 3, 1.2)
        self.assertLessEqual(long_ssid[0], 2 * 148)


if __name__ == "__main__":
    unittest.main()
