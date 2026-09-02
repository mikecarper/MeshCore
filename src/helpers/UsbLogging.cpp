#include "UsbLogging.h"

#if defined(ARDUINO)
#include <Arduino.h>
#include <atomic>

#if defined(NRF52_PLATFORM)
  #include "NonBlockingWriteStream.h"
#endif

#if defined(NRF52_PLATFORM) && \
    (defined(ENABLE_USB_INTERFACE) || defined(OTA_FOLDER_SERIAL))
  #define MESH_NRF52_PRIMARY_USB_NONBLOCKING 1
  #include <Adafruit_TinyUSB.h>
  #if !defined(CFG_TUD_CDC) || CFG_TUD_CDC < 1
    #error "nRF52 USB Companion/mOTA requires CFG_TUD_CDC >= 1"
  #endif
#else
  #define MESH_NRF52_PRIMARY_USB_NONBLOCKING 0
#endif

#if defined(MESH_DUAL_CDC_LOGGING)
  #if !defined(COMPANION_FEATURE_DEDICATED_USB_LOGGING) || \
      !COMPANION_FEATURE_DEDICATED_USB_LOGGING
    #error "MESH_DUAL_CDC_LOGGING requires its dedicated Companion capability"
  #endif
  #if !defined(NRF52_PLATFORM)
    #error "MESH_DUAL_CDC_LOGGING is supported only by nRF52 Full Companion"
  #endif
  #if !defined(ENABLE_USB_INTERFACE)
    #error "MESH_DUAL_CDC_LOGGING requires ENABLE_USB_INTERFACE"
  #endif
  #if !defined(CFG_TUD_CDC) || CFG_TUD_CDC < 2
    #error "MESH_DUAL_CDC_LOGGING requires CFG_TUD_CDC >= 2"
  #endif
#endif

