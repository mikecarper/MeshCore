#!/usr/bin/env python3
"""Run the sole repeater ESP-NOW production lifecycle and CLI with fault injection."""
from pathlib import Path
import shutil
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class ESPNowRuntimeTests(unittest.TestCase):
    def test_production_defaults_aliases_routing_retries_and_ota_exclusion(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text(encoding="ascii")
        callback_header = (ROOT / "src/helpers/CommonCLI.h").read_text(encoding="ascii")
        implementation = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="ascii")
        defaults_start = implementation.index("// bridge defaults")
        defaults_end = implementation.index("// GPS defaults", defaults_start)
        boot_start = implementation.index('mesh::hilStartupTrace("mesh_bridge_begin");')
        boot_start = implementation.index("#if defined(WITH_BRIDGE)", boot_start)
        boot_end = implementation.index('mesh::hilStartupTrace("mesh_bridge_ready");', boot_start)
        lifecycle_signatures = (
            "AbstractBridge* activeBridge() {", "const AbstractBridge* activeBridge() const {",
            "bool startSharedEspNowBridgeIfReady()", "bool isBridgeRunning() const override",
            "bool setBridgeState(bool enable) override",
            "bool restartBridge() override",
        )
        routing = {}
        for name in ("logRx", "logTx"):
            start = implementation.index("void MyMesh::" + name + "(")
            start = implementation.index("#ifdef WITH_MQTT_BRIDGE\n", start)
            # Keep the complete bridge guards, stopping before file logging's guard.
            end = implementation.index("#if MESH_ENABLE_PACKET_FILE_LOGGING\n", start)
            routing[name] = implementation[start:end]
        service_start = implementation.index("#if defined(WITH_ESPNOW_BRIDGE)",
                                             implementation.index("MyMesh::servicePostMeshLoop()"))
        service_end = implementation.index("  if (next_flood_advert", service_start)
        pending_start = implementation.index("#if defined(WITH_BRIDGE)",
                                             implementation.index("mesh::powerdiag::Mask MyMesh::pendingWorkReason()"))
        pending_end = implementation.index("  if (radio_driver.isWatchdogObserving()", pending_start)
        commands = "\n".join(extract_braced(cli, signature) for signature in (
            "void CommonCLI::handleSetCmd(", "void CommonCLI::handleGetCmd("))
        template = (ROOT / "test/fixtures/espnow_runtime.cpp").read_text(encoding="ascii")
        for key, value in {
            "DEFAULTS": implementation[defaults_start:defaults_end],
            "BOOT": implementation[boot_start:boot_end],
            "CALLBACK_FORWARDS": "\n".join(extract_braced(callback_header, signature) for signature in (
                "virtual bool setEspNowBridgeState(bool enable)",
                "virtual bool restartEspNowBridge()", "virtual bool isEspNowBridgeRunning()")),
            "PARSERS": "\n".join(extract_braced(cli, signature) for signature in (
                "static bool parseOnOffStrict(", "static bool configKeyEquals(")),
            "ROUTE_RX": routing["logRx"], "ROUTE_TX": routing["logTx"],
            "SERVICE": implementation[service_start:service_end],
            "PENDING": implementation[pending_start:pending_end],
        }.items():
            template = template.replace("@" + key + "@", value)
        for profile, macros in (
            ("merged", ["ESPNOW_BRIDGE_MERGED=1"]),
            ("legacy_dedicated", []),
            ("merged_default_on", ["ESPNOW_BRIDGE_MERGED=1", "ESPNOW_BRIDGE_DEFAULT_ON=1"]),
        ):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory(prefix="espnow-runtime-") as directory:
                work = Path(directory)
                flags = ["-DWITH_BRIDGE=1", "-DWITH_ESPNOW_BRIDGE=1",
                         *["-D" + macro for macro in macros]]
                processed = subprocess.run([compiler, "-E", "-P", "-x", "c++", *flags, "-"],
                    input=commands, capture_output=True, text=True, check=True).stdout
                processed_header = subprocess.run([compiler, "-E", "-P", "-x", "c++", *flags, "-"],
                    input=re.sub(r'^\s*#include[^\n]*', '', header, flags=re.M),
                    capture_output=True, text=True, check=True).stdout
                lifecycle = "\n".join(extract_braced(processed_header, signature)
                                      for signature in lifecycle_signatures)
                sets = "\nelse ".join(extract_braced(processed, signature) for signature in (
                    'if (memcmp(config, "espnow.enabled ", 15) == 0)',
                    'if (memcmp(config, "bridge.enabled ", 15) == 0)',
                    'if (memcmp(config, "bridge.source ", 14) == 0)',
                    'if (memcmp(config, "bridge.channel ", 15) == 0)',
                    'if (memcmp(config, "bridge.secret ", 14) == 0)',
                    'if (memcmp(config, "bridge.format ", 14) == 0)',
                ))
                gets = "\nelse ".join(extract_braced(processed, signature) for signature in (
                    'if (configKeyEquals(config, "bridge.type"))',
                    'if (configKeyEquals(config, "bridge.enabled"))',
                    'if (configKeyEquals(config, "bridge.running"))',
                    'if (configKeyEquals(config, "espnow.enabled"))',
                    'if (configKeyEquals(config, "espnow.running"))',
                    'if (configKeyEquals(config, "bridge.channel"))',
                    'if (configKeyEquals(config, "bridge.secret"))',
                    'if (configKeyEquals(config, "bridge.format"))',
                ))
                source, binary = work / "main.cpp", work / "test"
                source.write_text(template.replace("@SET_BRANCHES@", sets)
                                  .replace("@GET_BRANCHES@", gets)
                                  .replace("@LIFECYCLE@", lifecycle), encoding="ascii")
                result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                    *flags, "-I" + str(ROOT / "src"), str(source), "-o", str(binary)],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("ESP-NOW runtime checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
