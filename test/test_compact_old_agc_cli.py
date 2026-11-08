#!/usr/bin/env python3
"""Exercise compact old-timer CLI without removing controls or weakening parsing."""
from pathlib import Path
import os,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class CompactOldCLI(unittest.TestCase):
 def test_full_range_auto_off_strict_inputs_and_compact_prose(self):
  radio='''#pragma once
#include <cstdint>
#include <cstddef>
#include <helpers/OldAGCPolicy.h>
namespace mesh { struct Radio {
 uint32_t interval=AGC_OLD_AUTO; unsigned writes=0;
 bool supportsOldAGC()const{return true;}
 uint32_t getOldAGCInterval()const{return interval;}
 bool setOldAGCInterval(uint32_t n){++writes;interval=n;return true;}
}; }
'''
  source=r'''
#include <helpers/AGCMaintenanceCLI.h>
#include <cassert>
#include <cstring>
#include <initializer_list>
int main(){
 mesh::Radio radio;char reply[160];
 auto command=[&](const char*s){return mesh::handleAGCMaintenanceCommand(&radio,s,16,reply,sizeof(reply));};
 assert(command("get agc.old")&&!strcmp(reply,"> auto"));
 for(const char* value:{"set agc.old auto","set agc.old off","set agc.old 0","set agc.old 16","set agc.old 120","set agc.old 86400"}) {
  assert(command(value));assert(!strncmp(reply,"OK - ",5));
  assert(!strstr(reply,"saved"));
#if MESH_CLI_COMPACT_REPLIES
  assert(strlen(reply)<45);

#endif
 }
 assert(radio.interval==86400);const unsigned writes=radio.writes;
 for(const char* value:{"set agc.old ","set agc.old -1","set agc.old 86401","set agc.old 4294967296","set agc.old 1x","set agc.old 1 2"}) {
  assert(command(value));assert(!strncmp(reply,"Error:",6));assert(radio.writes==writes&&radio.interval==86400);
 }
 for(const char* value:{"get agc.status","get agc.timing","get agc.old.errors","get agc.reset.timing"}) {
  assert(command(value)&&!strcmp(reply,"Error: unsupported"));
 }
 for(const char* value:{"get agc.errors","get agc.old.recovery","get agc.old.timing extra","get agc.statusx","set agc.oldx 1"}) assert(!command(value));
 mesh::Radio fresh;assert(fresh.interval==mesh::AGC_OLD_AUTO);
}
'''
  with tempfile.TemporaryDirectory(prefix='meshcore-compact-old-')as t:
   p=Path(t);(p/'Dispatcher.h').write_text(radio);(p/'main.cpp').write_text(source)
   for compact in (0,1):
    exe=p/str(compact);r=subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-DSTM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC=1','-DMESH_ENABLE_OLD_AGC=1','-DMESH_CLI_COMPACT_REPLIES='+str(compact),'-I'+t,'-I'+str(ROOT/'src'),str(p/'main.cpp'),'-o',str(exe)],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    r=subprocess.run([str(exe)],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
if __name__=='__main__':unittest.main()
