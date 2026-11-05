"""Exercise actual radio-safe persistence, boot defaults and CLI errors."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <cassert>
#include <cstring>
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include "settings_under_test.cpp"
int main() {
  using namespace mesh::lr2021;
  MemoryFS fs;
#if defined(NRF52_PLATFORM)
  fs.rename_replaces=true;
#endif
  char reply[160];
  auto run=[&](const char* cmd) { return handleSafeModeCommand(cmd,reply,sizeof(reply)); };
  assert(safeMode()); // no scan before persistent boot settings load
  loadSafeModeSettings(&fs); assert(!safeMode());
  assert(run("get radio.safe") && !strcmp(reply,"> off"));
  assert(run("set radio.safe on") && !strncmp(reply,"OK",2) && safeMode());
  loadSafeModeSettings(&fs); assert(safeMode());
  assert(run("set radio.safe off") && !strncmp(reply,"OK",2) && !safeMode());
  loadSafeModeSettings(&fs); assert(!safeMode());
  const auto good=fs.files.at("/radio_safe");
  fs.fail_write=true;
  assert(run("set radio.safe on") && strstr(reply,"not saved") && strstr(reply,"on for this boot") && safeMode());
  assert(run("get radio.safe") && !strcmp(reply,"> on (not saved)"));
  fs.fail_write=false;
  loadSafeModeSettings(&fs); assert(!safeMode()); // previous committed off survives
  fs.fail_rename_from={"/radio_safe.tmp", "/radio_safe.bak"};
  assert(run("set radio.safe on") && strstr(reply,"not saved") && safeMode());
  assert(!valid && !writable); // publication was indeterminate
  assert(run("get radio.safe") && !strcmp(reply,"> on (not saved)"));
  fs.fail_rename_from.clear();
  loadSafeModeSettings(&fs); assert(!safeMode());
  assert(run("set radio.safe on") && !strncmp(reply,"OK",2) && safeMode());
  fs.fail_write=true;
  assert(run("set radio.safe off") && strstr(reply,"not saved") && safeMode());
  fs.fail_write=false;
  loadSafeModeSettings(&fs); assert(safeMode());
  assert(run("set radio.safe off") && !strncmp(reply,"OK",2) && !safeMode());
  fs.files["/radio_safe"][2]^=1;
  loadSafeModeSettings(&fs); assert(safeMode());
  assert(run("get radio.safe") && strstr(reply,"settings unavailable"));
  assert(run("set radio.safe on") && !strncmp(reply,"OK",2));
  loadSafeModeSettings(&fs); assert(safeMode());
  fs.files["/radio_safe"]=good;
  fs.fail_read_open=true;
  loadSafeModeSettings(&fs); assert(safeMode());
  fs.fail_read_open=false;
  assert(run("set radio.safe off") && !strncmp(reply,"Error",5) && safeMode());
  loadSafeModeSettings(&fs); assert(!safeMode());
  loadSafeModeSettings(nullptr); assert(safeMode());
  assert(run("set radio.safe off") && !strncmp(reply,"Error",5) && safeMode());
  assert(!run("set radio.safely on"));
}
'''

class SafeModeTests(unittest.TestCase):
    def test_boot_and_commands_on_esp_and_nrf(self):
        for platform in ('ESP32_PLATFORM', 'NRF52_PLATFORM'):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                tmp=Path(directory)
                (tmp/'helpers').mkdir()
                mock=(ROOT/'test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h').read_text()
                (tmp/'IdentityStore.h').write_text(mock)
                (tmp/'helpers/IdentityStore.h').write_text('#include "../IdentityStore.h"\n')
                (tmp/'FilePresence.h').write_text('''#pragma once
#include "IdentityStore.h"
namespace mesh { template <typename FS> bool filePresence(FS* fs,const char* path,bool& present) {
  if (!fs) return false; present=fs->exists(path); return true;
} }
''')
                (tmp/'helpers/FilePresence.h').write_text('#include "../FilePresence.h"\n')
                for name in ('CheckedSettingsFile.h','ContactFileTransaction.h','AtomicFileWriter.h'):
                    (tmp/name).write_bytes((ROOT/'src/helpers'/name).read_bytes())
                    (tmp/'helpers'/name).write_text('#include "../'+name+'"\n')
                (tmp/'LR2021SafeMode.h').write_bytes((ROOT/'src/helpers/radiolib/LR2021SafeMode.h').read_bytes())
                (tmp/'settings_under_test.cpp').write_bytes((ROOT/'src/helpers/radiolib/LR2021SafeMode.cpp').read_bytes())
                (tmp/'test.cpp').write_text(HARNESS)
                result=subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror',
                    '-Wno-sign-compare','-Wno-misleading-indentation','-D'+platform,
                    '-DMESH_W12_PRAM_EXPERIMENT=1','-I'+str(tmp),'-I'+str(ROOT/'src/helpers'),
                    str(tmp/'test.cpp'),'-o',str(tmp/'test')],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                result=subprocess.run([str(tmp/'test')],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':
    unittest.main()
