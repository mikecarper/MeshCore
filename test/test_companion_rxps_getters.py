#!/usr/bin/env python3
"""Exercise the production shared RXPS getters and ASCII adapter under sanitizers."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]


class CompanionRxpsGetterTests(unittest.TestCase):
    def test_real_getters_are_bounded_readonly_and_match_active_primary(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="ascii")
        getter = extract_braced(source, "bool MyMesh::handleRxPowerSavingGetCommand(")
        local = extract_braced(source, "bool MyMesh::handleLocalControlCommand(")
        signature = "  if (handleRxPowerSavingGetCommand(command, reply, reply_size)) return true;"
        self.assertIn(signature, local)
        # Keep the real local entry validation/space handling and registration;
        # unrelated setters/transport helpers are not part of this fixture.
        entry = local[:local.index("  if (mesh::powerdiag::handleCommand")]
        entry += signature + "\n  return false;\n}\n"
        terminal = extract_braced(source, "void MyMesh::handleTerminalCommand(")
        ascii_adapter = extract_braced(terminal, "if (handleRxPowerSavingGetCommand(command, local_reply, sizeof(local_reply)))")
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-rxps-get-") as directory:
            work = Path(directory)
            (work / "production_getters.inc").write_text(getter + "\n" + entry, encoding="ascii")
            (work / "production_ascii.inc").write_text(ascii_adapter, encoding="ascii")
            for platform in ("NRF52_PLATFORM", "ESP32_PLATFORM", "STM32_PLATFORM"):
                for temporary in (0, 1):
                    with self.subTest(platform=platform, temporary=temporary):
                        binary = work / (platform + str(temporary))
                        built = subprocess.run([
                            os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                            "-D" + platform + "=1", "-DCOMPANION_FEATURE_TEMP_RADIO=" + str(temporary),
                            "-I", str(ROOT / "src"), "-isystem", str(ROOT / "test/mocks"), "-I", str(work),
                            str(ROOT / "test/fixtures/companion_rxps_getters.cpp"), "-o", str(binary),
                        ], capture_output=True, text=True, timeout=60)
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                        self.assertIn("RXPS getter contracts passed", ran.stdout)

    def test_framed_admin_and_ascii_share_the_same_getter(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="ascii")
        local = extract_braced(source, "bool MyMesh::handleLocalControlCommand(")
        self.assertIn("handleRxPowerSavingGetCommand(command, reply, reply_size)", local)
        shared = extract_braced(source, "bool MyMesh::handleCommand(")
        self.assertIn("handleLocalControlCommand(command, reply, reply_capacity)", shared)
        framed = source[source.index("} else if (mesh::companion::isRunCliFrame("):]
        self.assertIn("handled = handleCommand(text, 0, reply_buf);", framed)
        admin = extract_braced(source, "void MyMesh::execAdminCommand(")
        self.assertIn("if (handleCommand(cmd, 0, reply)) return;", admin)
        self.assertNotIn("get radio.rxps", admin)
        terminal = extract_braced(source, "void MyMesh::handleTerminalCommand(")
        self.assertLess(terminal.index("handleRxPowerSavingGetCommand("), terminal.index("handleLocalControlCommand("))
        self.assertNotIn("recalcRxPowerSavingFromLevel", terminal)
        helper = extract_braced(source, "bool MyMesh::handleRxPowerSavingGetCommand(")
        self.assertNotIn("savePrefs", helper)
        self.assertNotIn("setRxPowerSaving", helper)
        self.assertNotIn("ota_", helper)
        self.assertNotIn("_prefs.rx_ps_rx_us =", helper)


if __name__ == "__main__":
    unittest.main()
