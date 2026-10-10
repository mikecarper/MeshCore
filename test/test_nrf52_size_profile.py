#!/usr/bin/env python3
"""Exercise the sourced nRF52 recipe helper without invoking PlatformIO."""

import configparser
import os
from pathlib import Path
import re
import shlex
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
CONFIG.read(ROOT / "platformio.ini")
BASE_FLAGS = shlex.split(CONFIG["nrf52_base"]["build_flags"])
SEED_FLAGS = " ".join((
    "-DKEEP_CALLER_FLAG=1", "-DENABLE_OTA=1", "-DOTA_FLASH_STORE=1",
    "-DUSE_CC310_HW_CRYPTO=1", "-DENV_INCLUDE_BME680_BSEC=1",
    "-DMESH_NRF52_LOOP_STACK_WORDS=2048", "-DMAX_NEIGHBOURS=50",
    "-DFLOOD_PACKET_FILTER_SLOTS=63",
))


def recipe(target, *, platform="NRF52_PLATFORM", ota=True, profile="standard", full=False):
    # Use the real eligibility predicates as well as the actual mutating helper.
    # A pio guard makes an accidental expansion into a firmware build fail fast.
    script = r'''
set -euo pipefail
pio() { printf 'unexpected PlatformIO invocation\n' >&2; return 99; }
source ./build.sh
target=$1
PIO_ENV_PLATFORM_BY_NAME[$target]=$2
PIO_ENV_OTA_BY_NAME[$target]=$3
BUILD_PROFILE_FOR_TARGET=$4
BUILD_PROFILE_EFFECTIVE=$4
MESHDEBUG_OVERRIDE=
PACKET_LOGGING_OVERRIDE=
MQTT_BRIDGE_OVERRIDE=
PLATFORMIO_BUILD_FLAGS=$5
PLATFORMIO_BUILD_UNFLAGS=-DKEEP_EXISTING_UNFLAG=1
BUILD_REDUCTIONS=()
repeater=0; full=0; lora_ota=0; ota_only=0
if is_repeater_role_target "$target"; then repeater=1; fi
if is_nrf52_companion_radio_full_target "$target"; then full=1; fi
if is_lora_ota_build "$target"; then lora_ota=1; fi
if is_lora_ota_only_target "$target"; then ota_only=1; fi
apply_nrf52_size_profile "$target"
if [ "$6" = 1 ]; then
  # Supply actual option data to the real query helper without asking PIO to
  # resolve the configuration. Both source directories already exist here.
  printf -v PIO_CONFIG_JSON '[ [ "env:%s", [ [ "build_src_filter", [ "+<helpers/ota/*.cpp>", "+<helpers/nrf52/SerialBLEInterface.cpp>" ] ] ] ] ]' "$target"
  PLATFORMIO_BUILD_SRC_FILTER=
  apply_companion_radio_full_profile "$target" "$target"
fi
printf '%s\0' "$PLATFORMIO_BUILD_FLAGS" "$PLATFORMIO_BUILD_UNFLAGS" \
  "$repeater" "$full" "$lora_ota" "$ota_only" "${BUILD_REDUCTIONS[*]}"
'''
    environment = dict(os.environ)
    for name in ("PLATFORMIO_BUILD_FLAGS", "PLATFORMIO_BUILD_UNFLAGS"):
        environment.pop(name, None)
    completed = subprocess.run(
        ["bash", "-c", script, "size-profile-test", target, platform,
         str(int(ota)), profile, SEED_FLAGS, str(int(full))], cwd=ROOT, env=environment,
        capture_output=True, check=True,
    )
    fields = completed.stdout.decode().split("\0")
    if len(fields) != 8 or fields[-1]:
        raise AssertionError("Unexpected sourced recipe output: %r" % completed.stdout)
    return dict(zip(("flags", "unflags", "repeater", "full", "ota", "ota_only",
                     "reductions"), fields[:-1]))


