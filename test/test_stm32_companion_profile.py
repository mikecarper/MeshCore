#!/usr/bin/env python3
"""Guard the headless RAK Companion's size recipe and deployed storage contract.

The stm32-companion-smoke job also links the actual image. These checks prevent
fixing its size limit by silently shrinking storage or Companion capacities.
"""

import configparser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class STM32CompanionProfileTest(unittest.TestCase):
    def setUp(self):
        self.config = configparser.ConfigParser(
            interpolation=None, inline_comment_prefixes=(";",))
        self.config.read(ROOT / "variants/rak3x72/platformio.ini")
        self.companion = self.config["env:RAK_3x72_companion_radio_usb"]

    def test_unused_timer_wrappers_are_removed_only_from_headless_companion(self):
        for flag in ("RADIOLIB_TONE_UNSUPPORTED", "HAL_TIM_MODULE_ONLY"):
            self.assertRegex(self.companion["build_flags"], r"-D\s+" + flag + r"\b")
            for section in self.config.sections():
                if section != self.companion.name:
                    self.assertNotIn(flag, self.config[section].get("build_flags", ""))

    def test_flash_and_filesystem_boundaries_do_not_move(self):
        self.assertNotIn("board_upload.maximum_size", self.companion)
        self.assertEqual(self.config["rak3x72"].getint("board_upload.maximum_size"),
                         229376)
        self.assertNotIn("LFS_FLASH_TOTAL_SIZE", self.companion["build_flags"])
        self.assertNotIn("LFS_FLASH_TOTAL_SIZE", self.config["rak3x72"]["build_flags"])
        header = (ROOT / "src/helpers/stm32/InternalFileSystem.h").read_text()
        self.assertRegex(header, r"#define\s+LFS_FLASH_TOTAL_SIZE\s+\(16\s*\*\s*2048\)")
        self.assertEqual(229376 + 16 * 2048, 256 * 1024)

    def test_companion_capacities_and_interfaces_are_retained(self):
        flags = self.companion["build_flags"]
        for definition in ("MAX_CONTACTS=100", "MAX_GROUP_CHANNELS=8",
                           "ENABLE_USB_INTERFACE"):
            self.assertRegex(flags, r"-D\s+" + re.escape(definition) + r"\b")
        for flags in (flags, self.config["rak3x72"]["build_flags"]):
            self.assertNotRegex(flags, r"COMPANION_FEATURE_\w+=0")
            self.assertNotRegex(flags, r"MESH_ENABLE_\w+=0")
        self.assertIn("+<../examples/companion_radio/*.cpp>",
                      self.companion["build_src_filter"])
        self.assertIn("-Oz", self.companion["build_flags"])
        self.assertEqual(self.companion["build_unflags"], "-Os")


if __name__ == "__main__":
    unittest.main()
