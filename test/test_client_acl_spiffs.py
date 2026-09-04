#!/usr/bin/env python3
"""Run real ClientACL persistence against ESP32's missing-file directory quirk."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/client_acl_spiffs"


class ClientAclSpiffsTest(unittest.TestCase):
    def test_real_acl_with_spiffs_file_semantics(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix=".tmp-client-acl-", dir=ROOT) as directory:
            binary = Path(directory) / "client-acl-spiffs.exe"
            compiled = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-DESP32=1",
                "-DESP32_PLATFORM=1", f"-I{FIXTURE / 'mocks'}", f"-I{ROOT / 'src'}",
                str(FIXTURE / "test_client_acl_spiffs.cpp"), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("24 ClientACL SPIFFS checks passed", checked.stdout)
            self.assertEqual(checked.stdout.count("PASS:"), 24)


if __name__ == "__main__":
    unittest.main()
