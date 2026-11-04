"""Exercise the real radio2 advert preference and legacy image compatibility."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import test_common_prefs_legacy_rxgain as legacy
from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]


class Radio2AdvertSettingTests(unittest.TestCase):
    def test_real_command_parser_and_failed_save_rollback(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        set_start = source.index('  } else if (memcmp(config, "radio2.advert ", 14) == 0) {')
        set_end = source.index('  } else if', set_start + 5)
        set_branch = 'if' + source[set_start + len('  } else if'):set_end] + '\n}'
        get_start = source.index('  } else if (configKeyEquals(config, "radio2.advert")) {')
        get_end = source.index('  } else if', get_start + 5)
        get_branch = 'if' + source[get_start + len('  } else if'):get_end] + '\n}'
        code = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
namespace mesh { namespace cli {
void formatError(char* out, size_t n, const char* message) { snprintf(out,n,"Error: %s",message); }
}}
struct Callbacks {
  unsigned notifications=0; bool last_enabled=false;
  void onRadio2FloodAdvertChanged(bool enabled) {++notifications;last_enabled=enabled;}
};
bool configKeyEquals(const char* a,const char* b){return !strcmp(a,b);}
@ONOFF@
struct CommonCLI {
  struct Prefs {uint8_t radio2_flood_advert_disabled=0;} prefs;
  Prefs* _prefs=&prefs;Callbacks callbacks;Callbacks* _callbacks=&callbacks;
  bool save_ok=true;unsigned saves=0;
  bool trySavePrefs(){++saves;return save_ok;}
  void set(const char* config,char* reply) {@SET@ else strcpy(reply,"unknown");}
  void get(const char* config,char* reply) {@GET@ else strcpy(reply,"unknown");}
};
int main() {
  CommonCLI cli;char reply[160]{};
  char config[160]{};
  auto set=[&](const char* text){memset(config,0,sizeof(config));strcpy(config,text);cli.set(config,reply);};
  auto get=[&](const char* text){memset(config,0,sizeof(config));strcpy(config,text);cli.get(config,reply);};
  get("radio2.advert");
  assert(!strcmp(reply,"> on") && cli.saves==0);
  get("radio2.advert.more");assert(!strcmp(reply,"unknown"));
  set("radio2.advertising off");assert(!strcmp(reply,"unknown"));
  set("radio2.advert");assert(!strcmp(reply,"unknown"));
  set("radio2.advert maybe");
  assert(strstr(reply,"on|off") && cli.saves==0);
  set("radio2.advert off");
  assert(!strcmp(reply,"OK") && cli.prefs.radio2_flood_advert_disabled==0xA5);
  assert(cli.callbacks.notifications==1 && !cli.callbacks.last_enabled);
  get("radio2.advert");assert(!strcmp(reply,"> off"));
  cli.save_ok=false;
  set("radio2.advert on");
  assert(strstr(reply,"save unconfirmed") && cli.prefs.radio2_flood_advert_disabled==0xA5);
  assert(cli.callbacks.notifications==1); // no scheduler mutation on failed save
  cli.save_ok=true;
  set("radio2.advert on");
  assert(!strcmp(reply,"OK") && !cli.prefs.radio2_flood_advert_disabled);
  assert(cli.callbacks.notifications==2 && cli.callbacks.last_enabled);
}
'''.replace('@SET@', set_branch).replace('@GET@', get_branch).replace(
            '@ONOFF@', extract_braced(source, 'static bool parseOnOffStrict('))
        with tempfile.TemporaryDirectory(prefix='radio2-advert-cli-') as folder:
            cpp, binary = Path(folder) / 'test.cpp', Path(folder) / 'test'
            cpp.write_text(code)
            flags = [] if os.name == 'nt' else [
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
            built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra',
                                    '-Werror', *flags, str(cpp), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_legacy_and_both_common_writers_round_trip(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        code = legacy.harness_source(source)

        # Exercise the real observer writer through byte 79 without requiring
        # unrelated MQTT and hardware dependencies in this small host fixture.
        start = source.index('static bool writeCommonPrefsImage(')
        body = source.index('{', start)
        end = source.index('WRITE_COMMON_PREFS(&prefs->rx_delay_base)', body)
        writer = ('template<typename Writer>\n' + source[start:end]
                  + '\n#undef WRITE_COMMON_PREFS\n#undef WRITE_COMMON_PREFS_BYTES\n'
                  + 'return true;\n}\n')
        code = code.replace('CommonCLI stock(uint8_t gain) {',
                            writer + '\nCommonCLI stock(uint8_t gain) {')
        code = code[:code.index('int main(int argc, char** argv) {')] + r'''
int main() {
  // Genuine stock v1.14.1 byte 79 is an RX gain, not an advert setting.
  auto old = stock(1).stock1141();
  assert(old.size() == 290 && old.bytes[79] == 1);
  CommonCLI legacy_reader; legacy_reader.load(old);
  assert(legacy_reader.prefs.rx_boosted_gain == 1);
  assert(legacy_reader.prefs.radio2_flood_advert_disabled == 0);

  for (uint8_t disabled : {0, 0xA5}) {
    CommonCLI writer = stock(1);
    writer.prefs.radio2_flood_advert_disabled = disabled;
    auto ordinary = writer.modern();
    assert(ordinary.size() == 291);
    assert(ordinary.bytes[79] == disabled);
    assert(ordinary.bytes[290] == 1);
    File observer;
    assert(writeCommonPrefsImage(observer, &writer.prefs));
    assert(observer.size() == 80);
    assert(std::equal(observer.bytes.begin(), observer.bytes.end(), ordinary.bytes.begin()));
    CommonCLI restored; restored.load(ordinary);
    assert(restored.prefs.radio2_flood_advert_disabled == disabled);
    assert(restored.prefs.rx_boosted_gain == 1);
  }

  // Other reserved byte values are never mistaken for the explicit off marker.
  for (uint8_t old_padding : {1, 2, 0xFF}) {
    auto image = stock(1).modern(); image.bytes[79] = old_padding;
    CommonCLI reader; reader.load(image);
    assert(reader.prefs.radio2_flood_advert_disabled == 0);
    assert(reader.prefs.rx_boosted_gain == 1);
  }
}
'''
        with tempfile.TemporaryDirectory(prefix='radio2-advert-prefs-') as folder:
            cpp, binary = Path(folder) / 'test.cpp', Path(folder) / 'test'
            cpp.write_text(code)
            flags = [] if os.name == 'nt' else [
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
            built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra',
                                    '-Werror', *flags, str(cpp), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == '__main__':
    unittest.main()
