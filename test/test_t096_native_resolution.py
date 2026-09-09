#!/usr/bin/env python3
"""Test the real ST7735 header and drawing methods with a recording TFT canvas."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/st7735_native"


class T096NativeResolutionTest(unittest.TestCase):
    def test_all_st7735_boards_are_native(self):
        driver = (ROOT / "src/helpers/ui/ST7735Display.cpp").read_text()
        source = (FIXTURE / "render.cpp").read_text() + "\n"
        source += "\n".join(line for line in driver.splitlines()
                            if line.startswith("#define SCALE_")) + "\n"
        for name in ("setTextSize", "textLineHeight", "setColor", "setCursor", "print", "fillRect",
                     "drawRect", "drawXbm", "getTextWidth"):
            start = driver.index(f"ST7735Display::{name}(")
            start = driver.rfind("\n", 0, start) + 1
            end = driver.index("\n}", start) + 2
            source += driver[start:end] + "\n"
        with tempfile.TemporaryDirectory(prefix="meshcore-t096-native-") as directory:
            for native in (False, True):
                for tft_pin in (False, True):
                    with self.subTest(native=native, tft_pin=tft_pin):
                        binary = Path(directory) / "render.exe"
                        flags = (["-DHELTEC_T096=1"] if native else [])
                        flags += ["-DUSE_PIN_TFT=1"] if tft_pin else []
                        if os.name != "nt":
                            flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
                        result = subprocess.run([
                            "c++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-parameter", *flags,
                            "-I", str(FIXTURE / "mocks"), "-I", str(ROOT / "src"),
                            "-x", "c++", "-", "-o", str(binary),
                        ], input=source, text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        result = subprocess.run([str(binary)], text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sprite->createSprite(160, 80)", driver)


if __name__ == "__main__":
    unittest.main()
