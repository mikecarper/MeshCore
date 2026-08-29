#!/usr/bin/env python3

import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "variants" / "sensecap_indicator-espnow" / "platformio.ini"
DISPLAY = ROOT / "src" / "helpers" / "ui" / "LGFXDisplay.cpp"


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


if __name__ == "__main__":
    unittest.main()
