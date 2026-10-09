"""Run the production shared profile command parser and persistence with a memory FS."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class RadioProfileCLITest(unittest.TestCase):
    def test_commands_persistence_and_timers(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as work:
            # Compact STM32 images omit fleet staging. Both paths must preserve
            # local schedules, persistence retries and temporary lease bounds.
            for fleet_enabled in (False, True):
                exe = Path(work) / f'profiles-{int(fleet_enabled)}.exe'
                subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra',
                    f'-DMESH_ENABLE_FLEET_CONTROL={int(fleet_enabled)}',
                    '-I', str(ROOT/'test/fixtures/radio_profiles/mocks'),
                    '-I', str(ROOT/'test/mocks'), '-I', str(ROOT/'src'),
                    str(ROOT/'src/helpers/RadioProfileCLI.cpp'),
                    str(ROOT/'test/fixtures/radio_profiles/cli_test.cpp'),
                    '-o', str(exe)], check=True, capture_output=True, text=True)
                for scenario in (None, 'permanent_order', 'temporary_boundary', 'lease_creation',
                                 'save_failure', 'schedule_permutations', 'storage_isolation',
                                 'relative_schedule'):
                    with self.subTest(fleet=fleet_enabled, scenario=scenario or 'commands_persistence_and_timers'):
                        subprocess.run([str(exe)] + ([scenario] if scenario else []), check=True)

if __name__ == '__main__':
    unittest.main()
