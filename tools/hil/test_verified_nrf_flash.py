"""The shared Heltec USB product string must never select a flash image."""

import json
from pathlib import Path
import unittest

from tools.hil.verified_nrf_flash import verify


INVENTORY = json.loads((Path(__file__).parent / "mercerwood_pi_nrf_inventory.json").read_text())


class VerifiedNrfFlashTest(unittest.TestCase):
    def test_tower_accepts_tower_artifacts(self):
        for artifact in (
            "heltec_mesh_tower_v2_sdcard_bootloader-OTAFIX2.4.8_s140_6.1.1.zip",
            "Heltec_tower_v2_companion_radio_full-v1.17.1.7.zip",
        ):
            self.assertEqual(verify(INVENTORY, "9352162A72082314",
                                    "Heltec MeshTower V2 SD", artifact),
                             "Heltec MeshTower V2 SD")

    def test_t114_image_refused_for_tower_serial(self):
        with self.assertRaisesRegex(ValueError, "not a Heltec MeshTower V2 SD firmware artifact"):
            verify(INVENTORY, "9352162A72082314", "Heltec MeshTower V2 SD",
                   "Heltec_t114_companion_radio_full-v1.17.1.7.uf2")

    def test_non_sd_bootloader_refused_for_sd_tower(self):
        with self.assertRaisesRegex(ValueError, "not a Heltec MeshTower V2 SD bootloader artifact"):
            verify(INVENTORY, "9352162A72082314", "Heltec MeshTower V2 SD",
                   "heltec_mesh_tower_v2_bootloader-OTAFIX2.4.8.zip")

    def test_t114_board_name_refused_for_tower_serial(self):
        with self.assertRaisesRegex(ValueError, "is Heltec MeshTower V2 SD, not Heltec T114"):
            verify(INVENTORY, "9352162A72082314", "Heltec T114",
                   "Heltec_t114_companion_radio_full-v1.17.1.7.uf2")

    def test_unknown_serial_refused(self):
        with self.assertRaisesRegex(ValueError, "unrecorded USB serial"):
            verify(INVENTORY, "UNKNOWN", "Heltec MeshTower V2 SD",
                   "Heltec_tower_v2_companion_radio_full.uf2")


if __name__ == "__main__":
    unittest.main()
