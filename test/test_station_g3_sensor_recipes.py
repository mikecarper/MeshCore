#!/usr/bin/env python3
"""Host contracts for Station G3 boot recipes and each Sensor RF slot.

Resolve tracked source recipes without starting PlatformIO, building firmware,
flashing hardware, or changing a release catalog. These fixtures check routing
and metadata; linked-image and RAM qualification still require real builds.
"""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from test_upstream_observer_recipes import Recipes
from test_esp32_full_partition import apply_policy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_mesh_america_release as catalogs
import generate_picker_controls as controls
from package_cascade_release import category
from package_esp32_partition_migration import BOARDS

SLOTS = {
    "Station_G3_ESP32": (48, 11, 21, 47, 12, 14, 13, 10),
    "Station_G3_ESP32_r2": (2, 43, 44, 1, 39, 41, 40, 42),
}
RADIO_PINS = (
    "P_LORA_DIO_1", "P_LORA_NSS", "P_LORA_RESET", "P_LORA_BUSY",
    "P_LORA_SCLK", "P_LORA_MISO", "P_LORA_MOSI", "P_PRIMARY_LNA_EN",
)
SENSORS = tuple(slot + "_sensor" for slot in SLOTS)
SOURCE = "a" * 40
FAMILY = "v1.17.1.9-halo-keymind-cascade-dev-" + SOURCE[:8]


def definitions(flags):
    """Preserve definition histories so a second slot's flags cannot shadow pins."""
    result = {}
    for name, value in re.findall(r"(?m)^\s*-D\s+(\w+)=([^\s;]+)", flags):
        result.setdefault(name, []).append(value)
    return result


def manifest(target):
    profile = target + "-full-logging-ota"
    stem = profile + "-" + FAMILY
    capabilities = ["profile.full", "ota.update.lora", "ota.update.wifi"]
    return dict(target=target, artifact_target=profile, platformio_env=target,
                platform="ESP32_PLATFORM", build_profile="full", verified=True,
                ota_update_verified=True, source_commit=SOURCE,
                capabilities=capabilities, ota_update_methods=["lora", "wifi"],
                verification=[dict(capability=capability, present=True,
                                   source="linked image") for capability in capabilities],
                files=[stem + suffix for suffix in (
                    ".bin", "-merged.bin", ".capabilities.json", ".memory.json")])


