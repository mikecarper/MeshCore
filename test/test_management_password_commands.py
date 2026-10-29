#!/usr/bin/env python3
"""Exercise production role setters against failed saves and active flash uploads."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ManagementPasswordCommandsTest(unittest.TestCase):
    def test_infrastructure_and_companion_publish_only_confirmed_saves(self):
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        start = cli.index('    } else if (memcmp(command, "password ", 9) == 0) {')
        end = cli.index('    } else if (memcmp(command, "clear stats",', start)
        infrastructure = cli[start:end].replace('} else if (', 'if (', 1) + '\n    }\n'
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        start = companion.index('bool MyMesh::handleLocalControlCommand(')
        end = companion.index('  if (mesh::powerdiag::handleCommand(', start)
        companion = companion[start:end] + '  return false;\n}\n'
        source = r'''
#include <helpers/esp32/ManagementPassword.h>
#include <helpers/CLICommandUtils.h>
#include <helpers/ConfigCapabilities.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
struct HardwareSerial {};
static HardwareSerial Serial;
#include <helpers/UsbMode.h>
using namespace mesh::esp32::managementPassword;
bool save_ok = true; int saves = 0;
bool durableSave() {
  ++saves; Snapshot current; assert(snapshot(current));
  UploadGuard blocked(current.generation); assert(!blocked.acquired());
  return save_ok;
}
namespace mesh { namespace esp32 { namespace companionAdminPassword {
bool save(const char*) { return durableSave(); }
} } }
struct StrHelper {
  static void strncpy(char* out, const char* in, size_t size) { ::strncpy(out, in, size); out[size - 1] = 0; }
};
struct NodePrefs { char password[16] = "password"; };
// Password commands return before unrelated capability or profile queries.
struct Board {
  bool getSystemWatchdogRunning(bool&) { assert(false); return false; }
  bool canControlLoRaFemLna() { assert(false); return false; }
  bool canControlLoRaFemPaGain() { assert(false); return false; }
  bool isLoRaFemPaGainEnabled() { assert(false); return false; }
} board;
struct Radio {
  bool supportsRxPowerSavingRfRxDisable() const { assert(false); return false; }
} radio_driver;
struct UI {
  bool supportsDisplayRotation() { assert(false); return false; }
};
struct Profiles {
  uint16_t primaryPreamble() const { assert(false); return 0; }
};
class CommonCLI {
 public:
  NodePrefs prefs; NodePrefs* _prefs = &prefs;
  bool trySavePrefs() { return durableSave(); }
  void command(char* command, char* reply) {
@INFRASTRUCTURE@
  }
};
class MyMesh {
 public:
  Profiles _radio_profiles;
  UI* _ui = nullptr;
  char _management_password[16] = "password";
  bool _management_password_ready = true;
  bool handleLocalControlCommand(const char*, char*, size_t);
  void command(char* command, char* reply) { handleLocalControlCommand(command, reply, 160); }
};
@COMPANION@
void unchanged(uint32_t generation) {
  Snapshot current; assert(snapshot(current));
  assert(current.generation == generation && !strcmp(current.password, "password"));
}
template <typename Role> void test(Role& role) {
  char command[80], reply[160]; Snapshot initial;
  assert(publish("password") && snapshot(initial));
  for (const char* value : {"", "1234567890123456", "bad\nvalue", "bad\rvalue"}) {
    snprintf(command, sizeof(command), "password %s", value);
    reply[0] = 0; const int before = saves; role.command(command, reply);
    assert(!strncmp(reply, "Error:", 6) && saves == before); unchanged(initial.generation);
  }
  save_ok = false; strcpy(command, "password replacement"); role.command(command, reply);
  assert(strstr(reply, "unconfirmed")); unchanged(initial.generation);
  save_ok = true;
  {
    UploadGuard upload(initial.generation); assert(upload.acquired());
    const int before = saves; role.command(command, reply);
    assert(strstr(reply, "busy") && saves == before); unchanged(initial.generation);
  }
  role.command(command, reply); assert(!strcmp(reply, "password now: replacement"));
  Snapshot changed; assert(snapshot(changed));
  assert(!strcmp(changed.password, "replacement") && changed.generation == initial.generation + 1);
  role.command(command, reply); assert(snapshot(changed) && changed.generation == initial.generation + 1);
  strcpy(command, "password  leading space"); role.command(command, reply);
  assert(snapshot(changed) && !strcmp(changed.password, " leading space"));
}
int main() {
  CommonCLI infrastructure; test(infrastructure);
  assert(!strcmp(infrastructure.prefs.password, " leading space"));
  MyMesh companion; test(companion);
  assert(companion._management_password_ready && !strcmp(companion._management_password, " leading space"));
}
'''.replace("@INFRASTRUCTURE@", infrastructure).replace("@COMPANION@", companion)
        with tempfile.TemporaryDirectory(prefix="management-command-") as temporary:
            work = Path(temporary)
            (work / "test.cpp").write_text(source)
            result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DMESH_MANAGEMENT_PASSWORD_HOST_TEST=1", "-DESP_PLATFORM=1", "-DESP32_PLATFORM=1", '-DWIFI_SSID=""',
                "-I", str(ROOT / "src"), str(work / "test.cpp"), "-o", str(work / "test")],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(work / "test")], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