namespace mesh {

#if defined(ENABLE_USB_INTERFACE)
// A USB Companion owns its primary stream for framed traffic until saved
// preferences are loaded. Starting disabled prevents early boot diagnostics
// from corrupting that stream before single-TTY builds can enter terminal mode.
static std::atomic<bool> usb_logging_enabled{false};
#else
static std::atomic<bool> usb_logging_enabled{true};
#endif
static std::atomic<bool> usb_logging_preference_known{false};

class NullUsbLoggingStream : public Stream {
 public:
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  void flush() override {}
  size_t write(uint8_t) override { return 1; }
  size_t write(const uint8_t*, size_t size) override { return size; }
};

static NullUsbLoggingStream null_usb_logging_stream;

#if MESH_NRF52_PRIMARY_USB_NONBLOCKING
#if defined(ENABLE_USB_INTERFACE)
// Writers/readers may access CDC0 only when DTR is high and the application has
// completed the current close epoch. Incrementing reset_generation therefore
// closes the transport atomically; an older completion can publish only its
// older epoch and can never reopen across a newer close.
static std::atomic<uint32_t> primary_usb_reset_generation{0};
static std::atomic<uint32_t> primary_usb_allowed_generation{0};
static std::atomic<bool> primary_usb_line_state_dtr{false};
static constexpr uint32_t primary_usb_session_settle_millis = 8;
static std::atomic<uint32_t> primary_usb_reset_settle_until{0};

static uint32_t primaryUsbSessionGeneration() {
  return primary_usb_reset_generation.load(std::memory_order_acquire);
}

static bool canAccessPrimaryUsbSession(void*) {
  return primary_usb_line_state_dtr.load(std::memory_order_acquire)
      && primary_usb_allowed_generation.load(std::memory_order_acquire)
          == primary_usb_reset_generation.load(std::memory_order_acquire);
}

static void endPrimaryUsbHostSession(bool clear_cdc_fifos) {
  const bool previous = primary_usb_line_state_dtr.exchange(
      false, std::memory_order_acq_rel);
  if (!previous) return;

  // Publish a short quiescence deadline before closing the generation gate.
  // TinyUSB's TX clear does not cancel an endpoint transfer already in its
  // class buffer, so the later final TX purge catches a racing old-session
  // write. RX is purged synchronously below; bytes sent by a reopened host
  // during the settle interval remain queued until the gate reopens.
  primary_usb_reset_settle_until.store(
      millis() + primary_usb_session_settle_millis,
      std::memory_order_release);
  primary_usb_reset_generation.fetch_add(1, std::memory_order_acq_rel);
  if (clear_cdc_fifos) {
    // The line-state callback runs before TinyUSB resets the device. A device
    // unmount/remount boundary has already reset these class FIFOs.
    tud_cdc_n_read_flush(0);
    (void)tud_cdc_n_write_clear(0);
  }
}
#endif
// Adafruit_USBD_CDC::write() retries from a yield loop until it has queued the
// complete request. A CDC host can change DTR or stop draining between the
// caller's availableForWrite() sample and that loop. Use TinyUSB's native
// single-attempt primitive so both Companion CDCs return short immediately and
// let their existing queue/whole-record policy decide what to retry.
static size_t writeTinyUsbCdcOnce(void* context, const uint8_t* data,
                                  size_t size) {
  const uint8_t instance = static_cast<uint8_t>(
      reinterpret_cast<uintptr_t>(context));
  if (data == nullptr || size == 0 || instance >= CFG_TUD_CDC) {
    return 0;
  }
#if defined(ENABLE_USB_INTERFACE)
  if (instance == 0 && !canAccessPrimaryUsbSession(nullptr)) {
    return 0;
  }
#endif
  if (!tud_cdc_n_connected(instance)) return 0;

  const size_t available = tud_cdc_n_write_available(instance);
  const size_t attempt = size < available ? size : available;
  if (attempt == 0) return 0;
  return tud_cdc_n_write(instance, data, attempt);
}

static SingleAttemptNonBlockingStream nonblocking_primary_usb_companion_port(
    Serial, writeTinyUsbCdcOnce, reinterpret_cast<void*>(uintptr_t{0})
#if defined(ENABLE_USB_INTERFACE)
    , canAccessPrimaryUsbSession
#endif
    );
// SerialMotaSource emits one contiguous request whose largest valid frame is
// 11 bytes. Preflight that entire record before the sole TinyUSB write attempt;
// a busy FIFO drops the request cleanly and lets the transaction retry later.
static AtomicWholeRecordNonBlockingStream<11>
    nonblocking_primary_usb_mota_port(
        nonblocking_primary_usb_companion_port);
#if defined(ENABLE_USB_INTERFACE)
// Unlike Binary Companion frames, a terminal reply is produced as many Print
// calls whose return values are not consumed by the CLI. Retain one complete
// multi-line response while a normal host drains CDC0, but bound the memory and
// drop later records instead of ever waiting on an unread endpoint.
static BufferedNonBlockingWriteStream<4096>
    buffered_primary_usb_terminal_port(
        nonblocking_primary_usb_companion_port);
static uint32_t primary_usb_terminal_seen_reset_generation = 0;
static uint32_t primary_usb_terminal_taken_reset_generation = 0;
#endif
#endif

static void setPlatformDebugOutputEnabled(bool enabled) {
#if defined(ESP32_PLATFORM) && defined(ENABLE_USB_INTERFACE)
  // Arduino-ESP32 log_e()/ESP-IDF diagnostics otherwise write straight to
  // the same UART/CDC stream used by Binary Companion. Keep that low-level
  // route under the same runtime gate as MeshCore's own diagnostics.
  Serial.setDebugOutput(enabled);
#else
  (void)enabled;
#endif
}

#if defined(MESH_DUAL_CDC_LOGGING)
static bool dedicated_usb_logging_port_configured = false;
static bool dedicated_usb_logging_port_started = false;
static std::atomic<bool> dedicated_usb_logging_port_connected{false};
static bool dedicated_usb_logging_sof_enabled = false;

static constexpr char dedicated_usb_logging_descriptor[] =
    "MeshCore Logging";

// Adafruit_USBD_CDC::begin() assigns "TinyUSB Serial" and immediately copies
// the interface descriptor into TinyUSBDevice's configuration buffer. Calling
// setStringDescriptor() after begin() therefore cannot rename that copied
// descriptor. Assign our string while the virtual descriptor builder is
// running, immediately before the core copies it instead.
class DedicatedUsbLoggingCdc : public Adafruit_USBD_CDC {
 public:
  uint16_t getInterfaceDescriptor(uint8_t itfnum_deprecated, uint8_t* buf,
                                  uint16_t bufsize) override {
    if (buf != nullptr) {
      setStringDescriptor(dedicated_usb_logging_descriptor);
    }
    return Adafruit_USBD_CDC::getInterfaceDescriptor(
        itfnum_deprecated, buf, bufsize);
  }
};

static DedicatedUsbLoggingCdc dedicated_usb_logging_port;
static SingleAttemptNonBlockingStream single_attempt_dedicated_usb_logging_port(
    dedicated_usb_logging_port, writeTinyUsbCdcOnce,
    reinterpret_cast<void*>(uintptr_t{1}));
static TaskOwnedWriteStream<> usb_task_dedicated_usb_logging_port(
    single_attempt_dedicated_usb_logging_port);
static WholeRecordNonBlockingStream<>
    nonblocking_dedicated_usb_logging_port(
        usb_task_dedicated_usb_logging_port);

static constexpr char dedicated_usb_logging_identity[] =
    "MeshCore USB logging port\r\n"
    "USB CDC 1; interface 02; Linux stable suffix: -if02\r\n";
static constexpr size_t dedicated_usb_logging_identity_size =
    sizeof(dedicated_usb_logging_identity) - 1;
static_assert(dedicated_usb_logging_identity_size <= 256,
              "USB identity marker exceeds the nonblocking record limit");
static_assert(dedicated_usb_logging_identity_size
                  <= CFG_TUD_CDC_TX_BUFSIZE,
              "USB identity marker exceeds the TinyUSB CDC TX FIFO");
static std::atomic<uint32_t> dedicated_usb_logging_reset_generation{0};
static uint32_t dedicated_usb_logging_seen_reset_generation = 0;
// TinyUSB reports SET_CONTROL_LINE_STATE from its owner task. Keep the last
// DTR value independently of main-loop polling so a close/reopen cannot be
// collapsed into one continuously connected sample.
static std::atomic<bool> dedicated_usb_logging_line_state_dtr{false};
static bool dedicated_usb_logging_usb_task_connected = false;
static size_t dedicated_usb_logging_identity_offset = 0;
// Windows configures line coding and then purges its COM buffers. Keep CDC1
// silent for more than one Windows scheduling quantum so that purge cannot
// discard the identity marker and expose a later diagnostic as byte zero.
static constexpr uint8_t dedicated_usb_logging_host_settle_sofs = 50;
static uint8_t dedicated_usb_logging_quiet_sofs = 0;

static void resetDedicatedUsbLoggingIdentity() {
  dedicated_usb_logging_identity_offset = 0;
}

static void resetDedicatedUsbLoggingUsbTaskState() {
  dedicated_usb_logging_usb_task_connected = false;
  resetDedicatedUsbLoggingIdentity();
  usb_task_dedicated_usb_logging_port.discardPending();
}

static void restartDedicatedUsbLoggingHostSession() {
  // This callback and the SOF drain both run in TinyUSB's task. Gate producers
  // before clearing both queues, then publish a generation so the next SOF
  // repeats the reset after any producer which was already in flight. Clearing
  // here also prevents bytes left in TinyUSB's TX FIFO from preceding the
  // identity marker after a rapid reopen.
  dedicated_usb_logging_port_connected.store(false,
                                               std::memory_order_release);
  (void)tud_cdc_n_write_clear(1);
  resetDedicatedUsbLoggingUsbTaskState();
  dedicated_usb_logging_quiet_sofs =
      dedicated_usb_logging_host_settle_sofs;
  dedicated_usb_logging_reset_generation.fetch_add(
      1, std::memory_order_acq_rel);
}

static void handleDedicatedUsbLoggingLineState(bool dtr) {
  const bool previous = dedicated_usb_logging_line_state_dtr.exchange(
      dtr, std::memory_order_acq_rel);
  if (previous == dtr) return;
  restartDedicatedUsbLoggingHostSession();
  // TinyUSB clears its SOF-consumer bits on every USB bus reset. A rapid
  // reset/re-enumeration can leave the main loop's requested-state cache true
  // throughout, so it would otherwise never ask TinyUSB to restore this
  // callback. SET_CONTROL_LINE_STATE runs in TinyUSB's owner task after the
  // new configuration is active, making the fresh DTR edge the exact place
  // to rearm (or disable) CDC1's sole drain tick.
  tud_sof_cb_enable(dtr && isUsbLoggingEnabled());
}

static void handleDedicatedUsbLoggingLineCoding() {
  // Windows can close and immediately reopen a COM handle without exposing a
  // distinct DTR-low interval to the device. It still configures line coding
  // for the newly opened handle. Treat that owner-task callback as a fresh
  // CDC1 host session only when DTR is already asserted; if it is not, the
  // subsequent DTR-high edge performs the reset instead.
  if (tud_cdc_n_connected(1)) {
    restartDedicatedUsbLoggingHostSession();
  }
}

// Called by TinyUSB's own high-priority task through tud_sof_cb(). Application
// writers only copy complete records into the queue above, so every CDC1 FIFO
// and endpoint operation happens in the same task as control transfers. The
// identity marker bypasses that producer queue: its cursor advances from the
// actual TinyUSB write result, not from queue acceptance. Sampling DTR here at
// 1 kHz also catches a close/reopen that occurs entirely between main-loop
// service calls.
void serviceDedicatedUsbLoggingFromUsbTask() {
  const uint32_t reset_generation =
      dedicated_usb_logging_reset_generation.load(std::memory_order_acquire);
  if (reset_generation != dedicated_usb_logging_seen_reset_generation) {
    resetDedicatedUsbLoggingUsbTaskState();
    dedicated_usb_logging_seen_reset_generation = reset_generation;
  }

  const bool physical_connected = tud_cdc_n_connected(1);
  const bool connected = isUsbLoggingEnabled() && physical_connected;

  if (!physical_connected) {
    // A bus reset need not deliver an explicit DTR-low request. Make the next
    // DTR-high callback an edge even when the previous host vanished abruptly.
    dedicated_usb_logging_line_state_dtr.store(
        false, std::memory_order_release);
  }
  if (!connected) {
    dedicated_usb_logging_port_connected.store(
        false, std::memory_order_release);
    resetDedicatedUsbLoggingUsbTaskState();
    return;
  }

  if (dedicated_usb_logging_quiet_sofs > 0) {
    dedicated_usb_logging_port_connected.store(
        false, std::memory_order_release);
    // Drop anything already in flight from the prior session and anything a
    // producer raced into the owner queue before the callback gated it.
    (void)tud_cdc_n_write_clear(1);
    resetDedicatedUsbLoggingUsbTaskState();
    --dedicated_usb_logging_quiet_sofs;
    return;
  }

  dedicated_usb_logging_port_connected.store(
      true, std::memory_order_release);

  if (!dedicated_usb_logging_usb_task_connected) {
    // Drop diagnostics from the prior host before sending byte zero of the
    // marker to the new host.
    usb_task_dedicated_usb_logging_port.discardPending();
    resetDedicatedUsbLoggingIdentity();
    dedicated_usb_logging_usb_task_connected = true;
  }

  if (dedicated_usb_logging_identity_offset
      < dedicated_usb_logging_identity_size) {
    // This direct single-attempt call runs in the sole CDC1 endpoint owner.
    // With the complete marker fitting in one TX FIFO, a short result can only
    // occur around a connection transition; restart from byte zero for the
    // next observed host instead of delivering only a suffix.
    const size_t remaining = dedicated_usb_logging_identity_size
        - dedicated_usb_logging_identity_offset;
    if (single_attempt_dedicated_usb_logging_port.availableForWrite()
        < static_cast<int>(remaining)) {
      (void)tud_cdc_n_write_flush(1);
      return;
    }
    const size_t written = single_attempt_dedicated_usb_logging_port.write(
        reinterpret_cast<const uint8_t*>(
            dedicated_usb_logging_identity
            + dedicated_usb_logging_identity_offset),
        remaining);

    dedicated_usb_logging_identity_offset =
        detail::nextUsbLoggingIdentityOffset(
            dedicated_usb_logging_identity_offset, remaining, written);
    (void)tud_cdc_n_write_flush(1);
    return;
  }

  // If the host stops reading, the TinyUSB FIFO and this queue simply fill and
  // later diagnostics are dropped without blocking either task.
  usb_task_dedicated_usb_logging_port.drainOne();
  (void)tud_cdc_n_write_flush(1);
}
#elif defined(NRF52_PLATFORM)
// Single-CDC nRF52 roles need the same protection. In particular, BLE debug
// callbacks and packet logging write through usbLoggingPort() without going
// through MeshCore's formatted-debug helper.
  #if MESH_NRF52_PRIMARY_USB_NONBLOCKING
static AtomicWholeRecordNonBlockingStream<>
    nonblocking_primary_usb_logging_port(
    nonblocking_primary_usb_companion_port);
  #else
static WholeRecordNonBlockingStream<>
    nonblocking_primary_usb_logging_port(Serial);
  #endif
#endif

bool isUsbLoggingEnabled() {
  return usb_logging_enabled.load(std::memory_order_relaxed);
}

void setUsbLoggingEnabled(bool enabled) {
  usb_logging_enabled.store(enabled, std::memory_order_relaxed);
  usb_logging_preference_known.store(true, std::memory_order_relaxed);
  setPlatformDebugOutputEnabled(enabled);
}

bool saveUsbLoggingBootPreference(bool enabled) {
  (void)enabled;
  return true;
}

void beginUsbLoggingPort() {
  // setup() calls this once before role preferences are loaded and again
  // afterwards. The first call silences framework diagnostics on a protected
  // ESP32 Companion stream; setUsbLoggingEnabled() restores them only when the
  // saved setting explicitly enables logging.
  setPlatformDebugOutputEnabled(isUsbLoggingEnabled());
#if defined(MESH_DUAL_CDC_LOGGING)
  if (dedicated_usb_logging_port_started
      || !usb_logging_preference_known.load(std::memory_order_relaxed)
      || !isUsbLoggingEnabled()) {
    return;
  }

  dedicated_usb_logging_port.begin(115200);
  dedicated_usb_logging_port_configured = true;
  dedicated_usb_logging_port_started = true;

  // The nRF52 core starts TinyUSB before setup(). If the host completed
  // enumeration in that small window, reconnect once so it reads the expanded
  // two-CDC descriptor. Usually enumeration has not completed and no reconnect
  // is needed.
  if (TinyUSBDevice.mounted()) {
    TinyUSBDevice.detach();
    delay(10);
    TinyUSBDevice.attach();
  }
#endif
}

void serviceUsbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  const bool connected = dedicated_usb_logging_port_started
      && dedicated_usb_logging_port.dtr();
  const bool should_service = connected && isUsbLoggingEnabled();
  // Only the TinyUSB owner task publishes a positive producer gate. The main
  // loop may close it, but must not bypass the post-open quiet window.
  if (!should_service) {
    dedicated_usb_logging_port_connected.store(
        false, std::memory_order_release);
  }
  if (!connected) {
    dedicated_usb_logging_line_state_dtr.store(
        false, std::memory_order_release);
  }

