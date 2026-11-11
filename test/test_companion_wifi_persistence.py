#!/usr/bin/env python3
"""Execute the Companion preference store and physical WiFi control path."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'examples/companion_radio/main.cpp'


class CompanionWiFiPersistenceTest(unittest.TestCase):
    def test_saved_policy_ui_alias_and_reboot_modes(self):
        main = MAIN.read_text()
        saving = method(main, 'static bool saveCompanionWiFiPreference(')
        setter = method(main, 'bool setCompanionWiFiEnabled(')
        selector = method(main, 'bool selectCompanionTransportMode(')
        source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <new>
#include <helpers/WirelessControl.h>
#include "examples/companion_radio/CompanionWiFi.h"
using namespace mesh::wireless;
uint32_t now=100;
uint32_t millis(){return now;}
struct CompanionNodePrefs { uint8_t wifi_enabled=1; };
struct {
  CompanionNodePrefs prefs;
  uint8_t disk=1;
  unsigned writes=0;
  bool save_ok=true;
  CompanionNodePrefs* getNodePrefs(){return &prefs;}
  bool savePrefs(){++writes;if(!save_ok)return false;disk=prefs.wifi_enabled;return true;}
} the_mesh;
''' + saving + '\n' + setter + '\n' + r'''
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
''' + selector + r'''
#endif
struct Adapter : Backend {
  bool active=true;
  unsigned transitions=0;
  uint8_t available()const override{return WiFi;}
  uint8_t enabled()const override{return active?WiFi:0;}
  uint8_t clients()const override{return 0;}
  bool hasWiFiPreference()const override{return true;}
  bool wifiPreference()const override{return the_mesh.prefs.wifi_enabled!=0;}
  bool saveWiFiPreference(bool enabled)override{return saveCompanionWiFiPreference(enabled);}
  bool wifiRequiresReboot()const override{
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE) || defined(RP2040_PLATFORM)
    return true;
#else
    return false;
#endif
  }
  Result set(uint8_t service,bool enabled)override{
    assert(service==WiFi);++transitions;active=enabled;return Result::Done;
  }
};
void reboot(Adapter& backend){
  Control& current=control();current.~Control();new(&current) Control;
  the_mesh.prefs.wifi_enabled=the_mesh.disk;
  backend.active=the_mesh.prefs.wifi_enabled!=0;
  control().begin(backend);
}
int main(){
  Adapter backend;control().begin(backend);
  // Repeating an existing saved selection consumes no flash write.
  assert(setCompanionWiFiEnabled(true));
  assert(the_mesh.writes==0 && the_mesh.prefs.wifi_enabled==1);
  now+=250;control().service(now);
#if !defined(BLE_PIN_CODE) && !defined(ENABLE_USB_INTERFACE) \
    && !defined(SERIAL_RX) && !defined(ETHERNET_ENABLED)
  // Even an explicit physical control must not strand a WiFi-only image.
  assert(!setCompanionWiFiEnabled(false));
  assert(the_mesh.writes==0 && the_mesh.disk==1 && backend.active);
#else
  // An NVS/file failure must not leave RAM changed or queue radio teardown.
  the_mesh.save_ok=false;
  assert(!setCompanionWiFiEnabled(false));
  assert(the_mesh.prefs.wifi_enabled==1 && the_mesh.disk==1);
  assert(!control().pending() && backend.active);
  the_mesh.save_ok=true;
  assert(setCompanionWiFiEnabled(false));
  assert(the_mesh.prefs.wifi_enabled==0 && the_mesh.disk==0);
  // The preference is durable before the pending runtime change executes.
  assert(backend.active);
  if(backend.wifiRequiresReboot())assert(!control().blocked(WiFi));
  reboot(backend);
  assert(!backend.active && control().blocked(WiFi));
  assert(!control().allowService(WiFi));
  char reply[160]={};
  assert(control().handle("get wifi.enabled",reply,sizeof(reply),now,Independent));
  assert(strstr(reply,"0"));
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  assert(!selectCompanionTransportMode(static_cast<CompanionTransportMode>(99)));
  const unsigned writes=the_mesh.writes;
  assert(selectCompanionTransportMode(CompanionTransportMode::WiFi));
  assert(the_mesh.writes==writes+1);
#else
  assert(setCompanionWiFiEnabled(true));
#endif
  assert(the_mesh.prefs.wifi_enabled==1 && the_mesh.disk==1);
  if(backend.wifiRequiresReboot()){
    assert(!backend.active && !control().pending());
    reboot(backend);assert(backend.active);
  }else{
    assert(control().pending() && !backend.active);
    now+=250;control().service(now);assert(backend.active);
  }
  assert(control().handle("set wifi.enabled off",reply,sizeof(reply),now,Independent));
  assert(!strncmp(reply,"OK",2) && the_mesh.disk==0);
  reboot(backend);assert(!backend.active);
  assert(control().handle("set wifi 1",reply,sizeof(reply),now,Independent));
  assert(!strncmp(reply,"OK",2) && the_mesh.disk==1);
#endif
}
'''
        with tempfile.TemporaryDirectory(prefix='companion-wifi-prefs-') as tmp:
            cpp = Path(tmp) / 'prefs.cpp'
            cpp.write_text(source)
            for name, defines in (
                ('esp32', ['-DESP32=1', '-DESP_PLATFORM=1', '-DWIFI_SSID="mesh"', '-DENABLE_USB_INTERFACE=1']),
                ('exclusive', ['-DESP32=1', '-DESP_PLATFORM=1', '-DWIFI_SSID="mesh"', '-DBLE_PIN_CODE=123456', '-DCOMPANION_EXCLUSIVE_WIFI_BLE=1']),
                ('wifi-only', ['-DESP32=1', '-DESP_PLATFORM=1', '-DWIFI_SSID="mesh"']),
                ('picow', ['-DRP2040_PLATFORM=1', '-DENABLE_WIFI_INTERFACE=1', '-DENABLE_USB_INTERFACE=1']),
            ):
                with self.subTest(mode=name):
                    binary=Path(tmp)/name
                    subprocess.run([os.environ.get('CXX','g++'),'-std=c++11','-Wall','-Wextra','-Werror',
                                    *defines,'-I'+str(ROOT/'src'),'-I'+str(ROOT),str(cpp),'-o',str(binary)],check=True)
                    subprocess.run([str(binary)],check=True)

    def test_policy_loaded_after_prefs_before_interfaces(self):
        setup=method(MAIN.read_text(),'void setup()')
        policy=setup.index('mesh::wireless::control().begin(companion_wireless);')
        self.assertGreater(policy,setup.rindex('the_mesh.begin('))
        self.assertLess(policy,setup.index('startCompanionWiFi();'))


if __name__=='__main__':unittest.main()
