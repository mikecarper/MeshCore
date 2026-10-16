"""Execute Pico W BLE queue, atomic notification, and recovery contracts."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/pico_ble_queues"
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
              if sys.platform.startswith("linux") else [])
SCENARIOS = ("normal", "admission", "priority", "mtu", "notify", "registration",
             "registration-sync-duplicate", "registration-error", "callback-clock",
             "deadline-progress", "best-effort", "locked", "stall-zero", "stall-wrap",
             "stall-cccd", "stall-register", "stall-notify", "stall-mtu",
             "disabled-reenable", "disabled-closed", "late-connect",
             "synchronous-disconnect", "cccd", "pending-cccd")


class PicoBleQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        helpers = (ROOT / "src/helpers/TxtDataHelpers.cpp").read_text()
        version = re.search(r"^#define FIRMWARE_VER_CODE\s+(\d+)", header, re.MULTILINE).group(1)
        producer = r'''
namespace StrHelper { void strzcpy(char*, const char*, size_t); }
@COPY@
#define CMD_DEVICE_QUERY 22
#define RESP_CODE_DEVICE_INFO 13
#define FIRMWARE_VER_CODE @VERSION@
#define MAX_CONTACTS 100
#define MAX_GROUP_CHANNELS 8
#define FIRMWARE_VERSION "host-regression"
#define FIRMWARE_BUILD_DATE "16 Oct 2026"
struct { const char* getManufacturerName() const { return "PicoW"; } } board;
struct ActualDeviceQuery {
  BaseSerialInterface* _serial = nullptr;
  uint8_t app_target_ver = 0;
  uint32_t _active_ble_pin = 123456;
  struct { bool isRepeatEn() const { return false; } uint8_t path_hash_mode = 2; } _prefs;
  void dispatch(uint8_t* cmd_frame, size_t len) {
    uint8_t out_frame[MAX_FRAME_SIZE]{};
    @QUERY@
  }
};
'''.replace("@COPY@", method(helpers, "void StrHelper::strzcpy("))
        producer = producer.replace("@VERSION@", version).replace(
            "@QUERY@", method(mesh, "if (cmd_frame[0] == CMD_DEVICE_QUERY && len >= 2)"))
        source = (ROOT / "src/helpers/rp2040/SerialBLEInterface.cpp").read_text()
        check = method(source, "size_t SerialBLEInterface::checkRecvFrame(")
        # Exact legacy admission behavior consumed RX without any TX capacity
        # check. Keep the rest of the implementation unchanged for its oracle.
        old_admission = check.replace("dest == nullptr || !isConnected() || recv_queue_len == 0\n"
                                      "      || !hasRequiredReplyCapacity()", "recv_queue_len == 0")
        if old_admission == check:
            raise AssertionError("production reply admission gate not found")
        blocked = method(source, "if (!sent && required)")
        old_mtu = source.replace(blocked, "(void)required; (void)sent;", 1)
        immediate_retry = source.replace(blocked, "if (!sent && required) { kickSend(); return; }", 1)
        # The throttle also prevents immediate recursion. Remove only that
        # throttle for this deliberate synchronous-rearm negative control.
        retry_guard = "  if (_tx_retry_pending\n      && !mesh::bleElapsedAtLeast((uint32_t)millis(), _tx_retry_started, 10)) return;\n"
        if source.count(retry_guard) != 1:
            raise AssertionError("production retry throttle not found")
        immediate_retry = immediate_retry.replace(retry_guard, "", 1)
        old_clock = source.replace("_tx_stall_watchdog.noteBlocked((uint32_t)millis())",
                                   "_tx_stall_watchdog.noteBlocked(now)", 1)
        if old_clock == source:
            raise AssertionError("production post-callback watchdog clock not found")
        variants = {"production": source, "legacy_admission": source.replace(check, old_admission, 1),
                    "legacy_mtu_drop": old_mtu, "immediate_retry": immediate_retry, "old_clock": old_clock}
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-pico-ble-")
        work = Path(cls.work.name)
        fixture = work / "test.cpp"
        fixture.write_text((FIXTURE / "test.cpp").read_text().replace("@QUERY_PRODUCER@", producer), encoding="ascii")
        cls.binaries = {}
        try:
            for variant, implementation in variants.items():
                cpp = work / (variant + ".cpp")
                cpp.write_text(implementation.replace('#include "SerialBLEInterface.h"',
                               '#include <helpers/rp2040/SerialBLEInterface.h>').replace(
                               '#include "../CompanionFrameQueue.h"', '#include <helpers/CompanionFrameQueue.h>'), encoding="ascii")
                binary = work / (variant + ".exe")
                result = subprocess.run([
                    compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                    *SANITIZERS, "-DPIO_FRAMEWORK_ARDUINO_ENABLE_BLUETOOTH=1",
                    f"-I{FIXTURE / 'mocks'}", f"-I{ROOT / 'src'}", str(fixture), str(cpp),
                    "-o", str(binary)], capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise AssertionError(result.stdout + result.stderr)
                cls.binaries[variant] = binary
        except BaseException:
            cls.work.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_delivery(self, variant, scenario):
        return subprocess.run([str(self.binaries[variant]), scenario], capture_output=True,
                              text=True, timeout=10)

    def test_actual_pico_transport_admission_notifications_and_recovery(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                result = self.run_delivery("production", scenario)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"PASS: Pico BLE delivery {scenario}", result.stdout)

    def test_old_admission_mtu_drop_and_synchronous_retry_controls(self):
        for variant, scenario, oracle in (
                ("legacy_admission", "admission", "a full required TX queue must retain the command until its reply fits"),
                ("legacy_mtu_drop", "mtu", "required DEVICE_QUERY response must survive an initial MTU of 23"),
                ("immediate_retry", "mtu", "blocked notification must defer instead of reentering its callback"),
                ("old_clock", "callback-clock", "a synchronous callback must not backdate the stall watchdog")):
            with self.subTest(variant=variant):
                result = self.run_delivery(variant, scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(oracle, result.stderr)
        # Unchanged healthy delivery is a positive control for every mutant.
        for variant in self.binaries:
            with self.subTest(positive=variant):
                result = self.run_delivery(variant, "normal")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pending_frames_keep_the_transport_awake_below_high_water(self):
        source = (ROOT / 'src/helpers/rp2040/SerialBLEInterface.cpp').read_text()
        methods = '\n'.join(method(source, 'bool SerialBLEInterface::' + name + '() const')
                            for name in ('isReadBusy', 'isWriteBusy', 'hasPendingIO'))
        code = r'''
#include <cassert>
#include <helpers/BleTxStallWatchdog.h>
#define FRAME_QUEUE_SIZE 8
static int locks = 0;
struct BluetoothLock {
  BluetoothLock() { ++locks; }
  ~BluetoothLock() { --locks; }
};
struct SerialBLEInterface {
  unsigned recv_queue_len = 0, send_queue_len = 0;
  bool _tx_pending = false;
  mesh::BleDisconnectRecovery _tx_disconnect_recovery;
  bool isReadBusy() const;
  bool isWriteBusy() const;
  bool hasPendingIO() const;
};
@METHODS@
int main() {
  SerialBLEInterface transport;
  for (unsigned rx = 0; rx <= FRAME_QUEUE_SIZE; ++rx) {
    for (unsigned tx = 0; tx <= FRAME_QUEUE_SIZE; ++tx) {
      for (unsigned pending = 0; pending <= 1; ++pending) {
        transport.recv_queue_len = rx;
        transport.send_queue_len = tx;
        transport._tx_pending = pending;
        assert(transport.isReadBusy() == (rx != 0));
        assert(transport.isWriteBusy() == (tx >= 5));
        assert(transport.hasPendingIO() == (rx || tx || pending));
        assert(locks == 0);
      }
    }
  }
  transport.recv_queue_len = transport.send_queue_len = 0;
  transport._tx_pending = false;
  transport._tx_disconnect_recovery.begin();
  assert(transport.isWriteBusy() && transport.hasPendingIO());
  assert(locks == 0);
}
'''.replace('@METHODS@', methods)
        for name in ('isReadBusy', 'isWriteBusy', 'hasPendingIO'):
            self.assertIn('BluetoothLock lock;', method(source, 'bool SerialBLEInterface::' + name))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'test.cpp').write_text(code)
            subprocess.run(['g++', '-std=c++17', '-fsanitize=address,undefined',
                            '-fno-pie', '-no-pie', '-I' + str(ROOT / 'src'),
                            str(path / 'test.cpp'), '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()
