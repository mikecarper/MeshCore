#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <functional>
#include <vector>
#include <helpers/BleTxStallWatchdog.h>
#include <helpers/nrf52/BleRxFrameQueue.h>
#include <helpers/nrf52/SecuritySessionTimer.h>

static unsigned critical_depth;
struct Guard {
  Guard() { ++critical_depth; }
  ~Guard() { --critical_depth; }
};
static std::function<void()> during_copy;
static void* scheduled_copy(void* dest, const void* src, size_t length) {
  void* result = std::memcpy(dest, src, length);
  if (during_copy) {
    auto operation = std::move(during_copy);
    during_copy = nullptr;
    operation();
  }
  return result;
}
#define memcpy scheduled_copy
#include <helpers/CompanionFrameQueue.h>
#undef memcpy

#define MAX_FRAME_SIZE 176
#define FRAME_QUEUE_SIZE 12
#define BLE_CONN_HANDLE_INVALID 0xffff
#define BLE_RETRY_THROTTLE_MS 250
#define BLE_DEBUG_PRINTLN(...) assert(!critical_depth)
static std::function<void(uint32_t)> trace_hook;
static void trace_event(uint32_t event) {
  assert(!critical_depth);
  if (trace_hook) trace_hook(event);
}
#define meshBleTrace(event, ...) trace_event(event)
#define NRF_SUCCESS 0
#define NRF_ERROR_INVALID_STATE 8
#define BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION 0x13
static std::vector<uint16_t> disconnect_requests;
static uint32_t sd_ble_gap_disconnect(uint16_t handle, uint8_t) {
  assert(!critical_depth);
  disconnect_requests.push_back(handle);
  return NRF_SUCCESS;
}
static uint32_t now_ms = 100;
static uint32_t millis() { return now_ms; }
struct BLEConnection {
  uint16_t mtu = 23;
  bool connected() const { assert(!critical_depth); return true; }
  uint16_t getMtu() const { assert(!critical_depth); return mtu; }
};
static struct {
  BLEConnection connection;
  BLEConnection* Connection(uint16_t handle) {
    assert(!critical_depth);
    return handle == BLE_CONN_HANDLE_INVALID ? nullptr : &connection;
  }
  unsigned connected() const { assert(!critical_depth); return 1; }
} Bluefruit;

struct SerialBLEInterface {
  struct Frame { uint8_t len = 0; uint8_t buf[MAX_FRAME_SIZE] = {}; };
  using RxQueueGuard = Guard;
  using RxQueue = mesh_nrf52::BleRxFrameQueue<Frame, FRAME_QUEUE_SIZE, Guard>;
  RxQueue recv_queue;
  uint8_t send_queue_len = 0;
  Frame send_queue[FRAME_QUEUE_SIZE];
  std::atomic<bool> _tx_reset_pending{false};
  uint32_t _send_queue_generation = 0;
  uint16_t _tx_recovery_handle = BLE_CONN_HANDLE_INVALID;
  uint32_t _tx_recovery_generation = 0;
  uint16_t _conn_handle = 1;
  bool _isEnabled = true, _isDeviceConnected = true;
  unsigned long _last_retry_attempt = 0;
  mesh::BleTxStallWatchdog _tx_stall_watchdog;
  mesh::BleDisconnectRecovery _tx_disconnect_recovery;
  SecuritySessionTimer _companion_start_timer;
  SecuritySessionTimer _security_timer;
  bool _peer_address_valid = true;
  struct Uart {
    struct Chunk { uint16_t handle; std::vector<uint8_t> bytes; };
    std::vector<Chunk> chunks;
    std::function<void()> after_write;
    unsigned attempts = 0, fail_on_attempt = 0;
    bool notifyEnabled(uint16_t) const { assert(!critical_depth); return true; }
    void flush() { assert(!critical_depth); }
    size_t write(uint16_t handle, const uint8_t* data, size_t length) {
      assert(!critical_depth);
      if (++attempts == fail_on_attempt) return 0;
      chunks.push_back({handle, std::vector<uint8_t>(data, data + length)});
      if (after_write) {
        auto operation = std::move(after_write);
        after_write = nullptr;
        operation();
      }
      return length;
    }
  } bleuart;
  SerialBLEInterface() { open(1); }
  void open(uint16_t handle) {
    _conn_handle = handle;
    recv_queue.setEnabled(true);
    recv_queue.connect(handle);
    recv_queue.secure(handle);
    _isEnabled = _isDeviceConnected = true;
  }
  void reconnect(uint16_t handle, bool deferred_callback = true) {
    recv_queue.disconnect(_conn_handle);
    open(handle);
    if (deferred_callback) clearBuffers();
  }
  void serviceBondedOnlyTransition() {}
  bool advertisingAllowed() const { return false; }
  bool isAdvertising() const { return false; }
  bool startAdvertising(const char*) { assert(false); return false; }
  void serviceTxRecovery(uint32_t);
  void clearBuffers();
  void resetTxState();
  bool servicePendingTxReset();
  void shiftSendQueueLeft();
  size_t writeBleUartFrame(const Frame&);
  bool hasRequiredReplyCapacity() const;
  size_t writeFrame(const uint8_t*, size_t);
  size_t checkRecvFrame(uint8_t*);
  void recoverStalledTx(const char*, uint16_t = BLE_CONN_HANDLE_INVALID, uint32_t = 0);
  bool isConnected() const;
  bool isWriteBusy() const;
  bool hasPendingIO() const;
};
@METHODS@

