#include "UsbLogging.h"
#include "UsbLoggingClientActivity.h"

#if defined(ARDUINO)
#include <Arduino.h>
#include <atomic>
#include <stdio.h>

#if MESH_ESP32_HWCDC_SESSION_GUARD
  #include "esp_idf_version.h"
  #include "hal/usb_serial_jtag_ll.h"
  #include "esp_arduino_version.h"
  #if ESP_ARDUINO_VERSION_MAJOR == 2 && ESP_ARDUINO_VERSION_MINOR == 0 \
      && ESP_ARDUINO_VERSION_PATCH == 17 \
      && (CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32S3)
    #define MESH_HWCDC_PINNED_TX_BACKPORT 1
    extern "C" void meshEsp32HwcdcSetTxAllowed(bool allowed);
    extern "C" void meshEsp32HwcdcKickTx();
    extern "C" bool meshEsp32HwcdcTxPending();
    extern "C" bool meshEsp32HwcdcDiscardTxStash();
  #else
    #define MESH_HWCDC_PINNED_TX_BACKPORT 0
  #endif
#endif

#if defined(NRF52_PLATFORM) || MESH_ESP32_HWCDC_SESSION_GUARD \
    || MESH_ESP32_TINYUSB_NONBLOCKING
  #include "NonBlockingWriteStream.h"
#endif
#if MESH_ESP32_TINYUSB_NONBLOCKING
  #include "esp32-hal-tinyusb.h"
  // Build-local Arduino hook queries its actual CDC descriptor's IN endpoint.
  extern "C" bool meshEsp32TinyUsbTxPending();
#endif
#if defined(NRF52_PLATFORM) && defined(USE_TINYUSB)
  #include <Adafruit_TinyUSB.h>
#endif
#if MESH_ESP32_HWCDC_SESSION_GUARD
  #include "UsbAsciiBinarySwitch.h"
#endif

#if defined(NRF52_PLATFORM) && \
    (defined(USE_TINYUSB) || defined(ENABLE_USB_INTERFACE) || defined(OTA_FOLDER_SERIAL))
  #define MESH_NRF52_PRIMARY_USB_NONBLOCKING 1
  #include <Adafruit_TinyUSB.h>
  #include <nrf.h>
  // Build-local CDC query installed by scripts/nrf52_usb_power_fix.py. It does
  // not clear/cancel an endpoint or change its USB bulk data-toggle state.
  extern "C" bool mesh_tud_cdc_n_tx_pending(uint8_t instance);
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

#if defined(ENABLE_USB_INTERFACE) \
    || (defined(ESP32_PLATFORM) && defined(MESHCORE_EXPANDED_PARTITION_PROFILE))
// A USB Companion owns its primary stream for framed traffic until saved
// preferences are loaded. Starting disabled prevents early boot diagnostics
// from corrupting that stream before single-TTY builds can enter terminal mode.
// Full infrastructure likewise waits for its saved runtime logging preference.
static std::atomic<bool> usb_logging_enabled{false};
#else
static std::atomic<bool> usb_logging_enabled{true};
#endif
static std::atomic<bool> usb_logging_preference_known{false};
static std::atomic<bool> usb_debug_enabled{false};
static std::atomic<uint32_t> usb_logging_tx_progress{0};
static std::atomic<bool> usb_logging_tx_waiting{false};
#if MESH_ESP32_TINYUSB_NONBLOCKING \
    || (defined(NRF52_PLATFORM) && defined(USE_TINYUSB))
static bool usb_logging_watchdog_detached = false;
static uint32_t usb_logging_watchdog_attach_at = 0;
#endif

static void noteUsbLoggingTxAttempt() {
  if (isUsbLoggingEnabled()) usb_logging_tx_waiting.store(true, std::memory_order_release);
}
static void noteUsbLoggingTxComplete() {
  if (!isUsbLoggingEnabled()) return;
  usb_logging_tx_progress.fetch_add(1, std::memory_order_acq_rel);
  usb_logging_tx_waiting.store(false, std::memory_order_release);
}

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

#if MESH_ESP32_TINYUSB_NONBLOCKING
// Arduino-ESP32 2.0.17 constructs Serial as USBCDC(0). Keep its RX queue and
// existing descriptors/callbacks, but never call its potentially unbounded
// write()/flush() or its mutex-taking availableForWrite(). The native CDC
// application API makes one FIFO attempt and can safely return short; its
// flush starts an available endpoint transfer without waiting for the host.
// TinyUSB still takes short RTOS FIFO/endpoint mutexes internally: this is a
// no-host-progress-wait contract, not a claim that the USB stack is lock-free.
static std::atomic<uint32_t> esp32_tinyusb_reset_generation{0};
static std::atomic<uint32_t> esp32_tinyusb_clean_generation{0};
static std::atomic_flag esp32_tinyusb_queue_busy = ATOMIC_FLAG_INIT;
static std::atomic<bool> esp32_tinyusb_terminal_discard_pending{false};
static std::atomic<uint32_t> esp32_tinyusb_terminal_dropped_bytes{0};
static uint32_t esp32_tinyusb_terminal_reported_dropped_bytes = 0;
static uint32_t esp32_tinyusb_taken_reset_generation = 0;
static std::atomic<bool> esp32_tinyusb_was_connected{false};
// 0: ordinary; 1: detached; 2: awaiting fresh mount; 3: preparing quarantine.
static std::atomic<uint8_t> esp32_tinyusb_reenumeration_state{0};
static std::atomic<bool> esp32_tinyusb_owner_dtr{false};
static std::atomic<uint32_t> esp32_tinyusb_reattach_after{0};
static constexpr uint32_t esp32_tinyusb_detach_millis = 20;

static bool canAccessEsp32TinyUsb(void*) {
  return !xPortInIsrContext()
      && esp32_tinyusb_reenumeration_state.load(std::memory_order_acquire) == 0
      && tud_cdc_n_connected(0)
      && esp32_tinyusb_clean_generation.load(std::memory_order_acquire)
          == esp32_tinyusb_reset_generation.load(std::memory_order_acquire);
}

static void requestEsp32TinyUsbReenumeration(bool force_reconnect = false) {
  uint8_t expected = 0;
  if (!esp32_tinyusb_reenumeration_state.compare_exchange_strong(
          expected, 3, std::memory_order_acq_rel)) {
    // A later watchdog stage may restart an already reattached device whose
    // host never delivered a fresh mount. Owner callbacks never force this.
    if (!force_reconnect || expected != 2
        || !esp32_tinyusb_reenumeration_state.compare_exchange_strong(
            expected, 3, std::memory_order_acq_rel)) return;
  }
  // The pinned ESP32 S2/S3 DCD disconnect is one bounded soft-disconnect
  // register write, not a send into TinyUSB's owner queue. Isolate an armed
  // old packet immediately; only the application loop may reattach later.
  (void)tud_disconnect();
  esp32_tinyusb_reattach_after.store(millis() + esp32_tinyusb_detach_millis,
                                    std::memory_order_release);
  // Publish only a fully prepared detach. The other core must never connect
  // before this callback has actually disconnected and started its deadline.
  esp32_tinyusb_reenumeration_state.store(1, std::memory_order_release);
}

static void purgeEsp32TinyUsbRx() {
  // Owner-task session hooks run before new-host RX callbacks. A fixed snapshot
  // bounds this purge even if an application reader consumes bytes concurrently.
  int pending = Serial.available();
  while (pending-- > 0) (void)Serial.read();
  // At unmount the class endpoints have already been reset. read_flush also
  // rearms OUT, so it must not address an unopened/reset endpoint there.
  if (tud_mounted()) tud_cdc_n_read_flush(0);
}

class Esp32TinyUsbFifoStream : public Stream {
 public:
  int available() override {
    const int count = Serial.available();
    return count > 0 ? count : 0;
  }
  int read() override { return Serial.read(); }
  int peek() override { return Serial.peek(); }
  void flush() override {}
  int availableForWrite() override {
    return canAccessEsp32TinyUsb(nullptr)
        ? static_cast<int>(tud_cdc_n_write_available(0)) : 0;
  }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* data,
               size_t size) override {
    if (data == nullptr || size == 0 || !canAccessEsp32TinyUsb(nullptr)) return 0;
    const size_t available = tud_cdc_n_write_available(0);
    const size_t attempt = size < available ? size : available;
    if (attempt == 0) return 0;
    noteUsbLoggingTxAttempt();
    const size_t written = tud_cdc_n_write(0, data, attempt);
    (void)tud_cdc_n_write_flush(0);
    return written;
  }
};

static Esp32TinyUsbFifoStream esp32_tinyusb_fifo_port;
static size_t writeEsp32TinyUsbOnce(void*, const uint8_t* data, size_t size) {
  return esp32_tinyusb_fifo_port.write(data, size);
}
static SingleAttemptNonBlockingStream nonblocking_esp32_tinyusb_port(
    esp32_tinyusb_fifo_port, writeEsp32TinyUsbOnce, nullptr,
    canAccessEsp32TinyUsb);
static AtomicWholeRecordNonBlockingStream<11> nonblocking_esp32_tinyusb_mota_port(
    nonblocking_esp32_tinyusb_port);

