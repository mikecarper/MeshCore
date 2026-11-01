"""Exercise WiFi status before a saved-network join attaches HTTP routes."""
from pathlib import Path
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start

ROOT = Path(__file__).resolve().parents[1]


class WebConfigWiFiStatusTest(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def fixture(self, role):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        header = (ROOT / "src/helpers/esp32/WebConfigServer.h").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "static void appendOtaSeederStatus(",
            "bool WebConfigServer::formatWiFiStatus(",
            "bool WebConfigServer::startAutoMode(",
            "void WebConfigServer::requestStop(",
            "void WebConfigServer::attachRoutes(",
            "void WebConfigServer::detachRoutes("))
        role_source = (ROOT / "examples" / role / "MyMesh.cpp").read_text()
        fixture = (ROOT / "test/fixtures/webconfig_wifi_status.cpp").read_text()
        return (fixture.replace("@METHODS@", methods)
                .replace("@IS_RUNNING@", extract_braced(header, "bool isRunning() const"))
                .replace("@ROLE_STATUS@", extract_braced(role_source,
                    "bool MyMesh::getWiFiStatus(")))

    def test_first_join_status_and_teardown_preserve_route_admission_and_companion_fallback(self):
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                fixture = self.fixture(role)
                self.compile_and_run(fixture)
                # Reproduce the reported false-off response using the old
                # formatter, and also catch dropping the runtime at its caller.
                stale_formatter = fixture.replace(
                    "const WebConfigServer* active = runtime ? runtime : _active;",
                    "const WebConfigServer* active = _active;")
                self.assertNotEqual(stale_formatter, fixture)
                with self.assertRaises(AssertionError):
                    self.compile_and_run(stale_formatter)
                stale_caller = fixture.replace("canonicalWiFiLegacyPrefs(),\n                                          _webconfig)",
                                               "canonicalWiFiLegacyPrefs())")
                self.assertNotEqual(stale_caller, fixture)
                with self.assertRaises(AssertionError):
                    self.compile_and_run(stale_caller)


if __name__ == "__main__":
    unittest.main()
