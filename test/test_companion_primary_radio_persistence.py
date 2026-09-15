"""Execute primary CLI transactions through real parsers and preference images."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_companion_preferences_transaction import HARNESS as PREFERENCES_HARNESS
from test_companion_preferences_transaction import esp_recovery_helpers
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/RadioProfileCLI.h>
#include <helpers/radiolib/RXPowerSaving.h>
#include <limits>
#define constrain(value,low,high) ((value)<(low)?(low):((value)>(high)?(high):(value)))
struct Clock : mesh::RTCClock {
  uint32_t getCurrentTime() override { return 1700000000; }
  void setCurrentTime(uint32_t) override {}
};
struct Radio : mesh::Radio {
  mesh::RadioProfiles p;
  Radio() { p.primary.freq=909.5f; p.primary.bw=62.5f; p.primary.sf=7; p.primary.cr=5; }
  mesh::RadioProfiles* profiles() override { return &p; }
  const mesh::RadioProfiles* profiles() const override { return &p; }
  bool validateProfile(const mesh::RadioProfileParams& v) const override {
    return mesh::RadioProfiles::valid(v) && v.bw<=500;
  }
  uint16_t profilePreamble(uint8_t profile) const override { return p.preamble(profile,32); }
  int recvRaw(uint8_t*,int) override { return 0; }
  uint32_t getEstAirtimeFor(int) override { return 10; }
  float packetScore(float,int) override { return 0; }
  bool startSendRaw(const uint8_t*,int) override { return true; }
  bool isSendComplete() override { return true; }
  void onSendFinished() override {}
  bool isInRecvMode() const override { return true; }
};
struct Sensors { double node_lat=0,node_lon=0; } sensors;
@COMMAND_FILTER@
struct MyMesh {
  CompanionNodePrefs _prefs;
  DataStore store;
  DataStore* _store=&store;
  MemoryFS profile_fs;
  Radio radio;
  Clock clock;
  mesh::RadioProfileCLI _radio_profiles;
  char reply[160]={};
  MyMesh() {
    _prefs.freq=909.5f; _prefs.bw=62.5f; _prefs.sf=7; _prefs.cr=5;
    _prefs.airtime_factor=1; _prefs.rx_ps_level=1; _prefs.rx_ps_preamble=16;
    _prefs.rx_ps_rx_us=3000; _prefs.rx_ps_sleep_us=4000;
    _radio_profiles.begin(&profile_fs,&radio,&clock);
    assert(_radio_profiles.savePrimaryPreamble(48));
    assert(savePrefs());
  }
  @SAVE_PREFS@
  void bootSanitize() { @COMPANION_SANITIZE@ }
  bool handleCommand(const char*,uint32_t,char*);
  const char* command(const char* value,uint32_t timestamp=0) {
    memset(reply,0,sizeof(reply));
    assert(handleCommand(value,timestamp,reply));
    assert(memchr(reply,0,sizeof(reply)));
    return reply;
  }
  void loadSaved(CompanionNodePrefs& loaded) {
    double lat=0,lon=0;
    assert(store.loadPrefs(loaded,lat,lon));
  }
  uint16_t rebootPreamble() {
    Radio reboot_radio;mesh::RadioProfileCLI reboot_cli;
    reboot_cli.begin(&profile_fs,&reboot_radio,&clock);
    return reboot_cli.primaryPreamble();
  }
  void expectOriginal() {
    assert(_prefs.freq==909.5f && _prefs.bw==62.5f && _prefs.sf==7 && _prefs.cr==5);
    assert(_prefs.rx_ps_rx_us==3000 && _prefs.rx_ps_sleep_us==4000);
    assert(_radio_profiles.primaryPreamble()==48 && rebootPreamble()==48);
    CompanionNodePrefs loaded;loadSaved(loaded);
    assert(loaded.freq==909.5f && loaded.bw==62.5f && loaded.sf==7 && loaded.cr==5);
  }
};
@DISPATCH@
// CommonCLI's infrastructure roles share the same accepted duty-cycle range.
// Extract their actual setter and boot sanitization, with only storage mocked.
struct Infrastructure {
  struct Prefs { float airtime_factor=1; } prefs;
  Prefs* _prefs=&prefs;
  uint8_t image[sizeof(float)]={};
  void savePrefs() { memcpy(image,&prefs.airtime_factor,sizeof(float)); }
  void command(const char* config,char* reply) { @INFRA_SETTER@ }
  void reboot() { memcpy(&prefs.airtime_factor,image,sizeof(float));@INFRA_SANITIZE@ }
};
static bool error(const char* reply) { return strstr(reply,"Error") || strstr(reply,"ERROR"); }
int main() {
  unsigned checks=0;
  // The outer profile validator accepts 125.005, but CommonRadioPrefs rejects
  // it. Rejection must preserve both on-disk images, even with a dirty adapter.
  for(uint32_t timestamp:{0U,1700000000U})for(bool dirty:{false,true}) {
    for(const char* command:{"set radio 915,125.005,7,5,32",
        "set radio 915,125,13,5,32", "set radio 149,125,7,5,32",
        "set radio 915,125,7,5,7", "set radio 915,125,7,5,32,extra",
        "set radio 915,nan,7,5,32"}) {
      MyMesh m;const auto profile=m.profile_fs.files["/radio_profiles"];
      const auto prefs=m.store.fs.files["/new_prefs"];
      if(dirty)m._prefs.getRadioPrefs()->markDirty();
      assert(error(m.command(command,timestamp)));
      assert(m.profile_fs.files["/radio_profiles"]==profile);
      assert(m.store.fs.files["/new_prefs"]==prefs);m.expectOriginal();++checks;
    }
  }
  // Profile-store write failure must roll back the accepted in-memory tuple;
  // preference-store failure must also restore the previously saved preamble.
  for(bool profile_failure:{false,true}) {
    MyMesh m;const auto profile=m.profile_fs.files["/radio_profiles"];
    const auto prefs=m.store.fs.files["/new_prefs"];
    m.profile_fs.fail_write=profile_failure;m.store.fs.fail_write=!profile_failure;
    assert(error(m.command("set radio 915,125,8,6,32")));
    assert(m.profile_fs.files["/radio_profiles"]==profile);
    assert(m.store.fs.files["/new_prefs"]==prefs);
    m.profile_fs.fail_write=m.store.fs.fail_write=false;
    m.expectOriginal();assert(!m._prefs.isDirty());
    assert(saveUnrelated(m));m.expectOriginal();++checks;
  }
  for(const char* command:{"set radio 915,125,8,6,32", "zz|set radio 915,125.0005,8,6,32"}) {
    MyMesh m;assert(!error(m.command(command)));
    assert(m.rebootPreamble()==32 && !m._prefs.isDirty());
    CompanionNodePrefs loaded;m.loadSaved(loaded);
    assert(loaded.freq==915 && loaded.sf==8 && loaded.cr==6);
    assert(fabsf(loaded.bw-125)<0.001f);++checks;
  }
  { // Omitted suffix intentionally restores the automatic preamble.
    MyMesh m;assert(!error(m.command("set radio 915,125,8,6")));
    assert(m.rebootPreamble()==0);++checks;
  }
  for(float duty:{1.0f,2.5f,9.9f,10.0f,100.0f}) {
    MyMesh m;char command[64];snprintf(command,sizeof(command),"set dutycycle %.1f",double(duty));
    assert(!error(m.command(command)));const float factor=m._prefs.airtime_factor;
    assert(fabsf(factor-(100/duty-1))<0.0001f);
    CompanionNodePrefs loaded;m.loadSaved(loaded);assert(loaded.airtime_factor==factor);
    MyMesh reboot;reboot.store.fs.files=m.store.fs.files;
    reboot.loadSaved(reboot._prefs);reboot.bootSanitize();
    assert(reboot._prefs.airtime_factor==factor);
    char expected[32];snprintf(expected,sizeof(expected),"> %.1f%%",double(duty));
    assert(!strcmp(reboot.command("get dutycycle"),expected));
    Infrastructure infra;char reply[160]={};infra.command(command+4,reply);
    assert(!error(reply));infra.prefs.airtime_factor=1;infra.reboot();
    assert(infra.prefs.airtime_factor==factor);++checks;
  }
  for(float invalid:{-1.0f,1000.0f,std::numeric_limits<float>::infinity(),
      std::numeric_limits<float>::quiet_NaN()}) {
    MyMesh m;m._prefs.airtime_factor=invalid;m.bootSanitize();
    const float expected=!isfinite(invalid)?1:(invalid<0?0:99);
    assert(m._prefs.airtime_factor==expected);
    Infrastructure infra;infra.prefs.airtime_factor=invalid;infra.savePrefs();infra.reboot();
    assert(infra.prefs.airtime_factor==expected);++checks;
  }
  for(const char* command:{"set dutycycle 0.9","set dutycycle 100.1","set dutycycle nan","set af 99"}) {
    MyMesh m;const auto prefs=m.store.fs.files["/new_prefs"];
    assert(error(m.command(command)));assert(m.store.fs.files["/new_prefs"]==prefs);
    assert(m._prefs.airtime_factor==1);++checks;
  }
  printf("%u production primary-radio persistence scenarios passed\n",checks);
}
'''


class CompanionPrimaryRadioPersistenceTests(unittest.TestCase):
    def test_production_validation_rollback_and_dutycycle_boot(self):
        companion = (ROOT/'examples/companion_radio/MyMesh.cpp').read_text(encoding='utf-8')
        header = (ROOT/'examples/companion_radio/MyMesh.h').read_text(encoding='utf-8')
        infra = (ROOT/'src/helpers/CommonCLI.cpp').read_text(encoding='utf-8')
        datastore = (ROOT/'examples/companion_radio/DataStore.cpp').read_text(encoding='utf-8')
        handler = extract_braced(companion, 'bool MyMesh::handleCommand(')
        # Keep the complete preprocessing sequence through the shared primary
        # tuple validation. Only unrelated dispatch routes are omitted.
        dispatch = handler[:handler.index('  if (sender_timestamp == 0 && handleDirectCommand')]
        dispatch += extract_braced(handler, 'if (isCompanionRadioPrefsCommand(command))')
        dispatch += '\nreturn false;\n}'

        def boot_checks(source, member):
            nonfinite = re.search(r'if \(!isfinite\(' + re.escape(member) + r'\)\)[^;]+;', source)
            clamp = re.search(re.escape(member) + r' = constrain\([^;]+;', source)
            self.assertIsNotNone(nonfinite)
            self.assertIsNotNone(clamp)
            return nonfinite.group() + '\n' + clamp.group()

        support = PREFERENCES_HARNESS[:PREFERENCES_HARNESS.index('int main(')]
        support = support.replace('@METHODS@', esp_recovery_helpers(datastore) + '\n'.join(extract_braced(datastore, sig) for sig in (
            'bool DataStore::loadPrefs(', 'bool DataStore::loadPrefsInt(', 'bool DataStore::savePrefs(')))
        replacements = {
            '@SAVE_PREFS@': extract_braced(header, 'bool savePrefs()'),
            '@COMMAND_FILTER@': extract_braced(companion, 'static bool isCompanionRadioPrefsCommand('),
            '@DISPATCH@': dispatch,
            '@COMPANION_SANITIZE@': boot_checks(companion, '_prefs.airtime_factor'),
            '@INFRA_SANITIZE@': boot_checks(infra, '_prefs->airtime_factor'),
            '@INFRA_SETTER@': extract_braced(infra, 'if (memcmp(config, "dutycycle ", 10) == 0)'),
        }
        harness = support + HARNESS
        for marker, value in replacements.items():
            harness = harness.replace(marker, value)
        # Saving an unrelated field must not resurrect a failed tuple later.
        harness = harness.replace('static bool error(', '''static bool saveUnrelated(MyMesh& m) {
  m._prefs.ble_pin=654321;return m.savePrefs();
}
static bool error(''')
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='companion-primary-radio-') as directory:
            work = Path(directory)
            # Reuse the profile fixture's identity/crypto mocks and add only
            # unused JSON conversion links required by real NodePrefs adapters.
            utils = (ROOT/'test/mocks/Utils.h').read_text()
            utils = utils.replace('class Utils {\npublic:', '''class Utils {
public:
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
''')
            (work/'Utils.h').write_text('#include <Arduino.h>\n#include <cassert>\n'+utils)
            (work/'Identity.h').write_text((ROOT/'test/mocks/Identity.h').read_text())
            transaction = (ROOT/'src/helpers/ContactFileTransaction.h').read_text()
            (work/'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            (work/'test.cpp').write_text(harness, encoding='utf-8')
            binary = work/('test.exe' if os.name=='nt' else 'test')
            flags = [] if os.name=='nt' else ['-fsanitize=address,undefined',
                '-fno-sanitize-recover=all','-fno-pie','-no-pie']
            build = subprocess.run([compiler,'-std=c++17','-Wall','-Wextra',
                '-Wno-unused-parameter','-Wno-sign-compare','-Wno-reorder',
                '-DESP32_PLATFORM=1',*flags,'-I',str(work),
                '-I',str(ROOT/'test/fixtures/radio_profiles/mocks'),
                '-I',str(ROOT/'test/mocks'),'-I',str(ROOT/'src'),
                '-I',str(ROOT/'src/helpers'),'-I',str(ROOT),str(work/'test.cpp'),
                *[str(ROOT/'src/helpers'/name) for name in (
                    'ConfigSerializer.cpp','DynamicConfigSerializer.cpp',
                    'CommonRadioPrefs.cpp','TxtDataHelpers.cpp','RadioProfileCLI.cpp')],
                '-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stdout+build.stderr)
            run = subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)


if __name__=='__main__':
    unittest.main()
