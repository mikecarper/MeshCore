"""Exercise production infrastructure radio saves with real file transactions."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <math.h>
#include <cstring>
#include <helpers/IdentityStore.h>
#include "ContactFileTransaction.h"
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#include <helpers/PrefsSaveRouting.h>
#include <helpers/DiscoveryResponsePrefs.h>
#include <helpers/GpsPowerPolicy.h>
#include <helpers/radiolib/RXPowerSaving.h>
#if defined(ESP32_PLATFORM)
#include <helpers/esp32/CpuClockPolicy.h>
#endif
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define MIN_LORA_TX_POWER -9
#define MAX_LORA_TX_POWER 22
#include <helpers/radiolib/RadioPowerLimits.h>
struct NodePrefs { @FIELDS@ };
struct RadioPrefsAdapter {
  NodePrefs* _parent;
  bool dirty = false;
  void markDirty() { dirty = true; }
  @RXPS_READ@
};
void markDirectRetryPrefsValid(NodePrefs*) {}
bool isValidLoRaBandwidth(float bw) { return bw==62.5f || bw==125; }
namespace mesh {
struct RadioProfiles { static constexpr unsigned MaxPreamble=65535; };
void setUsbLoggingEnabled(bool) {}
void setUsbDebugEnabled(bool) {}
}
struct Profiles {
  bool accepted=true;
  uint16_t saved=48;
  bool acceptsPrimary(float,float,uint8_t,uint8_t,uint16_t) const { return accepted; }
  void adoptPrimaryPreamble(uint16_t value) { saved=value; }
};
struct CommonCLI;
struct Callbacks {
  CommonCLI* cli;
  void savePrefs(PrefsSaveRouting::Scope scope);
};
struct CommonCLI {
#if defined(ESP32_PLATFORM)
  struct CpuBoard {
    bool supportsCpuFrequencyMHz(uint16_t mhz) const {
      return mhz == 80 || mhz == 160 || mhz == 240;
    }
  } cpu_board;
  CpuBoard* _board = &cpu_board;
#endif
  MemoryFS fs;
  NodePrefs prefs{};
  NodePrefs* _prefs=&prefs;
  Profiles _radio_profiles;
  Callbacks callbacks{this};
  Callbacks* _callbacks=&callbacks;
  bool _common_save_result_known=false, _common_save_succeeded=false;
  CommonCLI() {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
    fs.rename_replaces=true;
#endif
    prefs.freq=910; prefs.bw=62.5f; prefs.sf=7; prefs.cr=5;
    prefs.tx_power_dbm=30; prefs.primary_radio_preamble=48;
    prefs.espnow_bridge_enabled=1;
    prefs.discovery_responses=3;
    prefs.gps_sync_interval_hours=336;
    prefs.rx_ps_rx_us=111; prefs.rx_ps_sleep_us=222;
  }
  void savePrefs(FILESYSTEM*,PrefsSaveRouting::Scope);
  bool saveCommonPrefs();
  bool savePrimaryRadioParams(float,float,uint8_t,uint8_t,uint16_t);
  bool acceptsPrimaryRadioParams(float,float,uint8_t,uint8_t,uint16_t);
  static void recalculateRxPowerSavingFromLevel(NodePrefs* p) {
    p->rx_ps_rx_us=p->sf*100; p->rx_ps_sleep_us=p->cr*200;
  }
  uint16_t loadTail(std::vector<uint8_t> bytes) {
    fs.files["/tail"]=bytes;
    File file=fs.open("/tail");
    @TAIL@
    return prefs.primary_radio_preamble;
  }
};
void Callbacks::savePrefs(PrefsSaveRouting::Scope scope) { cli->savePrefs(&cli->fs,scope); }
@METHODS@
@SERIALIZER@
struct Capture { std::vector<uint8_t> bytes;
 size_t write(const uint8_t* p,size_t n) { bytes.insert(bytes.end(),p,p+n);return n; }
};
int main() {
  NodePrefs stored{};
  stored.freq = 911.150f;
  stored.powersaving_enabled = 1;
  stored.rx_powersaving_enabled = 1;
  stored.rx_ps_rx_us = 1234;
  stored.rx_ps_sleep_us = 5678;
  stored.rx_ps_level = 6;
  stored.rx_ps_preamble = 32;
  RadioPrefsAdapter adapter{&stored};
  auto snapshot = adapter.readRxPowerSavingConfig();
  assert(snapshot.enabled == 1 && snapshot.rx_us == 1234);
  assert(snapshot.sleep_us == 5678 && snapshot.level == 6 && snapshot.preamble == 32);
  snapshot.enabled = 0;
  snapshot.rx_us = 2345;
  snapshot.sleep_us = 6789;
  snapshot.level = 7;
  snapshot.preamble = 16;
  assert(stored.rx_ps_sleep_us == 5678 && !adapter.dirty);
  assert(!adapter.dirty && stored.rx_powersaving_enabled == 1);
  assert(stored.rx_ps_rx_us == 1234 && stored.rx_ps_sleep_us == 5678);
  assert(stored.rx_ps_level == 6 && stored.rx_ps_preamble == 32);
  assert(stored.freq == 911.150f && stored.powersaving_enabled == 1);
  CommonCLI cli;
  assert(cli.saveCommonPrefs());
  auto original=cli.fs.files["/com_prefs"];
  // Appending preferences must not move the established preamble bytes.
  const size_t preamble_offset=866;
  assert(original.size()==877);
  assert(original[preamble_offset]==48 && original[preamble_offset+1]==0);
  assert(original[preamble_offset+2]==1);
  assert(original[preamble_offset+3]==0x50 && original[preamble_offset+4]==0x01);
  assert(original[871]==0 && original[872]==0); // debug and trace remain appended
  assert(original[873]==0); // channel selection does not move the previous fields
  Capture capture; assert(writeCommonPrefsImage(capture,&cli.prefs));
  assert(capture.bytes==original); // Both serializers retain the same layout.
  for (int fault : {0,1,2,3,4}) {
    const NodePrefs previous=cli.prefs;
    if(fault==0)cli.fs.fail_write=true;
    if(fault==1)cli.fs.fail_write_after=100;
    if(fault==2)cli.fs.fail_read_open=true;
    if(fault==3)cli.fs.fail_rename=1;
    if(fault==4)cli._radio_profiles.accepted=false;
    assert(!cli.savePrimaryRadioParams(920,125,9,6,96));
    assert(cli.prefs.freq==previous.freq && cli.prefs.bw==previous.bw);
    assert(cli.prefs.sf==previous.sf && cli.prefs.cr==previous.cr);
    assert(cli.prefs.tx_power_dbm==previous.tx_power_dbm);
    assert(cli.prefs.rx_ps_rx_us==previous.rx_ps_rx_us);
    assert(cli.prefs.rx_ps_sleep_us==previous.rx_ps_sleep_us);
    assert(cli.prefs.primary_radio_preamble==48 && cli._radio_profiles.saved==48);
    assert(cli.fs.files["/com_prefs"]==original);
    cli.fs.fail_write=false;cli.fs.fail_write_after=-1;
    cli.fs.fail_read_open=false;cli.fs.fail_rename=0;
    cli._radio_profiles.accepted=true;
  }
  assert(cli.savePrimaryRadioParams(920,125,9,6,96));
  auto committed=cli.fs.files["/com_prefs"];
  float freq=0,bw=0;memcpy(&freq,committed.data()+72,4);memcpy(&bw,committed.data()+116,4);
  assert(freq==920 && bw==125 && committed[112]==9 && committed[113]==6);
  assert(committed[preamble_offset]==96 && committed[preamble_offset+1]==0);
  assert(committed[preamble_offset+2]==1);
  assert(committed[preamble_offset+3]==0x50 && committed[preamble_offset+4]==0x01);
  assert(committed[871]==0 && committed[872]==0);
  assert(cli._radio_profiles.saved==96 && cli.prefs.tx_power_dbm==22);
  assert(cli.prefs.rx_ps_rx_us==900 && cli.prefs.rx_ps_sleep_us==1200);
  // CR4 is saved at the same legacy byte offset, with the same atomic rollback.
  assert(cli.savePrimaryRadioParams(920,125,9,4,96));
  assert(cli.fs.files["/com_prefs"].size()==committed.size());
  assert(cli.fs.files["/com_prefs"][113]==4 && cli.prefs.cr==4);
  for(uint8_t cr:{uint8_t(3),uint8_t(9)}) {
    const auto image=cli.fs.files["/com_prefs"];
    assert(!cli.savePrimaryRadioParams(920,125,9,cr,96));
    assert(cli.prefs.cr==4 && cli.fs.files["/com_prefs"]==image);
  }
  committed=cli.fs.files["/com_prefs"];
  // Old and torn tails preserve the imported legacy preamble; valid tails win.
  assert(cli.loadTail({0})==96);
  assert(cli.loadTail({0,32})==96);
  assert(cli.loadTail({0,64,0})==64);
  assert(cli.loadTail({0,7,0})==64);
  assert(cli.loadTail({0,0,0})==0);
  assert(cli.loadTail({0,64,0,0})==64 && cli.prefs.espnow_bridge_enabled==0);
  assert(cli.loadTail({0,64,0,1,48,0})==64 && cli.prefs.gps_sync_interval_hours==48);
  assert(cli.loadTail({0,64,0,1,0xff})==64 && cli.prefs.gps_sync_interval_hours==48);
  for(float bad : {NAN,INFINITY,149.0f,2501.0f})
    assert(!cli.savePrimaryRadioParams(bad,125,9,6,0));
  for(float bad : {NAN,INFINITY,-INFINITY,-125.0f})
    assert(!cli.savePrimaryRadioParams(920,bad,9,6,0));
  assert(cli.fs.files["/com_prefs"]==committed);
}
'''


class CommonRadioPersistenceTest(unittest.TestCase):
    def test_snapshot_constructors_and_both_real_adapters_support_cpp11(self):
        source = r'''
#include <helpers/radiolib/RXPowerSaving.h>
#include <cassert>
#include <cstddef>
#include <initializer_list>
#include <type_traits>
constexpr RxPowerSavingConfig defaults;
static_assert(defaults.enabled == 0, "Disabled default");
static_assert(defaults.rx_us == RX_POWERSAVING_DEFAULT_RX_US, "Default RX period");
static_assert(defaults.sleep_us == RX_POWERSAVING_DEFAULT_SLEEP_US, "Default sleep period");
static_assert(defaults.level == RX_POWERSAVING_BALANCED_LEVEL, "Default level");
static_assert(defaults.preamble == RX_POWERSAVING_PROFILE_PREAMBLE, "Default preamble");
constexpr RxPowerSavingConfig explicit_config(1, 1234, 5678, 7, 32);
static_assert(explicit_config.enabled == 1 && explicit_config.rx_us == 1234
    && explicit_config.sleep_us == 5678 && explicit_config.level == 7
    && explicit_config.preamble == 32, "Value constructor");
static_assert(std::is_standard_layout<RxPowerSavingConfig>::value, "Snapshot layout");
static_assert(std::is_trivially_copyable<RxPowerSavingConfig>::value, "Value snapshot");
struct Fields {
  uint8_t rx_powersaving_enabled = 0;
  uint32_t rx_ps_rx_us = 0, rx_ps_sleep_us = 0;
  uint8_t rx_ps_level = 0, rx_ps_preamble = 0;
};
struct CommonAdapter { Fields* _parent; @COMMON_READ@ };
struct CompanionAdapter { Fields* _parent; @COMPANION_READ@ };
int main() {
  for (uint8_t enabled : {0, 1}) {
    Fields fields;
    fields.rx_powersaving_enabled = enabled;
    fields.rx_ps_rx_us = 30000000;
    fields.rx_ps_sleep_us = 2345;
    fields.rx_ps_level = 10;
    fields.rx_ps_preamble = 128;
    CommonAdapter common{&fields};
    CompanionAdapter companion{&fields};
    auto a = common.readRxPowerSavingConfig();
    auto b = companion.readRxPowerSavingConfig();
    for (const auto& snapshot : {a, b}) {
      assert(snapshot.enabled == enabled && snapshot.rx_us == 30000000
          && snapshot.sleep_us == 2345 && snapshot.level == 10 && snapshot.preamble == 128);
    }
    a.sleep_us = b.rx_us = 0;
    assert(fields.rx_ps_rx_us == 30000000 && fields.rx_ps_sleep_us == 2345);
  }
}
'''
        for marker, path in (
            ('COMMON_READ', 'src/helpers/CommonCLI.h'),
            ('COMPANION_READ', 'examples/companion_radio/NodePrefs.h'),
        ):
            header = (ROOT / path).read_text(encoding='ascii')
            adapter = extract_braced(header, 'RxPowerSavingConfig readRxPowerSavingConfig(')
            source = source.replace('@' + marker + '@', adapter.replace(' override', ''))
        sanitizers = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='rxps-snapshot-cpp11-') as directory:
            work = Path(directory)
            cpp, binary = work / 'snapshot.cpp', work / 'snapshot'
            cpp.write_text(source, encoding='ascii')
            for standard in ('c++11', 'c++17'):
                with self.subTest(standard=standard):
                    built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=' + standard,
                        '-Wall', '-Wextra', '-Werror', *sanitizers, '-I' + str(ROOT / 'src'),
                        str(cpp), '-o', str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_production_transactions(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text(encoding='utf-8')
        header = (ROOT / 'src/helpers/CommonCLI.h').read_text(encoding='utf-8')
        fields = header.split('class NodePrefs :', 1)[1].split('private:', 1)[0]
        fields = '\n'.join(re.findall(
            r'^\s*(?:float|double|char|u?int(?:8|16|32)_t)\s+[^;]+;', fields, re.M))
        fields = re.sub(r'\s*=\s*[^,;]+', '', fields)
        for macro in set(re.findall(r'\bFLOOD_\w+', fields)):
            value = re.search(r'#define\s+' + macro + r'\s+(\d+)', header).group(1)
            fields = re.sub(r'\b'+macro+r'\b', value, fields)
        methods = '\n'.join(extract_braced(source, signature) for signature in (
            'void CommonCLI::savePrefs(FILESYSTEM*', 'bool CommonCLI::saveCommonPrefs(',
            'bool CommonCLI::savePrimaryRadioParams(',
            'bool CommonCLI::acceptsPrimaryRadioParams('))
        serializer = 'template<typename Writer>\n' + extract_braced(source, 'static bool writeCommonPrefsImage(')
        tail = extract_braced(source, 'if (file.available() >= (int)sizeof(_prefs->bridge_format))')
        code = HARNESS.replace('@FIELDS@',fields).replace('@METHODS@',("\n#include <stddef.h>\ntemplate<typename Writer>\n" + extract_braced(source, "static void writeCommonRetryPrefs(") + "\n" + methods))
        code = code.replace('@SERIALIZER@',serializer).replace('@TAIL@',tail)
        adapter = extract_braced(header, 'RxPowerSavingConfig readRxPowerSavingConfig(').replace(' override', '')
        code = code.replace('@RXPS_READ@', adapter)
        sanitizer_flags = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='common-radio-save-') as directory:
            work = Path(directory)
            transaction = (ROOT / 'src/helpers/ContactFileTransaction.h').read_text()
            (work / 'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            (work / 'test.cpp').write_text(code, encoding='utf-8')
            for platform in ('NRF52_PLATFORM','STM32_PLATFORM','ESP32_PLATFORM','RP2040_PLATFORM'):
                with self.subTest(platform=platform):
                    exe = work / 'test'
                    built = subprocess.run(['g++','-std=c++17','-DENABLE_OTA=1','-D'+platform+'=1',
                        *sanitizer_flags,
                        '-I'+str(work),'-I'+str(ROOT/'test/fixtures/radio_profiles/mocks'),
                        '-I'+str(ROOT/'test/mocks'),
                        '-I'+str(ROOT/'src'),'-I'+str(ROOT/'src/helpers'),
                        str(work/'test.cpp'),'-o',str(exe)],
                        capture_output=True,text=True,timeout=60)
                    self.assertEqual(built.returncode,0,built.stderr)
                    tested = subprocess.run([str(exe)],capture_output=True,text=True,timeout=10)
                    self.assertEqual(tested.returncode,0,tested.stderr)


if __name__ == '__main__':
    unittest.main()
