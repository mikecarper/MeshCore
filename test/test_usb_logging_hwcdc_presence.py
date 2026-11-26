"""Real HWCDC ISR, stats lease and watchdog runtime across idle/SOF gaps."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body
import test_hwcdc_tx_backport as backport
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'test/fixtures/usb_logging_watchdog'

HARNESS = r'''
#include <Arduino.h>
#include <FS.h>
#include <helpers/PersistentStoreFormat.h>
#include <helpers/UsbLogging.h>
#include <helpers/UsbLoggingClientActivity.h>
#include <helpers/UsbLoggingWatchdog.h>
#include <helpers/UsbLoggingWatchdogPolicy.h>
#include <helpers/UsbAsciiBinarySwitch.h>
#include <cstdio>
#include <cstdlib>
#include <string>
#define portENTER_CRITICAL(m) portENTER_CRITICAL_SAFE(m)
#define portEXIT_CRITICAL(m) portEXIT_CRITICAL_SAFE(m)
using esp_event_base_t = int;
static bool in_isr = false;
static bool xPortInIsrContext() { return in_isr; }
namespace mesh {
bool usbDataLocked() { return false; }
static bool logging_enabled = true;
static unsigned bool_samples = 0, probes = 0;
static std::vector<uint8_t> recoveries;
bool isUsbLoggingEnabled() { return logging_enabled; }
bool isUsbLoggingTransportRecoveryPending() { return false; }
void probeUsbLoggingTransport() { ++probes; } // Enqueue is not completion.
UsbLoggingRecoveryResult recoverUsbLoggingTransport(uint8_t stage) {
  recoveries.push_back(stage);
  return UsbLoggingRecoveryResult::Attempted;
}
static std::atomic<uint32_t> usb_logging_tx_progress{0};
static std::atomic<bool> usb_logging_tx_waiting{false};
static std::atomic<size_t> esp32_hwcdc_tx_buffer_capacity{4096};
static std::atomic<uint32_t> esp32_hwcdc_access_generation{0};
static std::atomic<uint32_t> esp32_hwcdc_bus_reset_generation{0};
static std::atomic<bool> esp32_hwcdc_startup_pending{false};
static std::atomic<bool> esp32_hwcdc_tx_kick_pending{false};
static std::atomic<bool> esp32_hwcdc_tx_primed{false};
static UsbSelfResetBurstGuard esp32_hwcdc_self_reset_guard;
static portMUX_TYPE esp32_hwcdc_session_mux;
static void noteUsbLoggingTxComplete() {
  ++usb_logging_tx_progress;
  usb_logging_tx_waiting.store(false);
}
struct SerialMock {
  bool isPlugged() { return HWCDC::isPlugged(); }
  explicit operator bool() { ++bool_samples; return HWCDC::isCDC_Connected(); }
  int availableForWrite() {
    unsigned waiting = 0;
    vRingbufferGetInfo(&ring, nullptr, nullptr, nullptr, nullptr, &waiting);
    return 4096 - waiting;
  }
  void setDebugOutput(bool) {}
} Serial;
@OBSERVATION@
@EVENT@
} // namespace mesh

static bool safe(void*) { return true; }
int main(int argc, char** argv) {
  assert(argc == 4);
  const std::string scenario = argv[1];
  const uint8_t mode = uint8_t(std::strtoul(argv[2], nullptr, 10));
  const uint32_t origin = uint32_t(std::strtoull(argv[3], nullptr, 10));
  reset();
  fs::FS fs;
  fs::stat_filesystem = &fs;
  std::vector<uint8_t> image(33, 0);
  image[0] = 'U'; image[1] = 'W'; image[2] = 2; image[4] = mode;
  mesh::storage::writeLE32(image.data() + 29,
      mesh::storage::updateCRC32(0xffffffffU, image.data(), 29) ^ 0xffffffffU);
  fs.files["/usb_wdg"] = image;
  mock_watchdog_millis = origin;
  mesh::loadUsbLoggingWatchdog(&fs);
  const auto run = [&](uint32_t elapsed) {
    mock_watchdog_millis = origin + elapsed;
    return mesh::serviceUsbLoggingWatchdog(safe);
  };

  // Before the first physical positive, neither startup nor an ISR sample
  // may manufacture host/reader presence or seed debounce state.
  assert(!mesh::observeUsbLoggingTransport().host_connected);
  plugged = true; in_isr = true;
  assert(!mesh::observeUsbLoggingTransport().host_connected);
  in_isr = false; plugged = false;
  assert(!mesh::observeUsbLoggingTransport().host_connected);
  plugged = true;
  intr_status = USB_SERIAL_JTAG_INTR_SERIAL_OUT_RECV_PKT;
  hw_cdc_isr_handler(nullptr); // The real pinned ISR establishes RX activity.
  assert(connected);
  mesh::noteUsbLoggingStatsCommand("stats-core");
  assert(!run(0) && !run(1));
  assert(mesh::usbLoggingStatus().logger_active);

  if (scenario == "idle-sof" || scenario == "idle-bool") {
    mock_watchdog_millis = origin + 1000;
    plugged = false;
    assert(!HWCDC::isCDC_Connected()); // Actual SDK heuristic loses connected.
    if (scenario == "idle-bool") plugged = true;
    assert(!run(1000));
    assert(mesh::isUsbLoggingClientActive()
           && "transient HWCDC observation must preserve the stats lease");
    assert(mesh::usbLoggingStatus().host_connected
           && mesh::usbLoggingStatus().reader_connected);
    if (scenario == "idle-sof") {
      assert(!run(1099)); // A real loss has not yet reached the 100 ms edge.
      plugged = true;
    }
    assert(!run(1100));
    assert(!run(600000));
    assert(mesh::recoveries.empty() && !fs.writes);
    assert(mesh::usbLoggingStatus().logger_active);
    assert(mesh::bool_samples == 0 && "HWCDC bool is not an open-reader signal");

    // Exact fifteen-minute expiry still requests USB-only repair five minutes
    // later. Missing stats on a healthy physical endpoint cannot reboot On.
    assert(!run(899999) && mesh::isUsbLoggingClientActive());
    assert(!run(900000) && !mesh::isUsbLoggingClientActive());
    assert(!run(1199999) && mesh::recoveries.empty());
    assert(!run(1200000) && mesh::recoveries == std::vector<uint8_t>{1});
    assert(!run(1260000) && mesh::recoveries == (std::vector<uint8_t>{1, 2}));
    assert(mesh::usbLoggingStatus().last_event.reasons
           == mesh::UsbLoggingWatchdogEvent::CLIENT_INACTIVE);
    assert(!run(3600000) && !mesh::usbLoggingStatus().reboot_count);
  } else if (scenario == "unplug") {
    plugged = false;
    assert(!run(1000) && mesh::isUsbLoggingClientActive());
    assert(!run(1099) && mesh::isUsbLoggingClientActive());
    assert(!run(1100) && !mesh::isUsbLoggingClientActive());
    assert(!mesh::usbLoggingStatus().host_connected
           && !mesh::usbLoggingStatus().reader_connected);
    assert(!run(301099) && mesh::recoveries.empty());
    assert(!run(301100) && mesh::recoveries == std::vector<uint8_t>{1});
    assert(!run(361100) && mesh::recoveries == (std::vector<uint8_t>{1, 2}));
    assert(run(3601100) == (mode == 1));
    assert(mesh::usbLoggingStatus().reboot_count == (mode == 1 ? 1U : 0U));
    plugged = true;
    assert(!run(3601101));
    assert(mesh::usbLoggingStatus().host_connected);
    assert(!mesh::isUsbLoggingClientActive()); // Replug never renews the lease.
  } else if (scenario == "bus-reset") {
    mock_watchdog_millis = origin + 1000;
    intr_status = USB_SERIAL_JTAG_INTR_BUS_RESET;
    hw_cdc_isr_handler(nullptr);
    assert(!connected);
    mesh::handleEsp32HwcdcEvent(nullptr, 0, ARDUINO_HW_CDC_BUS_RESET_EVENT, nullptr);
    assert(!mesh::isUsbLoggingClientActive()); // No debounce of real reset.
    assert(mesh::esp32_hwcdc_bus_reset_generation.load() == 1);
    assert(mesh::esp32_hwcdc_access_generation.load() == 1);
    assert(!mesh_hwcdc_tx_allowed);
  } else if (scenario == "stall") {
    ring.data.push_back({1, 2, 3});
    pulse(); // Actual ISR stages an unacknowledged FIFO; no later host ACK.
    assert(meshEsp32HwcdcTxPending());
    assert(!run(1000));
    assert(!run(31000) && mesh::usbLoggingStatus().stalled);
    assert(!run(301000) && mesh::recoveries == std::vector<uint8_t>{1});
    assert(!run(361000) && mesh::recoveries == (std::vector<uint8_t>{1, 2}));
    assert(run(3601000) == (mode == 1));
    assert(mesh::usbLoggingStatus().reboot_count == (mode == 1 ? 1U : 0U));
  } else assert(false);
  std::puts("PASS: HWCDC presence, real stats lease and runtime");
}
'''


class HwcdcPresenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if cls.compiler is None:
            raise unittest.SkipTest('host C++17 compiler required')
        cls.directory = tempfile.TemporaryDirectory(prefix='meshcore-hwcdc-presence-')
        cls.addClassCleanup(cls.directory.cleanup)
        cls.folder = Path(cls.directory.name)
        source = (ROOT / 'src/helpers/UsbLogging.cpp').read_text()
        cls.observation = body(source, 'UsbLoggingObservation observeUsbLoggingTransport()')
        cls.event = body(source, 'static void handleEsp32HwcdcEvent(')
        cls.current = cls.compile_backend(cls.observation, 'current')

    @classmethod
    def compile_backend(cls, observation, suffix):
        prefix = backport.BackportTest().harness().split('int main(){', 1)[0]
        source = cls.folder / (suffix + '.cpp')
        source.write_text(prefix + HARNESS.replace('@OBSERVATION@', observation)
                          .replace('@EVENT@', cls.event), encoding='ascii')
        runtime = cls.folder / 'UsbLoggingWatchdog.cpp'
        shutil.copyfile(ROOT / 'src/helpers/UsbLoggingWatchdog.cpp', runtime)
        binary = cls.folder / suffix
        result = subprocess.run([
            cls.compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-Wno-unused-parameter', '-Wno-sign-compare',
            '-pthread', *SANITIZER_FLAGS, '-DARDUINO', '-DESP32', '-DESP32_PLATFORM',
            '-DARDUINO_USB_MODE=1', '-DARDUINO_USB_CDC_ON_BOOT=1',
            '-DMESH_HWCDC_PINNED_TX_BACKPORT=1',
            f'-I{FIXTURE / "mocks"}', f'-I{ROOT / "src/helpers"}', f'-I{ROOT / "src"}',
            str(runtime), str(ROOT / 'src/helpers/UsbLoggingClientActivity.cpp'),
            str(source), '-o', str(binary)], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return binary

    def run_cases(self, scenarios, binary=None, negative=False):
        for mode in (1, 2):
            for origin in (0, 0xffffffff - 1050):
                for scenario in scenarios:
                    with self.subTest(mode=mode, origin=origin, scenario=scenario):
                        result = subprocess.run([str(binary or self.current), scenario,
                                                 str(mode), str(origin)], capture_output=True,
                                                text=True, timeout=10)
                        if negative:
                            self.assertNotEqual(result.returncode, 0)
                            self.assertIn('transient HWCDC observation must preserve the stats lease',
                                          result.stderr)
                            self.assertNotIn('AddressSanitizer:', result.stderr)
                            self.assertNotIn('runtime error:', result.stderr)
                        else:
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                            self.assertIn('PASS: HWCDC presence', result.stdout)

    def test_idle_transients_preserve_lease_but_real_expiry_repairs_only_usb(self):
        self.run_cases(('idle-sof', 'idle-bool'))

    def test_sustained_unplug_and_actual_bus_reset_invalidate_lease(self):
        self.run_cases(('unplug', 'bus-reset'))

    def test_actual_isr_unacknowledged_fifo_remains_a_physical_stall(self):
        self.run_cases(('stall',))

    def test_original_raw_hwcdc_presence_negative_control(self):
        start = self.observation.index('  // The SDK\'s five-millisecond SOF sample')
        end = self.observation.index('  const size_t capacity', start)
        original = self.observation[:start] + (
            '  result.host_connected = Serial.isPlugged();\n'
            '  result.reader_connected = result.host_connected && bool(Serial);\n'
        ) + self.observation[end:]
        binary = self.compile_backend(original, 'original')
        self.run_cases(('idle-sof', 'idle-bool'), binary, negative=True)


if __name__ == '__main__':
    unittest.main()
