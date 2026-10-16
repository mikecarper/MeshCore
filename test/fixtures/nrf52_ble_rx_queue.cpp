#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>
#include <utility>
#include <vector>
#include <helpers/BleTxStallWatchdog.h>
#include <helpers/nrf52/SecuritySessionTimer.h>

static unsigned critical_depth = 0;
static std::deque<std::function<void()>> deferred;
static std::function<void()> during_copy;

// Model a single-core RTOS: a preemption requested during a critical section
// runs when the outermost guard exits. All UART/logging operations assert that
// interrupts and task switching are enabled.
static void preempt(std::function<void()> operation) {
  if (critical_depth) deferred.push_back(std::move(operation));
  else operation();
}

struct TestGuard {
  TestGuard() { ++critical_depth; }
  ~TestGuard() {
    assert(critical_depth);
    if (--critical_depth) return;
    while (!deferred.empty()) {
      auto operation = std::move(deferred.front());
      deferred.pop_front();
      operation();
    }
  }
};

static void* guarded_copy(void* dest, const void* source, size_t length) {
  assert(critical_depth);
  const size_t first = length / 2;
  std::memcpy(dest, source, first);
  if (during_copy) {
    auto operation = std::move(during_copy);
    during_copy = nullptr;
    preempt(std::move(operation));
  }
  std::memcpy(static_cast<uint8_t*>(dest) + first,
              static_cast<const uint8_t*>(source) + first, length - first);
  return dest;
}

#define memcpy guarded_copy
#include <helpers/nrf52/BleRxFrameQueue.h>
#undef memcpy

#define MAX_FRAME_SIZE 176
#define FRAME_QUEUE_SIZE 12
#define COMPANION_RADIO_FULL 1
#define COMPANION_FEATURE_BLE_MOTA_SOURCE 0
#define BLE_CONN_HANDLE_INVALID 0xffff
#define BLE_DEBUG_PRINTLN(...) do { assert(!critical_depth); } while (0)
#define meshBleTrace(...) do { assert(!critical_depth); } while (0)

enum {
  BLE_GAP_EVT_CONNECTED = 1, BLE_GAP_EVT_DISCONNECTED,
  BLE_GAP_EVT_CONN_SEC_UPDATE, BLE_GATTS_EVT_WRITE,
  BLE_GATTS_EVT_SYS_ATTR_MISSING, BLE_GAP_EVT_CONN_PARAM_UPDATE_REQUEST,
  BLE_GAP_ROLE_PERIPH = 1, BLE_UUID_TYPE_BLE = 1,
  BLE_UUID_DESCRIPTOR_CLIENT_CHAR_CONFIG = 0x2902,
  BLE_HCI_AUTHENTICATION_FAILURE = 5,
  BLE_HCI_STATUS_CODE_PIN_OR_KEY_MISSING = 6,
  BLE_HCI_CONN_TERMINATED_DUE_TO_MIC_FAILURE = 0x3d,
  BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION = 0x13,
  NRF_SUCCESS = 0
};
struct ble_gap_addr_t { uint8_t addr[6] = {}; };
struct ble_gap_evt_connected_t {
  uint8_t role = BLE_GAP_ROLE_PERIPH;
  ble_gap_addr_t peer_addr;
};
struct ble_gap_evt_disconnected_t { uint8_t reason = 0; };
struct ble_gatts_evt_write_t {
  struct { uint8_t type = 0; uint16_t uuid = 0; } uuid;
  uint16_t len = 0;
  uint8_t data[2] = {};
};
struct ble_evt_t {
  struct { uint16_t evt_id = 0; } header;
  struct {
    struct { uint16_t conn_handle = 7; } common_evt;
    struct {
      uint16_t conn_handle = 7;
      struct {
        ble_gap_evt_connected_t connected;
        ble_gap_evt_disconnected_t disconnected;
      } params;
    } gap_evt;
    struct {
      uint16_t conn_handle = 7;
      struct { ble_gatts_evt_write_t write; } params;
    } gatts_evt;
  } evt;
};
struct BLEConnection {
  bool connected() const { return true; }
  bool secured() const { return true; }
  bool bonded() const { return true; }
};
struct {
  BLEConnection connection;
  BLEConnection* Connection(uint16_t) { return &connection; }
} Bluefruit;
static bool cccd_written_this_connection = false;
static struct { std::atomic<bool> valid{false}; } cccd_ram_cache;
static void captureCccdDeferred(uint16_t, ble_gap_addr_t*) {}
template <typename... Args> static bool ada_callback(Args...) { return true; }
static uint32_t sd_ble_gap_disconnect(uint16_t, uint8_t) { return NRF_SUCCESS; }
static uint32_t sd_ble_gap_conn_param_update(uint16_t, const void*) { return NRF_SUCCESS; }
static uint32_t millis() { return 100; }

