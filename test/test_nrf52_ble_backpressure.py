#!/usr/bin/env python3
"""Execute production nRF52 BLE queue/dispatch methods with simulated UART IO.

This covers real admission, RX-dequeue, retry-throttle and recovery ordering,
not Bluefruit/SoftDevice callbacks or physical ATT delivery.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/nrf52/SerialBLEInterface.cpp"

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#include <helpers/CompanionFrameQueue.h>
#include <helpers/BleTxStallWatchdog.h>

#define MAX_FRAME_SIZE 176
#define FRAME_QUEUE_SIZE 12
#define BLE_RETRY_THROTTLE_MS 250
#define BLE_DEBUG_PRINTLN(...) do {} while (0)
#define meshBleTrace(...) do {} while (0)
static uint32_t current_millis = 100;
uint32_t millis() { return current_millis; }

struct SerialBLEInterface {
  struct Frame { uint8_t len = 0; uint8_t buf[MAX_FRAME_SIZE] = {}; };
  uint8_t send_queue_len = 0, recv_queue_len = 0;
  Frame send_queue[FRAME_QUEUE_SIZE], recv_queue[FRAME_QUEUE_SIZE];
  unsigned long _last_retry_attempt = 0;
  mesh::BleTxStallWatchdog _tx_stall_watchdog;
  mesh::BleDisconnectRecovery _tx_disconnect_recovery;
  bool connected = true, allow_write = false, partial_write = false;
  unsigned write_attempts = 0, recovery_services = 0, recoveries = 0;
  std::vector<std::vector<uint8_t>> transmitted;
  bool isConnected() const { return connected && !_tx_disconnect_recovery.pending(); }
  void serviceBondedOnlyTransition() {}
  void serviceTxRecovery(uint32_t) { ++recovery_services; }
  void recoverStalledTx(const char*) { ++recoveries; _tx_disconnect_recovery.begin(); }
  size_t writeBleUartFrame(const Frame& frame) {
    ++write_attempts;
    if (partial_write) return 1;
    if (!allow_write) return 0;
    transmitted.emplace_back(frame.buf, frame.buf + frame.len);
    return frame.len;
  }
  void receive(const uint8_t* bytes, size_t size) {
    assert(recv_queue_len < FRAME_QUEUE_SIZE && size <= MAX_FRAME_SIZE);
    recv_queue[recv_queue_len].len = size;
    memcpy(recv_queue[recv_queue_len++].buf, bytes, size);
  }
  void shiftSendQueueLeft();
  void shiftRecvQueueLeft();
  bool hasRequiredReplyCapacity() const;
  bool isWriteBusy() const;
  size_t writeFrame(const uint8_t*, size_t);
  size_t checkRecvFrame(uint8_t*);
};

@METHODS@

static void full_required_queue() {
  current_millis = 100;
  SerialBLEInterface link;
  for (uint8_t i = 0; i < FRAME_QUEUE_SIZE; ++i) {
    const uint8_t reply[] = {0x0d, i};
    assert(link.writeFrame(reply, sizeof(reply)) == sizeof(reply));
  }
  const uint8_t command[] = {66, 'A', '7', '|', 'v', 'e', 'r'};
  link.receive(command, sizeof(command));
  uint8_t received[MAX_FRAME_SIZE] = {};
  assert(link.checkRecvFrame(received) == 0);
  assert(link.recv_queue_len == 1 && link.send_queue_len == FRAME_QUEUE_SIZE);
  assert(link.write_attempts == 1 && link.transmitted.empty());
  ++current_millis;
  assert(link.checkRecvFrame(received) == 0);
  assert(link.write_attempts == 1); // Existing 250 ms retry throttle is retained.
  assert(link.recv_queue_len == 1 && link.isWriteBusy());

  current_millis = 351;
  link.allow_write = true;
  // A completed TX frees admission on this SAME call, before RX is examined.
  assert(link.checkRecvFrame(received) == sizeof(command));
  assert(memcmp(received, command, sizeof(command)) == 0);
  assert(link.recv_queue_len == 0 && link.send_queue_len == FRAME_QUEUE_SIZE - 1);
  const uint8_t reply[] = {29, 'A', '7', '|', 'O', 'K'};
  assert(link.writeFrame(reply, sizeof(reply)) == sizeof(reply));
  for (unsigned i = 0; i < FRAME_QUEUE_SIZE + 2; ++i) {
    ++current_millis;
    assert(link.checkRecvFrame(received) == 0);
  }
  assert(link.transmitted.size() == FRAME_QUEUE_SIZE + 1);
  for (uint8_t i = 0; i < FRAME_QUEUE_SIZE; ++i)
    assert((link.transmitted[i] == std::vector<uint8_t>{0x0d, i}));
  assert(link.transmitted.back() == std::vector<uint8_t>(reply, reply + sizeof(reply)));
  assert(link.send_queue_len == 0 && link.recv_queue_len == 0);
}

static void best_effort_eviction() {
  current_millis = 100;
  SerialBLEInterface link;
  for (uint8_t i = 0; i < FRAME_QUEUE_SIZE - 1; ++i) {
    const uint8_t log[] = {0x88, i};
    assert(link.writeFrame(log, sizeof(log)) == sizeof(log));
  }
  const uint8_t log[] = {0x88, 99};
  assert(link.writeFrame(log, sizeof(log)) == 0); // One required slot stays reserved.
  const uint8_t previous[] = {0x0d, 99};
  assert(link.writeFrame(previous, sizeof(previous)) == sizeof(previous));
  const uint8_t command[] = {66, 'B', '8', '|', 'v', 'e', 'r'};
  link.receive(command, sizeof(command));
  uint8_t received[MAX_FRAME_SIZE] = {};
  assert(link.isWriteBusy()); // Preserve the nRF52 >=8/12 producer high-water mark.
  assert(link.checkRecvFrame(received) == sizeof(command));
  assert(memcmp(received, command, sizeof(command)) == 0);
  const uint8_t reply[] = {29, 'B', '8', '|', 'O', 'K'};
  assert(link.writeFrame(reply, sizeof(reply)) == sizeof(reply));
  current_millis = 351;
  link.allow_write = true;
  for (unsigned i = 0; i < FRAME_QUEUE_SIZE + 2; ++i) {
    assert(link.checkRecvFrame(received) == 0);
    ++current_millis;
  }
  assert(link.transmitted.size() == FRAME_QUEUE_SIZE);
  assert(link.transmitted[0] == std::vector<uint8_t>(previous, previous + sizeof(previous)));
  assert(link.transmitted[1] == std::vector<uint8_t>(reply, reply + sizeof(reply)));
  for (uint8_t i = 0; i < FRAME_QUEUE_SIZE - 2; ++i)
    assert((link.transmitted[i + 2] == std::vector<uint8_t>{0x88, i}));
}

static void producer_threshold_and_recovery_ordering() {
  current_millis = 100;
  SerialBLEInterface link;
  const uint8_t reply[] = {0x0d, 1};
  for (unsigned i = 0; i < 7; ++i) assert(link.writeFrame(reply, sizeof(reply)) == sizeof(reply));
  assert(!link.isWriteBusy());
  assert(link.writeFrame(reply, sizeof(reply)) == sizeof(reply));
  assert(link.isWriteBusy());
  const uint8_t command[] = {66, 'v'};
  link.receive(command, sizeof(command));
  link._tx_disconnect_recovery.begin();
  uint8_t received[MAX_FRAME_SIZE] = {};
  assert(link.checkRecvFrame(received) == 0);
  assert(link.recovery_services == 1 && link.write_attempts == 0 && link.recv_queue_len == 1);

  SerialBLEInterface partial;
  partial.partial_write = true;
  assert(partial.writeFrame(reply, sizeof(reply)) == sizeof(reply));
  partial.receive(command, sizeof(command));
  assert(partial.checkRecvFrame(received) == 0);
  assert(partial.recoveries == 1 && partial.write_attempts == 1);
  assert(partial.recv_queue_len == 1); // Recovery is invoked instead of dispatch.
}

int main() {
  full_required_queue();
  best_effort_eviction();
  producer_threshold_and_recovery_ordering();
}
'''


class Nrf52BleBackpressureTests(unittest.TestCase):
    def test_real_queue_dispatch_throttle_and_recovery_under_sanitizers(self):
        source = SOURCE.read_text()
        signatures = ["void SerialBLEInterface::shiftSendQueueLeft()",
                      "void SerialBLEInterface::shiftRecvQueueLeft()",
                      "size_t SerialBLEInterface::writeFrame(",
                      "size_t SerialBLEInterface::checkRecvFrame(",
                      "bool SerialBLEInterface::isWriteBusy() const"]
        capacity = "bool SerialBLEInterface::hasRequiredReplyCapacity() const"
        if capacity in source:
            signatures.append(capacity)
        extracted = "\n".join(method(source, signature) for signature in signatures)
        sanitizers = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        with tempfile.TemporaryDirectory(prefix="meshcore-nrf-ble-backpressure-") as temp:
            binary = Path(temp) / "backpressure"
            built = subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-I" + str(ROOT / "src"),
                                    *sanitizers, "-o", str(binary)], input=HARNESS.replace("@METHODS@", extracted),
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_queue_capacity_remains_twelve(self):
        header = (ROOT / "src/helpers/nrf52/SerialBLEInterface.h").read_text()
        self.assertRegex(header, r"#define\s+FRAME_QUEUE_SIZE\s+12\b")


if __name__ == "__main__":
    unittest.main()
