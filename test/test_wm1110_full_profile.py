"""WM1110 Full retains BLE while using the real CH340 primary UART."""
import configparser
import os
from pathlib import Path
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
CONFIG.read(ROOT / "platformio.ini")
CONFIG.read(ROOT / "variants/wio_wm1110/platformio.ini")
TARGET = "wio_wm1110_companion_radio_full"


class Wm1110FullProfileTests(unittest.TestCase):
    def test_full_is_an_explicit_exact_board_child_of_the_unchanged_ble_recipe(self):
        base = CONFIG["env:wio_wm1110_companion_radio_ble"]
        full = CONFIG["env:" + TARGET]
        self.assertEqual(full["extends"], "env:wio_wm1110_companion_radio_ble")
        self.assertEqual(base["board_build.ldscript"], "boards/nrf52840_s140_v7_extrafs.ld")
        self.assertEqual(base["board_upload.maximum_size"], "708608")
        self.assertNotIn("MESH_PRIMARY_UART_PORT", base["build_flags"])
        self.assertNotIn("ENABLE_USB_INTERFACE", base["build_flags"])
        flags = shlex.split(full["build_flags"])
        for flag in ("COMPANION_RADIO_FULL=1", "ENABLE_USB_INTERFACE=1",
                     "MESH_PRIMARY_UART_PORT=Serial1", "ENABLE_OTA=1",
                     "OTA_SEEDER_ONLY=1", "MOTA_TARGET_ID=0", "OTA_FOLDER_SERIAL=1",
                     "COMPANION_FEATURE_USB_MOTA_SOURCE=1", "COMPANION_FEATURE_BLE_MOTA_SOURCE=1",
                     "OTA_SHARED_COMPANION_QUEUE=1", "OTA_TARGET_NAME_FRONT_CODED=1",
                     "MESH_DEBUG=1", "MESH_PACKET_LOGGING=1", "SERIAL_BUFFER_SIZE=1024"):
            self.assertIn(flag, flags)
        for value in ("MAX_CONTACTS=350", "MAX_GROUP_CHANNELS=40", "OFFLINE_QUEUE_SIZE=256",
                      "BLE_PIN_CODE=123456"):
            self.assertIn(value, shlex.split(base["build_flags"]))
        self.assertIn("helpers/ota/*.cpp", full["build_src_filter"])
        self.assertNotIn("MESH_DUAL_CDC_LOGGING", full["build_flags"])
        self.assertNotIn("CFG_TUD_CDC=2", full["build_flags"])

    @unittest.skipIf(os.name == "nt", "sourced Bash policy runs under Linux/WSL")
    def test_full_build_overlay_never_invents_a_second_usb_endpoint_for_uart(self):
        script = r'''
set -euo pipefail
pio() { echo 'unexpected PIO invocation' >&2; return 99; }
source ./build.sh
target=$1
PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
PIO_ENV_BUILD_BASE_BY_NAME[$target]=$target
printf -v PIO_CONFIG_JSON '[ [ "env:%s", [ [ "build_flags", [ "%s" ] ], [ "build_src_filter", [ "+<helpers/ota/*.cpp>", "+<helpers/nrf52/SerialBLEInterface.cpp>" ] ] ] ] ]' "$target" "$2"
PLATFORMIO_BUILD_FLAGS=
PLATFORMIO_BUILD_UNFLAGS=
BUILD_REDUCTIONS=()
MESHDEBUG_OVERRIDE=
PACKET_LOGGING_OVERRIDE=
MQTT_BRIDGE_OVERRIDE=
apply_companion_radio_full_profile "$target" "$target"
printf '%s' "$PLATFORMIO_BUILD_FLAGS"
'''
        for target, configured, dual in (
            (TARGET, "-DMESH_PRIMARY_UART_PORT=Serial1", False),
            ("t1000e_companion_radio_full", "", False),
            ("Xiao_nrf52_companion_radio_full", "", True),
        ):
            result = subprocess.run(["bash", "-c", script, "wm1110-policy", target, configured],
                                    cwd=ROOT, text=True, capture_output=True, check=True)
            with self.subTest(target=target):
                flags = shlex.split(result.stdout)
                self.assertIn("-DENABLE_USB_INTERFACE=1", flags)
                self.assertIn("-DBLE_PIN_CODE=123456", flags)
                self.assertIn("-DCOMPANION_FEATURE_USB_MOTA_SOURCE=1", flags)
                self.assertIn("-DCOMPANION_FEATURE_BLE_MOTA_SOURCE=1", flags)
                self.assertEqual("-DMESH_DUAL_CDC_LOGGING=1" in flags, dual)
                self.assertEqual("-DCFG_TUD_CDC=2" in flags, dual)
                self.assertEqual("-DCOMPANION_FEATURE_DEDICATED_USB_LOGGING=1" in flags, dual)


if __name__ == "__main__":
    unittest.main()
