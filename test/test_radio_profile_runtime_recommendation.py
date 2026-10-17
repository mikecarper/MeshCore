"""Delay displayed advice without changing the scanner's measured-max policy."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import test_radio_profile_scan as scan
from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]


class RuntimeRecommendationTest(unittest.TestCase):
    def compile_run(self, source):
        with tempfile.TemporaryDirectory() as folder:
            cpp, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            (Path(folder) / "Arduino.h").write_text("#pragma once\n#include <cstdint>\n#include <cmath>\n")
            cpp.write_text(source, encoding="ascii")
            built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17",
                "-Wall", "-Wextra", "-fsanitize=address,undefined", "-no-pie",
                "-fno-omit-frame-pointer", "-I", folder, "-I", str(ROOT / "src"),
                str(cpp), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_clock_boundaries_resets_and_unchanged_maximum_budget(self):
        self.compile_run(r'''
#include <RadioProfiles.h>
#include <cassert>
#include <cstdint>
#include <initializer_list>
using P=mesh::RadioProfiles;
P pair() {
  P p;p.primary={909.5f,500,0,7,5};p.secondary.params=p.primary;
  p.secondary.params.freq=910.5f;p.secondary.mode=mesh::RadioProfileMode::Rx;
  return p;
}
void train(P& p,uint32_t now,uint32_t a=100,uint32_t b=100) {
  for(unsigned n=0;n<P::SwitchTestSamplesPerDirection;++n) {
    p.sampleSwitch(0,1,a,now);p.sampleSwitch(1,0,b,now);
  }
  assert(p.switchTestReady() && !p.recommendationReady());
  assert(p.switch_test_samples[0]==4 && p.switch_test_samples[1]==4);
}
void same(const P& a,const P& b) {
  assert(a.switch_runtime_started==b.switch_runtime_started);
  assert(a.switch_runtime_ready==b.switch_runtime_ready);
  assert(a.switch_runtime_started_ms==b.switch_runtime_started_ms);
  for(unsigned i=0;i<2;++i) {
    assert(a.switch_test_samples[i]==b.switch_test_samples[i]);
    assert(a.switch_test_max_us[i]==b.switch_test_max_us[i]);
  }
}
int main() {
  // Zero is a valid real start, and unsigned millisecond wrap is harmless.
  for(uint32_t start:{0u,1234u,UINT32_MAX-60000u}) {
    auto p=pair();assert(p.recommendationRemainingMs(start)==120000);
    train(p,start);assert(p.switch_runtime_started && p.switch_runtime_started_ms==start);
    assert(p.switchBudgetUs()==110 && p.preamble(0,32)==32);
    const auto wire_a=p.preamble(0,32),wire_b=p.preamble(1,32);
    const auto dwell_a=p.listenUs(0),dwell_b=p.listenUs(1);
    p.sampleSwitch(0,1,100,start+119999u);
    assert(!p.recommendationReady() && p.recommendationRemainingMs(start+119999u)==1);
    p.sampleSwitch(1,0,100,start+120000u);
    assert(p.recommendationReady() && p.recommendationRemainingMs(start+120000u)==0);
    assert(p.switchBudgetUs()==110);
    assert(p.preamble(0,32)==wire_a && p.preamble(1,32)==wire_b);
    assert(p.listenUs(0)==dwell_a && p.listenUs(1)==dwell_b);
    p.sampleSwitch(0,1,8428,start+120001u);
    assert(p.switch_test_max_us[0]==8428 && p.switchBudgetUs()==9271);
    assert(p.preamble(0,32)==168); // late maximum still affects real scheduling
    assert(p.switch_test_samples[0]==4 && p.switch_test_samples[1]==4);
    p.sampleSwitch(1,0,UINT32_MAX,start+120002u);
    assert(p.switchBudgetUs()==UINT32_MAX); // existing overflow-safe guard
  }
  // The old provisional maximum adapts immediately BEFORE two minutes too.
  {
    auto p=pair();train(p,0);p.sampleSwitch(0,1,8428,1000);
    assert(!p.recommendationReady() && p.switchBudgetUs()==9271);
    assert(p.preamble(0,32)==168);
    p.sampleSwitch(1,0,8428,120000);
    assert(p.recommendationReady() && p.switchBudgetUs()==9271);
  }
  // Invalid work cannot start the clock; elapsed time without the four good
  // samples in EACH direction is not recommendation readiness.
  {
    auto p=pair();p.sampleSwitch(0,0,100,120000);p.sampleSwitch(0,1,0,120000);
    p.sampleSwitch(2,0,100,120000);p.sampleSwitch(0,2,100,120000);
    assert(!p.switch_runtime_started && !p.recommendationReady());
    p.sampleSwitch(0,1,100,0);p.sampleSwitch(0,1,100,120000);
    assert(!p.switchTestReady() && !p.recommendationReady());
    assert(p.recommendationRemainingMs(120000)==0);
    for(unsigned n=0;n<4;++n) {p.sampleSwitch(0,1,100,120001);p.sampleSwitch(1,0,100,120001);}
    assert(p.switchTestReady() && p.recommendationReady());
  }
  // Pair/lease changes reset the complete timer. No-op publication and a
  // whole-object rollback preserve it, together with the unchanged maxima.
  {
    auto p=pair();train(p,0);p.sampleSwitch(0,1,100,120000);const auto old=p;
    p.setPrimary(p.primary,p.primary_temporary);p.setSecondary(p.secondary,p.secondary_temporary);same(p,old);
    auto changed=p.primary;changed.freq+=1;p.setPrimary(changed,false);
    assert(!p.switchTestReady() && !p.recommendationReady() && !p.switch_runtime_started);
    assert(p.switchBudgetUs()==0 && p.recommendationRemainingMs(120000)==120000);
    p=old;same(p,old);p.setSecondary(p.secondary,!p.secondary_temporary);
    assert(!p.recommendationReady() && !p.switch_runtime_started);
    p=old;auto disabled=p.secondary;disabled.mode=mesh::RadioProfileMode::Off;
    p.setSecondary(disabled,false);p.sampleSwitch(0,1,100,120000);
    assert(!p.switch_runtime_started && !p.recommendationReady());
  }
}
''')

    def test_actual_scanner_keeps_bounded_startup_and_natural_visits(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        signatures = [("uint8_t", "beginReconfigure"), ("void", "endReconfigure"),
                      ("void", "serviceProfileScan")]
        signatures += [("mesh::RadioParamApplyResult", name) for name in (
            "tuneProfile", "prepareTransmitProfile", "tryRestoreCodingRate", "trySetParams", "trySetPrimaryParams")]
        methods = "\n".join(method(source, f"{kind} RadioLibWrapper::{name}(") for kind, name in signatures)
        methods += "\n" + method(source, "mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile, bool")
        wrapper = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text()
        methods += "\n" + method(wrapper, "void setProfileStandbyWarm(").replace(
            "void setProfileStandbyWarm(bool enabled) override",
            "void RadioLibWrapper::setProfileStandbyWarm(bool enabled)")
        fixture = scan.HARNESS[:scan.HARNESS.index("int main()")].replace("@METHODS@", methods)
        fixture += r'''
int main() {
  RadioLibWrapper w;w.enable();
  assert(w._profiles.switchTestReady() && !w._profiles.recommendationReady());
  const auto applies=w.applies;
  for(unsigned i=0;i<16;++i) w.serviceProfileScan();
  assert(w.applies==applies); // not repeated startup bursts while advice waits
  const auto started=w._profiles.switch_runtime_started_ms;
  elapsed_us=(uint64_t(started)+119999)*1000;
  w.serviceProfileScan();
  assert(w._profiles.recommendationReady() && w.applies==applies+1);
  assert(w._profiles.switchBudgetUs()==1320);
  const auto old=w._profiles;
  w.packet=true;auto requested=w._profiles.primary;requested.preamble=64;
  assert(w.trySetPrimaryParams(requested,true)==mesh::RadioParamApplyResult::BUSY);
  assert(w._profiles.recommendationReady() && w._profiles.switch_runtime_started_ms==old.switch_runtime_started_ms);
}
'''
        self.compile_run(fixture)


if __name__ == "__main__":
    unittest.main()
