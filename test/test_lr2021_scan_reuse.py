"""Exercise the production LR2021 scan ownership and command selection."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_lr2021_apply_transaction import method

ROOT = Path(__file__).resolve().parents[1]


class LR2021ScanReuseTests(unittest.TestCase):
    def test_owned_scan_and_invalidation(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ unavailable")
        source = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        signatures = {
            "INVALIDATE": "void invalidateScanTuple(",
            "BEGIN": "void beginProfileRetune(",
            "END": "void endProfileRetune(",
            "MATCH": "bool scanTargetMatches(",
            "APPLY": "bool applyParams(",
            "SEND": "bool startSendRaw(",
            "CR": "bool setCodingRate(",
            "RX": "int startReceiveMode(",
        }
        snippets = {name: method(source, signature).replace(" override", "")
                    for name, signature in signatures.items()}
        code = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <vector>
#include <RadioProfiles.h>
#include <helpers/radiolib/LR2021Band.h>
#include <helpers/radiolib/LR2021ApplyStatus.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -1
#define LORA_TX_POWER 5
#define RADIOLIB_LR2021_CAL_IMG_FREQ_TRIG_MHZ 20.0f
#define MESH_ENABLE_TX_BEACONS 0
#define MESH_ENABLE_MESHTEXT_WIRE 0
#define MESH_ENABLE_ETHERNET_WIRE 0
#define MESH_ENABLE_HOST_KISS 0
namespace mesh { int8_t clampLoRaTxPower(int8_t p,float) { return p; } }
struct PacketMillis { uint32_t preambleMillis, payloadMillis; };
enum Command { Frequency, Power, Sf, Bw, Cr, Preamble, Side, Standby, Receive };
struct CustomLR2021 {
  std::vector<Command> trace;
  int fail=-1;
  float physicalFrequency=910.525f;
  int16_t run(Command c) {
    trace.push_back(c);
    if (int(c)==fail) { fail=-1; return -705; }
    return 0;
  }
  int16_t setFrequency(float f) {
    const int16_t result=run(Frequency);
    if (!result) physicalFrequency=f;
    return result;
  }
  float getFreqMHz() const { return physicalFrequency; }
  int16_t setOutputPower(int8_t) { return run(Power); }
  int16_t setSpreadingFactor(uint8_t) { return run(Sf); }
  int16_t setBandwidth(float) { return run(Bw); }
  int16_t setCodingRate(uint8_t) { return run(Cr); }
  int16_t setPreambleLength(uint16_t) { return run(Preamble); }
  void setPreambleMillis(uint32_t) {}
  void setMaxPayloadMillis(uint32_t) {}
  int16_t standby() { return run(Standby); }
  int16_t startReceive() { return run(Receive); }
};
struct Board { bool ready=true; bool prepareRadioFrequency(float) { return ready; } };
struct RadioLibWrapper { bool startSendRaw(const uint8_t*,int) { return true; } };
struct Wrapper : RadioLibWrapper {
  mesh::lr2021::ApplyStatus _apply_status;
  CustomLR2021 chip; CustomLR2021* _radio=&chip;
  Board board; Board* _board=&board;
  mesh::RadioProfiles _profiles;
  uint8_t _active_profile=0, _cur_sf=7, _cur_cr=5;
  float _cur_freq=910.525f, _cur_bw=62.5f;
  uint32_t _profile_generation=1;
  bool _params_valid=true,_profile_refresh_required=false,_profile_repair_required=false;
  bool _dbm_valid=true; int8_t _cur_dbm=5;
  bool _scan_tuple_owned=false,_scan_retune=false,_scan_tuple_applied=false;
  Wrapper() {
    _profiles.primary.freq=910.525f; _profiles.primary.bw=62.5f;
    _profiles.primary.sf=7; _profiles.primary.cr=5;
    _profiles.secondary.params=_profiles.primary;
    _profiles.secondary.params.freq=911.15f; _profiles.secondary.params.bw=250.f;
    _profiles.secondary.mode=mesh::RadioProfileMode::RxTx;
  }
  int16_t recordApply(mesh::lr2021::ApplyStatus::Stage s,int16_t c) { return _apply_status.record(s,c); }
  uint16_t preambleLengthForParams(uint8_t,float) { return 32; }
  bool applySideDetectorConfig(uint8_t,float) { return chip.run(Side)==0; }
  PacketMillis calcMaxPacketMillis(uint8_t,float,uint8_t,uint16_t) { return {44,440}; }
  float hardwareFrequencyMHz(float f) const { return f; }
  bool isCarrierWaveActive() const { return false; }
  @INVALIDATE@
  @BEGIN@
  @END@
  @MATCH@
  @APPLY@
  @SEND@
  @CR@
  @RX@
  bool hop(bool continuous=true, bool defer=false) {
    beginProfileRetune(continuous);
    if (defer) { endProfileRetune(true); return false; }
    const auto old=_profiles.params(_active_profile);
    const uint8_t old_profile=_active_profile;
    const auto p=_profiles.params(_active_profile^1);
    bool ok=applyParams(p.freq,p.bw,p.sf,p.cr);
    if (ok) {
      _cur_freq=p.freq;_cur_bw=p.bw;_cur_sf=p.sf;_cur_cr=p.cr;
      _active_profile^=1;_profile_generation=_profiles.generation[_active_profile];
      ok=startReceiveMode()==0;
    }
    endProfileRetune(ok);
    if (!ok) {
      // Mirror tuneProfile's rollback after apply or RX-restart failure. It
      // must use the full setter path even when the failed hop was optimized.
      _active_profile=old_profile;_profile_generation=_profiles.generation[old_profile];
      _cur_freq=old.freq;_cur_bw=old.bw;_cur_sf=old.sf;_cur_cr=old.cr;
      assert(applyParams(old.freq,old.bw,old.sf,old.cr));
      assert(startReceiveMode()==0);
    }
    return ok;
  }
  bool saw(Command c) const { for (auto x:chip.trace) if(x==c)return true; return false; }
};
int main() {
  Wrapper w;
#if !defined(MESHNOLOGY_W12) || (defined(MESH_DISABLE_LR2021_SCAN_REUSE) && MESH_DISABLE_LR2021_SCAN_REUSE)
  // A different LR2021 board retains the full pre-existing retune path.
  assert(w.hop()); assert(w.saw(Sf)&&w.saw(Cr));
  w.chip.trace.clear(); assert(w.hop()); assert(w.saw(Sf)&&w.saw(Cr));
#else
  assert(w.hop()); assert(w.saw(Sf)&&w.saw(Cr)&&w.saw(Bw)); // first hop establishes ownership
  w.chip.trace.clear(); assert(w.hop());
  assert(!w.saw(Sf)&&!w.saw(Cr)&&w.saw(Bw)&&w.saw(Power)&&w.saw(Preamble)&&w.saw(Side));
  // A deferred BUSY hop neither modifies hardware nor discards known ownership.
  w.chip.trace.clear(); assert(!w.hop(true,true)); assert(w.chip.trace.empty());
  assert(w.hop()); assert(!w.saw(Sf)&&!w.saw(Cr));
  // Change SF alone; BW is still written and CR is reused.
  auto& next1=w._active_profile ? w._profiles.primary : w._profiles.secondary.params;
  next1.sf=8;
  w.chip.trace.clear(); assert(w.hop()); assert(w.saw(Sf)&&!w.saw(Cr)&&w.saw(Bw));
  // Change CR alone; SF is reused and CR is written.
  auto& target=w._active_profile ? w._profiles.primary : w._profiles.secondary.params;
  target.sf=w._cur_sf;target.cr=7;
  w.chip.trace.clear(); assert(w.hop()); assert(!w.saw(Sf)&&w.saw(Cr)&&w.saw(Bw));
  // Packet TX, explicit CR, normal RX restart, and failed apply revoke reuse.
  uint8_t byte=0;w.startSendRaw(&byte,1);
  w.chip.trace.clear(); assert(w.hop()); assert(w.saw(Sf)&&w.saw(Cr));
  assert(w.setCodingRate(5));
  w.chip.trace.clear();assert(w.hop());assert(w.saw(Sf)&&w.saw(Cr));
  assert(w.startReceiveMode()==0);
  w.chip.trace.clear();assert(w.hop());assert(w.saw(Sf)&&w.saw(Cr));
  w.chip.fail=Bw;w.chip.trace.clear();assert(!w.hop());assert(!w._scan_tuple_owned);
  assert(w.saw(Sf)&&w.saw(Cr)); // rollback did not reuse a partial tuple
  w.chip.fail=-1;w.chip.trace.clear();assert(w.hop());assert(w.saw(Sf)&&w.saw(Cr));
  w.chip.fail=Receive;w.chip.trace.clear();assert(!w.hop());assert(!w._scan_tuple_owned);
  assert(w.saw(Sf)&&w.saw(Cr)); // failed RX restart restored the prior tuple fully
  w.chip.trace.clear();assert(w.hop());assert(w.saw(Sf)&&w.saw(Cr));
  // A cold/forced refresh and a stale generation never reuse old state.
  w._profile_refresh_required=true;w.chip.trace.clear();assert(w.hop());assert(w.saw(Sf)&&w.saw(Cr));
  w._profile_refresh_required=false;w.chip.trace.clear();assert(w.hop());assert(w.saw(Sf)&&w.saw(Cr));
  ++w._profiles.generation[w._active_profile];w.chip.trace.clear();assert(w.hop());
  assert(w.saw(Sf)&&w.saw(Cr));
  // Recalibration-size or LF/HF-crossing hops stay on the full path.
  Wrapper far;assert(far.hop());assert(far._scan_tuple_owned);
  far._profiles.primary.freq=far._cur_freq-20.f;
  far.chip.trace.clear();assert(far.hop());
  assert(far.saw(Sf)&&far.saw(Cr));
  Wrapper bands;assert(bands.hop());assert(bands._scan_tuple_owned);
  bands._profiles.primary.freq=2440.f;
  bands.chip.trace.clear();assert(bands.hop());
  assert(bands.saw(Sf)&&bands.saw(Cr));
  // An out-of-band chip change invalidates the wrapper's physical cache.
  Wrapper stale;assert(stale.hop());stale.chip.physicalFrequency+=1.f;
  stale.chip.trace.clear();assert(stale.hop());assert(stale.saw(Sf)&&stale.saw(Cr));
#endif
}
'''
        for name, snippet in snippets.items():
            code = code.replace("@" + name + "@", snippet)
        with tempfile.TemporaryDirectory(prefix="lr2021-scan-reuse-") as temp:
            cpp = Path(temp) / "test.cpp"
            exe = Path(temp) / "test"
            cpp.write_text(code)
            for flags in ([], ["-DMESHNOLOGY_W12=1"],
                          ["-DMESHNOLOGY_W12=1", "-DMESH_DISABLE_LR2021_SCAN_REUSE=1"]):
                with self.subTest(flags=flags):
                    build = subprocess.run([compiler, "-std=c++17", *flags,
                                            "-I", str(ROOT / "src"), str(cpp),
                                            "-o", str(exe)], capture_output=True, text=True)
                    self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                    run = subprocess.run([str(exe)], capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