// The ESP32 CDC FIFO is just 64 bytes. Retain complete producer writes in ONE
// chronological queue: independent log/reply queues would interleave their
// partial lines as that small FIFO drains. Diagnostics leave most of the bounded
// queue reserved for functional replies; congestion drops records, not LoRa.
static constexpr size_t esp32_tinyusb_text_capacity = 4096;
static constexpr size_t esp32_tinyusb_functional_reserve = 3072;
static constexpr size_t esp32_tinyusb_log_record_capacity = 640;
// ESP32 Print::printf has no Adafruit 256-byte scratch-length bug. Disable
// that nRF52-specific sentinel without changing the existing nRF52 facade.
static BufferedNonBlockingWriteStream<esp32_tinyusb_text_capacity,
    esp32_tinyusb_text_capacity + 1> esp32_tinyusb_text_queue(
        nonblocking_esp32_tinyusb_port);

template <bool Diagnostic>
class Esp32TinyUsbBufferedStream : public Stream {
 public:
  int available() override { return nonblocking_esp32_tinyusb_port.available(); }
  int read() override { return nonblocking_esp32_tinyusb_port.read(); }
  int peek() override { return nonblocking_esp32_tinyusb_port.peek(); }
  void flush() override { serviceUsbTerminalPort(); }
  int availableForWrite() override {
    if (esp32_tinyusb_queue_busy.test_and_set(std::memory_order_acquire)) return 0;
    const int available = canQueue()
        ? esp32_tinyusb_text_queue.availableForWrite() : 0;
    esp32_tinyusb_queue_busy.clear(std::memory_order_release);
    if (!Diagnostic) return available;
    // SerialLogLine must admit its entire <=640-byte record instead of
    // splitting it when only a few bytes of diagnostic capacity remain.
    return available >= static_cast<int>(esp32_tinyusb_functional_reserve
                                         + esp32_tinyusb_log_record_capacity)
        ? available - esp32_tinyusb_functional_reserve : 0;
  }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* data,
               size_t size) override {
    if (data == nullptr || size == 0) return 0;
    if (esp32_tinyusb_queue_busy.test_and_set(std::memory_order_acquire)) {
      noteDropped(size);
      return 0;
    }
    size_t written = 0;
    if (canQueue()) {
      const size_t available = esp32_tinyusb_text_queue.availableForWrite();
      if (!Diagnostic || (available >= esp32_tinyusb_functional_reserve
          && size <= available - esp32_tinyusb_functional_reserve)) {
        written = esp32_tinyusb_text_queue.write(data, size);
      }
    }
    if (written != size) noteDropped(size - written);
    esp32_tinyusb_queue_busy.clear(std::memory_order_release);
    return written;
  }

 private:
  bool canQueue() const {
    return canAccessEsp32TinyUsb(nullptr)
        && (Diagnostic ? isUsbLoggingEnabled()
                       : !esp32_tinyusb_terminal_discard_pending.load(
                             std::memory_order_acquire));
  }
  void noteDropped(size_t size) {
    if (!Diagnostic && !xPortInIsrContext() && tud_cdc_n_connected(0)) {
      esp32_tinyusb_terminal_dropped_bytes.fetch_add(
          static_cast<uint32_t>(size), std::memory_order_relaxed);
    }
  }
};

static Esp32TinyUsbBufferedStream<true> buffered_esp32_tinyusb_logging_port;
static Esp32TinyUsbBufferedStream<false> buffered_esp32_tinyusb_terminal_port;

static void clearEsp32TinyUsbTx(void*) {
  (void)tud_cdc_n_write_clear(0);
  if (tud_mounted() && meshEsp32TinyUsbTxPending()) {
    requestEsp32TinyUsbReenumeration();
  }
}

static void closeEsp32TinyUsbSession(bool device_boundary) {
  clearUsbLoggingClientActivity();
  esp32_tinyusb_reset_generation.fetch_add(1, std::memory_order_acq_rel);
  // Do this synchronously in TinyUSB's owner, never in its delayed Arduino
  // event handler or the main-loop cleanup (which could purge fresh RX).
  purgeEsp32TinyUsbRx();
  if (!device_boundary
      && !nonblocking_esp32_tinyusb_port.tryRunExclusive(clearEsp32TinyUsbTx)) {
    // A writer already in flight can still arm old bytes after the close.
    // Physically quarantine it instead of racing an endpoint abort/FIFO purge.
    requestEsp32TinyUsbReenumeration();
  }
}

static void purgeEsp32TinyUsbDetachedQueues(void*) {
  purgeEsp32TinyUsbRx();
  (void)tud_cdc_n_write_clear(0);
}

static void serviceEsp32TinyUsbPorts() {
  if (xPortInIsrContext()) return;
  if (esp32_tinyusb_queue_busy.test_and_set(std::memory_order_acquire)) return;
  const uint8_t reenumeration =
      esp32_tinyusb_reenumeration_state.load(std::memory_order_acquire);
  if (reenumeration != 0) {
    if (reenumeration == 1
        && (int32_t)(millis() - esp32_tinyusb_reattach_after.load(
                          std::memory_order_acquire)) >= 0
        && (!usb_logging_watchdog_detached
            || (int32_t)(millis() - usb_logging_watchdog_attach_at) >= 0)
        && nonblocking_esp32_tinyusb_port.tryRunExclusive(
            purgeEsp32TinyUsbDetachedQueues)) {
      // Publish before connect: a fast owner mount callback can run immediately.
      esp32_tinyusb_reenumeration_state.store(2, std::memory_order_release);
      (void)tud_connect();
      usb_logging_watchdog_detached = false;
    }
    esp32_tinyusb_queue_busy.clear(std::memory_order_release);
    return; // Only the fresh owner-task mount hook can reopen this gate.
  }
  // Polling also catches a physical disconnect without a CDC line-state
  // event. Events capture quick close/reopen pairs between service calls.
  const bool connected = tud_cdc_n_connected(0);
  if (esp32_tinyusb_was_connected.exchange(connected,
                                          std::memory_order_acq_rel) && !connected) {
    clearUsbLoggingClientActivity();
    esp32_tinyusb_reset_generation.fetch_add(1, std::memory_order_acq_rel);
  }
  const uint32_t generation =
      esp32_tinyusb_reset_generation.load(std::memory_order_acquire);
  if (generation != esp32_tinyusb_clean_generation.load(std::memory_order_acquire)) {
    if (!nonblocking_esp32_tinyusb_port.tryRunExclusive(clearEsp32TinyUsbTx)) {
      esp32_tinyusb_queue_busy.clear(std::memory_order_release);
      return;
    }
    esp32_tinyusb_text_queue.discardPending();
    esp32_tinyusb_terminal_reported_dropped_bytes =
        esp32_tinyusb_terminal_dropped_bytes.load(std::memory_order_relaxed);
    // The synchronous owner hook already purged old RX. Keep the new host's
    // first query, while protocol owners reset their partial parser separately.
    esp32_tinyusb_clean_generation.store(generation, std::memory_order_release);
  }
  if (esp32_tinyusb_terminal_discard_pending.exchange(false,
                                                     std::memory_order_acq_rel)) {
    esp32_tinyusb_text_queue.discardPending();
    esp32_tinyusb_terminal_reported_dropped_bytes =
        esp32_tinyusb_terminal_dropped_bytes.load(std::memory_order_relaxed);
  }
  if (canAccessEsp32TinyUsb(nullptr)) {
    esp32_tinyusb_text_queue.service();
    const uint32_t dropped =
        esp32_tinyusb_terminal_dropped_bytes.load(std::memory_order_relaxed);
    if (dropped != esp32_tinyusb_terminal_reported_dropped_bytes
        && esp32_tinyusb_text_queue.availableForWrite() >= 96) {
      char marker[96];
      const int length = snprintf(marker, sizeof(marker),
          "\r\n[USB terminal output dropped %lu bytes]\r\n",
          static_cast<unsigned long>(
              dropped - esp32_tinyusb_terminal_reported_dropped_bytes));
      if (length > 0 && static_cast<size_t>(length) < sizeof(marker)
          && esp32_tinyusb_text_queue.write(
              reinterpret_cast<const uint8_t*>(marker), length)
              == static_cast<size_t>(length)) {
        esp32_tinyusb_terminal_reported_dropped_bytes = dropped;
      }
    }
  }
  esp32_tinyusb_queue_busy.clear(std::memory_order_release);
}
#endif

