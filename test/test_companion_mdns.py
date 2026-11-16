"""Run mDNS discovery across deferred WiFi starts, renames and retry rollover."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CompanionMdnsTests(unittest.TestCase):
    def test_network_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/'Arduino.h').write_text('#pragma once\n#include <cstdint>\nextern uint32_t now_ms;\ninline uint32_t millis(){return now_ms;}\n')
            (work/'ESPmDNS.h').write_text(r'''
#pragma once
#include <cassert>
#include <cstring>
struct FakeMdns {
 int starts=0,stops=0,services=0,fields=0;bool works=true;char hostname[64]{};
 bool begin(const char* name){++starts;strcpy(hostname,name);return works;}
 void end(){++stops;}
 void addService(const char* service,const char* transport,unsigned port){
  assert(!strcmp(service,"meshcore")&&!strcmp(transport,"tcp")&&port==5000);++services;
 }
 void addServiceTxt(const char*,const char*,const char*,const char*){++fields;}
};
inline FakeMdns MDNS;
''')
            (work/'test.cpp').write_text(r'''
#include <helpers/esp32/CompanionMdns.h>
#include <cassert>
uint32_t now_ms=UINT32_MAX-3000;
int main(){
 CompanionMdns discovery;uint8_t key[32]={1,2,3};
 auto poll=[&](bool up,const char*name="My node"){discovery.loop(up,name,key,"test",2,5000);};
 poll(false);assert(MDNS.starts==0);
 MDNS.works=false;poll(true);assert(MDNS.starts==1);
 now_ms+=4999;poll(true);assert(MDNS.starts==1);
 now_ms+=1;MDNS.works=true;poll(true);assert(MDNS.starts==2&&MDNS.services==1&&MDNS.fields==3);
 assert(!strcmp(MDNS.hostname,"meshcore-my-node"));
 poll(true);assert(MDNS.starts==2);
 poll(true,"Renamed");assert(MDNS.starts==3&&MDNS.stops==1);
 assert(!strcmp(MDNS.hostname,"meshcore-renamed"));
 poll(false);poll(false);assert(MDNS.stops==2);
 poll(true);assert(MDNS.starts==4);
}
''')
            compiled = subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',
                '-DESP32_PLATFORM=1','-DENABLE_WIFI_INTERFACE=1','-DENABLE_ESP_MDNS=1',
                '-I'+str(work),'-I'+str(ROOT/'src'),str(work/'test.cpp'),'-o',str(work/'test')],
                capture_output=True,text=True)
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            result=subprocess.run([str(work/'test')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__ == '__main__':
    unittest.main()
