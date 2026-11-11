"""Compare supported BLE behavior while excluding impossible ARM transports."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
#include <helpers/WirelessControl.h>
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace mesh::wireless;
static_assert(sizeof(Command)==2,"parser result is register sized");
struct Saved {bool bluetooth=true,off=false;uint8_t restore=0;};
struct BLEBackend : Backend {
 Saved& saved;bool active=true,save_ok=true,runtime_ok=true;uint8_t sessions=Independent;unsigned writes=0,calls=0;
 BLEBackend(Saved& s):saved(s){}
 uint8_t available()const override{return Bluetooth;}
 uint8_t enabled()const override{return active?Bluetooth:0;}
 uint8_t clients()const override{return sessions;}
 bool hasBluetoothPreference()const override{return true;}
 bool bluetoothPreference()const override{return saved.bluetooth;}
 bool saveBluetoothPreference(bool on)override{++writes;if(!save_ok)return false;saved.bluetooth=on;return true;}
 bool hasGroupPreference()const override{return true;}
 bool groupPreference(bool& off,uint8_t& mask)const override{off=saved.off;mask=saved.restore;return true;}
 bool saveGroupPreference(bool off,uint8_t mask)override{++writes;if(!save_ok)return false;saved.off=off;saved.restore=mask;return true;}
 Result set(uint8_t bit,bool on)override{assert(bit==Bluetooth);++calls;if(!runtime_ok)return Result::Failed;active=on;return Result::Done;}
};
struct Fixture {
 Saved saved;BLEBackend backend;Control c;uint32_t now=0;char reply[160];
 Fixture():backend(saved){c.begin(backend);}
 void state(){printf("%u,%u,%u,%u,%u,%u,%u,%u\n",c.enabled(),c.masterOff(),c.pending(),c.blocked(Bluetooth),saved.bluetooth,saved.off,backend.calls,backend.writes);}
 void cmd(const char* text,uint8_t who=Independent){assert(c.handle(text,reply,sizeof(reply),now,who));puts(reply);}
 void tick(){now+=250;c.service(now);state();}
};
int main(){
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
 static_assert(BuildMask==Bluetooth,"BLE is the only physical wireless service");
 Control unavailable;assert(!unavailable.hasWiFiPreference()&&unavailable.wifiPreference());
 assert(!unavailable.allowWiFiForOTA()&&!unavailable.allowService(WiFi)&&!unavailable.allowService(EspNow));
#endif
 Fixture f;
 for(const char* text:{"get wifi","get wifi.enabled","get espnow","get espnow.enabled","get 2.4ghz",
     "set wifi on","set wifi.enabled 0","set espnow.enabled on"})f.cmd(text);
 f.cmd("set 2.4ghz off");f.tick();assert(f.saved.off&&!f.backend.active&&f.saved.bluetooth);
 // A fresh boot uses the durable group gate and retains its BLE restore choice.
 f.c=Control();f.c.begin(f.backend);f.state();f.cmd("get 2.4ghz");
 f.cmd("set 2.4ghz on");f.tick();assert(f.backend.active&&!f.saved.off);
 f.cmd("set 2.4ghz off");f.tick();f.saved.bluetooth=false;
 f.cmd("set 2.4ghz on");f.tick();assert(!f.backend.active&&!f.saved.off);
 f.cmd("set 2.4ghz on all");f.tick();assert(f.backend.active&&f.saved.bluetooth);
 // Lost management fallback cancels before teardown and restores durable state.
 f.backend.sessions=Independent;f.cmd("set 2.4ghz off",Bluetooth);
 f.backend.sessions=0;f.tick();assert(!f.saved.off&&f.backend.active);
 f.cmd("get 2.4ghz");f.backend.sessions=Independent;
 f.backend.save_ok=false;f.cmd("set 2.4ghz off");f.state();assert(!f.c.pending()&&!f.saved.off);
 f.backend.save_ok=true;f.cmd("set 2.4ghz off");f.tick();
 f.backend.runtime_ok=false;f.cmd("set 2.4ghz on");f.tick();f.cmd("get 2.4ghz");
 assert(!f.saved.off&&!f.backend.active); // Desired On survives runtime failure.
 f.backend.runtime_ok=true;f.cmd("set 2.4ghz on all");f.tick();
 assert(f.c.suspendForEmergency());f.state();assert(!f.backend.active);
 assert(f.c.resumeAfterEmergency());f.state();assert(f.backend.active);
}
'''
# Control has atomics, so use a fresh object rather than assignment for reboot.
HARNESS=HARNESS.replace(' f.c=Control();f.c.begin(f.backend);f.state();f.cmd("get 2.4ghz");',
                        ' {Control reboot;reboot.begin(f.backend);assert(reboot.masterOff()&&reboot.blocked(Bluetooth));}\n f.state();f.cmd("get 2.4ghz");')
HARNESS=HARNESS.replace('#include <cstring>','#include <cstring>\n#include <initializer_list>')

class ARMCapabilitiesTest(unittest.TestCase):
    def test_ble_only_replay_matches_generic_controller(self):
        with tempfile.TemporaryDirectory(prefix='wireless-arm-ble-') as tmp:
            folder=Path(tmp);(folder/'test.cpp').write_text(HARNESS)
            for compact in (0,1):
                results=[]
                for flags in ([],['-DNRF52_PLATFORM=1','-DBLE_PIN_CODE=123456'],
                              ['-DSTM32_PLATFORM=1','-DBLE_PIN_CODE=123456']):
                    binary=folder/'test'
                    subprocess.run([os.environ.get('CXX','g++'),'-std=c++11','-Os','-Wall','-Wextra','-Werror',
                        f'-DMESH_CLI_COMPACT_REPLIES={compact}','-DMESH_ENABLE_EMERGENCY_POWER=1',*flags,
                        '-I',str(ROOT/'src'),str(folder/'test.cpp'),'-o',str(binary)],check=True)
                    if flags:
                        self.assertNotIn(b'Error: WiFi setting could not be saved',binary.read_bytes())
                        self.assertNotIn(b'ESP-NOW setting could not be saved',binary.read_bytes())
                    results.append(subprocess.check_output([str(binary)]))
                self.assertEqual(results[0],results[1]);self.assertEqual(results[0],results[2])

if __name__=='__main__':unittest.main()
