"""Contract checks for the shared ESP32 Wi-Fi partition migrator."""

from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "src/helpers/ESP32PartitionMigrationPolicy.h"
SOURCE = ROOT / "examples/esp32_partition_migrator/main.cpp"
PROFILE = ROOT / "variants/heltec_v4/platformio.ini"
MIGRATOR_BOARD = ROOT / "boards/heltec_v4_migrator.json"
XIAO_PROFILE = ROOT / "variants/xiao_s3_wio/platformio.ini"
XIAO_MIGRATOR_BOARD = ROOT / "boards/seeed_xiao_esp32s3_migrator.json"
EXPANDER = ROOT / "examples/partition_expander/main.cpp"
PARTITIONS = Path.home() / ".platformio/packages/framework-arduinoespressif32/tools/partitions/default_16MB.csv"
PARTITIONS_8MB = Path.home() / ".platformio/packages/framework-arduinoespressif32/tools/partitions/default_8MB.csv"
PARTITIONS_4MB = ROOT / "variants/dual_ota_full_4MB.csv"
GENERATOR = Path.home() / ".platformio/packages/framework-arduinoespressif32/tools/gen_esp32part.py"


class HeltecV4PartitionMigratorTest(unittest.TestCase):
    def test_profile_is_a_small_legacy_slot_image_with_its_own_wifi_uploader(self):
        profile = PROFILE.read_text(encoding="utf-8")
        start = profile.index("[env:heltec_v4_partition_migrator]")
        section = profile[start:]
        self.assertIn("board = heltec_v4_migrator", section)
        self.assertIn("board_build.partitions = default.csv", section)
        self.assertIn("+<../examples/esp32_partition_migrator>", section)
        self.assertIn("file://arch/esp32/AsyncElegantOTA", section)

        xiao_profile = XIAO_PROFILE.read_text(encoding="utf-8")
        self.assertIn("[env:xiao_s3_partition_migrator]", xiao_profile)
        self.assertIn("board = seeed_xiao_esp32s3_migrator", xiao_profile)
        self.assertIn("[env:xiao_s3_partition_legacy_seed]", xiao_profile)
        self.assertIn("board_build.partitions = default.csv", xiao_profile)
        xiao_board = XIAO_MIGRATOR_BOARD.read_text(encoding="utf-8")
        self.assertIn('"flash_size": "8MB"', xiao_board)

    def test_migration_stages_identity_and_self_copies_before_table_switch(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("bool stageLegacyIdentity()", source)
        self.assertIn("bool restoreStagedIdentity()", source)
        self.assertIn("/identity/_main.id", source)
        self.assertIn('Preferences migration_nvs', source)
        self.assertIn('kMigrationIdentityPendingKey[] = "id-pending"', source)
        self.assertIn("migration::canMigrateGeneric", source)
        self.assertIn("migration::isTargetLayout", source)
        self.assertIn("migration::targetForFlash", source)
        self.assertIn("copyAndVerify(*running", source)
        self.assertIn("plan->layout.app0_address", source)
        self.assertIn("running->size > plan->layout.app0_size", source)
        migration_body = source[source.index("void runMigration()") :]
        self.assertLess(migration_body.index("stageLegacyIdentity()"),
                        migration_body.index("copyAndVerify(*running"))
        self.assertLess(migration_body.index("esp_ota_set_boot_partition(\n      boot_in_app0 ? refs.app0 : refs.app1)"),
                        migration_body.index("publishExpandedPartitionTable(*plan)"))
        self.assertLess(migration_body.index("stageLegacyConfig()"),
                        migration_body.index("copyAndVerify(*running"))
        self.assertLess(migration_body.index("stageExpanderHandoff()"),
                        migration_body.index("publishExpandedPartitionTable(*plan)"))
        setup_body = source[source.index("void setup()") :]
        self.assertIn("restoreStagedIdentity()", setup_body)
        self.assertIn("restoreStagedConfig()", setup_body)
        self.assertIn("verifyExpandedIdentityFile()", setup_body)
        self.assertIn('kMigrationConfigRestoredKey[] = "cfg-restored"', source)
        self.assertIn("nvs.putBool(kMigrationConfigRestoredKey, true)", source)
        self.assertIn("resumeLegacyOtaReceiver(geometry)", setup_body)
        self.assertLess(setup_body.index("restoreStagedIdentity()"),
                        setup_body.index("restoreStagedConfig()"))
        self.assertLess(setup_body.index("restoreStagedConfig()"),
                        setup_body.index("resumeLegacyOtaReceiver(geometry)"))
        self.assertIn("validOtherLoRaFirmware(*running, *old_receiver)", migration_body)
        self.assertIn("copyAndVerify(*refs.app1", migration_body)
        self.assertIn("stageResumeSlot(resume.resume_slot)", migration_body)
        self.assertIn("AsyncElegantOTA.begin(&server)", source)
        self.assertIn("MeshCore-Migrate", source)
        self.assertIn("DRAM_ATTR esp_partition_t copy_destination", source)
        self.assertIn("partition_table_flash_hooks.region_protected = partitionTableRegionProtected", source)
        self.assertIn("chip->os_func = &partition_table_flash_hooks", source)
        self.assertIn("esp_flash_erase_region(chip", source)
        self.assertIn("esp_flash_write(chip", source)
        self.assertNotIn("#include <esp_flash_internal.h>", source)
        for path in ("/com_prefs", "/node_prefs", "/s_contacts",
                     "/s_login_replay", "/radio_profiles", "/regions2",
                     "/com_prefs.bak", "/radio_profiles.bak", "/prefs.json",
                     "/management", "/ota_speed", "/bsec_state.bin"):
            self.assertIn(path, source)

        board = MIGRATOR_BOARD.read_text(encoding="utf-8")
        self.assertIn('"-DARDUINO_USB_CDC_ON_BOOT=0"', board)

    def test_embedded_partition_prefixes_match_arduino_default_targets(self):
        self.assertTrue(PARTITIONS.is_file(), "Arduino partition CSV is required")
        self.assertTrue(PARTITIONS_8MB.is_file(), "Arduino 8 MiB partition CSV is required")
        self.assertTrue(GENERATOR.is_file(), "Arduino partition generator is required")
        text = HEADER.read_text(encoding="utf-8")
        block = text[text.index("kExpandedPartitionTablePrefix[]"):
                     text.index("};", text.index("kExpandedPartitionTablePrefix[]"))]
        embedded = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-F]{2})", block))
        self.assertEqual(0xE0, len(embedded))
        with tempfile.TemporaryDirectory(prefix="meshcore-v4-partition-") as directory:
            generated = Path(directory) / "default16.bin"
            subprocess.run([sys.executable, str(GENERATOR), str(PARTITIONS), str(generated)],
                           check=True, capture_output=True, text=True)
            self.assertEqual(embedded, generated.read_bytes()[:len(embedded)])
            generated_8mb = Path(directory) / "default8.bin"
            subprocess.run([sys.executable, str(GENERATOR), str(PARTITIONS_8MB), str(generated_8mb)],
                           check=True, capture_output=True, text=True)
            start_8mb = text.index("kExpanded8MBPartitionTablePrefix[]")
            block_8mb = text[start_8mb:text.index("};", start_8mb)]
            embedded_8mb = bytes(int(value, 16)
                                 for value in re.findall(r"0x([0-9A-F]{2})", block_8mb))
            self.assertEqual(embedded_8mb, generated_8mb.read_bytes()[:len(embedded_8mb)])
            generated_4mb = Path(directory) / "default4.bin"
            subprocess.run([sys.executable, str(GENERATOR), str(PARTITIONS_4MB),
                            str(generated_4mb)], check=True, capture_output=True, text=True)
            start_4mb = text.index("kExpanded4MBPartitionTablePrefix[]")
            block_4mb = text[start_4mb:text.index("};", start_4mb)]
            embedded_4mb = bytes(int(value, 16)
                                 for value in re.findall(r"0x([0-9A-F]{2})", block_4mb))
            self.assertEqual(0xC0, len(embedded_4mb))
            self.assertEqual(embedded_4mb, generated_4mb.read_bytes()[:len(embedded_4mb)])

    def test_expander_pins_full_target_and_reboots_after_apply(self):
        expander = EXPANDER.read_text(encoding="utf-8")
        self.assertIn("MOTA_MIGRATION_TARGET_ID", expander)
        self.assertIn("manifest.is_full()", expander)
        self.assertIn("loadPrimaryRadio(profile)", expander)
        self.assertIn("returnToVerifiedFull()", expander)
        self.assertIn("hasExpanderHandoff()", expander)
        self.assertIn("hasVerifiedConfigHandoff()", expander)
        self.assertIn("ota.apply_fetched(message)", expander)
        self.assertIn("ota_reboot_to_apply()", expander)
        self.assertLess(expander.index("ota.apply_fetched(message)"),
                        expander.index("ota_reboot_to_apply()"))


if __name__ == "__main__":
    unittest.main()
