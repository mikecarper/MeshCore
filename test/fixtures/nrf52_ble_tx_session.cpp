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

struct SerialBLEInterface;
static SerialBLEInterface* instance;
struct SerialBLEInterface {
  using Frame = mesh::CompanionTxFrame;
  using RxQueueGuard = Guard;
  using RxQueue = mesh_nrf52::BleRxFrameQueue<Frame, FRAME_QUEUE_SIZE, Guard>;
  RxQueue recv_queue;
  uint8_t send_queue_len = 0;
  Frame send_queue[FRAME_QUEUE_SIZE] = {};
  std::atomic<bool> _tx_reset_pending{false};
  std::atomic<bool> _companionDataSeen{true};
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
  struct MotaStream {
    bool active = false;
    void setActive(bool value) { active = value; }
  } _mota_stream;
  bool _peer_address_valid = true;
  struct Uart {
    struct Chunk { uint16_t handle; std::vector<uint8_t> bytes; };
    std::vector<Chunk> chunks;
    std::vector<uint8_t> rx_bytes;
    std::function<void()> after_write;
    std::function<void()> failed_write;
    bool reject_writes = false;
    unsigned attempts = 0, fail_on_attempt = 0, partial_on_attempt = 0;
    bool notifyEnabled(uint16_t) const { assert(!critical_depth); return true; }
    void flush() { assert(!critical_depth); rx_bytes.clear(); }
    int available() const { assert(!critical_depth); return rx_bytes.size(); }
    int read(uint8_t* destination, size_t length) {
      assert(!critical_depth && length <= rx_bytes.size());
      std::memcpy(destination, rx_bytes.data(), length);
      rx_bytes.erase(rx_bytes.begin(), rx_bytes.begin() + length);
      return length;
    }
    size_t write(uint16_t handle, const uint8_t* data, size_t length) {
      assert(!critical_depth);
      if (++attempts == fail_on_attempt || reject_writes) {
        if (failed_write) {
          auto operation = std::move(failed_write);
          failed_write = nullptr;
          operation();
        }
        return 0;
      }
      if (attempts == partial_on_attempt && length > 1) length = 1;
      chunks.push_back({handle, std::vector<uint8_t>(data, data + length)});
      if (after_write) {
        auto operation = std::move(after_write);
        after_write = nullptr;
        operation();
      }
      return length;
    }
  } bleuart;
  SerialBLEInterface() { instance = this; open(1); }
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
  static void onBleUartRX(uint16_t);
  void setMotaStreamActive(bool);
  void recoverStalledTx(const char*, uint16_t = BLE_CONN_HANDLE_INVALID, uint32_t = 0);
  bool isConnected() const;
  bool isWriteBusy() const;
  bool hasPendingIO() const;
};
@METHODS@

static void application_start() {
  for (bool required : {false, true}) {
    SerialBLEInterface port;
    const size_t requests = disconnect_requests.size();
    now_ms = 100;
    Bluefruit.connection.mtu = 23;
    port._companionDataSeen.store(false);
    port._companion_start_timer.start(now_ms);
    uint8_t frame[75] = {uint8_t(required ? 0x87 : 0x88)};
    uint8_t received[MAX_FRAME_SIZE] = {};
    port.bleuart.fail_on_attempt = 4; // Three default-MTU slots accept 60 bytes.
    assert(port.writeFrame(frame, sizeof(frame)) == sizeof(frame));
    for (uint32_t elapsed : {0U, 250U, 10001U}) {
      now_ms = 100 + elapsed;
      assert(port.checkRecvFrame(received) == 0);
      assert(port.bleuart.chunks.empty() && !port.bleuart.attempts);
      assert(port.send_queue_len == 1 && !port._last_retry_attempt);
      assert(!port._tx_stall_watchdog.active() && !port._tx_disconnect_recovery.pending());
      assert(port._companion_start_timer.pending());
      assert(disconnect_requests.size() == requests);
    }
    // Even a terminal's first newline opens the gate through the real RX
    // callback, independently of checkRecvFrame's application-task polling.
    Bluefruit.connection.mtu = 247;
    port.bleuart.rx_bytes = {'\n'};
    SerialBLEInterface::onBleUartRX(1);
    assert(port._companionDataSeen && !port._companion_start_timer.pending());
    assert(port.checkRecvFrame(received) == 1 && received[0] == '\n');
    assert(port.send_queue_len == 0 && port.bleuart.chunks.size() == 1);
    assert(port.bleuart.chunks[0].bytes == std::vector<uint8_t>(frame, frame + sizeof(frame)));
    assert(disconnect_requests.size() == requests);
  }
  // A Full Companion's explicit mOTA activation establishes readiness without
  // requiring a Nordic UART command, retaining the separate mOTA transport.
  SerialBLEInterface port;
  port._companionDataSeen.store(false);
  port._companion_start_timer.start(now_ms);
  const uint8_t frame[] = {0x83};
  uint8_t received[MAX_FRAME_SIZE] = {};
  assert(port.writeFrame(frame, sizeof(frame)) == sizeof(frame));
  assert(port.checkRecvFrame(received) == 0 && port.bleuart.chunks.empty());
  port.setMotaStreamActive(true);
  assert(port._mota_stream.active && port._companionDataSeen);
  assert(!port._companion_start_timer.pending());
  assert(port.checkRecvFrame(received) == 0 && port.send_queue_len == 0);
  assert(port.bleuart.chunks.size() == 1);
  Bluefruit.connection.mtu = 23;
}

