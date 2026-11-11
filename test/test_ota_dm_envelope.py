"""Test the unwired DM carrier primitive, including an independent HMAC."""
import hashlib
import hmac
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OtaDmEnvelopeTest(unittest.TestCase):
    def test_authenticated_bounded_fragments(self):
        compiler = os.environ.get('CXX') or shutil.which('c++') or shutil.which('g++')
        if not compiler: self.skipTest('C++ compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='ota-dm-envelope-') as folder:
            binary = Path(folder) / ('test.exe' if os.name == 'nt' else 'test')
            build = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                    '-I'+str(ROOT/'src'), '-I'+str(ROOT/'test/mocks'),
                                    str(ROOT/'test/fixtures/ota_dm_envelope.cpp'), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            lines = result.stdout.splitlines()
            frame = bytes.fromhex(lines[0])
            expected = hmac.new(bytes([0x11])*32, b'MeshCore OTA DM v1\0' + bytes([0x22])*32 +
                                bytes([0x33])*32 + frame[:-16], hashlib.sha256).digest()[:16]
            self.assertEqual(frame[-16:], expected)
            self.assertEqual(lines[1], 'PASS OTA DM envelope and bounded reassembly')


if __name__ == '__main__': unittest.main()
