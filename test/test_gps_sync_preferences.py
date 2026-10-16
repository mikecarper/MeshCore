"""Run canonical GPS sync CLI branches with durable and live state failures."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <helpers/GpsPowerPolicy.h>
struct Prefs { uint16_t gps_sync_interval_hours = 0; };
struct Provider {
  uint16_t hours = 0;
  uint16_t getTimeSyncIntervalHours() const { return hours; }
};
struct Sensors {
  Provider provider;
  bool present = true;
  unsigned applications = 0;
  Provider* getLocationProvider() { return present ? &provider : nullptr; }
  void applyGpsTimeSyncInterval(uint16_t hours) { provider.hours = hours; ++applications; }
};
struct CLI {
  Prefs prefs, persisted;
  Prefs* _prefs = &prefs;
  Sensors sensors;
  Sensors* _sensors = &sensors;
  bool save_ok = true;
  unsigned saves = 0;
  bool trySavePrefs() { ++saves; if (save_ok) persisted = prefs; return save_ok; }
  void set(const char* config, char* reply) { @COMMON_SET@ }
  void get(const char* config, char* reply) { @COMMON_GET@ }
};
struct Companion {
  Prefs _prefs, persisted;
  Sensors sensors;
  bool save_ok = true;
  unsigned saves = 0;
  bool savePrefs() { ++saves; if (save_ok) persisted = _prefs; return save_ok; }
  @SAVE_PREFERENCE@
  bool command(const char* command, char* reply, size_t reply_size) {
    @COMPANION_GET@
    const char* gps_sync_value = nullptr;
    @COMPANION_PREFIX@
    @COMPANION_SET@
    return false;
  }
};
int main() {
  char reply[160];
  CLI common; Companion companion;
  common.get("gps.sync.interval", reply);
  assert(strstr(reply, "default") && !strstr(reply, "0 hours"));
  assert(companion.command("get gps.sync.interval", reply, sizeof(reply)));
  assert(strstr(reply, "default") && !strstr(reply, "0 hours"));
  for (const char* value : {"1", "24", "336", "337", "4294967296", "999999999999999999999999"}) {
    char command[96];
    uint16_t expected=0; assert(mesh::gps::parseSyncIntervalHours(value,expected));
    snprintf(command,sizeof(command),"gps.sync.interval %s",value);
    common.set(command,reply);
    assert(common.prefs.gps_sync_interval_hours==expected);
    assert(common.persisted.gps_sync_interval_hours==expected && common.sensors.provider.hours==expected);
    char actual[32];snprintf(actual,sizeof(actual),"%u hours",unsigned(expected));
    assert(strstr(reply,actual) && strstr(reply,"saved"));
    snprintf(command,sizeof(command),"set gps.sync.interval %s",value);
    assert(companion.command(command,reply,sizeof(reply)));
    assert(companion._prefs.gps_sync_interval_hours==expected);
    assert(companion.persisted.gps_sync_interval_hours==expected && companion.sensors.provider.hours==expected);
    assert(strstr(reply,actual) && strstr(reply,"saved"));
  }
  for(const char* value : {"", "0", "0000", "-1", "1.5", "5h", "1 2", "337junk"}) {
    char command[64];
    const unsigned common_saves=common.saves, companion_saves=companion.saves;
    snprintf(command,sizeof(command),"gps.sync.interval %s",value);
    common.set(command,reply);assert(strstr(reply,"Error:"));
    snprintf(command,sizeof(command),"set gps.sync.interval %s",value);
    assert(companion.command(command,reply,sizeof(reply)) && strstr(reply,"Error:"));
    assert(common.saves==common_saves && companion.saves==companion_saves);
  }
  common.save_ok=companion.save_ok=false;
  const unsigned common_applied=common.sensors.applications, companion_applied=companion.sensors.applications;
  common.set("gps.sync.interval 7",reply);assert(strstr(reply,"unconfirmed")&&strstr(reply,"reboot"));
  assert(companion.command("set gps.sync.interval 7",reply,sizeof(reply)) && strstr(reply,"could not be saved"));
  assert(common.prefs.gps_sync_interval_hours==336 && common.persisted.gps_sync_interval_hours==336);
  assert(companion._prefs.gps_sync_interval_hours==336 && companion.persisted.gps_sync_interval_hours==336);
  assert(common.sensors.provider.hours==336 && companion.sensors.provider.hours==336);
  assert(common.sensors.applications==common_applied && companion.sensors.applications==companion_applied);
  common.get("gps.sync.interval",reply);assert(!strcmp(reply,"> 336 hours"));
  assert(companion.command("get gps.sync.interval",reply,sizeof(reply)) && !strcmp(reply,"> 336 hours"));
  common.sensors.present=companion.sensors.present=false;
  const unsigned common_saves=common.saves, companion_saves=companion.saves;
  common.set("gps.sync.interval 1",reply);assert(strstr(reply,"unavailable"));
  assert(companion.command("set gps.sync.interval 1",reply,sizeof(reply)) && strstr(reply,"unavailable"));
  common.get("gps.sync.interval",reply);assert(strstr(reply,"unavailable"));
  assert(companion.command("get gps.sync.interval",reply,sizeof(reply)) && strstr(reply,"unavailable"));
  assert(common.saves==common_saves && companion.saves==companion_saves);
}
'''


class GpsSyncPreferencesTest(unittest.TestCase):
    def test_canonical_cli_transactions(self):
        common = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        companion = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        header = (ROOT / 'examples/companion_radio/MyMesh.h').read_text()
        replacements = {
            '@COMMON_SET@': extract_braced(common, 'if (strncmp(config, "gps.sync.interval", 17)'),
            '@COMMON_GET@': extract_braced(common, 'if (strcmp(config, "gps.sync.interval") == 0)'),
            '@COMPANION_GET@': extract_braced(companion, 'if (strcmp(command, "get gps.sync.interval") == 0)'),
            '@COMPANION_PREFIX@': extract_braced(companion, 'if (strncmp(command, "set gps.sync.interval", 21)'),
            '@COMPANION_SET@': extract_braced(companion, 'if (gps_sync_value)'),
            '@SAVE_PREFERENCE@': extract_braced(header, 'template <typename T> bool savePreference('),
        }
        code = HARNESS
        for token, value in replacements.items():
            code = code.replace(token, value)
        with tempfile.TemporaryDirectory(prefix='meshcore-gps-sync-prefs-') as directory:
            cpp, exe = Path(directory) / 'test.cpp', Path(directory) / 'test.exe'
            cpp.write_text(code)
            subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-I' + str(ROOT / 'src'), str(cpp), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
