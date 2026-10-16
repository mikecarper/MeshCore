#!/usr/bin/env python3
"""Run complete actual UI loops against explicit 32-bit clocks and fields.

The test adapts display/screen/radio/input dependencies, not scheduling or popup
conditions. It excludes optional button, touch, buzzer, reader and vibration
branches. Startup begin()/hardware renderers are not compiled by this boundary.
"""

from pathlib import Path
import shutil
import subprocess
import unittest
import tempfile

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / 'fixtures/companion_ui_deadlines.cpp'
VARIANTS = ('ui-orig', 'ui-tiny', 'ui-new')
BUILD_VARIANTS = VARIANTS + ('ui-tiny-cache',)


class CompanionUiDeadlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            raise unittest.SkipTest('a host C++17 compiler is required')
        cls.directory = tempfile.TemporaryDirectory(prefix='companion-ui-deadlines-')
        cls.addClassCleanup(cls.directory.cleanup)
        work = Path(cls.directory.name)
        cls.binaries = {}
        for variant in BUILD_VARIANTS:
            source_variant = 'ui-tiny' if variant == 'ui-tiny-cache' else variant
            source = (ROOT / f'examples/companion_radio/{source_variant}/UITask.cpp').read_text(encoding='ascii')
            generated = FIXTURE.read_text(encoding='ascii').replace('@LOOP@', extract_braced(source, 'void UITask::loop()'))
            generated = generated.replace('@ALERT@', '' if variant == 'ui-orig' else
                                          extract_braced(source, 'void UITask::showAlert('))
            generated = generated.replace('@SCREEN@', '' if variant == 'ui-orig' else
                                          extract_braced(source, 'void UITask::setCurrScreen('))
            cpp, binary = work / (variant + '.cpp'), work / variant
            cpp.write_text(generated, encoding='ascii')
            built = subprocess.run([
                compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
                '-DUI_ORIG=' + str(int(variant == 'ui-orig')), '-DUI_HAS_JOYSTICK=0',
                '-DUI_TINY_CACHE=' + str(int(variant == 'ui-tiny-cache')),
                '-DCOMPANION_FEATURE_READER=0', '-DAUTO_OFF_MILLIS=0',
                *([] if variant in ('ui-orig', 'ui-tiny-cache') else ['-DAUTO_SHUTDOWN_MILLIVOLTS=3400']),
                str(cpp), '-o', str(binary)], capture_output=True, text=True, timeout=60)
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)
            cls.binaries[variant] = binary

    def check_scenario(self, scenario, variants=VARIANTS):
        for variant in variants:
            with self.subTest(variant=variant, scenario=scenario):
                checked = subprocess.run([str(self.binaries[variant]), scenario],
                                         capture_output=True, text=True, timeout=10)
                print(f'\n[{variant}; {scenario}]\n' + checked.stdout + checked.stderr, flush=True)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn('PASS: ' + scenario, checked.stdout)

    def test_healthy_refresh_schedule(self):
        self.check_scenario('healthy')

    def test_upper_half_refresh_schedule(self):
        self.check_scenario('upper-half')

    def test_actual_forced_screen_change_in_upper_half(self):
        self.check_scenario('force-upper-half', VARIANTS[1:])

    def test_morse_deferred_refresh_survives_wrap(self):
        self.check_scenario('deferred-across-wrap')

    def test_wrapped_future_deadline_is_not_force_zero(self):
        self.check_scenario('wrapped-future-deadline')

    def test_alert_remains_active_across_wrap(self):
        self.check_scenario('alert-across-wrap', VARIANTS[1:])

    def test_alert_with_zero_expiry_is_active(self):
        self.check_scenario('alert-zero-deadline', VARIANTS[1:])

    def test_expired_alert_never_returns_after_wrap(self):
        self.check_scenario('expired-after-wrap', VARIANTS[1:])

    def test_expired_alert_never_returns_after_halfwrap(self):
        self.check_scenario('expired-after-halfwrap', VARIANTS[1:])

    def test_protective_battery_poll_survives_wrap(self):
        self.check_scenario('battery-deferred-wrap', VARIANTS[1:])

    def test_healthy_and_upper_half_battery_schedule(self):
        for scenario in ('battery-healthy', 'battery-upper-half'):
            self.check_scenario(scenario, VARIANTS[1:])

    def test_upper_half_alert_schedule(self):
        self.check_scenario('alert-upper-half', VARIANTS[1:])

    def test_future_battery_poll_does_not_run_early(self):
        self.check_scenario('battery-wrapped-future', VARIANTS[1:])

    def test_alert_expiry_is_consumed_outside_display_gate(self):
        self.check_scenario('expire-with-display-off', VARIANTS[1:])

    def test_alert_expiry_is_consumed_outside_morse_gate(self):
        self.check_scenario('expire-during-morse', VARIANTS[1:])

    def test_alert_expiry_is_consumed_outside_gesture_gate(self):
        self.check_scenario('expire-during-gesture', ('ui-new',))

    def test_clean_orig_preview_keeps_due_refresh_after_26_days(self):
        self.check_scenario('orig-long-clean-idle', ('ui-orig',))

    def test_display_off_live_loops_keep_refresh_due_after_26_days(self):
        self.check_scenario('display-off-long-idle', ('ui-tiny', 'ui-new', 'ui-tiny-cache'))

    def test_fresh_orig_dirty_event_preserves_one_second_throttle(self):
        self.check_scenario('fresh-dirty-throttle', ('ui-orig',))

    def test_tiny_cached_adc_waits_for_safe_wake_after_26_days(self):
        self.check_scenario('cache-battery-long-idle', ('ui-tiny-cache',))

    def test_tiny_cached_adc_future_zero_and_off_morse_gates(self):
        self.check_scenario('cache-battery-wrapped-future', ('ui-tiny-cache',))