static void enqueue_reset() {
  SerialBLEInterface port;
  const uint8_t old_reply[] = {1, 11}, interrupted[] = {2, 22}, fresh[] = {3, 33};
  assert(port.writeFrame(old_reply, sizeof(old_reply)) == sizeof(old_reply));
  port._last_retry_attempt = 77;
  port._tx_stall_watchdog.noteBlocked(100);
  during_copy = [&]() { port.clearBuffers(); };
  assert(port.writeFrame(interrupted, sizeof(interrupted)) == 0);
  assert(port.send_queue_len == 0 && !port._last_retry_attempt);
  assert(!port._tx_stall_watchdog.active() && !port._tx_disconnect_recovery.pending());
  assert(port.writeFrame(fresh, sizeof(fresh)) == sizeof(fresh));
  uint8_t received[MAX_FRAME_SIZE];
  assert(port.checkRecvFrame(received) == 0 && port.send_queue_len == 0);
  assert(port.bleuart.chunks.size() == 1 && port.bleuart.chunks[0].bytes[1] == 33);
}
static void callback_ownership() {
  SerialBLEInterface port;
  const uint8_t reply[] = {1, 11};
  for (unsigned i = 0; i < FRAME_QUEUE_SIZE; ++i)
    assert(port.writeFrame(reply, sizeof(reply)) == sizeof(reply));
  assert(port.isWriteBusy() && port.hasPendingIO());
  port.clearBuffers();
  // A callback invalidates acceptance immediately without mutating memory
  // that the interrupted main-task enqueue/copy may still be using.
  assert(port.send_queue_len == FRAME_QUEUE_SIZE && port._tx_reset_pending);
  assert(!port.isWriteBusy() && !port.hasPendingIO() && port.hasRequiredReplyCapacity());
  assert(port.servicePendingTxReset() && !port.servicePendingTxReset());
  assert(port.send_queue_len == 0);
}
static void connection_changes() {
  for (uint16_t replacement : {uint16_t(1), uint16_t(2)})
  for (bool deferred_callback : {false, true}) {
    SerialBLEInterface port;
    const size_t requests = disconnect_requests.size();
    uint8_t frame[40];
    for (unsigned i = 0; i < sizeof(frame); ++i) frame[i] = i;
    assert(port.writeFrame(frame, sizeof(frame)) == sizeof(frame));
    port.bleuart.after_write = [&]() { port.reconnect(replacement, deferred_callback); };
    uint8_t received[MAX_FRAME_SIZE];
    assert(port.checkRecvFrame(received) == 0);
    assert(port.bleuart.chunks.size() == 1);
    assert(port.bleuart.chunks[0].handle == 1 && port.bleuart.chunks[0].bytes.size() == 20);
    assert(port.send_queue_len == 0 && !port._tx_disconnect_recovery.pending());
    // Old partial completion must not recover/disconnect the new session.
    assert(disconnect_requests.size() == requests && port.isConnected());
    const uint8_t fresh[] = {1, 99};
    assert(port.writeFrame(fresh, sizeof(fresh)) == sizeof(fresh));
    assert(port.checkRecvFrame(received) == 0 && port.send_queue_len == 0);
    assert(port.bleuart.chunks.size() == 2);
    assert(port.bleuart.chunks[1].handle == replacement
        && port.bleuart.chunks[1].bytes == std::vector<uint8_t>(fresh, fresh + sizeof(fresh)));
  }
}
static void old_queue_and_recovery() {
  uint8_t received[MAX_FRAME_SIZE];
  const uint8_t reply[] = {1, 11};
  {
    SerialBLEInterface port;
    assert(port.writeFrame(reply, sizeof(reply)) == sizeof(reply));
    port.reconnect(2, false); // Physical event precedes its deferred callback.
    assert(port.checkRecvFrame(received) == 0 && port.bleuart.chunks.empty());
    assert(port.send_queue_len == 0 && !port._tx_disconnect_recovery.pending());
  }
  {
    SerialBLEInterface port;
    const size_t requests = disconnect_requests.size();
    uint8_t frame[40] = {1};
    assert(port.writeFrame(frame, sizeof(frame)) == sizeof(frame));
    port.bleuart.fail_on_attempt = 2;
    assert(port.checkRecvFrame(received) == 0);
    assert(port.bleuart.chunks.size() == 1 && port._tx_disconnect_recovery.pending());
    assert(port.send_queue_len == 0 && disconnect_requests.size() == requests+1);
    assert(disconnect_requests.back() == 1);
    assert(port.checkRecvFrame(received) == 0 && port._tx_disconnect_recovery.pending());
    assert(disconnect_requests.size() == requests+1); // Own recovery persists and throttles retries.
    now_ms += 1001;
    assert(port.checkRecvFrame(received) == 0 && port._tx_disconnect_recovery.pending());
    assert(disconnect_requests.size() == requests+2 && disconnect_requests.back() == 1);
    port.reconnect(2);
    assert(port.isConnected()); // Old recovery cannot poison a secured new link.
    assert(port.writeFrame(reply, sizeof(reply)) == sizeof(reply));
    assert(port.checkRecvFrame(received) == 0 && !port._tx_disconnect_recovery.pending());
  }
}
static void recovery_connection_changes() {
  for (uint32_t scheduling_point : {0xf031U, 0xf025U, 0xf020U})
  for (uint16_t replacement : {uint16_t(1), uint16_t(2)}) {
    SerialBLEInterface port;
    const size_t requests = disconnect_requests.size();
    uint8_t frame[40] = {1}, received[MAX_FRAME_SIZE];
    assert(port.writeFrame(frame, sizeof(frame)) == sizeof(frame));
    port.bleuart.fail_on_attempt = 2;
    bool changed = false;
    trace_hook = [&](uint32_t event) {
      if (!changed && event == scheduling_point) { changed = true; port.reconnect(replacement); }
    };
    assert(port.checkRecvFrame(received) == 0 && changed);
    trace_hook = nullptr;
    assert(disconnect_requests.size() == requests);
    assert(port.checkRecvFrame(received) == 0 && !port._tx_disconnect_recovery.pending());
    assert(port.isConnected() && disconnect_requests.size() == requests);
  }
  // A physical connect event may precede its deferred callback, even when
  // SoftDevice reuses a handle. A retry must still not disconnect that link.
  for (uint16_t replacement : {uint16_t(1), uint16_t(2)}) {
    SerialBLEInterface port;
    uint8_t frame[40] = {1}, received[MAX_FRAME_SIZE];
    assert(port.writeFrame(frame, sizeof(frame)) == sizeof(frame));
    port.bleuart.fail_on_attempt = 2;
    assert(port.checkRecvFrame(received) == 0 && port._tx_disconnect_recovery.pending());
    const size_t requests = disconnect_requests.size();
    port.reconnect(replacement, false);
    now_ms += 1001;
    assert(port.checkRecvFrame(received) == 0 && !port._tx_disconnect_recovery.pending());
    assert(disconnect_requests.size() == requests && port.isConnected());
  }
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!std::strcmp(argv[1], "enqueue")) enqueue_reset();
  else if (!std::strcmp(argv[1], "connection")) connection_changes();
  else {
    enqueue_reset(); callback_ownership(); connection_changes(); old_queue_and_recovery();
    recovery_connection_changes();
  }
  assert(!critical_depth && !during_copy);
}
