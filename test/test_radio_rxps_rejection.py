"""Run actual RXPS rejection and scan restoration methods with host adapters.

Reuses the profile-scan I/O harness, actual wrapper transitions/calibration
getter, and CommonCLI's actual RXPS branch plus Repeater forwarding callback.
Capability and physical I/O are adapters; this is not RF, real sleep timing,
or whole-role firmware/hardware qualification.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_radio_profile_scan import HARNESS
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

CASES = r'''
struct NodePrefs {
  uint8_t rx_powersaving_enabled = 0, rx_ps_level = 0, rx_ps_preamble = 0, sf = 7;
  uint32_t rx_ps_rx_us = 50000, rx_ps_sleep_us = 50000;
  float bw = 125;
};
struct Callbacks {
  RadioLibWrapper& radio_driver;
  bool setRxPowerSaving(bool, uint32_t, uint32_t);
};
struct CommonCLI {
  NodePrefs* _prefs;
  Callbacks* _callbacks;
  unsigned saves = 0;
  void savePrefs() { ++saves; }
  bool calculateRxPowerSavingLevel(uint32_t,uint8_t,float,uint32_t,uint32_t*,uint32_t*);
  void set(const char*, char*);
};
// Rendering a successful adjustment note is outside this rejection boundary.
void appendRxPowerSavingAdjustmentNote(char*, const NodePrefs*, uint8_t, float) {}
@CALLBACK@
@CALCULATOR@
void CommonCLI::set(const char* config, char* reply) { @CLI@ }

static const char* scenario;
#define CHECK(expression, diagnostic) do { if (!(expression)) { \
  std::fprintf(stderr, "FAIL %s: %s (line %d)\n", scenario, diagnostic, __LINE__); \
  return 1; } } while (0)

static auto snapshot(const RadioLibWrapper& w) {
  return std::make_tuple(w.state, w._rx_ps_enabled, w._rx_ps_armed,
      w._rx_ps_continuous_fallback, w._profile_saved_rxps, w._profile_rxps_suspended,
      w._rx_ps_rx_us, w._rx_ps_sleep_us, w._nf_refresh_requested,
      w._nf_calib_active, w._noise_floor_valid, w._noise_floor_secondary_valid,
      w._nf_last_calib, w._nf_calib_deadline, w._nf_sample_from,
      w._floor_estimator.count(), w._secondary_floor_estimator.count(),
      w.isCalibratingNoiseFloor(), w.rx_starts, w.chip.standbys, w.applies);
}
static auto prefsSnapshot(const NodePrefs& p) {
  return std::make_tuple(p.rx_powersaving_enabled, p.rx_ps_level,
      p.rx_ps_preamble, p.rx_ps_rx_us, p.rx_ps_sleep_us);
}
static void prepare(RadioLibWrapper& w, bool scan, bool support) {
  w.supported = support;
  w._nf_refresh_requested = true;
  w._noise_floor_valid = false;
  if (scan) w.enable();
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  scenario = argv[1];
  if (!std::strcmp(scenario, "reject-outside") || !std::strcmp(scenario, "reject-inside")
      || !std::strcmp(scenario, "reject-restoration")) {
    const bool scan = std::strcmp(scenario, "reject-outside") != 0;
    const bool restoration_only = !std::strcmp(scenario, "reject-restoration");
    for (uint32_t period : {1000u, 10000u, 50000u, 30000000u}) {
      RadioLibWrapper w;
      prepare(w, scan, false);
      CHECK(w.isCalibratingNoiseFloor(), "plain RX needs calibration sleep fence before rejection");
      const auto before = snapshot(w);
      CHECK(!w.setRxPowerSaving(true, period, period), "unsupported enable must be rejected");
      // Separate negative control reaches the restoration oracle even if the
      // historical setter already changed its deferred state here.
      if (!restoration_only) CHECK(snapshot(w) == before, "rejected RXPS changed radio state");
      if (scan) {
        w._profiles.setSecondary({}, false);
        w.serviceProfileScan();
        CHECK(!w._profile_rxps_suspended && w.state == STATE_RX,
              "scan exit did not restore normal receiver");
        CHECK(w._nf_refresh_requested && w.isCalibratingNoiseFloor(),
              "rejected RXPS lost calibration request or sleep fence");
        CHECK(!w._rx_ps_enabled && !w._rx_ps_armed,
              "rejected RXPS became enabled after scan exit");
        CHECK(w._rx_ps_rx_us == 50000 && w._rx_ps_sleep_us == 50000,
              "rejected timings became active after scan exit");
      }
    }
  } else if (!std::strcmp(scenario, "cli-outside") || !std::strcmp(scenario, "cli-inside")) {
    RadioLibWrapper w;
    prepare(w, !std::strcmp(scenario, "cli-inside"), false);
    NodePrefs prefs;
    Callbacks callbacks{w};
    CommonCLI cli{&prefs, &callbacks};
    char reply[160] = {};
    const auto before = snapshot(w);
    const auto saved = prefsSnapshot(prefs);
    cli.set("radio.rxps 10000 20000", reply);
    CHECK(!std::strcmp(reply, "ERROR: RX powersaving unsupported"),
          "actual CLI must report unsupported rather than OK");
    CHECK(!cli.saves && prefsSnapshot(prefs) == saved,
          "rejected actual CLI must not save or update preferences");
    CHECK(snapshot(w) == before, "rejected RXPS changed radio state");
  } else if (!std::strcmp(scenario, "supported-deferred")) {
    for (bool initial : {false, true}) for (bool requested : {false, true}) {
      RadioLibWrapper w;
      w.supported = true;
      CHECK(w.setRxPowerSaving(initial, 50000, 50000), "initial supported policy rejected");
      w.enable();
      CHECK(w._profile_rxps_suspended && !w._rx_ps_enabled && !w._rx_ps_armed,
            "scan must suspend physical duty cycling");
      const auto starts = w.rx_starts, standbys = w.chip.standbys, applies = w.applies;
      const bool refresh = w._nf_refresh_requested;
      CHECK(w.setRxPowerSaving(requested, 10000, 20000), "supported deferred policy rejected");
      CHECK(w._profile_saved_rxps == requested && !w._rx_ps_enabled && !w._rx_ps_armed,
            "deferred setting must change only saved enable policy");
      CHECK(w._rx_ps_rx_us == 10000 && w._rx_ps_sleep_us == 20000,
            "accepted deferred timings not retained");
      CHECK(w.rx_starts == starts && w.chip.standbys == standbys && w.applies == applies
          && w._nf_refresh_requested == refresh, "deferred setting touched live scan I/O/calibration");
      w._nf_window_armed = w._nf_lazy_refresh = w._nf_refresh_attempted = true;
      w._profiles.setSecondary({}, false);
      w.serviceProfileScan();
      CHECK(!w._profile_rxps_suspended && w.state == STATE_RX
          && w._rx_ps_enabled == requested && w._rx_ps_armed == requested,
            "accepted deferred policy not restored after scan");
      CHECK(!w._nf_window_armed && !w._nf_lazy_refresh && !w._nf_refresh_attempted,
            "scan exit must invalidate stale single-profile calibration policy/deadline");
    }
  } else if (!std::strcmp(scenario, "unsupported-disable")) {
    for (bool scan : {false, true}) {
      RadioLibWrapper w;
      prepare(w, scan, false);
      CHECK(w.setRxPowerSaving(false, 10000, 20000), "unsupported-family disable must remain accepted");
      CHECK(!w._profile_saved_rxps && !w._rx_ps_enabled && !w._rx_ps_armed
          && w._rx_ps_rx_us == 10000 && w._rx_ps_sleep_us == 20000,
            "accepted disable did not preserve disabled policy/timings");
      CHECK(w._nf_refresh_requested && w.isCalibratingNoiseFloor(),
            "accepted disable lost continuous calibration sleep fence");
      if (scan) {
        w._profiles.setSecondary({}, false);
        w.serviceProfileScan();
        CHECK(!w._rx_ps_enabled && !w._rx_ps_armed && w.isCalibratingNoiseFloor(),
              "accepted disable did not survive scan exit");
      }
    }
  } else if (!std::strcmp(scenario, "owner-guards")) {
    for (bool scan : {false, true}) for (bool support : {false, true}) {
      for (bool enable : {false, true}) {
        RadioLibWrapper w;
        prepare(w, scan, support);
        w._cw_active = true;
        const auto before = snapshot(w);
        CHECK(!w.setRxPowerSaving(enable, 10000, 20000), "carrier ownership must reject RXPS mutation");
        CHECK(snapshot(w) == before, "carrier-owned rejection changed state");
        w._cw_active = false;
#if MESH_ENABLE_TX_BEACONS
        w._beacon_owned = true;
        CHECK(!w.setRxPowerSaving(enable, 10000, 20000), "beacon ownership must reject RXPS mutation");
        CHECK(snapshot(w) == before, "beacon-owned rejection changed state");
#endif
      }
    }
  } else return 3;
  std::printf("PASS %s\n", scenario);
}
'''


class RadioRxpsRejectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            raise unittest.SkipTest('a host C++17 compiler is required')
        cls.temp = tempfile.TemporaryDirectory(prefix='radio-rxps-rejection-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        (work / 'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cmath>\n',
                                        encoding='ascii')
        source = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.cpp').read_text(encoding='utf-8')
        header = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.h').read_text(encoding='utf-8')
        common = (ROOT / 'src/helpers/CommonCLI.cpp').read_text(encoding='utf-8')
        repeater = (ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text(encoding='utf-8')
        names = [('uint8_t', 'beginReconfigure'), ('void', 'endReconfigure'),
                 ('void', 'serviceProfileScan'), ('bool', 'setRxPowerSaving'),
                 ('void', 'recalibrateNoiseFloor'), ('bool', 'validateProfile')]
        names += [('mesh::RadioParamApplyResult', name) for name in (
            'tuneProfile', 'prepareTransmitProfile', 'tryRestoreCodingRate',
            'trySetParams', 'trySetPrimaryParams')]
        methods = '\n'.join(method(source, f'{kind} RadioLibWrapper::{name}(')
                            for kind, name in names)
        methods += '\n' + method(source,
            'mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile, bool')
        methods += '\nvoid RadioLibWrapper::setProfileStandbyWarm(bool enabled) { chip.standbyXOSC=enabled; }'
        harness = HARNESS[:HARNESS.index('int main() {')]
        harness = harness.replace('#include <cassert>',
            '#include <cassert>\n#include <cstdio>\n#include <cstring>\n#include <tuple>\n'
            '#include <helpers/CLICommandUtils.h>')
        harness = harness.replace('int standby() { return 0; }',
                                  'unsigned standbys=0; int standby() { ++standbys; return 0; }')
        harness = harness.replace('bool _rx_ps_enabled=true, _rx_ps_armed=true',
                                  'bool _rx_ps_enabled=false, _rx_ps_armed=false')
        harness = harness.replace('bool supportsRxPowerSaving() { return true; }',
            'bool supported=false;\n  bool supportsRxPowerSaving() const { return supported; }\n'
            '  bool supportsMeshtext() const { return false; }\n'
            '  bool _beacon_owned=false, _beacon_restoring=false;\n  unsigned rx_starts=0;')
        harness = harness.replace('  void startRecv() {', '  void startRecv() {\n    ++rx_starts;')
        harness = harness.replace('_rx_ps_armed=_rx_ps_enabled;',
                                  '_rx_ps_armed=supported && _rx_ps_enabled;')
        harness = harness.replace('  void recalibrateNoiseFloor() { _noise_floor_valid=false; }',
            '  void recalibrateNoiseFloor();\n  bool setRxPowerSaving(bool,uint32_t,uint32_t);\n'
            + method(header, 'bool isCalibratingNoiseFloor() const').replace(' override', ''))
        harness = harness.replace('bool validateProfile(const mesh::RadioProfileParams& p) const { return mesh::RadioProfiles::valid(p); }',
                                  'bool validateProfile(const mesh::RadioProfileParams&) const;')
        harness = harness.replace('#define NF_CALIB_SETTLE_MS 7UL',
            '#define NF_CALIB_SETTLE_MS 7UL\n#define NF_CALIB_TIMEOUT_MS NoiseFloorEstimator::WINDOW_TIMEOUT_MS')
        cases = CASES.replace('@CALLBACK@', method(repeater, 'bool MyMesh::setRxPowerSaving(')
                              .replace('MyMesh::', 'Callbacks::'))
        cases = cases.replace('@CALCULATOR@', method(common, 'bool CommonCLI::calculateRxPowerSavingLevel('))
        cases = cases.replace('@CLI@', method(common, 'if (memcmp(config, "radio.rxps ", 11) == 0)'))
        setter = method(source, 'bool RadioLibWrapper::setRxPowerSaving(')
        guard = '  if (enabled && !supportsRxPowerSaving()) return false;\n'
        if setter.count(guard) != 1:
            raise AssertionError('expected actual early capability rejection')
        suspended = method(setter, 'if (_profile_rxps_suspended)')
        if suspended.count('return true;') != 1:
            raise AssertionError('expected actual accepted deferred branch')
        legacy = setter.replace(guard, '', 1).replace(suspended,
            suspended.replace('return true;', 'return !enabled || supportsRxPowerSaving();')
            + '\n  ' + guard.strip(), 1)
        sanitize = [] if not sys.platform.startswith('linux') else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-omit-frame-pointer', '-fno-pie', '-no-pie']
        cls.binaries = {}
        for kind, generated in (('production', methods), ('legacy', methods.replace(setter, legacy, 1))):
            cpp = work / (kind + '.cpp')
            cpp.write_text(harness.replace('@METHODS@', generated) + cases, encoding='utf-8')
            for beacons in (0, 1):
                binary = work / (kind + str(beacons) + ('.exe' if os.name == 'nt' else ''))
                built = subprocess.run([
                    compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    *sanitize, '-DMESH_RADIO_PROFILE_CHIP=1276',
                    '-DMESH_ENABLE_TX_BEACONS=' + str(beacons),
                    '-I' + str(work), '-I' + str(ROOT / 'src'), str(cpp), '-o', str(binary)],
                    capture_output=True, text=True, timeout=60)
                if built.returncode:
                    raise AssertionError(built.stdout + built.stderr)
                cls.binaries[kind, beacons] = binary

    def check_case(self, case, kind='production', fails=False,
                   diagnostic='rejected RXPS changed radio state'):
        for beacons in (0, 1):
            with self.subTest(case=case, kind=kind, beacons=beacons):
                checked = subprocess.run([str(self.binaries[kind, beacons]), case],
                                         capture_output=True, text=True, timeout=10)
                if fails:
                    self.assertNotEqual(checked.returncode, 0)
                    self.assertIn(diagnostic, checked.stderr)
                    self.assertNotIn('runtime error:', checked.stderr)
                    self.assertNotIn('AddressSanitizer:', checked.stderr)
                else:
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn('PASS ' + case, checked.stdout)

    def test_unsupported_enable_is_unchanged_inside_and_outside_scan(self):
        for case in ('reject-outside', 'reject-inside'):
            self.check_case(case)

    def test_actual_cli_rejects_without_saving_or_changing_radio_state(self):
        for case in ('cli-outside', 'cli-inside'):
            self.check_case(case)

    def test_rejected_policy_does_not_change_restoration_or_calibration_sleep_fence(self):
        self.check_case('reject-restoration')

    def test_supported_deferred_enable_and_disable_are_restored(self):
        self.check_case('supported-deferred')

    def test_unsupported_disable_remains_accepted(self):
        self.check_case('unsupported-disable')

    def test_carrier_and_beacon_owner_guards_remain_unchanged(self):
        self.check_case('owner-guards')

    def test_original_suspended_branch_has_intended_semantic_failure(self):
        # Outside-scan rejection is a healthy control for the historical code.
        self.check_case('reject-outside', 'legacy')
        for case in ('reject-inside', 'cli-inside'):
            self.check_case(case, 'legacy', fails=True)
        self.check_case('reject-restoration', 'legacy', fails=True,
                        diagnostic='rejected RXPS lost calibration request or sleep fence')

    def test_supported_normal_sx1276_infrastructure_reaches_this_boundary(self):
        variant = (ROOT / 'variants/heltec_v2/platformio.ini').read_text(encoding='ascii')
        self.assertIn('[env:Heltec_v2_repeater]', variant)
        self.assertIn('WRAPPER_CLASS=CustomSX1276Wrapper', variant)
        wrapper = (ROOT / 'src/helpers/radiolib/CustomSX1276Wrapper.h').read_text(encoding='ascii')
        self.assertIn('public RadioLibWrapper', wrapper)
        self.assertNotIn('supportsRxPowerSaving()', wrapper)
        base = (ROOT / 'src/Dispatcher.h').read_text(encoding='utf-8')
        self.assertIn('return false;', method(base, 'virtual bool supportsRxPowerSaving() const'))
        common = (ROOT / 'src/helpers/CommonCLI.cpp').read_text(encoding='utf-8')
        self.assertIn('_radio_profiles.handle(command, reply, 160, sender_timestamp != 0)',
                      method(common, 'void CommonCLI::handleCommand('))


if __name__ == '__main__':
    unittest.main()
