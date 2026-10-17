#!/usr/bin/env python3
"""Resolve ESP32 USB recipes with PlatformIO without starting a build.

The board manifests select Serial's SDK class and the build-local SDK patch.
Exercise both selectors after PlatformIO's real flag/unflag processing. This
also checks UART and non-ESP32 recipes, whose transports must not migrate.
"""

from collections import Counter
import json
import os
from pathlib import Path
import re
import runpy
import sys
import unittest

try:
    import SCons.Script
except ImportError:
    core = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio"))
    for path in sorted((core / "packages" / "tool-scons").glob("scons-local-*"), reverse=True):
        sys.path.insert(0, str(path))
    import SCons.Script

from platformio.builder.tools.piobuild import ParseFlagsExtended, ProcessFlags, ProcessUnFlags
from platformio.project.config import ProjectConfig


ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio"))
USB_MODE = "ARDUINO_USB_MODE"
USB_CDC = "ARDUINO_USB_CDC_ON_BOOT"
USB_MACROS = (USB_MODE, USB_CDC)

# These are the eight native-USB families that used TinyUSB before migration.
# G2 already used HWCDC for its observer repeater and KISS roles. New
# Sensor roles inherit the same reviewed backend rather than migrating it.
MIGRATED_BOARD_COUNTS = {
    "heltec_e213": 8,
    "heltec_e290": 9,
    "heltec_t190": 9,
    "heltec_tracker_v1_1": 2,
    "heltec_tracker_v2": 16,
    "station-g2": 12,
    "station-g3-esp32": 16,
    "t_beam_1w": 11,
}
PREVIOUS_G2_HWCDC = {"Station_G2_repeater_observer_mqtt", "Station_G2_kiss_modem"}
LEGACY_DIAGNOSTIC_CDC_OVERRIDES = {
    "heltec_v4_partition_migrator_test_usb_diagnostic",
    "xiao_s3_partition_migrator_test_usb_diagnostic",
}

# Host-managed partition utilities keep application CDC disabled while flash
# tables change. Review them by name, independently of normal firmware roles:
# S3/C3 recover the ROM USB interface, and classic ESP32 uses external UART.
USB_PARTITION_BRIDGES = {
    "esp32_s3_4mb_partition_migrator_usb": ("esp32-s3-devkitc-1", "esp32s3", "uart"),
    "esp32_s3_8mb_partition_migrator_usb": ("esp32-s3-devkitc-1", "esp32s3", "uart"),
    "esp32_s3_16mb_partition_migrator_usb": ("esp32-s3-devkitc-1", "esp32s3", "uart"),
    "esp32_c3_4mb_partition_migrator_usb": ("esp32-c3-devkitm-1", "esp32c3", "uart"),
    "esp32_4mb_partition_migrator_usb": ("esp32dev", "esp32", "classic_uart"),
    "esp32_8mb_partition_migrator_usb": ("esp32dev", "esp32", "classic_uart"),
}

# Pin reviewed family/transport counts rather than copying a machine's
# environment inventory. USB partition bridges are tallied separately above;
# mixed firmware families have explicit role rules below.
UNCHANGED_COUNTS = {
    ("ESP32-S3-WROOM-1-N4", "uart"): 21,
    ("ebyte_eora-s3", "hwcdc"): 8,
    ("esp32-c3-devkitm-1", "uart"): 19,
    ("esp32-c6-devkitm-1", "hwcdc"): 16,
    ("esp32-s3-devkitc-1", "hwcdc"): 22,
    ("esp32-s3-devkitc-1", "uart"): 55,
    ("esp32-s3-zero", "hwcdc"): 25,
    ("esp32dev", "classic_uart"): 5,
    ("esp32doit-devkit-v1", "classic_uart"): 19,
    ("esp32s3box", "hwcdc"): 7,
    ("heltec-rc32", "hwcdc"): 22,
    ("heltec_v4", "hwcdc"): 46,
    ("heltec_v4_migrator", "hwcdc"): 1,
    ("heltec_v4_migrator", "uart"): 4,
    ("heltec_v4_r8", "hwcdc"): 25,
    ("heltec_wifi_lora_32_V2", "classic_uart"): 9,
    ("meshnology_w12", "hwcdc"): 12,
    ("seeed_xiao_esp32c3", "hwcdc"): 7,
    ("seeed_xiao_esp32s3", "hwcdc"): 26,
    ("seeed_xiao_esp32s3_migrator", "hwcdc"): 1,
    ("seeed_xiao_esp32s3_migrator", "uart"): 3,
    ("seeed_wio_tracker_l2", "hwcdc"): 5,
    ("t-deck", "hwcdc"): 4,
    ("t3_s3_v1_x", "hwcdc"): 19,
    ("t_beams3_supreme", "hwcdc"): 9,
    ("thinknode_m7", "uart"): 10,
    ("thinknode_m9", "uart"): 6,
    ("ttgo-lora32-v1", "classic_uart"): 12,
    ("ttgo-t-beam", "classic_uart"): 16,
    ("waveshare_s3_lr1121_migrator", "uart"): 2,
}

