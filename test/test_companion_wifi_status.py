#!/usr/bin/env python3

from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CompanionWiFiStatusTest(unittest.TestCase):
    def test_runtime_fallback_classification(self):
        source = r'''
#include <helpers/CompanionWiFiStatus.h>

using mesh::wifi::CompanionWiFiFallbackState;
using mesh::wifi::classifyCompanionWiFiFallback;

int main() {
  if (classifyCompanionWiFiFallback({false, false, false}) !=
      CompanionWiFiFallbackState::OffDisabled) return 1;
  if (classifyCompanionWiFiFallback({true, false, false}) !=
      CompanionWiFiFallbackState::Inactive) return 2;
  if (classifyCompanionWiFiFallback({true, true, true}) !=
      CompanionWiFiFallbackState::ReconnectScheduled) return 3;
  if (classifyCompanionWiFiFallback({true, true, false}) !=
      CompanionWiFiFallbackState::ConnectingOrRetrying) return 4;
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "companion_wifi_status"
            compiled = subprocess.run(
                [
                    "c++",
                    "-std=c++11",
                    f"-I{ROOT / 'src'}",
                    "-x",
                    "c++",
                    "-",
                    "-o",
                    str(executable),
                ],
                input=source,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_wifi_status_command_uses_runtime_formatter(self):
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        local_start = mesh.index("bool MyMesh::handleLocalControlCommand(")
        local_end = mesh.index(
            "\n#if COMPANION_FEATURE_TEMP_RADIO\nvoid MyMesh::serviceTempRadio()",
            local_start,
        )
        local = mesh[local_start:local_end]
        status_case = re.search(
            r"case mesh::cli::StandaloneWiFiKey::Status:(.*?)return true;",
            local,
            re.DOTALL,
        )
        self.assertIsNotNone(status_case)
        self.assertIn(
            "formatCompanionWiFiStatus(reply, reply_size);",
            status_case.group(1),
        )
        self.assertNotIn(
            "WebConfigServer::formatWiFiStatus", status_case.group(1)
        )

    def test_active_runtime_fallback_is_not_off(self):
        server = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        start = server.index("bool WebConfigServer::formatWiFiStatus(")
        end = server.index("\nbool WebConfigServer::startSetupMode", start)
        formatter = server[start:end]

        self.assertIn("classifyCompanionWiFiFallback", formatter)
        self.assertIn(
            '> off, WiFi disabled; configured SSID: %s', formatter
        )
        self.assertIn(
            '> inactive, WiFi enabled but services stopped;', formatter
        )
        self.assertIn('> reconnect scheduled, SSID: %s', formatter)
        self.assertIn('> connecting/retrying, SSID: %s', formatter)
        # Existing rich setup/connecting and connected reports retain priority.
        self.assertLess(
            formatter.index("active->_mode == MODE_SETUP"),
            formatter.index("classifyCompanionWiFiFallback"),
        )
        self.assertLess(
            formatter.index("if (status == WL_CONNECTED)"),
            formatter.index("classifyCompanionWiFiFallback"),
        )


if __name__ == "__main__":
    unittest.main()
