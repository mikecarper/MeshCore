"""Exercise the experimental LR2021 auto-trim policy without RF hardware."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

SOURCE = r'''
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <initializer_list>
#include <helpers/radiolib/LR2021AutoTrim.h>
using namespace mesh::lr2021;
using Action = AutoTrim::Action;
static uint8_t keys[4][32] = {{1}, {2}, {3}, {4}};
static uint32_t timestamp = 1700000000, now_ms = 1000;
static AutoTrimSample sample(int32_t offset, unsigned peer = 0) {
  AutoTrimSample s; s.sender_key = keys[peer]; s.offset_hz = offset;
  s.frequency_hz = 910525000; s.signed_time = ++timestamp; s.utc_now = timestamp;
  s.captured_at_ms = s.now_ms = ++now_ms;
  s.verified = s.packet_bound = s.direct_rf = true; return s;
}
static void batch(AutoTrim& t, int32_t offset) {
  for (unsigned peer = 0; peer < 3; ++peer) { auto s=sample(offset + int(peer) - 1, peer); if (!t.observe(s)) fprintf(stderr,"failed off=%d peer=%u action=%d now=%u round=%u count=%u\n",offset,peer,int(t.action()),now_ms,t.round(),t.sampleCount()); else continue; assert(false); }
}
static void apply(AutoTrim& t, unsigned expected) {
  assert(t.action() == Action::Apply && t.target().enabled);
  assert(t.target().a == expected && t.target().b == expected);
  t.applied(true, now_ms); assert(t.action() == Action::Collect);
}
int main() {
  XoscTrimSelection original;
  AutoTrim t;
  assert(t.begin(original, 910525000, 1000, 600000, 60000, 500, 1000));
  auto bad = sample(1); bad.verified = false; assert(!t.observe(bad));
  bad = sample(1); bad.packet_bound = false; assert(!t.observe(bad));
  bad = sample(1); bad.direct_rf = false; assert(!t.observe(bad));
  bad = sample(1); bad.frequency_hz++; assert(!t.observe(bad));
  bad = sample(1); bad.utc_now = 0; assert(!t.observe(bad));
  bad = sample(1); bad.utc_now += 121; assert(!t.observe(bad));
  bad = sample(1); bad.captured_at_ms = 999; assert(!t.observe(bad));
  bad = sample(1); bad.captured_at_ms = 1000; assert(!t.observe(bad));
  bad = sample(1); bad.captured_at_ms = now_ms - 30001; assert(!t.observe(bad));
  assert(t.sampleCount() == 0);
  batch(t, 20000); apply(t, 3); batch(t, 4000);
  apply(t, 8); batch(t, 1000); apply(t, 7); batch(t, 1800);
  apply(t, 9); batch(t, 1200); apply(t, 8); batch(t, 1100);
  assert(t.action() == Action::Commit && t.target().a == 8);
  t.committed(false); assert(t.action() == Action::Restore && !t.target().enabled);
  t.applied(false, now_ms); assert(t.action() == Action::Restore);
  t.applied(true, now_ms); assert(t.action() == Action::Complete);
  t.reset(); assert(t.action() == Action::Idle);

  // Before the fallback wait, one sender cannot fill the preferred group;
  // the same locked identities must
  // contribute across trials, with no replay of an earlier signed advert.
  assert(t.begin(original, 910525000, now_ms, 600000, 60000, 500, 1000));
  auto one = sample(10000, 0); assert(t.observe(one));
  assert(!t.observe(sample(10000, 0)) && t.sampleCount() == 1);
  assert(t.observe(sample(10000, 1))); assert(t.observe(sample(10000, 2)));
  apply(t, 3);
  auto replay = sample(5000, 0); replay.signed_time = one.signed_time;
  assert(!t.observe(replay)); assert(!t.observe(sample(5000, 3)));
  assert(t.sampleCount() == 0); t.cancel(); t.applied(true, now_ms); t.reset();

  // Replayed timestamps never count, even when other fields are valid.
  assert(t.begin(original, 910525000, UINT32_MAX - 500, 2000, 1000, 500, 1000));
  now_ms = UINT32_MAX - 500; auto first = sample(500); assert(t.observe(first));
  assert(!t.observe(first) && t.sampleCount() == 1);
  t.tick(1499); assert(t.action() == Action::Restore && !t.target().enabled);
  t.applied(true, now_ms); t.reset();

  now_ms = 1000;
  // A measurement spread beyond the allowed bound cannot select any code.
  assert(t.begin(original, 910525000, now_ms, 10000, 1000, 500, 1000));
  assert(t.observe(sample(0, 0))); assert(t.observe(sample(0, 1)));
  assert(t.observe(sample(501, 2)));
  assert(t.action() == Action::Restore);
  t.applied(true, now_ms); t.reset();

  // The observed peer offsets span 1081 Hz, which is within the W12's
  // 1500 Hz tolerance. A spread just beyond that tolerance must abort.
  assert(t.begin(original, 910525000, now_ms, 10000, 1000, 1500, 1000));
  assert(t.observe(sample(887, 0))); assert(t.observe(sample(1739, 1)));
  assert(t.observe(sample(1968, 2))); assert(t.action() == Action::Apply);
  t.cancel(); t.applied(true, now_ms); t.reset();
  assert(t.begin(original, 910525000, now_ms, 10000, 1000, 1500, 1000));
  assert(t.observe(sample(0, 0))); assert(t.observe(sample(0, 1)));
  assert(t.observe(sample(1501, 2))); assert(t.action() == Action::Restore);
  t.applied(true, now_ms); t.reset();

  // Widening peer agreement does not weaken the separate requirement that
  // the final independent median match the winning trial within 1000 Hz.
  assert(t.begin(original, 910525000, now_ms, 10000, 1000, 1500, 1000));
  batch(t, 20000); apply(t, 3); batch(t, 4000);
  apply(t, 8); batch(t, 1000); apply(t, 7); batch(t, 1800);
  apply(t, 9); batch(t, 1200); apply(t, 8); batch(t, 2001);
  assert(t.action() == Action::Restore); t.applied(true, now_ms); t.reset();

  // The original asymmetric trim remains best. Verify it independently and
  // correct the measured residual digitally instead of discarding the run.
  original.enabled = true; original.a = 7; original.b = 6;
  assert(t.begin(original, 910525000, now_ms, 10000, 1000, 500, 1000));
  batch(t, 2200); apply(t, 3); batch(t, 3500); apply(t, 8); batch(t, 2800);
  apply(t, 7); batch(t, 3000); apply(t, 9); batch(t, 2900);
  assert(t.action() == Action::Apply && t.target().a == 7 && t.target().b == 6);
  t.applied(true, now_ms); batch(t, 2210);
  assert(t.action() == Action::Commit && t.verifiedOffsetHz() == 2210);
  t.beginDigitalVerification(now_ms); batch(t, 550);
  assert(t.action() == Action::Commit && t.digitalVerified()); t.committed(true);
  assert(t.action() == Action::Complete); t.reset();

  // A candidate with a modest improvement is not adopted; the independent
  // verification must compare against the original, not the candidate.
  assert(t.begin(original, 910525000, now_ms, 10000, 1000, 500, 1000));
  batch(t, 2600); apply(t, 3); batch(t, 2300); apply(t, 8); batch(t, 3100);
  apply(t, 2); batch(t, 3200); apply(t, 4); batch(t, 3000);
  assert(t.action() == Action::Apply && t.target().a == 7 && t.target().b == 6);
  t.applied(true, now_ms); batch(t, 2610);
  assert(t.action() == Action::Commit && t.verifiedOffsetHz() == 2610);
  t.beginDigitalVerification(now_ms); batch(t, 650);
  assert(t.action() == Action::Commit && t.digitalVerified()); t.committed(true);
  assert(t.action() == Action::Complete); t.reset();

  // A small but measurable baseline skips crystal trials, then independently
  // verifies both the existing trim and the temporary digital correction.
  assert(t.begin(original, 910525000, now_ms, 10000, 1000, 500, 1000));
  batch(t, 600);
  assert(t.action() == Action::Collect && t.round() == 5);
  batch(t, 610); assert(t.action() == Action::Commit && t.verifiedOffsetHz() == 610);
  t.committed(true); assert(t.action() == Action::Commit); // no early save
  t.beginDigitalVerification(now_ms); batch(t, 150);
  assert(t.action() == Action::Commit && t.digitalVerified()); t.committed(true);
  assert(t.action() == Action::Complete && t.target().a == 7 && t.target().b == 6);
  t.reset();

  for (int post_correction : {1500, 3000, -700}) {
    assert(t.begin(original, 910525000, now_ms, 10000, 1000, 500, 2000));
    batch(t, 1500); batch(t, 1500); assert(t.action() == Action::Commit);
    t.beginDigitalVerification(now_ms); batch(t, post_correction);
    assert(t.action() == Action::Restore); t.applied(true, now_ms); t.reset();
  }
  assert(t.begin(original, 910525000, now_ms, 10000, 1000, 500, 2000));
  batch(t, 200); assert(t.action() == Action::Complete); t.reset();

  // Silence at baseline is unknown. Probe bounded windows and require two
  // independent batches at the best candidate before permitting a commit.
  original.enabled = false; original.a = original.b = 0; now_ms = 10000;
  assert(t.begin(original, 910525000, now_ms, 100000, 10000, 500, 1000));
  now_ms += 10000; t.tick(now_ms); apply(t, 3);
  now_ms += 10000; t.tick(now_ms); apply(t, 8);
  batch(t, 1400); apply(t, 7);
  now_ms += 10000; t.tick(now_ms); apply(t, 9);
  now_ms += 10000; t.tick(now_ms); apply(t, 8);
  batch(t, 1500); assert(t.action() == Action::Commit);
  t.beginDigitalVerification(now_ms); batch(t, 400); assert(t.action() == Action::Commit);
  t.committed(true); assert(t.action() == Action::Complete); t.reset();

  // A deaf baseline is not evidence of zero error. The independently verified
  // consensus must itself be within 2 kHz to permit a commit.
  now_ms = 10000;
  assert(t.begin(original, 910525000, now_ms, 100000, 10000, 500, 1000));
  now_ms += 10000; t.tick(now_ms); apply(t, 3);
  now_ms += 10000; t.tick(now_ms); apply(t, 8);
  batch(t, 3000); apply(t, 7);
  now_ms += 10000; t.tick(now_ms); apply(t, 9);
  now_ms += 10000; t.tick(now_ms); apply(t, 8);
  batch(t, 3100); assert(t.action() == Action::Restore);
  t.applied(true, now_ms); t.reset();

  // Total silence, including an inactive reference, can never commit.
  now_ms = 10000;
  assert(t.begin(original, 910525000, now_ms, 100000, 10000, 500, 1000));
  for (unsigned expected : {3, 8, 2, 4}) {
    now_ms += 10000; t.tick(now_ms); apply(t, expected);
  }
  now_ms += 10000; t.tick(now_ms);
  assert(t.action() == Action::Restore && !t.target().enabled);
}
'''


class LR2021AutoTrimTests(unittest.TestCase):
    def test_actual_background_admission_preserves_every_radio_owner(self):
        from cpp_source import body
        from test_lr2021_calibration import LR2021CalibrationTests
        wrapper_source=(ROOT/'src/helpers/radiolib/CustomLR2021Wrapper.h').read_text()
        method=body(wrapper_source,'bool backgroundAutoTrimEligible(')
        profile_method=body(wrapper_source,'bool backgroundAutoTrimProfile(')
        source=r'''
#include <cassert>
#include <cstdint>
struct Wrapper {
  bool supported=true,rx=true,agc=false,carrier=false,txm=false,rxm=false,beacon=false,irq=false,busy=false,packet=false;
  unsigned activity_reads=0;bool _params_valid=true;
  struct Profiles {bool dual=false;uint32_t generation[2]={3,4};struct{float freq=910.525f;}primary;bool enabled()const{return dual;}} p;
  uint8_t active_profile=0;float _cur_freq=910.525f;
  uint32_t actual_generation=3;
  bool supportsXoscTrim()const{return supported;} bool isInRecvMode()const{return rx;}
  bool isAGCMaintenancePending()const{return agc;} bool isCarrierWaveActive()const{return carrier;}
  bool isMorseTxActive()const{return txm;} bool isMorseRxActive()const{return rxm;}
  bool isBeaconRadioOwned()const{return beacon;} bool hasPendingRadioInterrupt()const{return irq;}
  bool isChipBusy()const{return busy;} Profiles* profiles(){return &p;} const Profiles* profiles()const{return &p;}
  uint32_t receiveProfileGeneration()const{return actual_generation;}
  uint8_t receiveProfile()const{return active_profile;}
  bool isReceivingPacket(){++activity_reads;return packet;}
  bool getFrequencyHzForAutoTrim(uint32_t& value)const{value=910525000;return true;}
@PROFILE_METHOD@
@METHOD@
};
int main(){
  Wrapper w;uint32_t hz=0;assert(w.backgroundAutoTrimEligible(hz)&&hz==910525000);
  for(bool* owner:{&w.agc,&w.carrier,&w.txm,&w.rxm,&w.beacon,&w.irq,&w.busy,&w.p.dual}){
    *owner=true;const unsigned before=w.activity_reads;assert(!w.backgroundAutoTrimEligible(hz));
    assert(w.activity_reads==before);*owner=false;
  }
  w.rx=false;assert(!w.backgroundAutoTrimEligible(hz));w.rx=true;
  w.supported=false;assert(!w.backgroundAutoTrimEligible(hz));w.supported=true;
  ++w.actual_generation;assert(!w.backgroundAutoTrimEligible(hz));--w.actual_generation;
  w.active_profile=1;assert(!w.backgroundAutoTrimEligible(hz));w.active_profile=0;
  w._cur_freq=911.15f;assert(!w.backgroundAutoTrimEligible(hz));w._cur_freq=w.p.primary.freq;
  w.packet=true;assert(!w.backgroundAutoTrimEligible(hz));
  // Config-only observation never probes RX activity and survives all owners.
  w.rx=false;w.agc=w.carrier=w.txm=w.rxm=w.beacon=w.irq=w.busy=true;
  const unsigned before=w.activity_reads;assert(w.backgroundAutoTrimProfile(hz));
  assert(hz==uint32_t(w.p.primary.freq*1000000.0f) && w.activity_reads==before);
  w.p.dual=true;assert(!w.backgroundAutoTrimProfile(hz));w.p.dual=false;
  w._params_valid=false;assert(!w.backgroundAutoTrimProfile(hz));w._params_valid=true;
  w.supported=false;assert(!w.backgroundAutoTrimProfile(hz));
}
'''.replace('@METHOD@',method).replace('@PROFILE_METHOD@',profile_method).replace('#include <cstdint>','#include <cstdint>\n#include <initializer_list>')
        LR2021CalibrationTests().compile_run(source)


    def test_best_effort_keeps_verified_reference_cohort_and_independent_batches(self):
        from test_lr2021_calibration import LR2021CalibrationTests
        source = r"""
