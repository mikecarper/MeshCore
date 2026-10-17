"""Exercise the complete original-UI RGB status handler and its actual writers.

Clock, ADC, power and GPIO are host fakes. Separate processes preserve real
function-static initialization. This is not physical LED or power-use proof.
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
FIXTURE = ROOT / 'test/fixtures/rgb_led_rollover.cpp'
CASES = (
    'boot-zero', 'boot-upper', 'boot-wrap', 'boot-zero-end', 'wheel-colors', 'message',
    'message-wrap', 'message-retrigger', 'message-first', 'message-decrease',
    'normal', 'unread', 'connected', 'low-battery', 'threshold',
    'heartbeat-wrap-on', 'heartbeat-wrap-off', 'heartbeat-zero-stamp',
    'heartbeat-long', 'heartbeat-msg-phase', 'battery-cadence',
    'poll-wrap-before', 'poll-wrap-after', 'poll-zero-stamp', 'poll-long',
    'poll-near-full', 'poll-read-delays', 'owner-fresh', 'mask-fresh',
    'owner-flourish-short', 'mask-flourish-long', 'owner-on-long',
    'owner-off-long', 'mask-on-long', 'mask-off-long', 'both-vetoes',
)
POWER_CASES = (
    'power-cadence', 'charging', 'charged', 'charge-unread',
    'charge-to-heartbeat', 'power-unpowered-charge-skip', 'poll-powered-read-delays',
)


def harness(mutation=None):
    source = (ROOT / 'examples/companion_radio/ui-orig/UITask.cpp').read_text(encoding='ascii')
    abstract = (ROOT / 'examples/companion_radio/AbstractUITask.h').read_text(encoding='ascii')
    notification = (ROOT / 'src/helpers/CompanionNotificationPolicy.h').read_text(encoding='ascii')
    output_enum = extract_braced(notification, 'enum Output : uint8_t') + ';'
    constants = source[source.index('#if defined(PIN_STATUS_LED) ||'):
                       source.index('#ifndef USER_BTN_PRESSED')]
    defaults = '\n'.join(re.findall(
        r'^  (?:bool _notification_led_owned|uint8_t _notification_outputs) = .*;',
        abstract, re.MULTILINE))
    setters = '\n'.join(extract_braced(abstract, signature) for signature in (
        'void setNotificationLedOwnership(', 'void setNotificationOutputs('))
    handler = extract_braced(source, 'void UITask::userLedHandler()')
    if mutation == 'no-owner-guard':
        guard = 'if (_notification_led_owned || !(_notification_outputs & mesh::notify::Led)) return;'
        if handler.count(guard) != 1:
            raise AssertionError('the actual ownership/output guard must be present')
        handler = handler.replace(guard, '', 1)
    elif mutation == 'absolute-battery-deadline':
        handler, count = re.subn(
            r'static_cast<uint32_t>\(cur_time - (\w+)\) > 60000',
            r'cur_time > static_cast<uint32_t>(\1 + 60000u)', handler)
        if count != 1:
            raise AssertionError('the actual elapsed battery timer must be present')
    elif mutation == 'completion-time-anchor':
        # The actual handler deliberately captures the clock before either read.
        # Do not depend on the private name of the elapsed-time anchor.
        rgb, mono = handler.split('#elif defined(PIN_STATUS_LED)', 1)
        rgb, count = re.subn(r'(\b\w+\s*=\s*)cur_time;', r'\1millis();', rgb)
        if count < 2:
            raise AssertionError('RGB timers must retain captured-time anchors')
        handler = rgb + '#elif defined(PIN_STATUS_LED)' + mono
    writers = '\n'.join(extract_braced(source, signature) for signature in (
        'static void statusLedWrite(', 'static void statusLedWheel('))
    code = FIXTURE.read_text(encoding='ascii')
    for token, value in {'CONSTANTS': constants, 'DEFAULTS': defaults,
                         'SETTERS': setters, 'HANDLER': handler,
                         'WRITERS': writers, 'OUTPUT_ENUM': output_enum}.items():
        code = code.replace('@' + token + '@', value)
    return code


class RgbLedRolloverTest(unittest.TestCase):
    def test_actual_rgb_handler(self):
        self.run_variants()

    def test_ownership_regression_is_rejected(self):
        self.run_variants('no-owner-guard', ('owner-fresh',), 'batt_reads == 0')

    def test_captured_poll_anchor_regression_is_rejected(self):
        self.run_variants('completion-time-anchor', ('poll-read-delays',), 'batt_reads == 2')

    def test_absolute_poll_deadline_regression_is_rejected(self):
        self.run_variants('absolute-battery-deadline', ('poll-wrap-before',),
                          'batt_reads == unsigned(before ? 1 : 2)')

    def run_variants(self, mutation=None, cases=None, expected_failure=None):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        sanitize = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='rgb-led-rollover-') as directory:
            work = Path(directory)
            source = work / 'fixture.cpp'
            source.write_text(harness(mutation), encoding='ascii')
            variants = ('charge', 'no-charge') if mutation else ('charge', 'no-charge', 'disabled')
            for variant in variants:
                with self.subTest(variant=variant):
                    macros = [] if variant == 'disabled' else [
                        '-DPIN_STATUS_LED_R=3', '-DPIN_STATUS_LED_G=24', '-DPIN_STATUS_LED_B=28']
                    if variant == 'charge':
                        # X1 aliases its monochrome status pin to RGB green.
                        macros += ['-DEXT_CHRG_DETECT=35', '-DPIN_STATUS_LED=24']
                    binary = work / (variant + ('.exe' if os.name == 'nt' else ''))
                    built = subprocess.run([
                        compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        *sanitize, *macros,
                        '-I' + str(ROOT / 'src'), str(source), '-o', str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    selected = cases if cases is not None else (
                        CASES + POWER_CASES if variant == 'charge' else
                        CASES if variant == 'no-charge' else ('no-led',))
                    for case in selected:
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
