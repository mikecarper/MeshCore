"""Exercise actual shared scanner command dispatch with a fake radio."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / 'src/helpers/radiolib/LR2021ScannerCLI.h'

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstddef>
#define USE_LR2021 1
#define MESH_W12_PRAM_EXPERIMENT 1
#define MESH_W12_MULTISF_EXPERIMENT 1
namespace mesh { namespace lr2021 {
bool safe = false;
bool safeMode() { return safe; }
bool handleSafeModeCommand(const char* cmd, char* out, size_t cap) {
  if (!strcmp(cmd,"get radio.safe")) { snprintf(out,cap,"> %s",safe?"on":"off"); return true; }
  if (!strcmp(cmd,"set radio.safe on")) { safe=true; snprintf(out,cap,"OK"); return true; }
  if (!strcmp(cmd,"set radio.safe off")) { safe=false; snprintf(out,cap,"OK"); return true; }
  return false;
}
bool handleMultiSfSettingsCommand(const char* cmd, char* out, size_t cap) {
  if (!strcmp(cmd,"get radio.multisf")) { snprintf(out,cap,"> on 5-8 radio1"); return true; }
  if (!strcmp(cmd,"set radio.multisf 6-9")) { snprintf(out,cap,"OK"); return true; }
  return false;
}
} }
struct Radio {
  bool uncertain=false, start_result=true, stop_result=true, recover_result=true;
  int starts=0, stops=0, recovers=0;
  bool labScannerUncertain() const { return uncertain; }
  void formatW12MultiSf(char* b,size_t n) { snprintf(b,n,"single active"); }
  void labScannerStatus(char* b,size_t n) { snprintf(b,n,"scanner status"); }
  void labScannerRecoveryDiag(char* b,size_t n) { snprintf(b,n,"recovery diag"); }
  void labScannerLast(char* b,size_t n) { snprintf(b,n,"last"); }
  void labScannerDiag(char* b,size_t n) { snprintf(b,n,"diag"); }
  void labScannerTiming(char* b,size_t n) { snprintf(b,n,"timing"); }
  bool labScannerSetAutomaticManual(bool enabled) {
    if (enabled) { ++starts; return start_result; }
    ++stops; return stop_result;
  }
  bool labScannerRecover() { ++recovers; return recover_result; }
};
#include "scanner_under_test.h"
int main() {
  Radio r; char reply[160]{};
  auto run=[&](const char* s) { reply[0]=0; return mesh::lr2021::handleScannerCommand(s,reply,sizeof(reply),r); };
  assert(!run("get radio"));
  assert(run("get radio.multisf") && strstr(reply,"on 5-8") && strstr(reply,"single active"));
  assert(run("set radio.multisf 6-9") && !strncmp(reply,"OK",2));
  assert(run("get pramscan") && strstr(reply,"scanner status"));
  assert(run("get pramscan.recovery") && strstr(reply,"recovery diag"));
  assert(run("get pramscan.last") && strstr(reply,"last"));
  assert(run("get pramscan.diag") && strstr(reply,"diag"));
  assert(run("get pramscan.timing") && strstr(reply,"timing"));
  assert(run("set pramscan on") && r.starts==1);
  assert(run("set pramscan off") && r.stops==1);
  assert(run("set radio.safe on") && mesh::lr2021::safe);
  assert(run("set pramscan on") && r.starts==1 && (strstr(reply,"ERR") || strstr(reply,"Error")));
  assert(run("set radio.safe off") && !mesh::lr2021::safe);
  r.uncertain=true;
  assert(run("get pramscan") && strstr(reply,"scanner status"));
  assert(run("get radio.safe") && strstr(reply,"off"));
  assert(run("set radio.safe on") && mesh::lr2021::safe);
  assert(run("set pramscan recover") && r.recovers==1);
  assert(run("set radio.safe off") && mesh::lr2021::safe && (strstr(reply,"ERR") || strstr(reply,"Error")));
  assert(run("set radio.multisf 6-9") && (strstr(reply,"ERR") || strstr(reply,"Error")));
  assert(!run("get tx"));
  r.uncertain=false; mesh::lr2021::safe=false;
  r.start_result=false; assert(run("set pramscan on") && (strstr(reply,"ERR") || strstr(reply,"Error")));
  r.stop_result=false; assert(run("set pramscan off") && (strstr(reply,"ERR") || strstr(reply,"Error")));
  r.recover_result=false; assert(run("set pramscan recover") && (strstr(reply,"ERR") || strstr(reply,"Error")));
  char small[12]; memset(small,'x',sizeof(small));
  assert(mesh::lr2021::handleScannerCommand("get radio.multisf",small,sizeof(small),r));
  assert(small[sizeof(small)-1]=='\0');
}
'''


class ScannerCommandTests(unittest.TestCase):
    def test_real_dispatch(self):
        source = HEADER.read_text()
        # The dispatcher only needs declarations for storage-backed settings;
        # its actual selection and driver calls remain unchanged.
        source = '\n'.join(line for line in source.splitlines()
                           if 'include' not in line or 'LR2021SafeMode.h' not in line)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            (path / 'scanner_under_test.h').write_text(source)
            (path / 'main.cpp').write_text(HARNESS)
            exe = path / 'test'
            subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(path / 'main.cpp'), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
