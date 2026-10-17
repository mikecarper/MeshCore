"""Resolve tracked GPS parser hooks, including real Full target synthesis.

Only PlatformIO's configuration reader and the builder's JSON target mapper
run here. No PlatformIO CLI, dependency download, compilation or flashing.
Developer-local overlays are deliberately excluded from the tracked matrix.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from platformio.project.config import ProjectConfig

ROOT = Path(__file__).resolve().parents[1]
HOOK = 'pre:scripts/micronmea_time_fix.py'


class MicroNmeaTimeFixRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = ProjectConfig(str(ROOT / 'platformio.ini'), parse_extra=False)
        for path in sorted((ROOT / 'variants').glob('*/platformio.ini')):
            cls.config.read(str(path), parse_extra=False)

    def has_micronmea(self, section):
        return any('MicroNMEA' in dependency
                   for dependency in self.config.get(section, 'lib_deps', []))

    def test_every_resolved_micronmea_recipe_loads_exactly_one_fix(self):
        tested = []
        for section in self.config.sections():
            if not section.startswith('env:') or not self.has_micronmea(section):
                continue
            with self.subTest(target=section):
                self.assertEqual(self.config.get(section, 'extra_scripts', []).count(HOOK), 1)
                tested.append(section)
        self.assertGreater(len(tested), 500)
        for target in ('native', 'RAK_4631_sensor', 't1000e_sensor', 'heltec_v4_sensor',
                       't1000e_companion_radio_usb', 'heltec_v4_r8_tft_companion_radio_ble'):
            self.assertIn('env:' + target, tested)

    def test_all_four_platform_bases_and_native_propagate_exactly_one_fix(self):
        for section in ('esp32_base', 'nrf52_base', 'rp2040_base', 'stm32_base', 'env:native'):
            with self.subTest(section=section):
                self.assertEqual(self.config.get(section, 'extra_scripts', []).count(HOOK), 1)

    def test_native_compiles_the_reviewed_real_parser(self):
        self.assertIn('stevemarple/MicroNMEA @ 2.0.6',
                      self.config.get('env:native', 'lib_deps'))
        self.assertEqual(self.config.get('env:native', 'lib_compat_mode'), 'off')

    def test_actual_builder_synthesized_full_targets_retain_their_base_hook(self):
        options = ['build_flags', 'lib_deps', 'build_src_filter', 'board', 'extra_scripts']
        records = [[section, [[key, self.config.get(section, key, [])] for key in options]]
                   for section in self.config.sections() if section.startswith('env:')]
        shell = r'''
source "$1/build_legacy.sh"
pio() { printf 'FORBIDDEN_PLATFORMIO\n' >&2; return 99; }
platformio() { printf 'FORBIDDEN_PLATFORMIO\n' >&2; return 99; }
ALL_PIO_ENVS=(fixture)
PIO_CONFIG_JSON=$(<"$2")
init_project_context
for target in "${SUPPORTED_PIO_ENVS[@]}"; do
  if is_companion_radio_full_target "$target"; then
    printf '%s\t%s\n' "$target" "${PIO_ENV_BUILD_BASE_BY_NAME[$target]:-$target}"
  fi
done
'''
        with tempfile.TemporaryDirectory(prefix='micronmea-recipes-') as temporary:
            path = Path(temporary) / 'config.json'
            path.write_text(json.dumps(records, ensure_ascii=True), encoding='ascii')
            result = subprocess.run([
                'bash', '-c', shell, 'micronmea-recipes', str(ROOT), str(path)],
                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('FORBIDDEN_PLATFORMIO', result.stdout + result.stderr)
        pairs = dict(line.split('\t') for line in result.stdout.splitlines())
        self.assertIn('t1000e_companion_radio_full', pairs)
        self.assertIn('heltec_v4_r8_tft_companion_radio_full', pairs)
        tested = []
        for target, base in pairs.items():
            section = 'env:' + base
            if not self.has_micronmea(section):
                continue
            with self.subTest(target=target, base=base):
                self.assertEqual(self.config.get(section, 'extra_scripts', []).count(HOOK), 1)
                tested.append(target)
        self.assertGreater(len(tested), 70)

    def test_regression_suites_are_required_in_ci(self):
        workflow = (ROOT / '.github/workflows/run-unit-tests.yml').read_text(encoding='ascii')
        self.assertIn('python3 -B test/test_micronmea_time_fix.py -v', workflow)
        self.assertIn('python3 -B test/test_micronmea_time_fix_recipes.py -v', workflow)


if __name__ == '__main__':
    unittest.main()