static void radio2_failed_marker_reply() {
  const uint8_t radio2[] = {0x92, 8, uint8_t(-90), 0x32, 0x88};
  const std::vector<uint8_t> reply{0x0D, 0x42}, primary{0x88, 1, 2, 3}, next{0x88, 4, 5, 6};
  uint8_t received[MAX_FRAME_SIZE] = {};
  for (bool stalled : {false, true}) {
    SerialBLEInterface port;
    Bluefruit.connection.mtu = 247;
    const size_t disconnects = disconnect_requests.size();
    port.bleuart.fail_on_attempt = 1;
    port.bleuart.reject_writes = stalled;
    assert(port.writeFrame(radio2, sizeof(radio2)) == sizeof(radio2));
    port.bleuart.failed_write = [&]() {
      assert(port.send_queue[0].buf[0] == 0x93);
      assert(port.writeFrame(primary.data(), primary.size()) == primary.size());
      assert(port.writeFrame(next.data(), next.size()) == next.size());
      assert(port.writeFrame(reply.data(), reply.size()) == reply.size());
    };
    port.checkRecvFrame(received);
    assert(port.bleuart.chunks.empty()); // A definitive zero-byte marker failure.
    assert(port.send_queue_len == 3 && port.send_queue[0].buf[0] == reply[0]);
    if (stalled) {
      for (unsigned i = 0; i < 50 && !port._tx_disconnect_recovery.pending(); ++i) {
        now_ms += 251; port.checkRecvFrame(received);
      }
      assert(port._tx_disconnect_recovery.pending() && port.send_queue_len == 0);
      assert(port.bleuart.chunks.empty() && disconnect_requests.size() == disconnects + 1);
    } else {
      for (unsigned i = 0; i < 8 && port.send_queue_len; ++i) {
        now_ms += 251; port.checkRecvFrame(received);
      }
      assert(port.send_queue_len == 0 && port.bleuart.chunks.size() == 3);
      assert(port.bleuart.chunks[0].bytes == reply);
      assert(port.bleuart.chunks[1].bytes == primary && port.bleuart.chunks[2].bytes == next);
      assert(disconnect_requests.size() == disconnects);
    }
  }
  // Without delivery-required traffic, retain the normal best-effort retry.
  SerialBLEInterface port;
  Bluefruit.connection.mtu = 247;
  port.bleuart.fail_on_attempt = 1;
  assert(port.writeFrame(radio2, sizeof(radio2)) == sizeof(radio2));
  port.bleuart.failed_write = [&]() { assert(port.writeFrame(primary.data(), primary.size()) == primary.size()); };
  port.checkRecvFrame(received);
  assert(port.send_queue_len == 2 && port.send_queue[0].buf[0] == 0x92);
  for (unsigned i = 0; i < 8 && port.send_queue_len; ++i) {
    now_ms += 251; port.checkRecvFrame(received);
  }
  assert(port.bleuart.chunks.size() == 3);
  assert(port.bleuart.chunks[0].bytes == std::vector<uint8_t>({0x92, 0xA1}));
  assert(port.bleuart.chunks[1].bytes == std::vector<uint8_t>({0x88, 8, uint8_t(-90), 0x32, 0x88}));
  assert(port.bleuart.chunks[2].bytes == primary);
  Bluefruit.connection.mtu = 23;
}

