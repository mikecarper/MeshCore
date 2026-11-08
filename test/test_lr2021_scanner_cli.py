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
  void labScannerHandoffDiag(char* b,size_t n) { snprintf(b,n,"handoff diag"); }
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
  auto allowed=[](const char* s) {
    return mesh::lr2021::isScannerFrameCommandAllowed(reinterpret_cast<const uint8_t*>(s),strlen(s));
  };
  const char* valid[]={"get radio.safe", "set radio.safe on", "set radio.safe off",
    "get radio.multisf", "set radio.multisf on", "set radio.multisf off",
    "set radio.multisf radio", "set radio.multisf radio1", "set radio.multisf radio2",
    "set radio.multisf 5-8", "set radio.multisf 6-9", "set radio.multisf 7-10",
    "get pramscan", "get pramscan.last", "get pramscan.diag", "get pramscan.timing",
    "get pramscan.recovery", "get pramscan.handoff", "set pramscan on", "set pramscan off", "set pramscan recover"};
  for (auto s:valid) assert(allowed(s));
  const char* invalid[]={"", "ota status", "get tx", "set radio.safe", "set radio.safe on extra",
    "set radio.multisf 4-7", "set radio.multisf 5-9", "set radio.multisf 8-7",
    "set radio.multisf 7-11", "set radio.multisf 5-8x", "set radio.multisf x-y",
    "set radio.multisf -5-8", "set radio.multisf +5-8", "set radio.multisf 5-+8",
    "set radio.multisf 99999999999999999999-8", "set radio.multisf 5-88888888888888888888",
    "set pramscan on;reboot", "set radio.safe on\n", "get radio.safe&reboot",
    "get radio.safe|reboot", "get radio.safe`", "get radio.safe$",
    "get radio.safe\\", "get radio.safe\"", "get radio.safe'"};
  for (auto s:invalid) assert(!allowed(s));
  const uint8_t embedded[]={'g','e','t',' ','r','a','d','i','o','.','s','a','f','e',0,'x'};
  assert(!mesh::lr2021::isScannerFrameCommandAllowed(embedded,sizeof(embedded)));
  assert(!mesh::lr2021::isScannerFrameCommandAllowed(nullptr,3));
  char oversized[60]; memset(oversized,'x',sizeof(oversized));
  assert(!mesh::lr2021::isScannerFrameCommandAllowed(reinterpret_cast<uint8_t*>(oversized),sizeof(oversized)));
  auto run=[&](const char* s) { reply[0]=0; return mesh::lr2021::handleScannerCommand(s,reply,sizeof(reply),r); };
  assert(!run("get radio"));
  assert(run("get radio.multisf") && strstr(reply,"on 5-8") && strstr(reply,"single active"));
  assert(run("set radio.multisf 6-9") && !strncmp(reply,"OK",2));
  assert(run("get pramscan") && strstr(reply,"scanner status"));
  assert(run("get pramscan.recovery") && strstr(reply,"recovery diag"));
  assert(run("get pramscan.handoff") && strstr(reply,"handoff diag"));
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
  assert(run("get pramscan.handoff") && strstr(reply,"handoff diag"));
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

FRAME_HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstddef>
#define USE_LR2021 1
#define MESH_W12_PRAM_EXPERIMENT 1
#define MESH_W12_MULTISF_EXPERIMENT 1
#define COMPANION_FEATURE_OTA_CLI 0
#define MAX_FRAME_SIZE 240
#define ERR_CODE_ILLEGAL_ARG 1
#define ERR_CODE_UNSUPPORTED_CMD 2
#define RESP_CODE_OK 0
namespace mesh { namespace lr2021 {
bool safeMode() { return false; }
bool handleSafeModeCommand(const char* c,char* r,size_t n) {
  if (strcmp(c,"get radio.safe")) return false;
  snprintf(r,n,"> off"); return true;
}
bool handleMultiSfSettingsCommand(const char*,char*,size_t) { return false; }
} }
struct Radio {
  bool labScannerUncertain() const { return false; }
  void formatW12MultiSf(char* p,size_t n) { snprintf(p,n,"inactive"); }
  void labScannerStatus(char* p,size_t n) { snprintf(p,n,"scanner"); }
  void labScannerRecoveryDiag(char* p,size_t n) { snprintf(p,n,"recovery"); }
  void labScannerHandoffDiag(char* p,size_t n) { snprintf(p,n,"handoff"); }
  void labScannerLast(char* p,size_t n) { snprintf(p,n,"last"); }
  void labScannerDiag(char* p,size_t n) { snprintf(p,n,"diag"); }
  void labScannerTiming(char* p,size_t n) { snprintf(p,n,"timing"); }
  bool labScannerSetAutomaticManual(bool) { return true; }
  bool labScannerRecover() { return true; }
};
#include "scanner_under_test.h"
enum class CompanionWirelessSource { Framed };
enum class CompanionBluetoothCommandSource { Framed };
bool handleCompanionWirelessCommand(const char*,char*,size_t,CompanionWirelessSource) { return false; }
bool handleCompanionBluetoothCommand(const char*,char*,size_t,CompanionBluetoothCommandSource) { return false; }
Radio radio;
bool handleLocalControlCommand(const char* c,char* r,size_t n) {
  return mesh::lr2021::handleScannerCommand(c,r,n,radio);
}
int error=0, written=0; uint8_t captured[MAX_FRAME_SIZE]{};
void writeErrFrame(int e) { error=e; }
struct Serial { void writeFrame(const uint8_t* p,size_t n) { ++written; memcpy(captured,p,n); } } serial;
Serial* _serial=&serial;
void run(const uint8_t* cmd_frame, size_t len) {
  uint8_t out_frame[MAX_FRAME_SIZE]{};
/* ACTUAL_FRAME_BODY */
}
int main() {
  const uint8_t ok[]={0x4a,'g','e','t',' ','r','a','d','i','o','.','s','a','f','e'};
  run(ok,sizeof(ok)); assert(!error && written==1 && captured[0]==0 && captured[1]==5);
  assert(!memcmp(captured+2,"> off",5));
  const uint8_t ota[]={0x4a,'o','t','a',' ','s','t','a','t','u','s'};
  run(ota,sizeof(ota)); assert(error==ERR_CODE_ILLEGAL_ARG && written==1);
  error=0;
  const uint8_t nul[]={0x4a,'g','e','t',' ','r','a','d','i','o','.','s','a','f','e',0,'x'};
  run(nul,sizeof(nul)); assert(error==ERR_CODE_ILLEGAL_ARG && written==1);
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

    def test_actual_framed_dispatch_without_ota_cli(self):
        source = HEADER.read_text()
        source = '\n'.join(line for line in source.splitlines()
                           if 'include' not in line or 'LR2021SafeMode.h' not in line)
        companion = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        start = companion.index('#if COMPANION_FEATURE_OTA_CLI ||',
                                companion.index('== mesh::companion::CMD_EXEC_LOCAL_OTA_CONTROL'))
        end = companion.index('\n  } else if (cmd_frame[0]', start)
        body = companion[start:end]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            (path / 'scanner_under_test.h').write_text(source)
            (path / 'main.cpp').write_text(FRAME_HARNESS.replace('/* ACTUAL_FRAME_BODY */', body))
            exe = path / 'test'
            subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(path / 'main.cpp'), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
