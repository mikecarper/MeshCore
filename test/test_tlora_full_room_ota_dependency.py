"""The exact-identity Full room build must link its selected OTA implementation."""
import configparser
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
TARGET = "LilyGo_TLora_V2_1_1_6_room_server"
OBSERVER = TARGET + "_observer_mqtt_"


class TLoRaFullRoomOtaDependencyTest(unittest.TestCase):
    def test_selected_full_source_links_the_full_ota_implementation(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "variants/lilygo_tlora_v2_1/platformio.ini")
        observer = config["env:" + OBSERVER]
        self.assertIn("-D LIGHTWEIGHT_WIFI_OTA=1", observer["build_flags"])
        dependencies = observer["lib_deps"].splitlines()
        self.assertIn("file://arch/esp32/AsyncElegantOTA", [x.strip() for x in dependencies])
        self.assertTrue((ROOT / "arch/esp32/AsyncElegantOTA/src/AsyncElegantOTA.cpp").is_file())

        # Use the real selection and overlay functions against a small resolved
        # metadata snapshot. Never invoke PlatformIO in this host test.
        script = r'''
source "$1"
pio() { echo 'PlatformIO is forbidden in this host fixture' >&2; exit 99; }
target=$2
base=$3
PIO_ENV_PLATFORM_BY_NAME[$target]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[$base]=ESP32_PLATFORM
PIO_ENV_BOARD_BY_NAME[$target]=ttgo-lora32-v1
PIO_ENV_BOARD_BY_NAME[$base]=ttgo-lora32-v1
PIO_ENV_MQTT_BY_NAME[$base]=1
PIO_ENV_FULL_WIFI_OTA_BY_NAME[$target]=1
ESP32_FULL_BUILD=1
PLATFORMIO_BUILD_FLAGS=''
PLATFORMIO_BUILD_UNFLAGS=''
BUILD_REDUCTIONS=()
BUILD_CAPABILITIES=()
is_esp32_canonical_full_release_target() { return 0; }
supports_esp32_full_build() { return 0; }
selected=$(get_exact_identity_full_pio_env "$target")
printf 'base=%s\n' "$selected"
apply_esp32_full_size_profile "$target"
printf 'flags=%s\n' "$PLATFORMIO_BUILD_FLAGS"
'''
        result = subprocess.run(["bash", "-c", script, "fixture", str(ROOT / "build_legacy.sh"),
                                 TARGET, OBSERVER], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("base=" + OBSERVER + "\n", result.stdout)
        self.assertIn("-ULIGHTWEIGHT_WIFI_OTA", result.stdout)
        self.assertIn("-UDISABLE_WIFI_OTA", result.stdout)


if __name__ == "__main__":
    unittest.main()
