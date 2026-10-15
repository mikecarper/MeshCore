"""Verify foreign-profile scanning preserves the MeshCore network wire format.

These are production timing/peer-RXPS tests, not an RF-delivery assertion.
No PlatformIO or connected radios are used.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <RadioProfiles.h>
#include <helpers/radiolib/RXPowerSaving.h>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <initializer_list>
using namespace mesh;

static RadioProfiles pair(uint8_t sf=7,float bw=62.5f) {
  RadioProfiles p;
  p.primary.freq=910.525f;p.primary.bw=bw;p.primary.sf=sf;p.primary.cr=5;
  p.secondary.params.freq=906.875f;p.secondary.params.bw=250;
  p.secondary.params.sf=11;p.secondary.params.cr=5;
  p.secondary.params.preamble=16;
  p.secondary.params.protocol=RadioProfileProtocol::Meshtext;
  p.secondary.mode=RadioProfileMode::RxTx;
  return p;
}
static void measured(RadioProfiles& p,uint32_t switch_us=1000) {
  for(unsigned i=0;i<RadioProfiles::SwitchTestSamplesPerDirection;++i) {
    p.sampleSwitch(0,1,switch_us);p.sampleSwitch(1,0,switch_us);
  }
  assert(p.switchTestReady());
}
static uint16_t wire(const RadioProfiles& p,uint8_t profile) {
  const auto& tuple=p.params(profile);
  // The production wrapper passes this same canonical single-profile value.
  return p.preamble(profile,rxPowerSavingPreambleForParams(tuple.sf,tuple.bw));
}
'''


class MeshtextPrimaryPreambleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("a native C++17 compiler is required")

    def run_native(self, cases, extra_flags=()):
        with tempfile.TemporaryDirectory(prefix="meshtext-primary-preamble-") as folder:
            cpp, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            cpp.write_text(HARNESS + cases, encoding="utf-8")
            flags = ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-DMESH_ENABLE_MESHTEXT_CHAT=1"]
            flags += list(extra_flags)
            if os.name != "nt":
                flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            built = subprocess.run([self.compiler, *flags, "-isystem", str(ROOT / "test/mocks"),
                                    "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                                   text=True, capture_output=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], text=True, capture_output=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_canonical_wire_defaults_before_and_after_scan_measurement(self):
        self.run_native(r'''
int main() {
  struct Tuple{uint8_t sf;float bw;uint16_t expected;};
  for(const auto& tuple:{Tuple{5,500,128},Tuple{6,500,64},Tuple{7,62.5f,32},Tuple{11,62.5f,16}}) {
    auto p=pair(tuple.sf,tuple.bw);
    assert(!p.switchTestReady());
    assert(wire(p,0)==tuple.expected); // do not publish provisional128/512 on the normal network
    assert(wire(p,1)==16);
    measured(p);
    assert(wire(p,0)==tuple.expected && wire(p,1)==16);
    assert(p.automaticPreambleFits());
    for(uint8_t profile:{0,1}) {
      assert(p.listenUs(profile)>=RadioProfiles::minimumListenUs(p.params(profile),profile==p.slowerProfile()));
      assert(RadioProfiles::safePreamble(p.params(profile),wire(p,profile)));
    }
    // The foreign receiver cannot advertise our scan timing to other peers.
    for(auto cross:{RadioCrossMode::Auto,RadioCrossMode::On,RadioCrossMode::Off}) {
      p.cross=cross;assert(!p.canCross());assert(wire(p,0)==tuple.expected);
    }
  }
}
''')

    def test_explicit_override_and_dual_meshcore_auto_are_preserved(self):
        self.run_native(r'''
int main() {
  auto p=pair();measured(p);
  assert(p.automaticPreamble(0)>32 && wire(p,0)==32);
  for(uint16_t explicit_preamble:{uint16_t(16),uint16_t(40),uint16_t(64),uint16_t(128),uint16_t(512)}) {
    p.primary.preamble=explicit_preamble;
    assert(wire(p,0)==explicit_preamble && wire(p,1)==16);
  }
  p.primary.preamble=0;
  p.secondary.params.protocol=RadioProfileProtocol::MeshCore;
  assert(wire(p,0)==uint16_t(p.automaticPreamble(0)) && wire(p,0)>32);
  p.resetSwitchTest();assert(wire(p,0)==RadioProfiles::MaxAutomaticPreamble);
  p.secondary.mode=RadioProfileMode::Off;assert(wire(p,0)==32);
}
''')

    def test_reported_usa_scan_dwell_and_peer_rxps_header_budget(self):
        self.run_native(r'''
int main() {
  auto p=pair();measured(p);
  assert(p.slowerProfile()==1 && p.automaticPreamble(0)==40);
  const auto primary_visit=p.listenUs(0),foreign_visit=p.listenUs(1);
  const auto timing=p.chirpTiming();
  assert(timing.valid && timing.preamble[0]>32); // retain the truthful scan recommendation
  // LongFast is slower, so its fixed16 drives dwell. Canonical MC TX does
  // not silently shorten either receive visit in this reported configuration.
  p.primary.preamble=40;
  assert(p.listenUs(0)==primary_visit && p.listenUs(1)==foreign_visit);
  p.primary.preamble=0;assert(wire(p,0)==32);
  uint32_t rx=0,sleep=0;
  assert(calcRxPowerSavingLevelAtOrAbove(6,7,62.5,16,2600,&rx,&sleep));
  const double timer_us=(2*((rx*8UL)/125UL)+((sleep-2600)*8UL)/125UL)*RX_POWERSAVING_TICK_US;
  const double symbols=RadioProfiles::symbolUs(p.primary);
  const double header_symbols=RX_POWERSAVING_SYNC_SYMBOLS+RX_POWERSAVING_HEADER_SYMBOLS+RX_POWERSAVING_TIMER_SLACK_SYMBOLS;
  assert(timer_us>=(wire(p,0)+header_symbols)*symbols);
  assert(timer_us<(p.automaticPreamble(0)+header_symbols)*symbols); // old adaptive wire was not covered
}
''')

    def test_t1000_lr1110_actual_duty_cycle_encoding_covers_canonical_wire(self):
        variant = (ROOT / "variants/t1000-e/platformio.ini").read_text()
        self.assertIn("-D USE_LR1110", variant)
        self.assertIn("-D MC_TCXO_DELAY_US=200", variant)
        production = method(
            (ROOT / "src/helpers/radiolib/CustomLR1110.h").read_text(),
            "int16_t startReceiveDutyCycle(",
        )
        self.run_native(r'''
using RadioLibIrqFlags_t=uint32_t;
#define RADIOLIB_IRQ_RX_DEFAULT_FLAGS 7
#define RADIOLIB_IRQ_RX_DEFAULT_MASK 1
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_INVALID_SLEEP_PERIOD -1
#define RADIOLIB_ERR_INVALID_RX_PERIOD -2
#define RADIOLIB_LR11X0_STANDBY_RC 0
#define RADIOLIB_LR11X0_LF_CLK_RC 0
#define RADIOLIB_LR11X0_LF_BUSY_RELEASE_ENABLED 1
#define RADIOLIB_LR11X0_RX_TIMEOUT_INF 0xffffff
#define RADIOLIB_RADIO_MODE_RX 1
#define RADIOLIB_LR11X0_RX_DUTY_CYCLE_MODE_RX 0
#define RADIOLIB_ASSERT(result) do {if((result)!=0)return result;}while(0)
struct RadioModeConfig_t {
  struct {uint32_t timeout,irqFlags,irqMask,len;} receive;
};
struct Lr1110DutyCycleProbe {
  uint8_t spreadingFactor=7;
  float bandwidthKhz=62.5;
  uint32_t tcxoDelay=200;
  uint16_t preambleLengthLoRa=32;
  uint32_t rx_ticks=0,sleep_ticks=0;
  unsigned standby_calls=0,clock_calls=0,stage_calls=0,command_calls=0;
  int16_t standby(uint8_t mode) {assert(mode==0);++standby_calls;return 0;}
  int16_t configLfClock(uint8_t mode) {assert(mode==1);++clock_calls;return 0;}
  int16_t stageMode(uint8_t mode,RadioModeConfig_t* config) {
    assert(mode==1 && config->receive.timeout==0xffffff);
    assert(config->receive.irqFlags==7 && config->receive.irqMask==1);
    ++stage_calls;return 0;
  }
  int16_t setRxDutyCycle(uint32_t rx,uint32_t sleep,uint8_t mode) {
    assert(mode==0);rx_ticks=rx;sleep_ticks=sleep;++command_calls;return 0;
  }
  @PRODUCTION@
};
int main() {
  // Execute the real LR1110 method, not an SX1262 tick conversion or a
  // reimplementation of its receiver-side preamble guard.
  uint32_t rx=0,sleep=0;
  assert(rxPowerSavingDefaultCaptureCost()==8 && rxPowerSavingDefaultTransitionUs()==1200);
  assert(calcRxPowerSavingLevelAtOrAbove(6,7,62.5,16,1200,&rx,&sleep));
  assert(rx==41016 && sleep==11872);
  Lr1110DutyCycleProbe peer;
  assert(peer.startReceiveDutyCycle(rx,sleep)==0);
  assert(peer.standby_calls==1 && peer.clock_calls==1 && peer.stage_calls==1 && peer.command_calls==1);
  assert(peer.rx_ticks==1344 && peer.sleep_ticks==349);
  const double timer_us=(2.0*peer.rx_ticks+peer.sleep_ticks)*1000000.0/32768;
  assert(std::fabs(timer_us-92681.884765625)<0.001);
  auto sender=pair();measured(sender);
  assert(wire(sender,0)==32 && sender.automaticPreamble(0)==40);
  const double symbol_us=RadioProfiles::symbolUs(sender.primary);
  // Exclude the model's extra safety symbol: the old wire still exceeds
  // this peer's post-detection header budget. Detection phase affects loss;
  // these bounds are not an assertion that every RF packet must be lost.
  const double header=RX_POWERSAVING_SYNC_SYMBOLS+RX_POWERSAVING_HEADER_SYMBOLS;
  assert(timer_us>=(wire(sender,0)+header)*symbol_us);
  assert(timer_us<(sender.automaticPreamble(0)+header)*symbol_us);
}
'''.replace("@PRODUCTION@", production),
            extra_flags=("-DUSE_LR1110", "-DMC_TCXO_DELAY_US=200"))


if __name__ == "__main__":
    unittest.main()
