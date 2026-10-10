#!/usr/bin/env python3
"""Execute production custom-broker setup; no CA must mean no connection."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class MqttTlsSecurityTest(unittest.TestCase):
    def compile_run(self, source, portable=False, expect_rejection=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-tls-security-") as temporary:
            work = Path(temporary)
            generated = work / "security.cpp"
            generated.write_text(source)
            binary = work / "security"
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", *sanitizers,
                                       *(["-DPORTABLE_MQTT_OBSERVER=1"] if portable else []),
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
        source = (ROOT / "src/helpers/bridges/MQTTBridge.cpp").read_text()
        setup = extract_braced(source, "bool MQTTBridge::setupSlot(")
        start = setup.index("    // Custom broker slot - build persistent URI")
        # This is the entire production custom setup branch and the final
        # connection attempt, including both real build preprocessor paths.
        custom = "bool MQTTBridge::setupCustom(int index) {\nMQTTSlot& slot=_slots[index];\n{\n" + setup[start:]
        return (ROOT / "test/fixtures/mqtt_tls_security.cpp").read_text().replace("@METHOD@", custom)

    def test_custom_tls_refuses_missing_ca_before_credentials_or_connect(self):
        self.compile_run(self.fixture())

    def test_portable_custom_tls_refuses_missing_ca_but_plaintext_policy_is_preserved(self):
        self.compile_run(self.fixture(), portable=True)

    def test_old_no_bundle_fallback_fails_executable_regression(self):
        fixture = self.fixture()
        old = fixture.replace("          slot.initial_connect_done = false;\n"
                              "          slot.last_reconnect_attempt = millis();\n"
                              "          return false;\n", "", 1)
        self.assertNotEqual(old, fixture)
        self.compile_run(old, expect_rejection=True)

    def test_old_portable_fallback_fails_executable_regression(self):
        fixture = self.fixture()
        old = re.sub(r'    if \(needs_tls\) \{\n      MQTT_DEBUG_PRINTLN\("MQTT%d TLS refused: custom brokers require a CA bundle"[\s\S]*?      return false;\n    }',
                     "    (void)needs_tls;", fixture, count=1)
        self.assertNotEqual(old, fixture)
        self.compile_run(old, portable=True, expect_rejection=True)


if __name__ == "__main__":
    unittest.main()
