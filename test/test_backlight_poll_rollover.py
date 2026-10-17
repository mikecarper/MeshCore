"""Compile actual guarded backlight polling with original timer initialization.

Both UI paths are fixture-enabled, plus feature-disabled controls. Current
normal BACKLIGHT_BTN recipes select ui-new; ui-tiny is consistency coverage.
Arduino clock/GPIO and display wake are host fakes, not hardware sleep tests.
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
FIXTURE = ROOT / 'test/fixtures/backlight_poll_rollover.cpp'
UIS = ('ui-new', 'ui-tiny')
CASES = ('zero', 'upper', 'upper-pressed', 'max', 'normal', 'signed-boundary',
         'wrap-visible', 'wrap-wake', 'deadline-zero', 'held', 'unobserved-release',
         'null-pressed', 'null-released', 'completion', 'completion-zero-wake',
         'completion-zero-read', 'long-released', 'long-pressed', 'near-full-cycle',
         'late-one-poll')


def production(ui):
    prefix = ROOT / 'examples/companion_radio' / ui
    source = (prefix / 'UITask.cpp').read_text(encoding='ascii')
    header = (prefix / 'UITask.h').read_text(encoding='ascii')
    first = source.index('#if defined(BACKLIGHT_BTN)')
    block = source[first:source.index('#endif', first) + len('#endif')]
    field = re.search(r'^  (?:int|uint32_t) (?:next|last)_backlight_btn_check = 0;',
                      header, re.MULTILINE).group()
    return source, field, block


def harness(ui, mutation=None):
    _, field, block = production(ui)
    if mutation == 'level-wake':
        block, count = re.subn(r'pressed\s*&&\s*button_state\s*!=\s*BacklightButtonState::Pressed\s*&&',
                               'pressed &&', block, count=1)
        if count != 1:
            raise AssertionError('backlight wake must recognize a newly sampled press')
    elif mutation == 'entry-anchor':
        old = 'last_backlight_btn_check = millis();'
        if block.count(old) != 1:
            raise AssertionError('backlight timestamp must be anchored after I/O and wake')
        block = block.replace(old, 'last_backlight_btn_check = backlight_now;', 1)
    return FIXTURE.read_text(encoding='ascii').replace('@FIELD@', field).replace('@BLOCK@', block)


class BacklightPollRolloverTest(unittest.TestCase):
    def test_actual_polling_wrap_cadence_and_button_state(self):
        self.run_variants()

    def test_block_remains_in_live_loop_with_32bit_timer(self):
        for ui in UIS:
            with self.subTest(ui=ui):
                source, field, block = production(ui)
                self.assertEqual(field.strip(), 'uint32_t last_backlight_btn_check = 0;')
                loop = extract_braced(source, 'void UITask::loop()')
                self.assertEqual(loop.count(block), 1)
                self.assertLess(loop.index(block), loop.index('userLedHandler();'))
                self.assertIn('digitalRead(PIN_BUTTON2) == LOW', block)
                self.assertIn('_display->wake(mesh::ui::DisplayWake::Button)', block)

    def test_held_level_wake_regression_is_rejected(self):
        self.run_variants('level-wake', ('held',), 'FAIL held: wakes == expected_wakes')

    def test_entry_anchoring_regression_is_rejected(self):
        self.run_variants('entry-anchor', ('completion',), 'FAIL completion: reads == expected_reads')

    def run_variants(self, mutation=None, cases=CASES, expected_failure=None):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        sanitize = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='backlight-poll-rollover-') as directory:
            work = Path(directory)
            for ui in UIS:
                source = work / (ui + '.cpp')
                source.write_text(harness(ui, mutation), encoding='ascii')
                for enabled in (True,) if mutation else (True, False):
                    with self.subTest(ui=ui, enabled=enabled):
                        binary = work / (ui + '-' + str(int(enabled)) +
                                         ('.exe' if os.name == 'nt' else ''))
                        # A disabled build deliberately has no PIN_BUTTON2/LOW
                        # definitions: accidental unguarded I/O cannot compile.
                        macros = ['-DBACKLIGHT_BTN=PIN_BUTTON2', '-DPIN_BUTTON2=14'] if enabled else []
                        built = subprocess.run([
                            compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                            *sanitize, *macros, str(source), '-o', str(binary),
                        ], capture_output=True, text=True, timeout=60)
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        # Preserve the real function-static sampled state by
                        # launching a fresh process, not resetting fake statics.
                        for case in cases if enabled else ('no-feature',):
                            with self.subTest(case=case):
                                tested = subprocess.run([str(binary), case], capture_output=True,
                                                        text=True, timeout=10)
                                if expected_failure:
                                    self.assertEqual(tested.returncode, 1, tested.stdout + tested.stderr)
                                    self.assertIn(expected_failure, tested.stderr)
                                    self.assertNotIn('AddressSanitizer:', tested.stderr)
                                    self.assertNotIn('runtime error:', tested.stderr)
                                else:
                                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                                    self.assertRegex(tested.stdout, r'PASS ' + case + r': \d+ checks')


if __name__ == '__main__':
    unittest.main()
