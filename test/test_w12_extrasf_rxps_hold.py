"""Exercise the production W12 extra-SF RXPS hold and rearm paths."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body
import test_rxps_runtime as rxps_runtime

ROOT = Path(__file__).resolve().parents[1]


class W12ExtraSfRxpsHoldTests(unittest.TestCase):
    def test_status_reports_actual_hold_and_radio2_priority(self):
        source = (ROOT / "src/helpers/radiolib/RXPowerSavingCLI.cpp").read_text()
        formatter = body(source, "void RXPowerSavingCLI::get(const RxPowerSavingConfig&")
        harness = r'''
#define MESHNOLOGY_W12 1
#define MESH_W12_RXPS_EXPERIMENT 1
#include <helpers/radiolib/RXPowerSavingCLI.h>
#include <cassert>
#include <cstdio>
#include <cstring>
FORMATTER
int main() {
  RxPowerSavingConfig config; config.enabled = 1;
  RxPowerSavingStatus status; status.supported = status.receiving = true;
  status.extra_sf_hold = true;
  char reply[160];
  RXPowerSavingCLI::get(config, status, 7, 62.5, reply, sizeof(reply));
  assert(strstr(reply, "desired=on,effective=continuous") && strstr(reply, "hold=extra-sf"));
  config.rx_us = config.sleep_us = 30000000; config.level = 10;
  status.last_error = INT16_MIN; status.arm_failures = UINT32_MAX;
  RXPowerSavingCLI::get(config, status, 7, 62.5, reply, sizeof(reply));
  assert(strstr(reply, "hold=extra-sf"));
  status.extra_sf_repair = true;
  RXPowerSavingCLI::get(config, status, 7, 62.5, reply, sizeof(reply));
  assert(strstr(reply, "hold=extra-sf-repair"));
  status.suspended = true;
  RXPowerSavingCLI::get(config, status, 7, 62.5, reply, sizeof(reply));
  assert(strstr(reply, "hold=radio2") && !strstr(reply, "hold=extra-sf"));
  config.enabled = 0;
  RXPowerSavingCLI::get(config, status, 7, 62.5, reply, sizeof(reply));
  assert(!strstr(reply, "hold="));
}
'''.replace("FORMATTER", formatter)
        with tempfile.TemporaryDirectory() as directory:
            cpp, exe = Path(directory) / "status.cpp", Path(directory) / "status"
            cpp.write_text(harness)
            build = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
                "-Werror", "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                "-I", str(ROOT / "src"), str(cpp), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_actual_w12_hold_and_rearm(self):
        wrapper = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        hold = body(wrapper, "bool rxPowerSavingExtraSfHold() const")
        hold = hold.replace("bool rxPowerSavingExtraSfHold() const override",
                            "bool RadioLibWrapper::rxPowerSavingExtraSfHold() const")
        configure = body(wrapper, "mesh::RadioParamApplyResult tryConfigSideDetectors(")
        configure = configure.replace("mesh::RadioParamApplyResult tryConfigSideDetectors(",
                                      "mesh::RadioParamApplyResult RadioLibWrapper::tryConfigSideDetectors(", 1)
        configure = configure.replace(") override {", ") {", 1)
        apply_side = body(wrapper, "bool applySideDetectorConfig(")
        apply_side = apply_side.replace("bool applySideDetectorConfig(",
                                        "bool RadioLibWrapper::applySideDetectorConfig(", 1)
        source = rxps_runtime.production_source()
        source = source.replace('#include <cassert>',
            '#define MESHNOLOGY_W12 1\n#define MESH_W12_RXPS_EXPERIMENT 1\n'
            '#define MESH_W12_MULTISF_EXPERIMENT 1\n'
            '#include <helpers/radiolib/LR2021SideDetectorConfig.h>\n'
            '#include <helpers/radiolib/LR2021ApplyStatus.h>\n'
            'struct LR2021LoRaSideDetector_t { uint8_t sf = 0; };\n'
            'constexpr int RADIOLIB_ERR_INVALID_SIDE_DETECT = -700;\n#include <cassert>', 1)
        source = source.replace('  bool _rx_ps_enabled = true, _rx_ps_armed = false;',
            '  uint8_t _sideDetSFs[3] = {8, 0, 0}; size_t _numSideDet = 0;\n'
            '  bool _params_valid = true, _side_detector_uncertain = false, detector_restore = true;\n'
            '  bool _multi_sf_enabled = false;\n'
            '  int detector_error = 0, deep_init_calls = 0; bool detector_irq = false;\n'
            '  uint8_t _cur_sf = 7; float _cur_bw = 62.5f;\n'
            '  bool rxPowerSavingExtraSfHold() const;\n'
            '  bool rxPowerSavingExtraSfRepair() const { return _side_detector_uncertain; }\n'
            '  mesh::RadioParamApplyResult tryConfigSideDetectors(const uint8_t*, uint8_t, float);\n'
            '  void invalidateScanTuple() {}\n'
            '  bool isInRecvMode() const { return state == STATE_RX; }\n'
            '  uint8_t getSpreadingFactor() const { return _cur_sf; }\n'
            '  bool applySideDetectorConfig(uint8_t, float);\n'
            '  bool isBeaconRadioOwned() const { return _beacon_owned; }\n'
            '  int recordApply(mesh::lr2021::ApplyStatus::Stage, int rc) { return rc; }\n'
            '  bool restoreAfterDeepInit() { ++deep_init_calls; return detector_restore; }\n'
            '  static void buildSideDetectorConfig(const uint8_t* s, size_t n, float, LR2021LoRaSideDetector_t* o) {\n'
            '    for (size_t i=0; i<n; ++i) o[i].sf=s[i];\n  }\n'
            '  bool _rx_ps_enabled = true, _rx_ps_armed = false;', 1)
        source = source.replace('    bool scanning = false;',
            '    struct { uint8_t sf=7; float bw=62.5f; } primary;\n'
            '    bool primary_temporary = false;\n    bool scanning = false;', 1)
        source = source.replace('    int16_t standby() {',
            '    float getBandwidthKhz() { return owner->_cur_bw; }\n'
            '    int16_t setSideDetector(const LR2021LoRaSideDetector_t*, uint8_t) {\n'
            '      if (owner->detector_irq) owner->irq_pending = true;\n'
            '      return owner->detector_error;\n    }\n'
            '    int16_t standby() {', 1)
        source = source.replace('int RadioLibWrapper::startReceivePowerSaving()',
            'using CustomLR2021 = RadioLibWrapper::Physical;\n'
            'int RadioLibWrapper::startReceivePowerSaving()', 1)
        source = source.replace('static uint32_t retryDelay(',
            hold + '\n' + configure + '\n' + apply_side + '\nstatic uint32_t retryDelay(', 1)
        extra = r'''
static void extraSfHoldAndResume() {
  // Multi-SF holds a saved RXPS preference without losing or misreporting it.
  RadioLibWrapper multisf;
  assert(multisf.startReceivePowerSaving() == 0 && multisf._rx_ps_armed);
  assert(multisf.beginReconfigure() == 1); multisf._multi_sf_enabled=true;
  multisf.endReconfigure(true);
  assert(!multisf._rx_ps_armed && multisf._rx_ps_continuous_fallback);
  assert(multisf.getRxPowerSavingStatus().extra_sf_hold);
  assert(multisf.beginReconfigure() == 1); multisf._multi_sf_enabled=false;
  multisf.endReconfigure(true);
  assert(multisf._rx_ps_armed && !multisf._rx_ps_continuous_fallback);
  // Saved RXPS on at boot must use continuous RX as soon as the compatible
  // detectors are installed; repeated maintenance must never rearm it.
  RadioLibWrapper r;
  r._numSideDet = 1;
  assert(r.rxPowerSavingExtraSfHold());
  assert(r.startReceivePowerSaving() == 0);
  assert(!r._rx_ps_armed && r._rx_ps_continuous_fallback && r.continuous_calls == 1);
  assert(r.getRxPowerSavingStatus().extra_sf_hold);
  r._rx_ps_arm_retry.recordFailure(now_ms);
  uint32_t delay = 0;
  assert(!r.getRxPowerSavingStatus().retry_pending && !r.getRxPowerSavingRetryDelay(now_ms, delay));
  for (unsigned i=0; i<50; ++i) { now_ms += 300000; r.loop(); }
  assert(r.arm_calls == 0);
  // Clearing the list performs the usual guarded receive restart and rearm.
  // An earlier failed arm keeps its ordinary bounded retry schedule.
  now_ms += 5000;
  assert(r.tryConfigSideDetectors(nullptr, 0, 62.5) == mesh::RadioParamApplyResult::APPLIED);
  assert(r._rx_ps_armed && !r.getRxPowerSavingStatus().extra_sf_hold);
  // Exercise the real detector setter while RXPS is armed.
  const uint8_t side[] = {8};
  assert(r.tryConfigSideDetectors(side, 1, 62.5) == mesh::RadioParamApplyResult::APPLIED);
  assert(!r._rx_ps_armed && r.getRxPowerSavingStatus().extra_sf_hold);
  // The saved preference can be turned off/on while the receive hold remains.
  assert(r.trySetRxPowerSaving(false, 50000, 50000) == mesh::RadioParamApplyResult::APPLIED);
  assert(!r._rx_ps_enabled && !r.getRxPowerSavingStatus().extra_sf_hold);
  assert(r.trySetRxPowerSaving(true, 50000, 50000) == mesh::RadioParamApplyResult::APPLIED);
  assert(r._rx_ps_enabled && !r._rx_ps_armed && r.getRxPowerSavingStatus().extra_sf_hold);
  // An incompatible temporary primary suspends the detectors and allows RXPS;
  // returning to the compatible tuple re-establishes the hold.
  assert(r.beginReconfigure() == 1); r._profiles.primary_temporary = true;
  assert(r.applySideDetectorConfig(9, 62.5)); r._cur_sf = 9; r.endReconfigure(true);
  assert(r._rx_ps_armed && !r.getRxPowerSavingStatus().extra_sf_hold);
  assert(r.beginReconfigure() == 1); r._profiles.primary_temporary = false;
  assert(r.applySideDetectorConfig(7, 62.5)); r._cur_sf = 7; r.endReconfigure(true);
  assert(!r._rx_ps_armed && r.getRxPowerSavingStatus().extra_sf_hold);
  // A pending packet blocks the change rather than clearing its FIFO.
  r.irq_pending = true;
  assert(r.beginReconfigure() == 2 && r.irq_pending);
  // Failed partial detector writes plus failed restoration hold RXPS even
  // when the previously saved detector list was empty.
  RadioLibWrapper partial;
  assert(partial.startReceivePowerSaving() == 0 && partial._rx_ps_armed);
  partial.detector_error = -706; partial.detector_restore = false;
  assert(partial.tryConfigSideDetectors(side, 1, 62.5) == mesh::RadioParamApplyResult::FAILED);
  assert(partial._profile_refresh_required && partial._side_detector_uncertain);
  assert(partial.deep_init_calls == 0);
  assert(!partial._rx_ps_armed && partial.getRxPowerSavingStatus().extra_sf_repair);
  partial.detector_error = 0; partial.detector_restore = true;
  assert(partial.tryConfigSideDetectors(nullptr, 0, 62.5) == mesh::RadioParamApplyResult::APPLIED);
  assert(!partial._side_detector_uncertain && !partial.getRxPowerSavingStatus().extra_sf_repair);
  // A completed detector write cannot report success if continuous RX failed
  // to restart. The caller can restore the prior setting and retry RX.
  RadioLibWrapper restart_failure;
  assert(restart_failure.startReceivePowerSaving() == 0 && restart_failure._rx_ps_armed);
  restart_failure.continuous_error = -705;
  assert(restart_failure.tryConfigSideDetectors(side, 1, 62.5) == mesh::RadioParamApplyResult::FAILED);
  assert(restart_failure.state == STATE_IDLE);
  restart_failure.continuous_error = 0;
  assert(restart_failure.tryConfigSideDetectors(nullptr, 0, 62.5) == mesh::RadioParamApplyResult::APPLIED);
  restart_failure.startRecv();
  assert(restart_failure._rx_ps_armed);
  // An IRQ racing the detector write keeps its FIFO and defers the setting.
  RadioLibWrapper late;
  assert(late.startReceivePowerSaving() == 0 && late._rx_ps_armed);
  late.detector_irq = true;
  assert(late.tryConfigSideDetectors(side, 1, 62.5) == mesh::RadioParamApplyResult::BUSY);
  assert(late.irq_pending && late._side_detector_uncertain && late.deep_init_calls == 0);
  assert(late.start_calls == 0);
  late.irq_pending = false; late.detector_irq = false;
  assert(late.tryConfigSideDetectors(side, 1, 62.5) == mesh::RadioParamApplyResult::APPLIED);
  late.startRecv();
  assert(!late._rx_ps_armed && late.getRxPowerSavingStatus().extra_sf_hold);
  RadioLibWrapper late_error;
  assert(late_error.startReceivePowerSaving() == 0);
  late_error.detector_irq = true; late_error.detector_error = -706;
  assert(late_error.tryConfigSideDetectors(side, 1, 62.5) == mesh::RadioParamApplyResult::FAILED);
  assert(late_error.irq_pending && late_error._side_detector_uncertain && late_error.deep_init_calls == 0);
  assert(late_error.start_calls == 0);
  std::puts("PASS: W12 extra-SF RXPS hold and rearm");
}
'''
        source = source.replace('int main() {', extra + '\nint main() {\n  extraSfHoldAndResume();', 1)
        rxps_runtime.RxPowerSavingRuntimeTests.run_harness(self, source)


if __name__ == "__main__":
    unittest.main()