  // SOF callbacks execute in TinyUSB's task and provide the bounded owner-task
  // drain tick. This cached requested state converges ordinary opens/closes
  // and runtime logging changes. TinyUSB clears the actual SOF-consumer bit on
  // bus reset, so the owner-task DTR callback above also rearms it after each
  // re-enumeration even when this sampled state never changed. Queue cursor
  // mutation remains in TinyUSB's owner task, and the reset generation stays
  // pending until a later reopen lets that callback reset before its first
  // drain.
  if (should_service != dedicated_usb_logging_sof_enabled) {
    if (should_service) {
      // Publish the new generation before the callback can run.
      dedicated_usb_logging_reset_generation.fetch_add(
          1, std::memory_order_acq_rel);
      tud_sof_cb_enable(true);
    } else {
      // Stop the consumer before publishing the next reset generation. The
      // generation remains pending until a later reopen enables the callback.
      tud_sof_cb_enable(false);
      dedicated_usb_logging_reset_generation.fetch_add(
          1, std::memory_order_acq_rel);
    }
    dedicated_usb_logging_sof_enabled = should_service;
  }
#endif
}

Stream& usbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  if (isUsbLoggingEnabled() && dedicated_usb_logging_port_started
      && dedicated_usb_logging_port_connected.load(std::memory_order_acquire)) {
    return nonblocking_dedicated_usb_logging_port;
  }
  return null_usb_logging_stream;
