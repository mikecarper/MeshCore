"""Raw KISS wire-only support: no Companion tunnel, dispatcher slot or RF."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


class KissEthernetWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("a host C++17 compiler is required")

    def compile_run(self, code, *flags):
        with tempfile.TemporaryDirectory(prefix="meshcore-kiss-wire-") as folder:
            source, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            source.write_text(code, encoding="utf-8")
            command = [self.compiler, "-std=c++17", "-Wall", "-Wextra", *flags,
                       "-I", str(ROOT / "src"), str(source), "-o", str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr[-12000:])
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wire_only_does_not_enable_companion_tunnel_or_foreign_queue(self):
        self.compile_run(r'''
#include <helpers/MeshtextConfig.h>
static_assert(MESH_ENABLE_ETHERNET_WIRE == 1, "raw wire must be enabled");
static_assert(MESH_ENABLE_ETHERNET_TUNNEL == 0, "no Companion tunnel");
static_assert(MESH_ENABLE_FOREIGN_RADIO == 0, "no foreign dispatcher slot");
int main() {}
''', "-DMESH_ENABLE_ETHERNET_WIRE=1", "-DMESH_ENABLE_ETHERNET_TUNNEL=0",
            "-DMESH_ENABLE_MESHTEXT_CHAT=0", "-DNRF52_PLATFORM=1")

    def test_actual_sx1262_and_sx1276_protocol_switching_with_wire_only(self):
        for chip in ("SX1262", "SX1276"):
            source = (ROOT / f"src/helpers/radiolib/Custom{chip}Wrapper.h").read_text()
            protocol = method(source, "bool applyProtocol(").replace(" override", "")
            code = r'''
#include <helpers/MeshtextConfig.h>
#include <RadioProfiles.h>
#include <cassert>
#define RADIOLIB_ERR_NONE 0
struct Custom@CHIP@ {
  unsigned writes=0;uint8_t sync=0;int result=0;
  int setProfileProtocol(uint8_t value) { ++writes;sync=value;return result; }
};
struct Driver {
  Custom@CHIP@ chip;Custom@CHIP@* _radio=&chip;bool supported=true;
  bool supportsMeshtext() const { return false; }
  bool supportsEthernetTunnel() const { return supported; }
  @METHOD@
};
int main() {
  using P=mesh::RadioProfileProtocol;Driver d;
  assert(d.applyProtocol(P::Ethernet) && d.chip.sync==0x16);
  assert(d.applyProtocol(P::MeshCore) && d.chip.sync==0x12);
  const auto before=d.chip.writes;d.supported=false;
  assert(!d.applyProtocol(P::Ethernet) && d.chip.writes==before);
  assert(!d.applyProtocol(P::Meshtext) && d.chip.writes==before);
  assert(!d.applyProtocol(P(99)) && d.chip.writes==before);
  d.chip.result=-1;assert(!d.applyProtocol(P::MeshCore));
}
'''.replace("@CHIP@", chip).replace("@METHOD@", protocol)
            with self.subTest(chip=chip):
                self.compile_run(code, "-DMESH_ENABLE_ETHERNET_WIRE=1",
                    "-DMESH_ENABLE_ETHERNET_TUNNEL=0", "-DMESH_ENABLE_MESHTEXT_WIRE=0")

    def test_actual_validator_gates_wire_and_chip_capability(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        validator = method(source, "bool RadioLibWrapper::validateProfile(")
        code = r'''
#include <helpers/MeshtextConfig.h>
#include <RadioProfiles.h>
#include <cassert>
struct RadioLibWrapper {
  bool supported=true;
  bool supportsMeshtext() const { return false; }
  bool supportsEthernetTunnel() const { return supported; }
  bool validateProfile(const mesh::RadioProfileParams&) const;
};
@VALIDATOR@
int main() {
  using P=mesh::RadioProfileProtocol;
  RadioLibWrapper d;mesh::RadioProfileParams p;
  p.freq=909.5;p.bw=500;p.sf=5;p.cr=5;p.preamble=512;p.protocol=P::Ethernet;
#if MESH_RADIO_PROFILE_CHIP == 1262
  assert(d.validateProfile(p)==bool(MESH_ENABLE_ETHERNET_WIRE));
#else
  assert(!d.validateProfile(p)); // SX1276 cannot use SF5.
  p.sf=7;assert(d.validateProfile(p)==bool(MESH_ENABLE_ETHERNET_WIRE));
#endif
  d.supported=false;assert(!d.validateProfile(p));
  p.protocol=P::MeshCore;assert(d.validateProfile(p));
}
'''.replace("@VALIDATOR@", validator)
        for wire in (0, 1):
            for chip in (1262, 1276):
                with self.subTest(wire=wire, chip=chip):
                    self.compile_run(code, f"-DMESH_ENABLE_ETHERNET_WIRE={wire}",
                        "-DMESH_ENABLE_ETHERNET_TUNNEL=0", f"-DMESH_RADIO_PROFILE_CHIP={chip}")


if __name__ == "__main__":
    unittest.main()
