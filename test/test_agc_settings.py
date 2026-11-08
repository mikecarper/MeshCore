"""Run the production saved AGC policy through real checked file transactions."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RADIO = r"""#pragma once
#include <cstdint>
#include <cstddef>
#include <helpers/OldAGCPolicy.h>
namespace mesh { struct Radio {
  bool supported=true, accept=true;
  uint32_t setting=AGC_OLD_AUTO;
  bool supportsOldAGC() const { return supported; }
  bool formatWarmRxRefresh(uint8_t,uint32_t,char*,size_t) const { return false; }
  uint32_t getOldAGCInterval() const { return setting; }
  bool setOldAGCInterval(uint32_t value) {
    if(!supported || !accept || !validOldAGCInterval(value)) return false;
    setting=value;return true;
  }
}; }
"""
HARNESS = r"""
#include <helpers/AGCSettings.h>
#include <cassert>
#include <cstring>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#endif
#include "store_under_test.cpp"
int main() {
  using namespace mesh;
#if !MESH_ENABLE_OLD_AGC
  MemoryFS fs; Radio radio; radio.supported=false; char reply[160];
  assert(beginAGCSettings(&radio,&fs) && fs.files.empty());
  for(const char* command:{"set agc.old 4","set agc.old auto","get agc.old"}) {
    assert(!handleStoredOldAGCCommand(&radio,command,reply,sizeof(reply)));
    assert(handleAGCMaintenanceCommand(&radio,command,0,reply,sizeof(reply)));
    assert(!strcmp(reply,"Error: unsupported"));
  }
  assert(!setStoredOldAGCInterval(&radio,"4",reply,sizeof(reply)));
  assert(!strcmp(reply,"Error: unsupported") && fs.files.empty());
#else
  MemoryFS fs;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  fs.rename_replaces=true;
#endif
  Radio radio; char reply[160];
  assert(beginAGCSettings(&radio,&fs));
  assert(radio.setting==AGC_OLD_AUTO && fs.files.empty());
  auto run=[&](const char* command) {
    return handleStoredOldAGCCommand(&radio,command,reply,sizeof(reply));
  };
  for(const char* value:{"auto","off","0","4","86400"}) {
    assert(setStoredOldAGCInterval(&radio,value,reply,sizeof(reply)));
    assert(strstr(reply,"(saved)"));
    assert(fs.files.at("/agc_prefs").size()==12);
    Radio rebooted;assert(beginAGCSettings(&rebooted,&fs));
    assert(rebooted.setting==radio.setting);
  }
  const auto good=fs.files.at("/agc_prefs");
  for(const char* value:{"","-1","86401","4294967295","4294967296","auto x","4x","1 2"}) {
    assert(!setStoredOldAGCInterval(&radio,value,reply,sizeof(reply)));
    assert(!strncmp(reply,"Error:",6));
    assert(radio.setting==86400 && fs.files.at("/agc_prefs")==good);
  }
  fs.fail_write=true;
  assert(run("set agc.old 4") && strstr(reply,"save unconfirmed"));
  assert(radio.setting==86400 && fs.files.at("/agc_prefs")==good);
  fs.fail_write=false;
  fs.fail_after_publication=true;
  assert(run("set agc.old 4") && strstr(reply,"save unconfirmed"));
  assert(radio.setting==86400 && fs.files.at("/agc_prefs")!=good);
  fs.fail_after_publication=false;fs.fail_read_open=false;
  Radio published;assert(beginAGCSettings(&published,&fs) && published.setting==4);
  // Corruption must not turn a saved 'off' into automatic calibration.
  assert(run("set agc.old off"));const auto off=fs.files.at("/agc_prefs");
  for(size_t bit=0;bit<off.size()*8;++bit) {
    fs.files["/agc_prefs"]=off;
    fs.files["/agc_prefs"][bit/8]^=uint8_t(1U<<(bit%8));
    Radio fresh;assert(!beginAGCSettings(&fresh,&fs) && fresh.setting==0);
  }
  fs.files["/agc_prefs"]=off;fs.files["/agc_prefs"].pop_back();
  Radio truncated;assert(!beginAGCSettings(&truncated,&fs) && truncated.setting==0);
  // A valid checksum cannot legitimize an unsupported interval.
  uint8_t invalid[12];memcpy(invalid,off.data(),12);
  storage::writeLE32(invalid+4,86401);
  storage::writeLE32(invalid+8,storage::updateCRC32(0xffffffffUL,invalid,8));
  fs.files["/agc_prefs"]={invalid,invalid+12};
  Radio invalid_value;assert(!beginAGCSettings(&invalid_value,&fs) && invalid_value.setting==0);
  fs.files["/agc_prefs"]=off;fs.fail_read_open=true;
  Radio unreadable;assert(!beginAGCSettings(&unreadable,&fs) && unreadable.setting==0);
  fs.fail_read_open=false;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  fs.files["/agc_prefs.bak"]=good;fs.files.erase("/agc_prefs");
  Radio recovered;assert(beginAGCSettings(&recovered,&fs) && recovered.setting==86400);
#endif
  assert(!beginAGCSettings(&radio,nullptr) && radio.setting==0);
  assert(run("set agc.old 4") && strstr(reply,"save unconfirmed") && radio.setting==0);
  assert(beginAGCSettings(&radio,&fs));
  radio.supported=false;
  assert(run("set agc.old 4") && strstr(reply,"unsupported"));
  assert(!run("set agc.oldx 4") && !run("get agc.old"));
  assert(!handleStoredOldAGCCommand(&radio,nullptr,reply,sizeof(reply)));
  radio.supported=true;
  char small[4]={'X','X','X','Z'};
  assert(handleStoredOldAGCCommand(&radio,"set agc.old off",small,3));
  assert(small[2]==0 && small[3]=='Z');
#endif
}
"""

class AGCSettingsTests(unittest.TestCase):
    def test_saved_policy_reboot_failures_and_corruption(self):
        with tempfile.TemporaryDirectory(prefix="agc-settings-") as temp:
            work=Path(temp);(work/"helpers").mkdir()
            (work/"Dispatcher.h").write_text(RADIO)
            mock=(ROOT/"test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text()
            mock=mock.replace("  bool fail_read_open = false;", "  bool fail_read_open = false;\n  bool fail_after_publication = false;")
            mock=mock.replace("    files[to] = files[from]; files.erase(from); return true;",
                '    files[to] = files[from]; files.erase(from);\n    if(fail_after_publication && !strcmp(to,"/agc_prefs")) fail_read_open=true;\n    return true;')
            (work/"helpers/IdentityStore.h").write_text(mock)
            transaction=(ROOT/"src/helpers/ContactFileTransaction.h").read_text()
            for header in ("IdentityStore.h","PersistentStoreFormat.h"):
                transaction=transaction.replace(f'#include "{header}"',f'#include <helpers/{header}>')
            (work/"helpers/ContactFileTransaction.h").write_text(transaction)
            store=(ROOT/"src/helpers/AGCSettings.cpp").read_text()
            for header in ("AGCSettings.h","CheckedSettingsFile.h","PersistentStoreFormat.h","AtomicFileWriter.h","ContactFileTransaction.h"):
                store=store.replace(f'#include "{header}"',f'#include <helpers/{header}>')
            (work/"store_under_test.cpp").write_text(store)
            (work/"test.cpp").write_text(HARNESS)
            for platform in ("ESP32_PLATFORM","RP2040_PLATFORM","NRF52_PLATFORM","STM32_PLATFORM"):
                for enabled in (0,1):
                    with self.subTest(platform=platform,old_agc=enabled):
                        binary=work/(platform+str(enabled))
                        built=subprocess.run([os.environ.get("CXX","g++"),"-std=c++17","-O1","-Wall","-Wextra","-Werror",
                            "-Wno-sign-compare","-Wno-unused-parameter","-fsanitize=address,undefined","-fno-sanitize-recover=all",
                            "-fno-pie","-no-pie","-DMESH_ENABLE_OLD_AGC="+str(enabled),"-D"+platform,
                            "-I"+str(work),"-I"+str(ROOT/"src"),str(work/"test.cpp"),"-o",str(binary)],capture_output=True,text=True,timeout=60)
                        self.assertEqual(built.returncode,0,built.stdout+built.stderr)
                        result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=20)
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=="__main__":unittest.main()
