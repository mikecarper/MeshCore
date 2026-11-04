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

  // One loud sender cannot fill a quorum; the same three identities must
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
                     'LR2021CalibrationPolicy', 'LR2021AutoTrim'):
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
  loadLR2021CalibrationSettings(nullptr,radio);
  cmd("set radio.xtrim auto"); assert(strstr(reply,"storage unavailable") && radio.calls==0);
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
}
'''
        stubs = {'helpers/IdentityStore.h': FS_STUB.replace('/display_prefs', '/radio_xtrim'),
                 'helpers/radiolib/CustomLR2021Wrapper.h': wrapper}
        LR2021CalibrationTests().compile_run(harness + '\n' + source,
            ('-DUSE_LR2021=1', '-DMESHNOLOGY_W12=1', '-DMESH_ENABLE_LR2021_AUTO_XTRIM=1',
             '-DMESH_ENABLE_RADIO_DIRECTION=1'), stubs)


if __name__ == "__main__":
    unittest.main()
