"""Test actual HomeScreen sensor-page refresh and force-input timer bodies.

Both UI implementations are compiled with the sensor page enabled at each
cadence. This does not claim that every normal board recipe enables that page.
Clock, task, and board/sensor/LPP boundaries are host fakes, not hardware tests.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'test/fixtures/home_sensor_refresh.cpp'


def harness(ui, mutation=None):
    source = (ROOT / 'examples/companion_radio' / ui / 'UITask.cpp').read_text(encoding='ascii')
    field_start = source.index('  CayenneLPP sensors_lpp;')
    refresh_start = source.index('  void refresh_sensors()', field_start)
    fields = source[field_start:refresh_start]
    # Keep sensor fields exactly as declared, without the neighboring radio UI.
    fields = fields.split('  uint32_t _radio_profile_page_started_at')[0]
    refresh = extract_braced(source, 'void refresh_sensors()')
    key = 'key' if ui == 'ui-new' else 'c'
    input_block = extract_braced(source,
                                f'if ({key} == KEY_ENTER && _page == HomePage::SENSORS)')
    if mutation == 'no-force-invalidation':
        if input_block.count('sensors_lpp.reset();') != 1:
            raise AssertionError('sensor-page input must explicitly invalidate its LPP cache')
        input_block = input_block.replace('sensors_lpp.reset();', '', 1)
    elif mutation == 'no-empty-marker':
        refresh, count = re.subn(r'sensors_lpp\.getSize\(\)\s*==\s*0\s*\|\|\s*',
                                 '', refresh, count=1)
        if count != 1:
            raise AssertionError('refresh must recognize its empty initial/forced cache')
    code = FIXTURE.read_text(encoding='ascii')
    for token, value in {'FIELDS': fields, 'REFRESH': refresh,
                         'KEY': key, 'INPUT': input_block}.items():
        code = code.replace('@' + token + '@', value)
    return code


class HomeSensorRefreshTest(unittest.TestCase):
    def test_actual_sensor_page_cadence_wrap_force_and_allocation(self):
        self.run_variants()

    def test_missing_force_invalidation_fails_intended_regression(self):
        self.run_variants('no-force-invalidation', 'force', 'FAIL forced input:')

    def test_missing_empty_marker_fails_first_poll_at_zero(self):
        self.run_variants('no-empty-marker', None, 'FAIL fresh page:')

    def run_variants(self, mutation=None, argument=None, expected_failure=None):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        sanitize = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='home-sensor-refresh-') as directory:
            work = Path(directory)
            for ui in ('ui-new', 'ui-tiny'):
                source = work / (ui + '.cpp')
                source.write_text(harness(ui, mutation), encoding='ascii')
                for auto_off in (15000, 0):
                    with self.subTest(ui=ui, auto_off=auto_off):
                        binary = work / (ui + '-' + str(auto_off) +
                                         ('.exe' if os.name == 'nt' else ''))
                        compiled = subprocess.run([
                            compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                            *sanitize, '-DAUTO_OFF_MILLIS=' + str(auto_off),
                            '-DTEST_NEW_UI=' + str(int(ui == 'ui-new')),
                            str(source), '-o', str(binary),
                        ], text=True, capture_output=True, timeout=60)
                        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                        tested = subprocess.run([str(binary), *([argument] if argument else [])],
                                                text=True, capture_output=True, timeout=10)
                        if expected_failure:
                            self.assertEqual(tested.returncode, 1, tested.stdout + tested.stderr)
                            self.assertIn(expected_failure, tested.stderr)
                            self.assertNotIn('AddressSanitizer:', tested.stderr)
                            self.assertNotIn('runtime error:', tested.stderr)
                        else:
                            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                            self.assertRegex(tested.stdout, r'PASS: (15|16) scenarios; \d+ checks')


if __name__ == '__main__':
    unittest.main()