#else
  if (!isUsbLoggingEnabled()) return null_usb_logging_stream;
  #if defined(NRF52_PLATFORM)
    return nonblocking_primary_usb_logging_port;
  #else
    return Serial;
  #endif
#endif
}

Stream& usbCompanionPort() {
#if MESH_NRF52_PRIMARY_USB_NONBLOCKING
  return nonblocking_primary_usb_companion_port;
#else
  return Serial;
#endif
}

Stream& usbMotaPort() {
#if MESH_NRF52_PRIMARY_USB_NONBLOCKING
  return nonblocking_primary_usb_mota_port;
#else
  return usbCompanionPort();
#endif
}

Stream& usbTerminalPort() {
#if defined(NRF52_PLATFORM) && defined(ENABLE_USB_INTERFACE)
  return buffered_primary_usb_terminal_port;
#else
  return usbCompanionPort();
#endif
}

void serviceUsbTerminalPort() {
#if defined(NRF52_PLATFORM) && defined(ENABLE_USB_INTERFACE)
  const uint32_t reset_generation =
      primaryUsbSessionGeneration();
  if (reset_generation != primary_usb_terminal_seen_reset_generation) {
    buffered_primary_usb_terminal_port.discardPending();
    primary_usb_terminal_seen_reset_generation = reset_generation;
  }
  buffered_primary_usb_terminal_port.service();
#endif
}

