"""Exercise Companion's production retry command parser and save semantics."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
#include "examples/companion_radio/CompanionRetry.h"

int main() {
  CompanionNodePrefs prefs;
  char reply[160] = {};
  int saves = 0, cancels = 0;
  bool save_ok = true;
  auto save = [&]() { ++saves; return save_ok; };
  auto cancel = [&]() { ++cancels; };
  auto run = [&](const char* command) {
    memset(reply, 0, sizeof(reply));
    return mesh::companion::handleRetryCommand(
        prefs, command, reply, sizeof(reply), save, cancel);
  };

  assert(run("get flood.retry.count") && !strcmp(reply, "> 15"));
  assert(run("get flood.retry.path") && !strcmp(reply, "> 1"));
  assert(run("get flood.retry.group.path") && !strcmp(reply, "> off"));
  assert(run("get flood.retry.advert") && !strcmp(reply, "> on"));
  assert(saves == 0);

  assert(run("set flood.retry.count 8") && !strcmp(reply, "OK"));
  assert(run("get flood.retry.count") && !strcmp(reply, "> 8"));
  assert(run("set flood.retry.count 2") && !strcmp(reply, "OK"));
  assert(run("get flood.retry.count") && !strcmp(reply, "> 2"));
  assert(run("set flood.retry.path off") && !strcmp(reply, "OK"));
  assert(run("get flood.retry.path") && !strcmp(reply, "> off"));
  assert(run("set flood.retry.group.path 3") && !strcmp(reply, "OK"));
  assert(run("get flood.retry.group.path") && !strcmp(reply, "> 3"));
  assert(run("set flood.retry.advert off") && !strcmp(reply, "OK"));
  assert(run("get flood.retry.advert") && !strcmp(reply, "> off"));

  const int saved_before_invalid = saves;
  for (const char* command : {"set flood.retry.count 16",
                              "set flood.retry.count -1",
                              "set flood.retry.count 3junk",
                              "set flood.retry.path 64",
                              "set flood.retry.group.path",
                              "set flood.retry.advert maybe"}) {
    assert(run(command) && !strncmp(reply, "Error:", 6));
  }
  assert(saves == saved_before_invalid);
  assert(prefs.flood_retry_attempts == 2);
  assert(prefs.flood_retry_max_path == 0xff);
  assert(prefs.flood_retry_group_max_path == 3);
  assert(prefs.flood_retry_advert_enabled == 0);

  save_ok = false;
  assert(run("set flood.retry.count 0"));
  assert(!strcmp(reply, "Error: retry setting could not be saved"));
  assert(prefs.flood_retry_attempts == 2 && cancels == 0);
  assert(run("set flood.retry.advert on"));
  assert(prefs.flood_retry_advert_enabled == 0);
  save_ok = true;
  assert(run("set flood.retry.count 0") && !strcmp(reply, "OK"));
  assert(prefs.flood_retry_attempts == 0 && cancels == 1);
  assert(run("set flood.retry.count 0") && cancels == 1);
  assert(!run("set flood.retry.bridge on"));
}
'''


class CompanionRetrySettingsTests(unittest.TestCase):
    def test_retry_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "retry.cpp"
            binary = work / "retry"
            source.write_text(HARNESS)
            (work / "Utils.h").write_text('''#pragma once
#include <Arduino.h>
#include <cassert>
namespace mesh { struct Utils {
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
}; }
''')
            build = subprocess.run([
                "g++", "-std=c++17", "-DNRF52_PLATFORM=1",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-fno-pie", "-no-pie",
                "-I", str(work),
                "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                "-I", str(ROOT / "test/mocks"),
                "-I", str(ROOT / "src"),
                "-I", str(ROOT),
                str(source),
                str(ROOT / "src/helpers/ConfigSerializer.cpp"),
                str(ROOT / "src/helpers/DynamicConfigSerializer.cpp"),
                str(ROOT / "src/helpers/CommonRadioPrefs.cpp"),
                str(ROOT / "src/helpers/TxtDataHelpers.cpp"),
                "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
