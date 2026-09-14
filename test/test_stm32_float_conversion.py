#!/usr/bin/env python3
"""Compile the real text helpers with STM32's utoa-only Arduino API."""
from pathlib import Path
import configparser
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Stm32FloatConversionTest(unittest.TestCase):
    def test_stm32_platform_keeps_the_supported_radiolib_gpio_api(self):
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read(ROOT / "platformio.ini")
        self.assertEqual(config["stm32_base"]["platform"], "ststm32@19.5.0")

    def test_signed_values_and_limits_without_ltoa(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Arduino.h").write_text("""
#pragma once
#include <stdlib.h>
#include <stdio.h>
inline char* utoa(unsigned int value, char* output, int base) {
  if (base != 10) abort();
  sprintf(output, "%u", value);
  return output;
}
""")
            (root / "test.cpp").write_text("""
#include <cassert>
#include <cstring>
#include "helpers/TxtDataHelpers.h"
int main() {
  assert(strcmp(StrHelper::ftoa(0), "0.0") == 0);
  assert(strcmp(StrHelper::ftoa(1), "1.0") == 0);
  assert(strcmp(StrHelper::ftoa(-2.5f), "-2.5") == 0);
  assert(strcmp(StrHelper::ftoa(62.5f), "62.5") == 0);
  assert(strcmp(StrHelper::ftoa(1.125f), "1.125") == 0);
  assert(strcmp(StrHelper::ftoa(2147483520.0f), "2147483520.0") == 0);
  assert(strcmp(StrHelper::ftoa(-2147483520.0f), "-2147483520.0") == 0);
  assert(strcmp(StrHelper::ftoa(2147483648.0f), "0") == 0);
  assert(strcmp(StrHelper::ftoa(1e-12f), "0") == 0);
}
""")
            binary = root / "test"
            subprocess.run(["g++", "-std=c++17", "-DSTM32_PLATFORM", "-I", str(root),
                            "-I", str(ROOT / "src"), str(root / "test.cpp"),
                            str(ROOT / "src/helpers/TxtDataHelpers.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
