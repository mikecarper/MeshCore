"""Compile the portable production chirp math, independent of radio hardware."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class RadioChirpMathTest(unittest.TestCase):
    def test_floor_budgets_rounding_and_profile_order(self):
        compiler=shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'chirps.exe'
            done=subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-I',str(ROOT/'src'),
                str(ROOT/'test/fixtures/radio_profiles/chirp_math.cpp'),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(done.returncode,0,done.stderr)
            done=subprocess.run([str(exe)],capture_output=True,text=True)
            self.assertEqual(done.returncode,0,done.stderr)

if __name__=='__main__':unittest.main()
