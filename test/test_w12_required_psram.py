#!/usr/bin/env python3
"""Full PSRAM-qualified repeaters require PSRAM; normal and other roles do not."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class W12RequiredPsramBuildTests(unittest.TestCase):
    def test_only_qualified_full_repeater_roles_require_psram(self):
        source = (ROOT / 'build_legacy.sh').read_text(encoding='utf8')
        start = source.index('apply_esp32_full_size_profile() {')
        function = source[start:source.index('\n}\n', start) + 2]
        prelude = r'''
ESP32_FULL_BUILD="$1"
QUALIFIED="$2"
ESP32_FULL_MAX_NEIGHBOURS=254
DRAM_LIMITED_MAX_NEIGHBOURS=50
PLATFORMIO_BUILD_FLAGS="-DEXISTING=1"
declare -A PIO_ENV_FULL_WIFI_OTA_BY_NAME=()
supports_esp32_full_build() { [ "$QUALIFIED" = 1 ]; }
is_room_server_role_target() { [[ "$1" == *room_server* ]]; }
requires_dram_limited_neighbors() { return 1; }
requires_esp32_companion_full_ota_fallback() { return 1; }
append_platformio_build_unflags() { :; }
record_build_reduction() { :; }
'''
        script = prelude + function + r'''
apply_esp32_full_size_profile "$3"
printf "%s" "$PLATFORMIO_BUILD_FLAGS"
'''
        qualified = {'LilyGo_TBeam_1W_repeater', 'LilyGo_TBeam_1W_repeater_bridge_espnow',
                     'LilyGo_TBeam_1W_repeater_observer_mqtt',
                     'T_Beam_S3_Supreme_SX1262_repeater',
                     'T_Beam_S3_Supreme_SX1262_repeater_bridge_espnow',
                     'T_Beam_S3_Supreme_SX1262_repeater_observer_mqtt',
                     'Wio_Tracker_L2_repeater',
                     'heltec_rc32_repeater', 'heltec_rc32_repeater_bridge_espnow',
                     'heltec_rc32_repeater_observer_mqtt', 'heltec_rc32_without_display_repeater',
                     'heltec_rc32_without_display_repeater_bridge_espnow',
                     'heltec_rc32_without_display_repeater_observer_mqtt',
                     'Heltec_E213_repeater', 'Heltec_E213_repeater_bridge_espnow',
                     'Heltec_E213_repeater_observer_mqtt', 'Heltec_E290_repeater',
                     'Heltec_E290_repeater_bridge_espnow', 'Heltec_E290_repeater_observer_mqtt',
                     'Heltec_T190_repeater', 'Heltec_T190_repeater_bridge_espnow',
                     'Heltec_T190_repeater_observer_mqtt',
                     'Station_G2_repeater', 'Station_G2_repeater_bridge_espnow',
                     'Station_G2_logging_repeater', 'Station_G2_logging_repeater_bridge_espnow',
                     'Station_G2_repeater_observer_mqtt', 'Station_G3_ESP32_repeater',
                     'Station_G3_ESP32_logging_repeater', 'Station_G3_ESP32_repeater_observer_mqtt',
                     'Station_G3_ESP32_r2_repeater', 'Station_G3_ESP32_r2_repeater_observer_mqtt',
                     'RAK_3112_repeater', 'RAK_3112_repeater_bridge_rs232',
                     'RAK_3112_repeater_bridge_espnow', 'RAK_3112_repeater_observer_mqtt',
                     'meshnology_w12_repeater', 'meshnology_w12_repeater_bridge_espnow',
                     'meshnology_w12_repeater_observer_mqtt',
                     'heltec_v4_repeater', 'heltec_v4_repeater_bridge_espnow',
                     'heltec_v4_repeater_observer_mqtt', 'heltec_v4_expansionkit_repeater',
                     'heltec_v4_expansionkit_repeater_observer_mqtt',
                     'heltec_v4_tft_repeater', 'heltec_v4_tft_repeater_bridge_espnow',
                     'heltec_v4_r8_repeater', 'heltec_v4_r8_repeater_bridge_espnow',
                     'heltec_v4_r8_repeater_observer_mqtt', 'heltec_v4_r8_tft_repeater',
                     'heltec_v4_r8_tft_repeater_bridge_espnow',
                     'heltec_v4_r8_tft_repeater_observer_mqtt'}
        others = {'RAK_3112_room_server', 'RAK_3112_room_server_observer_mqtt',
                  'RAK_3112_sensor', 'RAK_3112_companion_radio_full', 'RAK_3112_repeater_extra',
                  'meshnology_w12_room_server', 'meshnology_w12_sensor',
                  'meshnology_w12_companion_radio_full', 'meshnology_w12_repeater_extra',
                  'heltec_v4_room_server', 'heltec_v4_r8_tft_room_server',
                  'heltec_v4_repeater_legacy_partition_test', 'heltec_v4_repeater_extra',
                  'heltec_v4_2_v4_3_companion_radio_full_femon',
                  'heltec_v4_r8_tft_companion_radio_full',
                  'LilyGo_TBeam_1W_room_server', 'LilyGo_TBeam_1W_room_server_observer_mqtt',
                  'LilyGo_TBeam_1W_companion_radio_full', 'LilyGo_TBeam_1W_repeater_extra',
                  'T_Beam_S3_Supreme_SX1262_room_server',
                  'T_Beam_S3_Supreme_SX1262_room_server_observer_mqtt',
                  'T_Beam_S3_Supreme_SX1262_companion_radio_full',
                  'T_Beam_S3_Supreme_SX1262_repeater_extra',
                  'Wio_Tracker_L2_room_server', 'Wio_Tracker_L2_companion_radio_full',
                  'Wio_Tracker_L2_repeater_extra',
                  'heltec_rc32_room_server', 'heltec_rc32_room_server_observer_mqtt',
                  'heltec_rc32_companion_radio_full', 'heltec_rc32_repeater_extra',
                  'heltec_rc32_without_display_companion_radio_full',
                  'Heltec_E213_room_server', 'Heltec_E213_room_server_observer_mqtt',
                  'Heltec_E213_companion_radio_full', 'Heltec_E213_repeater_extra',
                  'Heltec_E290_room_server', 'Heltec_E290_companion_radio_full',
                  'Heltec_T190_room_server', 'Heltec_T190_companion_radio_full',
                  'Station_G2_room_server', 'Station_G2_room_server_observer_mqtt',
                  'Station_G2_companion_radio_full', 'Station_G2_repeater_extra',
                  'Station_G3_ESP32_room_server', 'Station_G3_ESP32_room_server_observer_mqtt',
                  'Station_G3_ESP32_companion_radio_full', 'Station_G3_ESP32_repeater_extra'}
        bash = shutil.which('bash')
        if not bash:
            self.skipTest('bash not installed')
        for full in ('0', '1'):
            for supported in ('0', '1'):
                for target in sorted(qualified | others):
                    with self.subTest(full=full, supported=supported, target=target):
                        result = subprocess.run([bash, '-c', script, 'bash', full, supported, target],
                                                capture_output=True, text=True, timeout=10, check=True)
                        flags = result.stdout.split()
                        self.assertIn('-DEXISTING=1', flags)
                        self.assertEqual(flags.count('-DMESH_REPEATER_REQUIRE_PSRAM=1'),
                                         int(full == '1' and supported == '1' and target in qualified),
                                         result.stdout)

if __name__ == '__main__':
    unittest.main()
