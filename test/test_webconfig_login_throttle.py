#!/usr/bin/env python3
"""Execute the WebConfig login throttle and the real /api/login handler."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
CLIENT_KEY = "req->client() ? req->client()->getRemoteAddress() : 0"


class WebConfigLoginThrottleTest(unittest.TestCase):
    def compile_run(self, source, includes=(), expect_rejection=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="meshcore-webconfig-login-") as temporary:
            work = Path(temporary)
            generated = work / "login.cpp"
            generated.write_text(source)
            binary = work / "login"
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", *sanitizers,
                                       "-I" + str(ROOT / "src"),
                                       *("-I" + str(path) for path in includes),
                                       str(generated), "-o", str(binary)],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            if expect_rejection:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Assertion", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def handler_fixture(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "static bool fixedTimeEquals(", "void WebConfigServer::handleLogin("))
        self.assertIn(CLIENT_KEY, methods)
        return (ROOT / "test/fixtures/webconfig_login_handler.cpp").read_text().replace("@METHODS@", methods)

    def json_include(self):
        json_header = next((ROOT / ".pio/libdeps").glob("*/ArduinoJson/src/ArduinoJson.h"), None)
        self.assertIsNotNone(json_header, "native dependency headers are required")
        return json_header.parent

    def test_policy_isolates_clients_and_bounds_all_clients_together(self):
        self.compile_run((ROOT / "test/fixtures/webconfig_login_throttle.cpp").read_text())

    def test_login_handler_locks_only_the_failing_address(self):
        self.compile_run(self.handler_fixture(), (self.json_include(),))

    def test_old_shared_lockout_fails_executable_regression(self):
        # Every client on one key is the old global lockout.
        fixture = self.handler_fixture()
        old = fixture.replace(CLIENT_KEY, "0", 1)
        self.assertNotEqual(old, fixture)
        self.compile_run(old, (self.json_include(),), expect_rejection=True)


if __name__ == "__main__":
    unittest.main()