#include <cassert>
#include <cstdint>
#include <helpers/radiolib/LR2021AutoTrim.h>
using namespace mesh::lr2021;
using Action=AutoTrim::Action;
static uint8_t keys[4][32]={{1},{2},{3},{4}};
static uint32_t clock_ms=1000,utc=1700000000;
static AutoTrimSample fresh(unsigned key,int offset) {
  AutoTrimSample s;s.sender_key=keys[key];s.offset_hz=offset;s.frequency_hz=910525000;
  s.signed_time=s.utc_now=++utc;s.captured_at_ms=s.now_ms=++clock_ms;
  s.verified=s.packet_bound=s.direct_rf=true;return s;
}
static void start(AutoTrim& t) {
  XoscTrimSelection original;assert(t.begin(original,910525000,clock_ms,900000,120000,1500,2000));
}
static void expirePreference(AutoTrim& t) {
  clock_ms+=AutoTrim::PreferredPeersWaitMs;t.tick(clock_ms);
}
int main() {
  // Three valid identities retain the immediate original consensus path.
  {AutoTrim t;start(t);assert(t.observe(fresh(0,600)));assert(t.observe(fresh(1,610)));
   assert(!t.cohortLocked()&&t.action()==Action::Collect);
   assert(t.observe(fresh(2,620)));assert(t.cohortLocked()&&t.peerCount()==3);
   assert(t.requiredSampleCount()==3&&t.round()==5);}
  // One verified reference becomes a stable cohort only after the bounded
  // preference period; it cannot save without both independent checks.
  {AutoTrim t;start(t);auto first=fresh(0,600);assert(t.observe(first));
   t.tick(clock_ms+19999);assert(!t.cohortLocked()&&t.round()==0);
   expirePreference(t);assert(t.cohortLocked()&&t.peerCount()==1&&t.round()==5);
   assert(t.requiredSampleCount()==1&&t.action()==Action::Collect);
   const uint8_t* selected=nullptr;uint32_t accepted=0;uint8_t samples=99;
   assert(t.peerStatus(0,selected,accepted,samples)&&selected[0]==1&&accepted==first.signed_time&&samples==0);
   assert(!t.peerStatus(1,selected,accepted,samples)&&!selected&&!accepted&&!samples);
   assert(!t.observe(fresh(1,600)));assert(t.peerCount()==1&&t.sampleCount()==0);
   auto replay=fresh(0,600);replay.signed_time=first.signed_time;assert(!t.observe(replay));
   assert(t.observe(fresh(0,610)));assert(t.action()==Action::Commit);
   t.committed(true);assert(t.action()==Action::Commit&&!t.digitalVerified());
   t.beginDigitalVerification(clock_ms);auto digital_replay=fresh(0,150);digital_replay.signed_time=first.signed_time;assert(!t.observe(digital_replay));
   assert(t.observe(fresh(0,150)));assert(t.digitalVerified()&&t.action()==Action::Commit);
   t.committed(true);assert(t.action()==Action::Complete);}
  // Each member of a two-peer group remains required in every new batch.
  {AutoTrim t;start(t);assert(t.observe(fresh(0,600)));assert(t.observe(fresh(1,610)));
   expirePreference(t);assert(t.peerCount()==2&&t.requiredSampleCount()==2&&t.round()==5);
   assert(t.observe(fresh(1,620)));assert(t.sampleCount()==1&&t.action()==Action::Collect);
   assert(!t.observe(fresh(2,620)));assert(t.peerCount()==2&&t.sampleCount()==1);
   assert(t.observe(fresh(0,610)));assert(t.action()==Action::Commit);
   t.beginDigitalVerification(clock_ms);assert(t.observe(fresh(0,150)));
   assert(t.action()==Action::Collect);assert(t.observe(fresh(1,160)));
   assert(t.action()==Action::Commit&&t.digitalVerified());}
  // A changed independent single-peer result aborts instead of saving it.
  {AutoTrim t;start(t);assert(t.observe(fresh(0,600)));expirePreference(t);
   assert(t.observe(fresh(0,2000)));assert(t.action()==Action::Restore);}
  // Unverified, stale, nonlocal and mismatched-frequency packets cannot
  // establish even a one-peer fallback or start its preference timer.
  {AutoTrim t;start(t);
   for(unsigned bad=0;bad<5;++bad) {
     auto s=fresh(0,600);
     if(bad==0)s.verified=false;
     if(bad==1)s.packet_bound=false;
     if(bad==2)s.direct_rf=false;
     if(bad==3)s.frequency_hz++;
     if(bad==4)s.utc_now+=121;
     assert(!t.observe(s));
   }
   clock_ms+=21000;t.tick(clock_ms);assert(t.peerCount()==0&&!t.cohortLocked()&&t.round()==0);}
  // The preference timer also works when firmware milliseconds wrap.
  {clock_ms=UINT32_MAX-1000;AutoTrim t;start(t);assert(t.observe(fresh(0,600)));
   expirePreference(t);assert(t.cohortLocked()&&t.peerCount()==1&&t.round()==5);}
  // Silence remains unknown and never creates a zero-peer successful batch.
  {clock_ms=1000;AutoTrim t;start(t);clock_ms+=120000;t.tick(clock_ms);
   assert(!t.cohortLocked()&&t.peerCount()==0&&t.action()==Action::Apply&&t.target().a==3);}
  // Fresh startup uses a real temporary setting, not a manufactured factory
  // baseline. Stable but excessive offset keeps searching the bounded list
  // with exactly the original locked identities.
  {clock_ms=1000;AutoTrim t;XoscTrimSelection original;
   assert(t.begin(original,910525000,clock_ms,900000,120000,1500,2000,true));
   assert(t.action()==Action::Apply&&t.target().a==8&&!t.current().enabled);
   t.applied(true,clock_ms);
   for(unsigned i=0;i<3;++i)assert(t.observe(fresh(i,3000)));
   assert(t.action()==Action::Apply&&t.target().a==3&&t.cohortLocked());
   t.applied(true,clock_ms);assert(!t.observe(fresh(3,1500)));
   for(unsigned i=0;i<3;++i)assert(t.observe(fresh(i,1500)));
   assert(t.round()==5&&t.action()==Action::Collect&&t.current().a==3);
   for(unsigned i=0;i<3;++i)assert(t.observe(fresh(i,2200)));
   assert(t.action()==Action::Apply&&!t.target().enabled&&t.round()==2);
   t.applied(true,clock_ms);
   for(unsigned i=0;i<3;++i)assert(t.observe(fresh(i,1600)));
   for(unsigned i=0;i<3;++i)assert(t.observe(fresh(i,1550)));
   assert(t.action()==Action::Commit&&!t.target().enabled&&!t.baselineKnown());
   t.beginDigitalVerification(clock_ms);
   for(unsigned i=0;i<3;++i)assert(t.observe(fresh(i,390)));
   assert(t.digitalVerified());t.committed(true);assert(t.action()==Action::Complete);}
  // A changed independent measurement is unsafe, rather than permission to
  // cycle through different references/candidates until one appears to work.
  {clock_ms=1000;AutoTrim t;XoscTrimSelection original;
   assert(t.begin(original,910525000,clock_ms,900000,120000,1500,2000,true));
   t.applied(true,clock_ms);
   for(unsigned i=0;i<3;++i)assert(t.observe(fresh(i,100)));
   assert(t.round()==5&&t.action()==Action::Collect);
   for(unsigned i=0;i<3;++i)assert(t.observe(fresh(i,1101)));
   assert(t.action()==Action::Restore&&!t.target().enabled);}
  // If the eventual native factory candidate is already near zero, retain
  // the documented default/zero meaning; do not invent a saved calibration.
  {clock_ms=1000;AutoTrim t;XoscTrimSelection original;
   assert(t.begin(original,910525000,clock_ms,900000,120000,1500,2000,true));
   t.applied(true,clock_ms);
   for(unsigned choice=0;choice<2;++choice) {
     clock_ms+=120000;t.tick(clock_ms);assert(t.action()==Action::Apply);t.applied(true,clock_ms);
   }
   assert(!t.current().enabled&&t.round()==2);
   for(unsigned i=0;i<3;++i)assert(t.observe(fresh(i,100)));
   assert(t.round()==5&&t.action()==Action::Collect);
   for(unsigned i=0;i<3;++i)assert(t.observe(fresh(i,110)));
   assert(t.action()==Action::Complete&&!t.target().enabled&&!t.digitalVerified());}
}
"""
        LR2021CalibrationTests().compile_run(source, flags=('-fsanitize=address,undefined','-fno-pie','-no-pie'))

    def test_controller(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "test.cpp"
            binary = Path(folder) / "test"
            source.write_text(SOURCE, encoding="utf-8")
            subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++11", "-Wall", "-Wextra",
                "-Werror", "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                "-I", str(ROOT / "src"), str(source), "-o", str(binary),
            ], check=True)
            subprocess.run([str(binary)], check=True)

    def test_calibration_command_and_rollback_integration(self):
        from test_lr2021_calibration import LR2021CalibrationTests, WRAPPER, FS_STUB
        wrapper = WRAPPER.replace('  bool supported=true,selected=false;', '''
  struct Profiles { bool active=false; uint32_t generation[2]={0,0}; bool enabled() const {return active;} } profile;
  Profiles* profiles() {return &profile;}
  uint32_t receiveProfileGeneration() const {return profile.generation[0];}
  bool getFrequencyHzForAutoTrim(uint32_t& out) const {out=910525000;return true;}
  bool eligible=true;
  bool backgroundAutoTrimProfile(uint32_t& out) const {
    out=910525000;return !profile.active;
  }
  bool backgroundAutoTrimEligible(uint32_t& out) const {
    out=910525000;return eligible && !profile.active;
  }
  int32_t correction=0;
  mesh::RadioParamApplyResult correction_result=mesh::RadioParamApplyResult::APPLIED;
  bool configureFrequencyCorrection(int32_t value) {correction=value;return true;}
  int32_t frequencyCorrectionPpb() const {return correction;}
  void cancelFrequencyCorrectionApply() {}
  mesh::RadioParamApplyResult trySetFrequencyCorrection(int32_t value) {
    if (correction == value) return mesh::RadioParamApplyResult::APPLIED;
    if (correction_result==mesh::RadioParamApplyResult::APPLIED) correction=value;
    return correction_result;
  }
  bool supported=true,selected=false;''')
        source = (ROOT / 'src/helpers/radiolib/LR2021CalibrationSettings.cpp').read_text()
        for name in ('CustomLR2021Wrapper', 'LR2021CalibrationSettings',
                     'LR2021CalibrationPolicy', 'LR2021AutoTrim', 'LR2021BootAutoTrim'):
            source = source.replace(f'#include "{name}.h"',
                                    f'#include <helpers/radiolib/{name}.h>')
        harness = r'''
#include <cassert>
#include <cstring>
#include <helpers/RadioDirectionPolicy.h>
#include <helpers/radiolib/LR2021CalibrationSettings.h>
#include <helpers/radiolib/CustomLR2021Wrapper.h>
namespace mesh { namespace radioDirection {
Mode test_mode = Mode::Both;
Mode mode() { return test_mode; }
}}
using namespace mesh::lr2021;
int main() {
  FILESYSTEM fs; CustomLR2021Wrapper radio; char reply[160];
  auto cmd=[&](const char* text) {assert(handleLR2021CalibrationCommand(text,reply,sizeof(reply),radio));};
  cmd("get radio.xtrim.auto.peer 0");assert(!strcmp(reply,"> none"));
  for(const char* invalid : {"get radio.xtrim.auto.peer","get radio.xtrim.auto.peer -1",
      "get radio.xtrim.auto.peer 3","get radio.xtrim.auto.peer 00","get radio.xtrim.auto.peer 0 1"}) {
    cmd(invalid);assert(strstr(reply,"Error: use get radio.xtrim.auto.peer"));
  }
  {
    // A factory-erased boot first applies only a temporary 8,8. Silence
    // advances in the requested order, never writes a candidate, and finally
    // restores the actual factory selection before the bounded backoff.
    FILESYSTEM silent_order;CustomLR2021Wrapper ordered;
    auto order_cmd=[&](const char* text) {assert(handleLR2021CalibrationCommand(text,reply,sizeof(reply),ordered));};
    assert(!loadLR2021CalibrationSettings(&silent_order,ordered));
    loopLR2021CalibrationSettings(ordered);test_now+=5000;loopLR2021CalibrationSettings(ordered);
    assert(!ordered.selected&&silent_order.files.empty());
    test_now+=2000;loopLR2021CalibrationSettings(ordered);
    assert(ordered.selected&&ordered.a==8&&ordered.b==8);
    order_cmd("get radio.xtrim");assert(strstr(reply,"> default (saved; auto trial running)"));
    for(int expected:{3,-1,7,9}) {
      test_now+=120000;loopLR2021CalibrationSettings(ordered);
      assert(expected<0?!ordered.selected:ordered.selected&&ordered.a==expected&&ordered.b==expected);
      assert(silent_order.files.empty());
    }
    test_now+=120000;loopLR2021CalibrationSettings(ordered);loopLR2021CalibrationSettings(ordered);
    assert(!ordered.selected&&ordered.correction==0&&silent_order.files.empty());
    order_cmd("get radio.xtrim.auto");assert(strstr(reply,"original retained"));
    order_cmd("get radio.xtrim.autostart");assert(strstr(reply,"backoff")&&strstr(reply,"attempts=1/2"));
  }
  {
    // Three immediately available signed peers at 8,8 do not trigger the
    // 3/factory sweep. Even a near-zero result needs an independent batch,
    // and cannot claim/save factory defaults when the hardware used 8,8.
    FILESYSTEM near_zero;CustomLR2021Wrapper first_rx;
    auto near_cmd=[&](const char* text) {assert(handleLR2021CalibrationCommand(text,reply,sizeof(reply),first_rx));};
    assert(!loadLR2021CalibrationSettings(&near_zero,first_rx));
    loopLR2021CalibrationSettings(first_rx);test_now+=5000;loopLR2021CalibrationSettings(first_rx);
    test_now+=2000;loopLR2021CalibrationSettings(first_rx);
    uint8_t keys[3][32]={{81},{82},{83}};uint32_t signed_time=1700400000;
    auto near_batch=[&](int offset) {
      for(unsigned i=0;i<3;++i) {
        PacketFrequencyObservation value;value.valid=true;value.frequency_hz=910525000;
        value.captured_at_ms=++test_now;value.offset_hz=offset+int(i);++signed_time;
        observeLR2021AutoTrimAdvert(keys[i],signed_time,signed_time,value,true);
      }
    };
    near_batch(200);near_cmd("get radio.xtrim.auto");
    assert(strstr(reply,"trial=8,8 phase=5")&&!strstr(reply,"> saved")&&near_zero.files.empty());
    near_batch(210);assert(near_zero.files.empty());
    loopLR2021CalibrationSettings(first_rx);loopLR2021CalibrationSettings(first_rx);
    near_cmd("get radio.xtrim.auto");assert(strstr(reply,"> saved; baseline unknown")&&strstr(reply,"trial=8,8 phase=5"));
    assert(first_rx.selected&&first_rx.a==8&&first_rx.correction==0&&near_zero.files.count("/radio_calibration"));
    const auto stable=near_zero.files;
    CustomLR2021Wrapper reboot;assert(loadLR2021CalibrationSettings(&near_zero,reboot));
    assert(reboot.selected&&reboot.a==8&&reboot.b==8&&reboot.correction==0);
    test_now+=10000;loopLR2021CalibrationSettings(reboot);assert(reboot.calls==0&&near_zero.files==stable);
    assert(handleLR2021CalibrationCommand("get radio.xtrim.autostart",reply,sizeof(reply),reboot)
        &&strstr(reply,"retained attempts=0/2"));
  }
  {
    // Actual empty settings boot with one verified reference: no manual
    // calibration command, stable cohort, independent checks, save and reboot.
    FILESYSTEM single_fs;CustomLR2021Wrapper single;
    auto single_cmd=[&](const char* text) {assert(handleLR2021CalibrationCommand(text,reply,sizeof(reply),single));};
    assert(!loadLR2021CalibrationSettings(&single_fs,single));
    loopLR2021CalibrationSettings(single);test_now+=5000;loopLR2021CalibrationSettings(single);
    single_cmd("get radio.xtrim.autostart");assert(strstr(reply,"waiting-refs attempts=1/2"));
    test_now+=2000;loopLR2021CalibrationSettings(single);
    assert(single.selected&&single.a==8&&single_fs.files.empty());
    uint8_t key[32]={71};uint32_t signed_time=1700300000;
    auto one_fresh=[&](int offset) {
      PacketFrequencyObservation value;value.valid=true;value.frequency_hz=910525000;
      value.captured_at_ms=++test_now;value.offset_hz=offset;++signed_time;
      observeLR2021AutoTrimAdvert(key,signed_time,signed_time,value,true);
    };
    one_fresh(1500);single_cmd("get radio.xtrim.auto");
    assert(strstr(reply,"samples=1/3")&&strstr(reply,"peers=1/3 confidence=pending"));
    test_now+=19999;loopLR2021CalibrationSettings(single);
    single_cmd("get radio.xtrim.auto");assert(strstr(reply,"phase=0"));
    ++test_now;loopLR2021CalibrationSettings(single);
    single_cmd("get radio.xtrim.auto");
    assert(strstr(reply,"phase=5 samples=0/1")&&strstr(reply,"peers=1/3 confidence=single-peer"));
    single_cmd("get radio.xtrim.auto.peer 0");
    assert(strstr(reply,"key=4700000000000000000000000000000000000000000000000000000000000000")
        &&strstr(reply,"signed_time=1700300001,samples=0,locked=1"));
    one_fresh(1450);loopLR2021CalibrationSettings(single);
    single_cmd("get radio.xtrim.auto");assert(strstr(reply,"phase=6")&&!strstr(reply,"> saved"));
    one_fresh(360);loopLR2021CalibrationSettings(single);loopLR2021CalibrationSettings(single);
    single_cmd("get radio.xtrim.auto");
    assert(strstr(reply,"> saved")&&strstr(reply,"peers=1/3 confidence=single-peer"));
    single_cmd("get radio.xtrim.autostart");assert(strstr(reply,"retained attempts=1/2"));
    single_cmd("get radio.xtrim.auto.peer 0");
    assert(strstr(reply,"signed_time=1700300003,samples=1,locked=1"));
    assert(single.correction==1194&&single_fs.files.count("/radio_calibration"));
    const auto saved_files=single_fs.files;
    loopLR2021CalibrationSettings(single);assert(single_fs.files==saved_files);
    CustomLR2021Wrapper single_reboot;assert(loadLR2021CalibrationSettings(&single_fs,single_reboot));
    assert(single_reboot.correction==1194&&single_reboot.selected&&single_reboot.a==8);
    assert(handleLR2021CalibrationCommand("get radio.xtrim.autostart",reply,sizeof(reply),single_reboot)
        &&strstr(reply,"retained attempts=0/2"));
    assert(handleLR2021CalibrationCommand("get radio.xtrim.auto.peer 0",reply,sizeof(reply),single_reboot)
        &&!strcmp(reply,"> none"));
  }
  {
    FILESYSTEM startup; CustomLR2021Wrapper automatic;
    auto bootcmd=[&](const char* text) {assert(handleLR2021CalibrationCommand(text,reply,sizeof(reply),automatic));};
    assert(!loadLR2021CalibrationSettings(&startup,automatic));
    // No startup mutation: dual or an occupied primary cannot start a trial.
    automatic.profile.active=true; loopLR2021CalibrationSettings(automatic);
    test_now+=10000;loopLR2021CalibrationSettings(automatic);assert(automatic.calls==0 && startup.files.empty());
    automatic.profile.active=false;automatic.eligible=false;
    loopLR2021CalibrationSettings(automatic);
    // Frequent owners prevent five uninterrupted idle seconds for thirty
    // seconds, but must not erase stable primary configuration history.
    for(unsigned second=1;second<=30;++second) {
      test_now+=1000;automatic.eligible=false;
      loopLR2021CalibrationSettings(automatic);assert(automatic.calls==0 && startup.files.empty());
      automatic.eligible=true; // Owner releases between service instants.
    }
    bootcmd("get radio.xtrim.autostart");assert(strstr(reply,"attempts=0/2"));
    automatic.eligible=true;loopLR2021CalibrationSettings(automatic);
    bootcmd("get radio.xtrim.autostart");assert(strstr(reply,"waiting-refs") && strstr(reply,"attempts=1/2"));
    test_now+=2000;loopLR2021CalibrationSettings(automatic);
    assert(automatic.selected&&automatic.a==8&&startup.files.empty());
    // Real settings + actual AutoTrim: initial and independent signed-peer
    // batches precede digital correction, then a fresh verification saves it.
    uint8_t peers[3][32]={{31},{32},{33}};uint32_t signed_time=1700200000;
    auto fresh=[&](int offset) {
      for(unsigned i=0;i<3;++i) {
        PacketFrequencyObservation value;value.valid=true;value.frequency_hz=910525000;
        value.captured_at_ms=++test_now;value.offset_hz=offset+int(i);
        ++signed_time;observeLR2021AutoTrimAdvert(peers[i],signed_time,signed_time,value,true);
      }
    };
    fresh(1500);fresh(1450);test_now+=2000;loopLR2021CalibrationSettings(automatic);
    assert(automatic.correction==1195 && startup.files.empty());
    fresh(360);loopLR2021CalibrationSettings(automatic);
    // A manual one-shot can arrive before the next loop retires completed
    // boot ownership. Its fresh deadline must not inherit the old timeout.
    bootcmd("set radio.xtrim auto");assert(strstr(reply,"listening"));
    test_now+=899000;loopLR2021CalibrationSettings(automatic);
    assert(automatic.selected && automatic.a==3);
    bootcmd("set radio.xtrim auto cancel");loopLR2021CalibrationSettings(automatic);
    CustomLR2021Wrapper reboot;assert(loadLR2021CalibrationSettings(&startup,reboot));
    assert(reboot.correction==1195);test_now+=2000000;loopLR2021CalibrationSettings(reboot);assert(reboot.calls==0);
    assert(handleLR2021CalibrationCommand("get radio.xtrim.autostart",reply,sizeof(reply),reboot) && strstr(reply,"retained"));
    // Persisted opt-out survives reboot and never consumes an attempt.
    FILESYSTEM controlled;assert(!loadLR2021CalibrationSettings(&controlled,automatic));
    bootcmd("set radio.xtrim.autostart off");assert(strstr(reply,"OK"));
    assert(!loadLR2021CalibrationSettings(&controlled,reboot));
    loopLR2021CalibrationSettings(reboot);test_now+=2000000;loopLR2021CalibrationSettings(reboot);
    assert(handleLR2021CalibrationCommand("get radio.xtrim.autostart",reply,sizeof(reply),reboot) && strstr(reply,"> off") && strstr(reply,"attempts=0/2"));
    assert(handleLR2021CalibrationCommand("set radio.xtrim auto",reply,sizeof(reply),reboot) && strstr(reply,"listening"));
    assert(handleLR2021CalibrationCommand("set radio.xtrim auto cancel",reply,sizeof(reply),reboot));
    loopLR2021CalibrationSettings(reboot);
    const auto record=controlled.files["/radio_xtrim_auto"];
    controlled.fail_write=true;
    assert(handleLR2021CalibrationCommand("set radio.xtrim.autostart on",reply,sizeof(reply),reboot) && strstr(reply,"save failed"));
    assert(controlled.files["/radio_xtrim_auto"]==record);controlled.fail_write=false;
    FILESYSTEM old_on;assert(!loadLR2021CalibrationSettings(&old_on,reboot));
    assert(handleLR2021CalibrationCommand("set radio.xtrim.autostart on",reply,sizeof(reply),reboot));
    controlled.files["/radio_xtrim_auto.bak"]=old_on.files["/radio_xtrim_auto"];
    controlled.files["/radio_xtrim_auto"][0]^=1;
    assert(!loadLR2021CalibrationSettings(&controlled,automatic));loopLR2021CalibrationSettings(automatic);
    test_now+=5000;loopLR2021CalibrationSettings(automatic);
    bootcmd("get radio.xtrim.autostart");assert(strstr(reply,"state=error"));
    // No references: bounded existing sweep restores, waits thirty minutes,
    // and explicit cancellation prevents an immediate or delayed restart.
    FILESYSTEM silent;assert(!loadLR2021CalibrationSettings(&silent,automatic));
    loopLR2021CalibrationSettings(automatic);test_now+=5000;loopLR2021CalibrationSettings(automatic);
    test_now+=900000;loopLR2021CalibrationSettings(automatic);loopLR2021CalibrationSettings(automatic);
    bootcmd("get radio.xtrim.autostart");assert(strstr(reply,"backoff"));
    const unsigned no_ref_calls=automatic.calls;
    test_now+=1799999;loopLR2021CalibrationSettings(automatic);assert(automatic.calls==no_ref_calls);
    bootcmd("set radio.xtrim auto cancel");test_now+=1800001;loopLR2021CalibrationSettings(automatic);
    bootcmd("get radio.xtrim.autostart");assert(strstr(reply,"cancelled") && strstr(reply,"attempts=1/2"));
    // A due retry observes a fresh five-second configuration window instead
    // of reusing history from before the thirty-minute backoff.
    FILESYSTEM retry;assert(!loadLR2021CalibrationSettings(&retry,automatic));
    loopLR2021CalibrationSettings(automatic);test_now+=5000;loopLR2021CalibrationSettings(automatic);
    test_now+=900000;loopLR2021CalibrationSettings(automatic);loopLR2021CalibrationSettings(automatic);
    test_now+=1800000;loopLR2021CalibrationSettings(automatic);
    test_now+=4999;loopLR2021CalibrationSettings(automatic);
    bootcmd("get radio.xtrim.autostart");assert(strstr(reply,"attempts=1/2"));
    ++test_now;loopLR2021CalibrationSettings(automatic);
    bootcmd("get radio.xtrim.autostart");assert(strstr(reply,"waiting-refs") && strstr(reply,"attempts=2/2"));
    bootcmd("set radio.xtrim auto cancel");loopLR2021CalibrationSettings(automatic);
    test_now=100;
  }
  loadLR2021CalibrationSettings(nullptr,radio);
  cmd("set radio.xtrim auto"); assert(strstr(reply,"storage unavailable") && radio.calls==0);
  // An interrupted first calibration may have no prior records. Successful
  // journal rollback legitimately returns to defaults; a corrupt journal must
  // remain a detectable failure instead of silently using those defaults.
  FILESYSTEM first_calibration;
  uint8_t empty_journal[26]={'X','C','J','1'};
  for(unsigned i=0;i<25;++i)empty_journal[25]^=empty_journal[i];
  first_calibration.files["/radio_calibration.journal"] =
      std::vector<uint8_t>(empty_journal,empty_journal+26);
  first_calibration.files["/radio_xtrim.tmp"]={1,2,3};
  CustomLR2021Wrapper first_reboot;
  assert(!loadLR2021CalibrationSettings(&first_calibration,first_reboot));
  assert(LR2021CalibrationSettingsReady());
  assert(!first_calibration.files.count("/radio_calibration.journal"));
  // A failed first migration leaves the journal so the next boot can restore
  // the previous legacy record, even if rollback initially cannot publish.
  FILESYSTEM migrated; XoscTrimSelection old; old.enabled=true; old.a=old.b=3;
  uint8_t old_bytes[8]; encodeLegacyXoscTrim(old,old_bytes);
  migrated.files["/radio_xtrim"] = std::vector<uint8_t>(old_bytes,old_bytes+8);
  assert(loadLR2021CalibrationSettings(&migrated,radio));
  migrated.fail_publish=true; cmd("set radio.xtrim 8"); assert(strstr(reply,"save failed"));
  assert(migrated.files.count("/radio_calibration.journal"));
  migrated.fail_publish=false; CustomLR2021Wrapper migrated_reboot;
  assert(loadLR2021CalibrationSettings(&migrated,migrated_reboot));
  assert(migrated_reboot.a==3 && migrated_reboot.correction==0);
  assert(!migrated.files.count("/radio_calibration.journal"));
  loadLR2021CalibrationSettings(&fs,radio);
  fs.fail_open_write=true;
  cmd("set radio.xtrim auto"); assert(strstr(reply,"storage unavailable") && radio.calls==0);
  fs.fail_open_write=false; fs.fail_write=true;
  cmd("set radio.xtrim auto"); assert(strstr(reply,"storage unavailable") && radio.calls==0);
  fs.fail_write=false; fs.corrupt_write=true;
  cmd("set radio.xtrim auto"); assert(strstr(reply,"storage unavailable") && radio.calls==0);
  fs.corrupt_write=false;
  // Existing trim in the deadband gets a digital-only update, followed by a
  // fresh three-peer check; a later near-zero run must not compound it.
  FILESYSTEM digital_only; loadLR2021CalibrationSettings(&digital_only,radio);
  cmd("set radio.xtrim auto"); assert(strstr(reply,"listening"));
  uint8_t direct_keys[3][32]={{11},{12},{13}}; uint32_t direct_time=1700100000;
  auto direct_send=[&](int offset) {
    for (unsigned i=0;i<3;++i) {
      PacketFrequencyObservation obs; obs.valid=true; obs.frequency_hz=910525000;
      obs.captured_at_ms=++test_now; obs.offset_hz=offset+int(i);
      ++direct_time; observeLR2021AutoTrimAdvert(direct_keys[i],direct_time,direct_time,obs,true);
    }
  };
  direct_send(1500); cmd("get radio.xtrim.auto"); assert(strstr(reply,"phase=5"));
  assert(strstr(reply,"peers=3/3 confidence=three-peer"));
  cmd("get radio.xtrim.auto.peer 0");
  char expected_peer[160];snprintf(expected_peer,sizeof(expected_peer),
      "> index=0,key=0b00000000000000000000000000000000000000000000000000000000000000,signed_time=%lu,samples=0,locked=1",
      (unsigned long)(direct_time-2));
  assert(!strcmp(reply,expected_peer));
  direct_send(1450); test_now+=2000; loopLR2021CalibrationSettings(radio);
  assert(radio.correction==1195 && !radio.selected);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"phase=6"));
  direct_send(360); loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"saved"));
  const auto digital_saved=digital_only.files["/radio_calibration"];
  cmd("set radio.xtrim auto"); direct_send(300);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"original retained"));
  assert(radio.correction==1195 && digital_only.files["/radio_calibration"]==digital_saved);
  // A negative residual adjusts the existing value rather than replacing it.
  cmd("set radio.xtrim auto"); direct_send(-1500); direct_send(-1450);
  test_now+=2000; loopLR2021CalibrationSettings(radio); assert(radio.correction==1);
  direct_send(-360); loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.correction"); assert(strstr(reply,"1 ppb (active); 1 ppb (saved)"));
  // A proposed correction beyond the bounded ppm range cannot be saved.
  XoscTrimSelection capped; capped.correction_ppb=5400; uint8_t capped_bytes[12]; encodeXoscTrim(capped,capped_bytes);
  digital_only.files["/radio_calibration"]=std::vector<uint8_t>(capped_bytes,capped_bytes+12);
  assert(loadLR2021CalibrationSettings(&digital_only,radio) && radio.correction==5400);
  const auto cap_saved=digital_only.files["/radio_calibration"];
  cmd("set radio.xtrim auto"); direct_send(1500); direct_send(1450);
  test_now+=2000; loopLR2021CalibrationSettings(radio); loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"original retained"));
  assert(radio.correction==5400 && digital_only.files["/radio_calibration"]==cap_saved);
  loadLR2021CalibrationSettings(&fs,radio);
  radio.profile.active=true; cmd("set radio.xtrim auto"); assert(strstr(reply,"Error"));
  radio.profile.active=false; cmd("set radio.xtrim auto"); assert(strstr(reply,"listening"));
  cmd("set radio.xtrim 9"); assert(strstr(reply,"auto trim active"));
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.selected && radio.a==3);
  assert(fs.files.empty());
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.a==8);
  uint8_t keys[3][32]={{1},{2},{3}}; uint32_t time=1700000000;
  auto send=[&](int offset) {
    for(unsigned i=0;i<3;++i) {
      PacketFrequencyObservation obs; obs.valid=true; obs.frequency_hz=910525000;
      const int peer_offset[3] = {-852, 0, 229};
      obs.captured_at_ms=++test_now; obs.offset_hz=offset+peer_offset[i];
      ++time; observeLR2021AutoTrimAdvert(keys[i],time,time,obs,true);
    }
  };
  send(1400); loopLR2021CalibrationSettings(radio); assert(radio.a==7);
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.a==9);
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.a==8);
  send(1450); assert(fs.files.empty()); loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"phase=6"));
  assert(radio.correction==1194); send(360); loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"saved; baseline unknown"));
  assert(fs.files.count("/radio_xtrim"));
  assert(fs.files.count("/radio_calibration"));
  // A crash after both target files were replaced, but before the journal was
  // cleared, must recover the previous pair on the next boot.
  const auto stable_legacy=fs.files["/radio_xtrim"];
  const auto stable_pair=fs.files["/radio_calibration"];
  uint8_t journal[26]={'X','C','J','1',3};
  memcpy(journal+5,stable_legacy.data(),8); memcpy(journal+13,stable_pair.data(),12);
  for (unsigned i=0;i<25;++i) journal[25]^=journal[i];
  fs.files["/radio_calibration.journal"]=std::vector<uint8_t>(journal,journal+26);
  fs.files["/radio_xtrim"]=std::vector<uint8_t>(old_bytes,old_bytes+8);
  XoscTrimSelection newer=old; newer.correction_ppb=2000; uint8_t pair[12]; encodeXoscTrim(newer,pair);
  fs.files["/radio_calibration"]=std::vector<uint8_t>(pair,pair+12);
  CustomLR2021Wrapper recovered;
  assert(loadLR2021CalibrationSettings(&fs,recovered) && recovered.a==8 && recovered.correction==1194);
  assert(fs.files["/radio_xtrim"]==stable_legacy && fs.files["/radio_calibration"]==stable_pair);
  assert(!fs.files.count("/radio_calibration.journal"));
  CustomLR2021Wrapper reboot;
  assert(loadLR2021CalibrationSettings(&fs,reboot) && reboot.selected && reboot.a==8 && reboot.correction==1194);
  // A legacy firmware can change the trim after downgrade. Re-upgrade must
  // honor that change and discard the now-stale digital correction.
  const auto new_legacy=fs.files["/radio_xtrim"];
  fs.files["/radio_xtrim"] = std::vector<uint8_t>(old_bytes,old_bytes+8);
  CustomLR2021Wrapper reupgrade;
  assert(loadLR2021CalibrationSettings(&fs,reupgrade));
  assert(reupgrade.a==3 && reupgrade.correction==0);
  fs.files["/radio_xtrim"]=new_legacy;
  assert(loadLR2021CalibrationSettings(&fs,reboot) && reboot.a==8 && reboot.correction==1194);
  fs.files["/radio_calibration"][11]^=1;
  assert(loadLR2021CalibrationSettings(&fs,reupgrade) && reupgrade.a==8 && reupgrade.correction==0);
  fs.files["/radio_calibration"]=stable_pair;
  assert(loadLR2021CalibrationSettings(&fs,reboot) && reboot.a==8 && reboot.correction==1194);
  cmd("set radio.xtrim auto"); assert(strstr(reply,"listening"));
  radio.profile.active=true; test_now+=2000; loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"original retained"));
  assert(radio.a==8);
  radio.profile.active=false; cmd("set radio.xtrim auto"); assert(strstr(reply,"listening"));
  ++radio.profile.generation[0]; test_now+=2000; loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"original retained"));
  mesh::radioDirection::test_mode=mesh::radioDirection::Mode::Tx;
  cmd("set radio.xtrim auto"); assert(strstr(reply,"RX and verified"));
  mesh::radioDirection::test_mode=mesh::radioDirection::Mode::Both;
  cmd("set radio.xtrim auto"); assert(strstr(reply,"listening"));
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.a==3);
  mesh::radioDirection::test_mode=mesh::radioDirection::Mode::Tx;
  const unsigned calls=radio.calls;
  loopLR2021CalibrationSettings(radio);
  assert(radio.calls==calls && radio.a==3);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"restore deferred"));
  cmd("get radio.xtrim"); assert(strstr(reply,"reboot required"));
  mesh::radioDirection::test_mode=mesh::radioDirection::Mode::Both;
  cmd("set radio.xtrim 8"); assert(strstr(reply,"applying"));
  test_now+=2000; loopLR2021CalibrationSettings(radio); loopLR2021CalibrationSettings(radio); assert(radio.a==8 && radio.correction==0);
  cmd("set radio.xtrim auto"); assert(strstr(reply,"listening"));
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.a==3);
  cmd("set radio.xtrim auto cancel"); radio.result=mesh::RadioParamApplyResult::BUSY;
  loopLR2021CalibrationSettings(radio); test_now+=30000; loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"restore deferred"));
  radio.result=mesh::RadioParamApplyResult::APPLIED;
  cmd("set radio.xtrim 8"); test_now+=2000; loopLR2021CalibrationSettings(radio); loopLR2021CalibrationSettings(radio);
  cmd("set radio.xtrim auto"); assert(strstr(reply,"listening"));
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.a==3);
  send(1400); loopLR2021CalibrationSettings(radio); assert(radio.a==8);
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.a==2);
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.a==4);
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.a==3);
  send(1450); loopLR2021CalibrationSettings(radio); send(360);
  fs.fail_publish=true; loopLR2021CalibrationSettings(radio);
  fs.fail_publish=false; loopLR2021CalibrationSettings(radio); loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"storage uncertain"));
  cmd("get radio.xtrim"); assert(strstr(reply,"storage uncertain"));
  assert(radio.a==8);
  cmd("set radio.xtrim auto"); assert(strstr(reply,"verified saved trim"));
  cmd("set radio.xtrim 8"); test_now+=2000; loopLR2021CalibrationSettings(radio); loopLR2021CalibrationSettings(radio);
  cmd("set radio.xtrim auto"); assert(strstr(reply,"listening"));
  test_now+=120000; loopLR2021CalibrationSettings(radio); assert(radio.a==3);
  cmd("set radio.xtrim auto cancel");
  radio.result=mesh::RadioParamApplyResult::FAILED;
  loopLR2021CalibrationSettings(radio);
  const unsigned failed_calls=radio.calls;
  loopLR2021CalibrationSettings(radio); assert(radio.calls==failed_calls);
  test_now+=1000; loopLR2021CalibrationSettings(radio);
  test_now+=1000; loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"restore deferred"));
  radio.result=mesh::RadioParamApplyResult::APPLIED;
  cmd("set radio.xtrim 8"); assert(strstr(reply,"applying"));
  test_now+=2000; loopLR2021CalibrationSettings(radio); loopLR2021CalibrationSettings(radio); assert(radio.a==8);
  cmd("set radio.xtrim auto"); assert(strstr(reply,"listening"));
  radio.result=mesh::RadioParamApplyResult::FAILED;
  test_now+=120000; loopLR2021CalibrationSettings(radio);
  mesh::radioDirection::test_mode=mesh::radioDirection::Mode::Tx;
  loopLR2021CalibrationSettings(radio);
  cmd("get radio.xtrim.auto"); assert(strstr(reply,"restore deferred"));
  first_calibration.files["/radio_calibration.journal"]={1,2,3};
  assert(!loadLR2021CalibrationSettings(&first_calibration,first_reboot));
  assert(!LR2021CalibrationSettingsReady());
  assert(first_calibration.files.count("/radio_calibration.journal"));
}
'''
        stubs = {'helpers/IdentityStore.h': FS_STUB.replace('/display_prefs', '/radio_xtrim'),
                 'helpers/radiolib/CustomLR2021Wrapper.h': wrapper}
        LR2021CalibrationTests().compile_run(harness + '\n' + source,
            ('-DUSE_LR2021=1', '-DMESHNOLOGY_W12=1', '-DMESH_ENABLE_LR2021_AUTO_XTRIM=1',
             '-DMESH_ENABLE_RADIO_DIRECTION=1'), stubs)


if __name__ == "__main__":
    unittest.main()
