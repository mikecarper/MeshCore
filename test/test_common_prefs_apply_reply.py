"""Run real RS-232 apply/save branches with controlled callback outcomes.

The callbacks model application and save results, not filesystem transactions.
Actual post-publication storage failures are exercised separately by
test_common_prefs_uncertain_reply.py.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


def actual_harness(compiler, flags):
    source = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
    # Preprocess the whole real method first: platform conditionals can
    # straddle the closing brace between adjacent else-if commands.
    selected = subprocess.run([
        compiler, "-E", "-P", "-x", "c++", *flags, "-",
    ], input=extract_braced(source, "void CommonCLI::handleSetCmd("),
        capture_output=True, text=True, check=True, timeout=20).stdout
    branches = []
    for method, signature in (
        ("rs232", 'if (strncmp(config, "rs232.enabled ", 14) == 0)'),
        ("bridge", 'if (memcmp(config, "bridge.enabled ", 15) == 0)'),
        ("baud", 'if (memcmp(config, "bridge.baud ", 12) == 0)'),
        ("uart", 'if (memcmp(config, "bridge.uart ", 12) == 0)'),
    ):
        definition = ("void CommonCLI::" + method
                      + "(const char* config, char* reply) {\n"
                      + extract_braced(selected, signature) + "\n}\n")
        if method == "bridge":
            # The strict apply/save bridge.enabled branch is standalone UART
            # only. WITH_MQTT_BRIDGE keeps its independent transport policy.
            definition = "#ifndef WITH_MQTT_BRIDGE\n" + definition + "#endif\n"
        branches.append(definition)
    fixture = (ROOT / "test/fixtures/common_prefs_apply_reply.cpp").read_text(
        encoding="ascii")
    return fixture.replace("@PARSE_ON_OFF@", extract_braced(
        source, "static bool parseOnOffStrict(")).replace(
            "@PRODUCTION_BRANCHES@", "\n".join(branches))


class CommonPrefsApplyReplyTests(unittest.TestCase):
    def test_actual_apply_save_rollback_combinations(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        sanitizer_flags = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                            "-fno-pie", "-no-pie"]
                           if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="common-prefs-apply-reply-") as directory:
            work = Path(directory)
            for mqtt in (False, True):
                with self.subTest(mqtt=mqtt):
                    flags = ["-DWITH_BRIDGE=1", "-DWITH_RS232_BRIDGE=1",
                             "-DWITH_RS232_BRIDGE_UART=1", "-DWITH_RS232_BRIDGE_ALT=1",
                             "-DWITH_RS232_BRIDGE_ALT_UART=2"]
                    if mqtt:
                        flags.append("-DWITH_MQTT_BRIDGE=1")
                    (work / "test.cpp").write_text(actual_harness(compiler, flags),
                                                  encoding="ascii")
                    binary = work / ("test.exe" if os.name == "nt" else "test")
                    built = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        *flags, *sanitizer_flags, "-I" + str(ROOT / "src"),
                        str(work / "test.cpp"), "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True,
                                            text=True, timeout=20)
                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                    cases = 64 if mqtt else 89
                    self.assertIn(
                        f"{cases} actual CommonCLI apply/save/restore cases passed",
                        tested.stdout)


if __name__ == "__main__":
    unittest.main()
