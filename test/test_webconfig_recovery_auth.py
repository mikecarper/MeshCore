#!/usr/bin/env python3
"""Execute actual recovery/login/logout gates with real JSON and fake sockets."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class WebConfigRecoveryAuthTest(unittest.TestCase):
    def compile_run(self, source, expect_rejection=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        json_header = next((ROOT / ".pio/libdeps").glob("*/ArduinoJson/src/ArduinoJson.h"), None)
        self.assertIsNotNone(json_header, "native dependency headers are required")
        with tempfile.TemporaryDirectory(prefix="meshcore-webconfig-auth-") as temporary:
            work = Path(temporary)
            generated = work / "auth.cpp"
            generated.write_text(source)
            binary = work / "auth"
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                                       *sanitizers, "-I" + str(json_header.parent),
                                       str(generated), "-o", str(binary)],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            if expect_rejection:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Assertion", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def fixture(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "static bool fixedTimeEquals(", "bool WebConfigServer::checkAuth(",
            "void WebConfigServer::handleLogin(", "void WebConfigServer::handleLogout("))
        # The actual config POST entry gate must reject a replacement password
        # before parsing or queueing it; exercise its exact production prefix.
        post = extract_braced(source, "void WebConfigServer::handleConfigPost(")
        gate = post[post.index("{") + 1:post.index("  if (req->contentLength()")]
        methods += "\nvoid WebConfigServer::checkConfigPost(AsyncWebServerRequest* req) {" + gate + "\nconfig_accepted=true;\n}"
        return (ROOT / "test/fixtures/webconfig_recovery_auth.cpp").read_text().replace("@METHODS@", methods)

    def test_saved_network_fallback_requires_current_admin_session(self):
        self.compile_run(self.fixture())

    def test_old_interface_only_fallback_fails_executable_regression(self):
        fixture = self.fixture()
        old = fixture.replace("if (_initial_setup || _wifi_ssid[0] == 0) return true;", "return true;", 1)
        self.assertNotEqual(old, fixture)
        self.compile_run(old, expect_rejection=True)

    def test_old_unauthenticated_logout_fails_executable_regression(self):
        fixture = self.fixture()
        signature = "void WebConfigServer::handleLogout("
        start = fixture.index(signature, fixture.index("@METHODS@") if "@METHODS@" in fixture else 0)
        prefix, logout = fixture[:start], fixture[start:]
        gate = '  if (!checkAuth(req)) { req->send(401, "application/json", "{\\"error\\":\\"auth\\"}"); return; }\n'
        old = prefix + logout.replace(gate, "", 1)
        self.assertNotEqual(old, fixture)
        self.compile_run(old, expect_rejection=True)


if __name__ == "__main__":
    unittest.main()
