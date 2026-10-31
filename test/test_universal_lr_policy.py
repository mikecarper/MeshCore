"""Run the shared power/airtime methods with each selected radio policy."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class UniversalLRPolicyTests(unittest.TestCase):
    def test_selected_chip_power_and_airtime(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        methods = "\n".join(body(source, signature).replace("RadioLibWrapper::", "")
            for signature in ("bool RadioLibWrapper::isTxPowerValid(",
                              "uint32_t RadioLibWrapper::getProfileAirtime(",
                              "uint32_t RadioLibWrapper::getBeaconAirtime("))
        harness = r'''
#include <cassert>
#include <cmath>
#include <initializer_list>
#include <RadioProfiles.h>
#define MAX_TRANS_UNIT 255
#define MIN_LORA_TX_POWER -9
#define MAX_LORA_TX_POWER 22
struct RadioLibWrapper {
 bool lr = false;
 mesh::RadioProfiles _profiles;
 float _cur_freq = 2400;
 bool usesLR1121Policy() const { return lr; }
 bool checkBeaconOutputPower(float, int8_t power) const { return power >= -9 && power <= 13; }
 bool validateProfile(const mesh::RadioProfileParams& p) const { return mesh::RadioProfiles::valid(p); }
 uint16_t profilePreamble(uint8_t profile) const {
  const auto& p = _profiles.params(profile);
  return p.preamble ? p.preamble : 32;
 }
 uint32_t getEstAirtimeFor(int) { return 777; }
 @METHODS@
};

int main() {
 for (bool lr : {false, true}) {
  RadioLibWrapper radio;
  radio.lr = lr;
  assert(!radio.isTxPowerValid(-10) && !radio.isTxPowerValid(23));
  assert(radio.isTxPowerValid(13));
  assert(radio.isTxPowerValid(22) == bool(MESH_RADIO_AUTO_LR1121 && lr));
  radio._profiles.secondary.mode = mesh::RadioProfileMode::Rx;
  for (float frequency : {915.0f, 2400.0f})
  for (float bandwidth : {62.5f, 125.0f, 812.5f})
  for (uint8_t sf = 5; sf <= 12; ++sf)
  for (uint8_t cr = 5; cr <= 8; ++cr) {
   mesh::RadioProfileParams p;
   p.freq = frequency; p.bw = bandwidth; p.sf = sf; p.cr = cr;
   radio._profiles.primary = p;
   for (int length = 0; length <= 255; ++length) {
    // For the SX backend the high-band sample is intentionally only an
    // arithmetic regression probe; the real backend rejects its frequency.
    // Use the previous floating-point block formula as an independent oracle.
    const double symbol = double(1U << sf) * 1000.0 / bandwidth;
    const int de = symbol >= 16000 || (MESH_RADIO_AUTO_LR1121 && lr && frequency > 1000 && sf > 10);
    const double blocks = ceil((8.0 * length - 4 * sf + (sf <= 6 ? 20 : 28) + 16)
                              / (4 * (sf - 2 * de)));
    const auto expected = uint32_t(ceil((32 + (sf <= 6 ? 6.25 : 4.25) + 8
                                + (blocks > 0 ? blocks * cr : 0)) * symbol / 1000.0));
    const auto floor = MESH_RADIO_AUTO_LR1121 && lr && expected < 200 ? 200 : expected;
    assert(radio.getProfileAirtime(0, length, 0) == floor);
    assert(radio.getProfileAirtime(0, length, cr) == floor);
    const auto beacon = length && mesh::RadioProfiles::safePreamble(p, 32) ? expected : 0;
    assert(radio.getBeaconAirtime(p, length) == beacon);
   }
  }
  assert(!radio.getProfileAirtime(2, 10, 0));
  assert(!radio.getBeaconAirtime(radio._profiles.primary, 256));
  radio._profiles.secondary.mode = mesh::RadioProfileMode::Off;
  assert(radio.getProfileAirtime(0, 1, 0) == 777);
 }
}
'''.replace("@METHODS@", methods)
        with tempfile.TemporaryDirectory(prefix="universal-lr-policy-") as folder:
            cpp = Path(folder) / "test.cpp"
            binary = Path(folder) / "test.exe"
            cpp.write_text(harness)
            for enabled in (0, 1):
                with self.subTest(lr_backend_compiled=enabled):
                    sanitize = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-pie", "-no-pie"]
                    built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++11", *sanitize,
                        "-DMESH_RADIO_AUTO_LR1121=" + str(enabled), "-DMESH_RADIO_PROFILE_CHIP=0",
                        "-DMESH_ENABLE_TX_BEACONS=1", "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
                    self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
