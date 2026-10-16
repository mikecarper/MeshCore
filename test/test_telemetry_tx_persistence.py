#!/usr/bin/env python3
"""Actual repeater prefs/migration/writers with injected filesystem failures."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/telemetry_tx_persistence"


class TelemetryPersistenceTests(unittest.TestCase):
    def test_real_persistence_recovery_and_scheduling_on_each_filesystem(self):
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="ascii")
        common = (ROOT / "src/helpers/CommonCLI_Management.cpp").read_text(encoding="ascii")
        packet = (ROOT / "src/Packet.cpp").read_text(encoding="ascii")
        utils = (ROOT / "src/Utils.cpp").read_text(encoding="ascii")
        state = common[common.index("static constexpr char DATA_ROUTE_FILE[]"):
                       common.index("static uint32_t readRoute32(")]
        generated = "namespace mesh {\n" + state + "\n"
        generated += "\n".join(extract_braced(packet, signature) for signature in (
            "bool Packet::isValidPathLen(", "size_t Packet::writePath(",
            "uint8_t Packet::copyPath(")) + "\n"
        generated += 'static const char hex_chars[] = "0123456789ABCDEF";\n'
        generated += extract_braced(utils, "void Utils::toHex(") + "\n"
        generated += "\n".join(extract_braced(common, signature) for signature in (
            "static uint32_t readRoute32(", "static void writeRoute32(",
            "static uint8_t nibble(", "static char* trimDataRoute(",
            "static bool parseDataPath(", "static void formatDataPath(",
            "static bool dataRouteSave(", "static bool dataRouteLoad(")) + "\n}\n"
        generated += "\n".join(extract_braced(common, signature) for signature in (
            "bool CommonCLI::adoptLegacyDataTxPath(", "bool CommonCLI::getDataTxPath(",
            "bool CommonCLI::handleDataTxCommand(")) + "\n"
        generated += '#define TELEMETRY_HISTORY_TX_PREFS_FILE "/telemetry_tx"\n'
        for name in ("RETRY_MILLIS", "PACKET_SPACING_MILLIS", "DEFAULT_DAYS", "MAX_DAYS",
                     "TEMPERATURE", "VOLTAGE", "EXTERNAL_VOLTAGE"):
            generated += re.search(r"static const uint(?:8|64)_t TELEMETRY_HISTORY_TX_" +
                                   name + r"[^;]*;", source).group() + "\n"
        generated += "\n".join(extract_braced(source, signature) for signature in (
            "static File openFloodSettingsRead(", "static void formatPathReply(",
            "static bool recoverTelemetryHistoryTxPrefs(",
            "static bool readTelemetryHistoryTxPrefsImage(", "void MyMesh::loadTelemetryHistoryTxPrefs(",
            "bool MyMesh::saveTelemetryHistoryTxPrefs(", "bool MyMesh::sendTelemetryHistorySnapshot(",
            "bool MyMesh::sendExternalVoltageHistorySnapshot(", "void MyMesh::serviceTelemetryHistoryTx(",
            "void MyMesh::formatTelemetryHistoryTxStatus(", "static char* trimSpaces(")) + "\n"
        generated += ('void MyMesh::handle(char* command, char* reply, ClientInfo* sender) {\n'
                      '  static const char telemetry_tx_schedule_command[] = "set telemetry.tx schedule";\n'
                      '  static const char telemetry_tx_send_now_command[] = "send telemetry.tx now";\n')
        generated += extract_braced(source, "if (strcmp(command, telemetry_tx_send_now_command)") + "\n"
        generated += extract_braced(source, "if (strncmp(command, telemetry_tx_schedule_command,")
        generated += '\n  strcpy(reply, "Err - unrecognized command");\n}\n'
        sanitizers = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                                               "-fno-sanitize-recover=all", "-fno-omit-frame-pointer",
                                               "-fno-pie", "-no-pie"]
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            (work / "production.inc").write_text(generated, encoding="ascii")
            # Copy the real helpers so their local hardware header resolves to
            # this filesystem boundary rather than the Arduino implementation.
            for name in ("AtomicFileWriter.h", "ContactFileTransaction.h", "FileRead.h",
                         "FilePresence.h", "PersistentStoreFormat.h"):
                shutil.copyfile(ROOT / "src/helpers" / name, work / name)
            shutil.copyfile(FIXTURE / "IdentityStore.h", work / "IdentityStore.h")
            shutil.copyfile(FIXTURE / "InternalFileSystem.h", work / "InternalFileSystem.h")
            for platform in ("ESP32_PLATFORM", "RP2040_PLATFORM", "NRF52_PLATFORM", "STM32_PLATFORM"):
                with self.subTest(platform=platform):
                    executable = work / platform
                    compiled = subprocess.run([
                        shutil.which("g++") or "g++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra",
                        *sanitizers, "-D" + platform, "-I", str(work), "-I", str(ROOT / "src"),
                        str(FIXTURE / "fixture.cpp"), "-o", str(executable)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    executed = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
                    self.assertIn("telemetry persistence checks passed", executed.stdout)


if __name__ == "__main__":
    unittest.main()
