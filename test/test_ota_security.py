#!/usr/bin/env python3
"""Run real OTA ingress/egress policy with real Ed25519 and bounded resources."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OtaSecurityTests(unittest.TestCase):
    def test_manifest_authentication_cooldown_catalog_and_paced_metadata(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="ota-security-") as directory:
            binary = Path(directory) / "security"
            sources = ("OtaManager.cpp", "OtaProtocol.cpp", "MotaContainer.cpp", "MerkleTree.cpp")
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-pie", "-no-pie"]
            compiled = subprocess.run([
                compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-sign-compare", *flags,
                "-I", str(ROOT / "test/fixtures/ota_security"),
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                "-I", str(ROOT / "test"),
                str(ROOT / "test/fixtures/ota_security/test.cpp"),
                str(ROOT / "test/fixtures/ota_security/identity_verify.cpp"),
                *[str(ROOT / "src/helpers/ota" / name) for name in sources],
                str(ROOT / "src/Utils.cpp"), "-lcrypto", "-Wl,--wrap=malloc",
                "-Wl,--wrap=free", "-o", str(binary),
            ], capture_output=True, text=True, timeout=90)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("PASS: authenticated automatic staging", checked.stdout)


if __name__ == "__main__":
    unittest.main()
