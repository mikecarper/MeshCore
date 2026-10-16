#!/usr/bin/env python3
"""Keep the RAK4631 adaptive RAK15002 build recipes scoped and bounded.

These are recipe-only checks. They source shell helpers with PlatformIO
stubbed, so a configuration regression cannot quietly start a firmware build.
"""

import configparser
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROOT_CONFIG = ROOT / "platformio.ini"
RAK_CONFIG = ROOT / "variants/rak4631/platformio.ini"
BUILD_SCRIPT = ROOT / "build_legacy.sh"
SEPARATOR = "\n__RAK15002_UNFLAGS__\n"

STORE_DEFINES = (
    "OTA_RAK15002_SD_STORE=1",
    "OTA_SD_STORE=1",
    "OTA_SD_CS_PIN=26",
    "OTA_SD_SCK_MHZ=4",
    "OTA_SD_CACHE_MAX=16",
    "OTA_MAX_SERVE=24",
)
RECEIVER_NOR_DEFINES = (
    "OTA_RAK_AUTO_STORE=1",
    "OTA_QSPI_STORE=1",
    "OTA_FLASH_STORE=1",
    "OTA_QSPI_SHARED_WISBLOCK_SPI=1",
    "OTA_QSPI_SCK_ARDUINO_PIN=3",
    "OTA_QSPI_CS_ARDUINO_PIN=31",
    "OTA_QSPI_IO0_ARDUINO_PIN=30",
    "OTA_QSPI_IO1_ARDUINO_PIN=29",
    "OTA_QSPI_IO2_NOT_CONNECTED=1",
    "OTA_QSPI_IO3_NOT_CONNECTED=1",
    "OTA_QSPI_SCK_FREQUENCY=NRF_QSPI_FREQ_32MDIV4",
    "OTA_QSPI_EXPECTED_SIZE=2097152UL",
)
RECEIVERS = (
    "RAK_4631_repeater_unified_lora_ota",
    "RAK_4631_repeater_lora_ota_no_external_sensors",
    "RAK_4631_room_server_lora_ota_no_external_sensors",
    "RAK_4631_room_server_ethernet_lora_ota_no_external_sensors",
    "RAK_4631_sensor_lora_ota_no_external_sensors",
)
FULL_COMPANION = "RAK_4631_companion_radio_full"
INTERNAL_BOOTLOADER_TARGETS = (
    "RAK_4631_repeater_unified_lora_ota",
    "RAK_4631_repeater_lora_ota_no_external_sensors",
    "RAK_4631_room_server_lora_ota_no_external_sensors",
    "RAK_4631_room_server_ethernet_lora_ota_no_external_sensors",
    "RAK_4631_sensor_lora_ota_no_external_sensors",
)


def read_config(path):
    config = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    config.read(path, encoding="utf-8")
    return config


def has_define(flags, define):
    return re.search(r"(?:^|\s)-D\s*" + re.escape(define) + r"(?:\s|$)", flags) is not None