# Native-only CI does not install the ESP32 platform's standard manifests.
# Use installed pinned manifests when present, and retain their 6.11.0 USB
# defaults for a test requiring no downloads. Project-owned manifests,
# including every migrated board, are always read from the checkout.
EXTERNAL_BOARD_DEFAULTS = {
    "esp32-c3-devkitm-1": ("esp32c3", []),
    "esp32-c6-devkitm-1": ("esp32c6", []),
    "esp32-s3-devkitc-1": ("esp32s3", ["-DARDUINO_USB_MODE=1"]),
    "esp32dev": ("esp32", []),
    "esp32doit-devkit-v1": ("esp32", []),
    "esp32s3box": ("esp32s3", ["-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=1"]),
    "heltec_wifi_lora_32_V2": ("esp32", []),
    "seeed_xiao_esp32c3": ("esp32c3", ["-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=1"]),
    "seeed_xiao_esp32s3": ("esp32s3", ["-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=1"]),
    "ttgo-lora32-v1": ("esp32", []),
    "ttgo-t-beam": ("esp32", []),
}


def flag_environment(board_flags=(), build_flags=(), build_unflags=()):
    env = SCons.Script.Environment(tools=["gcc"])
    for function in (ParseFlagsExtended, ProcessFlags, ProcessUnFlags):
        env.AddMethod(function)
    # Match ProcessProgramDeps: board, user, then build_unflags.
    env.ProcessFlags(list(board_flags))
    env.ProcessFlags(list(build_flags))
    env.ProcessUnFlags(list(build_unflags))
    return env


def usb_values(env, *, legacy_diagnostic_cdc_override=False):
    values = {name: [] for name in USB_MACROS}
    for definition in env.get("CPPDEFINES", []):
        if isinstance(definition, (tuple, list)):
            name, value = definition[:2]
        else:
            name, value = definition, "1"
        if name in values:
            values[name].append(str(value))
    for name, history in values.items():
        definitions = set(history)
        permitted_override = legacy_diagnostic_cdc_override and name == USB_CDC
        if (len(definitions) > 1 and not permitted_override) or definitions - {"0", "1"}:
            raise ValueError(f"ambiguous USB selector {name}: {sorted(definitions)}")
        # ProcessFlags moves -U options to the end of the compiler command.
        # The SDK patch selector reads CPPDEFINES, so such an override could
        # compile a different class from the one whose source was patched.
        if re.search(r"-U\s*" + re.escape(name) + r"\b", str(env.get("_CPPDEFFLAGS", ""))):
            raise ValueError(f"USB selector undefinition bypasses SDK selection: {name}")
    result = {name: history[-1] if history else "0" for name, history in values.items()}
    if result[USB_CDC] == "1" and not values[USB_MODE]:
        raise ValueError("native CDC requires an explicit USB backend for SDK selection")
    return result


def bool_option(value):
    if isinstance(value, bool):
        return value
    if str(value).lower() not in ("true", "false", "1", "0", "yes", "no"):
        raise ValueError(f"invalid upload boolean: {value}")
    return str(value).lower() in ("true", "1", "yes")


def expected_other_transport(board, name):
    if name in USB_PARTITION_BRIDGES:
        expected_board, _, kind = USB_PARTITION_BRIDGES[name]
        if board != expected_board:
            raise AssertionError(f"USB partition bridge {name} changed board: {board}")
        return kind
    choices = {kind for family, kind in UNCHANGED_COUNTS if family == board}
    if len(choices) == 1:
        return next(iter(choices))
    if board == "esp32-s3-devkitc-1":
        return "hwcdc" if name.startswith(("Heltec_Wireless_Tracker_", "RAK_3112_")) else "uart"
    if board in ("heltec_v4_migrator", "seeed_xiao_esp32s3_migrator"):
        return "hwcdc" if name.endswith("_test_usb_diagnostic") else "uart"
    raise AssertionError(f"unreviewed ESP32 board: {board}")