struct SerialBLEInterface {
  struct Frame {
    uint8_t len = 0;
    uint8_t buf[MAX_FRAME_SIZE] = {};
    Frame& operator=(const Frame& source) {
      assert(critical_depth);
      len = source.len;
      guarded_copy(buf, source.buf, sizeof(buf));
      return *this;
    }
  };
  using RxQueueGuard = TestGuard;
  using RxQueue = mesh_nrf52::BleRxFrameQueue<Frame, FRAME_QUEUE_SIZE, RxQueueGuard>;
  RxQueue recv_queue;
  uint8_t send_queue_len = 0;
  std::atomic<bool> _tx_reset_pending{false};
  uint16_t _tx_recovery_handle = BLE_CONN_HANDLE_INVALID;
  uint32_t _tx_recovery_generation = 0;
  unsigned long _last_retry_attempt = 0;
  mesh::BleTxStallWatchdog _tx_stall_watchdog;
  mesh::BleDisconnectRecovery _tx_disconnect_recovery;
  std::atomic<bool> _companionDataSeen{false};
  std::atomic<bool> _pairingRequestPending{false};
  SecuritySessionTimer _security_timer;
  SecuritySessionTimer _companion_start_timer;
  ble_gap_addr_t _peer_address;
  bool _peer_address_valid = false, _bond_removed_for_connection = false;
  bool _bonded_only = false, _isDeviceConnected = false;
  uint16_t _conn_handle = BLE_CONN_HANDLE_INVALID;
  unsigned removed_bonds = 0, bonded_recoveries = 0;
  bool connected = true;
  struct Uart {
    std::vector<uint8_t> bytes;
    std::function<void()> before_read, after_read, during_flush;
    unsigned reads = 0, flushes = 0;
    int available() const {
      assert(!critical_depth);
      return bytes.size();
    }
    int read(uint8_t* destination, size_t length) {
      assert(!critical_depth);
      ++reads;
      if (before_read) {
        auto operation = std::move(before_read);
        before_read = nullptr;
        operation();
      }
      length = std::min(length, bytes.size());
      if (length) std::memcpy(destination, bytes.data(), length);
      bytes.erase(bytes.begin(), bytes.begin() + length);
      if (after_read) {
        auto operation = std::move(after_read);
        after_read = nullptr;
        operation();
      }
      return length;
    }
    void flush() {
      assert(!critical_depth);
      ++flushes;
      bytes.clear();
      if (during_flush) {
        auto operation = std::move(during_flush);
        during_flush = nullptr;
        operation();
      }
    }
  } bleuart;
  void open(uint16_t handle = 7) {
    recv_queue.setEnabled(true);
    recv_queue.connect(handle);
    recv_queue.secure(handle);
  }
  bool isConnected() const { assert(!critical_depth); return connected; }
  bool isValidConnection(uint16_t handle) const { return handle == _conn_handle; }
  bool removeStoredBondForPeer(const char*) {
    assert(!critical_depth && _peer_address_valid);
    ++removed_bonds;
    _bond_removed_for_connection = true;
    return true;
  }
  void requestBondedOnlyRecovery(const char*) {
    assert(!critical_depth);
    ++bonded_recoveries;
  }
  void clearBuffers();
  void resetTxState();
  bool servicePendingTxReset();
  static void onBLEEvent(ble_evt_t*);
  static void onConnect(uint16_t);
  static void onDisconnect(uint16_t, uint8_t);
  static void onBleUartRX(uint16_t);
  bool isReadBusy() const;
  bool hasPendingIO() const;
};

