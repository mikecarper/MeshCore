"""Exercise scan-health clocks, production scan ownership and bounded CLI replies."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import test_radio_profile_scan as scan
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


class RadioProfileHealthTest(unittest.TestCase):
    def compile_run(self, source, cli=False):
        with tempfile.TemporaryDirectory(prefix="radio-profile-health-") as folder:
            cpp, binary = Path(folder) / "test.cpp", Path(folder) / "test.exe"
            (Path(folder) / "Arduino.h").write_text(
                "#pragma once\n#include <cstdint>\n#include <cmath>\n", encoding="ascii")
            cpp.write_text(source, encoding="utf-8")
            includes = ["-I", str(ROOT)]
            sources = [str(cpp)]
            if cli:
                includes += ["-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                             "-I", str(ROOT / "test/mocks")]
                sources += [str(ROOT / "src/helpers/RadioProfileCLI.cpp")]
            else:
                includes += ["-I", folder]
            includes += ["-I", str(ROOT / "src")]
            built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
                                    *includes, *sources, "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_clocks_hold_classification_saturation_and_resets(self):
        self.compile_run(r'''
#include <helpers/RadioProfileScanHealth.h>
#include <cassert>
using H=mesh::RadioProfileScanHealth;
static_assert(sizeof(H)<=80, "scan health must remain a small fixed allocation");
int main() {
  H h;
  h.service(100,0); h.service(150,0); assert(h.service_gap_us==50);
  h.visit(1,200); h.visit(0,300); assert(h.profile[0].idle_gap_us==200);
  h.hold(H::Receive); h.finishVisit(0,1000,100);
  assert(h.profile[0].late_visits==0);
  h.visit(1,500); h.visit(0,600);
  assert(h.profile[0].busy_gap_us==300 && h.profile[1].busy_gap_us==300);
  assert(h.profile[0].rx_holds==1 && h.profile[1].rx_holds==1);
  h.finishVisit(0,150,100); h.finishVisit(0,100,100);
  assert(h.profile[0].late_visits==1 && h.profile[0].overdue_us==50);
  h.hold(H::Transmit|H::Receive); h.visit(1,700); h.visit(0,800);
  assert(h.profile[0].tx_holds==1 && h.profile[0].rx_holds==2);
  h.hold(H::Other); h.visit(1,900); h.visit(0,1000);
  assert(h.profile[0].tx_holds==1 && h.profile[0].rx_holds==2);
  h.profile[0].late_visits=UINT32_MAX; h.finishVisit(0,200,100);
  assert(h.profile[0].late_visits==UINT32_MAX);
  h.profile[0].rx_holds=UINT32_MAX; h.hold(H::Receive); h.visit(0,1100);
  assert(h.profile[0].rx_holds==UINT32_MAX);
  h.pause(); h.service(3000000,0); h.visit(1,3000100); h.visit(0,3000200);
  assert(h.profile[0].busy_gap_us==300); // no off-time added to the next session
  H wrap; wrap.service(UINT32_MAX-49,0); wrap.service(50,0);
  wrap.visit(0,75); assert(wrap.service_gap_us==100 && wrap.profile[0].idle_gap_us==125);
  H clock; assert(clock.timestamp(UINT32_MAX-1)==0);
  assert(clock.timestamp(1)==3);
  clock.pause(); assert(clock.timestamp(UINT32_MAX-1,15625,512)==0);
  assert(clock.timestamp(1,15625,512)==91);
  assert(clock.timestamp(510,15625,512)==15625); // exact fractional RTC accumulation
  const auto before=clock.profile[0].late_visits;
  clock.service(1,2); clock.visit(2,2); clock.finishVisit(2,200,1);
  assert(clock.profile[0].late_visits==before);
}
''')

    def test_production_scan_reports_idle_delay_without_counting_receive_holds_as_late(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text(encoding="utf-8")
        names = [("uint8_t", "beginReconfigure"), ("void", "endReconfigure"),
                 ("void", "serviceProfileScan")]
        names += [("mesh::RadioParamApplyResult", name) for name in
                  ("tuneProfile", "prepareTransmitProfile", "tryRestoreCodingRate", "trySetParams",
                   "trySetPrimaryParams")]
        methods = "\n".join(method(source, f"{kind} RadioLibWrapper::{name}(") for kind, name in names)
        methods += "\n" + method(source,
            "mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile, bool")
        wrapper = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text(encoding="utf-8")
        methods += "\n" + method(wrapper, "void setProfileStandbyWarm(").replace(
            "void setProfileStandbyWarm(bool enabled) override",
            "void RadioLibWrapper::setProfileStandbyWarm(bool enabled)")
        harness = scan.HARNESS.replace("int main() {", "int original_scan_main() {", 1)
        harness = harness.replace("@METHODS@", methods)
        self.compile_run(harness + r'''
int main() {
  RadioLibWrapper w; w.enable(); w._nf_refresh_requested=false;
  w.serviceProfileScan();
  auto first=w._active_profile;
  auto dwell=w._profiles.listenUs(first,w.profilePreamble(w._profiles.slowerProfile()));
  elapsed_us+=dwell+500; w.serviceProfileScan();
  assert(w._active_profile!=first);
  assert(w._profiles.scan_health.profile[first].late_visits==1);
  assert(w._profiles.scan_health.profile[first].overdue_us==500);
  const auto current=w._active_profile;
  dwell=w._profiles.listenUs(current,w.profilePreamble(w._profiles.slowerProfile()));
  w.packet=true; elapsed_us+=dwell+2000; w.serviceProfileScan();
  assert(w._active_profile==current);
  w.packet=false; elapsed_us+=200; w.serviceProfileScan();
  assert(w._active_profile!=current);
  assert(w._profiles.scan_health.profile[current].late_visits==0);
  assert(w._profiles.scan_health.profile[first].rx_holds==1);
  w.state=STATE_TX_WAIT; elapsed_us+=30000; w.serviceProfileScan();
  w.state=STATE_RX; elapsed_us+=30000; w.serviceProfileScan();
  assert(w._profiles.scan_health.profile[current].tx_holds==1);
  auto replacement=w._profiles.secondary; replacement.params.freq+=1;
  w._profiles.setSecondary(replacement,false);
  assert(w._profiles.scan_health.service_gap_us==0);

  RadioLibWrapper post_tx; post_tx.enable(); post_tx._nf_refresh_requested=false;
  post_tx.serviceProfileScan();
  post_tx.state=STATE_TX_WAIT; elapsed_us+=30000; post_tx.serviceProfileScan();
  post_tx.state=STATE_IDLE; post_tx.serviceProfileScan();
  // loop() rearms RX separately after an idle/post-TX retune.
  post_tx.state=STATE_RX;
  const auto clean=post_tx._active_profile;
  dwell=post_tx._profiles.listenUs(clean,post_tx.profilePreamble(post_tx._profiles.slowerProfile()));
  elapsed_us+=dwell+500; post_tx.serviceProfileScan();
  assert(post_tx._profiles.scan_health.profile[clean].late_visits==1);
  assert(post_tx._profiles.scan_health.profile[clean].overdue_us==500);

  const auto service_gap=post_tx._profiles.scan_health.service_gap_us;
  post_tx._cw_active=true; post_tx.serviceProfileScan();
  elapsed_us+=5000000; post_tx.serviceProfileScan();
  post_tx._cw_active=false; post_tx.serviceProfileScan();
  assert(post_tx._profiles.scan_health.service_gap_us==service_gap);
}
''')

    def test_production_cli_health_is_read_only_and_fits_companion_reply(self):
        self.compile_run(r'''
#define main original_cli_main
#include "test/fixtures/radio_profiles/cli_test.cpp"
#undef main
int main() {
  Fixture f;
  auto& h=f.radio.p.scan_health;
  h.profile[0]={100,200,10,3,4,5};
  h.profile[1]={300,400,20,6,7,8}; h.service_gap_us=50;
  const auto saved=f.fs.files;
  assert(!strcmp(f.cmd("get radio2.health"),
      "> host late=3,6 idle=100,300 busy=200,400 over=10,20 svc=50us"));
  assert(strstr(f.cmd("get radio2.health radio"),"rx_hold=4 tx_hold=5"));
  assert(strstr(f.cmd("get radio2.health radio2"),"rx_hold=7 tx_hold=8"));
  assert(strstr(f.cmd("get radio2.status"),"late=3,6"));
  f.cmd("set radio2.health 0",false);
  assert(strstr(f.reply,"read-only"));
  assert(strstr(f.cmd("get radio2.health bad"),"Error:"));
  assert(f.fs.files==saved && h.profile[0].late_visits==3);
  for(auto& p:h.profile) p={UINT32_MAX,UINT32_MAX,UINT32_MAX,UINT32_MAX,UINT32_MAX,UINT32_MAX};
  h.service_gap_us=UINT32_MAX;
  assert(strstr(f.cmd("get radio2.health"),"svc=4294967295us"));
  assert(strstr(f.cmd("get radio2.health radio2"),"tx_hold=4294967295"));
  char small[16]; small[15]='X';
  assert(f.cli.handle("get radio2.health",small,15,false));
  assert(small[15]=='X' && memchr(small,0,15));
}
''', cli=True)


if __name__ == "__main__":
    unittest.main()
