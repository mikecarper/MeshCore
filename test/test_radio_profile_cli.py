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
            exe = Path(work) / 'profiles.exe'
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra',
                '-I', str(ROOT/'test/fixtures/radio_profiles/mocks'),
                '-I', str(ROOT/'test/mocks'), '-I', str(ROOT/'src'),
                str(ROOT/'src/helpers/RadioProfileCLI.cpp'),
                str(ROOT/'test/fixtures/radio_profiles/cli_test.cpp'),
                '-o', str(exe)], check=True, capture_output=True, text=True)
            subprocess.run([str(exe)], check=True)

if __name__ == '__main__':
    unittest.main()
