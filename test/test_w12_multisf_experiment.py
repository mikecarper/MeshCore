"""Host-only checks for an opt-in W12 receive/TX split experiment."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from cpp_source import body as method

ROOT = Path(__file__).resolve().parents[1]


class W12MultiSfExperiment(unittest.TestCase):
    def test_plan_and_guarded_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("w12_multisf_plan", "w12_multisf_transaction"):
                exe = Path(d) / name
                subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                "-I", "src", f"test/fixtures/{name}.cpp", "-o", str(exe)],
                               cwd=ROOT, check=True)
                subprocess.run([str(exe)], cwd=ROOT, check=True)

    def test_driver_restores_tx_and_uses_logical_airtime(self):
        source = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        send = method(source, "bool startSendRaw(")
        self.assertLess(send.index("beginReconfigure()"), send.index("setSideDetector(nullptr"))
        self.assertLess(send.index("setSpreadingFactor(plan.tx_sf)"), send.index("RadioLibWrapper::startSendRaw(bytes, len)"))
        self.assertIn("restoreAfterDeepInit()", send)
        receive = method(source, "int startReceiveMode()")
        self.assertIn("applyMultiSfReceiveTuple(plan)", receive)
        tuple_apply = method(source, "bool applyMultiSfReceiveTuple(")
        self.assertIn("activityBounds(plan, _cur_bw, assumed_preamble", tuple_apply)
        self.assertIn("radio->setPreambleMillis(bounds.preamble_ms)", tuple_apply)
        self.assertIn("radio->setMaxPayloadMillis(bounds.payload_ms)", tuple_apply)
        enable = method(source, "mesh::RadioParamApplyResult trySetW12MultiSf(")
        self.assertIn("radio->setPreambleMillis(normal_bounds.preambleMillis)", enable)
        airtime = method((ROOT / "src/helpers/radiolib/W12PramScannerLab.inc").read_text(),
                         "uint32_t getEstAirtimeFor(")
        self.assertIn("RadioLibWrapper::getProfileAirtime(0,len,_cur_cr)", airtime)

    def test_actual_send_path_guards_and_restores(self):
        source = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        send = method(source, "bool startSendRaw(").replace(" override", "")
        harness = r'''
#include <cassert>
#include <cstdint>
#define MESH_W12_MULTISF_EXPERIMENT 1
#define RADIOLIB_ERR_NONE 0
namespace mesh { namespace w12multisf {
enum class Admission { Ready, Unsupported };
struct Plan { uint8_t tx_sf = 7; };
} }
struct CustomLR2021 {
  int fail = 0, sf = 5, detector_calls = 0;
  int setSideDetector(void*, int) { ++detector_calls; return fail == 1 ? -1 : 0; }
  int setSpreadingFactor(int n) { if (fail == 2) return -1; sf = n; return 0; }
  int setPreambleLength(int) { return fail == 3 ? -1 : 0; }
};
struct RadioLibWrapper {
  CustomLR2021 chip; CustomLR2021* _radio = &chip;
  bool started = false, sent_ok = true;
  bool startSendRaw(const uint8_t*, int) { started = true; return sent_ok; }
};
struct Test : RadioLibWrapper {
  bool _multi_sf_enabled = true, _profile_refresh_required = false, _profile_repair_required = false;
  int admission = 1, resumes = 0; bool restored = true, receiving = true, pending = false;
  mesh::w12multisf::Admission multiSfPlan(mesh::w12multisf::Plan&) { return mesh::w12multisf::Admission::Ready; }
  void invalidateScanTuple() {}
  uint8_t beginReconfigure() { return admission; }
  int currentPreambleLength() { return 32; }
  bool restoreAfterDeepInit() { return restored; }
  void endReconfigure(bool b) { if (b) ++resumes; }
  bool isInRecvMode() { return receiving; }
  bool hasPendingRadioInterrupt() { return pending; }
  void startRecv() { ++resumes; }
  SEND_METHOD
};
int main() {
  uint8_t packet = 1;
  Test busy; busy.admission = 2;
  assert(!busy.startSendRaw(&packet, 1));
  assert(!busy.started && busy.chip.detector_calls == 0 && busy.chip.sf == 5);
  Test ok; assert(ok.startSendRaw(&packet, 1));
  assert(ok.started && ok.chip.sf == 7 && ok.chip.detector_calls == 1);
  for (int failure = 1; failure <= 3; ++failure) {
    Test failed; failed.chip.fail = failure;
    assert(!failed.startSendRaw(&packet, 1));
    assert(!failed.started && failed.resumes == 1 && !failed._profile_repair_required);
    Test broken; broken.chip.fail = failure; broken.restored = false;
    assert(!broken.startSendRaw(&packet, 1));
    assert(!broken.started && broken._profile_repair_required);
  }
  Test start_failed; start_failed.sent_ok = false;
  assert(!start_failed.startSendRaw(&packet, 1) && start_failed.resumes == 1);
}
'''.replace("SEND_METHOD", send)
        with tempfile.TemporaryDirectory() as d:
            src, exe = Path(d) / "test.cpp", Path(d) / "test"
            src.write_text(harness)
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            str(src), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_actual_receive_path_checks_profile_and_programs_side_detectors(self):
        source = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        receive = method(source, "int startReceiveMode()").replace(" override", "")
        harness = r'''
#include <cassert>
#include <cstdint>
#define MESH_W12_MULTISF_EXPERIMENT 1
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -1
namespace mesh {
namespace lr2021 { struct ApplyStatus { enum class Stage { Standby, Receive }; }; }
namespace w12multisf { enum class Admission { Ready, Unsupported }; struct Plan {}; }
}
struct FakeRadio {
  int standby_error = 0, starts = 0;
  int standby() { return standby_error; }
  int startReceive() { ++starts; return 0; }
};
struct Test {
  FakeRadio chip, *_radio = &chip;
  bool _multi_sf_enabled = true, tuple_ok = true, supported = true, _scan_retune = false;
  int tuples = 0;
  void invalidateScanTuple() {}
  int recordApply(mesh::lr2021::ApplyStatus::Stage, int code) { return code; }
  mesh::w12multisf::Admission multiSfPlan(mesh::w12multisf::Plan&) {
    return supported ? mesh::w12multisf::Admission::Ready : mesh::w12multisf::Admission::Unsupported;
  }
  bool applyMultiSfReceiveTuple(const mesh::w12multisf::Plan&) { ++tuples; return tuple_ok; }
  RECEIVE_METHOD
};
int main() {
  Test normal; assert(normal.startReceiveMode() == 0);
  assert(normal.tuples == 1 && normal.chip.starts == 1);
  Test blocked; blocked.supported = false;
  assert(blocked.startReceiveMode() == -1 && blocked.chip.starts == 0 && blocked.tuples == 0);
  Test error; error.tuple_ok = false;
  assert(error.startReceiveMode() == -1 && error.chip.starts == 0);
  Test standby; standby.chip.standby_error = -7;
  assert(standby.startReceiveMode() == -7 && standby.tuples == 0 && standby.chip.starts == 0);
}
'''.replace("RECEIVE_METHOD", receive)
        with tempfile.TemporaryDirectory() as d:
            src, exe = Path(d) / "test.cpp", Path(d) / "test"
            src.write_text(harness)
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            str(src), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_actual_enable_path_validates_before_changing_the_radio(self):
        source = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        enable = method(source, "mesh::RadioParamApplyResult trySetW12MultiSf(").replace(" override", "")
        harness = r'''
#include <cassert>
#include <cstdint>
#include "helpers/radiolib/W12MultiSfPlan.h"
#define RADIOLIB_ERR_NONE 0
namespace mesh {
enum class RadioParamApplyResult { APPLIED, BUSY, FAILED };
enum class RadioProfileProtocol { MeshCore, Other };
}
struct Profiles {
  struct { mesh::RadioProfileProtocol protocol = mesh::RadioProfileProtocol::MeshCore; } primary;
  bool dual = false;
  bool enabled() const { return dual; }
};
struct PacketMillis { uint32_t preambleMillis, payloadMillis; };
struct CustomLR2021 {
  int fail = 0, writes = 0, sf = 7;
  int setSideDetector(void*, int) { ++writes; return fail == 1 ? -1 : 0; }
  int setSpreadingFactor(int n) { ++writes; if (fail == 2) return -1; sf = n; return 0; }
  void setPreambleMillis(uint32_t) {}
  void setMaxPayloadMillis(uint32_t) {}
};
struct Test {
  bool _params_valid = true, _rx_ps_enabled = false, _multi_sf_enabled = false;
  bool _profile_refresh_required = false, _profile_repair_required = false;
  uint8_t _multi_sf_lower = 5, _multi_sf_upper = 8, _cur_sf = 7, _numSideDet = 0;
  uint8_t _cur_cr = 5;
  float _cur_bw = 62.5;
  int admission = 1, resumes = 0; bool restored = true, receiving = true;
  Profiles _profiles; CustomLR2021 chip, *_radio = &chip;
  uint8_t beginReconfigure() { return admission; }
  void invalidateScanTuple() {}
  void endReconfigure(bool b) { if (b) ++resumes; }
  bool isInRecvMode() { return receiving; }
  bool restoreAfterDeepInit() { return restored; }
  uint16_t currentPreambleLength() { return 32; }
  PacketMillis calcMaxPacketMillis(uint8_t, float, uint8_t, uint16_t) { return {1, 2}; }
  ENABLE_METHOD
};
int main() {
  using Result = mesh::RadioParamApplyResult;
  Test valid; assert(valid.trySetW12MultiSf(true, 5, 8) == Result::APPLIED);
  assert(valid._multi_sf_enabled && valid.chip.writes == 2 && valid.resumes == 1);
  assert(valid.trySetW12MultiSf(false, 0, 0) == Result::APPLIED && !valid._multi_sf_enabled);
  Test bw; bw._cur_bw = 1000;
  assert(bw.trySetW12MultiSf(true, 5, 8) == Result::FAILED && bw.chip.writes == 0);
  Test dual; dual._profiles.dual = true;
  assert(dual.trySetW12MultiSf(true, 5, 8) == Result::FAILED && dual.chip.writes == 0);
  Test power; power._rx_ps_enabled = true;
  assert(power.trySetW12MultiSf(true, 5, 8) == Result::FAILED && power.chip.writes == 0);
  Test busy; busy.admission = 2;
  assert(busy.trySetW12MultiSf(true, 5, 8) == Result::BUSY && busy.chip.writes == 0);
  Test fail; fail.chip.fail = 2;
  assert(fail.trySetW12MultiSf(true, 5, 8) == Result::FAILED);
  assert(!fail._multi_sf_enabled && fail.resumes == 1 && !fail._profile_repair_required);
  Test broken; broken.chip.fail = 1; broken.restored = false;
  assert(broken.trySetW12MultiSf(true, 5, 8) == Result::FAILED);
  assert(!broken._multi_sf_enabled && broken._profile_repair_required);
}
'''.replace("ENABLE_METHOD", enable)
        with tempfile.TemporaryDirectory() as d:
            src, exe = Path(d) / "test.cpp", Path(d) / "test"
            src.write_text(harness)
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", "src",
                            str(src), "-o", str(exe)], cwd=ROOT, check=True)
            subprocess.run([str(exe)], check=True)

    def test_actual_receive_tuple_installs_slowest_sf_activity_bounds(self):
        source = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        method_source = method(source, "bool applyMultiSfReceiveTuple(")
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include "helpers/radiolib/W12MultiSfPlan.h"
#define MAX_TRANS_UNIT 255
#define RADIOLIB_ERR_NONE 0
struct LR2021LoRaSideDetector_t { uint8_t sf = 0; };
struct CustomLR2021 {
  int fail = 0; uint8_t sf = 0, count = 0; uint32_t preamble = 0, payload = 0;
  int setSpreadingFactor(uint8_t value) { if (fail == 1) return -1; sf = value; return 0; }
  int setSideDetector(const LR2021LoRaSideDetector_t*, uint8_t n) { if (fail == 2) return -1; count = n; return 0; }
  void setPreambleMillis(uint32_t value) { preamble = value; }
  void setMaxPayloadMillis(uint32_t value) { payload = value; }
};
struct Test {
  float _cur_bw = 62.5; uint16_t configured_preamble = 32;
  CustomLR2021 chip, *_radio = &chip;
  uint16_t currentPreambleLength() { return configured_preamble; }
  static void buildSideDetectorConfig(const uint8_t* sfs, size_t n, float, LR2021LoRaSideDetector_t* cfg) {
    for (size_t i = 0; i < n; ++i) cfg[i].sf = sfs[i];
  }
  TUPLE_METHOD
};
int main() {
  mesh::w12multisf::Plan plan;
  assert(mesh::w12multisf::makePlan(true, 5, 8, 7, 62.5, false, false, false, plan)
      == mesh::w12multisf::Admission::Ready);
  Test ok; assert(ok.applyMultiSfReceiveTuple(plan));
  assert(ok.chip.sf == 5 && ok.chip.count == 3 && ok.chip.preamble >= 2098 && ok.chip.payload >= 2000);
  Test long_pre; long_pre.configured_preamble = 1024;
  assert(long_pre.applyMultiSfReceiveTuple(plan) && long_pre.chip.preamble > ok.chip.preamble);
  for (int failure = 1; failure <= 2; ++failure) {
    Test t; t.chip.fail = failure;
    assert(!t.applyMultiSfReceiveTuple(plan));
    assert(t.chip.preamble == 0 && t.chip.payload == 0);
  }
}
'''.replace("TUPLE_METHOD", method_source)
        with tempfile.TemporaryDirectory() as d:
            src, exe = Path(d) / "test.cpp", Path(d) / "test"
            src.write_text(harness)
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", "src",
                            str(src), "-o", str(exe)], cwd=ROOT, check=True)
            subprocess.run([str(exe)], check=True)

    def test_opt_in_and_conflict_guards(self):
        source = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        self.assertIn("MESH_W12_MULTISF_EXPERIMENT", source)
        self.assertIn("if (enabled && _multi_sf_enabled)", source)
        profile_check = method(source, "bool validateProfile(")
        self.assertIn("RadioLibWrapper::validateProfile(profile)", profile_check)
        self.assertIn("profile.sf, profile.bw", profile_check)
        profile = (ROOT / "src/helpers/RadioProfileCLI.cpp").read_text()
        self.assertIn("radio_->w12MultiSfEnabled() && (base || temporary || scheduled)", profile)
        self.assertIn("radio_ && radio_->w12MultiSfEnabled()", profile)
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        self.assertIn("_prefs->rx_powersaving_enabled || _prefs->extra_sf[0]", cli)


if __name__ == "__main__":
    unittest.main()
