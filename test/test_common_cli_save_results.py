"""Exercise production CLI save acknowledgements, rollback, and interval parsing."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


def compile_run(source, macros=()):
    with tempfile.TemporaryDirectory(prefix='common-cli-save-') as directory:
        work = Path(directory)
        cpp, exe = work / 'test.cpp', work / 'test'
        cpp.write_text(source, encoding='utf-8')
        subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                        '-fno-pie', '-no-pie', *['-D' + m + '=1' for m in macros],
                        '-I' + str(ROOT / 'src'), str(cpp), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


class CommonCLISaveResultsTest(unittest.TestCase):
    def test_production_handlers(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text(encoding='utf-8')
        code = (ROOT / 'test/fixtures/common_cli_save_results/main.cpp').read_text()
        methods = '\n'.join(extract_braced(source, signature) for signature in (
            'void CommonCLI::savePrefs(PrefsSaveRouting::Scope scope)',
            'bool CommonCLI::trySavePrefs(', 'bool CommonCLI::saveObserverPrefs('))
        parsers = '\n'.join(extract_braced(source, signature) for signature in (
            'static bool looksUnsignedInteger(', 'static const char* skipSpacesConst(',
            'static bool parseUint32Strict('))
        branches = '\nelse '.join(extract_braced(source, signature) for signature in (
            'if (memcmp(config, "flood.advert.interval ", 22) == 0)',
            'if (memcmp(config, "advert.interval ", 16) == 0)',
            'if (memcmp(config, "guest.password ", 15) == 0)',
            'if (memcmp(config, "prv.key ", 8) == 0)',
            'if (memcmp(config, "multi.acks ", 11) == 0)'))
        handler = extract_braced(source, 'void CommonCLI::handleCommand(')
        guard = re.search(r'PrefsSaveReplyGuard save_reply\([^;]+;', handler).group()
        for key, value in {
            'PARSERS': parsers, 'SAVE_METHODS': methods, 'SET_BRANCHES': branches,
            'REPLY_GUARD': guard,
            'PASSWORD': extract_braced(source, 'if (memcmp(command, "password ", 9) == 0)'),
        }.items():
            code = code.replace('@' + key + '@', value)
        for macros in ((), ('WITH_MQTT_BRIDGE',)):
            with self.subTest(macros=macros):
                compile_run(code, macros)

    def test_observer_setters_use_the_observer_store(self):
        source = (ROOT / 'src/helpers/CommonCLI_Observer.cpp').read_text()
        setter = extract_braced(source, 'bool CommonCLI::handleObserverSetCmd(')
        self.assertNotIn('savePrefs(', setter)
        self.assertIn('saveObserverPrefs();', setter)

    def test_all_identity_callbacks_return_the_store_result(self):
        methods = []
        for role, name, cls in (('simple_repeater', 'MyMesh', 'Repeater'),
                                ('simple_room_server', 'MyMesh', 'Room'),
                                ('simple_sensor', 'SensorMesh', 'Sensor')):
            source = (ROOT / 'examples' / role / (name + '.cpp')).read_text()
            header = (ROOT / 'examples' / role / (name + '.h')).read_text()
            self.assertIn('bool saveIdentity(', header)
            method = extract_braced(source, f'bool {name}::saveIdentity(')
            methods.append(method.replace(name + '::', cls + '::'))
        code = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
namespace mesh { struct LocalIdentity {}; }
struct FS { bool ok = false; unsigned calls = 0; };
struct IdentityStore {
  FS& fs;
  IdentityStore(FS& f, const char*) : fs(f) {}
  bool saveWithRetry(const char* name, const mesh::LocalIdentity&) {
    assert(strcmp(name, "_main") == 0); ++fs.calls; return fs.ok;
  }
};
struct Repeater { FS fs; FS* _fs = &fs; bool saveIdentity(const mesh::LocalIdentity&); };
struct Room { FS fs; FS* _fs = &fs; bool saveIdentity(const mesh::LocalIdentity&); };
struct Sensor { FS fs; FS* _fs = &fs; bool saveIdentity(const mesh::LocalIdentity&); };
@METHODS@
template<typename Role> void check() {
  Role role; mesh::LocalIdentity id;
  for (bool result : {false, true, false}) {
    role.fs.ok = result;
    assert(role.saveIdentity(id) == result);
  }
  assert(role.fs.calls == 3);
}
int main() { check<Repeater>(); check<Room>(); check<Sensor>(); }
'''.replace('@METHODS@', '\n'.join(methods))
        for platform in ('NRF52_PLATFORM', 'STM32_PLATFORM', 'ESP32', 'RP2040_PLATFORM'):
            with self.subTest(platform=platform):
                compile_run(code, (platform,))


if __name__ == '__main__':
    unittest.main()
