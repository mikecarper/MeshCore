"""Exercise real monochrome LED timers, exact field defaults, and ownership gates.

The complete production handler is compiled for original/new/tiny UIs. RGB and
backlight are not configured. uint32_t millis and GPIO writes are host fakes;
this is not electrical, scheduler-latency, or hardware power-consumption proof.
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
FIXTURE = ROOT / 'test/fixtures/mono_led_rollover.cpp'
UIS = ('ui-orig', 'ui-new', 'ui-tiny')
CASES = ('zero', 'upper', 'max', 'normal', 'unread', 'signed', 'wrap', 'off-wrap',
         'zero-stamp', 'message-on', 'message-off', 'owner-fresh', 'mask-fresh',
         'owner-on-long', 'owner-off-long', 'mask-on-long', 'mask-off-long',
         'suppressed-short', 'delayed-one-phase', 'both-vetoes')


def harness(ui, mutation=None):
    prefix = ROOT / 'examples/companion_radio' / ui
    source = (prefix / 'UITask.cpp').read_text(encoding='ascii')
    header = (prefix / 'UITask.h').read_text(encoding='ascii')
    abstract = (ROOT / 'examples/companion_radio/AbstractUITask.h').read_text(encoding='ascii')
    handler = extract_braced(source, 'void UITask::userLedHandler()')
    first_constant = source.index('#define LED_ON_MILLIS')
    constants = source[source.rfind('#', 0, first_constant):
                       source.index('#endif', first_constant) + len('#endif')]
    fields = ''
    if ui != 'ui-orig':
        first_field = header.index('  int led_state = 0;')
        fields = header[header.rfind('#ifdef PIN_STATUS_LED', 0, first_field):
                        header.index('#endif', first_field) + len('#endif')]
    defaults = '\n'.join(re.findall(r'^  (?:bool _notification_led_owned|uint8_t _notification_outputs) = .*;',
                                    abstract, re.MULTILINE))
    setters = '\n'.join(extract_braced(abstract, signature) for signature in (
        'void setNotificationLedOwnership(', 'void setNotificationOutputs('))
    if mutation == 'no-owner-guard':
        guard = 'if (_notification_led_owned || !(_notification_outputs & mesh::notify::Led)) return;'
        if handler.count(guard) != 1:
            raise AssertionError('the actual ownership/output guard must be present')
        handler = handler.replace(guard, '', 1)
    elif mutation == 'non-strict-edge':
        # Only mutate the monochrome branch, leaving the unrelated RGB text alone.
        first = handler.index('#elif defined(PIN_STATUS_LED)') if ui == 'ui-orig' else 0
        before, mono = handler[:first], handler[first:]
        mono, count = re.subn(r'(\)\s*)>(\s*(?:led_)?phase_millis\))', r'\1>=\2', mono, count=1)
        if count != 1:
            raise AssertionError('monochrome phase timer must retain its strict edge')
        handler = before + mono
    code = FIXTURE.read_text(encoding='ascii')
    for token, value in {'CONSTANTS': constants, 'FIELDS': fields, 'DEFAULTS': defaults,
                         'SETTERS': setters, 'HANDLER': handler}.items():
        code = code.replace('@' + token + '@', value)
    return code


class MonoLedRolloverTest(unittest.TestCase):
    def test_actual_monochrome_handlers_preserve_cadence_and_ownership(self):
        self.run_variants()

    def test_missing_ownership_guard_fails_semantic_check(self):
        self.run_variants('no-owner-guard', ('owner-fresh',), 'FAIL owner-fresh: writes == count')

    def test_early_edge_regression_is_rejected(self):
        self.run_variants('non-strict-edge', ('zero',), 'FAIL zero: writes == count')

    def run_variants(self, mutation=None, cases=CASES, expected_failure=None):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        sanitize = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='mono-led-rollover-') as directory:
            work = Path(directory)
            for ui in UIS:
                source = work / (ui + '.cpp')
                source.write_text(harness(ui, mutation), encoding='ascii')
                variants = (0, 1) if mutation else (0, 1, None)
                for polarity in variants:
                    with self.subTest(ui=ui, polarity=polarity):
                        name = ui + '-' + ('none' if polarity is None else str(polarity))
                        binary = work / (name + ('.exe' if os.name == 'nt' else ''))
                        macros = (['-DPIN_STATUS_LED=24', '-DLED_STATE_ON=' + str(polarity)]
                                  if polarity is not None else [])
                        built = subprocess.run([
                            compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                            '-Wno-misleading-indentation', *sanitize, *macros,
                            '-I' + str(ROOT / 'src'), str(source), '-o', str(binary),
                        ], capture_output=True, text=True, timeout=60)
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        # ui-orig really uses function statics: never reset them in a fake.
                        for case in cases if polarity is not None else ('no-led',):
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
