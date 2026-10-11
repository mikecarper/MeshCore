"""Verify the compact Companion tail preserves bytes and atomic failure handling."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_companion_preferences_transaction import HARNESS, esp_recovery_helpers
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

LEGACY_TAIL = r'''
    success = success && file.write((uint8_t *)&_prefs.flood_retry_attempts, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.flood_retry_max_path, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.flood_retry_group_max_path, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.flood_retry_advert_enabled, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.one_key_dm_enabled, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.bluetooth_enabled, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.gps_sync_interval_hours, 2) == 2;
    success = success && file.write((uint8_t *)&_prefs.usb_debug_enabled, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.lost_reply, 1) == 1;
'''

TESTS = r'''
int main() {
  CompanionNodePrefs original;
  strcpy(original.node_name, "old complete image");
  original.freq=915.125f; original.ble_pin=456789;
  original.flood_retry_attempts=4; original.flood_retry_max_path=3;
  original.flood_retry_group_max_path=2; original.flood_retry_advert_enabled=1;
  original.one_key_dm_enabled=1; original.bluetooth_enabled=0;
  original.gps_sync_interval_hours=336; original.usb_debug_enabled=1;
  original.lost_reply=2;
#ifdef TBEAM_1W
  strcpy(original.fan_mode,"on"); original.fan_lo=32; original.fan_hi=45;
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
  strcpy(original.wifi_ssid,"tail-test"); strcpy(original.wifi_pwd,"same bytes");
#endif
  // Compare every byte against the previous scalar serializer, including
  // arbitrary byte values that a raw caller might pass before CLI validation.
  for (unsigned value=0; value<256; ++value) {
    CompanionNodePrefs prefs=original;
    prefs.flood_retry_attempts=uint8_t(value);
    prefs.flood_retry_max_path=uint8_t(value+17);
    prefs.flood_retry_group_max_path=uint8_t(value+37);
    prefs.flood_retry_advert_enabled=uint8_t(value+53);
    prefs.one_key_dm_enabled=uint8_t(value+71);
    prefs.bluetooth_enabled=uint8_t(value+89);
    prefs.gps_sync_interval_hours=uint16_t(value*257);
    prefs.usb_debug_enabled=uint8_t(value+103);
    prefs.lost_reply=uint8_t(value);
    DataStore compact, legacy;
    assert(compact.savePrefs(prefs,47.1,-122.2));
    assert(legacy.savePrefsLegacy(prefs,47.1,-122.2));
    const auto& image=compact.fs.files["/new_prefs"];
    assert(image==legacy.fs.files["/new_prefs"]);
#if defined(TBEAM_1W)
    assert(image.size()==243);
#elif defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
    assert(image.size()==333);
#else
    assert(image.size()==236);
#endif
    const uint8_t expected[]={prefs.flood_retry_attempts,prefs.flood_retry_max_path,
        prefs.flood_retry_group_max_path,prefs.flood_retry_advert_enabled,
        prefs.one_key_dm_enabled,prefs.bluetooth_enabled,0,0,prefs.usb_debug_enabled,
        prefs.lost_reply};
    assert(!memcmp(image.data()+image.size()-10,expected,6));
    assert(!memcmp(image.data()+image.size()-4,&prefs.gps_sync_interval_hours,2));
    assert(image[image.size()-2]==prefs.usb_debug_enabled);
    assert(image.back()==prefs.lost_reply);
  }
  DataStore initial; assert(initial.savePrefs(original,47.1,-122.2));
  const auto disk=initial.fs.files["/new_prefs"];
  CompanionNodePrefs changed=original;
  strcpy(changed.node_name,"new complete image"); changed.freq=910.5f;
  changed.flood_retry_attempts=7; changed.bluetooth_enabled=1;
  changed.gps_sync_interval_hours=24; changed.usb_debug_enabled=0;
  changed.lost_reply=1;
  auto expectOld=[&](DataStore& store) {
    assert(store.fs.files["/new_prefs"]==disk);
    assert(!store.fs.exists("/new_prefs.tmp"));
    DataStore reboot; reboot.fs.files["/new_prefs"]=disk;
    CompanionNodePrefs loaded; double lat=0,lon=0;
    assert(reboot.loadPrefs(loaded,lat,lon));
    assert(!strcmp(loaded.node_name,original.node_name));
    assert(loaded.freq==original.freq && loaded.ble_pin==original.ble_pin);
    assert(loaded.flood_retry_attempts==4 && loaded.bluetooth_enabled==0);
    assert(loaded.gps_sync_interval_hours==336 && loaded.usb_debug_enabled==1);
    assert(loaded.lost_reply==2);
    assert(lat==47.1 && lon==-122.2);
  };
  // A short return at each boundary of the single final write must abort the
  // transaction, including either byte of the two-byte GPS field.
  for (unsigned boundary=0; boundary<10; ++boundary) {
    DataStore store; store.fs.files["/new_prefs"]=disk;
    store.fs.fail_write_after=int(disk.size()-10+boundary);
    assert(!store.savePrefs(changed,42.3,-121.2)); expectOld(store);
    store.fs.fail_write_after=-1;
    assert(store.savePrefs(changed,42.3,-121.2));
    assert(store.fs.files["/new_prefs"]!=disk);
  }
  // Fully written bytes still cannot replace the live image until readback
  // verifies every final-tail boundary. LittleFS's retries also must fail.
  for (unsigned boundary=0; boundary<10; ++boundary) {
    DataStore store; store.fs.files["/new_prefs"]=disk;
    store.fs.fail_read_after=int(disk.size()-10+boundary);
    assert(!store.savePrefs(changed,42.3,-121.2)); expectOld(store);
  }
  {
    DataStore store; store.fs.files["/new_prefs"]=disk;
    store.fs.fail_read_open=true;
    assert(!store.savePrefs(changed,42.3,-121.2)); expectOld(store);
  }
#if defined(STM32_PLATFORM) || defined(NRF52_PLATFORM)
  const unsigned rename_steps=1;
#else
  const unsigned rename_steps=2;
#endif
  for (unsigned step=1; step<=rename_steps; ++step) {
    DataStore store; store.fs.files["/new_prefs"]=disk;
    store.fs.fail_rename=int(step);
    assert(!store.savePrefs(changed,42.3,-121.2)); expectOld(store);
  }
  assert(initial.savePrefs(changed,42.3,-121.2));
  CompanionNodePrefs loaded; double lat=0,lon=0;
  assert(initial.loadPrefs(loaded,lat,lon));
  assert(!strcmp(loaded.node_name,changed.node_name));
  assert(loaded.flood_retry_attempts==7 && loaded.bluetooth_enabled==1);
  assert(loaded.gps_sync_interval_hours==24 && loaded.usb_debug_enabled==0);
  assert(loaded.lost_reply==1);
  assert(lat==42.3 && lon==-121.2);
}
'''


class CompanionPrefsTailTests(unittest.TestCase):
    def test_full_image_parity_and_atomic_tail_faults(self):
        source = (ROOT / 'examples/companion_radio/DataStore.cpp').read_text()
        save = method(source, 'bool DataStore::savePrefs(')
        # Keep the complete original scalar writer independent of any future
        # production field-table changes, including every native tail byte.
        legacy_save = (ROOT / 'test/fixtures/companion_prefs_storage/legacy_writer.inc').read_text()
        methods = esp_recovery_helpers(source) + '\n'.join(method(source, signature) for signature in (
            'bool DataStore::loadPrefs(', 'bool DataStore::loadPrefsInt('))
        methods += '\n' + save + '\n' + legacy_save
        code = HARNESS.split('int main(')[0].replace('@METHODS@', methods)
        code = code.replace(' bool savePrefs(const CompanionNodePrefs&, double, double);',
                            ' bool savePrefs(const CompanionNodePrefs&, double, double);\n'
                            ' bool savePrefsLegacy(const CompanionNodePrefs&, double, double);')
        with tempfile.TemporaryDirectory(prefix='meshcore-prefs-tail-') as directory:
            work = Path(directory)
            (work / 'Utils.h').write_text('''#pragma once
#include <Arduino.h>
#include <cassert>
namespace mesh { struct Utils {
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
}; }
''')
            (work / 'platform_shim.h').write_text('''#include <cstdlib>
#include <cstdio>
inline char* utoa(unsigned int value,char* output,int base){
 if(base!=10)abort();sprintf(output,"%u",value);return output;
}
''')
            transaction = (ROOT / 'src/helpers/ContactFileTransaction.h').read_text()
            (work / 'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            cpp = work / 'test.cpp'
            cpp.write_text(code + TESTS)
            for platform in ('STM32_PLATFORM', 'NRF52_PLATFORM', 'ESP32_PLATFORM', 'RP2040_PLATFORM',
                             'ESP32_PLATFORM,TBEAM_1W', 'RP2040_PLATFORM,ENABLE_WIFI_INTERFACE'):
                with self.subTest(platform=platform):
                    binary = work / 'test'
                    sanitizers = [] if os.name == 'nt' else [
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                        '-fno-pie', '-no-pie']
                    built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                        *['-D' + flag + '=1' for flag in platform.split(',')],
                        *sanitizers, '-include', str(work / 'platform_shim.h'),
                        '-I', str(work), '-I', str(ROOT / 'test/fixtures/radio_profiles/mocks'),
                        '-I', str(ROOT / 'test/mocks'), '-I', str(ROOT / 'src'),
                        '-I', str(ROOT / 'src/helpers'), '-I', str(ROOT), str(cpp),
                        *[str(ROOT / 'src/helpers' / name) for name in (
                            'ConfigSerializer.cpp', 'DynamicConfigSerializer.cpp',
                            'CommonRadioPrefs.cpp', 'TxtDataHelpers.cpp')],
                        '-o', str(binary)], capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == '__main__':
    unittest.main()
