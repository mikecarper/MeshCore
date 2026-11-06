#!/usr/bin/env python3
"""Compile the actual lab selector with strict parsing and no radio I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from cpp_source import body

ROOT=Path(__file__).resolve().parents[1]

class PulseCliTests(unittest.TestCase):
 def test_actual_selector_is_lab_only(self):
  method=body((ROOT/'src/helpers/AGCMaintenanceCLI.h').read_text(),'inline bool handleRxpsPulseLabCommand(')
  parser=body((ROOT/'src/helpers/CLICommandUtils.h').read_text(),'inline bool parseUnsignedIntegerStrict(')
  source=r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <limits>
#include <initializer_list>
#include <ctype.h>
#include <errno.h>
#include <limits.h>
namespace mesh {
namespace cli {
@PARSE@
void formatError(char* out,size_t n,const char* why) { snprintf(out,n,"ERR: %s",why); }
}
struct Radio {
 bool supported=true,pending=false;uint32_t sleep=4000;unsigned changes=0;
 bool supportsWarmRxRefresh() const {return supported;}
 uint32_t getAGCPulseSleep() const {return sleep;}
 bool setAGCPulseSleep(uint32_t value) {if(pending)return false;sleep=value;++changes;return true;}
};
@METHOD@
}
int main() {
 mesh::Radio r;char reply[160]={};
#if MESH_RXPS_PULSE_LAB
 assert(mesh::handleRxpsPulseLabCommand(&r,"get agc.test.sleep_us",reply,sizeof(reply)));
 assert(!strcmp(reply,"> 4000") && !r.changes);
 for(uint32_t v:{2616U,3000U,3116U,4000U,6554U}) {
  char command[80];snprintf(command,sizeof(command),"set agc.test.sleep_us %u",unsigned(v));
  assert(mesh::handleRxpsPulseLabCommand(&r,command,reply,sizeof(reply)));
  assert(!strncmp(reply,"OK - ",5) && r.sleep==v);
 }
 const unsigned count=r.changes;
 for(const char* v:{"0","-1","3000x","3000.0","2999","2615","6555","4294967296"}) {
  char command[90];snprintf(command,sizeof(command),"set agc.test.sleep_us %s",v);
  assert(mesh::handleRxpsPulseLabCommand(&r,command,reply,sizeof(reply)));
  assert(!strncmp(reply,"ERR:",4) && r.changes==count);
 }
 r.pending=true;
 assert(mesh::handleRxpsPulseLabCommand(&r,"set agc.test.sleep_us 3000",reply,sizeof(reply)));
 assert(!strncmp(reply,"ERR:",4) && r.changes==count);
 r.supported=false;
 assert(mesh::handleRxpsPulseLabCommand(&r,"get agc.test.sleep_us",reply,sizeof(reply)));
 assert(!strcmp(reply,"ERR: unsupported"));
#else
 assert(!mesh::handleRxpsPulseLabCommand(&r,"get agc.test.sleep_us",reply,sizeof(reply)));
 assert(!mesh::handleRxpsPulseLabCommand(&r,"set agc.test.sleep_us 2616",reply,sizeof(reply)));
 assert(!r.changes && r.sleep==4000);
#endif
 assert(!mesh::handleRxpsPulseLabCommand(&r,"get agc.test.sleep_usx",reply,sizeof(reply)));
}
'''.replace('@METHOD@',method).replace('@PARSE@',parser)
  with tempfile.TemporaryDirectory(prefix='meshcore-pulse-cli-') as directory:
   path=Path(directory);(path/'test.cpp').write_text(source,encoding='ascii')
   for flag in (0,1):
    exe=path/('cli'+str(flag))
    built=subprocess.run(['g++','-std=c++11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie','-DMESH_RXPS_PULSE_LAB='+str(flag),str(path/'test.cpp'),'-o',str(exe)],capture_output=True,text=True)
    self.assertEqual(built.returncode,0,built.stderr)
    ran=subprocess.run([str(exe)],capture_output=True,text=True)
    self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)

if __name__=='__main__':unittest.main()