#if MESH_ESP32_HWCDC_SESSION_GUARD
// Every primary HWCDC role shares one producer gate. In particular, returning
// this facade from usbLoggingPort() means a task which cached its Stream& before
// a disconnect still checks the current session at the actual write boundary.
static std::atomic<uint32_t> esp32_hwcdc_access_generation{0};
static std::atomic<uint32_t> esp32_hwcdc_allowed_generation{0};
static std::atomic<uint32_t> esp32_hwcdc_bus_reset_generation{0};
static UsbSelfResetBurstGuard esp32_hwcdc_self_reset_guard;
// Cold-start queues contain only this boot's diagnostics, never a previous
// application session. The host can enumerate and submit its first command
// while setup() is still initializing the radio/filesystem. Do not turn that
// initial enumeration into a second PHY detach which invalidates its new port.
// TX/RX event delivery must not end startup: boot diagnostics and a queued
// first command are normal before the protocol owner begins its service loop.
static std::atomic<bool> esp32_hwcdc_startup_pending{true};
// HWCDC::write() retains bytes in its software ring when the framework's raw
// five-millisecond SOF detector happens to report false. In that path the core
// does not leave SERIAL_IN_EMPTY enabled, so a valid Companion response can
// remain parked until a later request happens to kick TX. Keep requesting that
// interrupt until the framework reports actual TX progress.
static std::atomic<bool> esp32_hwcdc_tx_kick_pending{false};
static std::atomic<bool> esp32_hwcdc_tx_primed{false};
static portMUX_TYPE esp32_hwcdc_session_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t esp32_hwcdc_taken_bus_reset_generation = 0;
static bool esp32_hwcdc_event_handler_registered = false;
static std::atomic<size_t> esp32_hwcdc_tx_buffer_capacity{0};
static std::atomic<bool> esp32_hwcdc_rx_queue_ready{false};
// A busy writer or temporary allocation failure may require more than one main
// loop to purge. Keep the original detach state/generation across retries so a
// failed attempt never reattaches stale bytes or turns into a reboot loop.
static bool esp32_hwcdc_cleanup_pending = false;
static bool esp32_hwcdc_restore_pad_enabled = false;
static uint32_t esp32_hwcdc_cleanup_generation = 0;

static bool canAccessEsp32Hwcdc(void*) {
  return esp32_hwcdc_rx_queue_ready.load(std::memory_order_acquire)
      && esp32_hwcdc_allowed_generation.load(std::memory_order_acquire)
      == esp32_hwcdc_access_generation.load(std::memory_order_acquire);
}