class Esp32HwcdcRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load tracked recipes explicitly. A developer's platformio.local.ini
        # must not create extra matrix rows or change this regression contract.
        cls.config = ProjectConfig(str(ROOT / "platformio.ini"), parse_extra=False)
        for path in sorted((ROOT / "variants").glob("*/platformio.ini")):
            cls.config.read(str(path), parse_extra=False)
        cls.sdk = runpy.run_path(str(ROOT / "scripts/esp32_usb_session_fix.py"))
        cls.rows = {}
        for section in cls.config.sections():
            if not section.startswith("env:"):
                continue
            build_flags = cls.config.get(section, "build_flags", [])
            board = cls.config.get(section, "board", "")
            manifest_path = ROOT / "boards" / f"{board}.json"
            manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
            esp32 = any(re.search(r"\bESP32_PLATFORM\b", flag) for flag in build_flags)
            if manifest:
                mcu = manifest["build"].get("mcu", "")
                board_flags = manifest["build"].get("extra_flags", [])
            elif esp32:
                mcu, board_flags = EXTERNAL_BOARD_DEFAULTS[board]
                pinned_path = CORE / "platforms" / "espressif32@6.11.0" / "boards" / f"{board}.json"
                if pinned_path.is_file():
                    pinned = json.loads(pinned_path.read_text())
                    mcu = pinned["build"]["mcu"]
                    board_flags = pinned["build"].get("extra_flags", [])
            else:
                mcu, board_flags = "", []
            if isinstance(board_flags, str):
                board_flags = [board_flags]
            env = flag_environment(board_flags, build_flags,
                                   cls.config.get(section, "build_unflags", []))
            try:
                values = usb_values(env, legacy_diagnostic_cdc_override=(
                    section[4:] in LEGACY_DIAGNOSTIC_CDC_OVERRIDES))
            except ValueError as error:
                raise ValueError(f"{section}: {error}") from error
            if not esp32:
                kind = "non_esp32"
            elif mcu == "esp32":
                kind = "classic_uart"
            elif values[USB_CDC] == "0":
                kind = "uart"
            else:
                kind = "hwcdc" if values[USB_MODE] == "1" else "tinyusb"
            cls.rows[section[4:]] = {
                "board": board, "mcu": mcu, "env": env, "values": values,
                "esp32": esp32, "kind": kind, "manifest": manifest,
            }

    def test_all_reviewed_family_roles_select_hwcdc_and_its_sdk_patch(self):
        counts = Counter()
        migrated = set()
        for name, row in self.rows.items():
            if row["board"] not in MIGRATED_BOARD_COUNTS:
                continue
            with self.subTest(environment=name):
                counts[row["board"]] += 1
                self.assertEqual(row["mcu"], "esp32s3")
                self.assertEqual(row["values"], {USB_MODE: "1", USB_CDC: "1"})
                self.assertEqual(row["kind"], "hwcdc")
                self.assertTrue(self.sdk["hwcdc_primary_enabled"](row["env"]))
                self.assertFalse(self.sdk["native_primary_enabled"](row["env"]))
                self.assertEqual(self.config.get(f"env:{name}", "platform"),
                                 "platformio/espressif32@6.11.0")
                self.assertFalse(self.config.get(f"env:{name}", "platform_packages", []))
                self.assertIn("pre:scripts/esp32_usb_session_fix.py",
                              self.config.get(f"env:{name}", "extra_scripts", []))
                if name not in PREVIOUS_G2_HWCDC:
                    migrated.add(name)
        self.assertEqual(counts, MIGRATED_BOARD_COUNTS)
        self.assertTrue(PREVIOUS_G2_HWCDC <= self.rows.keys())
        self.assertEqual(len(migrated), 81)

    def test_migrated_uploads_use_hardware_reset_without_touch_or_port_wait(self):
        for name, row in self.rows.items():
            if row["board"] not in MIGRATED_BOARD_COUNTS:
                continue
            with self.subTest(environment=name):
                upload = row["manifest"]["upload"]
                for option in ("use_1200bps_touch", "wait_for_upload_port"):
                    self.assertIn(option, upload, "native HWCDC upload defaults must be explicit")
                    value = self.config.get(f"env:{name}", f"board_upload.{option}", upload[option])
                    self.assertFalse(bool_option(value), option)
                before_reset = self.config.get(f"env:{name}", "board_upload.before_reset",
                                               upload.get("before_reset", "default_reset"))
                self.assertIn(before_reset, ("default_reset", "usb_reset"))
                self.assertIn(["0x303A", "0x1001"], row["manifest"]["build"]["hwids"])

    def test_no_native_tinyusb_esp32_role_remains_and_sdk_selectors_agree(self):
        counts = Counter()
        for name, row in self.rows.items():
            if not row["esp32"]:
                continue
            with self.subTest(environment=name):
                counts[row["kind"]] += 1
                self.assertNotEqual(row["kind"], "tinyusb")
                self.assertEqual(self.sdk["hwcdc_primary_enabled"](row["env"]),
                                 row["kind"] == "hwcdc")
                self.assertFalse(self.sdk["native_primary_enabled"](row["env"]))
        expected = Counter({"hwcdc": 338, "uart": 120, "classic_uart": 61})
        expected.update(kind for _, _, kind in USB_PARTITION_BRIDGES.values())
        self.assertEqual(counts, expected)

    def test_canonical_full_companions_preserve_their_usb_base_transport(self):
        for name, board in (
            ("Waveshare_S3_LR1121_companion_radio_full", "esp32-s3-zero"),
            ("Wio_Tracker_L2_companion_radio_full", "seeed_wio_tracker_l2"),
        ):
            with self.subTest(environment=name):
                base_name = name.removesuffix("_full") + "_usb"
                self.assertEqual(self.config.get(f"env:{name}", "extends"),
                                 [f"env:{base_name}"])
                for row in (self.rows[name], self.rows[base_name]):
                    self.assertEqual((row["board"], row["mcu"], row["kind"]),
                                     (board, "esp32s3", "hwcdc"))
                    self.assertEqual(row["values"], {USB_MODE: "1", USB_CDC: "1"})
                    self.assertTrue(self.sdk["hwcdc_primary_enabled"](row["env"]))
                    self.assertFalse(self.sdk["native_primary_enabled"](row["env"]))

    def test_usb_partition_bridges_keep_application_usb_disabled(self):
        names = {name for name in self.rows if name.startswith("esp32_")
                 and name.endswith("_partition_migrator_usb")}
        self.assertEqual(names, USB_PARTITION_BRIDGES.keys())
        for name, (board, mcu, kind) in USB_PARTITION_BRIDGES.items():
            with self.subTest(environment=name):
                row = self.rows[name]
                self.assertEqual((row["board"], row["mcu"], row["kind"]), (board, mcu, kind))
                self.assertEqual(row["values"], {USB_MODE: "0", USB_CDC: "0"})
                definitions = dict(definition if isinstance(definition, (tuple, list))
                                   else (definition, 1)
                                   for definition in row["env"].get("CPPDEFINES", []))
                self.assertEqual(str(definitions.get("MESHCORE_MIGRATION_USB")), "1")
                for option in ("ARDUINO_USB_MSC_ON_BOOT", "ARDUINO_USB_DFU_ON_BOOT",
                               "ARDUINO_USB_ON_BOOT"):
                    self.assertEqual(str(definitions.get(option, 0)), "0", option)
                self.assertFalse(self.sdk["hwcdc_primary_enabled"](row["env"]))
                self.assertFalse(self.sdk["native_primary_enabled"](row["env"]))

    def test_all_m5stack_native_usb_roles_select_hwcdc_and_its_startup_patch(self):
        # M5's documented USB-C wiring exposes C6 Serial/JTAG, including when
        # the Companion uses BLE. Resolve real SDK/compiler flags so a UART
        # console regression cannot hide in the unchanged-transport counts.
        names = {name for name in self.rows if name.startswith("M5Stack_Unit_C6L_")}
        self.assertEqual(names, {
            "M5Stack_Unit_C6L_repeater", "M5Stack_Unit_C6L_room_server",
            "M5Stack_Unit_C6L_companion_radio_ble",
            "M5Stack_Unit_C6L_companion_radio_usb", "M5Stack_Unit_C6L_kiss_modem",
        })
        for name in names:
            with self.subTest(environment=name):
                row = self.rows[name]
                self.assertEqual(row["mcu"], "esp32c6")
                self.assertEqual(row["kind"], "hwcdc")
                self.assertEqual(row["values"], {USB_MODE: "1", USB_CDC: "1"})
                self.assertTrue(self.sdk["hwcdc_primary_enabled"](row["env"]))
                self.assertFalse(self.sdk["native_primary_enabled"](row["env"]))
                self.assertEqual(self.config.get(f"env:{name}", "extra_scripts", []).count(
                    "pre:scripts/esp32_usb_session_fix.py"), 1)
                self.assertEqual(self.config.get(f"env:{name}", "platform"),
                                 "https://github.com/pioarduino/platform-espressif32/releases/"
                                 "download/53.03.13-1/platform-espressif32.zip")

    def test_m5stack_selector_removal_reproduces_the_old_uart_backend(self):
        for name in ("M5Stack_Unit_C6L_repeater", "M5Stack_Unit_C6L_room_server",
                     "M5Stack_Unit_C6L_companion_radio_ble", "M5Stack_Unit_C6L_kiss_modem"):
            with self.subTest(environment=name):
                env = flag_environment(build_flags=self.config.get(f"env:{name}", "build_flags"),
                                       build_unflags=["-DARDUINO_USB_MODE=1",
                                                      "-DARDUINO_USB_CDC_ON_BOOT=1"])
                self.assertEqual(usb_values(env), {USB_MODE: "0", USB_CDC: "0"})
                self.assertFalse(self.sdk["hwcdc_primary_enabled"](env))

    def test_uart_existing_hwcdc_and_newer_mcu_recipes_match_reviewed_transports(self):
        counts = Counter()
        for name, row in self.rows.items():
            if not row["esp32"] or row["board"] in MIGRATED_BOARD_COUNTS:
                continue
            with self.subTest(environment=name):
                expected = expected_other_transport(row["board"], name)
                self.assertEqual(row["kind"], expected)
                counts[row["board"], row["kind"]] += 1
        expected = Counter(UNCHANGED_COUNTS)
        expected.update((board, kind) for board, _, kind in USB_PARTITION_BRIDGES.values())
        self.assertEqual(counts, expected)

    def test_non_esp32_recipes_do_not_acquire_esp32_usb_selectors(self):
        checked = set()
        for name, row in self.rows.items():
            if row["esp32"]:
                continue
            with self.subTest(environment=name):
                checked.add(name)
                self.assertEqual(row["values"], {USB_MODE: "0", USB_CDC: "0"})
                self.assertFalse(self.sdk["hwcdc_primary_enabled"](row["env"]))
                self.assertFalse(self.sdk["native_primary_enabled"](row["env"]))
        # Every current non-ESP32 recipe is checked above, including newly
        # added boards. Keep representative architecture coverage explicit
        # rather than pinning an unrelated, ever-growing environment count.
        self.assertTrue({"RAK_3x72_companion_radio_usb",
                         "Xiao_nrf52_companion_radio_usb",
                         "PicoW_companion_radio_usb"} <= checked)

    def test_only_existing_partition_diagnostics_keep_their_cdc_override(self):
        # These two pre-existing diagnostic recipes intentionally select CDC1
        # over a CDC0 recovery board. Keep their compiler/SDK last-definition
        # behavior while rejecting conflicting selectors in normal images.
        for name in LEGACY_DIAGNOSTIC_CDC_OVERRIDES:
            with self.subTest(environment=name):
                row = self.rows[name]
                with self.assertRaises(ValueError):
                    usb_values(row["env"])
                self.assertEqual(row["kind"], "hwcdc")
                self.assertEqual(row["values"], {USB_MODE: "1", USB_CDC: "1"})

    def test_real_unflags_remove_the_old_selector_and_identical_duplicates_are_safe(self):
        env = flag_environment(["-DARDUINO_USB_MODE=0", "-DARDUINO_USB_CDC_ON_BOOT=1"],
                               ["-D ARDUINO_USB_MODE=1", "-DARDUINO_USB_MODE=1"],
                               ["-DARDUINO_USB_MODE=0"])
        self.assertEqual(usb_values(env), {USB_MODE: "1", USB_CDC: "1"})
        self.assertTrue(self.sdk["hwcdc_primary_enabled"](env))
        self.assertFalse(self.sdk["native_primary_enabled"](env))
        env = flag_environment(["-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=0"])
        self.assertEqual(usb_values(env), {USB_MODE: "1", USB_CDC: "0"})
        self.assertFalse(self.sdk["hwcdc_primary_enabled"](env))

    def test_conflicting_missing_and_undefined_backend_flags_are_rejected(self):
        cases = (
            (["-DARDUINO_USB_MODE=0", "-DARDUINO_USB_CDC_ON_BOOT=1"],
             ["-DARDUINO_USB_MODE=1"], []),
            (["-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=1"],
             ["-DARDUINO_USB_CDC_ON_BOOT=0"], []),
            (["-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=1"],
             ["-UARDUINO_USB_MODE"], []),
            (["-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=1"],
             [], ["-DARDUINO_USB_MODE"]),
            (["-DARDUINO_USB_MODE=2", "-DARDUINO_USB_CDC_ON_BOOT=1"], [], []),
        )
        for board_flags, flags, unflags in cases:
            with self.subTest(board=board_flags, flags=flags, unflags=unflags):
                with self.assertRaises(ValueError):
                    usb_values(flag_environment(board_flags, flags, unflags))


if __name__ == "__main__":
    unittest.main()