static SerialBLEInterface* instance;
@METHODS@

using Queue = SerialBLEInterface::RxQueue;
using Result = Queue::PushResult;

static SerialBLEInterface::Frame frame(uint32_t id, uint8_t length = MAX_FRAME_SIZE) {
  SerialBLEInterface::Frame value;
  value.len = length;
  std::memset(value.buf, id % 251, sizeof(value.buf));
  std::memcpy(value.buf, &id, sizeof(id));
  return value;
}

static void expect_pop(Queue& queue, uint32_t id, uint8_t length = MAX_FRAME_SIZE) {
  uint8_t destination[MAX_FRAME_SIZE] = {};
  assert(queue.pop(destination) == length);
  uint32_t actual;
  std::memcpy(&actual, destination, sizeof(actual));
  assert(actual == id);
  for (unsigned i = sizeof(id); i < length; ++i) assert(destination[i] == id % 251);
}

static void ring_order_capacity_and_interleaved_copy() {
  SerialBLEInterface link;
  link.open();
  Queue& queue = link.recv_queue;
  uint32_t generation;
  assert(queue.snapshot(7, generation));
  for (unsigned i = 0; i < 12; ++i)
    assert(queue.push(7, generation, frame(i)) == Result::Accepted);
  assert(queue.push(7, generation, frame(12)) == Result::Full);
  assert(queue.size() == 12 && link.isReadBusy() && link.hasPendingIO());

  // Request producer execution halfway through the actual pop memcpy. Its
  // append must run after the copy/dequeue completes, preserving the head.
  during_copy = [&]() {
    assert(queue.size() == 11);
    assert(queue.push(7, generation, frame(12)) == Result::Accepted);
  };
  expect_pop(queue, 0);
  for (unsigned i = 1; i <= 12; ++i) expect_pop(queue, i);
  assert(!link.isReadBusy() && !link.hasPendingIO());
  link.send_queue_len = 1;
  assert(link.hasPendingIO());

  // Exercise both indices wrapping through many complete rings.
  for (uint32_t base = 100; base < 1300; base += 12) {
    for (unsigned offset = 0; offset < 12; ++offset)
      assert(queue.push(7, generation, frame(base + offset)) == Result::Accepted);
    for (unsigned offset = 0; offset < 12; ++offset) expect_pop(queue, base + offset);
  }

  assert(queue.push(7, generation, frame(99)) == Result::Accepted);
  during_copy = [&]() { link.clearBuffers(); };
  expect_pop(queue, 99);
  assert(queue.size() == 0 && !queue.current(7, generation));

  assert(queue.snapshot(7, generation));
  // Dequeue attempted during a producer copy cannot inspect an unpublished
  // slot. The custom Frame assignment checks that push guards its copy too.
  during_copy = [&]() { expect_pop(queue, 88); };
  assert(queue.push(7, generation, frame(88)) == Result::Accepted);
  assert(queue.size() == 0);
}