static void handleEsp32HwcdcEvent(void*, esp_event_base_t, int32_t event_id,
                                  void*) {
  if (event_id == ARDUINO_HW_CDC_BUS_RESET_EVENT) {
    clearUsbLoggingClientActivity();
    if (esp32_hwcdc_startup_pending.load(std::memory_order_acquire)) return;
    // A host may issue several reset requests while enumerating the clean
    // transport. Suppress the complete burst; post-clean traffic below ends
    // the exemption before any later active-session reset can be ignored.
    if (esp32_hwcdc_self_reset_guard.shouldIgnoreBusReset()) {
      return;
    }

    // Quarantine reads and writes before publishing the boundary to the main
    // loop. If this callback lands just after that loop sampled the generation,
    // no old-session parser or producer can run during the intervening pass.
    portENTER_CRITICAL(&esp32_hwcdc_session_mux);
    esp32_hwcdc_access_generation.fetch_add(
        1, std::memory_order_acq_rel);
    esp32_hwcdc_tx_kick_pending.store(false, std::memory_order_release);
    esp32_hwcdc_tx_primed.store(false, std::memory_order_release);
#if MESH_HWCDC_PINNED_TX_BACKPORT
    meshEsp32HwcdcSetTxAllowed(false);
#else
    usb_serial_jtag_ll_disable_intr_mask(
        USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
#endif
    portEXIT_CRITICAL(&esp32_hwcdc_session_mux);
    Serial.setDebugOutput(false);
    esp32_hwcdc_bus_reset_generation.fetch_add(
        1, std::memory_order_acq_rel);
    return;
  }

  if (event_id == ARDUINO_HW_CDC_RX_EVENT
      || event_id == ARDUINO_HW_CDC_TX_EVENT) {
    // If no self-reset event was delivered, post-clean traffic proves that the
    // expected enumeration is over. A subsequent reset must not be ignored.
    esp32_hwcdc_self_reset_guard.notePostCleanActivity();
  }
  if (event_id == ARDUINO_HW_CDC_TX_EVENT
      && esp32_hwcdc_tx_primed.exchange(true, std::memory_order_acq_rel)) {
    // The first empty interrupt only stages the first packet. A subsequent
    // one proves the host picked up the preceding IN transfer.
    noteUsbLoggingTxComplete();
  }
}

class Esp32HwcdcSessionStream : public Stream {
public:
  int available() override {
    const int count = Serial.available();
    if (count > 0) noteActivity();
    return count;
  }

  int read() override {
    const int value = Serial.read();
    if (value >= 0) noteActivity();
    return value;
  }

  int peek() override {
    const int value = Serial.peek();
    if (value >= 0) noteActivity();
    return value;
  }

  void flush() override { Serial.flush(); }
  int availableForWrite() override { return Serial.availableForWrite(); }
  size_t write(uint8_t value) override { return write(&value, 1); }

  size_t write(const uint8_t* data, size_t size) override {
    if (data == nullptr || size == 0) return 0;
    // The shared producer guard excludes other application writers here.
    // The ISR can only free ring capacity, so one fresh capacity sample keeps
    // HWCDC::write out of its wait-for-space remainder loop.
    const int available = Serial.availableForWrite();
    if (available <= 0) return 0;
    const size_t attempt = size < static_cast<size_t>(available)
        ? size : static_cast<size_t>(available);
    noteUsbLoggingTxAttempt();
    esp32_hwcdc_tx_kick_pending.store(true, std::memory_order_release);
    return Serial.write(data, attempt);
  }

private:
  static void noteActivity() {
    esp32_hwcdc_self_reset_guard.notePostCleanActivity();
  }
};

static Esp32HwcdcSessionStream esp32_hwcdc_session_stream;

static size_t writeEsp32HwcdcOnce(void*, const uint8_t* data, size_t size) {
  return esp32_hwcdc_session_stream.write(data, size);
}

static SingleAttemptNonBlockingStream guarded_esp32_hwcdc_port(
    esp32_hwcdc_session_stream, writeEsp32HwcdcOnce, nullptr,
    canAccessEsp32Hwcdc);
static AtomicWholeRecordNonBlockingStream<11>
    guarded_esp32_hwcdc_mota_port(guarded_esp32_hwcdc_port);

// Admit each complete packet log line under the same guard as CLI replies.
// Short records can still use a smaller ring if the preferred allocation fails.
static AtomicWholeRecordNonBlockingStream<640> guarded_esp32_hwcdc_logging_port(
    guarded_esp32_hwcdc_port);

static void serviceEsp32HwcdcTxKickExclusive(void*) {
  if (!esp32_hwcdc_tx_kick_pending.load(std::memory_order_acquire)
      || !canAccessEsp32Hwcdc(nullptr)) {
    return;
  }

  // Unlike the asynchronous TX event queue, the ring's free capacity is
  // direct proof that every tracked byte has left HWCDC's software queue.
  // Sample it while the shared producer gate excludes every MeshCore writer.
  const size_t tx_capacity = esp32_hwcdc_tx_buffer_capacity.load(
      std::memory_order_acquire);
  if (tx_capacity != 0 && Serial.availableForWrite() >= tx_capacity
#if MESH_HWCDC_PINNED_TX_BACKPORT
      && !meshEsp32HwcdcTxPending()
#endif
      ) {
    esp32_hwcdc_tx_kick_pending.store(false, std::memory_order_release);
    return;
  }
  if (!Serial.isPlugged()) return;

  // Match the framework's normal connected-write kick. Serialize the final
  // generation check and register writes with BUS_RESET quarantine so a stale
  // service pass cannot re-enable TX after the session has been closed.
  portENTER_CRITICAL(&esp32_hwcdc_session_mux);
  if (esp32_hwcdc_tx_kick_pending.load(std::memory_order_acquire)
      && canAccessEsp32Hwcdc(nullptr) && Serial.isPlugged()) {
#if MESH_HWCDC_PINNED_TX_BACKPORT
    meshEsp32HwcdcKickTx();
#else
    usb_serial_jtag_ll_txfifo_flush();
    usb_serial_jtag_ll_ena_intr_mask(
        USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
#endif
  }
  portEXIT_CRITICAL(&esp32_hwcdc_session_mux);
}

static void serviceEsp32HwcdcTxKick() {
  if (!esp32_hwcdc_tx_kick_pending.load(std::memory_order_acquire)) return;
  (void)guarded_esp32_hwcdc_port.tryRunExclusive(
      serviceEsp32HwcdcTxKickExclusive);
}
#endif

#if MESH_NRF52_PRIMARY_USB_NONBLOCKING
#if defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB)
// DTR or actual host input proves a CDC0 client. Stock MeshCLI deliberately
// deasserts DTR, so it cannot be a prerequisite for receiving its first frame.
// Access still requires the application to complete the current close epoch.
// Incrementing reset_generation therefore
// closes the transport atomically; an older completion can publish only its
// older epoch and can never reopen across a newer close.
static std::atomic<uint32_t> primary_usb_reset_generation{0};
static std::atomic<uint32_t> primary_usb_allowed_generation{0};
static std::atomic<bool> primary_usb_terminal_discard_pending{false};
static std::atomic<bool> primary_usb_line_state_dtr{false};
static std::atomic<uint32_t> primary_usb_rx_generation{UINT32_MAX};
static constexpr uint32_t primary_usb_session_settle_millis = 8;
static std::atomic<uint32_t> primary_usb_reset_settle_until{0};
static std::atomic<bool> nrf52_usb_reenumeration_pending{false};
static std::atomic<uint32_t> nrf52_usb_reattach_after{0};
// Only the application service loop owns this phase. USB callbacks merely
// quarantine the pull-up and publish the atomic request below.
static bool nrf52_usb_stack_detach_issued = false;
static constexpr uint32_t nrf52_usb_detach_millis = 20;

static void requestNrf52UsbSessionReenumeration() {
  // Only an armed old-session packet needs this recovery. TinyUSB has no safe
  // nRF52 per-endpoint abort API; FIFO clears cannot retract endpoint RAM. A
  // physical detach guarantees that packet cannot enter a reopened handle,
  // and the host's fresh enumeration resets endpoint/data-toggle state. Both
  // CDC ports briefly re-enumerate whenever a close finds a pending transfer,
  // including an unread packet or the stack's trailing zero-length packet.
  if (nrf52_usb_reenumeration_pending.exchange(true,
                                               std::memory_order_acq_rel)) return;
  primary_usb_line_state_dtr.store(false, std::memory_order_release);
  primary_usb_reset_generation.fetch_add(1, std::memory_order_acq_rel);
  // Match the Nordic driver's bounded physical-disconnect operation now so
  // an armed packet cannot reach a rapidly reopened host. Do NOT call
  // TinyUSBDevice.detach() from this CDC/SOF callback: dcd_disconnect also
  // queues UNPLUGGED with an infinite FreeRTOS send timeout. A full event
  // queue would then make its sole consumer wait forever on its own queue.
  NRF_USBD->USBPULLUP = 0;
  __ISB();
  __DSB();
}

static void clearNrf52UsbTxForSession(uint8_t instance) {
  (void)tud_cdc_n_write_clear(instance);
  if (mesh_tud_cdc_n_tx_pending(instance)) {
    requestNrf52UsbSessionReenumeration();
  }
}

static void serviceNrf52UsbSessionReenumeration() {
  if (!nrf52_usb_reenumeration_pending.load(std::memory_order_acquire)) return;
  if (!nrf52_usb_stack_detach_issued) {
    // Called only from the application loop, never a TinyUSB owner callback.
    // The owner can therefore drain a full event queue while this supported
    // detach operation enqueues the reset, instead of blocking on itself.
    TinyUSBDevice.detach();
    nrf52_usb_stack_detach_issued = true;
    // Start the quiet interval at the actual stack detach, not at a possibly
    // much earlier callback request while the application loop was occupied.
    nrf52_usb_reattach_after.store(millis() + nrf52_usb_detach_millis,
                                  std::memory_order_release);
    return;
  }
  if ((int32_t)(millis() - nrf52_usb_reattach_after.load(
                       std::memory_order_acquire)) < 0
      || tud_mounted()) return;
#if defined(USE_TINYUSB)
  // A watchdog recovery can overlap an old-session endpoint quarantine. Both
  // detach deadlines must expire before either service reattaches the device.
  if (usb_logging_watchdog_detached
      && (int32_t)(millis() - usb_logging_watchdog_attach_at) < 0) return;
#endif
  // Waiting for unmount as well as the quiet interval ensures TinyUSB's owner
  // has consumed the unplug event. Never reattach with its old class state.
  TinyUSBDevice.attach();
#if defined(USE_TINYUSB)
  usb_logging_watchdog_detached = false;
#endif
  nrf52_usb_stack_detach_issued = false;
  nrf52_usb_reenumeration_pending.store(false, std::memory_order_release);
}

static uint32_t primaryUsbSessionGeneration() {
  return primary_usb_reset_generation.load(std::memory_order_acquire);
}

static bool canAccessPrimaryUsbSession(void*) {
  if (primary_usb_terminal_discard_pending.load(std::memory_order_acquire)) return false;
  const uint32_t generation = primaryUsbSessionGeneration();
  if (nrf52_usb_reenumeration_pending.load(std::memory_order_acquire)
      || !tud_mounted()
      || primary_usb_allowed_generation.load(std::memory_order_acquire)
          != generation) return false;

  if (!primary_usb_line_state_dtr.load(std::memory_order_acquire)
      && primary_usb_rx_generation.load(std::memory_order_acquire)
          != generation) {
    if (tud_cdc_n_available(0) == 0) return false;
    // Tag the proof with the epoch sampled before looking at RX. A callback
    // racing this store can invalidate it without an old reader reopening the
    // next host's session after cleanup.
    primary_usb_rx_generation.store(generation, std::memory_order_release);
  }
  return primaryUsbSessionGeneration() == generation && tud_mounted();
}

static void endPrimaryUsbHostSession(bool clear_cdc_fifos) {
  const bool previous = primary_usb_line_state_dtr.exchange(
      false, std::memory_order_acq_rel);
  const bool received = primary_usb_rx_generation.load(std::memory_order_acquire)
      == primaryUsbSessionGeneration();
  if (!previous && !received
      && !(clear_cdc_fifos && tud_cdc_n_available(0) != 0)) return;

  // Publish a short quiescence deadline before closing the generation gate.
  // FIFO-only cleanup suffices when no old IN packet is armed; otherwise
  // quarantine and physically re-enumerate rather than leaking that packet.
  // RX is purged synchronously below; bytes sent by a reopened host
  // during the settle interval remain queued until the gate reopens.
  primary_usb_reset_settle_until.store(
      millis() + primary_usb_session_settle_millis,
      std::memory_order_release);
  primary_usb_reset_generation.fetch_add(1, std::memory_order_acq_rel);
  if (!hasDedicatedUsbLoggingPort()) clearUsbLoggingClientActivity();
  if (clear_cdc_fifos) {
    // The line-state callback runs before TinyUSB resets the device. A device
    // unmount/remount boundary has already reset these class FIFOs.
    tud_cdc_n_read_flush(0);
    clearNrf52UsbTxForSession(0);
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
#if defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB)
  if (nrf52_usb_reenumeration_pending.load(std::memory_order_acquire)) return 0;
  if (instance == 0) {
    if (!canAccessPrimaryUsbSession(nullptr)) return 0;
  } else
#endif
  {
    if (!tud_cdc_n_connected(instance)) return 0;
  }

  const size_t available = tud_cdc_n_write_available(instance);
  const size_t attempt = size < available ? size : available;
  if (attempt == 0) return 0;
  if (instance == (hasDedicatedUsbLoggingPort() ? 1 : 0))
    noteUsbLoggingTxAttempt();
  return tud_cdc_n_write(instance, data, attempt);
}

static SingleAttemptNonBlockingStream nonblocking_primary_usb_companion_port(
    Serial, writeTinyUsbCdcOnce, reinterpret_cast<void*>(uintptr_t{0})
#if defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB)
    , canAccessPrimaryUsbSession
#endif
    );
// SerialMotaSource emits one contiguous request whose largest valid frame is
// 11 bytes. Preflight that entire record before the sole TinyUSB write attempt;
// a busy FIFO drops the request cleanly and lets the transaction retry later.
static AtomicWholeRecordNonBlockingStream<11>
    nonblocking_primary_usb_mota_port(
        nonblocking_primary_usb_companion_port);
#if defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB)
// Unlike Binary Companion frames, a terminal reply is produced as many Print
// calls whose return values are not consumed by the CLI. Retain one complete
// multi-line response while a normal host drains CDC0, but bound the memory and
// drop later records instead of ever waiting on an unread endpoint.
static BufferedNonBlockingWriteStream<4096>
    buffered_primary_usb_terminal_port(
        nonblocking_primary_usb_companion_port);
static uint32_t primary_usb_terminal_seen_reset_generation = 0;
static uint32_t primary_usb_terminal_taken_reset_generation = 0;

// Functional replies and diagnostics on one CDC need one chronological queue
// so their unwritten suffixes cannot overtake each other. Diagnostics leave
// most capacity available for stats/configuration replies.
static constexpr size_t nrf52_usb_functional_reserve = 3072;
class Nrf52BufferedLoggingStream : public Stream {
 public:
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  void flush() override { serviceUsbTerminalPort(); }
  int availableForWrite() override {
    const int free = buffered_primary_usb_terminal_port.availableForWrite();
    return free > static_cast<int>(nrf52_usb_functional_reserve)
        ? free - nrf52_usb_functional_reserve : 0;
  }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* data, size_t size) override {
    if (!data || !size || size > 4096 || !canAccessPrimaryUsbSession(nullptr)
        || availableForWrite() < static_cast<int>(size)) return 0;
    return buffered_primary_usb_terminal_port.write(data, size);
  }
};
static Nrf52BufferedLoggingStream buffered_primary_usb_logging_port;
#endif
#endif

static void setPlatformDebugOutputEnabled(bool enabled) {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  // The framework putc hook bypasses the common producer gate. MeshCore
  // diagnostics remain enabled through usbLoggingPort(); keep raw framework
  // bytes from racing a binary mOTA/Companion record's capacity preflight.
  (void)enabled;
  Serial.setDebugOutput(false);
#elif defined(ESP32_PLATFORM) \
    && (defined(ENABLE_USB_INTERFACE) || MESH_ESP32_HWCDC_SESSION_GUARD)
  // Arduino-ESP32 log_e()/ESP-IDF diagnostics otherwise write straight to
  // the same UART/CDC stream used by Binary Companion.
#if MESH_ESP32_HWCDC_SESSION_GUARD
  // HWCDC's framework putc hook bypasses the guarded Stream and cannot make
  // its check-plus-write atomic with a BUS_RESET callback. Keep that raw route
  // disabled on a shared protocol port; MeshCore diagnostics still use the
  // guarded usbLoggingPort() below.
  (void)enabled;
  Serial.setDebugOutput(false);
#else
  Serial.setDebugOutput(enabled);
#endif
#else
  (void)enabled;
#endif
}

#if defined(MESH_DUAL_CDC_LOGGING)
static bool dedicated_usb_logging_port_configured = false;
static bool dedicated_usb_logging_port_started = false;
static std::atomic<bool> dedicated_usb_logging_port_connected{false};
static std::atomic<bool> dedicated_usb_logging_watchdog_reset_requested{false};
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
static UsbLoggingStatsLineParser dedicated_usb_logging_stats_parser;

static void resetDedicatedUsbLoggingIdentity() {
  dedicated_usb_logging_identity_offset = 0;
}

static void resetDedicatedUsbLoggingUsbTaskState() {
  dedicated_usb_logging_usb_task_connected = false;
  resetDedicatedUsbLoggingIdentity();
  usb_task_dedicated_usb_logging_port.discardPending();
}

static void restartDedicatedUsbLoggingHostSession() {
  clearUsbLoggingClientActivity();
  dedicated_usb_logging_stats_parser.reset();
  tud_cdc_n_read_flush(1);
  // This callback and the SOF drain both run in TinyUSB's task. Gate producers
  // before clearing both queues, then publish a generation so the next SOF
  // repeats the reset after any producer which was already in flight. Clearing
  // here also prevents bytes left in TinyUSB's TX FIFO from preceding the
  // identity marker after a rapid reopen.
  dedicated_usb_logging_port_connected.store(false,
                                               std::memory_order_release);
  clearNrf52UsbTxForSession(1);
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
  if (nrf52_usb_reenumeration_pending.load(std::memory_order_acquire)) {
    dedicated_usb_logging_port_connected.store(false,
                                               std::memory_order_release);
    resetDedicatedUsbLoggingUsbTaskState();
    return;
  }
  if (dedicated_usb_logging_watchdog_reset_requested.exchange(false,
                                                              std::memory_order_acq_rel)) {
    restartDedicatedUsbLoggingHostSession();
    // Clearing an armed old packet can itself request a detach. Do not touch
    // any endpoint again in this owner tick while that recovery is pending.
    if (nrf52_usb_reenumeration_pending.load(std::memory_order_acquire)) return;
  }
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
    if (dedicated_usb_logging_usb_task_connected)
      clearUsbLoggingClientActivity();
    dedicated_usb_logging_stats_parser.reset();
    dedicated_usb_logging_port_connected.store(
        false, std::memory_order_release);
    resetDedicatedUsbLoggingUsbTaskState();
    return;
  }

  if (dedicated_usb_logging_quiet_sofs > 0) {
    dedicated_usb_logging_port_connected.store(
        false, std::memory_order_release);
    // Drop racing producer bytes. An armed old IN packet requires a physical
    // detach, not another FIFO clear or an arbitrary number of quiet SOFs.
    clearNrf52UsbTxForSession(1);
    resetDedicatedUsbLoggingUsbTaskState();
    --dedicated_usb_logging_quiet_sofs;
    return;
  }

  dedicated_usb_logging_port_connected.store(
      true, std::memory_order_release);

  // CDC1 is still logging-only, not a second configuration console. Consume
  // bounded stats polling input solely to distinguish an application from
  // a plugged cable/open port. Endpoint access stays in TinyUSB's owner.
  for (size_t budget = 0; budget < 64 && tud_cdc_n_available(1); ++budget) {
    uint8_t value;
    if (tud_cdc_n_read(1, &value, 1) != 1) break;
    if (dedicated_usb_logging_stats_parser.consume(static_cast<char>(value)))
      noteUsbLoggingStatsCommand("stats-core");
  }

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
#endif
#if defined(NRF52_PLATFORM)
// Single-CDC nRF52 roles need the same protection. In particular, BLE debug
// callbacks and packet logging write through usbLoggingPort() without going
// through MeshCore's formatted-debug helper.
  #if MESH_NRF52_PRIMARY_USB_NONBLOCKING && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
static WholeRecordNonBlockingStream<640>
    nonblocking_primary_usb_logging_port(
    buffered_primary_usb_logging_port);
  #elif MESH_NRF52_PRIMARY_USB_NONBLOCKING
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

bool isUsbDebugEnabled() {
  return usb_debug_enabled.load(std::memory_order_relaxed);
}

void setUsbDebugEnabled(bool enabled) {
  usb_debug_enabled.store(enabled, std::memory_order_relaxed);
  setPlatformDebugOutputEnabled(isUsbDebugLoggingEnabled());
}

void setUsbLoggingEnabled(bool enabled) {
  usb_logging_enabled.store(enabled, std::memory_order_relaxed);
  if (!enabled) {
    clearUsbLoggingClientActivity();
    usb_logging_tx_waiting.store(false, std::memory_order_release);
  }
  usb_logging_preference_known.store(true, std::memory_order_relaxed);
  setPlatformDebugOutputEnabled(isUsbDebugLoggingEnabled());
  // Turning diagnostics off is not a protocol switch. Retain functional
  // replies in the shared text queue; actual Binary/mOTA ownership changes
  // explicitly call discardUsbTerminalOutput() after their bounded barrier.
}

bool saveUsbLoggingBootPreference(bool enabled) {
  (void)enabled;
  return true;
}

static std::atomic<bool> usb_logging_packet_stream{false};

void configureUsbLoggingPacketStream(bool enabled) {
  usb_logging_packet_stream.store(enabled, std::memory_order_release);
}

bool isUsbLoggingPacketStream() {
  return usb_logging_packet_stream.load(std::memory_order_acquire);
}

void setUsbCompanionTxBufferCapacity(size_t capacity) {
#if MESH_ESP32_HWCDC_SESSION_GUARD
  esp32_hwcdc_tx_buffer_capacity.store(capacity, std::memory_order_release);
#else
  (void)capacity;
#endif
}

void prepareUsbLoggingPort() {
#if MESH_ESP32_HWCDC_SESSION_GUARD
  // The SDK's 256-byte default can lose the CR of an oversized ASCII setter
  // before the Companion's line-length check can reject it. Retain an
  // oversized command and exceed the terminal's 542-byte line buffer so
  // still larger single lines enter discard mode before this queue fills.
  // RX events report only admitted bytes, with no reliable overflow signal.
  // Like the TX ring, this queue must never be replaced under a live USB ISR.
  static_assert(MESH_ESP32_USB_RX_BUFFER_SIZE >= 1024,
                "HWCDC RX queue must retain oversized terminal commands");
  // On allocation failure, begin() may fall back to the unsafe SDK default.
  // Keep the application stream quarantined instead of parsing damaged input.
  esp32_hwcdc_rx_queue_ready.store(
      Serial.setRxBufferSize(MESH_ESP32_USB_RX_BUFFER_SIZE)
          == MESH_ESP32_USB_RX_BUFFER_SIZE,
      std::memory_order_release);
  // HWCDC::setTxBufferSize() deletes/recreates its ring without taking the TX
  // mutex or masking the USB ISR. Resize only during early setup, before
  // Serial.begin() creates that mutex and enables the interrupt handler.
  static const size_t usb_tx_sizes[] = {
      MESH_ESP32_USB_TX_BUFFER_SIZE, 2048, 1024, 512, 256};
  size_t capacity = 0;
  for (size_t candidate : usb_tx_sizes) {
    capacity = Serial.setTxBufferSize(candidate);
    if (capacity == candidate) break;
  }
  Serial.setTxTimeoutMs(5);
  setUsbCompanionTxBufferCapacity(capacity);
#endif
}

void beginUsbLoggingPort() {
  // setup() calls this once before role preferences are loaded and again
  // afterwards. The first call silences framework diagnostics on a protected
  // ESP32 Companion stream; setUsbLoggingEnabled() restores them only when the
  // saved settings explicitly enable both USB output and debug verbosity.
  setPlatformDebugOutputEnabled(isUsbDebugLoggingEnabled());
#if MESH_ESP32_HWCDC_SESSION_GUARD
  // If every pre-begin resize failed, begin() may have recovered by allocating
  // HWCDC's built-in ring. Discover it without replacing a live ISR-owned ring.
  if (esp32_hwcdc_tx_buffer_capacity.load(std::memory_order_acquire) == 0) {
    setUsbCompanionTxBufferCapacity(Serial.availableForWrite());
  }
  if (!esp32_hwcdc_event_handler_registered) {
    Serial.onEvent(ARDUINO_HW_CDC_ANY_EVENT, handleEsp32HwcdcEvent);
    esp32_hwcdc_event_handler_registered = true;
  }
#endif
#if defined(MESH_DUAL_CDC_LOGGING)
  if (isUsbLoggingPacketStream() || dedicated_usb_logging_port_started
      || !usb_logging_preference_known.load(std::memory_order_relaxed)
      || !isUsbLoggingEnabled()) {
    return;
  }

  // The core already started USB before setup. A host can have fetched the
  // old descriptor even while mounted() is false. Detach BEFORE mutating the
  // live configuration buffer, allow the owner to consume the unplug event,
  // then always enumerate the complete descriptor (not just mounted hosts).
  TinyUSBDevice.detach();
  delay(10);
  dedicated_usb_logging_port.begin(115200);
  dedicated_usb_logging_port_configured = true;
  dedicated_usb_logging_port_started = true;

  TinyUSBDevice.attach();
#endif
}

void serviceUsbLoggingPort() {
#if MESH_ESP32_TINYUSB_NONBLOCKING \
    || (defined(NRF52_PLATFORM) && defined(USE_TINYUSB))
  // A timed reconnect retains all descriptors and never blocks the radio loop
  // in delay(), Serial.end(), or a host-progress wait.
  if (usb_logging_watchdog_detached
      && int32_t(millis() - usb_logging_watchdog_attach_at) >= 0
#if MESH_ESP32_TINYUSB_NONBLOCKING
      && esp32_tinyusb_reenumeration_state.load(std::memory_order_acquire) == 0
#endif
#if defined(NRF52_PLATFORM)
      && !nrf52_usb_reenumeration_pending.load(std::memory_order_acquire)
      && !tud_mounted()
#endif
      ) {
    (void)tud_connect();
    usb_logging_watchdog_detached = false;
  }
#endif
#if defined(NRF52_PLATFORM) && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
  serviceNrf52UsbSessionReenumeration();
#endif
#if MESH_ESP32_TINYUSB_NONBLOCKING
  serviceEsp32TinyUsbPorts();
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  serviceEsp32HwcdcTxKick();
#endif
#if defined(MESH_DUAL_CDC_LOGGING)
  if (isUsbLoggingPacketStream()) return;
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
#if MESH_NRF52_USB_CONSOLE_COOPERATIVE && !defined(ENABLE_USB_INTERFACE)
  serviceUsbTerminalPort();
#endif
}

struct Esp32HwcdcPurgeResult {
  bool tx_empty = false;
};

#if MESH_ESP32_HWCDC_SESSION_GUARD
static bool detachEsp32HwcdcPads() {
#if ESP_IDF_VERSION_MAJOR >= 5
  // IDF 5.x replaced the C3/S3 state-preserving light-sleep helper with the
  // unified PHY query/setter pair also used by C6.
  const bool enabled = usb_serial_jtag_ll_phy_is_pad_enabled();
  usb_serial_jtag_ll_phy_enable_pad(false);
  return enabled;
#else
  // IDF 4.x (Arduino-ESP32 2.x C3/S3) supplies this combined helper.
  return usb_serial_jtag_ll_pad_backup_and_disable();
#endif
}

static void restoreEsp32HwcdcPads(bool enabled) {
#if ESP_IDF_VERSION_MAJOR >= 5
  usb_serial_jtag_ll_phy_enable_pad(enabled);
#else
  usb_serial_jtag_ll_enable_pad(enabled);
#endif
}

static void purgeEsp32HwcdcQueues(void* opaque) {
  Esp32HwcdcPurgeResult* result =
      static_cast<Esp32HwcdcPurgeResult*>(opaque);
  size_t tx_capacity = esp32_hwcdc_tx_buffer_capacity.load(
      std::memory_order_acquire);
  if (tx_capacity == 0) {
    // Never replace HWCDC's ring after begin() has enabled its ISR. Without a
    // known empty capacity we cannot prove the old session was purged, so keep
    // the transport quarantined and let the caller retry at a bounded rate.
    while (Serial.read() >= 0) {}
    return;
  }

#if MESH_HWCDC_PINNED_TX_BACKPORT
  // The producer gate and driver TX gate are closed while pads are detached.
  if (!meshEsp32HwcdcDiscardTxStash()) return;
#endif
  const uint32_t purge_started = millis();
  uint8_t flush_attempts = 0;
  do {
    // The bundled HWCDC flush removes only one contiguous BYTEBUF item, and a
    // busy TX mutex makes it return without a result. Require at least two
    // passes, then verify the configured empty capacity under a deadline.
    Serial.flush();
    ++flush_attempts;
    result->tx_empty = flush_attempts >= 2 && tx_capacity != 0
        && Serial.availableForWrite() >= tx_capacity
#if MESH_HWCDC_PINNED_TX_BACKPORT
        && !meshEsp32HwcdcTxPending()
#endif
        ;
    if (!result->tx_empty) delay(1);
  } while (!result->tx_empty
           && (uint32_t)(millis() - purge_started) < 100U);

  while (Serial.read() >= 0) {}
}

static bool beginEsp32HwcdcSessionReset() {
  if (esp32_hwcdc_cleanup_pending) return false;
  // HWCDC owns RTOS queues, a TX mutex, an ISR, and an event task. Calling
  // end()/begin() here can delete those objects while a WiFi/MQTT/diagnostic
  // producer is writing. Close the independent transport gate first. Runtime
  // logging changes remain preferences while the low-level debug route is
  // forced off by that gate.
  portENTER_CRITICAL(&esp32_hwcdc_session_mux);
  esp32_hwcdc_cleanup_generation =
      esp32_hwcdc_access_generation.fetch_add(
          1, std::memory_order_acq_rel) + 1U;
  esp32_hwcdc_tx_kick_pending.store(false, std::memory_order_release);
  esp32_hwcdc_tx_primed.store(false, std::memory_order_release);
#if MESH_HWCDC_PINNED_TX_BACKPORT
  meshEsp32HwcdcSetTxAllowed(false);
#else
  usb_serial_jtag_ll_disable_intr_mask(
      USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
#endif
  portEXIT_CRITICAL(&esp32_hwcdc_session_mux);
  setPlatformDebugOutputEnabled(false);

  esp32_hwcdc_restore_pad_enabled = detachEsp32HwcdcPads();
  esp32_hwcdc_cleanup_pending = true;
  // Always hold a real host-visible detach interval. The SOF tracker may
  // already be false when a physical-loss edge reaches the main loop.
  delay(10);
  const uint32_t detached_at = millis();
  while (Serial.isPlugged()
         && (uint32_t)(millis() - detached_at) < 50U) {
    delay(1);
  }
  return true;
}
#endif

bool resetUsbCompanionTransport() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  esp32_tinyusb_reset_generation.fetch_add(1, std::memory_order_acq_rel);
  serviceEsp32TinyUsbPorts();
  return esp32_tinyusb_clean_generation.load(std::memory_order_acquire)
      == esp32_tinyusb_reset_generation.load(std::memory_order_acquire);
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  (void)beginEsp32HwcdcSessionReset();
  // tryRunExclusive() shares the same writer guard as all primary Stream
  // facades. Retry briefly if a producer entered Serial.write() just before
  // the access gate closed; HWCDC writes are capped at five milliseconds.
  Esp32HwcdcPurgeResult result;
  const uint32_t writer_deadline = millis();
  bool ran_exclusive = false;
  do {
    ran_exclusive = guarded_esp32_hwcdc_port.tryRunExclusive(
        purgeEsp32HwcdcQueues, &result);
    if (!ran_exclusive) delay(1);
  } while (!ran_exclusive
           && (uint32_t)(millis() - writer_deadline) < 50U);

  if (!ran_exclusive || !result.tx_empty) {
    // Leave the PHY and producer gate closed. The caller retries from a later
    // loop; rebooting here can create an enumeration watchdog/restart cycle.
    return false;
  }

  esp32_hwcdc_self_reset_guard.expectSelfResetBurst();
  restoreEsp32HwcdcPads(esp32_hwcdc_restore_pad_enabled);
  // Publish only the epoch actually purged. If another BUS_RESET arrived
  // during cleanup its newer generation remains quarantined for the next pass.
  portENTER_CRITICAL(&esp32_hwcdc_session_mux);
  esp32_hwcdc_tx_kick_pending.store(false, std::memory_order_release);
  esp32_hwcdc_allowed_generation.store(
      esp32_hwcdc_cleanup_generation, std::memory_order_release);
#if MESH_HWCDC_PINNED_TX_BACKPORT
  // A newer BUS_RESET remains quarantined until its own cleanup epoch.
  if (esp32_hwcdc_cleanup_generation
      == esp32_hwcdc_access_generation.load(std::memory_order_acquire)) {
    meshEsp32HwcdcSetTxAllowed(true);
  }
#endif
  portEXIT_CRITICAL(&esp32_hwcdc_session_mux);
  esp32_hwcdc_cleanup_pending = false;
  // A concurrent runtime preference change is authoritative; never restore a
  // stale snapshot taken before the purge.
  setPlatformDebugOutputEnabled(isUsbDebugLoggingEnabled());
  return esp32_hwcdc_allowed_generation.load(std::memory_order_acquire)
      == esp32_hwcdc_access_generation.load(std::memory_order_acquire);
#endif
  return true;
}

Stream& usbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  if (!isUsbLoggingPacketStream()) {
    if (isUsbLoggingEnabled() && dedicated_usb_logging_port_started
        && dedicated_usb_logging_port_connected.load(std::memory_order_acquire)) {
      return nonblocking_dedicated_usb_logging_port;
    }
    return null_usb_logging_stream;
  }
#endif
  if (!isUsbLoggingEnabled()) return null_usb_logging_stream;
  #if MESH_ESP32_TINYUSB_NONBLOCKING
    return buffered_esp32_tinyusb_logging_port;
  #elif MESH_ESP32_HWCDC_SESSION_GUARD
    return guarded_esp32_hwcdc_logging_port;
  #elif defined(NRF52_PLATFORM)
    return nonblocking_primary_usb_logging_port;
  #else
    return Serial;
  #endif
}

Stream& usbDebugPort() {
  if (!isUsbDebugLoggingEnabled()) return null_usb_logging_stream;
  return usbLoggingPort();
}

Stream& usbCompanionPort() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  return nonblocking_esp32_tinyusb_port;
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  return guarded_esp32_hwcdc_port;
#elif MESH_NRF52_PRIMARY_USB_NONBLOCKING
  return nonblocking_primary_usb_companion_port;
#else
  return Serial;
#endif
}

