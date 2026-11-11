"""Exercise exact Full repeater RAM recipes without reducing their services."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class T190FullPsramTest(unittest.TestCase):
    def flags(self, full, target):
        source = (ROOT / "build_legacy.sh").read_text()
        start = source.index("apply_esp32_full_size_profile() {")
        function = source[start:source.index("\n}\n", start) + 2]
        script = r'''
ESP32_FULL_BUILD="$1"
ESP32_FULL_MAX_NEIGHBOURS=254
DRAM_LIMITED_MAX_NEIGHBOURS=50
PLATFORMIO_BUILD_FLAGS=""
declare -A PIO_ENV_FULL_WIFI_OTA_BY_NAME=()
supports_esp32_full_build() { return 0; }
is_room_server_role_target() { return 1; }
requires_dram_limited_neighbors() { return 1; }
requires_esp32_companion_full_ota_fallback() { return 1; }
append_platformio_build_unflags() { :; }
append_platformio_extra_script() { :; }
record_build_reduction() { :; }
''' + function + r'''
apply_esp32_full_size_profile "$2"
printf '%s\n' "$PLATFORMIO_BUILD_FLAGS"
'''
        return subprocess.check_output(["bash", "-c", script, "test", full, target],
                                       cwd=ROOT, text=True)

    def test_exact_full_alias_uses_board_psram_without_reducing_capacities(self):
        board = json.loads((ROOT / "boards/heltec_t190.json").read_text())
        self.assertIn("-DBOARD_HAS_PSRAM", board["build"]["extra_flags"])
        for full, expected in (("1", True), ("0", False)):
            with self.subTest(full=full):
                flags = self.flags(full, "Heltec_T190_repeater_")
                self.assertEqual("-DMESH_REPEATER_REQUIRE_PSRAM=1" in flags, expected)
                if expected:
                    self.assertIn("-DMAX_NEIGHBOURS=254", flags)
                    self.assertIn("-DWIFI_OTA_SEEDER=1", flags)
                    self.assertNotIn("-DFLOOD_PACKET_FILTER_SLOTS=", flags)

    def test_full_trackers_use_supported_canvas_without_cutting_radio_services(self):
        for target in ("Heltec_Wireless_Tracker_repeater", "heltec_tracker_v2_repeater"):
            with self.subTest(target=target):
                flags = self.flags("1", target)
                self.assertIn("-DST7735_BUFFER_COLOR_DEPTH=8", flags)
                self.assertIn("-DMAX_NEIGHBOURS=254", flags)
                self.assertIn("-DWIFI_OTA_SEEDER=1", flags)
                self.assertNotIn("-DFLOOD_PACKET_FILTER_SLOTS=", flags)
                self.assertNotIn("-DST7735_BUFFER_COLOR_DEPTH=8", self.flags("0", target))
                self.assertNotIn("-DST7735_BUFFER_COLOR_DEPTH=8",
                                 self.flags("1", target.replace("repeater", "room_server")))


if __name__ == "__main__":
    unittest.main()
