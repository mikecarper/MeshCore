#!/usr/bin/env python3
"""Run the production terminal parsers with their full RTC-backed input lines."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import test_companion_usb_input as usb
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / 'examples/companion_radio/main.cpp').read_text()
DEFINES = ('ESP32_PLATFORM=1', 'COMPANION_RADIO_FULL=1', 'HELTEC_WIRELESS_PAPER=1',
           'ENABLE_USB_INTERFACE=1', 'WIFI_SSID="test"', 'ENABLE_OTA=1',
           'CONFIG_IDF_TARGET_ESP32S3=1', 'CONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP=1',
           'COMPANION_RTC_TERMINAL_INPUT=1')
PREFIX = '\n'.join('#define ' + item.replace('=', ' ', 1) for item in DEFINES) + '\n'
PREFIX += '#include <helpers/CompanionRtcTerminalInput.h>\n'


def declaration(name):
    return re.search(r'^\s*MESH_COMPANION_TERMINAL_INPUT_ATTR static char ' + name
                     + r'\[[^\n]+;', MAIN, re.M).group().strip()


class CompanionRtcTerminalInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rtc-terminal-')
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        (self.work / 'sdkconfig.h').write_text('#pragma once\n')
        (self.work / 'esp_attr.h').write_text(
            '#pragma once\n#define RTC_FAST_ATTR __attribute__((section(".rtc.force_fast.test")))\n')

    def build(self, code, *, extra=(), compile_only=False):
        source = self.work / 'terminal.cpp'
        output = self.work / ('terminal.o' if compile_only else 'terminal')
        source.write_text(code)
        result = subprocess.run(['g++', '-std=c++17', '-I', str(self.work),
            '-I', str(ROOT / 'test/mocks'), '-I', str(ROOT / 'src'),
            *(['-c'] if compile_only else []), str(source), *map(str, extra), '-o', str(output)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return output

    def storage(self):
        return (declaration('usb_terminal_line') + '\n' + declaration('ota_console_line') + '\n'
            'static size_t usb_terminal_line_len=0,ota_console_len=0;\n'
            'static bool usb_terminal_discard_line=false,ota_console_discard_line=false;\n'
            + extract_braced(MAIN, 'static void clearUsbTerminalLine()') + '\n'
            + extract_braced(MAIN, 'static void ota_console_clear_line()') + '\n'
            + extract_braced(MAIN, 'static void resetCompanionRtcTerminalInput()') + '\n')

    def test_concrete_rtc_arrays_reset_before_transport_start_and_keep_both_full_sizes(self):
        code = PREFIX + '#include <cstring>\n#include <cstddef>\n#define MAX_TRANS_UNIT 255\n'
        code += self.storage() + r'''
static_assert(sizeof(usb_terminal_line)==542 && sizeof(ota_console_line)==542,
              "Both complete input capacities must remain unchanged");
int main() {
  memset(usb_terminal_line,'U',sizeof(usb_terminal_line));
  memset(ota_console_line,'T',sizeof(ota_console_line));
  usb_terminal_line_len=541; ota_console_len=541;
  usb_terminal_discard_line=ota_console_discard_line=true;
  resetCompanionRtcTerminalInput();
  for(char c:usb_terminal_line) if(c) return 1;
  for(char c:ota_console_line) if(c) return 2;
  return usb_terminal_line_len || ota_console_len
      || usb_terminal_discard_line || ota_console_discard_line;
}
'''
        binary = self.build(code)
        subprocess.run([str(binary)], check=True, timeout=10)
        objdump = shutil.which('objdump')
        self.assertIsNotNone(objdump, 'objdump is required to verify RTC section placement')
        sections = subprocess.check_output([objdump, '-h', str(binary)], text=True)
        section = re.search(r'\.rtc\.force_fast\.test\s+([0-9a-fA-F]+)', sections)
        self.assertIsNotNone(section, sections)
        self.assertTrue(1084 <= int(section[1], 16) <= 1090, sections)
        symbols = subprocess.check_output([objdump, '-t', str(binary)], text=True)
        for name in ('usb_terminal_line', 'ota_console_line'):
            self.assertRegex(symbols, r'\.rtc\.force_fast\.test\s+0*21e\s+[^\n]*' + name)
        setup = extract_braced(MAIN, 'void setup()')
        self.assertLess(setup.index('resetCompanionRtcTerminalInput();'), setup.index('Serial.begin('))
        self.assertLess(setup.index('resetCompanionRtcTerminalInput();'), setup.index('prepareUsbLoggingPort();'))

    def test_actual_usb_parser_and_protocol_transitions_with_rtc_input(self):
        signatures = ('static void clearUsbTerminalLine()',
            'static bool acceptUsbBinaryStartupFrame(uint32_t completed_at)',
            'static void enterUsbTerminalMode(bool show_banner',
            'static void leaveUsbTerminalMode(bool acknowledge)', 'static void resetUsbMotaMode()',
            'static void leaveUsbMotaMode(bool acknowledge)',
            'static bool enterUsbMotaMode(mesh::UsbMotaEntryOrigin origin)',
            'static void serviceUsbMota()', 'static bool serviceUsbMotaResponseBoundary()',
            'static void resetUsbTerminalHostSession()', 'static void serviceUsbTerminal()',
            'static void expireUsbBinaryStartupProbeBeforeDispatch()')
        functions = '\n'.join(extract_braced(MAIN, signature) for signature in signatures)
        functions = functions.replace('static void enterUsbTerminalMode(bool show_banner)',
                                      'static void enterUsbTerminalMode(bool show_banner = true)')
        accessor = extract_braced(usb.CONTEXT, 'static SerialMotaSource& serialFolderSource()')
        code = usb.HARNESS.replace('@FUNCTIONS@', functions).replace('@SOURCE_ACCESSOR@', accessor)
        code = code.replace('@TERMINAL_LINE_DECL@', declaration('usb_terminal_line'))
        code = PREFIX + usb.bind_production_input_constants(code)
        binary = self.build(code, extra=('-DESP32_PLATFORM=1', ROOT / 'src/helpers/ArduinoSerialInterface.cpp',
                                        ROOT / 'src/helpers/ota/MotaSourceSerial.cpp'))
        subprocess.run([str(binary)], check=True, timeout=20)

    def test_actual_tcp_parser_keeps_full_command_limit_and_recovers_after_overflow(self):
        code = PREFIX + r'''
#include <cassert>
#include <cstring>
#include <cstddef>
#include <deque>
#include <string>
#include <vector>
#define MAX_TRANS_UNIT 255
#define COMPANION_FEATURE_NETWORK_TERMINAL 1
#define COMPANION_FEATURE_READER 0
'''
        code += self.storage() + r'''
struct WiFiClient {
  std::deque<char> input; std::string output; bool live=true;
  operator bool() const { return live; }
  bool connected() const { return live; }
  int available() const { return input.size(); }
  int read() { char c=input.front();input.pop_front();return c; }
  void print(const char* text) { output+=text; }
  void stop() { live=false; }
  void push(const std::string& text) { for(char c:text)input.push_back(c); }
} ota_console_client;
struct Server { WiFiClient available() { WiFiClient c;c.live=false;return c; } } ota_console_server;
struct Mesh {
  std::vector<std::string> commands;
  bool isNetworkTerminalMode(WiFiClient&) { return true; }
  bool isTerminalMode() { return false; }
  bool enterNetworkTerminalMode(WiFiClient&) { return true; }
  void handleTerminalCommand(char* text) { commands.emplace_back(text); }
} the_mesh;
struct Probe { bool isActive() { return false; } } usb_binary_startup_probe;
struct Serial { unsigned getCompletedFrameCount() { return 0; } } usb_serial_interface;
struct Handoff { template<typename... T> bool begin(T...) { return true; }
  bool shouldRestoreAscii(unsigned) { return false; } void cancel() {} } ota_console_usb_handoff;
static bool hasObservableActiveUsbTerminalClient() { return false; }
static void enterUsbTerminalMode() {}
static void leaveUsbTerminalMode(bool) {}
static void ota_console_release_terminal() {}
static void serviceUsbLoggingOwnership(bool) {}
namespace mesh { bool isUsbLoggingEnabled() { return false; } }
'''
        code += extract_braced(MAIN, 'static void ota_console_loop()') + r'''
int main() {
  resetCompanionRtcTerminalInput();
  strcpy(usb_terminal_line,"unfinished USB command"); usb_terminal_line_len=22;
  const std::string maximum(sizeof(ota_console_line)-1,'x');
  ota_console_client.push(maximum+"\r\n"); ota_console_loop();
  assert(the_mesh.commands.size()==1 && the_mesh.commands[0]==maximum);
  assert(!strcmp(usb_terminal_line,"unfinished USB command") && usb_terminal_line_len==22);
  ota_console_client.push(std::string(sizeof(ota_console_line),'z')+"\nget name\n");
  ota_console_loop();
  assert(the_mesh.commands.size()==2 && the_mesh.commands[1]=="get name");
  assert(!ota_console_len && !ota_console_discard_line);
  assert(ota_console_client.output.find("ERROR: command too long")!=std::string::npos);
}
'''
        subprocess.run([str(self.build(code))], check=True, timeout=10)

    def test_unselected_has_no_sdk_dependency_and_unsupported_recipes_fail_closed(self):
        source = self.work / 'policy.cpp'
        source.write_text('#include <helpers/CompanionRtcTerminalInput.h>\n')
        default = subprocess.run(['g++', '-E', '-P', '-I', str(ROOT / 'src'), str(source)],
                                 capture_output=True, text=True, timeout=15)
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(default.stdout.strip(), '')
        cases = ([item for item in DEFINES if not item.startswith('HELTEC_WIRELESS_PAPER')],
                 [item for item in DEFINES if not item.startswith('COMPANION_RADIO_FULL')],
                 [item for item in DEFINES if not item.startswith('CONFIG_IDF_TARGET_')],
                 [item for item in DEFINES if not item.startswith('CONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP')],
                 [item for item in DEFINES if not item.startswith('ENABLE_USB_INTERFACE')],
                 [item for item in DEFINES if not item.startswith('WIFI_SSID')],
                 DEFINES + ('COMPANION_RTC_FAST_OFFLINE_FRAMES=32',),
                 DEFINES + ('COMPANION_RTC_SLOW_OFFLINE_FRAMES=32',),
                 [item if not item.startswith('COMPANION_RTC_TERMINAL_INPUT')
                  else 'COMPANION_RTC_TERMINAL_INPUT=2' for item in DEFINES])
        for defines in cases:
            with self.subTest(defines=defines):
                result = subprocess.run(['g++', '-fsyntax-only', '-I', str(self.work),
                    '-I', str(ROOT / 'src'), *['-D'+item for item in defines], str(source)],
                    capture_output=True, text=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)

    def test_only_wireless_paper_full_selects_rtc_input_without_capacity_changes(self):
        source = (ROOT / 'build_legacy.sh').read_text()
        self.assertEqual(source.count('-DCOMPANION_RTC_TERMINAL_INPUT=1'), 1)
        start = source.index('  # BLE + WiFi exhaust internal DRAM')
        start = source.index('  case "${env_name,,}" in', start)
        case = source[start:source.index('\n  esac', start)+len('\n  esac')]
        for target, selected, contacts, queue in (
                ('Heltec_wireless_paper_companion_radio_full', True, 350, 256),
                ('Xiao_C3_companion_radio_full', False, 100, 224),
                ('Heltec_v3_companion_radio_full', False, 100, 256)):
            with self.subTest(target=target):
                program = ('env_name='+target+'\nPLATFORMIO_BUILD_FLAGS=""\n'
                    'append_platformio_build_unflags() { :; }\nrecord_build_reduction() { :; }\n'
                    +case+'\nprintf "%s" "$PLATFORMIO_BUILD_FLAGS"\n')
                result = subprocess.run(['bash', '-c', program], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                flags = dict(re.findall(r'-D([A-Z0-9_]+)=(\d+)', result.stdout))
                self.assertEqual(flags.get('COMPANION_RTC_TERMINAL_INPUT')=='1', selected)
                self.assertEqual(int(flags['MAX_CONTACTS']), contacts)
                self.assertEqual(int(flags.get('OFFLINE_QUEUE_SIZE', 256)), queue)
                if target in ('Xiao_C3_companion_radio_full', 'Heltec_v3_companion_radio_full'):
                    self.assertEqual(flags.get('COMPANION_RTC_FAST_OFFLINE_FRAMES'), '32')
                else:
                    self.assertNotIn('COMPANION_RTC_FAST_OFFLINE_FRAMES', flags)
                if target == 'Heltec_v3_companion_radio_full':
                    self.assertEqual(flags.get('COMPANION_RTC_SLOW_OFFLINE_FRAMES'), '32')
                else:
                    self.assertNotIn('COMPANION_RTC_SLOW_OFFLINE_FRAMES', flags)


if __name__ == '__main__':
    unittest.main()
