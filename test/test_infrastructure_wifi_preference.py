#!/usr/bin/env python3
"""Exercise the infrastructure WiFi master policy against a persistent NVS fake."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFERENCES = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <cstring>
#include <map>
#include <string>
enum PreferenceType { PT_INVALID, PT_U8, PT_STR };
struct Stored { PreferenceType type; uint8_t value; };
struct Preferences {
  inline static std::map<std::string, Stored> values;
  inline static bool available=true, fail_write=false, commit_fail_once=false, bad_verify_once=false, verify_pending=false;
  inline static unsigned writes=0;
  bool begin(const char* name, bool) { assert(!strcmp(name,"mesh-wifi")); return available; }
  bool isKey(const char* key) { return values.count(key); }
  PreferenceType getType(const char* key) { return isKey(key)?values[key].type:PT_INVALID; }
  uint8_t getUChar(const char* key, uint8_t fallback=0) {
    if(verify_pending){verify_pending=false;return fallback;}
    return getType(key)==PT_U8?values[key].value:fallback;
  }
  size_t putUChar(const char* key,uint8_t value) {
    ++writes;if(fail_write)return 0;
    values[key]={PT_U8,value};
    verify_pending=bad_verify_once;bad_verify_once=false;
    if(commit_fail_once){commit_fail_once=false;return 0;}
    return 1;
  }
  bool remove(const char* key) { return values.erase(key); }
  void end() {}
};
'''
HARNESS = r'''
#include <helpers/esp32/InfrastructureWiFiPreference.h>
using namespace mesh::wifi;
void reboot(){infrastructureWiFiPreferenceState()=InfrastructureWiFiPreference{};}
int main(){
  // Only an absent setting has the compatibility default On.
  assert(loadInfrastructureWiFiPreference()&&infrastructureWiFiEnabled());
  Preferences::values["ssid"]={PT_STR,42};
  assert(saveInfrastructureWiFiEnabled(false));
  assert(!infrastructureWiFiEnabled());
  reboot();assert(loadInfrastructureWiFiPreference()&&!infrastructureWiFiEnabled());
  assert(Preferences::values["ssid"].type==PT_STR&&Preferences::values["ssid"].value==42);
  auto writes=Preferences::writes;
  assert(saveInfrastructureWiFiEnabled(false)&&Preferences::writes==writes);
  assert(saveInfrastructureWiFiEnabled(true));
  reboot();assert(loadInfrastructureWiFiPreference()&&infrastructureWiFiEnabled());

  // Corrupt settings and unavailable NVS never silently turn WiFi On.
  Preferences::values["infra_enabled"]={PT_STR,1};
  reboot();assert(!loadInfrastructureWiFiPreference()&&!infrastructureWiFiEnabled());
  Preferences::values["infra_enabled"]={PT_U8,2};
  reboot();assert(!loadInfrastructureWiFiPreference()&&!infrastructureWiFiEnabled());
  assert(saveInfrastructureWiFiEnabled(false)); // Explicit save repairs the image.
  Preferences::available=false;
  reboot();assert(!loadInfrastructureWiFiPreference()&&!infrastructureWiFiEnabled());
  assert(!saveInfrastructureWiFiEnabled(true));
  Preferences::available=true;
  reboot();assert(loadInfrastructureWiFiPreference()&&!infrastructureWiFiEnabled());

  // Failures are not acknowledged, and a write that reports failure after
  // changing NVS is rolled back to the previous saved policy.
  Preferences::fail_write=true;
  assert(!saveInfrastructureWiFiEnabled(true)&&!infrastructureWiFiEnabled());
  Preferences::fail_write=false;
  reboot();assert(loadInfrastructureWiFiPreference()&&!infrastructureWiFiEnabled());
  Preferences::commit_fail_once=true;
  assert(!saveInfrastructureWiFiEnabled(true)&&!infrastructureWiFiEnabled());
  reboot();assert(loadInfrastructureWiFiPreference()&&!infrastructureWiFiEnabled());
  // Inject failure in the candidate verification, not the initial load.
  Preferences::bad_verify_once=true;
  assert(!saveInfrastructureWiFiEnabled(true)&&!infrastructureWiFiEnabled());
  reboot();assert(loadInfrastructureWiFiPreference()&&!infrastructureWiFiEnabled());
  assert(saveInfrastructureWiFiEnabled(true));
  reboot();assert(loadInfrastructureWiFiPreference()&&infrastructureWiFiEnabled());
}
'''

class InfrastructureWiFiPreferenceTests(unittest.TestCase):
    def test_saved_policy_reboot_and_storage_failures(self):
        with tempfile.TemporaryDirectory(prefix='meshcore-infra-wifi-') as directory:
            path = Path(directory)
            (path/'Preferences.h').write_text(PREFERENCES)
            (path/'main.cpp').write_text(HARNESS)
            binary = path/'test'
            result = subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror',
                '-I'+str(path),'-I'+str(ROOT/'src'),str(path/'main.cpp'),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result = subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
