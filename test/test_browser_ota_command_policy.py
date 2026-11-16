"""Exercise the production browser-only Companion command policy."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BrowserOtaCommandPolicyTest(unittest.TestCase):
    def test_only_ota_commands_are_admitted(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-ota-policy-") as directory:
            binary = Path(directory) / "policy.exe"
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    "-I" + str(ROOT / "src"),
                                    str(ROOT / "test/fixtures/browser_ota_socket/policy.cpp"),
                                    "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS browser OTA", result.stdout)

    def test_policy_is_checked_before_command_dispatch(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        body = source[source.index("void MyMesh::handleCmdFrame(size_t len)"):]
        self.assertLess(body.index("browserOtaRestricted()"), body.index("CMD_DEVICE_QUERY"))
        self.assertIn("mesh::browser_ota::allowed(cmd_frame, len, guarded, target)", body)


if __name__ == "__main__":
    unittest.main()