class StationG3SensorRecipesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = Recipes()

    def assert_dio_boot_recipes(self, recipes):
        board = json.loads((ROOT / "boards/station-g3-esp32.json").read_text())
        self.assertEqual(board["build"]["flash_mode"], "dio")
        self.assertEqual(board["build"]["arduino"]["memory_type"], "qio_opi")
        targets = [section for section in recipes.config.sections()
                   if section.startswith("env:Station_G3_ESP32_")]
        self.assertGreaterEqual(len(targets), 16)
        for slot in SLOTS:
            for role in ("repeater", "room_server", "sensor",
                         "repeater_observer_mqtt", "room_server_observer_mqtt"):
                self.assertIn("env:" + slot + "_" + role, targets)
        for role in ("companion_radio_usb", "companion_radio_ble",
                     "companion_radio_wifi", "companion_radio_full", "kiss_modem"):
            self.assertIn("env:Station_G3_ESP32_" + role, targets)
        for section in targets:
            with self.subTest(target=section):
                self.assertEqual(recipes.value(section, "board"), "station-g3-esp32")
                flash_mode = recipes.value(section, "board_build.flash_mode")
                self.assertEqual(flash_mode or board["build"]["flash_mode"], "dio")
                memory_type = recipes.value(section, "board_build.arduino.memory_type")
                self.assertEqual(memory_type or board["build"]["arduino"]["memory_type"], "qio_opi")
                psram_type = recipes.value(section, "board_build.psram_type")
                self.assertIn(psram_type, ("", "opi"))

        for target in ("Station_G3_ESP32_repeater", "Station_G3_ESP32_r2_repeater",
                       "Station_G3_ESP32_companion_radio_full"):
            section = "env:" + target
            values = {
                "build.mcu": board["build"]["mcu"],
                "build.flash_mode": recipes.value(section, "board_build.flash_mode")
                                    or board["build"]["flash_mode"],
                "build.arduino.memory_type": board["build"]["arduino"]["memory_type"],
                "build.partitions": recipes.value(section, "board_build.partitions"),
                "upload.flash_size": board["upload"]["flash_size"],
            }
            with self.subTest(full_target=target):
                # build.sh injects this existing policy for Full promotion;
                # ordinary tracked recipes do not list it in extra_scripts.
                full, _ = apply_policy(values, companion="companion_radio" in target)
                self.assertEqual(full.values["build.flash_mode"], "dio")
                self.assertEqual(full.values["build.arduino.memory_type"], "qio_opi")

    def test_all_g3_roles_inherit_dio_without_changing_psram_or_full_policy(self):
        self.assert_dio_boot_recipes(self.recipes)

    def test_nested_role_override_cannot_silently_restore_qio(self):
        recipes = Recipes()
        recipes.config.set("env:Station_G3_ESP32_r2_sensor", "board_build.flash_mode", "qio")
        # Exercise resolved inheritance, not a textual manifest-only assertion.
        # subTest records failures instead of raising, so collect this control
        # with an independent TestResult whose failure must be observable.
        control = StationG3SensorRecipesTests(
            "test_all_g3_roles_inherit_dio_without_changing_psram_or_full_policy")
        control.recipes = recipes
        result = unittest.TestResult()
        control.run(result)
        self.assertEqual(len(result.failures), 1)
        self.assertFalse(result.errors)
        self.assertIn("'qio' != 'dio'", result.failures[0][1])

    def test_each_sensor_inherits_its_own_slot_and_only_one_pin_set(self):
        text = (ROOT / "variants/station_g3_esp32/platformio.ini").read_text()
        for slot, pins in SLOTS.items():
            section = "env:" + slot + "_sensor"
            with self.subTest(slot=slot):
                self.assertEqual(text.count("[" + section + "]"), 1)
                self.assertEqual(self.recipes.config.get(section, "extends"), slot)
                values = definitions(self.recipes.value(section, "build_flags"))
                for name, pin in zip(RADIO_PINS, pins):
                    self.assertEqual(values[name], [str(pin)], name)
                for name, pin in {"P_PA1_EN": 9, "PIN_BOARD_SDA": 5,
                                  "PIN_BOARD_SCL": 6, "PIN_USER_BTN": 38,
                                  "PIN_GPS_RX": 15, "PIN_GPS_TX": 7}.items():
                    self.assertEqual(values[name], [str(pin)], name)
                all_pins = (*pins, 9, 5, 6, 38, 15, 7)
                self.assertEqual(len(all_pins), len(set(all_pins)))
                self.assertEqual(values["P_PRIMARY_LNA_EN_ACTIVE"], ["LOW"])
                self.assertEqual(values["P_PA1_EN_ACTIVE"], ["HIGH"])

    def test_sensor_entrypoints_board_ui_and_ota_sources_are_selected(self):
        for target in SENSORS:
            section = "env:" + target
            with self.subTest(target=target):
                for path in ("../examples/simple_sensor/main.cpp",
                             "../examples/simple_sensor/SensorMesh.cpp",
                             "../examples/simple_sensor/UITask.cpp",
                             "../examples/simple_sensor/TimeSeriesData.cpp",
                             "../variants/station_g3_esp32/target.cpp",
                             "../variants/station_g3_esp32/StationG3Board.cpp",
                             "../variants/station_g3_esp32/LoRaFEMControl.cpp",
                             "helpers/ui/SH1106Display.cpp", "helpers/ui/MomentaryButton.cpp",
                             "helpers/sensors/EnvironmentSensorManager.cpp",
                             "helpers/esp32/WiFiOtaSeeder.cpp", "helpers/ota/OtaTinf.c"):
                    self.assertTrue(self.recipes.includes(section, path), path)
                for role in ("simple_repeater", "simple_room_server", "companion_radio", "kiss_modem"):
                    self.assertFalse(self.recipes.includes(section, f"../examples/{role}/main.cpp"))
                flags = self.recipes.value(section, "build_flags")
                for flag in ("ESP32_PLATFORM", "ENABLE_OTA=1", "OTA_FLASH_STORE=1",
                             "ENV_INCLUDE_GPS=1", "ENV_INCLUDE_INA219=1",
                             "ENV_INCLUDE_BME280=1", "DISPLAY_CLASS=SH1106Display"):
                    self.assertIn(flag, flags)
                self.assertNotIn("-UENV_INCLUDE_", flags)

    def test_native_usb_partition_and_qualification_hooks_match_existing_slot(self):
        board = json.loads((ROOT / "boards/station-g3-esp32.json").read_text())
        self.assertEqual(board["build"]["mcu"], "esp32s3")
        self.assertEqual(board["upload"]["flash_size"], "16MB")
        self.assertIn("-DARDUINO_USB_MODE=1", board["build"]["extra_flags"])
        self.assertIn("-DARDUINO_USB_CDC_ON_BOOT=1", board["build"]["extra_flags"])
        for target in SENSORS:
            section = "env:" + target
            with self.subTest(target=target):
                self.assertEqual(self.recipes.value(section, "board"), "station-g3-esp32")
                self.assertEqual(self.recipes.value(section, "board_build.partitions"),
                                 "default_16MB.csv")
                for hook in ("esp32_usb_session_fix.py", "meshcore_image_identity.py",
                             "tools/mota/pio_endf.py", "check_firmware_ram.py",
                             "check_esp32_dram.py"):
                    self.assertIn(hook, self.recipes.value(section, "extra_scripts"))
                self.assertIn("file://arch/esp32/AsyncElegantOTA",
                              self.recipes.value(section, "lib_deps"))

    def test_release_discovery_and_full_matrix_keep_both_sensor_identities(self):
        # The fixture rows come from the source-resolved recipes above. Any
        # accidental PIO or compilation call fails before doing work.
        program = r'''
set -euo pipefail
source build.sh
pio() { echo "unexpected PlatformIO" >&2; return 99; }
prepare_esp32_arduino3_framework() { return 99; }
run_pio_with_size_detection() { return 99; }
collect_build_artifacts() { return 99; }
SUPPORTED_PIO_ENVS=(Station_G3_ESP32_sensor Station_G3_ESP32_r2_sensor)
for target in "${SUPPORTED_PIO_ENVS[@]}"; do
  PIO_ENV_PLATFORM_BY_NAME[$target]=ESP32_PLATFORM
  PIO_ENV_BOARD_BY_NAME[$target]=station-g3-esp32
  PIO_ENV_FULL_BUILD_BY_NAME[$target]=1
  PIO_ENV_OTA_BY_NAME[$target]=1
  PIO_ENV_FULL_WIFI_OTA_BY_NAME[$target]=1
  PIO_ENV_MQTT_BY_NAME[$target]=0
  is_sensor_role_target "$target"
  is_esp32_full_only_bulk_target "$target"
  ! is_esp32_partition_migration_full_target "$target"
  [ "$(get_exact_identity_full_pio_env "$target")" = "$target" ]
done
[ "$(get_pio_envs_for_variant_role sensor)" = "$(printf '%s\n' "${SUPPORTED_PIO_ENVS[@]}")" ]
REQUIRE_OTA_UPDATES=0
calls=()
run_logged_build_targets() {
  [ "$BUILD_PROFILE_EFFECTIVE:$ESP32_FULL_BUILD:$FIRMWARE_FILENAME_INFIX" = full:1:full-logging ]
  for target in "$@"; do is_lora_ota_build "$target"; done
  calls+=("$*")
}
run_logging_matrix_build_targets "${SUPPORTED_PIO_ENVS[@]}" >/dev/null
[ "${#calls[@]}" = 1 ]
[ "${calls[0]}" = "${SUPPORTED_PIO_ENVS[*]}" ]
'''
        result = subprocess.run(["bash", "-c", program], cwd=ROOT,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_full_sensor_packages_are_not_legacy_partition_migrations(self):
        migrated = {spec["target"] for spec in BOARDS.values()}
        for target in SENSORS:
            with self.subTest(target=target):
                self.assertNotIn(target, migrated)
                self.assertEqual(category(dict(manifest=manifest(target))), "full-profiles")

    def test_picker_parses_distinct_slot_hardware(self):
        profiles = catalogs.target_profiles(target + "-full-logging-ota" for target in SENSORS)
        for slot in SLOTS:
            profile = profiles[slot + "_sensor-full-logging-ota"]
            with self.subTest(slot=slot):
                self.assertEqual(profile["hardware"], slot)
                self.assertEqual(profile["role"], "sensor")
                self.assertEqual(profile["feature"], "full")

    def test_both_mesh_america_catalogs_map_each_sensor_to_its_own_card(self):
        rows = [manifest(target) for target in SENSORS]
        records = [(row, "full-profiles-" + FAMILY,
                    [Path(name) for name in row["files"] if name.endswith(".bin")])
                   for row in rows]
        plan = dict(version="1.17.1.9", source=SOURCE, groups=[dict(key="full-profiles")])
        names = {"Station_G3_ESP32": "UnitEng/BQ Voyage Station G3",
                 "Station_G3_ESP32_r2": "UnitEng/BQ Voyage Station G3 ESP32 R2"}
        for filename in ("keymind-cascade-v1.16.0-provider.json",
                         "keymind-cascade-logging-v1.16.0-provider.json"):
            catalog = json.loads((ROOT / "mesh-america" / filename).read_text())
            targets = {row["artifact_target"] for row in rows}
            targets.update(catalogs.identity(file["name"]) for device in catalog["device"]
                           for firmware in device["firmware"]
                           for version in firmware["version"].values() for file in version["files"])
            result = catalogs.generate(catalog, plan, FAMILY, records,
                                       catalogs.target_profiles(targets), "mikecarper/MeshCore")
            self.assertEqual(len(result["device"]), 2)
            for slot, name in names.items():
                with self.subTest(catalog=filename, slot=slot):
                    card, = [device for device in result["device"] if device["name"] == name]
                    firmware, = card["firmware"]
                    self.assertEqual(firmware["role"], "sensor")
                    self.assertEqual(firmware["subTitle"], slot + "_sensor-full-logging-ota - Full profile")
                    files = firmware["version"][FAMILY]["files"]
                    self.assertEqual({file["type"] for file in files}, {"flash-wipe", "flash-update"})
                    self.assertTrue(all(file["name"].startswith(slot + "_sensor-") for file in files))

    def test_controls_expose_qualified_ota_without_inventing_a_sensor_web_portal(self):
        rows = [manifest(target) for target in SENSORS]
        config = [("env:" + target, [("build_flags", self.recipes.value(
            "env:" + target, "build_flags").splitlines())]) for target in SENSORS]
        with tempfile.TemporaryDirectory(prefix="mesh-g3-sensor-controls-") as temporary:
            stage = Path(temporary)
            for group in ("companion", "full-profiles"):
                (stage / group).mkdir()
            (stage / "release-plan.json").write_text(json.dumps(dict(source=SOURCE, groups=[
                dict(key="companion", tag=FAMILY), dict(key="full-profiles", tag="full-profiles-" + FAMILY)])))
            (stage / "companion/TARGET-MANIFEST.json").write_text("[]")
            (stage / "full-profiles/TARGET-MANIFEST.json").write_text(json.dumps(rows))
            generated = controls.generate(stage, config)
        self.assertEqual(set(generated["profiles"]), {target + "-full-logging" for target in SENSORS})
        for target in SENSORS:
            profile = generated["profiles"][target + "-full-logging"]
            with self.subTest(target=target):
                self.assertEqual(profile["otaRole"], "lora-receiver")
                self.assertEqual(profile["updateMethods"], ["lora", "wifi"])
                self.assertTrue(profile["gps"])
                self.assertTrue(profile["display"])
                self.assertTrue(profile["femRx"])
                self.assertTrue(profile["femTx"])
                self.assertFalse(profile["webconfig"])
                self.assertNotIn(target, generated["partitionMigrations"])


if __name__ == "__main__":
    unittest.main()