#if defined(NRF52_PLATFORM) && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
bool isUsbCompanionClientConnected() {
  return canAccessPrimaryUsbSession(nullptr);
}
#endif

Stream& usbMotaPort() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  return nonblocking_esp32_tinyusb_mota_port;
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  return guarded_esp32_hwcdc_mota_port;
#elif MESH_NRF52_PRIMARY_USB_NONBLOCKING
  return nonblocking_primary_usb_mota_port;
#else
  return usbCompanionPort();
#endif
}

Stream& usbTerminalPort(bool enabled) {
  if (!enabled) return null_usb_logging_stream;
#if MESH_ESP32_TINYUSB_NONBLOCKING
  return buffered_esp32_tinyusb_terminal_port;
#elif defined(NRF52_PLATFORM) && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
  return buffered_primary_usb_terminal_port;
#else
  return usbCompanionPort();
#endif
}

Stream& usbConsolePort() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  return buffered_esp32_tinyusb_terminal_port;
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  return guarded_esp32_hwcdc_port;
#elif MESH_NRF52_USB_CONSOLE_COOPERATIVE
  return buffered_primary_usb_terminal_port;
#else
  return Serial;
#endif
}

bool canAcceptUsbConsoleCommand() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  return buffered_esp32_tinyusb_terminal_port.availableForWrite()
      >= static_cast<int>(esp32_tinyusb_functional_reserve);
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  // Leave enough room for a complete ordinary CLI response. Larger listings
  // already retain and retry a short write from their cooperative pump.
  return guarded_esp32_hwcdc_port.availableForWrite() >= 256;
