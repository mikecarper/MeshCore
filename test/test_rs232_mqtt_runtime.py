#!/usr/bin/env python3
"""Execute real UART/MQTT/ESP-NOW repeater lifecycle and CLI independently."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


def ota_resume_fields():
    header = (ROOT / "src/helpers/CommonCLI.h").read_text(encoding="ascii")
    start = header.index("  bool _wifi_ota_resume_mqtt = false;")
    return header[start:header.index("#endif", start)]


class RS232MQTTRuntimeTests(unittest.TestCase):
    def test_independent_transports_route_retry_restart_and_survive_browser_ota(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        observer = (ROOT / "src/helpers/CommonCLI_Observer.cpp").read_text(encoding="ascii")
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text(encoding="ascii")
        implementation = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="ascii")
        flags = ["-DWITH_RS232_BRIDGE=Serial2", "-DWITH_RS232_BRIDGE_UART=2",
                 "-DWITH_RS232_BRIDGE_RX=5", "-DWITH_RS232_BRIDGE_TX=6",
                 "-DRS232_BRIDGE_MERGED=1", "-DWITH_MQTT_BRIDGE=1", "-DWITH_ESPNOW_BRIDGE=1", "-DWITH_BRIDGE=1",
                 "-DENV_INCLUDE_GPS=1", "-DLIGHTWEIGHT_WIFI_OTA=1", "-DMESH_ENABLE_FLOOD_RULE_ENGINE=1"]
        def preprocess(source, extra=()):
            return subprocess.run([compiler, "-E", "-P", "-x", "c++", *flags, *extra, "-"],
                input=source, capture_output=True, text=True, check=True).stdout
        processed_header = preprocess(re.sub(r'^\s*#include[^\n]*', '', header, flags=re.M))
        lifecycle = "\n".join(extract_braced(processed_header, signature) for signature in (
            "AbstractBridge* activeBridge() {", "const AbstractBridge* activeBridge() const {",
            "RS232Bridge* createRS232Bridge()", "bool beginRS232Bridge()", "bool endRS232Bridge()",
            "void serviceRs232BridgePower()", "void formatRs232Status(char* reply)",
            "bool isBridgeRunning() const override", "bool rs232BridgeEnabled() const", "bool isRs232BridgeRunning()",
            "bool setRs232BridgeState(bool enable)", "bool restartRs232Bridge()",
            "bool startSharedEspNowBridgeIfReady()", "bool isEspNowBridgeRunning()",
            "bool setEspNowBridgeState(bool enable)", "bool restartEspNowBridge()",
            "bool isMqttBridgeRunning()", "bool requestMqttBridgeStop()", "bool isMqttBridgeStopping()",
            "bool setMqttBridgeState(bool enable)",
            "bool restartMqttBridge()", "bool setBridgeState(bool enable)", "bool restartBridge()",
        ))
        defaults_start = implementation.index("// bridge defaults")
        defaults_end = implementation.index("// GPS defaults", defaults_start)
        boot_start = implementation.index('mesh::hilStartupTrace("mesh_bridge_begin");')
        boot_start = implementation.index("#if defined(WITH_BRIDGE)", boot_start)
        boot_end = implementation.index('mesh::hilStartupTrace("mesh_bridge_ready");', boot_start)
        routing = {}
        for name in ("logRx", "logTx"):
            start = implementation.index("void MyMesh::" + name + "(")
            start = implementation.index("#ifdef WITH_MQTT_BRIDGE\n", start)
            # Keep the complete bridge guards, stopping before file logging's guard.
            end = implementation.index("#if MESH_ENABLE_PACKET_FILE_LOGGING\n", start)
            routing[name] = implementation[start:end]
        service_start = implementation.index("#if defined(WITH_MQTT_BRIDGE)\n  // MQTT owns TLS",
                                             implementation.index("MyMesh::servicePostMeshLoop()"))
        service_end = implementation.index("  if (next_flood_advert", service_start)
        pending_start = implementation.index("#if defined(WITH_BRIDGE)",
                                             implementation.index("mesh::powerdiag::Mask MyMesh::pendingWorkReason()"))
        pending_end = implementation.index("  if (radio_driver.isWatchdogObserving()", pending_start)
        commands = preprocess("\n".join(extract_braced(cli, signature) for signature in (
            "void CommonCLI::handleSetCmd(", "void CommonCLI::handleGetCmd(")))
        sets = "\nelse ".join(extract_braced(commands, signature) for signature in (
            'if (strncmp(config, "mqtt.enabled ", 13) == 0)',
            'if (strncmp(config, "rs232.enabled ", 14) == 0)',
            'if (memcmp(config, "espnow.enabled ", 15) == 0)',
            'if (memcmp(config, "bridge.enabled ", 15) == 0)',
            'if (memcmp(config, "bridge.baud ", 12) == 0)',
            'if (memcmp(config, "bridge.uart ", 12) == 0)',
            'if (memcmp(config, "bridge.channel ", 15) == 0)',
            'if (memcmp(config, "bridge.secret ", 14) == 0)',
            'if (memcmp(config, "bridge.format ", 14) == 0)',
        ))
        gets = "\nelse ".join(extract_braced(commands, signature) for signature in (
            'if (configKeyEquals(config, "bridge.type"))',
            'if (configKeyEquals(config, "rs232.enabled"))',
            'if (configKeyEquals(config, "rs232.running"))',
            'if (configKeyEquals(config, "bridge.enabled"))',
            'if (configKeyEquals(config, "bridge.running"))',
            'if (configKeyEquals(config, "espnow.enabled"))',
            'if (configKeyEquals(config, "espnow.running"))',
            'if (configKeyEquals(config, "bridge.baud"))',
            'if (configKeyEquals(config, "bridge.uart"))',
        ))
        gets += "\nelse " + "\nelse ".join(extract_braced(observer, signature) for signature in (
            'if (strcmp(config, "mqtt.enabled") == 0)', 'if (strcmp(config, "mqtt.running") == 0)',
            'if (strcmp(config, "mqtt.stopping") == 0)'))
        ota_start = cli.index('    } else if (memcmp(command, "start ota", 9)')
        ota_end = cli.index('    } else if (memcmp(command, "clock", 5)', ota_start)
        code = (ROOT / "test/fixtures/rs232_mqtt_runtime.cpp").read_text(encoding="ascii")
        for key, value in {
            "LIFECYCLE": lifecycle, "DEFAULTS": implementation[defaults_start:defaults_end],
            "FILTER": extract_braced(header, "void configureBridgeFilter(AbstractBridge* active_bridge)"),
            "BOOT": implementation[boot_start:boot_end], "ROUTE_RX": routing["logRx"],
            "ROUTE_TX": routing["logTx"], "SERVICE": implementation[service_start:service_end],
            "PENDING": implementation[pending_start:pending_end], "SET_BRANCHES": sets,
            "GET_BRANCHES": gets, "OTA": cli[ota_start:ota_end],
            "OTA_STATE": ota_resume_fields(),
            "PARSERS": "\n".join(extract_braced(cli, signature) for signature in (
                "static bool parseOnOffStrict(", "static bool configKeyEquals(")),
        }.items():
            code = code.replace("@" + key + "@", value)
        with tempfile.TemporaryDirectory(prefix="rs232-mqtt-runtime-") as temporary:
            work = Path(temporary)
            source, binary = work / "main.cpp", work / "test"
            source.write_text(code, encoding="ascii")
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                *flags, "-I" + str(ROOT / "src"), str(source), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Three transport runtime checks passed", result.stdout)

    def test_uart_and_mqtt_without_espnow_preserve_primary_mqtt_alias(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        callback_header = (ROOT / "src/helpers/CommonCLI.h").read_text(encoding="ascii")
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text(encoding="ascii")
        implementation = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="ascii")
        flags = ["-DWITH_RS232_BRIDGE=Serial2", "-DWITH_RS232_BRIDGE_UART=2",
                 "-DWITH_RS232_BRIDGE_RX=5", "-DWITH_RS232_BRIDGE_TX=6", "-DRS232_BRIDGE_MERGED=1",
                 "-DWITH_MQTT_BRIDGE=1", "-DWITH_BRIDGE=1", "-DENV_INCLUDE_GPS=1",
                 "-DLIGHTWEIGHT_WIFI_OTA=1", "-DMESH_ENABLE_FLOOD_RULE_ENGINE=1"]
        def preprocess(source):
            return subprocess.run([compiler, "-E", "-P", "-x", "c++", *flags, "-"],
                input=source, capture_output=True, text=True, check=True).stdout
        processed_header = preprocess(re.sub(r'^\s*#include[^\n]*', '', header, flags=re.M))
        lifecycle = "\n".join(extract_braced(processed_header, signature) for signature in (
            "AbstractBridge* activeBridge() {", "const AbstractBridge* activeBridge() const {",
            "RS232Bridge* createRS232Bridge()", "bool beginRS232Bridge()", "bool endRS232Bridge()",
            "void serviceRs232BridgePower()", "void formatRs232Status(char* reply)",
            "bool rs232BridgeEnabled() const", "bool isBridgeRunning() const override",
            "bool isRs232BridgeRunning()", "bool setRs232BridgeState(bool enable)",
            "bool restartRs232Bridge()", "bool isMqttBridgeRunning()", "bool requestMqttBridgeStop()",
            "bool isMqttBridgeStopping()", "bool setBridgeState(bool enable)",
            "bool restartBridge()"))
        code = (ROOT / "test/fixtures/rs232_mqtt_runtime.cpp").read_text(encoding="ascii")
        code = code[:code.index("int main() {")]
        for signature in ("virtual bool isEspNowBridgeRunning() = 0;",
                          "virtual bool setEspNowBridgeState(bool) = 0;",
                          "virtual bool restartEspNowBridge() = 0;"):
            code = code.replace(signature, "")
        for short, full in (("virtual bool setMqttBridgeState(bool) = 0;", "virtual bool setMqttBridgeState(bool enable)"),
                            ("virtual bool restartMqttBridge() = 0;", "virtual bool restartMqttBridge()")):
            code = code.replace(short, extract_braced(callback_header, full))
        commands = preprocess("\n".join(extract_braced(cli, signature) for signature in (
            "void CommonCLI::handleSetCmd(", "void CommonCLI::handleGetCmd(")))
        boot_start = implementation.index('mesh::hilStartupTrace("mesh_bridge_begin");')
        boot_start = implementation.index("#if defined(WITH_BRIDGE)", boot_start)
        boot_end = implementation.index('mesh::hilStartupTrace("mesh_bridge_ready");', boot_start)
        service_start = implementation.index("#if defined(WITH_MQTT_BRIDGE)\n  // MQTT owns TLS", implementation.index("MyMesh::servicePostMeshLoop()"))
        service_end = implementation.index("  if (next_flood_advert", service_start)
        pending_start = implementation.index("#if defined(WITH_BRIDGE)", implementation.index("mesh::powerdiag::Mask MyMesh::pendingWorkReason()"))
        pending_end = implementation.index("  if (radio_driver.isWatchdogObserving()", pending_start)
        route = {}
        for name in ("logRx", "logTx"):
            start = implementation.index("#ifdef WITH_MQTT_BRIDGE\n", implementation.index("void MyMesh::" + name + "("))
            # Keep the complete bridge guards, stopping before file logging's guard.
            end = implementation.index("#if MESH_ENABLE_PACKET_FILE_LOGGING\n", start)
            route[name] = implementation[start:end]
        ota_start = cli.index('    } else if (memcmp(command, "start ota", 9)')
        defaults_start = implementation.index("// bridge defaults")
        substitutions = {
            "LIFECYCLE": lifecycle, "BOOT": implementation[boot_start:boot_end],
            "DEFAULTS": implementation[defaults_start:implementation.index("// GPS defaults", defaults_start)],
            "FILTER": extract_braced(header, "void configureBridgeFilter(AbstractBridge* active_bridge)"),
            "ROUTE_RX": route["logRx"], "ROUTE_TX": route["logTx"],
            "SERVICE": implementation[service_start:service_end], "PENDING": implementation[pending_start:pending_end],
            "SET_BRANCHES": "\nelse ".join(extract_braced(commands, signature) for signature in (
                'if (strncmp(config, "mqtt.enabled ", 13) == 0)', 'if (strncmp(config, "rs232.enabled ", 14) == 0)',
                'if (memcmp(config, "bridge.enabled ", 15) == 0)', 'if (memcmp(config, "bridge.baud ", 12) == 0)',
                'if (memcmp(config, "bridge.uart ", 12) == 0)')),
            "GET_BRANCHES": extract_braced(commands, 'if (configKeyEquals(config, "bridge.type"))'),
            "PARSERS": "\n".join(extract_braced(cli, signature) for signature in (
                "static bool parseOnOffStrict(", "static bool configKeyEquals(")),
            "OTA": cli[ota_start:cli.index('    } else if (memcmp(command, "clock", 5)', ota_start)],
            "OTA_STATE": ota_resume_fields(),
        }
        for key, value in substitutions.items():
            code = code.replace("@" + key + "@", value)
        code += r'''int main() {
  assert(millisHasNowPassed(millis()));
  MyMesh mesh; mesh.defaults(); mesh.boot(); CommonCLI cli(mesh);
  expect_get(cli, "bridge.type", "> mqtt+rs232");
  assert(mesh.isMqttBridgeRunning() && !mesh.isRs232BridgeRunning());
  mesh.mqtt_bridge->setNetworkSuspended(true);
  assert(mesh.isMqttBridgeRunning() && mesh._prefs.bridge_enabled);
  assert(!mesh.bridgesPreventSleep());
  char reply[256] = {};
  cli.set("rs232.enabled on", reply); assert(strcmp(reply, "OK") == 0);
  assert(mesh.isRs232BridgeRunning() && mesh.isMqttBridgeRunning());
  assert(mesh.bridgesPreventSleep());  // Paused MQTT cannot mask an active UART.
  mesh.mqtt_bridge->setNetworkSuspended(false);
  const unsigned starts = mesh.mqtt_bridge->begins;
  cli.set("bridge.baud 57600", reply); assert(strcmp(reply, "OK") == 0);
  assert(mesh.isRs232BridgeRunning() && mesh.mqtt_bridge->begins == starts);
  cli.set("bridge.enabled off", reply); assert(strcmp(reply, "OK") == 0);
  assert(!mesh._prefs.bridge_enabled && mesh._prefs.rs232_bridge_enabled);
  assert(!mesh.isMqttBridgeRunning() && mesh.isRs232BridgeRunning());
  assert(mesh.isBridgeRunning() && mesh.bridgesPreventSleep());
  cli.set("bridge.enabled on", reply); assert(strcmp(reply, "OK") == 0);
  assert(mesh.isMqttBridgeRunning() && mesh.isRs232BridgeRunning());
  mesh::Packet packet;
  mesh.routeRx(&packet); mesh.routeTx(&packet); mesh.serviceBridges();
  assert(mesh.bridge->sent == 1 && mesh.bridge->loops == 1);
  assert(mesh.mqtt_bridge->received == 1 && mesh.mqtt_bridge->sent == 1);
  assert(mesh.filter_checks == 3);
  const unsigned uart_starts = Serial2.begins;
  cli.run("start ota ap", reply);
  assert(mesh._cli.board.ota && !mesh.isMqttBridgeRunning() && mesh.isRs232BridgeRunning());
  cli.run("stop ota", reply);
  assert(!mesh._cli.board.ota && mesh.isMqttBridgeRunning() && Serial2.begins == uart_starts);
  cli.set("rs232.enabled off", reply); cli.set("mqtt.enabled off", reply);
  assert(!mesh.bridge && mesh.isMqttBridgeStopping() && mesh.bridgesPreventSleep());
  mesh.serviceBridges();
  assert(!mesh.bridge && !mesh.isMqttBridgeRunning() && !mesh.bridgesPreventSleep());
  cli.set("rs232.enabled on", reply); mesh.mqtt_bridge->fail_starts = 1;
  cli.set("mqtt.enabled on", reply);
  assert(strstr(reply, "setting saved") && !mesh.isMqttBridgeRunning());
  assert(mesh.isRs232BridgeRunning() && mesh.bridgesPreventSleep());
  cli.set("mqtt.enabled on", reply);
  assert(strcmp(reply, "OK") == 0 && mesh.isMqttBridgeRunning());
}'''
        with tempfile.TemporaryDirectory(prefix="rs232-mqtt-only-") as directory:
            work = Path(directory)
            source, binary = work / "main.cpp", work / "test"
            source.write_text(code, encoding="ascii")
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                *flags, "-I" + str(ROOT / "src"), str(source), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
