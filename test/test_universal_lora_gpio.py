"""Compile the real GPIO policy with the universal T3 S3 radio pin union."""
import configparser
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UniversalLoRaGpioTest(unittest.TestCase):
    def compile_and_run(self, definitions, body):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++ compiler required")
        sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="universal-lora-gpio-") as folder:
            work = Path(folder)
            source = work / "test.cpp"
            source.write_text("#include <helpers/UserGpioPinPolicy.h>\n"
                              "#include <cassert>\nint main(){\n" + body + "\n}\n")
            binary = work / ("test.exe" if os.name == "nt" else "test")
            compiled = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                *sanitizer, *["-D" + item for item in definitions],
                "-I" + str(ROOT / "src"), str(source), "-o", str(binary)],
                capture_output=True, text=True, timeout=45)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)

    def test_actual_t3_recipe_reserves_both_radio_families(self):
        config = configparser.ConfigParser(interpolation=None,
                                          inline_comment_prefixes=(";",))
        config.read(ROOT / "variants/lilygo_t3s3_universal/platformio.ini")
        flags = config["LilyGo_T3S3"]["build_flags"]
        pins = dict((match[1], match[2]) for match in re.finditer(
            r"-D\s*((?:P_LORA_|PIN_)[A-Z0-9_]+)=(-?\d+)\b", flags))
        self.assertEqual(pins["P_LORA_DIO_3"], "21")
        self.assertEqual(pins["P_LORA_DIO_4"], "10")
        self.assertEqual(pins["P_LORA_DIO_5"], "36")
        self.assertEqual(pins["PIN_OLED_RESET"], "-1")
        self.assertNotIn("SX127X_RXEN", flags)
        self.assertNotIn("SX127X_TXEN", flags)
        # This is the hardware union, independent of the selected modem:
        # SPI, reset/CS, both IRQs, SX126x BUSY/SX127x DIO2, and DIO3/4/5.
        reserved = {0, 1, 3, 5, 6, 7, 8, 9, 10, 17, 18, 21, 33, 34, 36, 37}
        body = "\n".join("assert(UserGpioPinPolicy::isFirmwareReserved(%d) == %s);" %
                         (pin, "true" if pin in reserved else "false")
                         for pin in range(64))
        self.compile_and_run([name + "=" + value for name, value in pins.items()], body)

    def test_extra_dio_aliases_ignore_disconnected_sentinels(self):
        self.compile_and_run(["RADIOLIB_NC=0xFFFFFFFFu", "P_LORA_DIO_3=RADIOLIB_NC",
                              "P_LORA_DIO_4=-1", "P_LORA_DIO_5=63"], """
for (unsigned pin=0; pin<64; ++pin)
  assert(UserGpioPinPolicy::isFirmwareReserved(pin) == (pin == 63));
""")

    def test_other_boards_do_not_gain_t3_specific_reservations(self):
        self.compile_and_run([], """
for (unsigned pin=0; pin<64; ++pin)
  assert(!UserGpioPinPolicy::isFirmwareReserved(pin));
""")


if __name__ == "__main__":
    unittest.main()
