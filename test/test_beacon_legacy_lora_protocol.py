"""Exercise SX1276, SX1268 and LLCC68 beacon wire-protocol selection.

Production driver/wrapper protocol methods, deep-recovery restoration, power
amplifier subclass, and profile validator run with counted RadioLib setter
boundaries. This proves control flow and cache repair, not RF interoperability.
No PlatformIO or hardware is used.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
RADIO = ROOT / "src/helpers/radiolib"


HARNESS = r'''
#include <RadioProfiles.h>
#include <helpers/radiolib/RadioProfileChip.h>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <vector>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_INVALID_OUTPUT_POWER -1
#define RADIOLIB_ERR_WRONG_MODEM -2
#define STATE_IDLE 0
namespace mesh { struct MainBoard {}; }
static unsigned gain_writes = 0;
static uint8_t last_gain = 0;
void dacWrite(uint8_t, uint8_t code) { ++gain_writes; last_gain = code; }
struct PhysicalLayer {
  uint8_t sync = 0x12;
  bool crc = false, explicit_header = false, iq = true;
  bool cached_crc = false, cached_implicit = true, cached_iq = true, lora = true;
  unsigned step = 0, fail_at = 0, power_writes = 0;
  int8_t drive = 0;
  int16_t status() { return ++step == fail_at ? -77 : 0; }
  int16_t setSyncWord(uint8_t value) {
    if (!lora) return RADIOLIB_ERR_WRONG_MODEM;
    const int16_t rc = status(); if (!rc) sync = value; return rc;
  }
  int16_t setCrc(bool value) {
    cached_crc = value;
    const int16_t rc = status(); if (!rc) crc = value; return rc;
  }
  int16_t explicitHeader() {
    cached_implicit = false;
    const int16_t rc = status(); if (!rc) explicit_header = true; return rc;
  }
  int16_t invertIQ(bool value) {
    cached_iq = value;
    const int16_t rc = status(); if (!rc) iq = value; return rc;
  }
  int16_t setPreambleLength(uint16_t) { return 0; }
  int16_t setOutputPower(int8_t dbm, bool) { ++power_writes; drive = dbm; return 0; }
  void resetPacketDefaults() {
    sync = 0x12; crc = false; explicit_header = false; iq = true;
    cached_crc = false; cached_implicit = true; cached_iq = true; step = 0;
  }
};
struct SX1276 : PhysicalLayer { int16_t setCRC(bool enabled) { return setCrc(enabled); } };
struct SX1268 : PhysicalLayer { int16_t setCRC(uint8_t length) { return setCrc(length != 0); } };
struct LLCC68 : PhysicalLayer { int16_t setCRC(uint8_t length) { return setCrc(length != 0); } };
@DRIVERS@
struct RadioLibWrapper {
  PhysicalLayer* _radio;
  mesh::MainBoard* _board;
  bool _params_valid = true, _rx_ps_armed = true, _dbm_valid = false, _rx_boosted_gain_valid = false;
  int state = 1;
  float _cur_freq = 906.875f, _cur_bw = 250;
  uint8_t _cur_sf = 11, _cur_cr = 5;
  int8_t _cur_dbm = 17;
  bool _cur_rx_boosted_gain = false;
  unsigned floor_refreshes = 0;
  mesh::RadioProfileProtocol _physical_protocol = mesh::RadioProfileProtocol::MeshCore;
  RadioLibWrapper(PhysicalLayer& radio, mesh::MainBoard& board) : _radio(&radio), _board(&board) {}
  virtual bool supportsMeshtext() const { return false; }
  virtual bool applyProtocol(mesh::RadioProfileProtocol p) { return p == mesh::RadioProfileProtocol::MeshCore; }
  virtual int16_t applyCachedTxPower(int8_t) { return 0; }
  bool setTxPower(int8_t value) { return applyCachedTxPower(value) == 0; }
  bool radioDeepInit() { _radio->resetPacketDefaults(); return true; }
  bool registerInterruptAction() { return true; }
  bool applyParams(float, float, uint8_t, uint8_t) { return true; }
  uint16_t currentPreambleLength() { return 16; }
  bool applyRxBoostedGainMode(bool) { return true; }
  void recalibrateNoiseFloor() { ++floor_refreshes; }
  bool restoreAfterDeepInit();
  bool validateProfile(const mesh::RadioProfileParams&) const;
};
@WRAPPERS@
@RESTORE@
@VALIDATE@
@DAC@
template <class Wrapper, class Chip>
static void protocols() {
  using Protocol = mesh::RadioProfileProtocol;
  mesh::MainBoard board;
  Chip chip;
  Wrapper wrapper(chip, board);
  assert(wrapper.supportsMeshtext());
  for (Protocol protocol : {Protocol::Meshtext, Protocol::MeshCore, Protocol::Meshtext}) {
    chip.resetPacketDefaults();
    assert(wrapper.applyProtocol(protocol));
    assert(chip.sync == (protocol == Protocol::Meshtext ? 0x2b : 0x12));
    assert(chip.step == 4 && chip.crc && chip.explicit_header && !chip.iq);
    assert(chip.cached_crc && !chip.cached_implicit && !chip.cached_iq);
  }
  chip.step = 0;
  assert(!wrapper.applyProtocol(static_cast<Protocol>(255)) && chip.step == 0);
  chip.lora = false;
  assert(!wrapper.applyProtocol(Protocol::Meshtext) && chip.step == 0);
}
template <class Wrapper, class Chip>
static void failures_and_recovery() {
  using Protocol = mesh::RadioProfileProtocol;
  for (unsigned fail = 1; fail <= 4; ++fail) {
    mesh::MainBoard board;
    Chip chip;
    Wrapper wrapper(chip, board);
    chip.fail_at = fail;
    assert(!wrapper.applyProtocol(Protocol::Meshtext) && chip.step == fail);
    // A setter may update a RadioLib cache before its hardware write fails.
    // The old protocol restoration must rewrite every invariant, not skip it.
    chip.fail_at = 0; chip.step = 0;
    assert(wrapper.applyProtocol(Protocol::MeshCore));
    assert(chip.sync == 0x12 && chip.crc && chip.explicit_header && !chip.iq);
    assert(chip.cached_crc && !chip.cached_implicit && !chip.cached_iq);
    wrapper._physical_protocol = Protocol::Meshtext;
    assert(wrapper.restoreAfterDeepInit());
    assert(chip.sync == 0x2b && chip.crc && chip.explicit_header && !chip.iq);
    assert(!wrapper._rx_ps_armed && wrapper.state == STATE_IDLE);
    chip.fail_at = fail;
    assert(!wrapper.restoreAfterDeepInit());
    chip.fail_at = 0;
    wrapper._physical_protocol = Protocol::MeshCore;
    assert(wrapper.restoreAfterDeepInit());
    assert(chip.sync == 0x12 && chip.crc && chip.explicit_header && !chip.iq);
  }
}
static void dac_inheritance() {
  mesh::MainBoard board;
  CustomSX1276 chip;
  const DacPaLevel levels[] = {{10, 30}, {17, 50}, {30, 130}};
  DacPaSX1276Wrapper wrapper(chip, board, 4, levels, 3);
  assert(wrapper.supportsMeshtext());
  assert(wrapper.beginPowerControl(17));
  assert(chip.drive == 2 && gain_writes == 1 && last_gain == 50);
  const unsigned power_writes = chip.power_writes;
  assert(wrapper.applyProtocol(mesh::RadioProfileProtocol::Meshtext));
  assert(chip.sync == 0x2b && chip.power_writes == power_writes && gain_writes == 1);
  assert(wrapper.applyProtocol(mesh::RadioProfileProtocol::MeshCore));
  assert(chip.sync == 0x12 && chip.power_writes == power_writes && gain_writes == 1);
}
static void validation() {
  mesh::MainBoard board;
  CustomSX1276 chip;
  CustomSX1276Wrapper wrapper(chip, board);
  mesh::RadioProfileParams p;
  p.freq = 906.875f; p.bw = 250; p.sf = 11; p.cr = 5;
  p.preamble = 16; p.protocol = mesh::RadioProfileProtocol::Meshtext;
#if defined(USE_SX1276)
  assert(wrapper.validateProfile(p));
  p.sf = 6; assert(!wrapper.validateProfile(p));
  p.sf = 9; assert(wrapper.validateProfile(p));
#elif defined(USE_SX1268)
  assert(!wrapper.validateProfile(p)); // US default is outside this chip's RF range.
  p.freq = 433.875f; assert(wrapper.validateProfile(p));
  p.freq = 906.875f; assert(!wrapper.validateProfile(p));
#elif defined(USE_LLCC68)
  assert(!wrapper.validateProfile(p)); // LongFast SF11/BW250 exceeds LLCC68's limit.
  p.sf = 9; assert(wrapper.validateProfile(p)); // MediumFast is supported.
  p.bw = 500; p.sf = 11; assert(wrapper.validateProfile(p));
  p.sf = 12; assert(!wrapper.validateProfile(p));
#endif
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "protocols")) {
    protocols<CustomSX1276Wrapper, CustomSX1276>();
    protocols<CustomSX1268Wrapper, CustomSX1268>();
    protocols<CustomLLCC68Wrapper, CustomLLCC68>();
  } else if (!strcmp(argv[1], "recovery")) {
    failures_and_recovery<CustomSX1276Wrapper, CustomSX1276>();
    failures_and_recovery<CustomSX1268Wrapper, CustomSX1268>();
    failures_and_recovery<CustomLLCC68Wrapper, CustomLLCC68>();
  } else if (!strcmp(argv[1], "dac")) dac_inheritance();
  else if (!strcmp(argv[1], "validation")) validation();
  else assert(false);
  puts("PASS");
}
'''


class BeaconLegacyLoRaProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise unittest.SkipTest("a host C++ compiler is required")
        drivers, wrappers, api_classes = [], [], []
        for name, parent in (("SX1276", "SX1276"), ("SX1268", "SX1268"), ("LLCC68", "LLCC68")):
            driver_source = (RADIO / ("Custom" + name + ".h")).read_text(encoding="utf-8")
            wrapper_source = (RADIO / ("Custom" + name + "Wrapper.h")).read_text(encoding="utf-8")
            drivers.append("struct Custom" + name + ": " + parent + " {\n"
                           + body(driver_source, "int16_t setProfileProtocol(") + "\n};")
            api_classes.append("struct Checked" + name + ": " + parent + " {\n"
                               + body(driver_source, "int16_t setProfileProtocol(") + "\n};")
            wrappers.append("struct Custom" + name + "Wrapper: RadioLibWrapper {\n"
                            + "Custom" + name + "Wrapper(Custom" + name
                            + "& radio,mesh::MainBoard& board):RadioLibWrapper(radio,board) {}\n"
                            + body(wrapper_source, "bool supportsMeshtext(") + "\n"
                            + body(wrapper_source, "bool applyProtocol(") + "\n};")
        shared = (RADIO / "RadioLibWrappers.cpp").read_text(encoding="utf-8")
        dac = (RADIO / "DacPaSX1276Wrapper.h").read_text(encoding="utf-8")
        source = (HARNESS.replace("@DRIVERS@", "\n".join(drivers))
                  .replace("@WRAPPERS@", "\n".join(wrappers))
                  .replace("@RESTORE@", body(shared, "bool RadioLibWrapper::restoreAfterDeepInit("))
                  .replace("@VALIDATE@", body(shared, "bool RadioLibWrapper::validateProfile("))
                  .replace("@DAC@", body(dac, "struct DacPaLevel") + ";\n"
                           + "#define DAC_PA_DEFAULT_DRIVE_DBM 2\n#define DAC_PA_TABLE_MAX INT8_MAX\n"
                           + body(dac, "class DacPaSX1276Wrapper") + ";\n"))
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-legacy-lora-wire-")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        cpp = work / "protocol.cpp"
        cpp.write_text(source, encoding="ascii")
        cls.compiler = compiler
        cls.api_source = "#include <RadioLib.h>\n" + "\n".join(api_classes)
        cls.binaries = {}
        for chip in ("SX1276", "SX1268", "LLCC68"):
            binary = work / (chip + (".exe" if sys.platform == "win32" else ""))
            flags = ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                     "-DMESH_ENABLE_TX_BEACONS=0", "-DMESH_ENABLE_MESHTEXT_WIRE=1", "-DUSE_" + chip]
            if sys.platform.startswith("linux"):
                flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            result = subprocess.run([compiler, *flags, "-I" + str(ROOT / "src"), str(cpp), "-o", str(binary)],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[chip] = binary

    def run_case(self, name, chip="SX1276"):
        result = subprocess.run([str(self.binaries[chip]), name], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_all_three_drivers_apply_full_meshtext_and_meshcore_packet_invariants(self):
        self.run_case("protocols")

    def test_each_failed_setter_repairs_caches_and_deep_recovery_restores_protocol(self):
        self.run_case("recovery")

    def test_dac_pa_inherits_protocol_without_changing_pa_drive_or_gain(self):
        self.run_case("dac")

    def test_chip_frequency_sf_and_bandwidth_limits_still_apply(self):
        for chip in self.binaries:
            with self.subTest(chip=chip):
                self.run_case("validation", chip)

    def test_driver_protocol_methods_compile_against_cached_real_radiolib_api(self):
        override = os.environ.get("MESHCORE_RADIOLIB_DIR")
        candidates = [Path(override)] if override else sorted((ROOT / ".pio/libdeps").glob("*/RadioLib"))
        radiolib = next((path for path in candidates if (path / "src/RadioLib.h").is_file()), None)
        if radiolib is None:
            self.skipTest("an existing RadioLib checkout is required (MESHCORE_RADIOLIB_DIR)")
        result = subprocess.run(
            [self.compiler, "-std=c++17", "-fsyntax-only", "-x", "c++", "-",
             "-I" + str(radiolib / "src")], input=self.api_source,
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