void discardUsbTerminalOutput() {
#if defined(NRF52_PLATFORM) && defined(ENABLE_USB_INTERFACE)
  buffered_primary_usb_terminal_port.discardPending();
#endif
}

bool hasPendingUsbTerminalOutput() {
#if defined(NRF52_PLATFORM) && defined(ENABLE_USB_INTERFACE)
  return buffered_primary_usb_terminal_port.queuedByteCount() != 0;
#else
  return false;
#endif
}

bool takeUsbTerminalSessionReset() {
#if defined(NRF52_PLATFORM) && defined(ENABLE_USB_INTERFACE)
  const uint32_t reset_generation =
      primaryUsbSessionGeneration();
  if (reset_generation == primary_usb_terminal_taken_reset_generation) {
    return false;
  }
  primary_usb_terminal_taken_reset_generation = reset_generation;
  return true;
#else
  return false;
#endif
}

#if defined(NRF52_PLATFORM) && defined(ENABLE_USB_INTERFACE)
static void completePrimaryUsbSessionReset(void*) {
  // This runs under the primary SingleAttempt stream's producer gate. It
  // removes a write that raced the owner-task close callback before allowing
  // any producer to address the reopened handle. RX was already purged when
  // the close/line-coding boundary was captured. Do not purge it again here:
  // hosts such as meshcli send APP_START immediately after opening the port,
  // while this generation gate is deliberately settling.
  (void)tud_cdc_n_write_clear(0);
  const uint32_t cleaned_generation =
      primary_usb_terminal_taken_reset_generation;
  primary_usb_allowed_generation.store(cleaned_generation,
                                       std::memory_order_release);
}
#endif

