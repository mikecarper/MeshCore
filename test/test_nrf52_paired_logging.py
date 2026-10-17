#!/usr/bin/env python3
"""Normal nRF52 sensor/OTA pairs retain their runtime USB packet logger."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
NORMAL_TARGETS = (
    "RAK_3401_repeater", "RAK_3401_room_server", "RAK_3401_sensor",
    "RAK_4631_repeater", "RAK_4631_room_server", "RAK_4631_sensor",
    "Heltec_tower_v2_repeater", "Heltec_tower_v2_room_server",
)
CANONICAL_TARGETS = tuple(
    target + "_lora_ota_no_external_sensors" for target in NORMAL_TARGETS
    if target != "Heltec_tower_v2_repeater"
) + (
    "Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors",
    "RAK_3401_repeater_unified_lora_ota", "RAK_4631_repeater_unified_lora_ota",
)
PACKET_MARKER = "logging.usb.packets=%s: %s, len=%d (type=%d, route=%s, payload_len=%d)"
CONTROL_MARKER = "logging.usb.control=OK - USB logging %s (saved)"


def build_policy(target, *, profile="full", disable_debug=0, debug="", packet="",
                 normalize=False, platform="NRF52_PLATFORM"):
    # Source production helpers without asking PlatformIO for inventory. The
    # normal-to-alias mappings below are the inputs used by the real resolver.
    command = r'''
set -euo pipefail
source build.sh
for base in RAK_3401_repeater RAK_3401_room_server RAK_3401_sensor \
    RAK_4631_repeater RAK_4631_room_server RAK_4631_sensor \
    Heltec_tower_v2_room_server; do
  PIO_ENV_PLATFORM_BY_NAME["$base"]=NRF52_PLATFORM
  PIO_ENV_PLATFORM_BY_NAME["${base}_lora_ota_no_external_sensors"]=NRF52_PLATFORM
done
PIO_ENV_PLATFORM_BY_NAME[Heltec_tower_v2_repeater]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors]=NRF52_PLATFORM
env_name="$1"
if [ "$6" = yes ]; then
  env_name=$(get_nrf52_sensor_ota_pair_target "$env_name")
fi
PIO_ENV_PLATFORM_BY_NAME["$env_name"]="$7"
NRF52_OTA_SENSOR_PROFILE="$2"
DISABLE_DEBUG="$3"
MESHDEBUG_OVERRIDE="$4"
PACKET_LOGGING_OVERRIDE="$5"
BUILD_PROFILE_FOR_TARGET=auto
PLATFORMIO_BUILD_FLAGS=""
BUILD_APPLICATION_EXPECTATIONS=()
disable_debug_flags "$env_name"
apply_debug_overrides "$env_name"
apply_mqtt_bridge_override "$env_name"
disable_usb_logging_for_mqtt "$env_name"
apply_merged_standard_usb_logging_profile "$env_name"
declare_full_logging_application_contract "$env_name"
printf '%s\n' "$env_name"
printf '%s\n' "$PLATFORMIO_BUILD_FLAGS"
printf '%s\n' "${BUILD_APPLICATION_EXPECTATIONS[@]}"
'''
    environment = os.environ.copy()
    for name in ("MQTT_BRIDGE_OVERRIDE", "MQTT_DEBUG_OVERRIDE",
                 "PLATFORMIO_BUILD_FLAGS", "MESHDEBUG_OVERRIDE",
                 "PACKET_LOGGING_OVERRIDE", "DISABLE_DEBUG"):
        environment.pop(name, None)
    result = subprocess.run(
        ["bash", "-c", command, "nrf52-paired-logging", target, profile,
         str(disable_debug), debug, packet, "yes" if normalize else "no",
         platform], cwd=ROOT, env=environment, text=True, capture_output=True,
        check=True, timeout=10,
    )
    lines = result.stdout.splitlines()
    return lines[0], shlex.split(lines[1]), lines[2:]


class Nrf52PairedLoggingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("c++")
        if cls.compiler is None:
            raise unittest.SkipTest("C++ preprocessor required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="meshcore-nrf-paired-logging-")
        cls.includes = Path(cls.temporary.name)
        (cls.includes / "Arduino.h").write_text("// No peripherals needed.\n", encoding="ascii")
        cls.macros_cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def macros(self, flags):
        key = tuple(flags)
        if key in self.macros_cache:
            return self.macros_cache[key]
        # Reproduce PlatformIO's -D-before--U ordering. Seed only verbose
        # diagnostics, not packet logging: the normal policy must supply it.
        defines = [flag for flag in flags if flag.startswith("-D")]
        undefines = [flag for flag in flags if flag.startswith("-U")]
        result = subprocess.run(
            [self.compiler, "-x", "c++", "-E", "-dM", "-",
             "-DARDUINO=10819", "-DNRF52_PLATFORM=1", "-DUSE_TINYUSB=1",
             "-DMESH_DEBUG=1", "-I", str(self.includes), "-I", str(ROOT / "src"),
             *defines, *undefines], input='#include <helpers/UsbLogging.h>\n',
            text=True, capture_output=True, check=True, timeout=10,
        )
        macros = {}
        for line in result.stdout.splitlines():
            if line.startswith("#define "):
                parts = line.split(maxsplit=2)
                macros[parts[1]] = parts[2] if len(parts) == 3 else ""
        self.macros_cache[key] = macros
        return macros

    def assert_contract(self, flags, expectations):
        macros = self.macros(flags)
        self.assertEqual(macros.get("MESH_PACKET_LOGGING"), "1")
        self.assertEqual(macros.get("MESH_USB_LOGGING_MERGED"), "1")
        self.assertEqual(macros.get("MESH_USB_LOGGING_AVAILABLE"), "1")
        self.assertIn(PACKET_MARKER, expectations)
        self.assertIn(CONTROL_MARKER, expectations)
        return macros

    def test_normal_targets_keep_logger_after_actual_alias_normalization(self):
        for target in NORMAL_TARGETS:
            for profile in ("full", "reduced"):
                with self.subTest(target=target, profile=profile):
                    selected, flags, expectations = build_policy(
                        target, profile=profile, normalize=True)
                    self.assertIn(selected, CANONICAL_TARGETS)
                    macros = self.assert_contract(flags, expectations)
                    self.assertNotIn("MESH_DEBUG", macros)

    def test_both_canonical_pairs_preserve_packet_logging_against_build_overrides(self):
        for target in CANONICAL_TARGETS:
            for profile in ("full", "reduced"):
                for disable_debug, debug, packet in ((0, "off", ""), (1, "", ""),
                                                      (0, "off", "off"), (1, "off", "off")):
                    with self.subTest(target=target, profile=profile,
                                      disable_debug=disable_debug, packet=packet):
                        _, flags, expectations = build_policy(
                            target, profile=profile, disable_debug=disable_debug,
                            debug=debug, packet=packet)
                        macros = self.assert_contract(flags, expectations)
                        self.assertNotIn("MESH_DEBUG", macros)

    def test_verbose_debug_is_explicit_and_disable_debug_still_wins(self):
        for disable_debug in (0, 1):
            with self.subTest(disable_debug=disable_debug):
                _, flags, expectations = build_policy(
                    CANONICAL_TARGETS[0], debug="on", disable_debug=disable_debug)
                macros = self.assert_contract(flags, expectations)
                if disable_debug:
                    self.assertNotIn("MESH_DEBUG", macros)
                else:
                    self.assertEqual(macros.get("MESH_DEBUG"), "1")

    def test_old_ota_aliases_outside_the_required_pair_keep_their_policy(self):
        for profile in ("", "custom"):
            for target in CANONICAL_TARGETS:
                with self.subTest(target=target, profile=profile):
                    _, flags, expectations = build_policy(
                        target, profile=profile, debug="off", packet="off")
                    macros = self.macros(flags)
                    self.assertNotIn("MESH_PACKET_LOGGING", macros)
                    self.assertEqual(macros.get("MESH_USB_LOGGING_AVAILABLE"), "0")
                    self.assertNotIn(PACKET_MARKER, expectations)
                    self.assertNotIn(CONTROL_MARKER, expectations)

    def test_specialized_unqualified_and_stm32_targets_are_not_broadened(self):
        targets = (
            "RAK_3401_repeater_rak13302_w25q16_lora_ota",
            "RAK_4631_repeater_rak15001_slot_c_lora_ota",
            "RAK_4631_repeater_w25q16_lora_ota",
            "RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors",
            "RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors",
            "RAK_4631_repeater_ethernet", "RAK_4631_room_server_ethernet",
            "RAK_4631_repeater_rak15002_sd_seeder_only",
            "RAK_4631_companion_radio_ble", "RAK_4631_kiss_modem",
            "nrf_repeater_lora_ota_no_external_sensors", "Heltec_t096_repeater_lora_ota_no_external_sensors",
            "RAK_3x72_companion_radio_usb", "Tiny_Relay_companion_radio_usb",
            "wio-e5_companion_radio_usb", "wio-e5-mini_companion_radio_usb",
        )
        for target in targets:
            with self.subTest(target=target):
                _, flags, expectations = build_policy(
                    target, debug="off", packet="off", disable_debug=1)
                macros = self.macros(flags)
                self.assertNotIn("MESH_PACKET_LOGGING", macros)
                self.assertEqual(macros.get("MESH_USB_LOGGING_AVAILABLE"), "0")
                self.assertNotIn(PACKET_MARKER, expectations)
        _, flags, expectations = build_policy(
            CANONICAL_TARGETS[0], debug="off", packet="off", disable_debug=1,
            platform="STM32_PLATFORM")
        self.assertNotIn("MESH_PACKET_LOGGING", self.macros(flags))
        self.assertNotIn(PACKET_MARKER, expectations)


if __name__ == "__main__":
    unittest.main()
