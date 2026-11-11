"""Compile and exercise the production hash-bound OTA install admission."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class OtaInstallHashTest(unittest.TestCase):
    def test_production_store_verification_normalizes_approval_and_checks_payload(self):
        compiler = os.environ.get('CXX') or shutil.which('c++') or shutil.which('g++')
        if not compiler: self.skipTest('C++ compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='ota-verifyhash-') as folder:
            binary = Path(folder) / ('test.exe' if os.name == 'nt' else 'test')
            (Path(folder) / 'Identity.h').write_text((ROOT / 'test/mocks/Identity.h').read_text())
            sources = ['OtaVerify.cpp', 'MotaContainer.cpp', 'MerkleTree.cpp']
            args = [compiler, '-std=c++17', '-O1', '-ffunction-sections', '-fdata-sections',
                    '-I' + folder, '-I' + str(ROOT / 'src'), '-I' + str(ROOT / 'test/mocks'), '-I' + str(ROOT / 'test'),
                    str(ROOT / 'test/fixtures/ota_installhash_verify.cpp'),
                    *[str(ROOT / 'src/helpers/ota' / name) for name in sources],
                    str(ROOT / 'src/Utils.cpp'), '-Wl,--gc-sections', '-o', str(binary)]
            build = subprocess.run(args, capture_output=True, text=True, timeout=60)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS real store verification hashes normalized full package', result.stdout)

    def test_real_dispatch_and_hash_gate(self):
        compiler = os.environ.get('CXX') or shutil.which('c++') or shutil.which('g++')
        if not compiler: self.skipTest('C++ compiler unavailable')
        cli = (ROOT / 'src/helpers/ota/OtaCli.cpp').read_text(encoding='utf-8')
        code = (ROOT / 'test/fixtures/ota_installhash.cpp').read_text(encoding='utf-8')
        replacements = {
            'PARSE_HEX': extract_braced(cli, 'static bool parse_hex_exact('),
            'IS_CMD': extract_braced(cli, 'static bool is_cmd('),
            'INSTALL_HASH': extract_braced(cli, 'if (is_cmd(a, "installhash", &rest))'),
        }
        for key, value in replacements.items(): code = code.replace('@' + key + '@', value)
        with tempfile.TemporaryDirectory(prefix='ota-installhash-') as folder:
            path = Path(folder); source = path / 'test.cpp'; binary = path / ('test.exe' if os.name == 'nt' else 'test')
            source.write_text(code, encoding='utf-8')
            build = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS verified hash is required before one apply', result.stdout)


if __name__ == '__main__': unittest.main()
