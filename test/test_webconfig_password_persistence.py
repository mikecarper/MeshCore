#!/usr/bin/env python3
"""Exercise WebConfig's actual password batch through the durable prefs image."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import test_common_prefs_uncertain_reply as prefs
import test_webconfig_password_sessions as sessions


ROOT = Path(__file__).resolve().parents[1]


class WebConfigPasswordPersistenceTest(unittest.TestCase):
    def test_form_save_survives_reboot_on_observer_and_standard_builds(self):
        # Combine the production WebConfig form/parser and drain with the
        # production CommonCLI serializer, transaction and reboot image reader.
        # Only the filesystem and HTTP transport are host adapters.
        common = prefs.actual_harness().split('// Included after the extracted')[0]
        web = sessions.WebConfigPasswordSessionsTest().command_fixture()
        web = web[web.index('struct AsyncResponseStream'):web.index('void reset()')]
        source = r'''
#include <mutex>
#include <string>
#include <ArduinoJson.h>
#include <helpers/esp32/ManagementPassword.h>
#include <helpers/WebConfigBatch.h>
#include <helpers/WebConfigKeys.h>
#ifdef WITH_MQTT_BRIDGE
const MQTTPresetDef MQTT_PRESETS[MQTT_PRESET_COUNT] = {};
#endif
namespace password = mesh::esp32::managementPassword;
using String = std::string;
using WCLock = std::lock_guard<std::mutex>;
uint32_t millis() { return 1000; }
namespace mesh {
namespace wifi {
constexpr bool kPrimaryEspNowRadio = false;
bool parseEspNowChannel(const char*, uint8_t&) { return false; }
}
struct Debug { template<typename... T> void printf(const char*, T...) {} };
Debug& usbDebugPort() { static Debug value; return value; }
}
''' + common + r'''
struct Callback {
  unsigned starts = 0, ends = 0, calls = 0;
  CommonCLI infrastructure;
  bool supportsCliTerminal() const { return true; }
  void execCommand(char* cmd, char* reply) { ++calls; infrastructure.password(cmd, reply); }
  void execAdminCommand(char* cmd, char* reply) { execCommand(cmd, reply); }
  void onConfigBatchStart() { ++starts; }
  void onConfigBatchEnd() { ++ends; }
};
''' + web + r'''
int main() {
  WebConfigServer server;
  strcpy(server.cb.infrastructure.prefs.password, "factory");
  server.cb.infrastructure.savePrefs(&server.cb.infrastructure.fs, PrefsSaveRouting::Scope::Common);
  assert(server.cb.infrastructure._common_save_succeeded);
  assert(password::initialize("factory"));
  AsyncWebServerRequest req;
  req.payload("{\"set\":{\"password\":\"new-secret\"}}");
  server.configForTest(&req);
  assert(req.status == 202);
  server.drainBatch(1000);
  assert(server.cb.calls == 1 && !strcmp(server._batch[0].reply, "OK"));
  assert(server.cb.infrastructure._common_save_succeeded);
  password::Snapshot active; assert(password::snapshot(active));
  assert(!strcmp(active.password, "new-secret"));

  CommonCLI reboot;
  reboot.fs.files = server.cb.infrastructure.fs.files;
  strcpy(reboot.prefs.password, "factory");
  reboot.loadPrefsInt(&reboot.fs, "/com_prefs");
  assert(!strcmp(reboot.prefs.password, "new-secret"));
  auto& state = password::detail::state();
  { password::detail::Lock lock(state); state.current = {}; state.changing = state.uploading = state.rotating = false; }
  assert(password::initialize(reboot.prefs.password));
  assert(password::snapshot(active) && !strcmp(active.password, "new-secret"));
  assert(password::basicAuthorizationMatches("Basic YWRtaW46bmV3LXNlY3JldA==", active.password));
  assert(!password::basicAuthorizationMatches("Basic YWRtaW46ZmFjdG9yeQ==", active.password));

  // A failed durable save must not report success, publish the candidate,
  // or change the credential recovered by a fresh boot.
  { password::detail::Lock lock(state); state.current = {}; state.changing = state.uploading = state.rotating = false; }
  assert(password::initialize("factory"));
  WebConfigServer failed;
  strcpy(failed.cb.infrastructure.prefs.password, "factory");
  failed.cb.infrastructure.savePrefs(&failed.cb.infrastructure.fs, PrefsSaveRouting::Scope::Common);
  assert(failed.cb.infrastructure._common_save_succeeded);
  failed.cb.infrastructure.fs.fail_write = true;
  AsyncWebServerRequest fail_req;
  fail_req.payload("{\"set\":{\"password\":\"new-secret\"}}");
  failed.configForTest(&fail_req);
  assert(fail_req.status == 202);
  failed.drainBatch(1000);
  assert(strstr(failed._batch[0].reply, "unconfirmed") != nullptr);
  assert(password::snapshot(active) && !strcmp(active.password, "factory"));
  assert(!strcmp(failed.cb.infrastructure.prefs.password, "factory"));
  CommonCLI failed_reboot;
  failed_reboot.fs.files = failed.cb.infrastructure.fs.files;
  failed_reboot.loadPrefsInt(&failed_reboot.fs, "/com_prefs");
  assert(!strcmp(failed_reboot.prefs.password, "factory"));
}
'''
        mock = (ROOT / 'test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h').read_text()
        transaction = (ROOT / 'src/helpers/ContactFileTransaction.h').read_text()
        json_header = next((ROOT / '.pio/libdeps').glob('*/ArduinoJson/src/ArduinoJson.h'), None)
        self.assertIsNotNone(json_header, 'ArduinoJson dependency headers are required')
        with tempfile.TemporaryDirectory(prefix='webconfig-password-persistence-') as directory:
            work = Path(directory)
            (work / 'helpers').mkdir()
            (work / 'Arduino.h').write_text('#pragma once\n#define PROGMEM\n')
            (work / 'helpers/IdentityStore.h').write_text(mock)
            (work / 'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            (work / 'test.cpp').write_text(source)
            sanitizer = [] if os.name == 'nt' else ['-fsanitize=address,undefined',
                '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
            for observer in (False, True):
                with self.subTest(observer=observer):
                    binary = work / ('test.exe' if os.name == 'nt' else 'test')
                    command = [os.environ.get('CXX', 'g++'), '-std=c++17',
                        '-DESP_PLATFORM=1', '-DESP32_PLATFORM=1',
                        '-DMESH_MANAGEMENT_PASSWORD_HOST_TEST=1', '-DENABLE_OTA=1',
                        *(['-DWITH_MQTT_BRIDGE=1'] if observer else []), *sanitizer,
                        '-I' + str(work), '-I' + str(ROOT / 'src'),
                        '-I' + str(ROOT / 'src/helpers'), '-I' + str(json_header.parent),
                        str(work / 'test.cpp'), '-o', str(binary)]
                    built = subprocess.run(command, capture_output=True, text=True, timeout=90)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