static void session_reset_and_stale_handles() {
  SerialBLEInterface link;
  Queue& queue = link.recv_queue;
  uint32_t generation;
  assert(!queue.snapshot(7, generation));
  queue.setEnabled(true);
  queue.connect(7);
  assert(!queue.snapshot(7, generation));
  queue.secure(8);
  assert(!queue.snapshot(7, generation));
  queue.secure(7);
  assert(queue.snapshot(7, generation));
  const uint32_t old = generation;
  assert(queue.push(7, old, frame(1)) == Result::Accepted);
  queue.reset([&]() {
    assert(!critical_depth && queue.size() == 0);
    assert(!queue.snapshot(7, generation));
    assert(queue.push(7, old, frame(2)) == Result::Stale);
    // Overlapping resets must keep RX suspended until BOTH flushes finish.
    queue.reset([&]() { assert(!queue.snapshot(7, generation)); });
    assert(!queue.snapshot(7, generation));
  });
  assert(queue.snapshot(7, generation) && generation != old);
  assert(queue.push(7, old, frame(2)) == Result::Stale);
  assert(queue.push(7, generation, frame(3)) == Result::Accepted);
  queue.disconnect(8); // A stale disconnect cannot reset the active handle.
  expect_pop(queue, 3);
  queue.disconnect(7);
  queue.connect(7); // The SoftDevice can reuse a connection handle.
  queue.secure(7);
  assert(queue.push(7, generation, frame(4)) == Result::Stale);
  assert(queue.snapshot(7, generation));
  queue.setEnabled(false);
  queue.setEnabled(true);
  queue.secure(7); // A delayed security callback must not revive that link.
  assert(!queue.snapshot(7, generation));
  queue.connect(7);
  queue.secure(7);
  assert(queue.snapshot(7, generation));
  assert(queue.push(7, generation, frame(5, 4)) == Result::Accepted);
  expect_pop(queue, 5, 4);

  // The BLE worker records connect before enqueuing/delegating the higher
  // level callbacks. A later enable cannot revive that earlier event.
  queue.setEnabled(false);
  queue.connect(7);
  queue.setEnabled(true);
  link.clearBuffers(); // Delayed onConnect clears buffers, not session ID.
  queue.secure(7);
  assert(!queue.snapshot(7, generation));
  queue.connect(7);
  queue.setEnabled(false);
  queue.setEnabled(true);
  link.clearBuffers();
  queue.secure(7);
  assert(!queue.snapshot(7, generation));
  queue.connect(7);
  queue.secure(7);
  assert(queue.snapshot(7, generation));
}

static void callback_read_reset_and_reconnect() {
  SerialBLEInterface link;
  instance = &link;
  link.open();
  link._companion_start_timer.start(100);
  link.bleuart.bytes = {1, 2, 3, 4};
  SerialBLEInterface::onBleUartRX(7);
  uint8_t destination[MAX_FRAME_SIZE] = {};
  assert(link.recv_queue.pop(destination) == 4 && destination[3] == 4);
  assert(link._companionDataSeen && !link._companion_start_timer.pending());

  // A reset after FIFO read but before queue commit invalidates the staged
  // frame. New-session bytes remain for their own callback.
  link.bleuart.bytes = {10, 11, 12};
  link.bleuart.after_read = [&]() {
    link.recv_queue.setEnabled(false);
    link.clearBuffers();
    link.open();
    link._companionDataSeen = false;
    link._companion_start_timer.start(200);
    link.bleuart.bytes = {20, 21};
  };
  SerialBLEInterface::onBleUartRX(7);
  assert(link.recv_queue.size() == 0 && !link._companionDataSeen);
  assert(link._companion_start_timer.pending() && link.bleuart.available() == 2);
  SerialBLEInterface::onBleUartRX(7);
  assert(link.recv_queue.pop(destination) == 2 && destination[0] == 20);

  link.bleuart.bytes = {30, 31};
  link.bleuart.before_read = [&]() { link.clearBuffers(); };
  SerialBLEInterface::onBleUartRX(7);
  assert(link.recv_queue.size() == 0); // Short FIFO reads never publish bytes.

  link.bleuart.bytes = {40, 41};
  const unsigned reads = link.bleuart.reads;
  SerialBLEInterface::onBleUartRX(8);
  assert(link.bleuart.reads == reads && link.bleuart.available() == 2);
  SerialBLEInterface::onBleUartRX(7);
  assert(link.recv_queue.pop(destination) == 2 && destination[0] == 40);

  link.bleuart.bytes.assign(MAX_FRAME_SIZE + 1, 0x55);
  SerialBLEInterface::onBleUartRX(7);
  assert(link.recv_queue.size() == 0 && !link.bleuart.available());

  for (unsigned i = 0; i < FRAME_QUEUE_SIZE; ++i) {
    link.bleuart.bytes = {static_cast<uint8_t>(i)};
    SerialBLEInterface::onBleUartRX(7);
  }
  link.bleuart.bytes = {99};
  SerialBLEInterface::onBleUartRX(7);
  assert(link.recv_queue.size() == FRAME_QUEUE_SIZE && !link.bleuart.available());
  for (unsigned i = 0; i < FRAME_QUEUE_SIZE; ++i)
    assert(link.recv_queue.pop(destination) == 1 && destination[0] == i);

  // FIFO flush can block/yield; none of its work may retain a task guard.
  link.bleuart.during_flush = [&]() {
    assert(!critical_depth);
    uint32_t token;
    assert(!link.recv_queue.snapshot(7, token));
    link.clearBuffers();
    assert(!link.recv_queue.snapshot(7, token));
  };
  link.clearBuffers();
  uint32_t token;
  assert(link.recv_queue.snapshot(7, token));
  assert(!critical_depth && deferred.empty());
}

