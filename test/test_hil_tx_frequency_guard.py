"""Execute actual TX entry points with the opt-in lab RF boundary enabled."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import test_beacon_radio
from test_beacon_radio import method
from test_carrier_wave import production_harness

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp"


class HilTxFrequencyGuardTest(unittest.TestCase):
    def compile_run(self, code, defines=(), guard=True):
        with tempfile.TemporaryDirectory(prefix="meshcore-hil-tx-") as directory:
            cpp, binary = Path(directory) / "test.cpp", Path(directory) / "test"
            cpp.write_text(code)
            command = [os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
                       "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                       *(["-DMESH_HIL_TX_FREQ_MHZ=909.5"] if guard else []),
                       *defines, str(cpp), "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_packet_and_beacon_actual_entry_points(self):
        code = test_beacon_radio.BeaconRadioTest().source().split("int main() {", 1)[0]
        code = code.replace("#include <cassert>", "#include <cassert>\n#include <limits>\n"
                            "#include <helpers/radiolib/HilTxFrequencyGuard.h>")
        code = code.replace("int startTransmit(uint8_t*, int) { return tx_result; }",
                            "unsigned starts=0; int startTransmit(uint8_t*, int) { ++starts; return tx_result; }")
        code = code.replace("bool _beacon_owned = false, _beacon_restoring = false;",
                            "bool _beacon_owned = false, _beacon_restoring = false;\n"
                            "float _hil_beacon_tx_frequency_mhz=0; bool hilPacketTransmitAllowed() const;")
        code += method(SOURCE.read_text(), "bool RadioLibWrapper::hilPacketTransmitAllowed() const")
        code += r'''
void configure(RadioLibWrapper& w, float f=909.5f) {
  w._profiles.primary.freq=w._cur_freq=f;
  w._profiles.primary.bw=w._cur_bw=500;
  w._profiles.primary.sf=w._cur_sf=5;
  w._profiles.primary.cr=w._cur_cr=5;
  w._physical_preamble=w.profilePreamble(0);
}
void blocked(RadioLibWrapper& w) {
  uint8_t bytes[14]={};
  assert(!w.startSendRaw(bytes,sizeof(bytes)));
  assert(w.board.before==0 && w.modem.starts==0);
}
int main() {
  uint8_t bytes[14]={};
  { RadioLibWrapper w; configure(w,910.525f); blocked(w); }
  { RadioLibWrapper w; configure(w); assert(w.startSendRaw(bytes,14));
    assert(w.board.before==1 && w.modem.starts==1); }
  { RadioLibWrapper w; configure(w); w._params_valid=false; blocked(w); }
  { RadioLibWrapper w; configure(w); w._profile_refresh_required=true; blocked(w); }
  { RadioLibWrapper w; configure(w); ++w._profiles.generation[0]; blocked(w); }
  { RadioLibWrapper w; configure(w); w._active_profile=2; blocked(w); }
  { RadioLibWrapper w; configure(w); w._active_profile=1; blocked(w); }
  { RadioLibWrapper w; configure(w); w._cur_freq=910.525f; blocked(w); }
  { RadioLibWrapper w; configure(w); w._profiles.primary.freq=910.525f; blocked(w); }
  { RadioLibWrapper w; configure(w); w._cur_bw=250; blocked(w); }
  { RadioLibWrapper w; configure(w); w._cur_sf=6; blocked(w); }
  { RadioLibWrapper w; configure(w); w._cur_cr=6; blocked(w); }
  { RadioLibWrapper w; configure(w); ++w._physical_preamble; blocked(w); }
  { RadioLibWrapper w; configure(w); w._physical_protocol=mesh::RadioProfileProtocol::Ethernet; blocked(w); }
  { RadioLibWrapper w; configure(w); w._cur_freq=std::numeric_limits<float>::quiet_NaN(); blocked(w); }
  { RadioLibWrapper w; configure(w); auto p=beacon(); p.freq=910.525f;
    assert(w.prepareBeaconTransmit(p,10)==Result::FAILED);
    assert(w.applications==0 && !w._beacon_owned); blocked(w); }
  { RadioLibWrapper w; configure(w,910.525f); auto p=beacon(); p.freq=909.5f;
    assert(w.prepareBeaconTransmit(p,10)==Result::APPLIED);
    assert(w._cur_freq==910.525f && w.modem.freq==909.5f);
    assert(w.startSendRaw(bytes,14)); assert(w.board.before==1 && w.modem.starts==1);
    w.onSendFinished(); w.modem.param_fail=true; w.deep_ok=false;
    assert(w.restoreBeaconReceive()==Result::BUSY);
    assert(w._hil_beacon_tx_frequency_mhz==0 && !w.hilPacketTransmitAllowed()); }
  { RadioLibWrapper w; configure(w); auto p=beacon(); p.freq=909.5f;
    w.modem.param_fail=true;
    assert(w.prepareBeaconTransmit(p,10)==Result::FAILED);
    assert(w._hil_beacon_tx_frequency_mhz==0); blocked(w); }
}
'''
        # After rejecting an unsafe beacon, the normal allowed profile can
        # still transmit. That case only checks the beacon's absence of RF.
        code = code.replace("assert(w.applications==0 && !w._beacon_owned); blocked(w);",
                            "assert(w.applications==0 && !w._beacon_owned && w.board.before==0 && w.modem.starts==0);")
        self.compile_run(code)

    def test_transmit_preparation_rejects_unsafe_tuple_before_retune(self):
        code = r'''
#include <cassert>
#include <RadioProfiles.h>
#include <helpers/radiolib/HilTxFrequencyGuard.h>
namespace mesh { enum class RadioParamApplyResult { APPLIED, BUSY, FAILED }; }
struct RadioLibWrapper {
  mesh::RadioProfiles _profiles;
  unsigned tunes=0;
  bool isChipBusy() const { return false; }
  bool isPacketPendingOrReceiving() const { return false; }
  mesh::RadioParamApplyResult tuneProfile(uint8_t) { ++tunes; return mesh::RadioParamApplyResult::APPLIED; }
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t, bool);
};
'''
        code += method(SOURCE.read_text(), "mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile, bool")
        code += r'''
int main() {
  using Result=mesh::RadioParamApplyResult;
  RadioLibWrapper w; w._profiles.primary.freq=910.525f;
  w._profiles.secondary.params.freq=909.5f;
  w._profiles.secondary.mode=mesh::RadioProfileMode::RxTx;
  assert(w.prepareTransmitProfile(0,false)==Result::FAILED && w.tunes==0);
  assert(w.prepareTransmitProfile(255,false)==Result::FAILED && w.tunes==0);
  assert(w.prepareTransmitProfile(1,false)==Result::APPLIED && w.tunes==1);
}
'''
        self.compile_run(code)

    def test_carrier_and_morse_actual_entry_points(self):
        code = production_harness().split("int main() {", 1)[0]
        code = code.replace("#include <cassert>", "#include <cassert>\n"
                            "#include <helpers/radiolib/HilTxFrequencyGuard.h>")
        code = code.replace("float _cur_freq=909.5;", "float _cur_freq=909.5, _cur_bw=500;\n"
                            "uint8_t _cur_sf=5, _cur_cr=5; uint16_t _physical_preamble=128;\n"
                            "uint32_t _profile_generation=1; bool hilPacketTransmitAllowed() const;\n"
                            "uint16_t profilePreamble(uint8_t p) const { return _profiles.preamble(p,32); }")
        code = code.replace("_profiles.primary.bw=500;", "_profiles.primary.bw=500;\n"
                            "_profiles.primary.sf=_profiles.secondary.params.sf=5;\n"
                            "_profiles.primary.cr=_profiles.secondary.params.cr=5;")
        code = code.replace("chip.frequency=frequency;", "chip.frequency=frequency;\n"
                            "_cur_bw=_profiles.params(target).bw; _cur_sf=_profiles.params(target).sf;\n"
                            "_cur_cr=_profiles.params(target).cr; _profile_generation=_profiles.generation[target];\n"
                            "_physical_preamble=profilePreamble(target);")
        code += method(SOURCE.read_text(), "bool RadioLibWrapper::hilPacketTransmitAllowed() const")
        code += r'''
int main() {
  using Result=mesh::RadioParamApplyResult;
  { RadioLibWrapper w; w._profiles.primary.freq=910.525f;
    assert(w.setCarrierWave(0,100)==Result::FAILED);
    assert(w.board.before==0 && w.chip.starts==0 && w.tunes==0); }
  { RadioLibWrapper w; assert(w.setCarrierWave(0,100)==Result::APPLIED);
    assert(w.board.before==1 && w.chip.starts==1);
    ++w._profiles.generation[0]; assert(!w.setTxPower(10));
    assert(w.chip.starts==1); assert(w.serviceCarrierWave()); assert(!w.chip.carrier); }
  { RadioLibWrapper w; w._profile_refresh_required=true;
    assert(w.setCarrierWave(0,100)==Result::FAILED);
    assert(w.board.before==0 && w.chip.starts==0); }
  { RadioLibWrapper w;
    assert(w.startMorseTx(910.525f,25,"EE")==mesh::MorseTxResult::InvalidFrequency);
    assert(w.board.before==0 && w.chip.starts==0 && w.board.frequency_prepares==0); }
  { RadioLibWrapper w; w._profiles.primary.freq=910.525f;
    assert(w.startMorseTx(909.5f,25,"EE")==mesh::MorseTxResult::Started);
    assert(w.board.before==1 && w.chip.starts==1 && w.chip.frequency==909.5f);
    w._morse_frequency=910.525f; assert(w.serviceMorseTx());
    assert(w.chip.starts==1 && !w.chip.carrier); }
}
'''
        self.compile_run(code, ("-DMESH_ENABLE_MORSE_RX=0",))

    def test_read_only_runtime_preflight_and_production_default(self):
        code = r'''
#include <cassert>
#include <cstring>
#include <limits>
#include <helpers/radiolib/HilTxFrequencyGuard.h>
int main() {
  char reply[160]="unchanged";
#if defined(MESH_HIL_TX_FREQ_MHZ)
  assert(mesh::handleHilTxFrequencyCommand("get lab.tx",reply,sizeof(reply)));
  assert(strcmp(reply,"> HIL TX lock=909.500 MHz; off-frequency TX blocked; primary RX-only when off-frequency")==0);
  assert(mesh::hilTransmitFrequencyAllowed(909.5f));
  assert(!mesh::hilTransmitFrequencyAllowed(910.525f));
  assert(!mesh::hilTransmitFrequencyAllowed(std::numeric_limits<float>::quiet_NaN()));
  assert(!mesh::handleHilTxFrequencyCommand("set lab.tx 910.525",reply,sizeof(reply)));
#else
  assert(!mesh::handleHilTxFrequencyCommand("get lab.tx",reply,sizeof(reply)));
  assert(strcmp(reply,"unchanged")==0);
  assert(mesh::hilTransmitFrequencyAllowed(910.525f));
#endif
}
'''
        self.compile_run(code)
        self.compile_run(code, guard=False)
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        self.assertIn("#if defined(MESH_HIL_TX_FREQ_MHZ)\n"
                      "  if (mesh::handleHilTxFrequencyCommand(command, reply, reply_size)) return true;\n#endif", source)


if __name__ == "__main__":
    unittest.main()
