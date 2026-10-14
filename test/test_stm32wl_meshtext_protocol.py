"""Check STM32WLx foreign-protocol setup and restoration without hardware."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


class STM32WLMeshtextProtocolTest(unittest.TestCase):
    def test_full_protocol_setup_errors_restore_and_owner_guards(self):
        driver = (ROOT / "src/helpers/radiolib/CustomSTM32WLx.h").read_text(encoding="ascii")
        wrapper = (ROOT / "src/helpers/radiolib/CustomSTM32WLxWrapper.h").read_text(encoding="ascii")
        setup = method(driver, "int16_t setProfileProtocol(")
        apply = method(wrapper, "bool applyProtocol(").replace(" override", "")
        coding = method(wrapper, "bool setCodingRate(").replace(" override", "")
        supports = method(wrapper, "bool supportsMeshtext()").replace(" override", "")
        source = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <string>
#define RADIOLIB_ERR_NONE 0
#define MESH_ENABLE_TX_BEACONS 1
namespace mesh { enum class RadioProfileProtocol : uint8_t { MeshCore, Meshtext }; }
struct STM32WLx {
  std::string trace;
  char fail = 0;
  uint8_t sync = 0xff, crc = 0, header = 1, iq = 1;
  unsigned coding_calls = 0;
  int16_t call(char op) { trace += op; return fail == op ? -101 : 0; }
  int16_t setSyncWord(uint8_t value) {
    const int16_t status = call('Y'); if (!status) sync = value; return status;
  }
  int16_t setCRC(uint8_t value) {
    assert(value == 1);
    const int16_t status = call('C'); if (!status) crc = value; return status;
  }
  int16_t explicitHeader() {
    const int16_t status = call('E'); if (!status) header = 0; return status;
  }
  int16_t invertIQ(bool enabled) {
    assert(!enabled);
    const int16_t status = call('I'); if (!status) iq = 0; return status;
  }
  int16_t setCodingRate(uint8_t cr) { assert(cr == 5); ++coding_calls; return call('R'); }
};
struct CustomSTM32WLx : STM32WLx { @SETUP@ };
struct Wrapper {
  CustomSTM32WLx* _radio;
  bool owned = false, carrier = false;
  bool isBeaconRadioOwned() const { return owned; }
  bool isCarrierWaveActive() const { return carrier; }
  @SUPPORTS@
  @APPLY@
  @CODING@
};
int main() {
  using Protocol = mesh::RadioProfileProtocol;
  for (uint8_t sync : {uint8_t(0x12), uint8_t(0x2b)}) {
    CustomSTM32WLx radio;
    assert(radio.setProfileProtocol(sync) == 0 && radio.trace == "YCEI");
    assert(radio.sync == sync && radio.crc == 1 && radio.header == 0 && radio.iq == 0);
    radio.trace.clear();
    assert(radio.setProfileProtocol(0x12) == 0 && radio.trace == "YCEI");
    assert(radio.sync == 0x12 && radio.crc == 1 && radio.header == 0 && radio.iq == 0);
  }
  for (char failed : std::string("YCEI")) {
    CustomSTM32WLx radio; radio.fail = failed;
    assert(radio.setProfileProtocol(0x2b) == -101);
    assert(radio.trace == std::string("YCEI").substr(0, std::string("YCEI").find(failed) + 1));
    // Ordinary restoration must repair every field after a partial failure.
    radio.fail = 0; radio.trace.clear();
    assert(radio.setProfileProtocol(0x12) == 0 && radio.trace == "YCEI");
    assert(radio.sync == 0x12 && radio.crc == 1 && radio.header == 0 && radio.iq == 0);
  }
  CustomSTM32WLx radio; Wrapper wrapper{&radio};
  assert(wrapper.supportsMeshtext());
  assert(wrapper.applyProtocol(Protocol::Meshtext) && radio.sync == 0x2b);
  radio.trace.clear();
  assert(wrapper.applyProtocol(Protocol::MeshCore) && radio.sync == 0x12 && radio.trace == "YCEI");
  radio.trace.clear();
  assert(!wrapper.applyProtocol(static_cast<Protocol>(255)) && radio.trace.empty());
  for (char failed : std::string("YCEI")) {
    radio.fail = failed;
    assert(!wrapper.applyProtocol(Protocol::Meshtext));
    radio.fail = 0; assert(wrapper.applyProtocol(Protocol::MeshCore));
  }
  wrapper.owned = true;
  assert(!wrapper.setCodingRate(5) && !radio.coding_calls);
  wrapper.owned = false; wrapper.carrier = true;
  assert(!wrapper.setCodingRate(5) && !radio.coding_calls);
  wrapper.carrier = false;
  assert(wrapper.setCodingRate(5) && radio.coding_calls == 1);
  radio.fail = 'R'; assert(!wrapper.setCodingRate(5) && radio.coding_calls == 2);
}
'''
        source = source.replace("@SETUP@", setup).replace("@APPLY@", apply).replace("@CODING@", coding).replace("@SUPPORTS@", supports)
        with tempfile.TemporaryDirectory(prefix="meshcore-stm32wl-protocol-") as work:
            cpp, exe = Path(work) / "test.cpp", Path(work) / "test"
            cpp.write_text(source, encoding="ascii")
            flags = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []
            built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror", *flags,
                                    str(cpp), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_stm32wl_inherits_the_pinned_sx126x_packet_api(self):
        pinned = ROOT / ".pio/libdeps/wio-e5-mini_companion_radio_usb/RadioLib/src/modules/SX126x"
        if not pinned.exists():
            self.skipTest("pinned STM32WLx dependency is not cached")
        stm32 = (pinned / "STM32WLx.h").read_text()
        sx126x = (pinned / "SX126x.h").read_text()
        self.assertIn("class STM32WLx : public SX1262", stm32)
        for signature in ("setSyncWord(", "setCRC(", "explicitHeader(", "invertIQ("):
            self.assertNotIn(signature, stm32)
            self.assertIn(signature, sx126x)
        driver = (ROOT / "src/helpers/radiolib/CustomSTM32WLx.h").read_text(encoding="ascii")
        setup = method(driver, "int16_t setProfileProtocol(")
        self.assertNotIn("_profileSwitch", setup)


if __name__ == "__main__":
    unittest.main()
