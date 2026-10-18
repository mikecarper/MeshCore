#!/usr/bin/env python3
"""Exercise production WebConfig AP startup against independent SDK state."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/esp32/WebConfigServer.cpp"
FIXTURE = ROOT / "test/fixtures/webconfig_ap_start.cpp"


class WebConfigApStartTest(unittest.TestCase):
    def test_live_ap_state_and_reusable_handoff(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "A host C++17 compiler is required")
        source = SOURCE.read_text(encoding="utf-8")
        # Keeping helper extraction optional lets the old implementation compile:
        # its false-success behavior must fail executable checks, not compilation.
        helper = ""
        if "static bool setupAccessPointStarted(" in source:
            helper += extract_braced(source, "static bool setupAccessPointStarted(") + "\n"
        if "static bool setupAccessPointReady(" in source:
            helper += extract_braced(source, "static bool setupAccessPointReady(") + "\n"
        if "static bool scanSetupNetworks(" in source:
            helper += extract_braced(source, "static bool scanSetupNetworks(")
        method = extract_braced(source, "void WebConfigServer::initializeManagementPassword(") + "\n" + extract_braced(source, "bool WebConfigServer::startSetupMode(")
        scan_handler = extract_braced(source, "void WebConfigServer::handleScan(")
        policy = (ROOT / "src/helpers/esp32/WiFiRadioPolicy.h").read_text(encoding="utf-8")
        mask = policy[policy.index("static constexpr uint8_t kAccessPointProtocolMask ="):]
        mask = mask[:mask.index(";") + 1]
        owner_policy = policy[policy.index("static constexpr uint8_t kLongRangeRadioOwner ="):]
        owner_policy = owner_policy[:owner_policy.index("// A bridge can start/stop")]
        mask += "\n" + owner_policy
        fixture = FIXTURE.read_text(encoding="utf-8").replace("@AP_MASK@", mask)
        ap_policy = (ROOT / "src/helpers/esp32/WiFiAccessPointPolicy.h").read_text(encoding="utf-8")
        scanner = "namespace mesh { namespace wifi {\n" + "\n".join(
            extract_braced(ap_policy, signature) for signature in (
                "inline bool finishAccessPointScan(", "inline bool scanBeforeAccessPoint(")) + "\n} }\n"
        helper = scanner + helper
        fixture = fixture.replace("@METHODS@", helper + "\n" + method + "\n" + scan_handler)
        with tempfile.TemporaryDirectory(prefix="webconfig-ap-start-") as directory:
            work = Path(directory)
            generated = work / "fixture.cpp"
            generated.write_text(fixture, encoding="utf-8")
            binary = work / "webconfig-ap-start.exe"
            sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                          "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            for arduino_major in (2, 3):
                for password in ("", "runtimepass", "short", "long-é-pass"):
                    with self.subTest(arduino_major=arduino_major, password_kind="protected" if password == "runtimepass" else "legacy"):
                        security_flags = ['-DTEST_ADMIN_PASSWORD="' + password + '"']
                        # This obsolete build secret must never select AP security.
                        security_flags += ['-DWEBCONFIG_AP_PASSWORD="unused-old-key"']
                        compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                            *sanitizer, f"-DESP_ARDUINO_VERSION_MAJOR={arduino_major}",
                            *security_flags, "-I" + str(ROOT / "src"),
                            str(generated), "-o", str(binary)],
                            capture_output=True, text=True, timeout=60)
                        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                        checked = subprocess.run([str(binary)], capture_output=True,
                                                 text=True, timeout=15)
                        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                        self.assertIn("WebConfig AP startup regression checks passed", checked.stdout)


if __name__ == "__main__":
    unittest.main()