#elif MESH_NRF52_USB_CONSOLE_COOPERATIVE
  return buffered_primary_usb_terminal_port.availableForWrite()
      >= static_cast<int>(nrf52_usb_functional_reserve);
#else
  return true;
#endif
}

void serviceUsbTerminalPort() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  serviceEsp32TinyUsbPorts();
#elif defined(NRF52_PLATFORM) && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
  const uint32_t reset_generation =
      primaryUsbSessionGeneration();
  if (reset_generation != primary_usb_terminal_seen_reset_generation) {
    if (!buffered_primary_usb_terminal_port.tryDiscardPending()) return;
    primary_usb_terminal_seen_reset_generation = reset_generation;
  }
  if (primary_usb_terminal_discard_pending.exchange(false, std::memory_order_acq_rel)
      && !buffered_primary_usb_terminal_port.tryDiscardPending()) {
    primary_usb_terminal_discard_pending.store(true, std::memory_order_release);
    return;
  }
  buffered_primary_usb_terminal_port.service();
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  serviceEsp32HwcdcTxKick();
#endif
}

void discardUsbTerminalOutput() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  esp32_tinyusb_terminal_discard_pending.store(true, std::memory_order_release);
  if (esp32_tinyusb_queue_busy.test_and_set(std::memory_order_acquire)) return;
  esp32_tinyusb_text_queue.discardPending();
  esp32_tinyusb_terminal_reported_dropped_bytes =
      esp32_tinyusb_terminal_dropped_bytes.load(std::memory_order_relaxed);
  esp32_tinyusb_terminal_discard_pending.store(false, std::memory_order_release);
  esp32_tinyusb_queue_busy.clear(std::memory_order_release);
