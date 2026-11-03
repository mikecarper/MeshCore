#!/usr/bin/env python3
"""Run Pocket Reader against the actual installed U8g2 font and decoder."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/pocket_reader"


class PocketReaderTest(unittest.TestCase):
    def test_reader_with_real_helvetica_font(self):
        compiler, cc = shutil.which("g++"), shutil.which("gcc")
        if not compiler or not cc:
            self.skipTest("host GCC and G++ required")
        candidates = [Path(os.environ.get("U8G2_GFX_ROOT", "/nonexistent"))]
        candidates += list((ROOT / ".pio/libdeps").glob("*/U8g2_for_Adafruit_GFX"))
        library = next((path / "src" for path in candidates
                        if (path / "src/U8g2_for_Adafruit_GFX.cpp").is_file()), None)
        if library is None:
            self.skipTest("installed U8g2_for_Adafruit_GFX required (or set U8G2_GFX_ROOT)")
        with tempfile.TemporaryDirectory(prefix="mesh-pocket-reader-") as directory:
            directory = Path(directory)
            # Compile the production font, without all the unrelated 40 MB of fonts.
            source = (library / "u8g2_fonts.c").read_text(encoding="latin-1")
            font = re.search(r"const uint8_t u8g2_font_helvR08_tf\[[^]]+\].*?;\n", source, re.S)
            self.assertIsNotNone(font)
            selected = directory / "font.c"
            selected.write_text('#include "u8g2_fonts.h"\n' + font.group())
            flags = ["-g", "-O1", "-DHELTEC_MESH_POCKET=1", "-DCOMPANION_RADIO_FULL=1", "-DCOMPANION_FEATURE_READER=1",
                     "-DNRF52_PLATFORM=1", "-DENABLE_USB_INTERFACE=1", "-DUI_POCKET_GUI=1",
                     "-DUI_SMALL_MESSAGE_FONT=0", "-DUI_BUTTON_READER_HINT=1", "-DUI_READER_TOUCH_BAR=0",
                     "-I" + str(FIXTURE), "-I" + str(ROOT / "src"), "-I" + str(ROOT),
                     "-I" + str(ROOT / "test/fixtures/companion_reader"), "-I" + str(library)]
            if os.name != "nt":
                flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
            commands = [
                [cc, "-std=c11", *flags, "-c", str(selected), "-o", str(directory / "font.o")],
                [cc, "-std=c11", *flags, "-c", str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(directory / "tinf.o")],
                [compiler, "-std=c++17", *flags, str(FIXTURE / "test_pocket_reader.cpp"),
                 str(ROOT / "src/helpers/CompanionReader.cpp"), str(library / "U8g2_for_Adafruit_GFX.cpp"),
                 str(directory / "font.o"), str(directory / "tinf.o"), "-o", str(directory / "reader")],
                [str(directory / "reader")],
            ]
            for command in commands:
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, " ".join(command) + "\n" + result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
