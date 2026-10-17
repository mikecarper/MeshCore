"""Exercise the actual sensor UI header, begin and full loop with a 32-bit clock.

Display, board, radio and button I/O are host adapters. Rendering, physical
sleep and hardware timing are not qualified by this scheduling regression.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / 'examples/simple_sensor'
FIXTURE = ROOT / 'test/fixtures/sensor_ui_polling.cpp'


class SensorUiPollingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            raise unittest.SkipTest('a host C++17 compiler is required')
        cls.directory = tempfile.TemporaryDirectory(prefix='sensor-ui-polling-')
        cls.addClassCleanup(cls.directory.cleanup)
        work = Path(cls.directory.name)
        (work / 'helpers/ui').mkdir(parents=True)
        for stub in ('helpers/ui/DisplayDriver.h', 'helpers/CommonCLI.h'):
            (work / stub).write_text('#pragma once\n', encoding='ascii')
        (work / 'UITask.h').write_bytes((UI / 'UITask.h').read_bytes())
        source = (UI / 'UITask.cpp').read_text(encoding='ascii')
        generated = FIXTURE.read_text(encoding='ascii')
        for marker, signature in (
                ('BEGIN', 'void UITask::begin('),
                ('RESET', 'void UITask::resetRadioProfileDisplayPage('),
                ('LOOP', 'void UITask::loop()')):
            generated = generated.replace('@' + marker + '@', extract_braced(source, signature))
        cpp = work / 'test.cpp'
        cpp.write_text(generated, encoding='ascii')
        sanitize = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
        cls.binaries = {}
        for variant in ('none', 'raw', 'momentary'):
            macros = ['-DTEST_RAW_BUTTON=' + str(int(variant == 'raw')),
                      '-DTEST_MOMENTARY_BUTTON=' + str(int(variant == 'momentary'))]
            if variant != 'none':
                macros += ['-DPIN_USER_BTN=7', '-DDISPLAY_CLASS=DisplayDriver']
            if variant == 'momentary':
                macros += ['-DMOMENTARY_BUTTON_WAKE_FROM_SLEEP=1',
                           '-DMOMENTARY_BUTTON_WAKE_HOLD_MS=1000']
            binary = work / (variant + ('.exe' if os.name == 'nt' else ''))
            built = subprocess.run([
                compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                *sanitize, *macros, '-I' + str(work), str(cpp), '-o', str(binary)],
                capture_output=True, text=True, timeout=60)
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)
            cls.binaries[variant] = binary

    def check_case(self, case):
        for variant, binary in self.binaries.items():
            with self.subTest(variant=variant, case=case):
                checked = subprocess.run([str(binary), case], capture_output=True,
                                         text=True, timeout=10)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn('PASS ' + case, checked.stdout)

    def test_initial_work_at_zero_upper_half_and_max(self):
        for case in ('zero', 'upper-half', 'max'):
            self.check_case(case)

    def test_normal_cadence_and_wrapped_future(self):
        for case in ('cadence', 'wrapped-future', 'zero-boundary'):
            self.check_case(case)

    def test_overdue_polling_and_morse_deferral_across_wrap(self):
        for case in ('overdue-wrap', 'morse-wrap'):
            self.check_case(case)

    def test_force_and_long_idle_refresh(self):
        for case in ('forced-refresh', 'long-display-off'):
            self.check_case(case)

    def test_force_at_zero_and_force_during_morse(self):
        for case in ('forced-zero', 'forced-morse', 'momentary-zero-wake',
                     'momentary-zero-morse'):
            self.check_case(case)

    def test_work_is_anchored_after_io_and_begin_rearms(self):
        for case in ('completion-anchor', 'rebegin'):
            self.check_case(case)

    def test_button_wake_paths(self):
        self.check_case('button-wake')


class SensorUiPollingWiringTest(unittest.TestCase):
    def test_live_role_and_ci_run_the_regression(self):
        main = (UI / 'main.cpp').read_text(encoding='ascii')
        self.assertIn('ui_task.begin(', extract_braced(main, 'void setup()'))
        self.assertIn('ui_task.loop();', extract_braced(main, '\nvoid loop()'))
        workflow = (ROOT / '.github/workflows/run-unit-tests.yml').read_text(encoding='ascii')
        self.assertIn('python3 -B test/test_sensor_ui_polling.py -v', workflow)


if __name__ == '__main__':
    unittest.main()
