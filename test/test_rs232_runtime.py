#!/usr/bin/env python3
"""Execute production RS-232 defaults, preference migration, and CLI lifecycle."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_ota_channel_persistence import persistence_harness

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/rs232_runtime/main.cpp"


class RS232RuntimeTests(unittest.TestCase):
    def test_legacy_json_import_requires_explicit_uart_before_enabling(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        header = (ROOT / "src/helpers/CommonCLI.h").read_text(encoding="ascii")
        code = (ROOT / "test/fixtures/rs232_runtime/json.cpp").read_text(encoding="ascii")
        code = code.replace("@BRIDGE_SCHEMA@", extract_braced(header, "class BridgePrefs") + ";")
        code = code.replace("@NAME_SCHEMA@", 'def("name", node_name, sizeof(node_name));')
        self.assertIn('def("name", node_name, sizeof(node_name));', header)
        code = code.replace("@BRIDGE_ROOT@", 'def("bridge", bridge);')
        self.assertIn('def("bridge", bridge);', header)
        code = code.replace("@IMPORT@", extract_braced(source, 'if (fs->exists("/prefs.json"))'))
        for profile, macros in (
            ("legacy_dedicated", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2"]),
            ("merged_default_on", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                                   "RS232_BRIDGE_MERGED=1", "RS232_BRIDGE_DEFAULT_ON=1"]),
            ("merged", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                        "RS232_BRIDGE_MERGED=1"]),
            ("merged_composite", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                                  "RS232_BRIDGE_MERGED=1", "WITH_ESPNOW_BRIDGE=1",
                                  "ESPNOW_BRIDGE_MERGED=1"]),
            ("mqtt_rs232", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                             "RS232_BRIDGE_MERGED=1", "WITH_MQTT_BRIDGE=1"]),
            ("mqtt_rs232_espnow", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                                    "RS232_BRIDGE_MERGED=1", "WITH_MQTT_BRIDGE=1",
                                    "WITH_ESPNOW_BRIDGE=1"]),
            ("sole_espnow_merged", ["WITH_ESPNOW_BRIDGE=1", "ESPNOW_BRIDGE_MERGED=1"]),
            ("sole_espnow_dedicated", ["WITH_ESPNOW_BRIDGE=1"]),
            ("sole_espnow_default_on", ["WITH_ESPNOW_BRIDGE=1", "ESPNOW_BRIDGE_MERGED=1",
                                       "ESPNOW_BRIDGE_DEFAULT_ON=1"]),
        ):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory(prefix="rs232-json-") as directory:
                work = Path(directory)
                (work / "Utils.h").write_text(
                    '#pragma once\n#include <Arduino.h>\nnamespace mesh { struct Utils {\n'
                    'static void printHex(Stream&, uint8_t*, size_t) {}\n'
                    'static bool fromHex(uint8_t*, size_t, const char*) { return true; }\n'
                    '}; }\n', encoding="ascii")
                source_file, binary = work / "main.cpp", work / "test"
                source_file.write_text(code, encoding="ascii")
                result = subprocess.run([
                    compiler, "-std=c++17",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                    *["-D" + macro for macro in macros], "-I" + str(work),
                    "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
                    str(source_file), str(ROOT / "src/helpers/ConfigSerializer.cpp"), "-o", str(binary),
                ], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_complete_production_preference_images(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        base = persistence_harness()
        code = base[:base.index("struct Capture {")]
        # The generic constants extraction includes the header's fallback UART1.
        # Use the resolved MKE UART2 flag instead, as a real build does.
        code = code.replace("#define WITH_RS232_BRIDGE_UART 1", "")
        code += (ROOT / "test/fixtures/rs232_runtime/prefs.cpp").read_text(encoding="ascii")
        for profile, macros in (
            ("ordinary_unmerged", []),
            ("rs232_dedicated", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2"]),
            ("rs232_default_on", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                                   "RS232_BRIDGE_MERGED=1", "RS232_BRIDGE_DEFAULT_ON=1"]),
            ("nrf52_rs232_default_on", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                                         "RS232_BRIDGE_MERGED=1", "RS232_BRIDGE_DEFAULT_ON=1"]),
            ("rs232_merged", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                              "RS232_BRIDGE_MERGED=1"]),
            ("rs232_espnow_merged", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                                     "RS232_BRIDGE_MERGED=1", "WITH_ESPNOW_BRIDGE=1",
                                     "ESPNOW_BRIDGE_MERGED=1"]),
            ("mqtt_rs232", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                             "RS232_BRIDGE_MERGED=1", "WITH_MQTT_BRIDGE=1"]),
            ("mqtt_rs232_espnow", ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2",
                                    "RS232_BRIDGE_MERGED=1", "WITH_MQTT_BRIDGE=1",
                                    "WITH_ESPNOW_BRIDGE=1"]),
            ("sole_espnow_merged", ["WITH_ESPNOW_BRIDGE=1", "ESPNOW_BRIDGE_MERGED=1"]),
            ("sole_espnow_dedicated", ["WITH_ESPNOW_BRIDGE=1"]),
            ("sole_espnow_default_on", ["WITH_ESPNOW_BRIDGE=1", "ESPNOW_BRIDGE_MERGED=1",
                                       "ESPNOW_BRIDGE_DEFAULT_ON=1"]),
        ):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory(prefix="rs232-prefs-") as directory:
                work = Path(directory)
                transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text(encoding="ascii")
                (work / "ContactFileTransaction.h").write_text(transaction.replace(
                    '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'), encoding="ascii")
                source, binary = work / "main.cpp", work / "test"
                source.write_text(code, encoding="ascii")
                result = subprocess.run([
                    compiler, "-std=c++17", "-D" + ("NRF52_PLATFORM=1" if profile.startswith("nrf52") else "ESP32_PLATFORM=1"), "-DENABLE_OTA=1",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                    *["-D" + macro for macro in macros], "-I" + str(work),
                    "-I" + str(ROOT / "test/fixtures/radio_profiles/mocks"),
                    "-I" + str(ROOT / "src"), "-I" + str(ROOT / "src/helpers"),
                    str(source), "-o", str(binary),
                ], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_uart_intent_survives_cross_profile_upgrades_without_claiming_mqtt_intent(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        base = persistence_harness().replace("#define WITH_RS232_BRIDGE_UART 1", "")
        code = base[:base.index("struct Capture {")]
        code += (ROOT / "test/fixtures/rs232_runtime/prefs.cpp").read_text(encoding="ascii")
        common = ["WITH_RS232_BRIDGE=Serial2", "WITH_RS232_BRIDGE_UART=2", "RS232_BRIDGE_MERGED=1"]
        profiles = {"normal": [*common, "WITH_ESPNOW_BRIDGE=1", "ESPNOW_BRIDGE_MERGED=1"],
                    "dedicated": common[:2],
                    "triple": [*common, "WITH_ESPNOW_BRIDGE=1", "WITH_MQTT_BRIDGE=1"],
                    "mqtt_uart": [*common, "WITH_MQTT_BRIDGE=1"]}
        with tempfile.TemporaryDirectory(prefix="rs232-profile-upgrade-") as directory:
            work = Path(directory)
            transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text(encoding="ascii")
            (work / "ContactFileTransaction.h").write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'), encoding="ascii")
            source = work / "main.cpp"
            source.write_text(code, encoding="ascii")
            for name, macros in profiles.items():
                result = subprocess.run([compiler, "-std=c++17", "-DESP32_PLATFORM=1", "-DENABLE_OTA=1",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                    *["-D" + macro for macro in macros], "-I" + str(work),
                    "-I" + str(ROOT / "test/fixtures/radio_profiles/mocks"),
                    "-I" + str(ROOT / "src"), "-I" + str(ROOT / "src/helpers"),
                    str(source), "-o", str(work / name)], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            def run(profile, operation, image, primary, espnow, uart, port=2):
                result = subprocess.run([str(work / profile), operation, str(image), str(primary),
                    str(espnow), str(uart), str(port)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for origin in ("triple", "mqtt_uart"):
                for uart in (0, 1):
                    image = work / f"{origin}-{uart}.bin"
                    run(origin, "write", image, 1, 0, uart)
                    self.assertEqual(len(image.read_bytes()), 887)
                    for target in ("normal", "dedicated"):
                        with self.subTest(origin=origin, target=target, uart=uart):
                            run(target, "read", image, uart, 0, uart)
                    # Old MQTT files persisted primary enabled plus UART2.
                    # Without the new independent marker this remains MQTT only.
                    old = work / "old-mqtt.bin"
                    old.write_bytes(image.read_bytes()[:874])
                    run(origin, "read", old, 1, 0, 0)
            for origin in ("normal", "dedicated"):
                for uart in (0, 1):
                    image = work / f"{origin}-{uart}.bin"
                    run(origin, "write", image, uart, 0, uart)
                    for target in ("triple", "mqtt_uart"):
                        with self.subTest(origin=origin, target=target, uart=uart):
                            run(target, "read", image, uart, 0, uart)
            image = work / "corrupt-triple.bin"
            run("triple", "write", image, 1, 0, 1)
            saved = image.read_bytes()
            for tail in (saved[:875] + bytes([1]), saved[:876], saved[:876] + bytes([0])):
                image.write_bytes(tail)
                for target in ("normal", "dedicated"):
                    run(target, "read", image, 0, 0, 0)
                for target in ("triple", "mqtt_uart"):
                    run(target, "read", image, 1, 0, 0)

    def test_production_defaults_upgrade_and_runtime_controls(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text(encoding="ascii")
        implementation = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="ascii")
        declaration_start = cli.index("#if defined(ESPNOW_BRIDGE_MERGED)", cli.index("mesh::hilStartupTrace(\"prefs_image_open_ready\")"))
        declaration_end = cli.index("    // Every supported layout", declaration_start)
        declaration = cli[declaration_start:declaration_end]
        read_start = cli.index("if (file.available() >= (int)sizeof(_prefs->bridge_uart))")
        read_end = cli.index("if (file.available() >= (int)sizeof(_prefs->bridge_format))", read_start)
        uart_read = cli[read_start:read_end] + "}\n"
        # The old image mock has just its UART byte, so marker reads return
        # zero; execute the real marker guard rather than inventing a fallback.
        marker_start = cli.index("uint8_t bridge_profile = 0;", cli.index("if (file.available() >= (int)sizeof(_prefs->ota_channel))"))
        uart_read += "uint8_t bridge_profile = 0;\n" + extract_braced(cli[marker_start:], "if (file.read(&bridge_profile,")
        migrate_start = cli.index("// sanitise bad bridge pref values")
        migrate_end = cli.index("if (!mesh::bridge::isValidEspNowFormat", migrate_start)
        migration = cli[migrate_start:migrate_end]
        defaults_start = implementation.index("// bridge defaults")
        defaults_end = implementation.index("StrHelper::strncpy(_prefs.bridge_secret", defaults_start)
        defaults = implementation[defaults_start:defaults_end]
        initial_start = cli.index("#ifdef WITH_RS232_BRIDGE", cli.index("bool is_fresh_install"))
        initial = cli[initial_start:cli.index("#endif", initial_start) + len("#endif")]
        boot_source = implementation[implementation.index('mesh::hilStartupTrace("mesh_bridge_begin")'):]
        boot = extract_braced(boot_source, "if (_prefs.bridge_enabled) {")
        secondary_boot = "if (_prefs.espnow_bridge_enabled) setEspNowBridgeState(true);"
        self.assertIn(secondary_boot, boot_source)
        boot += "\n#ifdef WITH_ESPNOW_BRIDGE\n" + secondary_boot + "\n#endif\n"
        lifecycle = "\n".join(extract_braced(header, signature) for signature in (
            "AbstractBridge* activeBridge() {", "const AbstractBridge* activeBridge() const {",
            "RS232Bridge* createRS232Bridge()", "bool beginRS232Bridge()",
            "bool endRS232Bridge()", "bool rs232BridgeEnabled() const",
            "void serviceRs232BridgePower()", "void formatRs232Status(char* reply)",
            "bool isRs232BridgeRunning() const override", "bool setRs232BridgeState(bool enable)",
            "bool restartRs232Bridge()", "bool isBridgeRunning() const override",
            "bool setBridgeState(bool enable) override", "bool restartBridge() override",
        ))
        lifecycle += "\n#ifdef WITH_ESPNOW_BRIDGE\n" + "\n".join(
            extract_braced(header, signature) for signature in (
                "bool startSharedEspNowBridgeIfReady()", "bool isEspNowBridgeRunning() override",
                "bool setEspNowBridgeState(bool enable) override", "bool restartEspNowBridge() override",
            )) + "\n#endif\n"
        routing = {}
        for name in ("logRx", "logTx"):
            start = implementation.index("void MyMesh::" + name + "(")
            start = implementation.index("#ifdef WITH_MQTT_BRIDGE\n", start)
            end = implementation.index("  if (_logging)", start)
            routing[name] = implementation[start:end]
        loop_start = implementation.index("#if defined(WITH_ESPNOW_BRIDGE)",
                                          implementation.index("MyMesh::servicePostMeshLoop()"))
        loop_end = implementation.index("  if (next_flood_advert", loop_start)
        pending_start = implementation.index("#if defined(WITH_BRIDGE)",
                                             implementation.index("mesh::powerdiag::Mask MyMesh::pendingWorkReason()"))
        pending_end = implementation.index("  if (radio_driver.isWatchdogObserving()", pending_start)
        # Preprocess complete dispatch methods before extracting branches.
        # Their feature guards cross the closing braces between else-if cases.
        commands = "\n".join(extract_braced(cli, signature) for signature in (
            "void CommonCLI::handleSetCmd(", "void CommonCLI::handleGetCmd("))
        code = FIXTURE.read_text(encoding="ascii")
        for name, value in {
            "PARSERS": "\n".join(extract_braced(cli, signature) for signature in (
                "static bool parseOnOffStrict(", "static bool configKeyEquals(")),
            "DEFAULTS": defaults, "INITIAL_UART": initial,
            "LOAD_UART": declaration + "\n" + uart_read + "\n" + migration,
            "LIFECYCLE": lifecycle, "BOOT": boot,
            "ROUTE_RX": routing["logRx"], "ROUTE_TX": routing["logTx"],
            "SERVICE": implementation[loop_start:loop_end],
            "PENDING": implementation[pending_start:pending_end],
        }.items():
            code = code.replace("@" + name + "@", value)
        for profile, macros in (
            ("merged", ["RS232_BRIDGE_MERGED=1"]),
            ("merged_default_on", ["RS232_BRIDGE_MERGED=1", "RS232_BRIDGE_DEFAULT_ON=1"]),
            ("legacy_dedicated", []),
            ("merged_rs232_espnow", ["RS232_BRIDGE_MERGED=1", "WITH_ESPNOW_BRIDGE=1",
                                     "ESPNOW_BRIDGE_MERGED=1"]),
        ):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory(prefix="rs232-runtime-") as directory:
                work = Path(directory)
                source, binary = work / "main.cpp", work / "test"
                processed = subprocess.run([
                    compiler, "-E", "-P", "-x", "c++", "-DWITH_BRIDGE=1",
                    "-DWITH_RS232_BRIDGE=Serial2", "-DWITH_RS232_BRIDGE_UART=2",
                    "-DENV_INCLUDE_GPS=1", *["-D" + macro for macro in macros], "-",
                ], input=commands, capture_output=True, text=True, check=True).stdout
                set_branches = "\nelse ".join(extract_braced(processed, signature) for signature in (
                    'if (memcmp(config, "bridge.enabled ", 15) == 0)',
                    'if (memcmp(config, "bridge.baud ", 12) == 0)',
                    'if (memcmp(config, "bridge.uart ", 12) == 0)',
                ))
                get_branches = "\nelse ".join(extract_braced(processed, signature) for signature in (
                    'if (configKeyEquals(config, "bridge.type"))',
                    'if (configKeyEquals(config, "bridge.enabled"))',
                    'if (configKeyEquals(config, "bridge.running"))',
                    'if (configKeyEquals(config, "bridge.baud"))',
                    'if (configKeyEquals(config, "bridge.uart"))',
                ))
                if "WITH_ESPNOW_BRIDGE=1" in macros:
                    set_branches = extract_braced(processed, 'if (memcmp(config, "espnow.enabled ", 15) == 0)') \
                        + "\nelse " + set_branches
                    set_branches += "\nelse " + "\nelse ".join(extract_braced(processed, signature) for signature in (
                        'if (memcmp(config, "bridge.channel ", 15) == 0)',
                        'if (memcmp(config, "bridge.secret ", 14) == 0)',
                        'if (memcmp(config, "bridge.format ", 14) == 0)',
                    ))
                    get_branches += "\nelse " + "\nelse ".join(extract_braced(processed, signature) for signature in (
                        'if (configKeyEquals(config, "espnow.enabled"))',
                        'if (configKeyEquals(config, "espnow.running"))',
                        'if (configKeyEquals(config, "bridge.channel"))',
                        'if (configKeyEquals(config, "bridge.secret"))',
                        'if (configKeyEquals(config, "bridge.format"))',
                    ))
                source.write_text(code.replace("@SET_BRANCHES@", set_branches)
                                  .replace("@GET_BRANCHES@", get_branches), encoding="ascii")
                result = subprocess.run([
                    compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                    *["-D" + macro for macro in macros], "-I" + str(ROOT / "src"),
                    str(source), "-o", str(binary),
                ], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("RS232 runtime checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
