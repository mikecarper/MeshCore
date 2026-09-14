#!/usr/bin/env python3
"""Execute the real nRF52 USB gates/callbacks with DTR-low and reopening hosts."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
USB = (ROOT / 'src/helpers/UsbLogging.cpp').read_text()


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


HARNESS = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <algorithm>
#define ENABLE_USB_INTERFACE 1
#define NRF52_PLATFORM 1
#define CFG_TUD_CDC 2
static bool mounted = true, dtr[2] = {false, false}, dfu = false;
static uint32_t now_ms = 100, rx_count = 0, tx_count = 0;
static unsigned rx_flushes = 0, tx_clears = 0, log_edges = 0;
static bool close_during_rx_sample = false;
static uint32_t millis() { return now_ms; }
static bool tud_mounted() { return mounted; }
static bool tud_cdc_n_connected(uint8_t n) { return mounted && dtr[n]; }
extern "C" void meshTinyUsbCdcLineStateChanged(uint8_t, bool, bool);
static uint32_t tud_cdc_n_available(uint8_t) {
  const auto count = rx_count;
  if (close_during_rx_sample) {
    close_during_rx_sample = false;
    meshTinyUsbCdcLineStateChanged(0, false, false);
  }
  return count;
}
static void tud_cdc_n_read_flush(uint8_t) { ++rx_flushes; rx_count = 0; }
static uint32_t tud_cdc_n_write_clear(uint8_t) { ++tx_clears; tx_count = 0; return 0; }
static uint32_t tud_cdc_n_write_available(uint8_t) { return 64 - tx_count; }
static uint32_t tud_cdc_n_write(uint8_t, const void*, uint32_t size) {
  const auto count = std::min(size, 64 - tx_count); tx_count += count; return count;
}
struct cdc_line_coding_t { uint32_t bit_rate; };
static uint32_t baud = 115200;
static void tud_cdc_get_line_coding(cdc_line_coding_t* coding) { coding->bit_rate = baud; }
static void TinyUSB_Port_EnterDFU() { dfu = true; }
namespace mesh {
@GATES@
static uint32_t primary_usb_terminal_taken_reset_generation = 0;
@WRITE@
@COMPLETE@
@CONNECTED@
static void handleDedicatedUsbLoggingLineState(bool) { ++log_edges; }
static void handleDedicatedUsbLoggingLineCoding() { ++log_edges; }
}
@CALLBACKS@
static void settle() {
  now_ms += 8;
  mesh::primary_usb_terminal_taken_reset_generation = mesh::primaryUsbSessionGeneration();
  mesh::completePrimaryUsbSessionReset(nullptr);
}
static void line(bool value, uint8_t instance = 0) {
  dtr[instance] = value;
  meshTinyUsbCdcLineStateChanged(instance, value, false);
}
int main() {
  uint8_t bytes[4] = {'>', 1, 0, 0};
  assert(!mesh::isUsbCompanionClientConnected());
  assert(mesh::writeTinyUsbCdcOnce(nullptr, bytes, 4) == 0);
  // Ordinary terminals retain their DTR behavior.
  line(true);
  assert(mesh::isUsbCompanionClientConnected());
  assert(mesh::writeTinyUsbCdcOnce(nullptr, bytes, 4) == 4);
  line(false);
  assert(!mesh::isUsbCompanionClientConnected());
  settle();
  assert(!mesh::isUsbCompanionClientConnected());
  // Stock MeshCLI holds DTR low. Its first real input opens only this epoch.
  rx_count = 4;
  assert(mesh::isUsbCompanionClientConnected());
  rx_count = 0;
  assert(mesh::isUsbCompanionClientConnected());
  assert(mesh::writeTinyUsbCdcOnce(nullptr, bytes, 4) == 4);
  // Low-DTR close, and input sent immediately on the next open.
  const auto old_generation = mesh::primaryUsbSessionGeneration();
  line(false);
  assert(mesh::primaryUsbSessionGeneration() != old_generation);
  rx_count = 4;
  assert(!mesh::isUsbCompanionClientConnected());
  settle();
  assert(rx_count == 4 && mesh::isUsbCompanionClientConnected());
  rx_count = 0;
  // A new handle's line coding also invalidates a DTR-low session.
  meshTinyUsbCdcLineCodingChanged(0);
  assert(!mesh::isUsbCompanionClientConnected());
  settle();
  assert(!mesh::isUsbCompanionClientConnected());
  // Closing after RX was sampled must not publish proof for the new epoch.
  rx_count = 4;
  close_during_rx_sample = true;
  assert(!mesh::isUsbCompanionClientConnected());
  settle();
  assert(!mesh::isUsbCompanionClientConnected());
  // A real new packet works after that race; DTR rising starts a clean owner.
  rx_count = 4;
  assert(mesh::isUsbCompanionClientConnected());
  line(true);
  assert(!mesh::isUsbCompanionClientConnected());
  settle();
  assert(mesh::isUsbCompanionClientConnected());
  line(false); settle();
  rx_count = 4;
  assert(mesh::isUsbCompanionClientConnected());
  rx_count = 0;
  // Physical loss revokes a low-DTR client even without a line-state callback.
  mounted = false;
  meshTinyUsbDeviceSessionBoundary();
  settle();
  assert(!mesh::isUsbCompanionClientConnected());
  mounted = true;
  meshTinyUsbDeviceSessionBoundary();
  assert(!mesh::isUsbCompanionClientConnected());
  rx_count = 4;
  assert(mesh::isUsbCompanionClientConnected());
  rx_count = 0;
  // CDC1 keeps the explicit logging-reader requirement and cannot reset CDC0.
  assert(mesh::writeTinyUsbCdcOnce(reinterpret_cast<void*>(1), bytes, 4) == 0);
#if MESH_DUAL_CDC_LOGGING
  const auto before_logging = mesh::primaryUsbSessionGeneration();
  line(true, 1);
  meshTinyUsbCdcLineCodingChanged(1);
  assert(log_edges >= 2 && mesh::primaryUsbSessionGeneration() == before_logging);
#endif
  // No write waits when the FIFO is full, including with DTR low.
  tx_count = 64;
  assert(mesh::writeTinyUsbCdcOnce(nullptr, bytes, 4) == 0);
  baud = 1200;
  line(false);
  assert(dfu);
}
'''


class Nrf52UsbClientTest(unittest.TestCase):
    def test_stop_token_reports_shared_logging_conflict(self):
        main = (ROOT / 'examples/companion_radio/main.cpp').read_text()
        stop = function(main, 'if (strcmp(usb_terminal_line, USB_TERMINAL_STOP_TOKEN) == 0)')
        source = r'''
#include <cassert>
#include <cstring>
#include <string>
#define MESH_USB_LOGGING_AVAILABLE 1
static bool dedicated = false, logging = false, binary = false;
static const char* USB_TERMINAL_STOP_TOKEN = "+++MESHCORE-TERM-STOP";
static char usb_terminal_line[32];
namespace mesh {
bool hasDedicatedUsbLoggingPort() { return dedicated; }
bool isUsbLoggingEnabled() { return logging; }
}
struct Output { std::string bytes; void print(const char* s) { bytes += s; } } output;
static Output& usbTerminalOutput() { return output; }
static void clearUsbTerminalLine() { usb_terminal_line[0] = 0; }
static void leaveUsbTerminalMode(bool acknowledge) { assert(acknowledge); binary = true; }
static void stop() { @STOP@ }
int main() {
  for (int mode = 0; mode != 3; ++mode) {
    logging = mode != 0; dedicated = mode == 2;
    binary = false; output.bytes.clear();
    strcpy(usb_terminal_line, USB_TERMINAL_STOP_TOKEN);
    stop();
    if (mode == 1) {
      assert(!binary && usb_terminal_line[0] == 0);
      assert(output.bytes.find("ERROR: set usb.logging off before Binary mode") != std::string::npos);
    } else {
      assert(binary && output.bytes.empty());
    }
  }
}
'''.replace('@STOP@', stop)
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / 'logging-mode.cpp'
            binary = Path(directory) / 'logging-mode'
            cpp.write_text(source)
            subprocess.run(['g++', '-std=c++17', str(cpp), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_default_terminal_is_quiet_until_ascii_input(self):
        mesh = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        header = (ROOT / 'examples/companion_radio/MyMesh.h').read_text()
        main = (ROOT / 'examples/companion_radio/main.cpp').read_text()
        source = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdarg>
#include <cstdio>
#include <string>
#define PUB_KEY_SIZE 32
#define FIRMWARE_VERSION "test-usb-client"
class Stream {
public:
  virtual int available() { return 0; }
  virtual int read() { return -1; }
  virtual int peek() { return -1; }
  virtual void flush() {}
  virtual size_t write(uint8_t value) { return write(&value, 1); }
  virtual size_t write(const uint8_t*, size_t) = 0;
  void print(const char* value) { write(reinterpret_cast<const uint8_t*>(value), strlen(value)); }
  void printf(const char* format, ...) {
    char value[512]; va_list args; va_start(args, format);
    vsnprintf(value, sizeof(value), format, args); va_end(args); print(value);
  }
};
class Capture : public Stream {
public:
  std::string bytes;
  size_t write(const uint8_t* data, size_t size) override {
    bytes.append(reinterpret_cast<const char*>(data), size); return size;
  }
} Serial;
namespace mesh {
@NULL@
static NullUsbLoggingStream null_usb_logging_stream;
Stream& usbCompanionPort() { return Serial; }
Stream& usbTerminalPort(bool enabled = true);
@PORT@
struct Utils {
  static void printHex(Stream& out, const uint8_t*, size_t) { out.print("00112233"); }
};
}
class MyMesh {
public:
  bool _terminal_mode = false, _terminal_usb_silent = false;
  Stream* _terminal_output = nullptr;
  struct { const char* node_name = "USB regression"; } _prefs;
  struct { uint8_t pub_key[PUB_KEY_SIZE] = {}; } self_id;
  void resetTerminalSession() {}
  @WAITING@
  Stream& terminalOutput();
  void printTerminalBanner(bool);
  void enterTerminalMode(bool show_banner = true);
  void exitTerminalMode();
};
@METHODS@
int main() {
  MyMesh radio;
  radio.enterTerminalMode(false);
  assert(radio.isTerminalWaitingForInput() && Serial.bytes.empty());
  radio.terminalOutput().print("ADVERT from -> another node\r\n> ");
  assert(Serial.bytes.empty());  // unsolicited radio events cannot corrupt an app probe
  radio.exitTerminalMode();
  const uint8_t reply[] = {'>', 1, 0, 5};
  mesh::usbCompanionPort().write(reply, sizeof(reply));
  assert(Serial.bytes == std::string(reinterpret_cast<const char*>(reply), sizeof(reply)));
  Serial.bytes.clear();
  radio.enterTerminalMode(false);
  radio.enterTerminalMode();  // first ASCII input reveals the ordinary terminal
  assert(!radio.isTerminalWaitingForInput());
  assert(Serial.bytes.find("Companion test-usb-client") != std::string::npos);
  assert(Serial.bytes.find("\r\n> ") != std::string::npos);
  radio.exitTerminalMode();
  assert(!radio._terminal_mode && radio._terminal_output == nullptr);
}
'''
        source = source.replace('@NULL@', function(USB, 'class NullUsbLoggingStream') + ';')
        source = source.replace('@PORT@', function(USB, 'Stream& usbTerminalPort('))
        source = source.replace('@WAITING@', function(header, 'bool isTerminalWaitingForInput('))
        source = source.replace('@METHODS@', '\n'.join(function(mesh, signature) for signature in (
            'Stream& MyMesh::terminalOutput(', 'void MyMesh::printTerminalBanner(',
            'void MyMesh::enterTerminalMode(', 'void MyMesh::exitTerminalMode(')))
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / 'quiet-terminal.cpp'
            cpp.write_text(source)
            for full in (False, True):
                with self.subTest(full=full):
                    binary = Path(directory) / ('quiet-terminal-' + str(full))
                    flags = ['-DCOMPANION_FEATURE_TEMP_RADIO=1'] if full else []
                    subprocess.run(['g++', '-std=c++17', *flags, str(cpp), '-o', str(binary)], check=True)
                    subprocess.run([str(binary)], check=True)
        terminal = function(main, 'static void serviceUsbTerminal()')
        self.assertLess(terminal.index('usb_binary_startup_probe.shouldStart('),
                        terminal.index('the_mesh.isTerminalWaitingForInput()'))
        self.assertIn('enterUsbTerminalMode(false);', function(main, 'static void serviceUsbAsciiSessionDefault('))

    def test_real_usb_callbacks_and_transport_with_both_client_styles(self):
        start = USB.index('static std::atomic<uint32_t> primary_usb_reset_generation')
        gates = USB[start:USB.index('\n#endif', start)]
        source = HARNESS.replace('@GATES@', gates)
        for marker, signature in (
            ('@WRITE@', 'static size_t writeTinyUsbCdcOnce('),
            ('@COMPLETE@', 'static void completePrimaryUsbSessionReset('),
            ('@CONNECTED@', 'bool isUsbCompanionClientConnected('),
        ):
            source = source.replace(marker, function(USB, signature))
        source = source.replace('@CALLBACKS@', '\n'.join(function(USB, 'extern "C" void ' + name + '(') for name in (
            'meshTinyUsbCdcLineStateChanged', 'meshTinyUsbDeviceSessionBoundary',
            'meshTinyUsbCdcLineCodingChanged')))
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / 'usb-client.cpp'
            cpp.write_text(source)
            for dual in (False, True):
                with self.subTest(dedicated_logging=dual):
                    binary = Path(directory) / ('usb-client-' + str(dual))
                    flags = ['-DMESH_DUAL_CDC_LOGGING=1'] if dual else []
                    subprocess.run(['g++', '-std=c++17', *flags, str(cpp), '-o', str(binary)], check=True)
                    subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
