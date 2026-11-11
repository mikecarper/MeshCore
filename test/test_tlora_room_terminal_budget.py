"""Canonical and observer Full rooms must share their qualified startup budget."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_firmware_ram as ram


class TLoRaRoomTerminalBudgetTest(unittest.TestCase):
    def profile_flags(self, target):
        script = r'''
source "$1"
pio() { echo 'PlatformIO is forbidden in this host fixture' >&2; exit 99; }
ESP32_FULL_BUILD=1
PLATFORMIO_BUILD_FLAGS=''
PLATFORMIO_BUILD_UNFLAGS=''
BUILD_REDUCTIONS=()
BUILD_CAPABILITIES=()
supports_esp32_full_build() { return 0; }
apply_esp32_full_size_profile "$2"
printf '%s\n' "$PLATFORMIO_BUILD_FLAGS"
'''
        result = subprocess.run(["bash", "-c", script, "fixture", str(ROOT / "build_legacy.sh"), target],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_canonical_and_observer_rooms_keep_complete_budget(self):
        for target in ("LilyGo_TLora_V2_1_1_6_room_server",
                       "LilyGo_TLora_V2_1_1_6_room_server_observer_mqtt_"):
            with self.subTest(target=target):
                self.assertIn("-DMESH_TERMINAL_INITIAL_OUTPUT_CAPACITY=2048", self.profile_flags(target))
                flags = {"ESP32_PLATFORM": 1, "CONFIG_IDF_TARGET_ESP32": 1,
                         "ADMIN_PASSWORD": "test", "WITH_MQTT_BRIDGE": 1,
                         "ENABLE_OTA": 1}
                default = ram.requirements("ESP32_PLATFORM", flags, target)
                qualified = ram.requirements("ESP32_PLATFORM", {**flags,
                    "MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY": 2048}, target)
                self.assertEqual(default["required_heap_bytes"] - qualified["required_heap_bytes"], 2048)
                for key in default["components"]:
                    if key != "browser_terminal_scrollback":
                        self.assertEqual(default["components"][key], qualified["components"][key])
        self.assertNotIn("MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY", self.profile_flags("unrelated_room_server"))


if __name__ == "__main__":
    unittest.main()