static void previous_prestart_partial() {
  SerialBLEInterface port;
  const size_t requests = disconnect_requests.size();
  Bluefruit.connection.mtu = 23;
  port._companionDataSeen.store(false);
  uint8_t frame[75] = {0x88}, received[MAX_FRAME_SIZE] = {};
  port.bleuart.fail_on_attempt = 4;
  assert(port.writeFrame(frame, sizeof(frame)) == sizeof(frame));
  assert(port.checkRecvFrame(received) == 0);
  assert(port.bleuart.chunks.size() == 3);
  size_t accepted = 0;
  for (const auto& chunk : port.bleuart.chunks) accepted += chunk.bytes.size();
  assert(accepted == 60 && port._tx_disconnect_recovery.pending());
  assert(disconnect_requests.size() == requests + 1);
}

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

static void radio2_log_groups() {
  const std::vector<uint8_t> marker{0x92, 0xA1};
  const uint8_t primary[] = {0x88, 8, uint8_t(-90), 0x32, 0x88};
  const uint8_t reply[] = {0x0D, 0x42};
  uint8_t received[MAX_FRAME_SIZE] = {};
  std::vector<uint8_t> radio2(MAX_FRAME_SIZE);
  for (size_t i = 0; i < radio2.size(); ++i) radio2[i] = uint8_t(i);
  radio2[0] = 0x92; radio2[1] = 12; radio2[2] = uint8_t(-100);
  auto legacy = radio2; legacy[0] = 0x88;
  {
    SerialBLEInterface port;
    Bluefruit.connection.mtu = 247;
    assert(port.writeFrame(radio2.data(), radio2.size()) == radio2.size());
    const auto logical = port.send_queue[0];
    port.bleuart.fail_on_attempt = 1;
    assert(port.writeBleUartFrame(logical) == 0 && port.bleuart.chunks.empty());
    assert(port.writeBleUartFrame(logical) == logical.len);
    assert(port.bleuart.chunks.size() == 1 && port.bleuart.chunks[0].bytes == marker);
    assert(logical.len == MAX_FRAME_SIZE && logical.buf[0] == 0x92);
  }
  {
    SerialBLEInterface port;
    Bluefruit.connection.mtu = 247;
    port.bleuart.partial_on_attempt = 1;
    assert(port.writeFrame(radio2.data(), radio2.size()) == radio2.size());
    port.checkRecvFrame(received);
    assert(port.bleuart.chunks.size() == 1);
    assert(port.bleuart.chunks[0].bytes == std::vector<uint8_t>{0x92});
    assert(port._tx_disconnect_recovery.pending() && port.send_queue_len == 0);
    now_ms += 251; port.checkRecvFrame(received);
    assert(port.bleuart.chunks.size() == 1); // Never follow a partial marker with a raw frame.
  }
  {
    SerialBLEInterface port;
    Bluefruit.connection.mtu = 23;
    assert(port.writeFrame(radio2.data(), radio2.size()) == radio2.size());
    port.checkRecvFrame(received);
    assert(port.bleuart.chunks.empty()); // Do not emit a marker before the raw frame fits.
    assert(!port._tx_disconnect_recovery.pending());
    Bluefruit.connection.mtu = 247;
    for (unsigned i = 0; i < 8 && port.bleuart.chunks.size() < 2; ++i) {
      now_ms += 251; port.checkRecvFrame(received);
    }
    assert(port.bleuart.chunks.size() == 2);
    assert(port.bleuart.chunks[0].bytes == marker);
    assert(port.bleuart.chunks[1].bytes == legacy);
    assert(port.send_queue_len == 0);
  }
  {
    SerialBLEInterface port;
    Bluefruit.connection.mtu = 247;
    port.bleuart.fail_on_attempt = 2; // Marker succeeds; first raw attempt cannot enter SDK.
    assert(port.writeFrame(radio2.data(), radio2.size()) == radio2.size());
    for (unsigned i = 0; i < 4 && port.bleuart.chunks.empty(); ++i) {
      now_ms += 251; port.checkRecvFrame(received);
    }
    assert(port.bleuart.chunks.size() == 1 && port.bleuart.chunks[0].bytes == marker);
    assert(port.send_queue_len == 1 && port.send_queue[0].buf[0] == 0x93);
    assert(port.writeFrame(primary, sizeof(primary)) == sizeof(primary));
    assert(port.writeFrame(reply, sizeof(reply)) == sizeof(reply));
    assert(port.send_queue[0].buf[0] == 0x93); // Even a command response cannot strand its marker.
    for (unsigned i = 0; i < 12 && port.send_queue_len; ++i) {
      now_ms += 251; port.checkRecvFrame(received);
    }
    assert(port.bleuart.chunks.size() == 4 && port.send_queue_len == 0);
    assert(port.bleuart.chunks[1].bytes == legacy);
    assert(port.bleuart.chunks[2].bytes == std::vector<uint8_t>(reply, reply + sizeof(reply)));
    assert(port.bleuart.chunks[3].bytes == std::vector<uint8_t>(primary, primary + sizeof(primary)));
  }
  {
    SerialBLEInterface port;
    Bluefruit.connection.mtu = 247;
    assert(port.writeFrame(radio2.data(), radio2.size()) == radio2.size());
    assert(port.writeFrame(reply, sizeof(reply)) == sizeof(reply));
    for (unsigned i = 0; i < 8 && port.send_queue_len; ++i) {
      now_ms += 251; port.checkRecvFrame(received);
    }
    assert(port.bleuart.chunks.size() == 3);
    assert(port.bleuart.chunks[0].bytes == std::vector<uint8_t>(reply, reply + sizeof(reply)));
    assert(port.bleuart.chunks[1].bytes == marker && port.bleuart.chunks[2].bytes == legacy);
  }
  {
    SerialBLEInterface port;
    Bluefruit.connection.mtu = 247;
    const uint8_t during_marker[] = {0x0D, 0x51};
    const uint8_t during_raw[] = {0x0D, 0x52};
    assert(port.writeFrame(radio2.data(), radio2.size()) == radio2.size());
    port.bleuart.after_write = [&]() {
      assert(port.bleuart.chunks.back().bytes == marker);
      assert(port.send_queue[0].buf[0] == 0x93);
      assert(port.writeFrame(during_marker, sizeof(during_marker)) == sizeof(during_marker));
      port.bleuart.after_write = [&]() {
        assert(port.bleuart.chunks.back().bytes == legacy);
        assert(port.send_queue[0].buf[0] == 0x93);
        assert(port.writeFrame(during_raw, sizeof(during_raw)) == sizeof(during_raw));
      };
    };
    for (unsigned i = 0; i < 8 && port.send_queue_len; ++i) {
      now_ms += 251; port.checkRecvFrame(received);
    }
    assert(port.bleuart.chunks.size() == 4 && port.send_queue_len == 0);
    assert(port.bleuart.chunks[0].bytes == marker && port.bleuart.chunks[1].bytes == legacy);
    assert(port.bleuart.chunks[2].bytes == std::vector<uint8_t>(during_marker, during_marker + sizeof(during_marker)));
    assert(port.bleuart.chunks[3].bytes == std::vector<uint8_t>(during_raw, during_raw + sizeof(during_raw)));
  }
  for (uint16_t replacement : {uint16_t(1), uint16_t(2)}) {
    SerialBLEInterface port;
    Bluefruit.connection.mtu = 247;
    assert(port.writeFrame(radio2.data(), radio2.size()) == radio2.size());
    port.bleuart.after_write = [&]() { port.reconnect(replacement); };
    port.checkRecvFrame(received);
    assert(port.bleuart.chunks.size() == 1 && port.bleuart.chunks[0].bytes == marker);
    assert(port.send_queue_len == 0); // The old callback completion cannot arm raw on a new session.
    assert(port.writeFrame(primary, sizeof(primary)) == sizeof(primary));
    now_ms += 251; port.checkRecvFrame(received);
    assert(port.bleuart.chunks.size() == 2);
    assert(port.bleuart.chunks[1].handle == replacement);
    assert(port.bleuart.chunks[1].bytes == std::vector<uint8_t>(primary, primary + sizeof(primary)));
  }
  Bluefruit.connection.mtu = 23;
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!std::strcmp(argv[1], "enqueue")) enqueue_reset();
  else if (!std::strcmp(argv[1], "connection")) connection_changes();
  else if (!std::strcmp(argv[1], "application-start")) application_start();
  else if (!std::strcmp(argv[1], "prestart-partial")) previous_prestart_partial();
  else if (!std::strcmp(argv[1], "radio2")) radio2_log_groups();
  else if (!std::strcmp(argv[1], "radio2-failed-marker")) radio2_failed_marker_reply();
  else {
    enqueue_reset(); callback_ownership(); connection_changes(); old_queue_and_recovery();
    recovery_connection_changes();
  }
  assert(!critical_depth && !during_copy);
}
