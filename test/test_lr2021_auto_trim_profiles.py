"""Check W12 auto-trim is available only on supported firmware roles."""
from configparser import ConfigParser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class W12AutoTrimProfileTests(unittest.TestCase):
    def test_supported_roles_and_exclusions(self):
        config = ConfigParser(interpolation=None, inline_comment_prefixes=(';',))
        config.read(ROOT / 'variants/meshnology_w12/platformio.ini')
        fragment = config['meshnology_w12_auto_trim']['build_flags']
        self.assertEqual(fragment.strip(), '-D MESH_ENABLE_LR2021_AUTO_XTRIM=1')
        flag = '${meshnology_w12_auto_trim.build_flags}'
        supported = (
            'repeater', 'repeater_bridge_espnow', 'repeater_observer_mqtt',
            'room_server', 'room_server_observer_mqtt', 'sensor',
            'companion_radio_usb', 'companion_radio_ble', 'companion_radio_wifi',
        )
        for role in supported:
            with self.subTest(role=role):
                self.assertIn(flag, config[f'env:meshnology_w12_{role}']['build_flags'])
        full = config['env:meshnology_w12_companion_radio_full']
        self.assertEqual(full['extends'], 'env:meshnology_w12_companion_radio_wifi')
        self.assertIn('${env:meshnology_w12_companion_radio_wifi.build_flags}',
                      full['build_flags'])
        self.assertNotIn('MESH_ENABLE_LR2021_AUTO_XTRIM', config['meshnology_w12']['build_flags'])
        for role in ('terminal_chat', 'kiss_modem'):
            with self.subTest(role=role):
                self.assertNotIn(flag, config[f'env:meshnology_w12_{role}'].get('build_flags', ''))


if __name__ == '__main__':
    unittest.main()
