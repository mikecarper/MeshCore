"""Exercise real Companion preferences saves and startup recovery with I/O faults."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <new>
#include <helpers/IdentityStore.h>
#include "ContactFileTransaction.h"
#if defined(STM32_PLATFORM) || defined(NRF52_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#endif
#include "examples/companion_radio/NodePrefs.h"
#define MESH_DEBUG_PRINTLN(...) ((void)0)
struct DataStore {
 MemoryFS fs;
 FILESYSTEM* _fs=&fs;
 bool _prefs_load_incomplete=false, _primary_storage_unavailable=false;
 bool _secondary_authority_unknown=false;
 DataStore(){
#if defined(STM32_PLATFORM) || defined(NRF52_PLATFORM)
   fs.rename_replaces=true;
#endif
 }
 bool loadPrefs(CompanionNodePrefs&, double&, double&);
 bool loadPrefsInt(const char*, CompanionNodePrefs&, double&, double&);
 bool savePrefs(const CompanionNodePrefs&, double, double);
};
File openRead(FILESYSTEM* fs,const char* path){return fs->open(path,"r");}
File openWrite(FILESYSTEM* fs,const char* path){return fs->open(path,"w");}
bool contactPathPresence(FILESYSTEM* fs,const char* path,bool& present){
 present=fs->exists(path);return true;
}
@METHODS@
int main(int argc,char** argv){
 const int scenario=argc>1?atoi(argv[1]):0;
 DataStore store;CompanionNodePrefs original;
 strcpy(original.node_name,"durable node");original.freq=910.525f;
 original.ble_pin=876543;original.bluetooth_stealth_mode=2;
 original.gps_interval=123;original.sf=7;original.cr=7;original.bw=62.5f;
 assert(store.savePrefs(original,47.1,-122.2));
 const auto disk=store.fs.files["/new_prefs"];
 CompanionNodePrefs changed=original;changed.freq=910.1f;
 if(scenario==0){
   store.fs.fail_write=true;
   assert(!store.savePrefs(changed,42.3,-121.2));
   assert(store.fs.files["/new_prefs"]==disk);
   store.fs.fail_write=false;store.fs.fail_write_after=17;
   assert(!store.savePrefs(changed,42.3,-121.2));
   assert(store.fs.files["/new_prefs"]==disk);
   store.fs.fail_write_after=-1;
#if defined(STM32_PLATFORM) || defined(NRF52_PLATFORM)
   const int rename_steps[]={1};
#else
   const int rename_steps[]={1,2};
#endif
   for(int fail_step : rename_steps){
     store.fs.fail_rename=fail_step;
     assert(!store.savePrefs(changed,42.3,-121.2));
     assert(store.fs.files["/new_prefs"]==disk);
   }
   assert(store.savePrefs(changed,42.3,-121.2));
   CompanionNodePrefs loaded;double lat=0,lon=0;
   assert(store.loadPrefs(loaded,lat,lon));
   assert(loaded.freq==changed.freq&&loaded.ble_pin==original.ble_pin);
   assert(lat==42.3&&lon==-121.2);
 } else if(scenario==1){
   for(int fault : {0,1,2,3,4}){
     for(const char* path : {"/new_prefs","/node_prefs"}){
       DataStore boot;boot.fs.files[path]=disk;
       CompanionNodePrefs live;double lat=1,lon=2;
       if(fault==0)boot.fs.fail_read_open=true;
       if(fault==1)boot.fs.fail_read_after=0;
       if(fault==2)boot.fs.fail_read_after=84;
       if(fault==3)boot.fs.fail_read_after=214;
       if(fault==4)boot.fs.files[path].resize(87);
       const auto durable=boot.fs.files[path];
       assert(!boot.loadPrefs(live,lat,lon));
       assert(live.node_name[0]==0&&lat==1&&lon==2);
       boot.fs.fail_read_after=-1;boot.fs.fail_read_open=false;
       assert(!boot.loadPrefs(live,lat,lon)); // quarantine lasts this boot
       assert(!boot.savePrefs(live,lat,lon));
       assert(boot.fs.files[path]==durable);
     }
   }
   DataStore fresh;CompanionNodePrefs defaults;double lat=0,lon=0;
   assert(fresh.loadPrefs(defaults,lat,lon));
   assert(fresh.savePrefs(defaults,lat,lon));
 } else if(scenario==2){
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
   store.fs.fail_rename_from={"/new_prefs.tmp","/new_prefs.bak"};
   assert(!store.savePrefs(changed,42.3,-121.2));
   assert(!store.fs.exists("/new_prefs"));
   assert(store.fs.files["/new_prefs.bak"]==disk);
   DataStore failed_boot;failed_boot.fs=store.fs;
   CompanionNodePrefs loaded;double lat=0,lon=0;
   assert(!failed_boot.loadPrefs(loaded,lat,lon));
   failed_boot.fs.fail_rename_from.clear();
   assert(!failed_boot.loadPrefs(loaded,lat,lon));
   assert(!failed_boot.savePrefs(changed,42.3,-121.2));
   assert(failed_boot.fs.files["/new_prefs.bak"]==disk);
   DataStore reboot;reboot.fs=failed_boot.fs;
   assert(reboot.loadPrefs(loaded,lat,lon));
   assert(loaded.freq==original.freq&&loaded.ble_pin==original.ble_pin);
   assert(lat==47.1&&lon==-122.2);
#endif
 } else if(scenario==3){
   // Copy the real adapter-bearing preferences from dirty storage. This
   // catches reads of uninitialized serializer bools under UBSan reliably.
   alignas(CompanionNodePrefs) unsigned char memory[sizeof(CompanionNodePrefs)];
   memset(memory,0xa5,sizeof(memory));
   auto* prefs=new(memory)CompanionNodePrefs();
   CompanionNodePrefs snapshot=*prefs;
   assert(!snapshot.isDirty());
   prefs->~CompanionNodePrefs();
 } else if(scenario==4){
   DataStore legacy;legacy.fs.files["/node_prefs"]=disk;
   legacy.fs.fail_write_after=17;
   CompanionNodePrefs loaded;double lat=0,lon=0;
   assert(legacy.loadPrefs(loaded,lat,lon));
   assert(loaded.freq==original.freq);
   assert(legacy.fs.files["/node_prefs"]==disk&&!legacy.fs.exists("/new_prefs"));
   legacy.fs.fail_write_after=-1;
   assert(legacy.loadPrefs(loaded,lat,lon));
   assert(!legacy.fs.exists("/node_prefs")&&legacy.fs.files["/new_prefs"]==disk);
 }
}
'''


class CompanionPreferencesTransactionTests(unittest.TestCase):
    def test_real_preferences_io_failures(self):
        source = (ROOT / 'examples/companion_radio/DataStore.cpp').read_text()
        methods = '\n'.join(method(source, signature) for signature in (
            'bool DataStore::loadPrefs(', 'bool DataStore::loadPrefsInt(',
            'bool DataStore::savePrefs('))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            # Binary preference persistence never invokes JSON blob conversion.
            # Satisfy those unrelated serializer links without crypto libraries.
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
            (work / 'test.cpp').write_text(HARNESS.replace('@METHODS@', methods))
            for platform in ('ESP32_PLATFORM', 'RP2040_PLATFORM', 'STM32_PLATFORM', 'NRF52_PLATFORM'):
                with self.subTest(platform=platform):
                    binary = work / 'test'
                    build = subprocess.run(['g++', '-std=c++17',
                        '-D'+platform+'=1', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                        '-fno-pie', '-no-pie', '-include', str(work / 'platform_shim.h'),
                        '-I', str(work),
                        '-I', str(ROOT / 'test/fixtures/radio_profiles/mocks'),
                        '-I', str(ROOT / 'test/mocks'), '-I', str(ROOT / 'src'),
                        '-I', str(ROOT / 'src/helpers'), '-I', str(ROOT),
                        str(work / 'test.cpp'),
                        str(ROOT / 'src/helpers/ConfigSerializer.cpp'),
                        str(ROOT / 'src/helpers/DynamicConfigSerializer.cpp'),
                        str(ROOT / 'src/helpers/CommonRadioPrefs.cpp'),
                        str(ROOT / 'src/helpers/TxtDataHelpers.cpp'),
                        '-o', str(binary)], capture_output=True, text=True)
                    self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                    for scenario in range(5):
                        with self.subTest(scenario=scenario):
                            run = subprocess.run([str(binary), str(scenario)], capture_output=True, text=True)
                            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
