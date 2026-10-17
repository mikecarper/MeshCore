"""Check one-shot infrastructure startup windows with a real uint32 clock.

Sensor coverage compiles its actual header, complete begin, page methods,
renderer and foreground loop. Repeater/Room coverage compiles their headers,
begin/reset methods, exact renderer/signature boot expressions and actual loop
prefix only. Board, radio, display and optional page helpers are host adapters;
this does not qualify complete infrastructure rendering or physical hardware.
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
FIXTURE = ROOT / 'test/fixtures/ui_startup_windows.cpp'
ROLES = ('simple_sensor', 'simple_repeater', 'simple_room_server')


def startup_retirement(loop):
    """Extract the real first retirement branch, never a handwritten predicate."""
    return extract_braced(loop, 'if (!_startup_complete &&')


def boot_expression(method):
    matched = re.search(r'\bif\s*\(([^\n]*_startup_complete[^\n]*)\)\s*\{', method)
    if not matched:
        raise AssertionError('expected a guarded single-line startup expression')
    return matched.group(1)


class UiStartupWindowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            raise unittest.SkipTest('a host C++17 compiler is required')
        cls.temp = tempfile.TemporaryDirectory(prefix='ui-startup-windows-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        (work / 'helpers/ui').mkdir(parents=True)
        for stub in ('helpers/ui/DisplayDriver.h', 'helpers/CommonCLI.h'):
            (work / stub).write_text('#pragma once\n', encoding='ascii')
        sanitize = [] if not sys.platform.startswith('linux') else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-omit-frame-pointer', '-fno-pie', '-no-pie']
        cls.binaries = {}
        fixture = FIXTURE.read_text(encoding='ascii')
        for role in ROLES:
            folder = ROOT / 'examples' / role
            source = (folder / 'UITask.cpp').read_text(encoding='ascii')
            header = (folder / 'UITask.h').read_text(encoding='ascii')
            if header.count('class UITask {') != 1:
                raise AssertionError('expected one real UI class')
            header = header.replace('class UITask {', 'struct UITask {')
            # Only expose observations; no extra state or changed declarations.
            header = header.replace('public:',
                                    'bool testRenderBoot();\n  bool testSignatureBoot();\npublic:', 1)
            directory = work / role
            directory.mkdir()
            (directory / 'UITask.h').write_text(header, encoding='ascii')
            begin = extract_braced(source, 'void UITask::begin(')
            reset = extract_braced(source, 'void UITask::resetRadioProfileDisplayPage(')
            loop = extract_braced(source, 'void UITask::loop()')
            if role == 'simple_sensor':
                methods = '\n'.join((begin, reset,
                    extract_braced(source, 'bool UITask::showingSecondaryRadioProfilePage('),
                    extract_braced(source, 'void UITask::renderCurrScreen()'), loop))
            else:
                prefix = loop[:loop.index('  const bool morse_timing')]
                render = extract_braced(source, 'void UITask::renderCurrScreen()')
                signature = extract_braced(source, 'uint32_t UITask::getFrameSignature()')
                methods = '\n'.join((begin, reset, prefix + '}\n',
                    'void UITask::applyDisplayFlip() {}',
                    'bool UITask::testRenderBoot() { return ' + boot_expression(render) + '; }',
                    'bool UITask::testSignatureBoot() { return ' + boot_expression(signature) + '; }'))
            variants = [('production', methods)]
            if role == 'simple_sensor':
                retirement = startup_retirement(loop)
                if methods.count(retirement) != 1:
                    raise AssertionError('expected exactly one actual retirement branch')
                variants.append(('no-retirement', methods.replace(retirement, '', 1)))
            for kind, generated_methods in variants:
                cpp = directory / (kind + '.cpp')
                cpp.write_text(fixture.replace('@METHODS@', generated_methods), encoding='ascii')
                binary = directory / (kind + ('.exe' if os.name == 'nt' else ''))
                built = subprocess.run([
                    compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    *sanitize, '-DTEST_SENSOR=' + str(int(role == 'simple_sensor')),
                    '-DTEST_REPEATER=' + str(int(role == 'simple_repeater')),
                    '-I' + str(work), str(cpp), '-o', str(binary)],
                    capture_output=True, text=True, timeout=60)
                if built.returncode:
                    raise AssertionError(built.stdout + built.stderr)
                cls.binaries[role, kind] = binary

    def check_case(self, case, roles=('simple_sensor',), kind='production', fails=False):
        for role in roles:
            with self.subTest(role=role, case=case, kind=kind):
                checked = subprocess.run([str(self.binaries[role, kind]), case],
                                         capture_output=True, text=True, timeout=10)
                if fails:
                    self.assertNotEqual(checked.returncode, 0, checked.stdout)
                    self.assertIn('FAIL ' + case, checked.stderr)
                    self.assertNotIn('runtime error:', checked.stderr)
                    self.assertNotIn('AddressSanitizer:', checked.stderr)
                else:
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn('PASS ' + case, checked.stdout)

    def test_sensor_exact_boot_boundary_and_absolute_late_begin(self):
        for case in ('boundary', 'late-begin', 'upper-begin', 'max-begin'):
            self.check_case(case)

    def test_sensor_live_loops_do_not_replay_after_full_clock_wrap(self):
        self.check_case('live-wrap')

    def test_sensor_retires_without_display_or_morse_rendering(self):
        for case in ('off-retirement', 'morse-retirement',
                     'off-first', 'morse-first'):
            self.check_case(case)

    def test_sensor_normal_wake_and_rebegin_preserve_startup_policy(self):
        for case in ('wake', 'rebegin'):
            self.check_case(case)

    def test_removing_actual_sensor_retirement_reproduces_semantic_failure(self):
        for case in ('live-wrap', 'off-retirement', 'morse-retirement',
                     'off-first', 'morse-first'):
            self.check_case(case, kind='no-retirement', fails=True)

    def test_infrastructure_actual_init_prefix_and_render_metadata(self):
        for case in ('infra-boundary', 'infra-wrap-start', 'infra-rebegin'):
            self.check_case(case, ('simple_repeater', 'simple_room_server'))

    def test_retirement_is_before_hardware_and_rendering_gates(self):
        for role in ROLES:
            with self.subTest(role=role):
                folder = ROOT / 'examples' / role
                source = (folder / 'UITask.cpp').read_text(encoding='ascii')
                header = (folder / 'UITask.h').read_text(encoding='ascii')
                self.assertRegex(header, r'bool _startup_complete\s*=\s*false;')
                loop = extract_braced(source, 'void UITask::loop()')
                retired = startup_retirement(loop)
                self.assertIn('_startup_complete = true;', retired)
                self.assertLess(loop.index(retired), loop.index('const bool morse_timing'))
                self.assertLess(loop.index(retired), loop.index('servicePower('))
                main = (folder / 'main.cpp').read_text(encoding='ascii')
                self.assertIn('ui_task.begin(', extract_braced(main, 'void setup()'))
                # Room's alternative preprocessor branches have unmatched
                # braces before preprocessing; do not parse them as one block.
                self.assertIn('ui_task.loop();', main[main.index('\nvoid loop()'):])


if __name__ == '__main__':
    unittest.main()