class CompanionUiDeadlineWiringContract(unittest.TestCase):
    def test_begin_types_force_paths_and_ci_keep_deadlines_armed(self):
        for variant in ('ui-orig', 'ui-tiny', 'ui-new'):
            with self.subTest(variant=variant):
                directory = ROOT / 'examples/companion_radio' / variant
                source = (directory / 'UITask.cpp').read_text(encoding='ascii')
                header = (directory / 'UITask.h').read_text(encoding='ascii')
                begin = extract_braced(source, 'void UITask::begin(')
                constructor = extract_braced(header, 'UITask(mesh::MainBoard*')
                self.assertRegex(header, r'\buint32_t\s+_next_refresh\s*;')
                # Construction precedes Arduino begin. It may use zero, but
                # every actual begin/re-begin must re-arm from its fresh clock.
                self.assertRegex(constructor, r'\b_next_refresh\s*=\s*0\s*;')
                self.assertIn('ui_started_at = millis();', begin)
                self.assertLess(begin.index('ui_started_at = millis();'),
                                begin.index('_next_refresh = ui_started_at;'))
                without_constructor = header.replace(constructor, '', 1)
                old_force = r'\b_next_refresh\s*=\s*(?:0|100)[uUlL]*\s*;'
                self.assertNotRegex(source, old_force)
                self.assertNotRegex(without_constructor, old_force)
                if variant == 'ui-orig':
                    # Re-begin does not rely on a constructor-only dirty flag.
                    self.assertIn('clearMsgPreview();', begin)
                    clear = extract_braced(source, 'void UITask::clearMsgPreview(')
                    self.assertIn('_need_refresh = true;', clear)
                else:
                    self.assertRegex(header, r'\buint32_t\s+_alert_expiry\s*;')
                    self.assertRegex(header, r'\buint32_t\s+next_batt_chck\s*;')
                    self.assertIn('next_batt_chck = _next_refresh = ui_started_at;', begin)
                    self.assertIn('_alert[0] = 0;', begin)
                    self.assertIn('_alert_expiry = 0;', begin)
                    for initialization in ('next_batt_chck = _next_refresh = ui_started_at;',
                                           '_alert[0] = 0;', '_alert_expiry = 0;'):
                        self.assertLess(begin.index(initialization),
                                        begin.index('setCurrScreen(splash);'))
                    screen = extract_braced(source, 'void UITask::setCurrScreen(')
                    self.assertIn('_next_refresh = millis();', screen)
                if variant == 'ui-new':
                    for signature in ('bool setTouchDebugEnabled(bool enabled)',
                                      'void inboxModeChanged()'):
                        inline = extract_braced(header, signature)
                        self.assertIn('_next_refresh = millis();', inline)
        main = (ROOT / 'examples/companion_radio/main.cpp').read_text(encoding='ascii')
        self.assertIn('ui_task.begin(', extract_braced(main, 'void setup()'))
        self.assertIn('ui_task.loop();', extract_braced(main, '\nvoid loop()'))
        workflow = (ROOT / '.github/workflows/run-unit-tests.yml').read_text(encoding='ascii')
        for module in ('test_companion_ui_deadlines', 'test_scrolling_status_bar_rollover'):
            self.assertIn('python3 -B test/' + module + '.py -v', workflow)


if __name__ == '__main__':
    unittest.main(verbosity=2)