def run_shell(script):
    # Windows' App Execution Alias can resolve `bash` to the WSL launcher,
    # which accepts the command but loses shell-local assignments. Prefer the
    # Git-for-Windows Bash used by the build scripts there; ordinary POSIX
    # runners keep their PATH-provided Bash.
    git_bash = Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "Git/bin/bash.exe"
    bash = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
    if not bash:
        raise unittest.SkipTest("Bash is required for build recipe checks")
    completed = subprocess.run(
        [bash, "-c", script, "rak15002-recipe-test"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(
            "recipe shell failed with %d:\nstdout:\n%s\nstderr:\n%s"
            % (completed.returncode, completed.stdout, completed.stderr)
        )
    return completed.stdout


def effective_flags(target):
    """Exercise the real overlay order without consulting/running PlatformIO."""
    script = r'''
set -euo pipefail
source ./build_legacy.sh
target=@TARGET@
# Every configuration query is deliberately false in this isolated test. The
# target-specific helper below must supply dynamic room/sensor OTA recipes.
pio_env_option_contains() { return 1; }
PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
PIO_ENV_OTA_BY_NAME[$target]=1
PIO_ENV_SD_OTA_BY_NAME[$target]=0
PIO_ENV_QSPI_OTA_BY_NAME[$target]=0
BUILD_PROFILE_EFFECTIVE=standard
BUILD_PROFILE_FOR_TARGET=standard
SKIP_DECLARED_REDUCTIONS=0
NRF52_OTA_SENSOR_PROFILE=reduced
PLATFORMIO_BUILD_FLAGS=-DKEEP_RECIPE_FLAG=1
PLATFORMIO_BUILD_UNFLAGS=
PLATFORMIO_BUILD_SRC_FILTER=
BUILD_REDUCTIONS=()
if is_companion_radio_full_target "$target"; then
  apply_companion_radio_full_profile "$target" "$target"
else
  apply_lora_ota_override "$target"
fi
apply_lora_ota_no_external_sensors_profile "$target"
apply_rak4631_rak15002_sd_store_profile "$target"
printf '%s\n__RAK15002_UNFLAGS__\n%s' "$PLATFORMIO_BUILD_FLAGS" "$PLATFORMIO_BUILD_UNFLAGS"
'''
    result = run_shell(script.replace("@TARGET@", shlex.quote(target))).split(SEPARATOR)
    if len(result) != 2:
        raise AssertionError("unexpected shell recipe result: %r" % result)
    return result


class Rak4631Rak15002SdRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = read_config(ROOT_CONFIG)
        cls.rak = read_config(RAK_CONFIG)

    def assert_store_defines(self, flags):
        for define in STORE_DEFINES:
            with self.subTest(define=define):
                self.assertTrue(has_define(flags, define), flags)

    def test_root_storage_helpers_pin_the_exact_optional_store(self):
        store = self.root["nrf52_rak15002_sd_store"]
        self.assert_store_defines(store["build_flags"])
        self.assertIn("SdFat-beta.git#a249cfa121e25a96ee14d8813abf01755f1f6a25",
                      store["lib_deps"])

        unified = self.root["nrf52_rak4631_unified_ota"]
        self.assertIn("${nrf52_wisblock_auto_ota.build_flags}", unified["build_flags"])
        self.assertIn("${nrf52_rak15002_sd_store.build_flags}", unified["build_flags"])

    def test_static_repeater_and_full_companion_recipes_use_the_right_helper(self):
        self.assertIn("${nrf52_rak4631_unified_ota.build_flags}",
                      self.rak["env:RAK_4631_repeater_unified_lora_ota"]["build_flags"])
        self.assertIn("${nrf52_rak4631_unified_ota.build_flags}",
                      self.rak["env:RAK_4631_repeater_lora_ota_no_external_sensors"]
                      ["build_flags"])

        companion = self.rak["env:" + FULL_COMPANION]
        self.assertIn("${nrf52_rak15002_sd_store.build_flags}", companion["build_flags"])
        for retained in ("ETHERNET_ENABLED=1", "ETHERNET_USE_RAK13800",
                         "ETHERNET_CLASS=RAK13800EthernetInterface",
                         "MESHCORE_REQUIRES_COMPANION_RADIO_FULL=1"):
            with self.subTest(retained=retained):
                self.assertIn(retained, companion["build_flags"])
        self.assertNotIn("OTA_HEAP_CONTEXT", companion["build_flags"])

        # Generated room/sensor pairs compile their ordinary RAK4631 role
        # environment. Keep the dependency scoped to the identities that can
        # receive the STORE overlay instead of all RAK4631 roles.
        for section, helper in (
                ("env:RAK_4631_repeater_unified_lora_ota",
                 "${nrf52_rak4631_unified_ota.lib_deps}"),
                ("env:RAK_4631_repeater_lora_ota_no_external_sensors",
                 "${nrf52_rak4631_unified_ota.lib_deps}"),
                ("env:RAK_4631_room_server", "${nrf52_rak15002_sd_store.lib_deps}"),
                ("env:RAK_4631_room_server_ethernet",
                 "${nrf52_rak15002_sd_store.lib_deps}"),
                ("env:RAK_4631_sensor", "${nrf52_rak15002_sd_store.lib_deps}"),
                ("env:" + FULL_COMPANION, "${nrf52_rak15002_sd_store.lib_deps}")):
            with self.subTest(section=section):
                self.assertIn(helper,
                              self.rak[section]["lib_deps"])

    def test_legacy_cache_only_seeder_fences_off_adaptive_receiver_flags(self):
        seeder = self.rak["env:RAK_4631_repeater_rak15002_sd_seeder_only"]
        flags = seeder["build_flags"]
        self.assertTrue(has_define(flags, "OTA_RAK15002_SD_CACHE=1"))
        self.assertTrue(has_define(flags, "OTA_HEAP_CONTEXT=1"))
        self.assertNotIn("OTA_RAK15002_SD_STORE", flags)
        unflags = seeder["build_unflags"]
        self.assertIn("${nrf52_base.build_unflags}", unflags)
        for define in ("OTA_RAK_AUTO_STORE=1", "OTA_QSPI_STORE=1",
                       "OTA_RAK15002_SD_STORE=1"):
            with self.subTest(define=define):
                self.assertTrue(has_define(unflags, define), unflags)

    def test_all_canonical_receiver_pair_identities_gain_adaptive_store_flags(self):
        for target in RECEIVERS:
            with self.subTest(target=target):
                flags, unflags = effective_flags(target)
                self.assert_store_defines(flags)
                for define in RECEIVER_NOR_DEFINES:
                    self.assertTrue(has_define(flags, define), flags)
                self.assertNotIn("OTA_SEEDER_ONLY", flags)
                self.assertNotIn("OTA_HEAP_CONTEXT", flags)
                self.assertNotIn("OTA_RAK15002_SD_STORE", unflags)

    def test_full_companion_is_sd_source_only_without_losing_existing_features(self):
        flags, unflags = effective_flags(FULL_COMPANION)
        self.assert_store_defines(flags)
        for define in ("OTA_SEEDER_ONLY=1", "COMPANION_RADIO_FULL=1",
                       "COMPANION_FEATURE_TEMP_RADIO=1", "COMPANION_FEATURE_OTA_CLI=1",
                       "OTA_SHARED_COMPANION_QUEUE=1"):
            with self.subTest(define=define):
                self.assertTrue(has_define(flags, define), flags)
        self.assertNotIn("OTA_RAK_AUTO_STORE", flags)
        self.assertNotIn("OTA_QSPI_STORE", flags)
        self.assertNotIn("OTA_HEAP_CONTEXT", flags)
        self.assertNotIn("OTA_RAK15002_SD_STORE", unflags)
        self.assertNotIn("OTA_SD_STORE=1", unflags)

    def test_noncanonical_or_legacy_targets_do_not_receive_store_overlay(self):
        script = r'''
set -euo pipefail
source ./build_legacy.sh
target=@TARGET@
PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
PLATFORMIO_BUILD_FLAGS=-DKEEP_RECIPE_FLAG=1
apply_rak4631_rak15002_sd_store_profile "$target"
printf '%s' "$PLATFORMIO_BUILD_FLAGS"
'''
        for target in (
                "RAK_4631_repeater", "RAK_4631_room_server", "RAK_4631_sensor",
                "RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors",
                "RAK_4631_repeater_rak15001_slot_c_lora_ota",
                "RAK_4631_repeater_rak15002_sd_seeder_only",
                "RAK_4631_companion_radio_usb"):
            with self.subTest(target=target):
                flags = run_shell(script.replace("@TARGET@", shlex.quote(target)))
                self.assertEqual(flags, "-DKEEP_RECIPE_FLAG=1")

    def test_qualified_repeater_keeps_hybrid_bootloader_linker_request(self):
        script = r'''
set -euo pipefail
source ./build_legacy.sh
target=RAK_4631_repeater_lora_ota_no_external_sensors
PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
PIO_ENV_QSPI_OTA_BY_NAME[$target]=1
PIO_ENV_SD_OTA_BY_NAME[$target]=0
is_lora_ota_build() { return 0; }
pio_env_option_contains() {
  [ "$1" = "$target" ] && [ "$2" = build_flags ] && [ "$3" = OTA_RAK_AUTO_STORE ]
}
unset MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE
apply_nrf52_lora_ota_build_recipe "$target" "$target"
printf '%s' "${MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE:-0}"
'''
        # The pre-script converts this request into OTA_INTERNAL_BOOTLOADER_UPDATE
        # and the nrf52840_s140_v6_mota64 linker. The optional RAK15002 must
        # not demote this already-qualified internal/NOR receiver contract.
        self.assertEqual(run_shell(script), "1")

    def test_dynamic_sd_receivers_keep_the_frozen_hybrid_request_before_overlay(self):
        """Synthetic role aliases must request mota64 before STORE flags exist."""
        inventory = {
            line.strip()
            for line in (ROOT / "tools/mota/nrf52_internal_bootloader_targets.txt")
            .read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertTrue(set(INTERNAL_BOOTLOADER_TARGETS).issubset(inventory))

        script = r'''
set -euo pipefail
source ./build_legacy.sh
target=@TARGET@
# These aliases resolve to an ordinary room/sensor PlatformIO base.  At the
# hybrid-linker decision point it has neither AUTO nor STORE flags yet.
PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
PIO_ENV_QSPI_OTA_BY_NAME[$target]=0
PIO_ENV_SD_OTA_BY_NAME[$target]=0
PIO_ENV_OTA_BY_NAME[$target]=1
NRF52_OTA_SENSOR_PROFILE=reduced
BUILD_PROFILE_EFFECTIVE=standard
BUILD_PROFILE_FOR_TARGET=standard
pio_env_option_contains() { return 1; }
unset MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE
apply_nrf52_lora_ota_build_recipe "$target" "$target"
printf '%s' "${MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE:-0}"
'''
        for target in INTERNAL_BOOTLOADER_TARGETS[2:]:
            with self.subTest(target=target):
                # The pre-script turns this request into the exact existing
                # nrf52840_s140_v6_mota64 linker plus both hybrid flags.  The
                # later optional SD overlay must never weaken that contract.
                self.assertEqual(
                    run_shell(script.replace("@TARGET@", shlex.quote(target))), "1")

    def test_adaptive_store_is_not_classified_as_an_sd_primary_profile(self):
        source = BUILD_SCRIPT.read_text(encoding="utf-8")
        match = re.search(
            r"python3 -c '\n(.*?)\n' \"\$SUPPORTED_PLATFORM_PATTERN\"",
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, "build context parser missing")
        config = [
            ["env:RAK_4631_repeater_lora_ota_no_external_sensors", [
                ["board", "rak4631"],
                ["build_flags", ["-D NRF52_PLATFORM", "-D OTA_RAK_AUTO_STORE=1",
                                  "-D OTA_QSPI_STORE=1", "-D OTA_SD_STORE=1",
                                  "-D OTA_RAK15002_SD_STORE=1"]],
                ["build_src_filter", ["+<helpers/ota/*.cpp>"]],
            ]],
            ["env:RAK_4631_repeater_rak15002_sd_seeder_only", [
                ["board", "rak4631"],
                ["build_flags", ["-D NRF52_PLATFORM", "-D OTA_SD_STORE=1",
                                  "-D OTA_RAK15002_SD_CACHE=1"]],
                ["build_src_filter", ["+<helpers/ota/*.cpp>"]],
            ]],
        ]
        result = subprocess.run(
            [sys.executable, "-c", match.group(1), "ESP32_PLATFORM|NRF52_PLATFORM"],
            input=json.dumps(config), text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = {line.split("\t", 1)[0]: line.split("\t")
                for line in result.stdout.strip().splitlines()}
        receiver = rows["RAK_4631_repeater_lora_ota_no_external_sensors"]
        cache = rows["RAK_4631_repeater_rak15002_sd_seeder_only"]
        self.assertEqual((receiver[4], receiver[5]), ("0", "1"))
        self.assertEqual(cache[4], "1")


if __name__ == "__main__":
    unittest.main()