bool tryCompleteUsbTerminalSessionReset() {
#if defined(NRF52_PLATFORM) && defined(ENABLE_USB_INTERFACE)
  const uint32_t settle_until =
      primary_usb_reset_settle_until.load(std::memory_order_acquire);
  if ((int32_t)(millis() - settle_until) < 0) return false;
  return nonblocking_primary_usb_companion_port.tryRunExclusive(
      completePrimaryUsbSessionReset);
#else
  return true;
#endif
}

bool hasDedicatedUsbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return true;
#else
  return false;
#endif
}

bool isDedicatedUsbLoggingPortConfigured() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return dedicated_usb_logging_port_configured;
#else
  return false;
#endif
}

bool usbLoggingInterfaceRestartRequired() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return dedicated_usb_logging_port_configured != isUsbLoggingEnabled();
#else
  return false;
#endif
}

const char* usbLoggingPortDescription() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return "dedicated USB CDC 1, interface 02 (Linux: *-if02; tty/COM name is host-assigned)";
#else
  return "primary USB serial port (tty/COM name is host-assigned)";
#endif
}

}  // namespace mesh

#if defined(NRF52_PLATFORM) && defined(ENABLE_USB_INTERFACE)
// A protocol-only, weak-declaration-free translation unit supplies strong
// versions of TinyUSB's callbacks and forwards here. Preserve Adafruit's CDC0
// 1200-baud touch behavior exactly, capture every CDC0 host-session close and
// USB device boundary, and give an optional CDC1 logging endpoint its exact
// reconnect edges.
extern "C" void meshTinyUsbCdcLineStateChanged(uint8_t instance, bool dtr,
                                                 bool rts) {
  (void)rts;
#if defined(MESH_DUAL_CDC_LOGGING)
  if (instance == 1) {
    mesh::handleDedicatedUsbLoggingLineState(dtr);
    return;
  }
#endif

  if (instance == 0 && !dtr) {
    // Discard raw input/output already admitted to TinyUSB. The generation
    // change keeps every facade closed until the application resets its
    // protocol owner and performs the final exclusive TX purge.
    mesh::endPrimaryUsbHostSession(true);
    cdc_line_coding_t coding;
    tud_cdc_get_line_coding(&coding);
    if (coding.bit_rate == 1200) {
      TinyUSB_Port_EnterDFU();
    }
  } else if (instance == 0) {
    const bool was_open = mesh::primary_usb_line_state_dtr.load(
        std::memory_order_acquire);
    if (!was_open) {
      // Purge any late bytes from the closed owner before publishing the fresh
      // session. New-host input sent after SET_CONTROL_LINE_STATE completes is
      // then retained while the application-side generation gate settles.
      tud_cdc_n_read_flush(0);
      mesh::primary_usb_line_state_dtr.store(
          true, std::memory_order_release);
    }
  }
}

