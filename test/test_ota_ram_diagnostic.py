#!/usr/bin/env python3
"""Exercise the real bootloader marker parser and its bounded CLI reply."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OtaRamDiagnosticTest(unittest.TestCase):
    def test_only_one_exact_marker_reports_hybrid_support(self):
        compiler = shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        source = r'''
#include "helpers/ota/OtaBlInfo.h"
#include <cassert>
#include <cstring>
using namespace mesh::ota;
int main() {
  uint8_t marker[16] = {};
  memcpy(marker, MOTA_RAM_CAP_MAGIC, sizeof(MOTA_RAM_CAP_MAGIC));
  mota_hybrid_wr16(marker + 8, MOTA_RAM_CAP_ABI);
  mota_hybrid_wr16(marker + 10, MOTA_HYBRID_AUTH_LEN);
  mota_hybrid_wr32(marker + 12, MOTA_NRF52_HYBRID_RAM_SIZE);
  uint8_t image[64]; memset(image, 0xff, sizeof(image));
  char result[160];
  ota_ram_caps_reply(result, sizeof(result), ota_ram_caps_scan_aligned(image, sizeof(image)));
  assert(!strcmp(result, "OTA RAM: hybrid=0"));
  memcpy(image + 4, marker, sizeof(marker));
  ota_ram_caps_reply(result, sizeof(result), ota_ram_caps_scan_aligned(image, sizeof(image)));
  assert(!strcmp(result, "OTA RAM: hybrid=1 abi=1 record=72 arena=65536"));
  memcpy(image + 24, marker, sizeof(marker));
  ota_ram_caps_reply(result, sizeof(result), ota_ram_caps_scan_aligned(image, sizeof(image)));
  assert(!strcmp(result, "OTA RAM: hybrid=0"));
  image[24 + 12] ^= 1;
  image[4 + 10] ^= 1;
  ota_ram_caps_reply(result, sizeof(result), ota_ram_caps_scan_aligned(image, sizeof(image)));
  assert(!strcmp(result, "OTA RAM: hybrid=0"));
  char small[4] = {'x','x','x','x'};
  ota_ram_caps_reply(small, sizeof(small), OtaRamCaps());
  assert(small[3] == '\0');
  ota_ram_caps_reply(nullptr, 0, OtaRamCaps());
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "ram-diagnostic"
            subprocess.run([compiler, "-std=c++11", "-I", str(ROOT / "src"), "-x", "c++",
                            "-", "-o", str(binary)], input=source, text=True, check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
