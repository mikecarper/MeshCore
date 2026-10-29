"""Execute production scanner-idle admission and Dispatcher wake deadlines."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <helpers/AGCResetScheduler.h>
#include <helpers/RadioDirectionPolicy.h>
namespace radioDirection = mesh::radioDirection;
#define MESH_ENABLE_FOREIGN_RADIO 1
#define MESH_ENABLE_TX_BEACONS 1
struct Profiles { bool active = false; bool enabled() const { return active; } };
struct Radio {
  Profiles profile;
  bool receive_retry = false;
  uint32_t receive_delay = 0, recovery = 0;
  Profiles* profiles() { return &profile; }
  bool getReceiveRetryDelay(uint32_t, uint32_t& out) const {
    out = receive_delay; return receive_retry;
  }
  uint32_t getRecoveryCount() const { return recovery; }
  @DEFAULT@
};
struct Scanner : Radio {
  bool supported = false;
  uint32_t delay = 0;
  mutable unsigned reads = 0;
  bool getProfileIdleDelay(uint32_t& out) const override {
    ++reads; out = delay; return supported;
  }
};
struct Clock { uint32_t now = 1000; uint32_t getMillis() const { return now; } };
struct Manager {
  bool tx = false, rx = false;
  uint32_t tx_at = 0, rx_at = 0;
  bool getNextOutboundTime(uint32_t, uint32_t& out) const { out = tx_at; return tx; }
  bool getNextInboundTime(uint32_t, uint32_t& out) const { out = rx_at; return rx; }
};
struct Dispatcher {
  Radio* _radio;
  Clock* _ms;
  Manager* _mgr;
  bool radio_available = true, foreign = false, beacon = false;
  bool beacon_wake_enabled = false, adaptive = false;
  uint32_t beacon_wake_at = 0, next_tx_time = 0;
  void* outbound = nullptr;
  bool outbound_radio_retry_pending = false;
  uint32_t outbound_radio_retry_at = 0, outbound_restore_retry_at = 0;
  uint8_t outbound_restore_cr = 0;
  mesh::AGCResetScheduler agc_reset;
  Dispatcher(Radio* radio, Clock* clock, Manager* manager)
      : _radio(radio), _ms(clock), _mgr(manager) {}
  bool isDualRadioActive() const { return _radio->profiles()->enabled(); }
  bool hasForeignRadioFrame() const { return foreign; }
  bool hasBeaconRadioFrame() const { return beacon; }
  bool useAdaptiveAGC() const { return adaptive; }
  @ADMISSION@
  bool getNextQueueWakeDelay(uint32_t&) const;
};
@DEADLINES@
int main() {
  Radio unknown;
  uint32_t unchanged = 71;
  assert(!unknown.getProfileIdleDelay(unchanged) && unchanged == 71);
  Clock clock;
  Manager manager;
  Dispatcher legacy{&unknown, &clock, &manager};
  assert(legacy.canIdleDualRadio());
  assert(!legacy.getDualRadioIdleDelay(unchanged) && unchanged == 0);
  unknown.profile.active = true;
  assert(!legacy.canIdleDualRadio());
  assert(!legacy.getDualRadioIdleDelay(unchanged) && unchanged == 0);
  assert(legacy.getNextQueueWakeDelay(unchanged) && unchanged == 0);

  for (uint32_t now : {1000U, UINT32_MAX - 15U}) {
    clock.now = now;
    manager = {};
    Scanner radio;
    Dispatcher node{&radio, &clock, &manager};
    uint32_t delay = 99;
    assert(node.canIdleDualRadio() && radio.reads == 0);
    assert(!node.getDualRadioIdleDelay(delay) && delay == 0 && radio.reads == 0);
    assert(!node.getNextQueueWakeDelay(delay) && radio.reads == 0);
    radio.profile.active = true;
    radio.delay = 37;
    assert(!node.canIdleDualRadio()); // Unknown ports keep their polling contract.
    assert(!node.getDualRadioIdleDelay(delay) && delay == 0); // Ignore a false writer's output.
    assert(node.getNextQueueWakeDelay(delay) && delay == 0);
    radio.supported = true;
    radio.delay = 0;
    assert(!node.canIdleDualRadio()); // A due/too-short visit is not an idle budget.
    assert(!node.getDualRadioIdleDelay(delay) && delay == 0);
    assert(node.getNextQueueWakeDelay(delay) && delay == 0);
    radio.delay = 37;
    assert(node.canIdleDualRadio());
    assert(node.getDualRadioIdleDelay(delay) && delay == 37);
    assert(node.getNextQueueWakeDelay(delay) && delay == 37);
    node.radio_available = false;
    assert(!node.canIdleDualRadio());
    assert(!node.getDualRadioIdleDelay(delay) && delay == 0);
    assert(node.getNextQueueWakeDelay(delay) && delay == 0);
    node.radio_available = true;

    // Scanner admission cannot hide any previously actionable owner/deadline.
    node.foreign = true;
    assert(node.getNextQueueWakeDelay(delay) && delay == 0);
    node.foreign = false;
    node.beacon = true;
    assert(node.getNextQueueWakeDelay(delay) && delay == 0);
    node.beacon = false;
    node.beacon_wake_enabled = true;
    node.beacon_wake_at = now + 50;
    assert(node.getNextQueueWakeDelay(delay) && delay == 37);
    node.beacon_wake_at = now + 4;
    assert(node.getNextQueueWakeDelay(delay) && delay == 4);
    node.beacon_wake_at = now;
    assert(node.getNextQueueWakeDelay(delay) && delay == 0);
    node.beacon_wake_enabled = false;

    radio.receive_retry = true; radio.receive_delay = 7;
    assert(node.getNextQueueWakeDelay(delay) && delay == 7);
    radio.receive_delay = 50;
    assert(node.getNextQueueWakeDelay(delay) && delay == 37);
    radio.receive_retry = false;
    node.outbound_restore_cr = 5;
    node.outbound_restore_retry_at = now + 2;
    assert(node.getNextQueueWakeDelay(delay) && delay == 2);
    node.outbound_restore_cr = 0;

    manager.tx = true; manager.tx_at = now + 5;
    node.next_tx_time = now + 12;
    assert(node.getNextQueueWakeDelay(delay) && delay == 12);
    manager.tx_at = now + 50;
    assert(node.getNextQueueWakeDelay(delay) && delay == 37);
    manager.rx = true; manager.rx_at = now + 3;
    assert(node.getNextQueueWakeDelay(delay) && delay == 3);
    manager.rx_at = now;
    assert(node.getNextQueueWakeDelay(delay) && delay == 0);
    manager = {};

    node.adaptive = true;
    node.agc_reset.configure(now, 8, true, radio.recovery);
    assert(node.getNextQueueWakeDelay(delay) && delay == 8);
    node.agc_reset.configure(now, 80, true, radio.recovery);
    assert(node.getNextQueueWakeDelay(delay) && delay == 37);
    ++radio.recovery;
    assert(node.getNextQueueWakeDelay(delay) && delay == 0);
    node.adaptive = false;

    node.outbound = &node;
    assert(node.getNextQueueWakeDelay(delay) && delay == 0);
    node.outbound_radio_retry_pending = true;
    node.outbound_radio_retry_at = now + 200;
    assert(node.getNextQueueWakeDelay(delay) && delay == 37);
    radio.delay = 400;
    assert(node.getNextQueueWakeDelay(delay) && delay == 200);
    radio.receive_retry = true; radio.receive_delay = 6;
    assert(node.getNextQueueWakeDelay(delay) && delay == 6);
    radio.delay = 3;
    assert(node.getNextQueueWakeDelay(delay) && delay == 3);
    node.outbound_radio_retry_at = now;
    assert(node.getNextQueueWakeDelay(delay) && delay == 0);
  }
}
'''


class RadioProfileIdleDispatcherTest(unittest.TestCase):
    def test_actual_default_admission_and_all_dispatcher_deadlines(self):
        header = (ROOT / 'src/Dispatcher.h').read_text(encoding='ascii')
        source = (ROOT / 'src/Dispatcher.cpp').read_text(encoding='ascii')
        code = HARNESS.replace('@DEFAULT@', method(header, 'virtual bool getProfileIdleDelay('))
        code = code.replace('@ADMISSION@', method(header, 'bool canIdleDualRadio() const') + '\n'
                            + method(header, 'bool getDualRadioIdleDelay('))
        code = code.replace('@DEADLINES@', method(source, 'bool Dispatcher::getNextQueueWakeDelay('))
        with tempfile.TemporaryDirectory(prefix='meshcore-profile-idle-dispatcher-') as directory:
            cpp, binary = Path(directory) / 'test.cpp', Path(directory) / 'test'
            cpp.write_text(code, encoding='ascii')
            flags = ['-std=c++17', '-Wall', '-Wextra', '-Werror']
            if os.name != 'nt':
                flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                          '-fno-pie', '-no-pie']
            result = subprocess.run([
                os.environ.get('CXX', 'c++'), *flags, '-I', str(ROOT / 'src'),
                str(cpp), '-o', str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
