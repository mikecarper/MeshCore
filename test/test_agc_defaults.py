"""Exercise fresh AGC defaults and each role's production interval getter."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

class AGCDefaultTests(unittest.TestCase):
    def test_new_preferences_use_16_seconds_in_all_roles(self):
        common = (ROOT / 'src/helpers/CommonCLI.h').read_text()
        companion = (ROOT / 'examples/companion_radio/NodePrefs.h').read_text()
        for role, header, prefs in (
            ('companion', 'examples/companion_radio/MyMesh.h', companion),
            ('repeater', 'examples/simple_repeater/MyMesh.h', common),
            ('room', 'examples/simple_room_server/MyMesh.h', common),
            ('sensor', 'examples/simple_sensor/SensorMesh.cpp', common),
        ):
            with self.subTest(role=role), tempfile.TemporaryDirectory(prefix='mesh-agc-default-') as tmp:
                field = re.search(r'uint8_t agc_reset_interval\s*=[^;]+;', prefs).group()
                signature = 'int SensorMesh::getAGCResetInterval() const' if role == 'sensor' else 'int getAGCResetInterval() const'
                getter = method((ROOT / header).read_text(), signature).replace('SensorMesh::', '').replace(' override', '')
                code = '#include <cassert>\n#include <cstdint>\n#include <initializer_list>\nstruct Role { struct {' + field + '} _prefs;\n' + getter + '\n};\n'
                code += 'int main(){Role role; assert(role.getAGCResetInterval()==16000); for(uint8_t saved : {0,1,4,7,255}){role._prefs.agc_reset_interval=saved;assert(role.getAGCResetInterval()==saved*4000);}}\n'
                cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
                cpp.write_text(code)
                built = subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(binary)], capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stderr)
                ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_repeater_and_current_cascade_build_do_not_override_16(self):
        source = (ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text()
        self.assertRegex(source, r'#ifndef DEFAULT_AGC_RESET_INTERVAL_SECONDS\s+#define DEFAULT_AGC_RESET_INTERVAL_SECONDS 16\s+#endif')
        self.assertEqual(re.findall(r'_prefs\.agc_reset_interval\s*=\s*([^;]+);', source), ['DEFAULT_AGC_RESET_INTERVAL_SECONDS / 4'])
        flags = (ROOT / 'build_legacy.sh').read_text()
        self.assertEqual(re.findall(r'-DDEFAULT_AGC_RESET_INTERVAL_SECONDS=(\d+)', flags), ['16'])
        for config in ROOT.glob('platformio*.ini'):
            with self.subTest(config=config.name):
                for value in re.findall(r'DEFAULT_AGC_RESET_INTERVAL_SECONDS=(\d+)', config.read_text()):
                    self.assertEqual(value, '16')

if __name__ == '__main__':
    unittest.main()
