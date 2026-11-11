"""Exercise PIN persistence, immediate application, and device-info queries."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest

from test_companion_preferences_transaction import HARNESS as STORE_HARNESS
from test_companion_preferences_transaction import esp_recovery_helpers
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])

HARNESS = r'''
#include <helpers/TxtDataHelpers.h>
#include <helpers/BleCommonPinWarning.h>
@CONSTANTS@
#define MAX_CONTACTS 350
#define MAX_GROUP_CHANNELS 40
#define FIRMWARE_BUILD_DATE "27 Oct 2026"
#define FIRMWARE_VERSION "pin-regression"
struct { const char* getManufacturerName() const { return "Companion"; } } board;
using Bytes=std::vector<uint8_t>;
uint32_t random_pin=345678;
uint32_t applied_pin=0;
unsigned apply_calls=0;
unsigned fail_apply_call=0;
bool reject_apply=false;
bool companionApplyBluetoothPin(uint32_t pin) {
 ++apply_calls;
 if(reject_apply||apply_calls==fail_apply_call)return false;
 applied_pin=pin;return true;
}
struct StdRNG {
 uint32_t nextInt(uint32_t low,uint32_t high) {
   assert(low==100000&&high==999999);return random_pin;
 }
};
struct BaseSerialInterface {};
struct Capture : BaseSerialInterface {
 std::vector<Bytes> frames;
 BaseSerialInterface* captureReplyRoute() {return this;}
 size_t writeFrame(const uint8_t* data,size_t size) {
   frames.emplace_back(data,data+size);return size;
 }
 size_t writeFrameToRoute(BaseSerialInterface*,const uint8_t* data,size_t size) {
   return writeFrame(data,size);
 }
};
struct FakeMillis { uint32_t now=0; uint32_t getMillis() const {return now;} };
struct MyMesh {
 CompanionNodePrefs _prefs;
 DataStore* _store;
 Capture wire;
 Capture* _serial=&wire;
 uint8_t app_target_ver=0,cmd_frame[180]={},out_frame[180]={};
 uint32_t _active_ble_pin=0;
 FakeMillis millis; FakeMillis* _ms=&millis;
 bool _ble_random_default=false,_using_common_ble_pin=false;
 bool _ble_pin_apply_failed=false;
 bool _ble_pin_restore_pending=false;
 uint32_t _ble_pin_restore=0,_ble_pin_restore_last_retry=0;
 bool _common_ble_pin_warning_pending=false,_common_ble_pin_warning_in_flight=false;
 BaseSerialInterface* _common_ble_pin_warning_route=nullptr;
 unsigned saves=0;
 unsigned warning_schedules=0;
 void scheduleCommonBlePinWarning(BaseSerialInterface* route) {
   assert(route==_serial);if(_using_common_ble_pin)++warning_schedules;
 }
 explicit MyMesh(DataStore& store):_store(&store) {}
 bool savePrefs() {++saves;return _store->savePrefs(_prefs,47.1,-122.2);}
 @SAVE_PREFERENCE@
 uint32_t getBLEPin();
 bool applyAndSaveBLEPin(uint32_t pin);
 void servicePendingBLEPinRestore();
 void writeOKFrame(BaseSerialInterface* route=nullptr);
 void writeErrFrame(uint8_t,BaseSerialInterface* route=nullptr);
 void boot(bool has_display) {
   double lat=0,lon=0;assert(_store->loadPrefs(_prefs,lat,lon));
   assert(lat==47.1&&lon==-122.2);
   _ble_random_default=has_display && BLE_PIN_CODE==123456;
   @BOOT_PIN@
 }
 Bytes command(const Bytes& bytes) {
   assert(bytes.size()<=sizeof(cmd_frame));
   wire.frames.clear();memcpy(cmd_frame,bytes.data(),bytes.size());
   const size_t len=bytes.size();
   @QUERY@ else @SET_PIN@
   assert(wire.frames.size()==1);return wire.frames[0];
 }
};
@METHODS@
static Bytes setPin(uint32_t pin) {
 return {CMD_SET_DEVICE_PIN,uint8_t(pin),uint8_t(pin>>8),uint8_t(pin>>16),uint8_t(pin>>24)};
}
static Bytes query(MyMesh& mesh,uint32_t expected) {
 const auto frame=mesh.command({CMD_DEVICE_QUERY,3});
 assert(frame.size()==82&&frame[0]==RESP_CODE_DEVICE_INFO&&frame[1]==FIRMWARE_VER_CODE);
 assert(frame[2]==MAX_CONTACTS/2&&frame[3]==MAX_GROUP_CHANNELS);
 const uint32_t saved=uint32_t(frame[4])|(uint32_t(frame[5])<<8)
     |(uint32_t(frame[6])<<16)|(uint32_t(frame[7])<<24);
 assert(saved==expected&&mesh.app_target_ver==3);
 assert(!strcmp(reinterpret_cast<const char*>(frame.data()+8),FIRMWARE_BUILD_DATE));
 assert(!strcmp(reinterpret_cast<const char*>(frame.data()+20),"Companion"));
 assert(!strcmp(reinterpret_cast<const char*>(frame.data()+60),FIRMWARE_VERSION));
 assert(frame[80]==0&&frame[81]==mesh._prefs.path_hash_mode);
 return frame;
}
static void seed(DataStore& store,uint32_t pin) {
 CompanionNodePrefs prefs;prefs.ble_pin=pin;strcpy(prefs.node_name,"persistent");
 prefs.freq=910.525f;prefs.bw=62.5f;prefs.sf=7;prefs.cr=5;
 assert(store.savePrefs(prefs,47.1,-122.2));
}
int main() {
 // A random/default preference is the wire sentinel, never this boot's PIN.
 for(uint32_t pin:{0u,100000u,654321u,999999u}) {
   DataStore store;seed(store,0);random_pin=345678;apply_calls=0;fail_apply_call=0;reject_apply=false;
   MyMesh live(store);live.boot(true);assert(live.getBLEPin()==345678);
   applied_pin=live.getBLEPin();
   const auto before=query(live,0);
   assert(live.command(setPin(pin))==Bytes{RESP_CODE_OK});
   assert(live.saves==1&&live._prefs.ble_pin==pin);
   assert(live.getBLEPin()==(pin?pin:345678));
   assert(applied_pin==live.getBLEPin());
   const auto after=query(live,pin);
   for(size_t i=0;i<before.size();++i)if(i<4||i>=8)assert(before[i]==after[i]);
   random_pin=709811;
   MyMesh reboot(store);reboot.boot(true);
   assert(reboot._prefs.ble_pin==pin&&reboot.getBLEPin()==(pin?pin:709811));
   assert(!strcmp(reboot._prefs.node_name,"persistent"));query(reboot,pin);
 }
 // Invalid values never attempt storage or change either kind of PIN.
 for(uint32_t pin:{1u,99999u,1000000u,UINT32_MAX}) {
   DataStore store;seed(store,654321);MyMesh live(store);live.boot(true);
   const auto disk=store.fs.files["/new_prefs"];
   assert(live.command(setPin(pin))==(Bytes{RESP_CODE_ERR,ERR_CODE_ILLEGAL_ARG}));
   assert(live.saves==0&&live._prefs.ble_pin==654321&&live.getBLEPin()==654321);
   assert(store.fs.files["/new_prefs"]==disk);query(live,654321);
 }
 // Apply first; if saving fails, restore the old live pairing PIN and leave
 // the old durable preference intact, even across unrelated later saves.
 for(unsigned fault:{0u,1u,2u
#if defined(ESP32_PLATFORM)
     ,3u
#endif
     }) {
   DataStore store;seed(store,654321);MyMesh live(store);live.boot(true);
   apply_calls=0;reject_apply=false;applied_pin=live.getBLEPin();
   const auto disk=store.fs.files["/new_prefs"];
   if(fault==0)store.fs.fail_write=true;
   if(fault==1)store.fs.fail_write_after=17;
   if(fault==2)store.fs.fail_rename=1;
   if(fault==3)store.fs.fail_rename=2;
   assert(live.command(setPin(876543))==(Bytes{RESP_CODE_ERR,ERR_CODE_FILE_IO_ERROR}));
   assert(live.saves==1&&live._prefs.ble_pin==654321&&live.getBLEPin()==654321);
   assert(apply_calls==2&&applied_pin==654321);
   assert(store.fs.files["/new_prefs"]==disk);query(live,654321);
   store.fs.fail_write=false;store.fs.fail_write_after=-1;store.fs.fail_rename=0;
   strcpy(live._prefs.node_name,"later save");assert(live.savePrefs());
   MyMesh reboot(store);reboot.boot(true);
   assert(reboot.getBLEPin()==654321&&!strcmp(reboot._prefs.node_name,"later save"));
   query(reboot,654321); // An unrelated successful save cannot resurrect the failed PIN.
 }
 // Turning a custom PIN off selects the current display-mode random PIN now.
 DataStore store;seed(store,654321);MyMesh live(store);live.boot(true);
 random_pin=709811;applied_pin=live.getBLEPin();reject_apply=false;
 assert(live.command(setPin(0))==Bytes{RESP_CODE_OK});
 query(live,0);assert(live.getBLEPin()==709811&&applied_pin==709811);
 MyMesh headless(store);headless.boot(false);
 assert(headless.getBLEPin()==BLE_PIN_CODE);query(headless,0);
 // Failure to apply the new pairing PIN must not be reported as success or
 // leave a different saved PIN that would become active after reboot.
 DataStore reject_store;seed(reject_store,654321);MyMesh rejected(reject_store);
 rejected.boot(true);applied_pin=654321;apply_calls=0;reject_apply=true;
 const auto reply=rejected.command(setPin(876543));
 assert((reply==Bytes{RESP_CODE_ERR,ERR_CODE_BAD_STATE}));
 assert(apply_calls==1&&rejected.saves==0&&applied_pin==654321&&rejected.getBLEPin()==654321);
 assert(rejected._prefs.ble_pin==654321);query(rejected,654321);
 reject_apply=false;MyMesh restarted(reject_store);restarted.boot(true);
 assert(restarted.getBLEPin()==654321);query(restarted,654321);
 // If the old PIN cannot immediately be restored after a disk fault, report
 // failure and keep retrying the restore without persisting the new PIN.
 DataStore retry_store;seed(retry_store,654321);MyMesh retry(retry_store);
 retry.boot(true);applied_pin=654321;apply_calls=0;fail_apply_call=2;
 retry_store.fs.fail_write=true;
 const auto retry_reply=retry.command(setPin(876543));
 assert(retry_reply.size()==2&&retry_reply[0]==RESP_CODE_ERR);
 assert(retry._prefs.ble_pin==654321&&retry._ble_pin_restore_pending);
 assert(retry.getBLEPin()==876543&&applied_pin==876543);
 query(retry,654321);
 retry.millis.now=1000;retry.servicePendingBLEPinRestore();
 assert(!retry._ble_pin_restore_pending&&retry.getBLEPin()==654321&&applied_pin==654321);
 retry_store.fs.fail_write=false;fail_apply_call=0;
 MyMesh retry_restart(retry_store);retry_restart.boot(true);
 assert(retry_restart.getBLEPin()==654321);query(retry_restart,654321);
 // Warning state follows the active pairing PIN, not stale boot settings.
 DataStore warning_store;seed(warning_store,654321);MyMesh warning(warning_store);
 warning.boot(false);assert(!warning._using_common_ble_pin);
 assert(warning.command(setPin(111111))==Bytes{RESP_CODE_OK});
 assert(warning._using_common_ble_pin&&warning.getBLEPin()==111111&&warning.warning_schedules==1);
 warning._common_ble_pin_warning_pending=true;
 warning._common_ble_pin_warning_in_flight=true;
 warning._common_ble_pin_warning_route=&warning.wire;
 assert(warning.command(setPin(876543))==Bytes{RESP_CODE_OK});
 assert(!warning._using_common_ble_pin&&!warning._common_ble_pin_warning_pending);
 assert(!warning._common_ble_pin_warning_in_flight&&!warning._common_ble_pin_warning_route);
 assert(warning.warning_schedules==1);
 std::puts("PASS: saved PIN queries, atomic faults, and immediate pairing PIN changes");
}
'''


def production_harness():
    mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
    header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
    store = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
    methods = esp_recovery_helpers(store) + "\n".join(method(store, signature) for signature in (
        "bool DataStore::loadPrefs(", "bool DataStore::loadPrefsInt(", "bool DataStore::savePrefs("))
    prefix = STORE_HARNESS.split("int main(")[0].replace("@METHODS@", methods)
    pin_begin = mesh.index("#ifdef BLE_PIN_CODE // 123456 by default")
    pin_end = mesh.index("\n  resetContacts();", pin_begin)
    names = ("CMD_DEVICE_QUERY", "CMD_SET_DEVICE_PIN", "RESP_CODE_DEVICE_INFO",
             "RESP_CODE_OK", "RESP_CODE_ERR", "ERR_CODE_FILE_IO_ERROR", "ERR_CODE_ILLEGAL_ARG",
             "ERR_CODE_BAD_STATE")
    constants = []
    for name, source in [(name, mesh) for name in names] + [("FIRMWARE_VER_CODE", header)]:
        constants.append(re.search(r"^#define\s+" + name + r"\s+\d+", source, re.M).group())
    harness = HARNESS.replace("@CONSTANTS@", "\n".join(constants))
    harness = harness.replace("@SAVE_PREFERENCE@", method(header, "template <typename T> bool savePreference("))
    harness = harness.replace("@BOOT_PIN@", mesh[pin_begin:pin_end])
    harness = harness.replace("@QUERY@", method(mesh, "if (cmd_frame[0] == CMD_DEVICE_QUERY && len >= 2)"))
    harness = harness.replace("@SET_PIN@", method(mesh, "if (cmd_frame[0] == CMD_SET_DEVICE_PIN && len >= 5)"))
    harness = harness.replace("@METHODS@", "\n".join(method(mesh, signature) for signature in (
        "uint32_t MyMesh::getBLEPin(", "bool MyMesh::applyAndSaveBLEPin(",
        "void MyMesh::servicePendingBLEPinRestore(",
        "void MyMesh::writeOKFrame(", "void MyMesh::writeErrFrame(")))
    return prefix + harness


class BluetoothPinTests(unittest.TestCase):
    def build_and_run(self, platform, legacy_query=False):
        harness = production_harness()
        if legacy_query:
            self.assertEqual(harness.count("&_prefs.ble_pin, 4"), 1)
            harness = harness.replace("&_prefs.ble_pin, 4", "&_active_ble_pin, 4", 1)
        with tempfile.TemporaryDirectory(prefix="meshcore-bluetooth-pin-") as directory:
            work = Path(directory)
            (work / "Utils.h").write_text('''#pragma once
#include <Arduino.h>
#include <cassert>
namespace mesh { struct Utils {
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
}; }
''')
            (work / "platform_shim.h").write_text('''#include <cstdlib>
#include <cstdio>
inline char* utoa(unsigned int value,char* output,int base){
 if(base!=10)abort();sprintf(output,"%u",value);return output;
}
''')
            transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text()
            (work / "ContactFileTransaction.h").write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            source = work / "pin.cpp"
            source.write_text(harness)
            binary = work / "pin.exe"
            built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17",
                *SANITIZERS, "-D" + platform + "=1", "-DBLE_PIN_CODE=123456",
                "-DDISPLAY_CLASS=FixtureDisplay", "-include", str(work / "platform_shim.h"),
                "-I", str(work), "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                "-I", str(ROOT / "src/helpers"), "-I", str(ROOT), str(source),
                *[str(ROOT / "src/helpers" / name) for name in (
                    "ConfigSerializer.cpp", "DynamicConfigSerializer.cpp", "CommonRadioPrefs.cpp",
                    "radiolib/RXPowerSaving.cpp", "radiolib/RXPowerSavingCLI.cpp", "TxtDataHelpers.cpp")],
                "-o", str(binary)], capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            return subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)

    def test_actual_commands_persistence_and_immediate_pairing(self):
        for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM"):
            with self.subTest(platform=platform):
                ran = self.build_and_run(platform)
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                self.assertIn("PASS: saved PIN queries", ran.stdout)

    def test_legacy_active_pin_query_fails_the_regression(self):
        ran = self.build_and_run("ESP32_PLATFORM", legacy_query=True)
        self.assertNotEqual(ran.returncode, 0, "The old active-PIN query unexpectedly passed")
        self.assertIn("saved==expected", ran.stderr)


if __name__ == "__main__":
    unittest.main()
