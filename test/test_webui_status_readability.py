"""Exercise live Web UI state independently from the saved boot switch."""
from pathlib import Path
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start

ROOT = Path(__file__).resolve().parents[1]


class WebUIStatusReadabilityTest(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def fixture(self, role):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        role_source = (ROOT / "examples" / role / "MyMesh.cpp").read_text()
        header = (ROOT / "src/helpers/esp32/WebConfigServer.h").read_text()
        getters = "\n".join(extract_braced(header, signature) for signature in (
            "bool isStartPending() const", "Mode mode() const",
            "bool isRunning() const", "bool isStopping() const"))
        if role == "companion_radio":
            block = extract_braced(role_source, '  if (strcmp(command, "get webui") == 0)')
            role_status = ("bool MyMesh::getWebUIStatus(char* reply) const {\n"
                           '  const char* command = "get webui";\n'
                           "  const size_t reply_size = 160;\n" + block +
                           "\n  return false;\n}")
            role_start = extract_braced(role_source, "bool MyMesh::startWebConfig(")
            default_enabled = "true"
        else:
            role_status = extract_braced(role_source, "bool MyMesh::getWebUIStatus(")
            start_method = extract_braced(role_source, "bool MyMesh::startWebConfigImpl(")
            block = extract_braced(start_method, "  if (_webconfig && _webconfig->isRunning())")
            role_start = ("bool MyMesh::startWebConfig(bool, char* reply) {\n"
                          "  const bool automatic_setup = false;\n" + block +
                          "\n  return false;\n}")
            default_enabled = "false"
        fixture = (ROOT / "test/fixtures/webui_status_readability.cpp").read_text()
        return (fixture.replace("@METHOD@", extract_braced(source,
                    "bool WebConfigServer::formatWebUIStatus("))
                .replace("@GETTERS@", getters)
                .replace("@ROLE_STATUS@", role_status)
                .replace("@ROLE_START@", role_start)
                .replace("@DEFAULT_ENABLED@", default_enabled))

    def test_live_states_and_repeated_starts_across_all_roles(self):
        for role in ("simple_repeater", "simple_room_server", "companion_radio"):
            with self.subTest(role=role):
                self.compile_and_run(self.fixture(role))


if __name__ == "__main__":
    unittest.main()
