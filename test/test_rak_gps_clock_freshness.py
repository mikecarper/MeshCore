"""Run the actual RAK12500 provider against retained-PVT GNSS/RTC adapters.

The actual full provider class and LocationProvider header are compiled. The
GNSS adapter models SparkFun's per-field unread flags and retained data after
failed polls; it is not an I2C/parser or hardware test. A separate installed-
library probe established that contract without making CI depend on libdeps.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'test/fixtures/rak_gps_clock_freshness.cpp'

ARDUINO = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
inline uint32_t now_ms = 0;
inline uint32_t millis() { return now_ms; }
inline constexpr int HIGH = 1;
inline int digitalRead(int) { return HIGH; }
'''

MESH = r'''
#pragma once
#include <Arduino.h>
namespace mesh {
class RTCClock {
  uint32_t utc = 1772323200U, anchored = 0;
public:
  unsigned writes = 0;
  uint32_t getCurrentTime() { return utc + (now_ms - anchored) / 1000U; }
  void setCurrentTime(uint32_t value) { utc = value; anchored = now_ms; ++writes; }
};
}
'''


class RakGpsClockFreshnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            raise unittest.SkipTest('a host C++17 compiler is required')
        temporary = tempfile.TemporaryDirectory(prefix='rak-gps-clock-')
        cls.addClassCleanup(temporary.cleanup)
        work = Path(temporary.name)
        (work / 'Arduino.h').write_text(ARDUINO, encoding='ascii')
        (work / 'Mesh.h').write_text(MESH, encoding='ascii')
        source = (ROOT / 'src/helpers/sensors/EnvironmentSensorManager.cpp').read_text(
            encoding='utf-8')
        provider = extract_braced(source, 'class RAK12500LocationProvider') + ';\n'
        loop = extract_braced(provider, 'void loop() override')
        failed_poll = extract_braced(loop, 'if (!ublox_GNSS.getPVT(8))')
        start = loop.index('ublox_GNSS.flushPVT();')
        end = loop.index(failed_poll) + len(failed_poll)
        admission = loop[start:end]
        if admission.count('ublox_GNSS.getPVT(8)') != 1:
            raise AssertionError('expected one actual explicit PVT admission')
        # Restore the historical normal-operation omission, without referring
        # to its now-removed position-request flag or copying a stale provider.
        legacy_loop = loop[:start] + 'if (_fresh_time_sync_required) {\n' + admission \
            + '\n      }' + loop[end:]
        legacy = provider.replace(loop, legacy_loop, 1)
        fixture = FIXTURE.read_text(encoding='ascii')
        sanitize = [] if not sys.platform.startswith('linux') else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-omit-frame-pointer', '-fno-pie', '-no-pie']
        cls.binaries = {}
        for kind, actual in (('production', provider), ('legacy', legacy)):
            cpp = work / (kind + '.cpp')
            cpp.write_text(fixture.replace('@PROVIDER@', actual), encoding='utf-8')
            binary = work / (kind + ('.exe' if os.name == 'nt' else ''))
            built = subprocess.run([
                compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-parameter', *sanitize, '-I' + str(work),
                '-I' + str(ROOT / 'src'), str(cpp), '-o', str(binary)],
                capture_output=True, text=True, timeout=60)
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)
            cls.binaries[kind] = binary

    def check_case(self, case, kind='production', diagnostic=None):
        checked = subprocess.run([str(self.binaries[kind]), case],
                                 capture_output=True, text=True, timeout=10)
        if diagnostic is not None:
            self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn(diagnostic, checked.stderr)
            self.assertNotIn('runtime error:', checked.stderr)
            self.assertNotIn('AddressSanitizer:', checked.stderr)
        else:
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn('PASS ' + case, checked.stdout)

    def test_one_startup_pvt_cannot_be_counted_again_after_poll_failure(self):
        self.check_case('startup-silence')

    def test_periodic_resync_does_not_rewind_clock_from_retained_pvt(self):
        self.check_case('periodic-silence')

    def test_poll_failure_resets_consecutive_count_and_three_new_samples_recover(self):
        self.check_case('consecutive-failure')

    def test_success_failure_and_throttled_calls_have_bounded_poll_and_getter_cost(self):
        self.check_case('poll-budget')

    def test_tracker_position_and_fresh_time_requests_require_new_pvt(self):
        for case in ('position-request', 'fresh-tracker'):
            with self.subTest(case=case):
                self.check_case(case)

    def test_reset_cannot_reuse_retained_receiver_cache(self):
        self.check_case('reset')

    def test_null_clock_still_updates_position_without_time_side_effects(self):
        self.check_case('null-clock')

    def test_unqualified_fresh_frames_reset_consecutive_time_samples(self):
        for field in ('fix', 'date', 'time', 'sats', 'epoch'):
            with self.subTest(field=field):
                self.check_case('invalid-' + field)

    def test_adapter_retains_cache_on_failed_explicit_and_implicit_polls(self):
        self.check_case('boundary-contract')

    def test_historical_omission_fails_clock_oracles_but_preserves_fresh_tracker(self):
        for case in ('startup-silence', 'periodic-silence'):
            with self.subTest(case=case):
                self.check_case(case, 'legacy', 'stale PVT must not update the clock')
        self.check_case('fresh-tracker', 'legacy')


if __name__ == '__main__':
    unittest.main()
