"""Execute persistent Bluetooth preferences, storage faults, and boot gates."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_companion_preferences_transaction import HARNESS as STORE_HARNESS
from test_companion_preferences_transaction import esp_recovery_helpers
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

PREFERENCE_HARNESS = r'''
struct MyMesh {
 CompanionNodePrefs _prefs;
 DataStore store;
 unsigned saves=0;
 bool savePrefs(){++saves;return store.savePrefs(_prefs,47.1,-122.2);}
 @SAVE_PREFERENCE@
 bool setBluetoothEnabledPreference(bool);
 bool isBluetoothEnabledPreference() const;
};
@PREFERENCE_METHODS@
int main(){
 MyMesh mesh;
 assert(mesh.isBluetoothEnabledPreference()); // New devices retain legacy-on behavior.
 mesh._prefs.wifi_enabled=0;mesh._prefs.usb_logging_enabled=1;
 strcpy(mesh._prefs.node_name,"durable node");mesh._prefs.freq=910.525f;
 mesh._prefs.ble_pin=876543;mesh._prefs.bluetooth_stealth_mode=2;
 mesh._prefs.one_key_dm_enabled=1;
 assert(mesh.savePrefs());
 const auto on_disk=mesh.store.fs.files["/new_prefs"];
#if defined(TBEAM_1W)
 assert(on_disk.size()==243);
#elif defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
 assert(on_disk.size()==333);
#else
 assert(on_disk.size()==236);
#endif
 const size_t bluetooth_offset=on_disk.size()-5;
 assert(on_disk[bluetooth_offset]==1&&on_disk.back()==0);
 // Both choices survive a fresh instance reading the actual on-device image.
 for(bool enabled : {false,true,false}){
   assert(mesh.setBluetoothEnabledPreference(enabled));
   DataStore reboot;reboot.fs=mesh.store.fs;
   CompanionNodePrefs prefs;prefs.bluetooth_enabled=!enabled;
   double lat=0,lon=0;assert(reboot.loadPrefs(prefs,lat,lon));
   assert(prefs.bluetooth_enabled==enabled&&lat==47.1&&lon==-122.2);
   assert(prefs.ble_pin==876543&&prefs.freq==910.525f);
   assert(prefs.bluetooth_stealth_mode==2&&prefs.one_key_dm_enabled==1);
   assert(prefs.wifi_enabled==0&&prefs.usb_logging_enabled==1);
   const auto& saved=reboot.fs.files["/new_prefs"];
   assert(saved[bluetooth_offset]==enabled&&saved.back()==0);
 }
 const auto off_disk=mesh.store.fs.files["/new_prefs"];
 // The public setter must roll live state back when the atomic save fails.
 mesh.store.fs.fail_write_after=17;
 assert(!mesh.setBluetoothEnabledPreference(true));
 assert(!mesh.isBluetoothEnabledPreference());
 assert(mesh.store.fs.files["/new_prefs"]==off_disk);
 mesh.store.fs.fail_write_after=-1;
 mesh.store.fs.fail_rename=1;
 assert(!mesh.setBluetoothEnabledPreference(true));
 assert(!mesh.isBluetoothEnabledPreference());
 assert(mesh.store.fs.files["/new_prefs"]==off_disk);
 mesh.store.fs.fail_rename=0;
 assert(mesh.setBluetoothEnabledPreference(true));
 mesh.store.fs.fail_write=true;
 assert(!mesh.setBluetoothEnabledPreference(false));
 assert(mesh.isBluetoothEnabledPreference());
 mesh.store.fs.fail_write=false;
 // Legacy images cannot accidentally inherit an off value from the caller.
 for(unsigned size : {84u,156u,215u,226u,unsigned(bluetooth_offset)}){
   DataStore legacy;legacy.fs.files["/new_prefs"]=on_disk;
   legacy.fs.files["/new_prefs"].resize(size);
   CompanionNodePrefs prefs;prefs.bluetooth_enabled=0;
   double lat=0,lon=0;assert(legacy.loadPrefs(prefs,lat,lon));
   assert(prefs.bluetooth_enabled==1&&prefs.ble_pin==876543);
   assert(legacy.fs.files["/new_prefs"].size()==size); // Read-only migration.
 }
 // Invalid new encoding rejects the transaction rather than partially loading.
 for(uint8_t corrupt : {2,255}){
   DataStore damaged;damaged.fs.files["/new_prefs"]=off_disk;
   damaged.fs.files["/new_prefs"][bluetooth_offset]=corrupt;
   CompanionNodePrefs prefs;prefs.bluetooth_enabled=1;
   strcpy(prefs.node_name,"unchanged");double lat=1,lon=2;
   assert(!damaged.loadPrefsInt("/new_prefs",prefs,lat,lon));
   assert(prefs.bluetooth_enabled==1&&!strcmp(prefs.node_name,"unchanged"));
   assert(lat==1&&lon==2);
 }
}
'''

STARTUP_HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <helpers/MultiSerialInterface.h>
#include <helpers/BluetoothMac.h>
#include "examples/companion_radio/BluetoothName.h"
#define BLE_NAME_PREFIX "MC-"
uint32_t now_ms=100;
uint32_t millis(){return now_ms;}
bool companion_bluetooth_initialized=false;
char companion_bluetooth_start_failure[96]={};
uint32_t companion_bluetooth_start_at=0;
constexpr uint32_t COMPANION_BLUETOOTH_RETRY_MS=5000;
uint8_t companion_bluetooth_session_stealth_mode=mesh::companion::BLUETOOTH_STEALTH_OFF;
struct CompanionNodePrefs {
 char bluetooth_name[64]={},node_name[32]="durable node";
 uint8_t bluetooth_stealth_peer_type=0,bluetooth_stealth_peer[6]={};
};
struct {
 CompanionNodePrefs prefs;
 bool preference=true;
 CompanionNodePrefs* getNodePrefs(){return &prefs;}
 bool isBluetoothEnabledPreference() const {return preference;}
 uint32_t getBLEPin(){return 876543;}
} the_mesh;
namespace mesh {
struct Logger {void println(const char*){}};
Logger& usbLoggingPort(){static Logger logger;return logger;}
}
struct Transport : BaseSerialInterface {
 bool enabled=false,begin_succeeds=true;
 unsigned enables=0,begins=0;
 template<class... T> bool begin(T...){++begins;return begin_succeeds;}
 const char* beginFailure(){return "fixture startup failure";}
 void enable() override{enabled=true;++enables;}
 void disable() override{enabled=false;}
 bool isEnabled() const override{return enabled;}
 bool isConnected() const override{return false;}
 bool isReadBusy() const override{return false;}
 bool isWriteBusy() const override{return false;}
 size_t writeFrame(const uint8_t*,size_t n) override{return n;}
 size_t checkRecvFrame(uint8_t*) override{return 0;}
} bluetooth_interface,usb_serial_interface;
MultiSerialInterface interface_manager;
bool identity_accepts=true;
unsigned identity_preparations=0;
bool prepareCompanionBluetoothIdentity(CompanionNodePrefs*){
 ++identity_preparations;return identity_accepts;
}
const uint8_t* companionBluetoothAddress(const CompanionNodePrefs*,bool&){return nullptr;}
@METHODS@
void fresh(bool preference){
 interface_manager=MultiSerialInterface();bluetooth_interface=Transport();
 usb_serial_interface=Transport();companion_bluetooth_initialized=false;
 companion_bluetooth_start_at=0;identity_accepts=true;identity_preparations=0;
 the_mesh.preference=preference;
 interface_manager.setBluetoothAutoEnable(preference);
 assert(interface_manager.addInterface(InterfaceType::USB,&usb_serial_interface));
}
int main(){
 // Both immediate and deferred stack registration must honor saved-off
 // advertising while preserving an initialized stack for USB recovery.
 for(bool manager_started : {false,true}){
   fresh(false);
   if(manager_started)interface_manager.enable();
   startCompanionBluetooth();
   assert(companion_bluetooth_initialized&&bluetooth_interface.begins==1);
   assert(!bluetooth_interface.enabled&&bluetooth_interface.enables==0);
   if(!manager_started)interface_manager.enable();
   assert(usb_serial_interface.enabled&&!bluetooth_interface.enabled);
   startCompanionBluetooth();serviceDeferredCompanionBluetooth();
   assert(bluetooth_interface.begins==1&&bluetooth_interface.enables==0);
   interface_manager.enableBluetooth();
   assert(bluetooth_interface.enabled&&bluetooth_interface.begins==1);
 }
 // A scheduled identity/start retry may initialize, never enable a saved-off
 // transport when the preference was changed before the retry became due.
 fresh(true);interface_manager.enable();identity_accepts=false;
 startCompanionBluetooth();assert(companion_bluetooth_start_at!=0);
 assert(!companion_bluetooth_initialized&&bluetooth_interface.begins==0);
 the_mesh.preference=false;interface_manager.setBluetoothAutoEnable(false);
 identity_accepts=true;now_ms+=COMPANION_BLUETOOTH_RETRY_MS;
 serviceDeferredCompanionBluetooth();
 assert(companion_bluetooth_initialized&&bluetooth_interface.begins==1);
 assert(bluetooth_interface.enables==0&&!bluetooth_interface.enabled&&usb_serial_interface.enabled);
 // Existing on/default preference still starts normally in either order.
 fresh(true);startCompanionBluetooth();interface_manager.enable();
 assert(bluetooth_interface.enabled&&bluetooth_interface.enables==1);
 fresh(true);interface_manager.enable();startCompanionBluetooth();
 assert(bluetooth_interface.enabled&&bluetooth_interface.enables==1);
}
'''


class BluetoothPersistenceTests(unittest.TestCase):
    def test_actual_startup_and_deferred_retry_do_not_advertise_saved_off(self):
        main = (ROOT/'examples/companion_radio/main.cpp').read_text()
        methods = '\n'.join(method(main, signature) for signature in (
            'static void scheduleCompanionBluetoothRetry(',
            'static void startCompanionBluetooth(',
            'static void serviceDeferredCompanionBluetooth('))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cstddef>\n')
            source = work/'startup.cpp'
            source.write_text(STARTUP_HARNESS.replace('@METHODS@', methods))
            for platform in ('NRF52_PLATFORM', 'ESP32'):
                with self.subTest(platform=platform):
                    binary = work/'startup'
                    built = subprocess.run([os.environ.get('CXX','g++'), '-std=c++17',
                        '-DBLE_PIN_CODE=123456','-D'+platform+'=1',
                        '-I',str(work),'-I',str(ROOT/'src'),'-I',str(ROOT),
                        str(source),'-o',str(binary)],capture_output=True,text=True)
                    self.assertEqual(built.returncode,0,built.stdout+built.stderr)
                    ran = subprocess.run([str(binary)],capture_output=True,text=True)
                    self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)

    def test_boot_policy_is_applied_before_any_interface_can_start(self):
        main = (ROOT/'examples/companion_radio/main.cpp').read_text()
        setup = method(main, '\nvoid setup()')
        policy = setup.index('interface_manager.setBluetoothAutoEnable(')
        self.assertLess(policy, setup.index('startCompanionBluetooth();'))
        self.assertLess(policy, setup.index('the_mesh.startInterface('))

    def test_actual_preference_roundtrips_and_atomic_failure_rollback(self):
        store = (ROOT/'examples/companion_radio/DataStore.cpp').read_text()
        mesh = (ROOT/'examples/companion_radio/MyMesh.cpp').read_text()
        header = (ROOT/'examples/companion_radio/MyMesh.h').read_text()
        methods = esp_recovery_helpers(store) + '\n'.join(method(store, signature) for signature in (
            'bool DataStore::loadPrefs(', 'bool DataStore::loadPrefsInt(',
            'bool DataStore::savePrefs('))
        prefix = STORE_HARNESS.split('int main(')[0].replace('@METHODS@', methods)
        harness = PREFERENCE_HARNESS.replace('@SAVE_PREFERENCE@', method(
            header, 'template <typename T> bool savePreference('))
        harness = harness.replace('@PREFERENCE_METHODS@', '\n'.join(method(mesh, signature)
            for signature in ('bool MyMesh::setBluetoothEnabledPreference(',
                              'bool MyMesh::isBluetoothEnabledPreference(')))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/'Utils.h').write_text('''#pragma once
#include <Arduino.h>
#include <cassert>
namespace mesh { struct Utils {
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
}; }
''')
            (work/'platform_shim.h').write_text('''#include <cstdlib>
#include <cstdio>
inline char* utoa(unsigned int value,char* output,int base){
 if(base!=10)abort();sprintf(output,"%u",value);return output;
}
''')
            transaction = (ROOT/'src/helpers/ContactFileTransaction.h').read_text()
            (work/'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            source = work/'preferences.cpp'
            source.write_text(prefix+harness)
            for flags in ('NRF52_PLATFORM', 'ESP32_PLATFORM', 'RP2040_PLATFORM',
                          'ESP32_PLATFORM,TBEAM_1W', 'RP2040_PLATFORM,ENABLE_WIFI_INTERFACE'):
                with self.subTest(platform=flags):
                    binary = work/'preferences'
                    built = subprocess.run([os.environ.get('CXX','g++'), '-std=c++17',
                        *['-D'+flag+'=1' for flag in flags.split(',')],
                        '-include',str(work/'platform_shim.h'),
                        '-I',str(work),'-I',str(ROOT/'test/fixtures/radio_profiles/mocks'),
                        '-I',str(ROOT/'test/mocks'),'-I',str(ROOT/'src'),
                        '-I',str(ROOT/'src/helpers'),'-I',str(ROOT), str(source),
                        *[str(ROOT/'src/helpers'/name) for name in ('ConfigSerializer.cpp',
                            'DynamicConfigSerializer.cpp','CommonRadioPrefs.cpp','TxtDataHelpers.cpp')],
                        '-o',str(binary)],capture_output=True,text=True)
                    self.assertEqual(built.returncode,0,built.stdout+built.stderr)
                    ran = subprocess.run([str(binary)],capture_output=True,text=True)
                    self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)


if __name__ == '__main__':
    unittest.main()
