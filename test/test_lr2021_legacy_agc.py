"""Execute LR2021's real legacy transaction and shared admission on a host radio."""
from pathlib import Path
import ast
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'test/fixtures/lr2021_legacy_agc/main.cpp'


class LR2021LegacyAGCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            raise unittest.SkipTest('Host C++ compiler required')
        cls.temporary = tempfile.TemporaryDirectory(prefix='lr2021-legacy-wrapper-')
        cls.addClassCleanup(cls.temporary.cleanup)
        folder = Path(cls.temporary.name)
        actual = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        code = FIXTURE.read_text()
        for token, signature in (('@BEGIN_RECONFIGURE@', 'uint8_t RadioLibWrapper::beginReconfigure('),
                                 ('@END_RECONFIGURE@', 'void RadioLibWrapper::endReconfigure(')):
            code = code.replace(token, body(actual, signature).replace('RadioLibWrapper::', ''))
        cls.compiler = compiler
        cls.source = code
        (folder / 'main.cpp').write_text(code)
        cls.binaries = []
        for enabled, warm, pram, wire in ((e, w, p, t) for e in (0, 1) for w in (0, 1)
                                          for p in (0, 1) for t in (0, 1)):
            binary = folder / f'legacy-{enabled}-{warm}-{pram}-{wire}'
            args = [compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
                    '-DUSE_LR2021=1', '-DMESH_ENABLE_OLD_AGC=' + str(enabled),
                    '-DMESH_WARM_RX_REFRESH=' + str(warm), '-DMESH_W12_PRAM_EXPERIMENT=' + str(pram),
                    '-DMESH_ENABLE_HOST_KISS=' + str(wire), '-DMESH_ENABLE_RADIO_DIRECTION=1',
                    '-DMESH_ENABLE_CARRIER_TOOLS=1', '-DMESH_ENABLE_MORSE_RX=1', '-DMESH_ENABLE_TX_BEACONS=1',
                    '-I' + str(ROOT / 'src'), str(folder / 'main.cpp'), '-o', str(binary)]
            built = subprocess.run(args, capture_output=True, text=True, timeout=90)
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)
            cls.binaries.append(((enabled, warm, pram, wire), binary))

    def scenario(self, name):
        for flags, binary in self.binaries:
            with self.subTest(flags=flags):
                result = subprocess.run([str(binary), name], capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_full_restore_includes_secondary_tuple_multisf_rxps_protocol_power_gain(self):
        self.scenario('success')

    def test_owners_and_invalid_profiles_do_not_issue_spi_or_pause_scanner(self):
        self.scenario('guards')

    def test_real_shared_admission_retains_busy_and_late_fifo_ownership(self):
        self.scenario('ownership')

    def test_failures_never_receive_success_credit_and_request_repair(self):
        self.scenario('failures')

    def test_scanner_warm_restore_manual_mode_outer_lease_and_late_fifo(self):
        self.scenario('scanner')

    def test_pram_owns_rf_while_host_noise_request_remains_queued(self):
        self.scenario('noise')

    def test_ignored_errors_or_noise_ownership_are_rejected_by_negative_controls(self):
        actual = (ROOT / 'src/helpers/radiolib/LR2021LegacyMaintenance.inc').read_text()
        mutations = {
            'calibration': actual.replace('radio->resetReceiverCalibration());',
                                           '(radio->resetReceiverCalibration(),RADIOLIB_ERR_NONE));'),
            'receive': actual.replace('(resume && !isInRecvMode())', 'false'),
            'noise': actual.replace('return !_lab_scan_active && PulseBase::isCalibratingNoiseFloor();',
                                    'return PulseBase::isCalibratingNoiseFloor();'),
            'applied_cache': actual.replace('_dbm_applied=false; _rx_boosted_gain_applied=false;', ''),
        }
        with tempfile.TemporaryDirectory(prefix='lr2021-legacy-negative-') as temp:
            folder = Path(temp)
            override = folder / 'helpers/radiolib'
            override.mkdir(parents=True)
            (folder / 'main.cpp').write_text(self.source)
            for name, code in mutations.items():
                with self.subTest(ignored=name):
                    self.assertNotEqual(code, actual)
                    (override / 'LR2021LegacyMaintenance.inc').write_text(code)
                    built = subprocess.run([self.compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                            '-Wno-unused-function', '-DUSE_LR2021=1', '-DMESH_ENABLE_OLD_AGC=1',
                                            '-DMESH_WARM_RX_REFRESH=1', '-DMESH_W12_PRAM_EXPERIMENT=1',
                                            '-DMESH_ENABLE_HOST_KISS=1', '-DMESH_ENABLE_RADIO_DIRECTION=1',
                                            '-DMESH_ENABLE_CARRIER_TOOLS=1', '-DMESH_ENABLE_MORSE_RX=1',
                                            '-DMESH_ENABLE_TX_BEACONS=1', '-I' + str(folder),
                                            '-I' + str(ROOT / 'src'), str(folder / 'main.cpp'), '-o', str(folder / 'test')],
                                           capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    result = subprocess.run([str(folder / 'test'), 'noise' if name == 'noise' else 'failures'], capture_output=True, text=True, timeout=20)
                    self.assertNotEqual(result.returncode, 0, 'Ignored native errors must not satisfy the failure tests')
                    self.assertIn({'noise': 'isCalibratingNoiseFloor', 'applied_cache': '!w._dbm_applied'}.get(name, 'Result::Failed'), result.stderr)

    def test_opt_in_timing_diagnostics_count_native_attempts_and_rx_without_false_credit(self):
        actual = (ROOT / 'src/helpers/radiolib/LR2021LegacyMaintenance.inc').read_text()
        marker = '_agc_timing.helperFinished(static_cast<uint32_t>(micros()));'
        self.assertIn(marker, actual)
        with tempfile.TemporaryDirectory(prefix='lr2021-legacy-diagnostics-') as temp:
            folder = Path(temp)
            override = folder / 'helpers/radiolib'
            override.mkdir(parents=True)
            (folder / 'main.cpp').write_text(self.source)
            for mutation in (False, True):
                code = actual.replace(marker, '(void)0;') if mutation else actual
                (override / 'LR2021LegacyMaintenance.inc').write_text(code)
                built = subprocess.run([self.compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-function', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-fno-pie', '-no-pie', '-DUSE_LR2021=1', '-DMESH_ENABLE_OLD_AGC=1',
                    '-DMESH_WARM_RX_REFRESH=1', '-DMESH_W12_PRAM_EXPERIMENT=1',
                    '-DMESH_ENABLE_HOST_KISS=1', '-DMESH_ENABLE_RADIO_DIRECTION=1',
                    '-DMESH_ENABLE_CARRIER_TOOLS=1', '-DMESH_ENABLE_MORSE_RX=1',
                    '-DMESH_ENABLE_TX_BEACONS=1', '-DMESH_AGC_TIMING_DIAGNOSTICS=1',
                    '-I' + str(folder), '-I' + str(ROOT / 'src'), str(folder / 'main.cpp'),
                    '-o', str(folder / 'test')], capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                result = subprocess.run([str(folder / 'test'), 'diagnostics'], capture_output=True,
                                        text=True, timeout=20)
                if mutation:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('helper_us.count==1', result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manual_scanner_lease_resumes_without_enabling_automatic_mode(self):
        # Reuse the scanner auto fixture's hardware boundary with the actual
        # service method. A mutation proves its old auto-only gate loses a lease.
        script = ROOT / 'lab/pram_scanner/test_automatic.py'
        tree = ast.parse(script.read_text())
        raw = next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'code' for t in node.targets)
                   and isinstance(node.value, ast.Constant))
        code = raw.split('int main(){', 1)[0]
        source = (ROOT / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
        signatures = [('@AUTO@', 'void labServiceAutomatic('), ('@UNCERTAIN@', 'void labServiceUncertain()'),
                      ('@SET@', 'bool labScannerSetAutomatic('), ('@RECONFIG@', 'bool labReconfigureBlocked()'),
                      ('@TRANSITION@', 'void labStopForProfileTransition()'), ('@CURRENT@', 'bool labRxProfileCurrent()')]
        for token, signature in signatures:
            code = code.replace(token, body(source, signature).replace(' override', ''))
        code += r'''
int main(){
  {Fake f;f._lab_auto.setEnabled(false);f._lab_resume_pending=true;f._lab_pram_loaded=true;f._lab_resume_at=now;
   f.labServiceAutomatic(true,false);assert(f.warm==1&&f.cold==0&&f._lab_scan_active&&!f._lab_resume_pending&&!f._lab_auto.enabled());
   assert(f.labScannerSetAutomatic(false));f.labServiceAutomatic(true,false);assert(f.warm==1&&f.cold==0&&!f._lab_scan_active);}
  {Fake f;f._lab_auto.setEnabled(false);f._lab_resume_pending=true;f._lab_pram_loaded=true;f._lab_resume_at=now;f._profile_repair_required=true;
   f.labServiceAutomatic(true,false);assert(f.warm==0&&f._lab_resume_pending);f._profile_repair_required=false;
   f.labServiceAutomatic(true,true);assert(f.warm==0&&f._lab_resume_pending);
   f.labServiceAutomatic(true,false);assert(f.warm==1&&!f._lab_auto.enabled());}
}
'''
        old = 'if ((!_lab_auto.enabled() && !_lab_resume_pending) || _lab_auto.blocked()) return;'
        self.assertIn(old, code)
        with tempfile.TemporaryDirectory(prefix='lr2021-manual-lease-') as temp:
            folder = Path(temp)
            for mutation in (False, True):
                current = code.replace(old, 'if (!_lab_auto.enabled() || _lab_auto.blocked()) return;') if mutation else code
                (folder / 'main.cpp').write_text(current)
                built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                        '-I' + str(ROOT / 'src'), str(folder / 'main.cpp'), '-o', str(folder / 'test')],
                                       capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                result = subprocess.run([str(folder / 'test')], capture_output=True, text=True, timeout=20)
                if mutation:
                    self.assertNotEqual(result.returncode, 0, 'Old gate must fail to resume the retained manual lease')
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