static void production_connection_events_preserve_disconnect_cleanup() {
  for (uint8_t reason : {uint8_t(BLE_HCI_AUTHENTICATION_FAILURE),
                         uint8_t(BLE_HCI_STATUS_CODE_PIN_OR_KEY_MISSING),
                         uint8_t(BLE_HCI_CONN_TERMINATED_DUE_TO_MIC_FAILURE),
                         uint8_t(BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION)}) {
    SerialBLEInterface link;
    instance = &link;
    link.recv_queue.setEnabled(true);
    link._bonded_only = true;
    link._bond_removed_for_connection = true;
    cccd_written_this_connection = true;
    ble_evt_t event;
    event.header.evt_id = BLE_GAP_EVT_CONNECTED;
    event.evt.gap_evt.params.connected.peer_addr.addr[0] = 42;
    SerialBLEInterface::onBLEEvent(&event);
    assert(link._peer_address_valid && link._peer_address.addr[0] == 42);
    assert(!link._bond_removed_for_connection && !cccd_written_this_connection);
    SerialBLEInterface::onConnect(7);
    assert(link._conn_handle == 7 && link._security_timer.pending());
    link.recv_queue.secure(7);
    uint32_t generation;
    assert(link.recv_queue.snapshot(7, generation));
    assert(link.recv_queue.push(7, generation, frame(42)) == Result::Accepted);
    link._isDeviceConnected = true;
    link._companion_start_timer.start(100);
    link.send_queue_len = 3;
    cccd_written_this_connection = true;

    event.header.evt_id = BLE_GAP_EVT_DISCONNECTED;
    event.evt.gap_evt.params.disconnected.reason = reason;
    SerialBLEInterface::onBLEEvent(&event);
    assert(link.recv_queue.size() == 0);
    assert(!link.recv_queue.current(7, generation));
    assert(link.recv_queue.push(7, generation, frame(43)) == Result::Stale);
    assert(!link._peer_address_valid && !cccd_written_this_connection);
    const unsigned authentication_failure =
        reason != BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION;
    assert(link.removed_bonds == authentication_failure);
    assert(link.bonded_recoveries == authentication_failure);
    SerialBLEInterface::onDisconnect(7, reason);
    assert(link._conn_handle == BLE_CONN_HANDLE_INVALID && !link._isDeviceConnected);
    assert(!link._security_timer.pending() && !link._companion_start_timer.pending());
    assert(link._tx_reset_pending && link.send_queue_len == 3);
    assert(link.servicePendingTxReset()); // Application task applies the TX reset.
    assert(link.send_queue_len == 0 && link.recv_queue.size() == 0);
  }

  // Run the actual event and deferred connect methods across an enable
  // transition, so the test also verifies where session identity is assigned.
  SerialBLEInterface link;
  instance = &link;
  link.recv_queue.setEnabled(true);
  ble_evt_t event;
  event.header.evt_id = BLE_GAP_EVT_CONNECTED;
  SerialBLEInterface::onBLEEvent(&event);
  link.recv_queue.setEnabled(false);
  link.recv_queue.setEnabled(true);
  SerialBLEInterface::onConnect(7);
  link.recv_queue.secure(7);
  uint32_t generation;
  assert(!link.recv_queue.snapshot(7, generation));
  SerialBLEInterface::onBLEEvent(&event); // A new physical session can open.
  SerialBLEInterface::onConnect(7);
  link.recv_queue.secure(7);
  assert(link.recv_queue.snapshot(7, generation));
}

int main() {
  ring_order_capacity_and_interleaved_copy();
  session_reset_and_stale_handles();
  callback_read_reset_and_reconnect();
  production_connection_events_preserve_disconnect_cleanup();
}
