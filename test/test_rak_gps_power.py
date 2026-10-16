#!/usr/bin/env python3
"""Execute WisBlock GPS/shared-rail ownership without touching hardware.

The production allocation-free policy is compiled unchanged. GPIO/UART/GNSS
integration regressions live alongside these portable boot/hold cases.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from test_gps_upstream_adaptations import ARDUINO, CAYENNE, MESH
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/wisblock_shared_power_policy.cpp"
ENVIRONMENT = ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp"


def lifecycle_harness(gps=True, negative=None):
    source = ENVIRONMENT.read_text(encoding="utf-8")
    mutations = {
        "shared_provider_pin": (
            "_location->setPinEn(shared_power_rail ? -1 : gps_control_pin);",
            "_location->setPinEn(gps_control_pin);"),
        "forced_user_on": (
            "_location->setPinEn(shared_power_rail ? -1 : gps_control_pin);",
            "_location->setPinEn(shared_power_rail ? -1 : gps_control_pin);\n"
            "    setGpsTelemetryUserEnabled(true);"),
        "no_wake_settle": ("if (!was_powered) delay(500);", "// Negative control: use UART before cold receiver settles."),
        "no_pad_release": ("pinMode(PIN_GPS_TX, INPUT);\n  pinMode(PIN_GPS_RX, INPUT);",
                           "// Negative control: retain driven serial pads after receiver power-off."),
    }
    if negative in mutations:
        before, after = mutations[negative]
        assert source.count(before) == 1, "negative-control source anchor changed"
        source = source.replace(before, after, 1)
    elif negative == "power_before_uart_end":
        before = extract_braced(source, "void EnvironmentSensorManager::stop_gps()")
        after = before.replace("if (serialGPSFlag) stopRakGpsUart();", "// Negative control: release UART too late.")
        assert after != before, "negative-control UART stop anchor changed"
        after = after.replace("applyWisBlockRailPower();", "applyWisBlockRailPower();\n  if (serialGPSFlag) stopRakGpsUart();", 1)
        source = source.replace(before, after, 1)
    elif negative is not None:
        raise ValueError("unknown lifecycle negative control")
    fixture_name = "rak_gps_power.cpp" if gps else "rak_peripheral_power_no_gps.cpp"
    fixture = (ROOT / "test/fixtures" / fixture_name).read_text(encoding="utf-8")
    helpers = "\n".join(extract_braced(source, signature) for signature in (
        "static uint8_t rakGpsControlActiveLevel(",
        "static void setRakGpsControl(",
        "static void stopRakGpsUart(",
        "static bool serialHasValidGpsSentence(",
        "static bool configureRakUbloxReceiver(",
    )) if gps else ""
    provider = extract_braced(source, "class RAK12500LocationProvider") + ";\n" if gps else ""
    signatures = (
        "bool EnvironmentSensorManager::begin()",
        "bool EnvironmentSensorManager::querySensors(",
        "uint8_t EnvironmentSensorManager::getVoltageSensorChannels(",
        "uint8_t EnvironmentSensorManager::queryVoltageSensors(",
        "int EnvironmentSensorManager::getNumSettings()",
        "const char* EnvironmentSensorManager::getSettingName(",
        "const char* EnvironmentSensorManager::getSettingValue(",
        "bool EnvironmentSensorManager::setSettingValue(",
        "void EnvironmentSensorManager::setPowerSavingEnabled(",
        "bool EnvironmentSensorManager::supportsSharedPeripheralMode()",
        "void EnvironmentSensorManager::applyWisBlockRailPower()",
        "uint8_t EnvironmentSensorManager::detectWisBlockStorageHolds()",
        "void EnvironmentSensorManager::configureSharedPeripheralMode(",
        "bool EnvironmentSensorManager::canRemoveSharedStoragePowerForDeepSleep() const",
        "bool EnvironmentSensorManager::isGpsPowered()",
        "void EnvironmentSensorManager::rakGPSInit()",
        "bool EnvironmentSensorManager::gpsIsAwake(",
        "void EnvironmentSensorManager::armGpsPowerSavingCycle()",
        "void EnvironmentSensorManager::start_gps()",
        "void EnvironmentSensorManager::stop_gps()",
        "bool EnvironmentSensorManager::gpsUsesSerialUart(",
        "bool EnvironmentSensorManager::gpsSerialTransportMayConflict(",
        "bool EnvironmentSensorManager::gpsSerialTransportCanYield(",
        "bool EnvironmentSensorManager::setGpsSerialTransportBlocked(",
        "void EnvironmentSensorManager::shutdownGpsTransportForDeepSleep()",
        "void EnvironmentSensorManager::loop()",
    )
    if not gps:
        signatures = tuple(signature for signature in signatures if signature not in (
            "void EnvironmentSensorManager::rakGPSInit()",
            "bool EnvironmentSensorManager::gpsIsAwake(",
            "void EnvironmentSensorManager::armGpsPowerSavingCycle()",
            "void EnvironmentSensorManager::start_gps()",
            "void EnvironmentSensorManager::stop_gps()",
            "void EnvironmentSensorManager::loop()",
        ))
    methods = "\n".join(extract_braced(source, signature) for signature in signatures)
    return fixture.replace("@HELPERS@", helpers).replace("@PROVIDER@", provider).replace("@METHODS@", methods)


def compile_lifecycle(testcase, cases, defines=(), gps=True, negative=None, auto_store=True):
    with tempfile.TemporaryDirectory(prefix="rak-gps-lifecycle-") as folder:
        work = Path(folder)
        arduino = ARDUINO.replace("#include <deque>", "#include <deque>\n#include <vector>\n#include <string>\n#include <cassert>\n#define INPUT 0")
        arduino = arduino.replace("inline void pinMode(int,int){}\ninline void digitalWrite(int,int){}\ninline int digitalRead(int){return HIGH;}", r'''
struct HardwareEvent { std::string name; int a; int b; uint32_t time; };
inline std::vector<HardwareEvent> hardware_events;
inline int pin_values[64]={};
inline void event(const char* name,int a=0,int b=0){hardware_events.push_back({name,a,b,now_ms});}
inline void pinMode(int pin,int mode){event("pinMode",pin,mode);}
inline void digitalWrite(int pin,int value){assert(pin>=0&&pin<64);pin_values[pin]=value;event("write",pin,value);}
inline int digitalRead(int pin){assert(pin>=0&&pin<64);return pin_values[pin];}
''')
        for name, content in {"Arduino.h": arduino, "Mesh.h": MESH, "CayenneLPP.h": CAYENNE,
                              "Wire.h": "#pragma once\nclass TwoWire {};\ninline TwoWire Wire;\n"}.items():
            (work / name).write_text(content, encoding="utf-8")
        cpp, binary = work / "lifecycle.cpp", work / "lifecycle"
        cpp.write_text(lifecycle_harness(gps=gps, negative=negative), encoding="utf-8")
        compiled = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            *(["-Wno-unused-variable"] if not gps else []),
            "-fno-pie", "-no-pie", f"-DENV_INCLUDE_GPS={int(gps)}", "-DNRF52_PLATFORM",
            "-DRAK_BOARD", *(["-DRAK_WISBLOCK_GPS"] if gps else []),
            *(["-DOTA_RAK_AUTO_STORE"] if auto_store else []),
            "-DWITH_RS232_BRIDGE_GPS_CONFLICT_UART=1", *defines,
            "-I" + str(work), "-I" + str(ROOT / "src"), str(cpp),
            str(ROOT / "src/helpers/SensorManager.cpp"), "-o", str(binary),
        ], text=True, capture_output=True, timeout=60)
        testcase.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        for case in cases:
            with testcase.subTest(scenario=case, defines=defines, negative=negative):
                result = subprocess.run([str(binary), case], text=True, capture_output=True, timeout=20)
                if negative:
                    testcase.assertNotEqual(result.returncode, 0, "unsafe production variant escaped the regression")
                    testcase.assertIn("Assertion", result.stderr)
                else:
                    testcase.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def compile_policy(testcase, flags=()):
    with tempfile.TemporaryDirectory(prefix="wisblock-gps-power-") as folder:
        binary = Path(folder) / "policy"
        compiled = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
            *flags, "-I" + str(ROOT / "src"), str(FIXTURE), "-o", str(binary),
        ], text=True, capture_output=True, timeout=60)
        testcase.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        executed = subprocess.run([str(binary)], text=True, capture_output=True, timeout=20)
        testcase.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)


class RakGpsPowerTests(unittest.TestCase):
    def test_actual_policy_boot_configuration_activity_and_fail_closed_holds(self):
        compile_policy(self, ("-DEXPECTED_COMPILE_HOLDS=0",))

    def test_actual_compile_time_radio_and_poe_dependencies_are_not_service_toggles(self):
        variants = [
            (("-DRAK_4631",), 0),
            (("-DRAK_3401",), 1),
            (("-DETHERNET_ENABLED=1",), 2),
            (("-DETHERNET_ENABLED=0",), 2),
            (("-DETHERNET_USE_RAK13800=1",), 2),
            (("-DETHERNET_USE_RAK13800=0",), 2),
            (("-DRAK4631_COMBINED_ETHERNET=1",), 2),
            (("-DRAK4631_COMBINED_ETHERNET=0",), 2),
            (("-DRAK_3401", "-DETHERNET_ENABLED=0"), 3),
            (("-DETHERNET_ENABLED=0", "-DETHERNET_USE_RAK13800=0",
              "-DRAK4631_COMBINED_ETHERNET=0"), 2),
        ]
        for flags, expected in variants:
            with self.subTest(flags=flags):
                compile_policy(self, (*flags, f"-DEXPECTED_COMPILE_HOLDS={expected}"))

    def test_actual_rak4631_uart_boot_off_wake_schedule_and_bridge_ownership(self):
        compile_lifecycle(self, ("uart", "shared_uart", "repeat", "prefs_before", "powered_status", "hourly", "absent", "storage",
                                "storage_ids", "i2c", "shared_i2c", "wake_failure", "initial_config_failure",
                                "wake_output_failure", "wake_rate_failure", "wake_bus_failure"),
                          ("-DRAK_4631",))

    def test_actual_radio_and_poe_holds_preserve_the_supply_when_gps_is_off(self):
        for defines in (("-DRAK_3401",), ("-DRAK_4631", "-DETHERNET_ENABLED=0")):
            with self.subTest(defines=defines):
                compile_lifecycle(self, ("held_uart", "held_i2c", "absent"), defines)

    def test_actual_dedicated_gps_control_isolated_from_shared_slot_power(self):
        for active in (0, 1):
            with self.subTest(active_level=active):
                compile_lifecycle(self, ("dedicated",),
                                  ("-DRAK_4631", "-DPIN_GPS_EN=14", f"-DPIN_GPS_EN_ACTIVE={active}"))

    def test_actual_boot_policy_protects_storage_and_poe_even_when_gps_is_not_compiled(self):
        for defines in (("-DRAK_4631",), ("-DRAK_3401",), ("-DRAK_4631", "-DETHERNET_ENABLED=0")):
            with self.subTest(defines=defines):
                compile_lifecycle(self, ("no_gps",), defines, gps=False)

    def test_independent_negative_controls_reject_forced_gps_and_unsafe_rail_ownership(self):
        for negative in ("shared_provider_pin", "forced_user_on", "no_wake_settle", "power_before_uart_end", "no_pad_release"):
            with self.subTest(negative=negative):
                compile_lifecycle(self, ("uart",), ("-DRAK_4631",), negative=negative)

    def test_fixed_shared_spi_or_sd_storage_is_a_rail_owner_without_auto_probe(self):
        for store in ("OTA_QSPI_SHARED_WISBLOCK_SPI", "OTA_SD_STORE"):
            with self.subTest(store=store):
                compile_lifecycle(self, ("fixed_storage",), ("-DRAK_4631", "-D" + store), auto_store=False)

    def test_terminal_storage_query_is_frozen_read_only_and_fail_closed(self):
        for defines in (("-DRAK_4631",), ("-DRAK_3401",),
                        ("-DRAK_4631", "-DETHERNET_ENABLED=1")):
            with self.subTest(defines=defines):
                compile_lifecycle(self, ("terminal_storage",), defines)


if __name__ == "__main__":
    unittest.main()
