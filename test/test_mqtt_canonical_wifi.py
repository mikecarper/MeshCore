#!/usr/bin/env python3
"""Execute the production NVS writer/resolver, CLI setters and MQTT consumer."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class FixtureFailure(AssertionError):
    pass


class MQTTCanonicalWiFiTest(unittest.TestCase):
    def fixture(self):
        web = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        mqtt = (ROOT / "src/helpers/bridges/MQTTBridge.cpp").read_text()
        fixture = (ROOT / "test/fixtures/mqtt_canonical_wifi.cpp").read_text()
        web_policy = "\n".join(extract_braced(web, signature) for signature in (
            "static bool bluetoothWiFiCoexistenceRequired(",
            "static uint8_t effectiveWiFiPowerSave(",
        ))
        web_methods = "\n".join(extract_braced(web, signature) for signature in (
            "mesh::wifi::CredentialState WebConfigServer::resolveWiFi(",
            "bool WebConfigServer::hasConfiguredWiFi(",
            "bool WebConfigServer::loadStandaloneWiFi(",
            "bool WebConfigServer::saveStandaloneWiFi(",
            "static bool loadWiFiEdit(",
            "bool WebConfigServer::setStandaloneWiFiSSID(",
            "bool WebConfigServer::setStandaloneWiFiPassword(",
            "bool WebConfigServer::setStandaloneWiFiPowerSave(",
            "bool WebConfigServer::formatWiFiPassword(",
            "bool WebConfigServer::formatWiFiPowerSave(",
            "bool WebConfigServer::reloadStandaloneWiFi(",
        ))
        batch_start = web.index('      if (_standalone_wifi && strcmp(e.key, "wifi.ssid")')
        batch_end = web.index("      _cb->execCommand(e.cmd, e.reply);", batch_start)
        credential_batch = web[batch_start:batch_end]
        commit_batch = extract_braced(web, "  if (_standalone_wifi_dirty)")
        observer = (ROOT / "src/helpers/CommonCLI_Observer.cpp").read_text()
        cli_set = extract_braced(observer[observer.index("bool CommonCLI::handleObserverSetCmd"):],
                                 "  if (_callbacks->usesCanonicalWiFi())")
        cli_get = extract_braced(observer[observer.index("bool CommonCLI::handleObserverGetCmd"):],
                                 "  if (_callbacks->usesCanonicalWiFi())")
        mqtt_methods = "\n".join(extract_braced(mqtt, signature) for signature in (
            "static bool mqttStationWiFiMutationAllowed(",
            "bool MQTTBridge::prepareWiFiCredentials(",
            "void MQTTBridge::beginWiFiStation(",
            "static bool isWiFiConfigValid(",
            "static bool customEndpointComplete(",
            "bool MQTTBridge::isConfigValid(",
            "bool MQTTBridge::isReady(",
        ))
        policy = (ROOT / "src/helpers/esp32/WiFiRadioPolicy.h").read_text(encoding="ascii")
        mqtt_methods = ("namespace mesh { namespace wifi {\n" + extract_braced(
            policy, "inline bool stationMutationAllowed(") + "\n}}\n" + mqtt_methods)
        begin = mqtt.index("  // Check if WiFi credentials are configured first")
        begin_end = mqtt.index("  // These are begin()/end()-scoped", begin)
        # These are the exact production early gate and the reconnect call;
        # expensive task/TLS allocation below the gate is a hardware endpoint.
        gate = mqtt[begin:begin_end]
        reconnect_start = mqtt.index("        WiFi.disconnect();", mqtt.index("bool MQTTBridge::handleWiFiConnection"))
        reconnect_start = mqtt.index("#ifdef ESP_PLATFORM", reconnect_start)
        reconnect_end = mqtt.index("#endif", reconnect_start) + len("#endif")
        reconnect = mqtt[reconnect_start:reconnect_end]
        return (fixture.replace("@WEB_POLICY@", web_policy)
                .replace("@WEB_METHODS@", web_methods)
                .replace("@MQTT_METHODS@", mqtt_methods)
                .replace("@BEGIN_GATE@", gate).replace("@RECONNECT@", reconnect)
                .replace("@CLI_SET@", cli_set).replace("@CLI_GET@", cli_get)
                .replace("@CREDENTIAL_BATCH@", credential_batch)
                .replace("@COMMIT_BATCH@", commit_batch))

    def compile(self, source, *flags):
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="mqtt-canonical-wifi-") as directory:
            path = Path(directory)
            (path / "Preferences.h").write_text(
                (ROOT / "test/fixtures/wifi_credentials_preferences.h").read_text(), encoding="ascii")
            (path / "test.cpp").write_text(source, encoding="ascii")
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DESP_PLATFORM=1", "-DESP32=1", "-DWITH_MQTT_BRIDGE=1", *flags,
                "-I", str(path), "-I", str(ROOT / "src"), str(path / "test.cpp"),
                "-o", str(path / "test")], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(path / "test")], capture_output=True,
                                    text=True, timeout=15)
            if result.returncode:
                raise FixtureFailure(result.stdout + result.stderr)

    def test_portal_cli_upgrade_reconnect_boundaries_and_write_failures(self):
        self.compile(self.fixture())

    def test_companion_compiled_password_fallback_is_preserved(self):
        self.compile(self.fixture(), '-DWIFI_PWD="compiled-password"')

    def test_pre_fix_mqtt_gate_rejects_fresh_portal_credentials(self):
        fixture = self.fixture()
        stale = fixture.replace("const bool wifi_configured = prepareWiFiCredentials();",
                                "const bool wifi_configured = isWiFiConfigValid(_obs, false);")
        self.assertNotEqual(stale, fixture)
        with self.assertRaises(FixtureFailure):
            self.compile(stale)

    def test_reconnect_cannot_revert_to_legacy_credentials(self):
        fixture = self.fixture()
        stale = fixture.replace("mesh::wifi::beginStation(_wifi_ssid, _wifi_password);",
                                "mesh::wifi::beginStation(_obs->wifi_ssid, _obs->wifi_password);")
        self.assertNotEqual(stale, fixture)
        with self.assertRaises(FixtureFailure):
            self.compile(stale)


if __name__ == "__main__":
    unittest.main()
