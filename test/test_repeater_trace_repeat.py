"""Execute the real repeater trace CLI and appended preference persistence."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_common_usb_debug import HARNESS as COMMON_HARNESS
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

TRACE_MAIN = r'''
int main() {
  CommonCLI cli;
  char reply[160] = {};
  assert(cli.prefs.trace_when_repeat_off == 0);
  cli.prefs.usb_debug_enabled = 1;
  cli.prefs.gps_sync_interval_hours = 336;
  cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Common);
  assert(cli._common_save_succeeded);
  const auto baseline = cli.fs.files["/com_prefs"];
  assert(baseline.size() == 877);
  const size_t trace_offset = 872, debug_offset = 871, gps_offset = 869;
  assert(baseline[trace_offset] == 0 && baseline[debug_offset] == 1);
  assert(baseline[866] == 48 && baseline[867] == 0 && baseline[868] == 1);
  assert(baseline[gps_offset] == 0x50 && baseline[gps_offset + 1] == 0x01);
  Capture capture;
  assert(writeCommonPrefsImage(capture, &cli.prefs));
  assert(capture.bytes == baseline);
  cli.get("repeat.trace", reply);
  assert(!strcmp(reply, "> off"));
  cli.set("repeat.trace on", reply);
  assert(!strcmp(reply, "OK - repeat.trace on (saved)"));
  assert(cli.prefs.trace_when_repeat_off == 1);
  const auto enabled = cli.fs.files["/com_prefs"];
  assert(enabled.size() == baseline.size() && enabled[trace_offset] == 1);
  assert(std::equal(baseline.begin(), baseline.begin() + trace_offset, enabled.begin()));
  Capture enabled_capture;
  assert(writeCommonPrefsImage(enabled_capture, &cli.prefs));
  assert(enabled_capture.bytes == enabled);
  cli.get("repeat.trace", reply);
  assert(!strcmp(reply, "> on"));

  for (const char* command : {"repeat.trace", "repeat.trace ", "repeat.trace\t",
      "repeat.trace true", "repeat.trace 1", "repeat.trace ON", "repeat.trace Off",
      "repeat.trace off junk", "repeat.trace off ", "repeat.trace off\t",
      "repeat.trace on reboot", "repeat.trace off reboot", "repeat.trace on\n"}) {
    const auto before = cli.fs.files["/com_prefs"];
    cli.set(command, reply);
    assert(!strcmp(reply, "Error: usage set repeat.trace on|off"));
    assert(cli.prefs.trace_when_repeat_off == 1 && cli.fs.files["/com_prefs"] == before);
  }
  for (const char* query : {"repeat.trace ", "repeat.trace\t", "repeat.trace on"}) {
    cli.get(query, reply);
    assert(!strcmp(reply, "Error: usage get repeat.trace"));
  }
  for (const char* collision : {"repeat.traces", "repeat.trace_off",
      "repeat.tracex on", "repeat.traceback", "repeat", "repeat.trace/on"}) {
    cli.set(collision, reply);
    assert(!strcmp(reply, "unknown"));
    cli.get(collision, reply);
    assert(!strcmp(reply, "unknown"));
    assert(cli.prefs.trace_when_repeat_off == 1 && cli.fs.files["/com_prefs"] == enabled);
  }
  cli.set("repeat.trace\t \toff", reply);
  assert(!strcmp(reply, "OK - repeat.trace off (saved)"));
  assert(cli.prefs.trace_when_repeat_off == 0);

  // Both enable and disable must preserve the saved and requested state when
  // staging, the trace-byte write, readback or publication fails.
  for (uint8_t desired : {1, 0}) {
    cli.set(desired ? "repeat.trace off" : "repeat.trace on", reply);
    assert(cli.prefs.trace_when_repeat_off == uint8_t(!desired));
    const auto committed = cli.fs.files["/com_prefs"];
    for (int fault : {0, 1, 2, 3}) {
      const unsigned callbacks_before = cli.callbacks.retry_updates;
      if (fault == 0) cli.fs.fail_write = true;
      if (fault == 1) cli.fs.fail_write_after = trace_offset;
      if (fault == 2) cli.fs.fail_read_open = true;
      if (fault == 3) cli.fs.fail_rename = 1;
      cli.set(desired ? "repeat.trace on" : "repeat.trace off", reply);
      assert(!strcmp(reply, "Error: save unconfirmed; reboot may differ"));
      assert(cli.prefs.trace_when_repeat_off == uint8_t(!desired));
      assert(cli.fs.files["/com_prefs"] == committed);
      assert(cli.callbacks.retry_updates == callbacks_before);
      cli.fs.fail_write = false; cli.fs.fail_write_after = -1;
      cli.fs.fail_read_open = false; cli.fs.fail_rename = 0;
    }
    cli.set(desired ? "repeat.trace on" : "repeat.trace off", reply);
    assert(cli.prefs.trace_when_repeat_off == desired);
    Capture saved;
    assert(writeCommonPrefsImage(saved, &cli.prefs));
    assert(saved.bytes == cli.fs.files["/com_prefs"]);
  }

  // Sharing CommonCLI must not expose the setting on other firmware roles.
  for (const char* role : {"room-server", "sensor", "companion", "observer", "Repeater", ""}) {
    cli.callbacks.role = role;
    const auto before = cli.fs.files["/com_prefs"];
    const uint8_t previous = cli.prefs.trace_when_repeat_off;
    cli.get("repeat.trace", reply);
    assert(!strcmp(reply, "Error: repeat.trace is only supported by repeaters"));
    for (const char* command : {"repeat.trace on", "repeat.trace off"}) {
      cli.set(command, reply);
      assert(!strcmp(reply, "Error: repeat.trace is only supported by repeaters"));
      assert(cli.prefs.trace_when_repeat_off == previous && cli.fs.files["/com_prefs"] == before);
    }
  }
  cli.callbacks.role = "repeater";

  // Execute the production RepeatPrefs dynamic schema and committed writers.
  RepeatPrefs dynamic(&cli.prefs);
  for (uint8_t requested : {1, 0, 2, 255}) {
    assert(dynamic.import("trace_off", requested));
    assert(cli.prefs.trace_when_repeat_off == requested);
    cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Common);
    assert(cli._common_save_succeeded);
    assert(cli.prefs.trace_when_repeat_off == (requested == 1 ? 1 : 0));
    assert(cli.fs.files["/com_prefs"][trace_offset] == (requested == 1 ? 1 : 0));
    Capture saved;
    assert(writeCommonPrefsImage(saved, &cli.prefs));
    assert(saved.bytes == cli.fs.files["/com_prefs"]);
  }

  // Every old or torn image lacks the complete appended preference and must
  // reset prior RAM intent to OFF. GPS was published before local debug/trace;
  // a complete GPS-only image must not reinterpret either GPS byte as intent.
  auto image = enabled;
  for (size_t size = 0; size <= trace_offset; ++size) {
    CommonCLI reader;
    reader.prefs.trace_when_repeat_off = 1;
    reader.prefs.usb_debug_enabled = 1;
    reader.fs.files["/com_prefs"] = {image.begin(), image.begin() + size};
    reader.loadPrefsInt(&reader.fs, "/com_prefs");
    assert(reader.prefs.trace_when_repeat_off == 0);
    if (size > debug_offset) assert(reader.prefs.usb_debug_enabled == 1);
    else assert(reader.prefs.usb_debug_enabled == 0);
    if (size >= gps_offset + 2) assert(reader.prefs.gps_sync_interval_hours == 336);
  }
  // An old image containing trace but lacking the later channel byte keeps trace ON.
  CommonCLI old_reader;
  old_reader.prefs.ota_channel = 2;
  old_reader.fs.files["/com_prefs"] = {image.begin(), image.begin() + trace_offset + 1};
  old_reader.loadPrefsInt(&old_reader.fs, "/com_prefs");
  assert(old_reader.prefs.trace_when_repeat_off == 1 && old_reader.prefs.ota_channel == 0);
  // Explicit old GPS-only files with either byte equal to one must not arm
  // the local boolean preferences, regardless of their previous RAM values.
  for (uint16_t hours : {1, 256, 257, 336}) {
    auto legacy = std::vector<uint8_t>(enabled.begin(), enabled.begin() + 871);
    legacy[gps_offset] = uint8_t(hours);
    legacy[gps_offset + 1] = uint8_t(hours >> 8);
    CommonCLI reader;
    reader.prefs.usb_debug_enabled = reader.prefs.trace_when_repeat_off = 1;
    reader.fs.files["/com_prefs"] = legacy;
    reader.loadPrefsInt(&reader.fs, "/com_prefs");
    assert(reader.prefs.gps_sync_interval_hours == hours);
    assert(reader.prefs.usb_debug_enabled == 0 && reader.prefs.trace_when_repeat_off == 0);
  }
  for (uint8_t saved : {0, 1, 2, 255}) {
    image[trace_offset] = saved;
    CommonCLI reader;
    reader.prefs.trace_when_repeat_off = 1;
    reader.fs.files["/com_prefs"] = image;
    reader.loadPrefsInt(&reader.fs, "/com_prefs");
    assert(reader.prefs.trace_when_repeat_off == (saved == 1 ? 1 : 0));
    assert(reader.prefs.usb_debug_enabled == 1);
    assert(reader.prefs.gps_sync_interval_hours == 336);
    assert(reader.prefs.freq == 910 && reader.prefs.primary_radio_preamble == 48);
  }
  cli.prefs.trace_when_repeat_off = 1;
  cli.loadPrefsInt(&cli.fs, "/missing");
  assert(cli.prefs.trace_when_repeat_off == 0);
}
'''


def production_harness(source, header):
    """Reuse the common USB fixture while executing real trace/store methods."""
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
    setter = extract_braced(source, 'void CommonCLI::handleSetCmd(')
    getter = extract_braced(source, 'void CommonCLI::handleGetCmd(')
    branch = 'if (strncmp(config, "repeat.trace", 12)'
    code = COMMON_HARNESS.split('int main() {', 1)[0].replace(
        'void updateAdvertTimer() {}',
        'void updateAdvertTimer() {}\n'
        '  const char* role = "repeater";\n'
        '  const char* getRole() const { return role; }\n'
        '  unsigned retry_updates = 0;\n'
        '  void onRetryConfigChanged() { ++retry_updates; }')
    replacements = {
        'FIELDS': fields, 'CONSTANTS': constants, 'HELPERS': helpers,
        'LEGACY': extract_braced(header, 'struct LegacyObserverTail') + ';',
        'SERIALIZER': 'template<typename Writer>\n' + extract_braced(
            source, 'static bool writeCommonPrefsImage('),
        'STORE': extract_braced(source, 'class CommonPrefsFileStore'),
        'METHODS': methods,
        'DYNAMIC': extract_braced(header, 'class RepeatPrefs :'),
        'SET_DEBUG': extract_braced(setter, branch), 'SET_MASTER': '',
        'GET_DEBUG': extract_braced(getter, branch), 'GET_MASTER': '',
    }
    for key, value in replacements.items():
        code = code.replace('@' + key + '@', value)
    return code + TRACE_MAIN


class RepeaterTraceRepeatTest(unittest.TestCase):
    def test_actual_cli_schema_and_old_new_persistence(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        header = (ROOT / 'src/helpers/CommonCLI.h').read_text()
        code = production_harness(source, header)
        sanitizer_flags = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie']
        variants = [('ENABLE_OTA', platform) for platform in (
            'NRF52_PLATFORM', 'STM32_PLATFORM', 'ESP32_PLATFORM', 'RP2040_PLATFORM')]
        variants += [('ENABLE_OTA', 'ESP32_PLATFORM', 'WITH_MQTT_BRIDGE'),
                     ('NRF52_PLATFORM',), ('ESP32_PLATFORM', 'WITH_MQTT_BRIDGE')]
        with tempfile.TemporaryDirectory(prefix='repeater-trace-') as directory:
            work = Path(directory)
            transaction = (ROOT / 'src/helpers/ContactFileTransaction.h').read_text()
            (work / 'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            (work / 'test.cpp').write_text(code)
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

    def test_outer_load_defaults_before_loading_saved_image(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        load = extract_braced(source, 'void CommonCLI::loadPrefs(FILESYSTEM*')
        self.assertIn('_prefs->trace_when_repeat_off = 0;', load)
        self.assertLess(load.index('_prefs->trace_when_repeat_off = 0;'),
                        load.index('loadPrefsInt('))


if __name__ == '__main__':
    unittest.main()
