"""Run complete infrastructure UI begin methods and headers with denied strdup.

Only board, display, button and radio-page boundaries are adapters. This does
not simulate total-heap exhaustion or qualify hardware/display initialization.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
ROLES = ('simple_sensor', 'simple_repeater', 'simple_room_server')
FIXTURE = ROOT / 'test/fixtures/infrastructure_ui_version.cpp'

# Original identical formatter from all three roles at 7acd76d0950e89d2324191c9359053b364e03db5.
COPY_FORMAT = '''  // strip off dash and commit hash by changing dash to null terminator
  // e.g: v1.2.3-abcdef -> v1.2.3
  char *version = strdup(firmware_version);
  char *dash = strchr(version, '-');
  if(dash){
    *dash = 0;
  }

  // v1.2.3 (1 Jan 2025)
  snprintf(_version_info, sizeof(_version_info), "%s (%s)", version, build_date);
  free(version);
'''


class InfrastructureUiVersionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            raise unittest.SkipTest('a host C++17 compiler is required')
        cls.temp = tempfile.TemporaryDirectory(prefix='infrastructure-ui-version-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        (work / 'helpers/ui').mkdir(parents=True)
        for stub in ('helpers/ui/DisplayDriver.h', 'helpers/CommonCLI.h',
                     'helpers/RadioActivityWindow.h', 'helpers/ui/ObserverDashboard.h',
                     'helpers/ui/CHSC6XTouch.h'):
            (work / stub).write_text('#pragma once\n', encoding='ascii')
        sanitize = [] if not sys.platform.startswith('linux') else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-omit-frame-pointer', '-fno-pie', '-no-pie']
        cls.binaries = {}
        for role in ROLES:
            directory = work / role
            directory.mkdir()
            source = (ROOT / 'examples' / role / 'UITask.cpp').read_text(encoding='ascii')
            header = (ROOT / 'examples' / role / 'UITask.h').read_text(encoding='ascii')
            # Access changes only; preserve the actual declarations and array.
            if header.count('class UITask {') != 1:
                raise AssertionError('expected one actual UI class declaration')
            (directory / 'UITask.h').write_text(
                header.replace('class UITask {', 'struct UITask {'), encoding='ascii')
            begin = extract_braced(source, 'void UITask::begin(')
            start = begin.index('  // strip off dash')
            end = (begin.index('  // Make the first read/frame', start)
                   if role == 'simple_sensor' else begin.rfind('}'))
            legacy = begin[:start] + COPY_FORMAT + '\n' + begin[end:]
            for kind, method in (('production', begin), ('copy', legacy)):
                cpp = directory / (kind + '.cpp')
                cpp.write_text(FIXTURE.read_text(encoding='ascii').replace('@BEGIN@', method),
                               encoding='ascii')
                for features in (0, 1, 2):
                    macros = ['-DTEST_SENSOR=' + str(int(role == 'simple_sensor')),
                              '-DTEST_REPEATER=' + str(int(role == 'simple_repeater')),
                              '-DTEST_FEATURES=' + str(features)]
                    if features:
                        macros += ['-DPIN_USER_BTN=4', '-DDISPLAY_CLASS=DisplayDriver',
                                   '-DMOMENTARY_BUTTON_WAKE_FROM_SLEEP=1']
                    if features == 2:
                        macros += ['-DDISPLAY_TOUCH_TOGGLE=1', '-DDISPLAY_ACTIVITY_DASHBOARD=1',
                                   '-DDISPLAY_REDRAW_ON_CHANGE=1']
                    binary = directory / (kind + '-' + str(features)
                                          + ('.exe' if os.name == 'nt' else ''))
                    built = subprocess.run([
                        compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        *sanitize, *macros, '-I' + str(work), str(cpp), '-o', str(binary)],
                        capture_output=True, text=True, timeout=60)
                    if built.returncode:
                        raise AssertionError(built.stdout + built.stderr)
                    cls.binaries[role, kind, features] = binary

    def check_case(self, case, kind='production', expected_failure=False):
        for role in ROLES:
            for features in (0, 1, 2):
                with self.subTest(role=role, features=features, case=case):
                    checked = subprocess.run([str(self.binaries[role, kind, features]), case],
                                             capture_output=True, text=True, timeout=10)
                    if expected_failure:
                        self.assertNotEqual(checked.returncode, 0)
                        self.assertRegex(checked.stderr,
                                         r'runtime error: null pointer|AddressSanitizer:')
                    else:
                        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                        self.assertIn('PASS ' + case, checked.stdout)

    def test_displayed_labels_bounds_and_startup_side_effects(self):
        self.check_case('format')

    def test_formatting_needs_no_mutable_version_copy(self):
        self.check_case('no-copy')

    def test_denied_version_allocation_cannot_crash_startup(self):
        self.check_case('denied')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'sanitizer diagnostic control')
    def test_original_copy_failure_is_reproduced_not_silently_skipped(self):
        self.check_case('denied', 'copy', expected_failure=True)

    def test_actual_capacity_live_startup_and_ci_wiring(self):
        for role in ROLES:
            with self.subTest(role=role):
                folder = ROOT / 'examples' / role
                header = (folder / 'UITask.h').read_text(encoding='ascii')
                self.assertRegex(header, r'char _version_info\[32\];')
                main = (folder / 'main.cpp').read_text(encoding='ascii')
                self.assertIn('ui_task.begin(', extract_braced(main, 'void setup()'))
        workflow = (ROOT / '.github/workflows/run-unit-tests.yml').read_text(encoding='ascii')
        self.assertIn('python3 -B test/test_infrastructure_ui_version.py -v', workflow)


if __name__ == '__main__':
    unittest.main()