extern "C" void meshTinyUsbDeviceSessionBoundary() {
  // TinyUSB does not issue SET_CONTROL_LINE_STATE(DTR=0) for a hard unplug or
  // bus reset. An unmount catches the former; the following mount catches a
  // reset/reconfigure whose old DTR state survived in this application. The
  // helper is edge-sensitive, so initial enumeration and the mount following
  // an already observed unmount do not create a false reset.
  mesh::endPrimaryUsbHostSession(false);
#if defined(MESH_DUAL_CDC_LOGGING)
  mesh::handleDedicatedUsbLoggingLineState(false);
#endif
}

extern "C" void meshTinyUsbCdcLineCodingChanged(uint8_t instance) {
  if (instance == 0) {
    // Windows may close/reopen a COM handle without presenting a distinct
    // DTR-low interval to the device, but it configures line coding for the
    // new handle. If CDC0 still appears open, treat this as a protocol-session
    // boundary. Restore the physical DTR sample after closing the epoch so an
    // intentional baud change on a continuously open handle recovers as soon
    // as the application has reset its parsers.
    if (mesh::primary_usb_line_state_dtr.load(std::memory_order_acquire)) {
      mesh::endPrimaryUsbHostSession(true);
      mesh::primary_usb_line_state_dtr.store(
          tud_cdc_n_connected(0), std::memory_order_release);
    }
    return;
  }
#if defined(MESH_DUAL_CDC_LOGGING)
  if (instance == 1) {
    mesh::handleDedicatedUsbLoggingLineCoding();
  }
#else
  (void)instance;
#endif
}

#if defined(MESH_DUAL_CDC_LOGGING)
// The weak-declaration-free bridge lives in UsbLoggingLineStateOverride.cpp.
// This forwarding target runs in TinyUSB's task, making CDC1 endpoint
// ownership explicit without inheriting TinyUSB's weak callback attribute.
extern "C" void meshTinyUsbStartOfFrame(uint32_t frame_count) {
  (void)frame_count;
  mesh::serviceDedicatedUsbLoggingFromUsbTask();
}
#endif
#endif
#endif
