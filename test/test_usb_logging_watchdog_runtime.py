"""Compile the actual watchdog runtime with isolated filesystem/USB boundaries."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'test' / 'fixtures' / 'usb_logging_watchdog'


class UsbLoggingWatchdogRuntimeTest(unittest.TestCase):
    def test_busy_snapshot_protects_commands_and_keeps_durable_policy_awake(self):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        callback = method((ROOT / 'examples/simple_sensor/main.cpp').read_text(),
                          'static bool usbLoggingRecoverySafe(void*)')
        harness = r'''
#include <Arduino.h>
#include <FS.h>
#include <helpers/UsbLogging.h>
#include <helpers/UsbLoggingWatchdog.cpp>
#include <cassert>
#include <cstring>

static mesh::UsbLoggingObservation observation{true, false, false, false, 0};
static bool pending_attach = false;
static bool logger_active = false;
namespace mesh {
bool isUsbLoggingEnabled() { return true; }
bool isUsbLoggingClientActive() { return logger_active; }
void clearUsbLoggingClientActivity() {}
bool isUsbLoggingTransportRecoveryPending() { return pending_attach; }
UsbLoggingObservation observeUsbLoggingTransport() { return observation; }
UsbLoggingRecoveryResult recoverUsbLoggingTransport(uint8_t) {
  return UsbLoggingRecoveryResult::Attempted;
}
void probeUsbLoggingTransport() {}
}
struct Board {
  bool isOTAUpdateRunning() const { return false; }
  bool isRadioTestActive() const { return false; }
} board;
struct Radio {
  bool isWatchdogObserving() const { return false; }
  bool isCalibratingNoiseFloor() const { return false; }
} radio_driver;
struct Mesh { bool canRecoverUsbLogging() const { return true; } } the_mesh;
static char command[32] = {};
@CALLBACK@

int main() {
  fs::FS fs;
  fs::stat_filesystem = &fs;
  mesh::loadUsbLoggingWatchdog(&fs);
  char reply[160];
  assert(mesh::handleUsbLoggingWatchdogCommand("set usb.watchdog on", reply, sizeof(reply)));
  assert(!std::strcmp(reply, "OK - USB watchdog on (saved)"));
  for (uint32_t now : {0U, 300000U, 360000U}) {
    mock_watchdog_millis = now;
    assert(!mesh::serviceUsbLoggingWatchdog(usbLoggingRecoverySafe));
  }
  assert(mesh::usbLoggingStatus().stage == 2);
  const auto writes = fs.write_opens;
  // Simulate a publisher preempted while the real status getter is called.
  // No test hook or substituted getter is used; all atomic words are real.
  mesh::status_event_guard.fetch_add(1);
  const auto busy = mesh::usbLoggingStatus();
  assert(busy.supported && busy.logging_enabled && busy.watchdog_enabled && !busy.watchdog_auto);
  assert(busy.reader_connected && !busy.stalled && busy.recovery_deferred && !busy.persistence_ready);
  assert(busy.backoff_step == 8 && busy.retry_seconds == 604800);
  assert(!busy.recovery_count && !busy.reboot_count && !busy.inactive_seconds);
  assert(!busy.auto_connected_seconds && !busy.last_event.sequence);
  assert(mesh::isUsbLoggingWatchdogArmed()); // Awake hint is not reset authority.
  std::strcpy(command, "stats-core");
  assert(!usbLoggingRecoverySafe(nullptr)); // Execute the actual sensor veto.
  mock_watchdog_millis = 3600000;
  assert(!mesh::serviceUsbLoggingWatchdog(usbLoggingRecoverySafe));
  assert(fs.write_opens == writes); // No new tier/journal/reset authorization.
  mesh::status_event_guard.fetch_add(1);
  assert(mesh::usbLoggingStatus().persistence_ready);
  assert(mesh::handleUsbLoggingWatchdogCommand("set usb.watchdog off", reply, sizeof(reply)));
  mesh::status_event_guard.fetch_add(1);
  assert(!mesh::isUsbLoggingWatchdogArmed());
  pending_attach = true;
  assert(mesh::isUsbLoggingWatchdogArmed()); // Finish USB reattach even in Off.
  mesh::status_event_guard.fetch_add(1);
  pending_attach = false;
  command[0] = 0;
  assert(mesh::handleUsbLoggingWatchdogCommand("set usb.watchdog auto", reply, sizeof(reply)));
  observation = {true, true, true, false, 1};
  logger_active = true;
  assert(!mesh::serviceUsbLoggingWatchdog(usbLoggingRecoverySafe));
  assert(mesh::isUsbLoggingWatchdogArmed()); // Auto has observed a logging client.
  observation = {true, false, false, false, 1};
  logger_active = false;
  for (uint32_t now : {3600001U, 3900001U, 3960001U}) {
    mock_watchdog_millis = now;
    assert(!mesh::serviceUsbLoggingWatchdog(usbLoggingRecoverySafe));
  }
  assert(mesh::usbLoggingStatus().stage == 2);
  assert(!mesh::isUsbLoggingWatchdogArmed()); // Auto completed USB-only attempts.
  mesh::status_event_guard.fetch_add(1);
  assert(mesh::usbLoggingStatus().watchdog_auto);
  assert(mesh::isUsbLoggingWatchdogArmed()); // Busy Auto publication stays awake.
  mesh::status_event_guard.fetch_add(1);
}
'''.replace('@CALLBACK@', callback)
        sanitizer_flags = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='usb-watchdog-snapshot-') as directory:
            source = Path(directory) / 'snapshot.cpp'
            source.write_text(harness, encoding='ascii')
            binary = Path(directory) / 'snapshot.exe'
            built = subprocess.run([
                compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pthread',
                *sanitizer_flags, '-DARDUINO', '-DESP32', '-DESP32_PLATFORM',
                '-DARDUINO_USB_MODE=0', '-DARDUINO_USB_CDC_ON_BOOT=1',
                '-I' + str(FIXTURE / 'mocks'), '-I' + str(ROOT / 'src'),
                str(source), '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stderr)

    def test_production_runtime_and_persistence(self):
        self.run_runtime()

    def test_old_cancelled_stall_clear_has_intended_semantic_failure(self):
        self.run_runtime(legacy=True)

    def run_runtime(self, legacy=False):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        sanitizer_flags = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='usb-watchdog-runtime-') as directory:
            source = ROOT / 'src' / 'helpers' / 'UsbLoggingWatchdog.cpp'
            for ota in (False, True):
                with self.subTest(ota_enabled=ota):
                    binary = Path(directory) / ('ota' + str(ota) + '.exe')
                    compile_source = source
                    if ota or legacy:
                        # Copy this translation unit byte-for-byte so quoted
                        # includes can use the isolated OTA fake or negative
                        # policy header. Production statements stay unchanged.
                        shadow = Path(directory) / 'helpers'
                        shadow.mkdir(exist_ok=True)
                        compile_source = shadow / 'UsbLoggingWatchdog.cpp'
                        compile_source.write_bytes(source.read_bytes())
                        if legacy:
                            header = (ROOT / 'src/helpers/UsbLoggingWatchdogPolicy.h').read_text(
                                encoding='ascii')
                            restart = '    _stage = 0;\n    _fault_since = _now;\n'
                            self.assertEqual(header.count(restart), 1)
                            (shadow / 'UsbLoggingWatchdogPolicy.h').write_text(
                                header.replace(restart, restart + '    _need_progress = false;\n', 1),
                                encoding='ascii')
                    built = subprocess.run([
                        compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pthread',
                        *sanitizer_flags, '-DARDUINO', '-DESP32', '-DESP32_PLATFORM',
                        '-DARDUINO_USB_MODE=0', '-DARDUINO_USB_CDC_ON_BOOT=1',
                        *(['-DENABLE_OTA'] if ota else []),
                        '-I' + directory,
                        '-I' + str(FIXTURE / 'mocks'),
                        '-I' + str(ROOT / 'src' / 'helpers'), '-I' + str(ROOT / 'src'),
                        str(compile_source), str(FIXTURE / 'test_usb_logging_watchdog_runtime.cpp'),
                        '-o', str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    if legacy:
                        self.assertNotEqual(tested.returncode, 0)
                        self.assertIn('cancelled purge must remain faulty without TX completion',
                                      tested.stderr)
                        self.assertNotIn('runtime error:', tested.stderr)
                        self.assertNotIn('AddressSanitizer:', tested.stderr)
                    else:
                        self.assertEqual(tested.returncode, 0, tested.stderr)
                        self.assertIn('USB watchdog runtime and persistence passed', tested.stdout)
                        self.assertIn('14 USB watchdog setter durability scenarios passed', tested.stdout)
                        self.assertIn(f'{8 if ota else 4} cancelled-stall recovery timelines passed',
                                      tested.stdout)

    def test_unsupported_transport_has_no_recovery_or_storage_work(self):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        with tempfile.TemporaryDirectory(prefix='usb-watchdog-unsupported-') as directory:
            harness = Path(directory) / 'unsupported.cpp'
            harness.write_text(r'''
#include <cassert>
#include <cstring>
#include <helpers/UsbLoggingWatchdog.h>
#include <helpers/UsbLoggingClientActivity.h>
namespace mesh { bool isUsbLoggingEnabled() { return true; } }
static bool safe(void*) { assert(false); return false; }
int main() {
  mesh::loadUsbLoggingWatchdog(nullptr, true);
  const auto status = mesh::usbLoggingStatus();
  assert(status.logging_enabled && !status.supported && !status.watchdog_auto);
  assert(!status.logger_active && !status.persistence_ready);
  assert(!mesh::serviceUsbLoggingWatchdog(safe));
  assert(!mesh::isUsbLoggingWatchdogArmed());
  assert(!mesh::isUsbLoggingWatchdogUpdateActive());
  mesh::noteUsbLoggingStatsCommand("stats-core");
  assert(!mesh::isUsbLoggingClientActive());
  mesh::clearUsbLoggingClientActivity();
  char reply[160] = {};
  for (const char* command : {"get usb.watchdog", "get usb.watchdog.last", "set usb.watchdog auto",
                             "set usb.watchdog on", "set usb.watchdog off"}) {
    assert(mesh::handleUsbLoggingWatchdogCommand(command, reply, sizeof(reply)));
    assert(strstr(reply, "unsupported"));
  }
  assert(!mesh::handleUsbLoggingWatchdogCommand("get usb.watchdogx", reply, sizeof(reply)));
  assert(!mesh::handleUsbLoggingWatchdogCommand("set usb.debug on", reply, sizeof(reply)));
  assert(!mesh::handleUsbLoggingWatchdogCommand(nullptr, reply, sizeof(reply)));
  assert(!mesh::handleUsbLoggingWatchdogCommand("get usb.watchdog", reply, 0));
}
''', encoding='ascii')
            binary = Path(directory) / 'unsupported.exe'
            built = subprocess.run([
                compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-DARDUINO', '-DESP32', '-DESP32_PLATFORM',
                '-I' + str(FIXTURE / 'mocks'),
                '-I' + str(ROOT / 'src' / 'helpers'), '-I' + str(ROOT / 'src'),
                str(ROOT / 'src' / 'helpers' / 'UsbLoggingWatchdog.cpp'),
                str(ROOT / 'src' / 'helpers' / 'UsbLoggingClientActivity.cpp'),
                str(harness), '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stderr)


if __name__ == '__main__':
    unittest.main()
