"""Exercise actual CommonCLI USB debug commands, images, and import commits."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <math.h>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <helpers/IdentityStore.h>
class TestFS : public MemoryFS {
 public:
  using MemoryFS::open;
  File open(const char* path, uint8_t mode) {
    return MemoryFS::open(path, mode == FILE_O_WRITE ? "w" : "r");
  }
};
#undef FILESYSTEM
#define FILESYSTEM TestFS
#include "ContactFileTransaction.h"
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#include <helpers/PrefsSaveRouting.h>
#include <helpers/CommonPrefsRecovery.h>
#include <helpers/HilStartupTrace.h>
#include <helpers/MQTTPrefsAtomicStore.h>
#include <helpers/CLICommandUtils.h>
#include <helpers/RepeaterRadioTiming.h>
#include <helpers/GpsPowerPolicy.h>
#include <helpers/bridges/ESPNowBridgeFormat.h>
#include <helpers/radiolib/LR2021SideDetectorConfig.h>
#define MIN_LORA_TX_POWER -9
#define MAX_LORA_TX_POWER 22
#include <helpers/radiolib/RadioPowerLimits.h>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define MESH_USB_LOGGING_AVAILABLE 1
#define DEFAULT_POWERSAVING_ENABLED 1
#define DEFAULT_CAD_ENABLED 0
#define BRIDGE_MAX_BAUD 115200
#define RX_POWERSAVING_DEFAULT_RX_US 65625UL
#define RX_POWERSAVING_DEFAULT_SLEEP_US 60000UL
#define FLOOD_RETRY_PATH_GATE_DISABLED 0xFF
#define MIN_LOCAL_ADVERT_INTERVAL 60
#define constrain(value, low, high) ((value) < (low) ? (low) : ((value) > (high) ? (high) : (value)))
@CONSTANTS@
namespace mesh {
struct RadioProfiles { static constexpr unsigned MaxPreamble = 65535; };
static bool usb_logging = true, usb_debug = false;
static bool prohibit_stale_debug = false;
static unsigned usb_updates = 0, debug_updates = 0;
void setUsbLoggingEnabled(bool enabled) {
  if (enabled && prohibit_stale_debug) assert(!usb_debug);
  usb_logging = enabled; ++usb_updates;
}
void setUsbDebugEnabled(bool enabled) { usb_debug = enabled; ++debug_updates; }
bool isUsbLoggingEnabled() { return usb_logging; }
bool isUsbDebugEnabled() { return usb_debug; }
bool isUsbDebugLoggingEnabled() { return usb_logging && usb_debug; }
}
struct NodePrefs {
  @FIELDS@
  bool dirty = false;
  void clearDirty() { dirty = false; }
  void markUnsaved() { dirty = true; }
};
@HELPERS@
void ensureRxPowerSavingDefaults(uint32_t*, uint32_t*) {}
void recalcRxPowerSavingFromLevel(uint8_t, uint8_t, float, uint8_t,
                                uint32_t*, uint32_t*) {}
@LEGACY@
@SERIALIZER@
@STORE@;
struct CommonCLI;
struct Callbacks {
  CommonCLI* cli;
  void savePrefs(PrefsSaveRouting::Scope scope);
  void updateAdvertTimer() {}
};
struct CommonCLI {
  FILESYSTEM fs;
  NodePrefs prefs;
  NodePrefs* _prefs = &prefs;
  LegacyObserverTail _legacy_tail{};
  Callbacks callbacks{this};
  Callbacks* _callbacks = &callbacks;
  bool _com_prefs_needs_upgrade = false;
  bool _common_save_result_known = false, _common_save_succeeded = false;
  bool _observer_save_result_known = false, _observer_save_succeeded = false;
  uint32_t _prefs_save_failures = 0;
  CommonCLI() {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
    fs.rename_replaces = true;
#endif
    prefs.freq = 910; prefs.bw = 62.5f; prefs.sf = 7; prefs.cr = 5;
    prefs.primary_radio_preamble = 48;
  }
  void savePrefs(FILESYSTEM*, PrefsSaveRouting::Scope);
  void savePrefs(PrefsSaveRouting::Scope scope = PrefsSaveRouting::Scope::Common);
  bool trySavePrefs(PrefsSaveRouting::Scope scope = PrefsSaveRouting::Scope::Common);
  void loadPrefsInt(FILESYSTEM*, const char*);
  bool recoverCommonPrefsFiles(FILESYSTEM*);
  bool saveCommonPrefsImageAtomically(FILESYSTEM*);
  bool saveMQTTPrefs(FILESYSTEM*) { return true; }
  void set(const char* config, char* reply) {
    @SET_DEBUG@
    @SET_MASTER@
    strcpy(reply, "unknown");
  }
  void get(const char* config, char* reply) {
    @GET_DEBUG@
    @GET_MASTER@
    strcpy(reply, "unknown");
  }
};
void Callbacks::savePrefs(PrefsSaveRouting::Scope scope) {
  cli->savePrefs(&cli->fs, scope);
}
@METHODS@

// Execute the production dynamic schema, replacing only its generic codec.
// Registered uint8_t references emulate imported configuration values.
class ConfigSerializer {
 protected:
  virtual void structure() = 0;
  template<typename T> void def(const char*, T&) {}
  void def(const char* key, uint8_t& value) { bytes[key] = &value; }
  void def(const char*, char*, size_t) {}
 public:
  std::map<std::string, uint8_t*> bytes;
  bool import(const char* key, uint8_t value) {
    structure();
    if (!bytes.count(key)) return false;
    *bytes[key] = value;
    return true;
  }
};
@DYNAMIC@;
struct Capture {
  std::vector<uint8_t> bytes;
  size_t write(const uint8_t* data, size_t n) {
    bytes.insert(bytes.end(), data, data + n);
    return n;
  }
};
int main() {
  CommonCLI cli;
  char reply[160] = {};
  assert(cli.prefs.usb_debug_enabled == 0);
  cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Common);
  assert(cli._common_save_succeeded);
  const auto baseline = cli.fs.files["/com_prefs"];
  assert(baseline.size() == 877);
  const size_t debug_offset = 871, trace_offset = 872;
  const size_t logging_offset = 863, preamble_offset = 866;
  const size_t espnow_offset = 868, gps_offset = 869;
  assert(baseline[debug_offset] == 0 && baseline[logging_offset] == 1);
  assert(baseline[preamble_offset] == 48 && baseline[espnow_offset] == 1);
  assert(baseline[gps_offset] == 0 && baseline[gps_offset + 1] == 0);
  assert(baseline[trace_offset] == 0);
  assert(baseline[873] == 0); // OTA channel follows the established tail.
  Capture capture;
  assert(writeCommonPrefsImage(capture, &cli.prefs));
  assert(capture.bytes == baseline); // Checked and ordinary writers agree.

  cli.set("usb.debug on", reply);
  assert(strcmp(reply, "OK - USB debug on (saved)") == 0);
  assert(cli.prefs.usb_debug_enabled == 1 && mesh::isUsbDebugLoggingEnabled());
  auto enabled = cli.fs.files["/com_prefs"];
  assert(enabled.size() == baseline.size() && enabled[debug_offset] == 1);
  assert(std::equal(baseline.begin(), baseline.begin() + debug_offset, enabled.begin()));
  cli.set("usb.logging off", reply);
  assert(!mesh::isUsbLoggingEnabled() && mesh::isUsbDebugEnabled());
  assert(!mesh::isUsbDebugLoggingEnabled());
  cli.get("usb.debug", reply);
  assert(strcmp(reply, "> on") == 0); // Saved intent is visible with master OFF.
  cli.get("usb.logging", reply);
  assert(strcmp(reply, "> off") == 0);

  // Malformed tokens and suffixes never mutate either gate or the image.
  const auto unchanged = cli.fs.files["/com_prefs"];
  for (const char* command : {"usb.debug", "usb.debug true", "usb.debug 1",
      "usb.debug ON", "usb.debug off junk", "usb.debug off ",
      "usb.debug on reboot", "usb.debug off reboot"}) {
    const unsigned updates = mesh::debug_updates;
    cli.set(command, reply);
    assert(strcmp(reply, "Error: usage set usb.debug on|off") == 0);
    assert(cli.prefs.usb_debug_enabled == 1 && mesh::isUsbDebugEnabled());
    assert(mesh::debug_updates == updates);
    assert(cli.fs.files["/com_prefs"] == unchanged);
  }
  cli.set("usb.debugger off", reply);
  assert(strcmp(reply, "unknown") == 0);
  cli.set("usb.debug\t \toff", reply);
  assert(strcmp(reply, "OK - USB debug off (saved)") == 0);
  assert(cli.prefs.usb_debug_enabled == 0 && !mesh::isUsbDebugEnabled());
  const auto debug_off = cli.fs.files["/com_prefs"];

  // Failed first/write/verification/publication stages cannot enable debug.
  for (int fault : {0, 1, 2, 3}) {
    if (fault == 0) cli.fs.fail_write = true;
    if (fault == 1) cli.fs.fail_write_after = debug_offset;
    if (fault == 2) cli.fs.fail_read_open = true;
    if (fault == 3) cli.fs.fail_rename = 1;
    const unsigned updates = mesh::debug_updates;
    cli.set("usb.debug on", reply);
    assert(strcmp(reply, "Error: save unconfirmed; reboot may differ") == 0);
    assert(cli.prefs.usb_debug_enabled == 0 && !mesh::isUsbDebugEnabled());
    assert(mesh::debug_updates == updates && !mesh::isUsbLoggingEnabled());
    assert(cli.fs.files["/com_prefs"] == debug_off);
    cli.fs.fail_write = false; cli.fs.fail_write_after = -1;
    cli.fs.fail_read_open = false; cli.fs.fail_rename = 0;
  }

  // Imported schemas apply gates only after a successful common commit.
  BridgePrefs dynamic(&cli.prefs);
  assert(dynamic.import("usb_dbg", 1));
  assert(!mesh::isUsbDebugEnabled());
  const unsigned observer_updates = mesh::debug_updates;
  cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Observer);
  assert(!mesh::isUsbDebugEnabled() && mesh::debug_updates == observer_updates);
  assert(cli.fs.files["/com_prefs"] == debug_off);
  cli.fs.fail_write = true;
  cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Common);
  assert(!cli._common_save_succeeded && !mesh::isUsbDebugEnabled());
  cli.fs.fail_write = false;
  cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Common);
  assert(cli._common_save_succeeded && mesh::isUsbDebugEnabled());
  assert(!mesh::isUsbDebugLoggingEnabled());
  assert(dynamic.import("usb_log", 1));
  cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Common);
  assert(mesh::isUsbDebugLoggingEnabled());
  // A debug-OFF import must take effect before the master is enabled again.
  mesh::setUsbLoggingEnabled(false);
  assert(dynamic.import("usb_dbg", 0));
  mesh::prohibit_stale_debug = true;
  cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Common);
  mesh::prohibit_stale_debug = false;
  assert(mesh::isUsbLoggingEnabled() && !mesh::isUsbDebugEnabled());
  for (uint8_t bad : {2, 255}) {
    assert(dynamic.import("usb_dbg", bad));
    cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Common);
    assert(cli._common_save_succeeded && !mesh::isUsbDebugEnabled());
    assert(cli.prefs.usb_debug_enabled == 0 && cli.fs.files["/com_prefs"][debug_offset] == 0);
  }

  // Every image without the complete debug byte inherits debug OFF, even
  // after loading debug ON; a missing later trace byte cannot erase debug.
  auto image = baseline;
  image[logging_offset] = 0; image[debug_offset] = 1;
  image[gps_offset] = 0x50; image[gps_offset + 1] = 0x01;
  for (size_t size = 0; size <= debug_offset; ++size) {
    CommonCLI reader;
    reader.prefs.usb_debug_enabled = 1;
    reader.fs.files["/com_prefs"] = {image.begin(), image.begin() + size};
    reader.loadPrefsInt(&reader.fs, "/com_prefs");
    assert(reader.prefs.usb_debug_enabled == 0);
    if (size > logging_offset) assert(reader.prefs.usb_logging_enabled == 0);
    if (size >= gps_offset + 2) assert(reader.prefs.gps_sync_interval_hours == 336);
    assert(reader.prefs.trace_when_repeat_off == 0);
  }
  for (uint8_t saved : {0, 1, 2, 255}) {
    image[debug_offset] = saved;
    cli.fs.files["/com_prefs"] = image;
    cli.prefs.usb_debug_enabled = 1;
    cli.loadPrefsInt(&cli.fs, "/com_prefs");
    assert(cli.prefs.usb_debug_enabled == (saved == 1 ? 1 : 0));
    assert(cli.prefs.usb_logging_enabled == 0);
    assert(cli.prefs.primary_radio_preamble == 48 && cli.prefs.espnow_bridge_enabled == 1);
    assert(cli.prefs.gps_sync_interval_hours == 336);
  }
  cli.prefs.usb_debug_enabled = 1;
  cli.loadPrefsInt(&cli.fs, "/missing");
  assert(cli.prefs.usb_debug_enabled == 0);
}
'''


class CommonUsbDebugTest(unittest.TestCase):
    def test_actual_common_commands_and_persistence(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text(encoding='utf-8')
        header = (ROOT / 'src/helpers/CommonCLI.h').read_text(encoding='utf-8')
        fields = header.split('class NodePrefs :', 1)[1].split('private:', 1)[0]
        fields = '\n'.join(re.findall(
            r'^\s*(?:float|double|char|u?int(?:8|16|32)_t)\s+[^;]+;', fields, re.M))
        constants = '\n'.join(re.findall(
            r'^\s*#define\s+\w+\s+(?:0x[\dA-Fa-f]+|\d+)\s*$', header, re.M))
        constants += '\n' + '\n'.join(re.findall(
            r'^static const size_t LEGACY_\w+\s*=[^;]+;', source, re.M))
        constants += '\n' + re.search(
            r'^static constexpr uint8_t DEFAULT_ADVERT_LOC_POLICY\s*=[^;]+;',
            header, re.M).group()
        helpers = '\n'.join(extract_braced(source, signature) for signature in (
            'static bool bwMatches(', 'static bool isValidLoRaBandwidth(',
            'static float defaultLoRaBandwidth(', 'static void markDirectRetryPrefsValid(',
            'static void applyFloodRetryPreset(', 'static void applyDirectRetryPreset(',
            'static void setDefaultDirectRetryPrefs(', 'static bool directRetryPrefsValid('))
        methods = '\n'.join(extract_braced(source, signature) for signature in (
            'static const char* commonPrefsSaveResultName(',
            'bool CommonCLI::recoverCommonPrefsFiles(',
            'bool CommonCLI::saveCommonPrefsImageAtomically(',
            'void CommonCLI::savePrefs(FILESYSTEM*',
            'void CommonCLI::savePrefs(PrefsSaveRouting::Scope scope)',
            'bool CommonCLI::trySavePrefs(', 'void CommonCLI::loadPrefsInt('))
        code = HARNESS
        for key, value in {
            'FIELDS': fields, 'CONSTANTS': constants, 'HELPERS': helpers,
            'LEGACY': extract_braced(header, 'struct LegacyObserverTail') + ';',
            'SERIALIZER': 'template<typename Writer>\n' + extract_braced(
                source, 'static bool writeCommonPrefsImage('),
            'STORE': extract_braced(source, 'class CommonPrefsFileStore'),
            'METHODS': methods,
            'DYNAMIC': extract_braced(header, 'class BridgePrefs :'),
            'SET_DEBUG': extract_braced(source, 'if (strncmp(config, "usb.debug", 9)'),
            'SET_MASTER': extract_braced(source, 'if (strncmp(config, "usb.logging", 11)'),
            'GET_DEBUG': extract_braced(source, 'if (strcmp(config, "usb.debug") == 0)'),
            'GET_MASTER': extract_braced(source, 'if (strcmp(config, "usb.logging") == 0)'),
        }.items():
            code = code.replace('@' + key + '@', value)
        sanitizer_flags = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='common-usb-debug-') as directory:
            work = Path(directory)
            transaction = (ROOT / 'src/helpers/ContactFileTransaction.h').read_text()
            (work / 'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            (work / 'test.cpp').write_text(code, encoding='utf-8')
            variants = [('ENABLE_OTA', platform) for platform in (
                'NRF52_PLATFORM', 'STM32_PLATFORM', 'ESP32_PLATFORM', 'RP2040_PLATFORM')]
            variants.append(('ENABLE_OTA', 'ESP32_PLATFORM', 'WITH_MQTT_BRIDGE'))
            variants.extend((('NRF52_PLATFORM',), ('ESP32_PLATFORM', 'WITH_MQTT_BRIDGE')))
            for macros in variants:
                with self.subTest(macros=macros):
                    exe = work / ('test.exe' if os.name == 'nt' else 'test')
                    built = subprocess.run([
                        os.environ.get('CXX', 'g++'), '-std=c++17',
                        *['-D' + macro + '=1' for macro in macros], *sanitizer_flags,
                        '-I' + str(work), '-I' + str(ROOT / 'test/fixtures/radio_profiles/mocks'),
                        '-I' + str(ROOT / 'src'), '-I' + str(ROOT / 'src/helpers'),
                        str(work / 'test.cpp'), '-o', str(exe)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    tested = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(tested.returncode, 0, tested.stderr)

    def test_outer_load_applies_both_gates(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text(encoding='utf-8')
        load = extract_braced(source, 'void CommonCLI::loadPrefs(FILESYSTEM*')
        self.assertIn('_prefs->usb_debug_enabled = 0;', load)
        self.assertIn('mesh::setUsbLoggingEnabled(_prefs->usb_logging_enabled != 0);', load)
        self.assertIn('mesh::setUsbDebugEnabled(_prefs->usb_debug_enabled != 0);', load)
        self.assertLess(load.index('mesh::setUsbDebugEnabled('),
                        load.index('mesh::setUsbLoggingEnabled('))


if __name__ == '__main__':
    unittest.main()