class Nrf52SizeProfileTest(unittest.TestCase):
    def assert_preserved_policy(self, result):
        flags = BASE_FLAGS + shlex.split(result["flags"])
        optimizers = [flag for flag in flags if re.fullmatch(r"-O(?:[0-3sgz]|fast)", flag)]
        # GCC uses the last optimization flag: -Oz followed by -Os regresses
        # Full Companion even though a simple "contains -Oz" assertion passes.
        self.assertTrue(optimizers)
        self.assertEqual(optimizers[-1], "-Oz")
        self.assertNotIn("-Os", shlex.split(result["flags"]))
        self.assertIn("-Ofast", shlex.split(result["unflags"]))
        self.assertIn("-DKEEP_EXISTING_UNFLAG=1", shlex.split(result["unflags"]))
        self.assertEqual(result["flags"], SEED_FLAGS)
        self.assertEqual(result["reductions"], "")
        self.assertIn("MESH_NRF52_LOOP_STACK_WORDS=2048", flags)
        self.assertIn("-Wl,--wrap=xTaskCreate", flags)
        self.assertIn("USE_CC310_HW_CRYPTO=1", flags)

    def test_repeater_inherits_size_optimizer_without_feature_reductions(self):
        result = recipe("RAK_4631_repeater")
        self.assertEqual(result["repeater"], "1")
        self.assertEqual(result["full"], "0")
        self.assert_preserved_policy(result)

    def test_full_companion_inherits_size_optimizer_without_feature_reductions(self):
        result = recipe("RAK_4631_companion_radio_full")
        self.assertEqual(result["repeater"], "0")
        self.assertEqual(result["full"], "1")
        self.assert_preserved_policy(result)

    def test_ethernet_inherits_size_optimizer_without_feature_reductions(self):
        result = recipe("RAK_4631_companion_radio_ethernet")
        self.assertEqual(result["repeater"], "0")
        self.assertEqual(result["full"], "0")
        self.assertEqual(result["ota_only"], "0")
        self.assert_preserved_policy(result)

    def test_constrained_sensor_ota_uses_real_opt_in_eligibility(self):
        result = recipe("RAK_3401_sensor_lora_ota_no_external_sensors")
        self.assertEqual(result["repeater"], "0")
        self.assertEqual(result["full"], "0")
        self.assertEqual(result["ota"], "1")
        self.assertEqual(result["ota_only"], "1")
        self.assert_preserved_policy(result)

    def test_nrf52_full_seeder_compresses_all_names_and_preserves_capabilities(self):
        for target in ("RAK_4631_companion_radio_full", "t1000e_companion_radio_full"):
            with self.subTest(target=target):
                result = recipe(target, full=True)
                flags = shlex.split(result["flags"])
                self.assertEqual(result["full"], "1")
                self.assertIn("-DOTA_TARGET_NAME_FRONT_CODED=1", flags)
                self.assertNotIn("-DOTA_TARGET_NAME_TABLE=0", flags)
                for flag in ("-DOTA_SEEDER_ONLY=1", "-DCOMPANION_RADIO_FULL=1",
                             "-DCOMPANION_FEATURE_USB_MOTA_SOURCE=1",
                             "-DCOMPANION_FEATURE_BLE_MOTA_SOURCE=1",
                             "-DCOMPANION_FEATURE_TEMP_RADIO=1",
                             "-DCOMPANION_FEATURE_OTA_CLI=1",
                             "-DOFFLINE_QUEUE_SIZE=256", "-DOTA_SHARED_COMPANION_QUEUE=1",
                             "-DMESH_NRF52_LOOP_STACK_WORDS=2048",
                             "-DUSE_CC310_HW_CRYPTO=1", "-DENV_INCLUDE_BME680_BSEC=1"):
                    self.assertIn(flag, flags)
                self.assertNotIn("-DED25519_COMPACT_BASE=1", flags)
                self.assertNotIn("-DED25519_COMPACT_SHA512=1", flags)
                optimizers = [flag for flag in BASE_FLAGS + flags
                              if re.fullmatch(r"-O(?:[0-3sgz]|fast)", flag)]
                self.assertEqual(optimizers[-1], "-Oz")
                dedicated = "-DCOMPANION_FEATURE_DEDICATED_USB_LOGGING=1"
                self.assertEqual(dedicated in flags, target != "t1000e_companion_radio_full")

    def test_name_compression_does_not_expand_ordinary_companion_or_esp32_policy(self):
        for target, platform in (("RAK_4631_companion_radio_usb", "NRF52_PLATFORM"),
                                 ("RAK_4631_companion_radio_ble", "NRF52_PLATFORM"),
                                 ("heltec_v4_companion_radio_full", "ESP32_PLATFORM")):
            with self.subTest(target=target):
                result = recipe(target, platform=platform, full=True)
                flags = shlex.split(result["flags"])
                self.assertNotIn("-DOTA_TARGET_NAME_FRONT_CODED=1", flags)
                if platform == "NRF52_PLATFORM":
                    self.assertEqual(result["flags"], SEED_FLAGS)
                    self.assertEqual(result["unflags"], "-DKEEP_EXISTING_UNFLAG=1")

    def test_non_eligible_platforms_roles_and_ota_opt_out_are_unchanged(self):
        cases = (
            ("Heltec_v4_repeater", "ESP32_PLATFORM", True),
            ("RAK_3x72_companion_radio_usb", "STM32_PLATFORM", True),
            ("RAK_4631_companion_radio_usb", "NRF52_PLATFORM", True),
            ("RAK_3401_sensor_lora_ota_no_external_sensors", "NRF52_PLATFORM", False),
            ("RAK_3401_sensor", "NRF52_PLATFORM", True),
        )
        for target, platform, ota in cases:
            with self.subTest(target=target, platform=platform, ota=ota):
                result = recipe(target, platform=platform, ota=ota)
                self.assertEqual(result["flags"], SEED_FLAGS)
                self.assertEqual(result["unflags"], "-DKEEP_EXISTING_UNFLAG=1")
                self.assertEqual(result["reductions"], "")

    def test_measured_table_reductions_keep_stack_sensors_and_ota(self):
        cases = (
            ("Heltec_t096_repeater_lora_ota_no_external_sensors", 4),
            ("Heltec_t1_repeater_lora_ota_no_external_sensors", 4),
            ("RAK_3401_repeater_unified_lora_ota", 47),
        )
        for target, slots in cases:
            with self.subTest(target=target):
                result = recipe(target)
                self.assertEqual(result["repeater"], "1")
                self.assertEqual(result["flags"],
                                 SEED_FLAGS + " -DFLOOD_PACKET_FILTER_SLOTS=%d" % slots)
                unflags = shlex.split(result["unflags"])
                self.assertIn("-Ofast", unflags)
                self.assertIn("FLOOD_PACKET_FILTER_SLOTS=63", unflags)
                self.assertIn("8 KiB loop stack", result["reductions"])
                self.assertIn("sensors", result["reductions"])
                self.assertIn("OTA retained", result["reductions"])
                optimizers = [flag for flag in BASE_FLAGS + shlex.split(result["flags"])
                              if re.fullmatch(r"-O(?:[0-3sgz]|fast)", flag)]
                self.assertEqual(optimizers[-1], "-Oz")


if __name__ == "__main__":
    unittest.main()
