#!/usr/bin/env python3
"""Render the real SSD1306 driver with Adafruit GFX and a host panel double.

Build a V4 environment first to install Adafruit GFX, or point
MESHCORE_GFX_LIBRARY to that library's directory. Only the hardware panel,
Arduino strings and Print glue are replaced. Picopixel is compared with the
original Adafruit font; Squeezed Regular 6 with its upstream BDF glyphs.
"""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/ssd1306_picopixel"


class SSD1306SmallMessageFontTest(unittest.TestCase):
    def assert_squeezed_glyphs_match_upstream(self, output):
        rendered = {}
        for line in output.splitlines():
            if line.startswith("six "):
                _, code, advance, pixels = line.split()
                rendered[int(code)] = (int(advance), pixels)
        self.assertEqual(set(rendered), set(range(32, 127)))
        source = (ROOT / "test/fixtures/small_message_font/squeezed6.bdf").read_text()
        for entry in source.split("STARTCHAR ")[1:]:
            code = int(re.search(r"^ENCODING (\d+)", entry, re.M)[1])
            advance = int(re.search(r"^DWIDTH (\d+)", entry, re.M)[1])
            w, h, x, y = map(int, re.search(r"^BBX (.+)$", entry, re.M)[1].split())
            rows = entry.split("BITMAP\n", 1)[1].split("ENDCHAR", 1)[0].splitlines()
            expected = ["0"] * 64
            for row, bits in enumerate(rows):
                for col in range(w):
                    if int(bits, 16) & (1 << (len(bits) * 4 - 1 - col)):
                        expected[(6 - h - y + row) * 8 + x + col] = "1"
            with self.subTest(glyph=chr(code)):
                self.assertEqual(rendered[code], (advance, "".join(expected)))

    def test_actual_font_bounds_wrapping_and_default_font_restoration(self):
        configured = os.environ.get("MESHCORE_GFX_LIBRARY")
        candidates = [Path(configured)] if configured else sorted(
            (ROOT / ".pio/libdeps").glob("*/Adafruit GFX Library")
        )
        library = next((path for path in candidates
                        if (path / "Adafruit_GFX.cpp").is_file()), None)
        if library is None:
            self.skipTest("Build a V4 environment or set MESHCORE_GFX_LIBRARY")

        with tempfile.TemporaryDirectory(prefix="meshcore-picopixel-") as temp:
            for enabled in (0, 1):
                with self.subTest(small_message_font=enabled):
                    binary = Path(temp) / f"render-{enabled}"
                    result = subprocess.run([
                        "c++", "-std=c++17", "-g",
                        "-fsanitize=address,undefined", "-DARDUINO=10819",
                        "-DNRF52_PLATFORM=1", "-DRAK4631_COMBINED_ETHERNET=1",
                        f"-DUI_SMALL_MESSAGE_FONT={enabled}",
                        "-I", str(FIXTURE / "mocks"),
                        "-I", str(ROOT / "src"), "-I", str(library),
                        str(ROOT / "src/helpers/ui/SSD1306Display.cpp"),
                        str(library / "Adafruit_GFX.cpp"),
                        str(FIXTURE / "render.cpp"), "-o", str(binary),
                    ], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    result = subprocess.run(
                        [str(binary)], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if enabled:
                        self.assert_squeezed_glyphs_match_upstream(result.stdout)


if __name__ == "__main__":
    unittest.main()