#elif defined(NRF52_PLATFORM) && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
  primary_usb_terminal_discard_pending.store(true, std::memory_order_release);
  serviceUsbTerminalPort();
#endif
}

bool hasPendingUsbTerminalOutput() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  if (esp32_tinyusb_queue_busy.test_and_set(std::memory_order_acquire)) return true;
  const bool pending = esp32_tinyusb_text_queue.queuedByteCount() != 0;
  esp32_tinyusb_queue_busy.clear(std::memory_order_release);
  return pending;
#elif defined(NRF52_PLATFORM) && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
  return primary_usb_terminal_discard_pending.load(std::memory_order_acquire)
      || buffered_primary_usb_terminal_port.queuedByteCount() != 0;
#else
  return false;
#endif
}

uint32_t usbTerminalDroppedBytes() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  return esp32_tinyusb_terminal_dropped_bytes.load(std::memory_order_relaxed);
#else
  return 0;
#endif
}

bool takeUsbTerminalSessionReset() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  const uint32_t reset_generation =
      esp32_tinyusb_reset_generation.load(std::memory_order_acquire);
  if (reset_generation == esp32_tinyusb_taken_reset_generation) return false;
  esp32_tinyusb_taken_reset_generation = reset_generation;
  return true;
#elif defined(NRF52_PLATFORM) && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
  const uint32_t reset_generation =
      primaryUsbSessionGeneration();
  if (reset_generation == primary_usb_terminal_taken_reset_generation) {
    return false;
  }
  primary_usb_terminal_taken_reset_generation = reset_generation;
  return true;
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  // Every HWCDC protocol owner checks for a boundary before processing input.
  // After this first service pass, resets retain the ordinary stale-session
  // quarantine even if no host has submitted a command yet.
  esp32_hwcdc_startup_pending.store(false, std::memory_order_release);
  const uint32_t reset_generation =
      esp32_hwcdc_bus_reset_generation.load(std::memory_order_acquire);
  if (reset_generation == esp32_hwcdc_taken_bus_reset_generation) {
    return false;
  }
  esp32_hwcdc_taken_bus_reset_generation = reset_generation;
  return true;
#else
  return false;
#endif
}

#if defined(NRF52_PLATFORM) && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
static void completePrimaryUsbSessionReset(void*) {
  // This runs under the primary SingleAttempt stream's producer gate. It
  // removes a write that raced the owner-task close callback before allowing
  // any producer to address the reopened handle. RX was already purged when
  // the close/line-coding boundary was captured. Do not purge it again here:
  // hosts such as meshcli send APP_START immediately after opening the port,
  // while this generation gate is deliberately settling.
  clearNrf52UsbTxForSession(0);
  const uint32_t cleaned_generation =
      primary_usb_terminal_taken_reset_generation;
  primary_usb_allowed_generation.store(cleaned_generation,
                                       std::memory_order_release);
}
#endif

bool tryCompleteUsbTerminalSessionReset() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  serviceEsp32TinyUsbPorts();
  return esp32_tinyusb_clean_generation.load(std::memory_order_acquire)
      == esp32_tinyusb_reset_generation.load(std::memory_order_acquire);
#elif defined(NRF52_PLATFORM) && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
  serviceNrf52UsbSessionReenumeration();
  if (nrf52_usb_reenumeration_pending.load(std::memory_order_acquire)) return false;
  serviceUsbTerminalPort();
  const uint32_t generation = primaryUsbSessionGeneration();
  if (primary_usb_terminal_discard_pending.load(std::memory_order_acquire)
      || primary_usb_terminal_seen_reset_generation != generation) return false;
  // Ordinary roles call this every loop, not only on a reset. Never purge
  // an already-open endpoint: that would drop every pending reply prefix.
  if (primary_usb_allowed_generation.load(std::memory_order_acquire) == generation)
    return true;
  const uint32_t settle_until =
      primary_usb_reset_settle_until.load(std::memory_order_acquire);
  if ((int32_t)(millis() - settle_until) < 0) return false;
  if (!nonblocking_primary_usb_companion_port.tryRunExclusive(
      completePrimaryUsbSessionReset)) return false;
  return primary_usb_allowed_generation.load(std::memory_order_acquire)
      == primaryUsbSessionGeneration()
      && !nrf52_usb_reenumeration_pending.load(std::memory_order_acquire);
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  esp32_hwcdc_startup_pending.store(false, std::memory_order_release);
  if (!esp32_hwcdc_cleanup_pending
      && esp32_hwcdc_allowed_generation.load(std::memory_order_acquire)
          == esp32_hwcdc_access_generation.load(std::memory_order_acquire)) {
    return true;
  }
  return resetUsbCompanionTransport();
#else
  return true;
#endif
}

bool hasDedicatedUsbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return !isUsbLoggingPacketStream();
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
  return dedicated_usb_logging_port_configured
      != (isUsbLoggingEnabled() && hasDedicatedUsbLoggingPort());
#else
  return false;
#endif
}

const char* usbLoggingPortDescription() {
#if defined(MESH_DUAL_CDC_LOGGING)
  if (hasDedicatedUsbLoggingPort())
    return "dedicated USB CDC 1, interface 02 (Linux: *-if02; tty/COM name is host-assigned)";
#endif
  return "primary USB serial port (tty/COM name is host-assigned)";
}

