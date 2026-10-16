"""No-hardware qualification of exact Waveshare observer migration recipes."""
import configparser
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_esp32_partition_migration import build_steps
from package_esp32_partition_migration import BOARDS, package_board, readme
from motalib import hardware_id_for_env, target_id_for_env

KEYS = ("waveshare-s3-lr1121-observer-repeater",
        "waveshare-s3-lr1121-observer-room-server")


class WaveshareMigrationRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configparser.ConfigParser(interpolation=None,
                                               inline_comment_prefixes=(";",))
        cls.config.read([ROOT / "platformio.ini", *sorted((ROOT / "variants").glob("*/platformio.ini"))],
                        encoding="utf-8")

    def expanded(self, section, field):
        config = self.config[section]
        if field not in config and "extends" in config:
            return self.expanded(config["extends"], field)
        value = config.get(field, "")
        return re.sub(r"\$\{([^}]+)\}",
                      lambda m: self.expanded(*m[1].rsplit(".", 1)), value)

    def test_only_observer_roles_get_exact_self_hosted_routes(self):
        waveshare = {key: spec for key, spec in BOARDS.items()
                     if spec["target"].startswith("Waveshare_")}
        self.assertEqual(set(KEYS), set(waveshare))
        for key, role in zip(KEYS, ("repeater", "room-server")):
            with self.subTest(key=key):
                spec = BOARDS[key]
                self.assertEqual(role, spec["role"])
                self.assertEqual(4 * 1024 * 1024, spec["flash_bytes"])
                self.assertEqual(0x1F0000, spec["slot_bytes"])
                self.assertEqual(0x200000, spec["slot1_address"])
                self.assertEqual((0x3F0000, 0x10000),
                                 (spec["spiffs_address"], spec["spiffs_bytes"]))
                self.assertEqual("min_spiffs.csv", spec["source_partition_table"])
                self.assertEqual(spec["wifi_bridge"], spec["expander_bridge"])
                self.assertEqual(spec["lora_bridge"], spec["expander_bridge"])
                self.assertEqual("esp32_s3_4mb_partition_migrator_usb", spec["usb_bridge"])
                self.assertEqual(9, spec["usb_chip"])
                self.assertEqual("Waveshare_S3_LR1121", hardware_id_for_env(spec["target"]))
                self.assertEqual("variants/dual_ota_full_4MB.csv",
                                 self.expanded("env:" + spec["target"], "board_build.partitions"))
        for role in ("repeater", "room_server", "companion_radio_usb", "companion_radio_ble"):
            self.assertEqual("min_spiffs.csv", self.expanded(
                "env:Waveshare_S3_LR1121_" + role, "board_build.partitions"))

    def test_expanders_reuse_real_lr1121_wiring_without_native_usb_or_legacy_handoff(self):
        board = json.loads((ROOT / "boards/waveshare_s3_lr1121_migrator.json").read_text())
        self.assertIn("-D ARDUINO_USB_CDC_ON_BOOT=0", board["build"]["extra_flags"])
        self.assertEqual("dio", board["build"]["flash_mode"])
        self.assertEqual("4MB", board["upload"]["flash_size"])
        for key in KEYS:
            spec = BOARDS[key]
            section = "env:" + spec["expander_bridge"]
            flags = self.expanded(section, "build_flags")
            for name, value in (("P_LORA_NSS", 42), ("P_LORA_DIO_1", 38),
                                ("P_LORA_RESET", 39), ("P_LORA_BUSY", 41),
                                ("P_LORA_SCLK", 40), ("P_LORA_MISO", 46), ("P_LORA_MOSI", 45)):
                self.assertIn(f"-D {name}={value}", flags)
            for flag in ("RADIO_CLASS=CustomLR1121", "WRAPPER_CLASS=CustomLR1121Wrapper",
                         "LR11X0_DIO3_TCXO_VOLTAGE=1.8", "LR1121_FORCE_HP_PA",
                         "MESHCORE_MIGRATION_STRICT_4MB=1", "OTA_FETCH_PIPELINE=4"):
                self.assertIn("-D " + flag, flags)
            self.assertIn(f"-D MOTA_MIGRATION_TARGET_ID=0x{target_id_for_env(spec['target']):08x}", flags)
            self.assertNotIn("MESHCORE_MIGRATION_RESUME_OTA", flags)
            self.assertEqual("waveshare_s3_lr1121_migrator", self.expanded(section, "board"))
            self.assertEqual("default.csv", self.expanded(section, "board_build.partitions"))
            self.assertEqual("1310720", self.expanded(section, "board_upload.maximum_size"))
            self.assertIn("../examples/partition_expander/*.cpp",
                          self.expanded(section, "build_src_filter"))

    def test_builds_both_exact_full_apps_then_each_wireless_bridge_only_once(self):
        steps = build_steps(list(KEYS), "v1.18.0-test", "usa-cascadia", "cascade", 2)
        self.assertEqual([True, True, False, False, False], [full for _, full in steps])
        self.assertEqual([BOARDS[key]["target"] for key in KEYS],
                         [command[3] for command, full in steps if full])
        self.assertEqual({BOARDS[key]["expander_bridge"] for key in KEYS}
                         | {"esp32_s3_4mb_partition_migrator_usb"},
                         {command[3] for command, full in steps if not full})
        for key in KEYS:
            guide = readme(key, BOARDS[key], "v1.18.0-test", "01234567", full_mota_blocks=960)
            self.assertIn("self-hosted, configuration-preserving", guide)
            self.assertIn("either old OTA slot", guide)
            self.assertIn("saved primary radio profile", guide)
            self.assertIn("has not been physically qualified", guide)
            self.assertIn("not power-loss atomic", guide)
            self.assertNotIn("bridge must be installed in old slot B", guide)

    def test_packager_rejects_a_wrong_final_filesystem_before_reading_bridges(self):
        spec = BOARDS[KEYS[0]]
        with tempfile.TemporaryDirectory(prefix="waveshare-migration-plan-") as temporary:
            directory = Path(temporary)
            stem = spec["target"] + "-full-usa-v1.18.0-test-01234567"
            app = b"\xe9test-app"
            (directory / (stem + ".bin")).write_bytes(app)
            (directory / (stem + ".capabilities.json")).write_text(json.dumps({
                "target": spec["target"], "build_profile": "full",
                "platform": "ESP32_PLATFORM", "ota_update_verified": True,
            }))
            merged = bytearray(b"\xff" * 0x10000 + app)
            for index, (name, ptype, subtype, address, size) in enumerate((
                ("nvs", 1, 2, 0x9000, 0x5000), ("otadata", 1, 0, 0xE000, 0x2000),
                ("app0", 0, 16, 0x10000, 0x1F0000), ("app1", 0, 17, 0x200000, 0x1F0000),
                ("spiffs", 1, 130, 0x3E0000, 0x20000),
            )):
                struct.pack_into("<HBBII16sI", merged, 0x8000 + 32 * index,
                                 0x50AA, ptype, subtype, address, size, name.encode(), 0)
            (directory / (stem + "-merged.bin")).write_bytes(merged)
            with patch("package_esp32_partition_migration.validate_package"):
                with self.assertRaisesRegex(ValueError, "SPIFFS differs"):
                    package_board(KEYS[0], spec, directory, directory / "output",
                                  "v1.18.0-test", "01234567", directory / "utilities")


if __name__ == "__main__":
    unittest.main()
