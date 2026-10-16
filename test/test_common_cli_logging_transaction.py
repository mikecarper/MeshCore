"""Run the real logging setters with failed saves and failed MQTT transitions."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include "helpers/CLICommandUtils.h"
struct Prefs { uint8_t usb_logging_enabled = 0, bridge_enabled = 0; };
static bool live_usb = false;
static unsigned usb_calls = 0;
namespace mesh {
void setUsbLoggingEnabled(bool value) { live_usb = value; ++usb_calls; }
}
struct Callbacks {
  bool live_wifi = false, apply_ok = true, stopping = false;
  unsigned calls = 0;
  bool setMqttBridgeState(bool value) {
    ++calls;
    if (apply_ok) live_wifi = value;
    return apply_ok;
  }
  bool requestMqttBridgeStop() { return setMqttBridgeState(false); }
  bool isMqttBridgeStopping() { return stopping; }
};
struct CLI {
  Prefs prefs, persisted;
  Prefs* _prefs = &prefs;
  Callbacks callbacks;
  Callbacks* _callbacks = &callbacks;
  bool save_ok = true;
  unsigned saves = 0;
  bool trySavePrefs() { ++saves; if (save_ok) persisted = prefs; return save_ok; }
  void set(const char* config, char* reply) { @BRANCHES@ }
};
static void check(const char* command, bool usb, bool wifi, bool failed) {
  CLI cli;
  cli.prefs = {uint8_t(!usb), uint8_t(!wifi)};
  cli.persisted = cli.prefs;
  live_usb = !usb;
  cli.callbacks.live_wifi = !wifi;
  usb_calls = 0;
  cli.save_ok = !failed;
  char reply[160]{};
  cli.set(command, reply);
  assert(cli.saves == 1);
  assert(cli.prefs.usb_logging_enabled == (failed ? !usb : usb));
  assert(cli.persisted.usb_logging_enabled == (failed ? !usb : usb));
  assert(live_usb == (failed ? !usb : usb));
  assert(usb_calls == (failed ? 0u : 1u));
  assert(strstr(reply, failed ? "unconfirmed" : "(saved)"));
  if (failed) assert(strstr(reply, "reboot may differ"));
  if (strncmp(command, "logging.output", 14) == 0) {
    assert(cli.prefs.bridge_enabled == (failed ? !wifi : wifi));
    assert(cli.persisted.bridge_enabled == (failed ? !wifi : wifi));
    assert(cli.callbacks.live_wifi == (failed ? !wifi : wifi));
    assert(cli.callbacks.calls == (failed ? 0u : 1u));
  } else {
    assert(cli.callbacks.calls == 0);
  }
}
int main() {
  for (bool failed : {false, true}) {
    check("usb.logging on", true, false, failed);
    check("usb.logging off", false, false, failed);
#if WITH_MQTT_BRIDGE
    check("logging.output off", false, false, failed);
    check("logging.output usb", true, false, failed);
    check("logging.output wifi", false, true, failed);
    check("logging.output both", true, true, failed);
#endif
  }
  CLI cli;
  char reply[160]{};
  cli.set("usb.logging invalid", reply);
  assert(cli.saves == 0 && strstr(reply, "Error:"));
#if WITH_MQTT_BRIDGE
  cli.set("logging.output invalid", reply);
  assert(cli.saves == 0 && strstr(reply, "Error:"));
  cli.callbacks.apply_ok = false;
  cli.set("logging.output both", reply);
  assert(cli.persisted.usb_logging_enabled && cli.persisted.bridge_enabled);
  assert(live_usb && !cli.callbacks.live_wifi);
  assert(strstr(reply, "saved, but MQTT runtime change failed"));
  // A restart during cleanup must not save ON or change either live output.
  cli.callbacks.stopping = true;
  cli.prefs = cli.persisted = {0, 0};
  live_usb = false;
  const unsigned before_saves = cli.saves, before_calls = cli.callbacks.calls;
  cli.set("logging.output both", reply);
  assert(strstr(reply, "MQTT is stopping"));
  assert(cli.saves == before_saves && cli.callbacks.calls == before_calls);
  assert(!cli.prefs.bridge_enabled && !cli.persisted.bridge_enabled && !live_usb);
#endif
}
'''


class LoggingTransactionTest(unittest.TestCase):
    def test_companion_reply_survives_input_buffer_reset_during_mode_change(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        branch = extract_braced(source, 'if (strncmp(command, "set logging.output ", 19) == 0)')
        harness = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include "helpers/CLICommandUtils.h"
static char input[64];
namespace CompanionMqttSetupPortal { static bool saveEnabled(bool) { return true; } }
namespace mesh { static bool saveUsbLoggingBootPreference(bool) { return true; } }
struct Mesh {
  bool _mqtt_enabled = true;
  struct { unsigned usb_logging_enabled = 1; } _prefs;
  void stopMQTT() {}
  bool savePrefs() { return true; }
  void applyUsbLoggingState(bool) { memset(input, 0, sizeof(input)); }
  bool handle(const char* command, char* reply, size_t reply_size) { @BRANCH@ return false; }
};
int main() {
  for (const char* mode : {"off", "usb", "wifi", "both"}) {
    Mesh node;
    char reply[160], expected[160];
    snprintf(input, sizeof(input), "set logging.output %s", mode);
    snprintf(expected, sizeof(expected), "OK - logging.output %s (saved)", mode);
    assert(node.handle(input, reply, sizeof(reply)));
    assert(input[0] == 0 && strcmp(reply, expected) == 0);
  }
}
'''.replace('@BRANCH@', branch)
        with tempfile.TemporaryDirectory() as directory:
            cpp, exe = Path(directory) / 'test.cpp', Path(directory) / 'test.exe'
            cpp.write_text(harness)
            subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-I' + str(ROOT / 'src'), str(cpp), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_real_setters_preserve_live_and_saved_state_on_failure(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        usb = extract_braced(source, 'if (strncmp(config, "usb.logging", 11) == 0')
        mqtt = extract_braced(source, 'if (strncmp(config, "logging.output", 14) == 0')
        for enabled in (0, 1):
            with self.subTest(mqtt=enabled), tempfile.TemporaryDirectory() as directory:
                cpp, exe = Path(directory) / 'test.cpp', Path(directory) / 'test.exe'
                cpp.write_text(HARNESS.replace('@BRANCHES@', usb + ('\n' + mqtt if enabled else '')))
                subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                '-DWITH_MQTT_BRIDGE=' + str(enabled),
                                '-I' + str(ROOT / 'src'), str(cpp), '-o', str(exe)], check=True)
                subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
