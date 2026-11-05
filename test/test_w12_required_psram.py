#!/usr/bin/env python3
"""Full W12 repeater builds require PSRAM; normal and other roles do not."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class W12RequiredPsramBuildTests(unittest.TestCase):
    def test_only_full_w12_repeater_roles_require_psram(self):
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
        qualified = {'meshnology_w12_repeater', 'meshnology_w12_repeater_bridge_espnow',
                     'meshnology_w12_repeater_observer_mqtt'}
        others = {'meshnology_w12_room_server', 'meshnology_w12_sensor',
                  'meshnology_w12_companion_radio_full', 'meshnology_w12_repeater_extra',
                  'heltec_v4_repeater', 'Station_G3_ESP32_repeater'}
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
