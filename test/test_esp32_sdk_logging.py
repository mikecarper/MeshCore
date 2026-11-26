"""Run the actual SDK sink, native-USB guard and Companion frame writer."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body
from test_serial_wifi_sessions import SANITIZER_FLAGS


ROOT = Path(__file__).resolve().parents[1]
ARDUINO = r'''
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>
inline uint32_t millis() { return 100; }
class Stream {
 public:
  virtual ~Stream() = default;
  virtual int available() { return 0; }
  virtual int read() { return -1; }
  virtual int peek() { return -1; }
  virtual void flush() {}
  virtual int availableForWrite() { return 0; }
  virtual size_t write(uint8_t value) { return write(&value, 1); }
  virtual size_t write(const uint8_t*, size_t) = 0;
};
'''

RUNTIME = r'''
#include <helpers/UsbLogging.h>
#include <helpers/NonBlockingWriteStream.h>
#include <helpers/ArduinoSerialInterface.h>
#include <atomic>
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <functional>
#include <string>
#include <thread>

static thread_local bool in_isr = false;
static bool xPortInIsrContext() { return in_isr; }
using TaskHandle_t = void*;
static thread_local TaskHandle_t current_task = reinterpret_cast<TaskHandle_t>(2);
static TaskHandle_t xTaskGetCurrentTaskHandle() { return current_task; }
static unsigned sdk_installs = 0, stdout_calls = 0;
static bool wireless_started = false;
static int emit_sdk(const char* format, ...);
struct SerialMock : Stream {
  int capacity = 4096;
  unsigned writes = 0, debug_changes = 0;
  bool debug = false;
  std::string output;
  std::function<void()> on_capacity, on_write;
  int availableForWrite() override {
    if (on_capacity) {
      const auto run = std::move(on_capacity);on_capacity = nullptr;run();
    }
    return capacity;
  }
  size_t write(const uint8_t* data, size_t size) override {
    assert(size <= static_cast<size_t>(capacity) && "SDK sink must never wait");
    if (on_write) {
      const auto run = std::move(on_write);on_write = nullptr;run();
    }
    ++writes;output.append(reinterpret_cast<const char*>(data), size);
    capacity -= static_cast<int>(size);
    return size;
  }
  size_t setRxBufferSize(size_t size) { return size; }
  size_t setTxBufferSize(size_t size) { capacity = static_cast<int>(size);return size; }
  void setTxTimeoutMs(unsigned value) { assert(value == 5); }
  void setDebugOutput(bool value) { ++debug_changes;debug = value; }
  void clear() { assert(!on_capacity && !on_write);capacity = 4096;writes = 0;output.clear(); }
} Serial;
using vprintf_like_t = int (*)(const char*, va_list);
static int sdk_stdout(const char* format, va_list args) {
  // The pinned SDK console has secondary USB_SERIAL_JTAG enabled. Its VFS
  // writer bypasses Arduino's setDebugOutput(false) and all protocol guards.
  char output[1024];
  const int length = vsnprintf(output, sizeof(output), format, args);
  assert(length >= 0 && length < static_cast<int>(sizeof(output)));
  ++stdout_calls;Serial.output.append(output, static_cast<size_t>(length));
  return length;
}
static vprintf_like_t sdk_sink = sdk_stdout;
static vprintf_like_t esp_log_set_vprintf(vprintf_like_t next) {
  assert(!wireless_started && "SDK sink must be installed before WiFi startup");
  ++sdk_installs;const auto previous = sdk_sink;sdk_sink = next;return previous;
}
static int emit_sdk(const char* format, ...) {
  va_list args;va_start(args, format);
  const int result = sdk_sink(format, args);
  va_end(args);return result;
}
static std::function<void()> during_format;
static unsigned formatter_calls = 0;
static int sdk_format(char* dest, size_t size, const char* format, va_list args) {
  ++formatter_calls;
  if (during_format) {
    const auto run = std::move(during_format);during_format = nullptr;run();
  }
  return vsnprintf(dest, size, format, args);
}

namespace mesh {
static bool usbDataLocked() { return false; }
static std::atomic<bool> usb_logging_enabled{false}, usb_debug_enabled{false};
static std::atomic<bool> usb_logging_preference_known{false};
static std::atomic<bool> usb_logging_tx_waiting{false};
@GATES@
static void clearUsbLoggingClientActivity() {}
#if MESH_ESP32_HWCDC_SESSION_GUARD
static void invalidateEsp32SdkLogging();
#endif
@PLATFORM_DEBUG@
@SETTERS@
#if MESH_ESP32_HWCDC_SESSION_GUARD
static std::atomic<bool> esp32_hwcdc_rx_queue_ready{false};
static std::atomic<uint32_t> esp32_hwcdc_access_generation{0};
static std::atomic<uint32_t> esp32_hwcdc_allowed_generation{0};
static std::atomic<bool> esp32_hwcdc_tx_kick_pending{false};
struct { void notePostCleanActivity() {} } esp32_hwcdc_self_reset_guard;
static void noteUsbLoggingTxAttempt() {}
static std::atomic<size_t> esp32_hwcdc_tx_buffer_capacity{0};
@ACCESS@
#define vsnprintf sdk_format
@BACKEND@
#undef vsnprintf
#endif
@CAPACITY@
@PREPARE@
}

static constexpr char late_probe[] =
    "E (2905571) wifi:timeout when WiFi un-init, type=4\r\n";
static void late_wifi_probe() {
  // Exact pinned WiFi logger shape: prefix, body and newline are three calls.
  emit_sdk("E (%u) wifi:", 2905571U);
  emit_sdk("%s, type=%d", "timeout when WiFi un-init", 4);
  emit_sdk("\r\n");
}

int main(int argc, char** argv) {
  assert(argc == 2);const std::string scenario = argv[1];
  current_task = reinterpret_cast<TaskHandle_t>(1);
  mesh::prepareUsbLoggingPort();
  mesh::setUsbDebugEnabled(true);
  mesh::setUsbLoggingEnabled(false);
  assert(!Serial.debug);
  wireless_started = true;
#if !MESH_ESP32_HWCDC_SESSION_GUARD
  assert(scenario == "scope" && sdk_installs == 0);
  late_wifi_probe();
  assert(stdout_calls == 3 && Serial.output == late_probe);
  std::puts("PASS: unrelated SDK logger is unchanged");
#else
#if !MESH_USB_LOGGING_AVAILABLE
    assert(scenario == "compiled-out");
    mesh::setUsbLoggingEnabled(true);late_wifi_probe();
    mesh::serviceEsp32SdkLogging();
    assert(Serial.output.empty() && !formatter_calls && !stdout_calls);
    std::puts("PASS: compiled-out SDK logs use no formatter or buffer");return 0;
#else
  if (scenario == "binary-late") {
    ArduinoSerialInterface companion;
    companion.begin(mesh::guarded_esp32_hwcdc_port);
    companion.enableFlowControl(true);companion.enable();
    const uint8_t reply[] = {0, 7, 9};
    const std::string framed(">\x03\x00\x00\x07\x09", 6);
    assert(companion.writeFrame(reply, sizeof(reply)) == sizeof(reply));
    assert(Serial.output == framed && !companion.hasPendingIO());
    // Reproduce the captured race: a complete valid response is followed by
    // an asynchronous WiFi timer log after its successful shutdown command.
    std::thread timer(late_wifi_probe);timer.join();
    mesh::serviceEsp32SdkLogging();
    assert(Serial.output == framed
           && "SDK diagnostics must not append to Binary Companion");
    assert(companion.writeFrame(reply, sizeof(reply)) == sizeof(reply));
    assert(Serial.output == framed + framed && !stdout_calls);
  } else {
    assert(sdk_installs == 1);
    // Setup retries do not replace a caller's selected sink repeatedly.
    mesh::installEsp32SdkLogging();assert(sdk_installs == 1);
    if (scenario == "gates") {
      mesh::setUsbDebugEnabled(false);mesh::setUsbLoggingEnabled(true);
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output.empty());
      mesh::setUsbDebugEnabled(true);mesh::setUsbLoggingEnabled(false);
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output.empty());
      mesh::setUsbLoggingEnabled(true);late_wifi_probe();
      assert(Serial.output.empty());mesh::serviceEsp32SdkLogging();
      assert(Serial.output == late_probe);Serial.clear();
      in_isr = true;late_wifi_probe();in_isr = false;
      mesh::serviceEsp32SdkLogging();
      assert(Serial.output.empty());
      mesh::esp32_hwcdc_access_generation.fetch_add(1);
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output.empty());
      mesh::esp32_hwcdc_allowed_generation.store(1);
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output == late_probe);
    } else if (scenario == "bounded") {
      mesh::setUsbLoggingEnabled(true);
      Serial.capacity = sizeof(late_probe) - 2;
      late_wifi_probe();mesh::serviceEsp32SdkLogging();
      assert(Serial.output.empty() && !Serial.writes);
      Serial.capacity = 4096;
      mesh::serviceEsp32SdkLogging();assert(Serial.output.empty());
      const std::string maximum = std::string(638, 'x') + '\n';
      const std::string oversized = std::string(639, 'y') + '\n';
      assert(emit_sdk("%s", maximum.c_str()) == 639);
      mesh::serviceEsp32SdkLogging();
      assert(Serial.output == maximum && Serial.writes == 1);Serial.clear();
      assert(emit_sdk("%s", oversized.c_str()) == 640);
      mesh::serviceEsp32SdkLogging();
      assert(Serial.output.empty() && !Serial.writes);
      emit_sdk("\n"); // Consume the overlong line through a known boundary.
      assert(emit_sdk("") == 0 && emit_sdk(nullptr) == 0);
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output == late_probe);
    } else if (scenario == "gate-during-capacity") {
      mesh::setUsbLoggingEnabled(true);
      Serial.on_capacity = [] { mesh::setUsbLoggingEnabled(false); };
      late_wifi_probe();mesh::serviceEsp32SdkLogging();
      assert(Serial.output.empty() && !Serial.writes);
      mesh::setUsbLoggingEnabled(true);late_wifi_probe();mesh::serviceEsp32SdkLogging();
      assert(Serial.output == late_probe);
    } else if (scenario == "reset-during-capacity") {
      mesh::setUsbLoggingEnabled(true);
      Serial.on_capacity = [] { mesh::esp32_hwcdc_access_generation.fetch_add(1); };
      late_wifi_probe();mesh::serviceEsp32SdkLogging();
      assert(Serial.output.empty() && !Serial.writes);
      mesh::esp32_hwcdc_allowed_generation.store(1);
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output == late_probe);
    } else if (scenario == "reentrant") {
      mesh::setUsbLoggingEnabled(true);
      during_format = [] { late_wifi_probe(); };
      late_wifi_probe();assert(Serial.output.empty());mesh::serviceEsp32SdkLogging();
      assert(Serial.output.empty());
      late_wifi_probe();mesh::serviceEsp32SdkLogging();
      assert(Serial.output == late_probe && Serial.writes == 1);Serial.clear();
      Serial.on_write = [] { late_wifi_probe(); };
      late_wifi_probe();mesh::serviceEsp32SdkLogging();
      assert(Serial.output == late_probe && Serial.writes == 1);
    } else if (scenario == "concurrent") {
      mesh::setUsbLoggingEnabled(true);
      std::atomic<bool> writing{false}, second_finished{false};
      during_format = [&] {
        writing.store(true);
        while (!second_finished.load()) std::this_thread::yield();
      };
      std::thread first(late_wifi_probe);
      std::thread second([&] {
        current_task = reinterpret_cast<TaskHandle_t>(3);
        while (!writing.load()) std::this_thread::yield();
        late_wifi_probe();second_finished.store(true);
      });
      first.join();second.join();
      assert(Serial.output.empty());mesh::serviceEsp32SdkLogging();
      assert(Serial.output.empty());
      late_wifi_probe();mesh::serviceEsp32SdkLogging();
      assert(Serial.output == late_probe && Serial.writes == 1);
    } else if (scenario == "owner") {
      mesh::setUsbLoggingEnabled(true);
      std::thread timer(late_wifi_probe);timer.join();
      std::thread other([] { mesh::serviceEsp32SdkLogging(); });other.join();
      assert(Serial.output.empty());mesh::serviceEsp32SdkLogging();
      assert(Serial.output == late_probe);
    } else if (scenario == "epochs") {
      mesh::setUsbLoggingEnabled(true);
      late_wifi_probe();mesh::setUsbLoggingEnabled(false);mesh::setUsbLoggingEnabled(true);
      mesh::serviceEsp32SdkLogging();assert(Serial.output.empty());
      late_wifi_probe();mesh::setUsbDebugEnabled(false);mesh::setUsbDebugEnabled(true);
      mesh::serviceEsp32SdkLogging();assert(Serial.output.empty());
      late_wifi_probe();mesh::esp32_hwcdc_access_generation.fetch_add(1);
      mesh::esp32_hwcdc_allowed_generation.store(1);
      mesh::serviceEsp32SdkLogging();assert(Serial.output.empty());
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output == late_probe);
    } else if (scenario == "format-epochs") {
      mesh::setUsbLoggingEnabled(true);
      during_format = [] { mesh::setUsbLoggingEnabled(false);mesh::setUsbLoggingEnabled(true); };
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output.empty());
      during_format = [] { mesh::esp32_hwcdc_access_generation.fetch_add(1);
                          mesh::esp32_hwcdc_allowed_generation.store(1); };
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output.empty());
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output == late_probe);
    } else if (scenario == "partial") {
      mesh::setUsbLoggingEnabled(true);
      emit_sdk("E (%u) wifi:", 2905571U);mesh::serviceEsp32SdkLogging();
      assert(Serial.output.empty());
      emit_sdk("timeout when WiFi un-init, type=%d", 4);mesh::serviceEsp32SdkLogging();
      assert(Serial.output.empty());
      emit_sdk("\r\n");mesh::serviceEsp32SdkLogging();
      assert(Serial.output == late_probe);
    } else if (scenario == "lost-chunk") {
      mesh::setUsbLoggingEnabled(true);
      emit_sdk("E (%u) wifi:", 2905571U);
      assert(!mesh::esp32_sdk_log_busy.test_and_set());
      emit_sdk("timeout when WiFi un-init, type=%d", 4); // Owner has the slot.
      mesh::esp32_sdk_log_busy.clear();
      emit_sdk("\r\n");mesh::serviceEsp32SdkLogging();assert(Serial.output.empty());
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output == late_probe);
    } else if (scenario == "stale-producer") {
      mesh::setUsbLoggingEnabled(true);
      during_format = [] { mesh::setUsbLoggingEnabled(false);mesh::setUsbLoggingEnabled(true); };
      emit_sdk("prefix without newline");
      // The original task never finishes its old line. A different producer
      // must recover at a newline rather than remain locked out forever.
      std::thread other([] { current_task = reinterpret_cast<TaskHandle_t>(3);
                            emit_sdk("\n");late_wifi_probe(); });other.join();
      mesh::serviceEsp32SdkLogging();assert(Serial.output == late_probe);
    } else if (scenario == "shared-writer") {
      mesh::setUsbLoggingEnabled(true);
      ArduinoSerialInterface companion;
      companion.begin(mesh::guarded_esp32_hwcdc_port);
      companion.enableFlowControl(true);companion.enable();
      const uint8_t reply[] = {0, 7, 9};
      const std::string framed(">\x03\x00\x00\x07\x09", 6);
      Serial.on_capacity = [&] {
        assert(companion.writeFrame(reply, sizeof(reply)) == sizeof(reply));
        assert(companion.hasPendingIO() && Serial.output.empty());
      };
      late_wifi_probe();mesh::serviceEsp32SdkLogging();assert(Serial.output == late_probe);
      mesh::setUsbLoggingEnabled(false);
      companion.loop();assert(!companion.hasPendingIO());
      assert(Serial.output == std::string(late_probe) + framed);
    } else assert(false);
    assert(!stdout_calls && !Serial.debug);
  }
  std::puts("PASS: SDK sink and actual USB/Companion writer");
#endif
#endif
}
'''


class Esp32SdkLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if cls.compiler is None:
            raise unittest.SkipTest('host C++17 compiler required')
        cls.temp = tempfile.TemporaryDirectory(prefix='meshcore-sdk-logging-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        (cls.folder / 'Arduino.h').write_text(ARDUINO, encoding='ascii')
        cls.source = (ROOT / 'src/helpers/UsbLogging.cpp').read_text()
        start = cls.source.index('class Esp32HwcdcSessionStream :')
        stop = cls.source.index('static void serviceEsp32HwcdcTxKickExclusive(', start)
        cls.parts = {
            'GATES': '\n'.join(body(cls.source, signature) for signature in (
                'bool isUsbLoggingEnabled()', 'bool isUsbDebugEnabled()')),
            'PLATFORM_DEBUG': body(cls.source, 'static void setPlatformDebugOutputEnabled('),
            'SETTERS': '\n'.join(body(cls.source, signature) for signature in (
                'void setUsbDebugEnabled(', 'void setUsbLoggingEnabled(')),
            'ACCESS': body(cls.source, 'static bool canAccessEsp32Hwcdc('),
            'BACKEND': cls.source[start:stop],
            'CAPACITY': body(cls.source, 'void setUsbCompanionTxBufferCapacity('),
            'PREPARE': body(cls.source, 'void prepareUsbLoggingPort()'),
        }
        cls.current = cls.compile('current')

    @classmethod
    def compile(cls, suffix, legacy=False, mode=1, cdc=1, logging=True):
        rendered = RUNTIME
        parts = dict(cls.parts)
        if legacy:
            original = '  installEsp32SdkLogging();\n'
            assert parts['PREPARE'].count(original) == 1
            parts['PREPARE'] = parts['PREPARE'].replace(original, '')
        for name, text in parts.items():
            rendered = rendered.replace('@' + name + '@', text)
        source = cls.folder / (suffix + '.cpp')
        source.write_text(rendered, encoding='ascii')
        binary = cls.folder / suffix
        result = subprocess.run([
            cls.compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-Wno-unused-variable', '-pthread',
            *SANITIZER_FLAGS, '-DARDUINO', '-DESP32', '-DESP32_PLATFORM',
            f'-DARDUINO_USB_MODE={mode}', f'-DARDUINO_USB_CDC_ON_BOOT={cdc}',
            '-DMESH_DEBUG=1', f'-I{cls.folder}', f'-I{ROOT / "src"}',
            *([] if logging else ['-DMESH_USB_LOGGING_DISABLED=1']),
            str(source), str(ROOT / 'src/helpers/ArduinoSerialInterface.cpp'),
            '-o', str(binary)], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return binary

    def test_actual_sink_preserves_binary_gates_bounds_sessions_and_concurrent_writers(self):
        for scenario in ('binary-late', 'gates', 'bounded', 'gate-during-capacity',
                         'reset-during-capacity', 'reentrant', 'concurrent', 'owner',
                         'epochs', 'format-epochs', 'partial', 'lost-chunk',
                         'stale-producer', 'shared-writer'):
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(self.current), scenario], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('PASS: SDK sink', result.stdout)

    def test_original_arduino_debug_hook_deterministically_corrupts_binary_tail(self):
        original = self.compile('original', legacy=True)
        result = subprocess.run([str(original), 'binary-late'], capture_output=True,
                                text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SDK diagnostics must not append to Binary Companion', result.stderr)
        self.assertNotIn('AddressSanitizer:', result.stderr)
        self.assertNotIn('runtime error:', result.stderr)

    def test_uart_and_tinyusb_console_routes_are_unchanged(self):
        for name, mode, cdc in (('uart', 1, 0), ('tinyusb', 0, 1)):
            with self.subTest(console=name):
                binary = self.compile(name, mode=mode, cdc=cdc)
                result = subprocess.run([str(binary), 'scope'], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('unrelated SDK logger is unchanged', result.stdout)

    def test_compiled_out_diagnostics_install_constant_drop_without_buffer(self):
        binary = self.compile('compiled-out', logging=False)
        result = subprocess.run([str(binary), 'compiled-out'], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no formatter or buffer', result.stdout)
        nm = shutil.which('nm')
        if nm:
            symbols = subprocess.run([nm, '-C', str(binary)], capture_output=True,
                                     text=True, check=True).stdout
            self.assertNotIn('esp32_sdk_log_record', symbols)

    def test_all_native_usb_roles_install_before_board_or_wifi_start(self):
        for role in ('companion_radio', 'simple_repeater', 'simple_room_server', 'simple_sensor'):
            with self.subTest(role=role):
                source = (ROOT / 'examples' / role / 'main.cpp').read_text()
                setup = source[source.index('void setup() {'):]
                self.assertLess(setup.index('mesh::prepareUsbLoggingPort();'),
                                setup.index('board.begin();'))
        self.assertLess(self.parts['PREPARE'].index('installEsp32SdkLogging();'),
                        self.parts['PREPARE'].index('Serial.setRxBufferSize('))
        service = body(self.source, 'void serviceUsbLoggingPort()')
        self.assertIn('serviceEsp32SdkLogging();', service)
        self.assertLess(service.index('serviceEsp32SdkLogging();'),
                        service.index('serviceEsp32HwcdcTxKick();'))


if __name__ == '__main__':
    unittest.main()