UsbLoggingObservation observeUsbLoggingTransport() {
  UsbLoggingObservation result;
  result.tx_progress = usb_logging_tx_progress.load(std::memory_order_acquire);
#if MESH_ESP32_TINYUSB_NONBLOCKING
  result.supported = true;
  if (xPortInIsrContext()) return result;
  result.host_connected = tud_mounted();
  result.reader_connected = tud_cdc_n_connected(0);
  result.pending = hasPendingUsbTerminalOutput()
      || usb_logging_tx_waiting.load(std::memory_order_acquire);
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  result.supported = true;
  if (xPortInIsrContext()) return result;
  result.host_connected = Serial.isPlugged();
  result.reader_connected = result.host_connected && bool(Serial);
  const size_t capacity = esp32_hwcdc_tx_buffer_capacity.load(std::memory_order_acquire);
  result.pending = usb_logging_tx_waiting.load(std::memory_order_acquire)
      || (capacity != 0 && Serial.availableForWrite() < int(capacity))
#if MESH_HWCDC_PINNED_TX_BACKPORT
      || meshEsp32HwcdcTxPending()
#endif
      ;
#elif defined(NRF52_PLATFORM) && defined(USE_TINYUSB)
  result.supported = true;
  const uint8_t instance = hasDedicatedUsbLoggingPort() ? 1 : 0;
  result.host_connected = tud_mounted();
  result.reader_connected = tud_cdc_n_connected(instance);
  result.pending = usb_logging_tx_waiting.load(std::memory_order_acquire)
      || tud_cdc_n_write_available(instance) < CFG_TUD_CDC_TX_BUFSIZE;
#if defined(MESH_DUAL_CDC_LOGGING)
  result.pending = result.pending || (hasDedicatedUsbLoggingPort()
      ? usb_task_dedicated_usb_logging_port.queuedRecordCount() != 0
      : hasPendingUsbTerminalOutput());
#else
  result.pending = result.pending || hasPendingUsbTerminalOutput();
#endif
#endif
  return result;
}

void probeUsbLoggingTransport() {
  if (!isUsbLoggingEnabled()) return;
#if MESH_ESP32_TINYUSB_NONBLOCKING || MESH_ESP32_HWCDC_SESSION_GUARD
  if (xPortInIsrContext()) return;
#endif
  static const uint8_t record[] = "[USB watchdog] heartbeat\r\n";
#if MESH_ESP32_TINYUSB_NONBLOCKING || MESH_ESP32_HWCDC_SESSION_GUARD \
    || defined(MESH_DUAL_CDC_LOGGING) || MESH_NRF52_USB_CONSOLE_COOPERATIVE
  Stream& port = usbLoggingPort();
  if (port.availableForWrite() >= int(sizeof(record) - 1))
    (void)port.write(record, sizeof(record) - 1);
#elif defined(NRF52_PLATFORM) && defined(USE_TINYUSB)
  // Ordinary nRF52 roles may still use the framework Serial facade. Never
  // call its retrying write path from the watchdog: use one native attempt.
  if (tud_cdc_n_connected(0)
      && tud_cdc_n_write_available(0) >= sizeof(record) - 1) {
    noteUsbLoggingTxAttempt();
    (void)tud_cdc_n_write(0, record, sizeof(record) - 1);
    (void)tud_cdc_n_write_flush(0);
  }
#endif
}

UsbLoggingRecoveryResult recoverUsbLoggingTransport(uint8_t stage) {
  if (!isUsbLoggingEnabled() || (stage != 1 && stage != 2))
    return UsbLoggingRecoveryResult::Unsupported;
#if MESH_ESP32_TINYUSB_NONBLOCKING
  if (xPortInIsrContext()) return UsbLoggingRecoveryResult::Deferred;
  if (stage == 1) {
    if (!resetUsbCompanionTransport()) return UsbLoggingRecoveryResult::Deferred;
    probeUsbLoggingTransport();
  } else {
    if (!usb_logging_watchdog_detached) {
      requestEsp32TinyUsbReenumeration(true);
      usb_logging_watchdog_attach_at = millis() + 50;
      usb_logging_watchdog_detached = true;
    }
  }
  return UsbLoggingRecoveryResult::Attempted;
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  if (xPortInIsrContext()) return UsbLoggingRecoveryResult::Deferred;
  if (stage == 1) {
    // First just re-kick the existing ISR. Purge/PHY reconnection is the next
    // stage, and the bounded existing helper retains the driver/descriptors.
    serviceEsp32HwcdcTxKick();
    probeUsbLoggingTransport();
  } else {
    // Publish a boundary even when every queue is already empty. The protocol
    // owner must clear its old parser/routes before finishing this same reset;
    // reopening here could hide the detach from its host-loss debounce.
    if (beginEsp32HwcdcSessionReset()) {
      esp32_hwcdc_bus_reset_generation.fetch_add(1, std::memory_order_acq_rel);
    }
  }
  return UsbLoggingRecoveryResult::Attempted;
#elif defined(NRF52_PLATFORM) && defined(USE_TINYUSB)
  if (stage == 1) {
#if defined(MESH_DUAL_CDC_LOGGING)
    if (hasDedicatedUsbLoggingPort()) {
      // Only publish a request. FIFO/identity/queue mutation remains in
      // CDC1's existing TinyUSB-owner SOF service, never in this call.
      dedicated_usb_logging_watchdog_reset_requested.store(true, std::memory_order_release);
      dedicated_usb_logging_port_connected.store(false, std::memory_order_release);
      if (tud_cdc_n_connected(1)) tud_sof_cb_enable(true);
    } else
#endif
    {
      endPrimaryUsbHostSession(false);
      clearNrf52UsbTxForSession(0);
      probeUsbLoggingTransport();
    }
  } else if (!usb_logging_watchdog_detached) {
    if (!tud_disconnect()) return UsbLoggingRecoveryResult::Deferred;
    usb_logging_watchdog_attach_at = millis() + 50;
    usb_logging_watchdog_detached = true;
  }
  return UsbLoggingRecoveryResult::Attempted;
#else
  return UsbLoggingRecoveryResult::Unsupported;
#endif
}

bool isUsbLoggingTransportRecoveryPending() {
#if defined(NRF52_PLATFORM) && defined(USE_TINYUSB)
  return usb_logging_watchdog_detached
      || nrf52_usb_reenumeration_pending.load(std::memory_order_acquire);
#elif defined(NRF52_PLATFORM) && defined(ENABLE_USB_INTERFACE)
  return nrf52_usb_reenumeration_pending.load(std::memory_order_acquire);
#elif MESH_ESP32_TINYUSB_NONBLOCKING
  const auto state = esp32_tinyusb_reenumeration_state.load(std::memory_order_acquire);
  return usb_logging_watchdog_detached || state == 1 || state == 3;
#elif MESH_ESP32_HWCDC_SESSION_GUARD
  return esp32_hwcdc_cleanup_pending
      || esp32_hwcdc_allowed_generation.load(std::memory_order_acquire)
          != esp32_hwcdc_access_generation.load(std::memory_order_acquire);
#else
  return false;
#endif
}

}  // namespace mesh

#if MESH_ESP32_HWCDC_SESSION_GUARD
// Capture startup at the actual hardware reset, not when the finite framework
// event queue eventually delivers it. A delayed initial enumeration event
// must not be reclassified as an active-session reset after loop() has begun.
extern "C" bool meshEsp32HwcdcShouldReportBusReset() {
  return !mesh::esp32_hwcdc_startup_pending.load(std::memory_order_acquire);
}
#endif

#if defined(NRF52_PLATFORM) && defined(USE_TINYUSB)
extern "C" void meshTinyUsbLoggingTxComplete(uint8_t instance) {
  if (instance == (mesh::hasDedicatedUsbLoggingPort() ? 1 : 0))
    mesh::noteUsbLoggingTxComplete();
}
#endif

#if MESH_ESP32_TINYUSB_NONBLOCKING
// These strong bridges are called synchronously by the build-local framework
// copy, not by Arduino's delayed event queue. Do not include weak declarations
// in this translation unit or these definitions would inherit that attribute.
extern "C" void meshEsp32TinyUsbTxComplete() {
  mesh::noteUsbLoggingTxComplete();
}

extern "C" void meshEsp32TinyUsbCdcLineState(bool dtr) {
  // Never use USBCDC::dtr here: a delayed framework unplug event can overwrite
  // that member after a new host opens. These samples come from the owner.
  const bool previous = mesh::esp32_tinyusb_owner_dtr.exchange(
      dtr, std::memory_order_acq_rel);
  if (previous != dtr) mesh::closeEsp32TinyUsbSession(dtr);
}

extern "C" bool meshEsp32TinyUsbAcceptRx() {
  // A late old OUT completion while DTR is low is not fresh-host input.
  return mesh::esp32_tinyusb_reenumeration_state.load(std::memory_order_acquire) == 0
      && tud_cdc_n_connected(0);
}

extern "C" void meshEsp32TinyUsbDeviceSessionBoundary(bool mounted) {
  mesh::closeEsp32TinyUsbSession(true);
  mesh::esp32_tinyusb_owner_dtr.store(false, std::memory_order_release);
  mesh::esp32_tinyusb_was_connected.store(false, std::memory_order_release);
  if (mounted) {
    uint8_t expected = 2;
    (void)mesh::esp32_tinyusb_reenumeration_state.compare_exchange_strong(
        expected, 0, std::memory_order_acq_rel);
  }
}
#endif

#if defined(NRF52_PLATFORM) && (defined(ENABLE_USB_INTERFACE) || defined(USE_TINYUSB))
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
      // DTR rising can also reopen a prior DTR-low client's handle.
      mesh::endPrimaryUsbHostSession(true);
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
    mesh::endPrimaryUsbHostSession(true);
    mesh::primary_usb_line_state_dtr.store(
        tud_cdc_n_connected(0), std::memory_order_release);
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
