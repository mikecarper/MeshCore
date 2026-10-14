"""Exercise real preset generation against each modem's production validator.

This is a host test, not evidence of RF operation or antenna band support.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]


class MeshtextModemProfilesTest(unittest.TestCase):
    def test_real_presets_respect_chip_limits_and_capability(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++ compiler is required")
        required = ["AESCommon.cpp", "AES128.cpp", "AES256.cpp", "BlockCipher.cpp", "Crypto.cpp"]
        override = os.environ.get("MESHCORE_CRYPTO_DIR")
        candidates = [Path(override)] if override else sorted((ROOT / ".pio/libdeps").glob("*/Crypto"))
        crypto = next((path for path in candidates if all((path / item).is_file() for item in required)), None)
        if not crypto:
            self.skipTest("an existing rweather/Crypto dependency is required (MESHCORE_CRYPTO_DIR)")
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        validator = method(source, "bool RadioLibWrapper::validateProfile(")
        harness = r'''
#include <helpers/MeshtextChat.h>
#include <helpers/radiolib/RadioProfileChip.h>
#include <cassert>
#include <cstdio>
struct RadioLibWrapper {
  bool supported = true;
  bool supportsMeshtext() const { return supported; }
  bool validateProfile(const mesh::RadioProfileParams&) const;
};
@VALIDATOR@
int main() {
  using namespace mesh;
  static_assert(MESH_RADIO_PROFILE_CHIP == EXPECTED_PROFILE_CHIP, "selected concrete modem");
  RadioLibWrapper radio;
  unsigned accepted = 0, rejected = 0;
  for (unsigned region = unsigned(mt::Region::US); region <= unsigned(mt::Region::ANZ); ++region) {
    for (unsigned preset = 0; preset <= unsigned(mt::Preset::LongSlow); ++preset) {
      RadioProfileParams p;
      if (!mt::modemConfig(mt::Region(region), mt::Preset(preset), p)) continue;
      assert(p.protocol == RadioProfileProtocol::Meshtext);
      bool expected = true;
#if EXPECTED_PROFILE_CHIP == 1268
      expected = region == unsigned(mt::Region::EU433);
#elif EXPECTED_PROFILE_CHIP == 68
      expected = (p.bw == 125 && p.sf <= 9) || (p.bw == 250 && p.sf <= 10)
          || (p.bw == 500 && p.sf <= 11);
#endif
      assert(radio.validateProfile(p) == expected);
      if (expected) ++accepted; else ++rejected;
      radio.supported = false;
      assert(!radio.validateProfile(p));
      p.protocol = RadioProfileProtocol::MeshCore;
      assert(radio.validateProfile(p) == expected);
      radio.supported = true;
      p.protocol = RadioProfileProtocol(99);
      assert(!radio.validateProfile(p));
    }
  }
  assert(accepted > 0);
#if EXPECTED_PROFILE_CHIP == 1268 || EXPECTED_PROFILE_CHIP == 68
  assert(rejected > 0);
#else
  assert(rejected == 0);
#endif
  printf("PASS: %u compatible and %u incompatible presets\n", accepted, rejected);
}
'''.replace("@VALIDATOR@", validator)
        with tempfile.TemporaryDirectory(prefix="meshcore-mt-modems-") as folder:
            cpp, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            cpp.write_text(harness)
            chips = [(chip, []) for chip in
                     ("SX1262", "SX1268", "LLCC68", "SX1276", "LR1110", "LR1121", "LR2021", "STM32WLX")]
            # Real E22 variants expose both optional chip macros. The chosen
            # radio class must still accept the SX1262's US/ANZ presets.
            chips += [("SX1262", ["-DRADIO_CLASS=CustomSX1262", "-DUSE_SX1268"]),
                      ("SX1268", ["-DRADIO_CLASS=CustomSX1268", "-DUSE_SX1262"]),
                      ("LR1110", ["-DRADIO_CLASS=CustomLR1110", "-DUSE_SX1268"])]
            for chip, extra in chips:
                with self.subTest(chip=chip, extra=extra):
                    expected = {"SX1262": 1262, "SX1268": 1268, "LLCC68": 68,
                                "SX1276": 1276, "LR1110": 1110, "LR1121": 1121,
                                "LR2021": 2021, "STM32WLX": 1262}[chip]
                    flags = ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                             "-DMESH_ENABLE_MESHTEXT_WIRE=1", "-DUSE_" + chip,
                             "-DEXPECTED_PROFILE_CHIP=" + str(expected), *extra]
                    if sys.platform.startswith("linux"):
                        flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                    command = [compiler, *flags, "-I", str(ROOT / "src"), "-I", str(crypto), str(cpp),
                               str(ROOT / "src/helpers/MeshtextChat.cpp"),
                               *[str(crypto / item) for item in required], "-o", str(binary)]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("PASS:", result.stdout)


if __name__ == "__main__":
    unittest.main()
