// The watchdog runtime, policy and HWCDC backend methods are production code.
// Only storage/USB/clock peripherals and application ownership are mocked.
#include <Arduino.h>
#include <FS.h>
#include <helpers/PersistentStoreFormat.h>
#include <helpers/UsbAsciiBinarySwitch.h>
#include <helpers/UsbLogging.h>
#include <helpers/UsbLoggingClientActivity.h>
#include <helpers/UsbLoggingWatchdog.h>
#include <helpers/UsbLoggingWatchdogPolicy.h>
#if defined(ENABLE_OTA)
#include <ota/OtaContext.h>
#endif
#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <functional>
#include <string>

#define portENTER_CRITICAL(value) ((void)0)
#define portEXIT_CRITICAL(value) ((void)0)
static bool in_isr = false;
static bool xPortInIsrContext() { return in_isr; }
static void delay(uint32_t duration) { mock_watchdog_millis += duration; }
using esp_event_base_t = int;
enum { ARDUINO_HW_CDC_BUS_RESET_EVENT = 1, ARDUINO_HW_CDC_RX_EVENT, ARDUINO_HW_CDC_TX_EVENT };

namespace mesh {
static bool logging_enabled = true, client_active = false;
bool isUsbLoggingEnabled() { return logging_enabled; }
bool isUsbDebugEnabled() { return false; }
bool isUsbLoggingClientActive() { return client_active; }
void clearUsbLoggingClientActivity() { client_active = false; }
static void setPlatformDebugOutputEnabled(bool) {}
void probeUsbLoggingTransport() {}  // Producer acceptance is not USB progress.

static std::atomic<size_t> esp32_hwcdc_tx_buffer_capacity{0};
static std::atomic<uint32_t> esp32_hwcdc_access_generation{0};
static std::atomic<uint32_t> esp32_hwcdc_allowed_generation{0};
static std::atomic<uint32_t> esp32_hwcdc_bus_reset_generation{0};
static uint32_t esp32_hwcdc_taken_bus_reset_generation = 0;
static std::atomic<bool> esp32_hwcdc_startup_pending{true};
static std::atomic<bool> esp32_hwcdc_rx_queue_ready{true};
static std::atomic<bool> esp32_hwcdc_tx_kick_pending{false};
static std::atomic<bool> esp32_hwcdc_tx_primed{false};
static std::atomic<uint32_t> usb_logging_tx_progress{0};
static std::atomic<bool> usb_logging_tx_waiting{false};
static bool esp32_hwcdc_cleanup_pending = false;
static bool esp32_hwcdc_restore_pad_enabled = false;
static uint32_t esp32_hwcdc_cleanup_generation = 0;
static UsbSelfResetBurstGuard esp32_hwcdc_self_reset_guard;
static bool pads_enabled = true, host_connected = false, reader_connected = false;
static size_t ring_available = 0;
static unsigned detach_calls = 0, flush_calls = 0;
static bool exclusive_busy = false, drain_on_flush = false;
static std::deque<uint8_t> host_rx;
static std::function<void()> at_restore;
static bool detachEsp32HwcdcPads() {
  ++detach_calls;
  const bool previous = pads_enabled;
  pads_enabled = false;
  return previous;
}
static void restoreEsp32HwcdcPads(bool enabled) {
  pads_enabled = enabled;
  if (at_restore) { auto callback = at_restore; at_restore = nullptr; callback(); }
}
enum { USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY = 1 };
[[maybe_unused]] static void usb_serial_jtag_ll_disable_intr_mask(unsigned) {}
[[maybe_unused]] static void usb_serial_jtag_ll_ena_intr_mask(unsigned) {}
[[maybe_unused]] static void usb_serial_jtag_ll_txfifo_flush() {}
bool meshEsp32HwcdcTxPending() { return false; }
bool meshEsp32HwcdcDiscardTxStash() { return true; }
void meshEsp32HwcdcSetTxAllowed(bool) {}
void meshEsp32HwcdcKickTx() {}
struct SerialMock {
  int read() {
    if (host_rx.empty()) return -1;
    int value = host_rx.front(); host_rx.pop_front(); return value;
  }
  int availableForWrite() { return int(ring_available); }
  bool isPlugged() { return pads_enabled && host_connected; }
  explicit operator bool() { return isPlugged() && reader_connected; }
  void setDebugOutput(bool) {}
  void flush() {
    ++flush_calls;
    if (drain_on_flush && !pads_enabled) ring_available = 4096;
  }
} Serial;
struct Exclusive {
  // No writer owns this gate in the fault reproduction. Safety deferrals are
  // tested separately through the real runtime's callback and OTA vetoes.
  bool tryRunExclusive(void (*function)(void*), void* opaque = nullptr) {
    if (exclusive_busy) return false;
    function(opaque);
    return true;
  }
} guarded_esp32_hwcdc_port;
struct Esp32HwcdcPurgeResult { bool tx_empty = false; };
@BACKEND_METHODS@
}  // namespace mesh

static bool usb_terminal_host_reset_completion_pending = false;
static uint32_t usb_terminal_host_reset_retry_at = 0;
static constexpr uint32_t USB_TRANSPORT_RESET_RETRY_MS = 1000;
static constexpr uint32_t USB_HOST_LOSS_EDGE_MS = 100;
static constexpr uint32_t USB_HOST_LOSS_GRACE_MS = 1000;
static mesh::UsbHostPresenceDebouncer usb_hwcdc_host_presence;
static char usb_terminal_line[64] = "stale USB input";
static size_t usb_terminal_line_len = sizeof("stale USB input") - 1;
static bool usb_terminal_discard_line = true, usb_logging_terminal_mode = true;
static bool usb_logging_reply_pending = true;
static mesh::UsbBinaryStartupProbe usb_binary_startup_probe;
static mesh::UsbAsciiSessionDefault usb_ascii_session_default;
struct UsbProtocolMock {
  unsigned resets = 0, passthrough_changes = 0;
  void setPassthroughMode(bool value) { assert(!value); ++passthrough_changes; }
  void resetSessionState() { ++resets; }
  uint32_t getCompletedFrameCount() const { return 7; }
} usb_serial_interface;
struct MeshMock {
  bool usb_work = true, ble_work = true, network_work = true;
  bool usb_stream = true, ble_stream = true, network_stream = true;
  bool input_stale = true, terminal_mode = true;
  unsigned input_resets = 0, serial_cancels = 0;
  void cancelPendingSerialOutput() { ++serial_cancels; }
  bool hasPendingSerialOutput() const { return false; }
  void cancelSerialResponseStream(UsbProtocolMock* route) {
    assert(route == &usb_serial_interface); usb_stream = false;
  }
  void cancelSerialOperationsForRoute(UsbProtocolMock* route) {
    assert(route == &usb_serial_interface); usb_work = false;
  }
  void resetUsbHostSessionInput() { input_stale = false; ++input_resets; }
  bool isTerminalMode() const { return terminal_mode; }
} the_mesh;
struct InterfaceManagerMock {
  bool usb_route = true, ble_route = true, network_route = true;
  void forgetReplyRouteForDisconnected(UsbProtocolMock* route) {
    assert(route == &usb_serial_interface); usb_route = false;
  }
} interface_manager;
struct BoardMock {
  bool isUsbHostConnected() { return mesh::Serial.isPlugged(); }
} board;
static unsigned terminal_leaves = 0, discarded_output = 0;
static void leaveUsbTerminalMode(bool) {
  ++terminal_leaves; the_mesh.terminal_mode = false;
}
namespace mesh { void discardUsbTerminalOutput() { ++discarded_output; } }
@COMPANION_METHODS@
static char command[64];
static bool command_overflow = false;
namespace mesh { bool canAcceptUsbConsoleCommand() { return true; } }
@ORDINARY_METHODS@

using Policy = mesh::UsbLoggingWatchdogPolicy;
using Event = mesh::UsbLoggingWatchdogEvent;
static bool can_recover(void* context) { return *static_cast<bool*>(context); }

static std::vector<uint8_t> image(uint8_t mode, uint8_t step) {
  std::vector<uint8_t> bytes(33, 0);
  bytes[0] = 'U'; bytes[1] = 'W'; bytes[2] = 2;
  bytes[4] = mode; bytes[5] = step;
  mesh::storage::writeLE32(bytes.data() + 29,
      mesh::storage::updateCRC32(0xffffffffU, bytes.data(), 29) ^ 0xffffffffU);
  return bytes;
}

struct Fixture {
  fs::FS fs;
  bool safe = true;
  // Every timeline crosses a real uint32 millis rollover before recovery.
  const uint32_t origin = UINT32_MAX - 1000;
  explicit Fixture(uint8_t mode, uint8_t step, bool zero_capacity = true,
                   bool healthy_ring = false) {
    mock_watchdog_millis = origin;
    in_isr = false;
    mesh::logging_enabled = true;
    mesh::client_active = false;
    mesh::host_connected = mesh::reader_connected = false;
    mesh::esp32_hwcdc_tx_buffer_capacity.store(zero_capacity ? 0 : 4096);
    mesh::ring_available = healthy_ring ? 4096 : 0;
    mesh::esp32_hwcdc_cleanup_pending = false;
    mesh::esp32_hwcdc_access_generation.store(0);
    mesh::esp32_hwcdc_allowed_generation.store(0);
    mesh::esp32_hwcdc_bus_reset_generation.store(0);
    mesh::esp32_hwcdc_taken_bus_reset_generation = 0;
    mesh::esp32_hwcdc_startup_pending.store(true);
    mesh::esp32_hwcdc_self_reset_guard.notePostCleanActivity();
    mesh::esp32_hwcdc_tx_kick_pending.store(false);
    mesh::usb_logging_tx_progress.store(0);
    mesh::pads_enabled = true;
    mesh::exclusive_busy = false;
    mesh::drain_on_flush = false;
    mesh::host_rx.clear();
    mesh::at_restore = nullptr;
    mesh::detach_calls = mesh::flush_calls = 0;
    usb_terminal_host_reset_completion_pending = false;
    usb_terminal_host_reset_retry_at = 0;
    usb_hwcdc_host_presence.reset();
    usb_terminal_line_len = sizeof("stale USB input") - 1;
    std::strcpy(usb_terminal_line, "stale USB input");
    usb_terminal_discard_line = usb_logging_terminal_mode = usb_logging_reply_pending = true;
    usb_serial_interface = {};
    the_mesh = {};
    interface_manager = {};
    terminal_leaves = discarded_output = 0;
    usb_binary_startup_probe.start(mock_watchdog_millis, 7);
    usb_ascii_session_default.cancel();
    command[0] = 0; command_overflow = false;
#if defined(ENABLE_OTA)
    mesh::ota::test_ota_context = nullptr;
#endif
    fs::stat_filesystem = &fs;
    fs.files["/usb_wdg"] = image(mode, step);
    mesh::loadUsbLoggingWatchdog(&fs);
  }
  bool run(uint64_t elapsed) {
    mock_watchdog_millis = uint32_t(uint64_t(origin) + elapsed);
    return mesh::serviceUsbLoggingWatchdog(can_recover, &safe);
  }
  void enter_stage2() {
    assert(!run(0));
    assert(!run(Policy::EARLY_RECOVERY_MS));
    assert(mesh::usbLoggingStatus().stage == 1);
    assert(!run(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
    std::puts("CHECK: HWCDC stage-2 detach was requested");
    const auto status = mesh::usbLoggingStatus();
    assert(status.stage == 2 && "HWCDC detach request must count as attempted");
    assert(status.recovery_count == 2 && status.persistence_ready);
    assert(status.last_event.action == Event::REENUMERATE && status.last_event.persisted);
    assert(mesh::detach_calls == 1);
    assert(mesh::esp32_hwcdc_cleanup_pending && !mesh::pads_enabled);
    assert(!mesh::canAccessEsp32Hwcdc(nullptr) && mesh::flush_calls == 0);
  }
};

static void assertFreshAppStartSurvives() {
  // A complete minimal APP_START frame: marker, little-endian length, payload.
  const std::deque<uint8_t> app_start{'<', 8, 0, 1, 3, 0, 0, 0, 0, 0, 0};
  mesh::host_rx = app_start;
  const unsigned resets = the_mesh.input_resets, detaches = mesh::detach_calls;
  const uint32_t generation = mesh::esp32_hwcdc_access_generation.load();
  for (unsigned i = 0; i < 3; ++i) {
    ++mock_watchdog_millis;
    serviceUsbTerminalHostSessionReset();
  }
  assert(mesh::host_rx == app_start && "a consumed watchdog boundary must not discard fresh APP_START");
  assert(the_mesh.input_resets == resets && mesh::detach_calls == detaches);
  assert(mesh::esp32_hwcdc_access_generation.load() == generation);
}

int main(int argc, char** argv) {
  setbuf(stdout, nullptr);
  assert(argc == 3);
  const std::string scenario = argv[1];
  const uint8_t step = uint8_t(std::strtoul(argv[2], nullptr, 10));
  assert(step == 0 || step == Policy::MAX_STEP);
  const uint64_t deadline = Policy::intervalMs(step);
  if (scenario == "zero" || scenario == "stuck" || scenario == "busy") {
    Fixture f(1, step, scenario == "zero");
    if (scenario == "busy") { mesh::ring_available = 4096; mesh::exclusive_busy = true; }
    assert(f.safe && !mesh::isUsbLoggingWatchdogUpdateActive());
    f.enter_stage2();
    assert(mesh::esp32_hwcdc_cleanup_pending && !mesh::pads_enabled);
    assert(mesh::isUsbLoggingTransportRecoveryPending());
    assert(!f.run(deadline - 1));
    assert(f.run(deadline));
    const auto status = mesh::usbLoggingStatus();
    assert(status.stage == 3 && status.reboot_count == 1);
    assert(status.backoff_step == Policy::nextStep(step) && status.persistence_ready);
    assert(status.inactive_seconds == deadline / 1000);
    assert(status.last_event.action == Event::REBOOT_REQUESTED && status.last_event.persisted);
    assert(mesh::esp32_hwcdc_cleanup_pending && !mesh::pads_enabled);
    assert(f.fs.files.at("/usb_wdg")[5] == Policy::nextStep(step));
    assert(mesh::storage::readLE32(f.fs.files.at("/usb_wdg").data() + 12) == 1);
  } else if (scenario == "healthy") {
    Fixture f(1, step, false, true);
    f.enter_stage2();
    serviceUsbTerminalHostSessionReset();
    assert(!mesh::esp32_hwcdc_cleanup_pending && mesh::pads_enabled);
    assert(mesh::flush_calls >= 2);
    mesh::host_connected = mesh::reader_connected = mesh::client_active = true;
    ++mesh::usb_logging_tx_progress;
    assert(!f.run(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS + 1000));
    assert(!f.run(deadline));
    assert(mesh::usbLoggingStatus().stage == 0 && !mesh::usbLoggingStatus().reboot_count);
  } else if (scenario == "safe-start" || scenario == "safe-deadline"
             || scenario == "ota-start" || scenario == "ota-deadline") {
    Fixture f(1, step);
    const bool after_stage2 = scenario.find("deadline") != std::string::npos;
    const bool ota_veto = scenario.find("ota") == 0;
    if (after_stage2) f.enter_stage2();
#if defined(ENABLE_OTA)
    mesh::ota::OtaContext ota;
    if (ota_veto) { ota.folder_active = true; mesh::ota::test_ota_context = &ota; }
#else
    assert(!ota_veto);
#endif
    f.safe = ota_veto;
    if (!after_stage2) assert(!f.run(0));
    const unsigned writes = f.fs.writes;
    assert(!f.run(deadline));
    assert(f.fs.writes == writes && !mesh::usbLoggingStatus().reboot_count);
    assert(mesh::usbLoggingStatus().stage == (after_stage2 ? 2 : 0));
    assert(mesh::detach_calls == (after_stage2 ? 1 : 0));
    f.safe = true;
#if defined(ENABLE_OTA)
    mesh::ota::test_ota_context = nullptr;
#endif
    // Releasing a protected operation does not bypass earlier recovery steps.
    assert(f.run(deadline + 1) == after_stage2);
  } else if (scenario == "off") {
    Fixture f(0, step);
    assert(!f.run(0) && !f.run(Policy::EARLY_RECOVERY_MS) && !f.run(deadline));
    assert(!mesh::detach_calls && !f.fs.writes && !mesh::usbLoggingStatus().reboot_count);
  } else if (scenario == "auto") {
    Fixture f(2, step);
    mesh::host_connected = mesh::reader_connected = mesh::client_active = true;
    assert(!f.run(0) && !f.run(1));
    mesh::host_connected = mesh::reader_connected = false;
    assert(!f.run(2));
    assert(!f.run(2 + Policy::EARLY_RECOVERY_MS));
    assert(!f.run(2 + Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
    assert(mesh::usbLoggingStatus().stage == 2 && mesh::detach_calls == 1);
    assert(!f.run(2 + deadline + Policy::RECOVERY_GRACE_MS));
    assert(mesh::usbLoggingStatus().watchdog_auto && !mesh::usbLoggingStatus().reboot_count);
  } else if (scenario == "journal-fail") {
    Fixture f(1, step);
    f.enter_stage2();
    f.fs.fail_write = true;
    assert(!f.run(deadline));
    const auto status = mesh::usbLoggingStatus();
    assert(!status.persistence_ready && !status.reboot_count && status.backoff_step == step);
    assert(status.last_event.action == Event::REBOOT_REQUESTED && !status.last_event.persisted);
    const unsigned writes = f.fs.writes;
    assert(!f.run(deadline + Policy::RECOVERY_GRACE_MS) && f.fs.writes == writes);
    assert(mesh::esp32_hwcdc_cleanup_pending && !mesh::pads_enabled);
  } else if (scenario == "resume-auto" || scenario == "resume-off") {
    Fixture f(1, step);
    f.enter_stage2();
    char reply[160];
    const char* command = scenario == "resume-auto" ? "set usb.watchdog auto" : "set usb.watchdog off";
    assert(mesh::handleUsbLoggingWatchdogCommand(command, reply, sizeof(reply)));
    assert(std::string(reply).find("OK - USB watchdog") == 0);
    mesh::logging_enabled = false; // Pending cleanup must finish even then.
    assert(mesh::isUsbLoggingWatchdogArmed());
    const unsigned old_detaches = mesh::detach_calls, old_flushes = mesh::flush_calls;
    const uint32_t generation = mesh::esp32_hwcdc_access_generation.load();
    // The actual Companion service has never observed a host or BUS_RESET.
    serviceUsbTerminalHostSessionReset();
    assert(usb_terminal_host_reset_completion_pending
           && "Companion must adopt a pending watchdog cleanup without a host edge");
    assert(!usb_terminal_line_len && !usb_terminal_line[0] && !usb_terminal_discard_line);
    assert(!usb_logging_terminal_mode && !usb_logging_reply_pending);
    assert(!the_mesh.input_stale && the_mesh.input_resets == 1 && terminal_leaves == 1);
    assert(!the_mesh.usb_work && !the_mesh.usb_stream && !interface_manager.usb_route);
    assert(the_mesh.ble_work && the_mesh.network_work && the_mesh.ble_stream && the_mesh.network_stream);
    assert(interface_manager.ble_route && interface_manager.network_route);
    assert(usb_serial_interface.resets == 1 && usb_serial_interface.passthrough_changes == 1);
    assert(discarded_output == 1 && mesh::esp32_hwcdc_access_generation.load() == generation);
    assert(!usb_binary_startup_probe.isActive());
    assert(!usb_ascii_session_default.shouldRestore(false, false, 7));
    mesh::esp32_hwcdc_tx_buffer_capacity.store(4096);
    mesh::ring_available = 4096;
    mock_watchdog_millis += USB_TRANSPORT_RESET_RETRY_MS - 1;
    serviceUsbTerminalHostSessionReset();
    assert(mesh::flush_calls == old_flushes && mesh::esp32_hwcdc_cleanup_pending);
    ++mock_watchdog_millis;
    serviceUsbTerminalHostSessionReset();
    assert(!usb_terminal_host_reset_completion_pending && !mesh::esp32_hwcdc_cleanup_pending);
    assert(mesh::pads_enabled && mesh::detach_calls == old_detaches);
    assert(mesh::esp32_hwcdc_access_generation.load() == mesh::esp32_hwcdc_allowed_generation.load());
    assert(!mesh::isUsbLoggingTransportRecoveryPending() && !mesh::isUsbLoggingWatchdogArmed());
    serviceUsbTerminalHostSessionReset();
    assert(mesh::detach_calls == old_detaches);
    assert(the_mesh.input_resets == 1 && usb_serial_interface.resets == 1);
    assert(mesh::esp32_hwcdc_access_generation.load() == generation);
    assert(usb_ascii_session_default.shouldRestore(true, false, 7));
  } else if (scenario.rfind("success-", 0) == 0) {
    const bool stalled = scenario.find("stall") != std::string::npos;
    const bool automatic = scenario.find("auto") != std::string::npos;
    Fixture f(automatic ? 2 : 1, step, false, !stalled);
    mesh::host_connected = mesh::reader_connected = mesh::client_active = true;
    mesh::drain_on_flush = stalled;
    usb_terminal_line[0] = 0; usb_terminal_line_len = 0;
    usb_terminal_discard_line = false; usb_logging_reply_pending = false;
    usb_binary_startup_probe.cancel();
    serviceUsbTerminalHostSessionReset();
    assert(!f.run(0));
    assert(!f.run(1)); // Auto observes the logger before its active policy edge.
    if (stalled) {
      assert(!f.run(Policy::STALL_OBSERVE_MS + 1));
      assert(mesh::usbLoggingStatus().stalled);
      strcpy(usb_terminal_line, "set password ");
      usb_terminal_line_len = strlen(usb_terminal_line);
    } else {
      mesh::client_active = false;
      assert(!f.run(2));
    }
    const uint32_t fault = stalled ? 1 : 2;
    assert(!f.run(fault + Policy::EARLY_RECOVERY_MS));
    assert(!f.run(fault + Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
    assert(mesh::esp32_hwcdc_cleanup_pending && !mesh::pads_enabled && mesh::flush_calls == 0);
    assert(the_mesh.input_resets == 0 && usb_serial_interface.resets == 0);
    const uint32_t generation = mesh::esp32_hwcdc_access_generation.load();
    serviceUsbTerminalHostSessionReset();
    assert(!mesh::esp32_hwcdc_cleanup_pending && mesh::pads_enabled);
    assert(!usb_terminal_line_len && !usb_terminal_discard_line && !the_mesh.terminal_mode);
    assert(the_mesh.input_resets == 1 && usb_serial_interface.resets == 1);
    assert(mesh::detach_calls == 1 && mesh::esp32_hwcdc_access_generation.load() == generation);
    assert(mesh::esp32_hwcdc_taken_bus_reset_generation == mesh::esp32_hwcdc_bus_reset_generation.load());
    assertFreshAppStartSurvives();
  } else if (scenario.rfind("ordinary-", 0) == 0) {
    Fixture f(1, step, false, false);
    mesh::host_connected = mesh::reader_connected = mesh::client_active = true;
    mesh::drain_on_flush = true;
    strcpy(command, "set password "); command_overflow = true;
    const auto service = scenario == "ordinary-repeater" ? service_simple_repeater
        : scenario == "ordinary-room" ? service_simple_room_server : service_simple_sensor;
    service();
    assert(!f.run(0) && !f.run(Policy::STALL_OBSERVE_MS));
    assert(mesh::usbLoggingStatus().stalled);
    assert(!f.run(Policy::EARLY_RECOVERY_MS));
    assert(!f.run(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
    assert(mesh::esp32_hwcdc_cleanup_pending && !mesh::pads_enabled);
    service();
    assert(command[0] == 0 && !mesh::esp32_hwcdc_cleanup_pending && mesh::pads_enabled);
    if (scenario != "ordinary-sensor") assert(the_mesh.serial_cancels == 1);
    if (scenario == "ordinary-repeater") assert(!command_overflow);
    strcpy(command, "new host input");
    service();
    assert(strcmp(command, "new host input") == 0 && mesh::detach_calls == 1);
  } else if (scenario == "idempotent-stage2") {
    Fixture f(1, step, false, true);
    f.enter_stage2();
    const uint32_t epoch = mesh::esp32_hwcdc_access_generation.load();
    const uint32_t boundary = mesh::esp32_hwcdc_bus_reset_generation.load();
    assert(mesh::recoverUsbLoggingTransport(2) == mesh::UsbLoggingRecoveryResult::Attempted);
    assert(mesh::esp32_hwcdc_access_generation.load() == epoch);
    assert(mesh::esp32_hwcdc_bus_reset_generation.load() == boundary && mesh::detach_calls == 1);
    serviceUsbTerminalHostSessionReset();
    assert(the_mesh.input_resets == 1 && mesh::detach_calls == 1);
    assertFreshAppStartSurvives();
  } else if (scenario == "superseded-before" || scenario == "superseded-during") {
    Fixture f(1, step, false, true);
    assert(!mesh::takeUsbTerminalSessionReset()); // Initial owner service, no synthetic edge.
    f.enter_stage2();
    const auto reset = [] {
      mesh::handleEsp32HwcdcEvent(nullptr, 0, ARDUINO_HW_CDC_RX_EVENT, nullptr);
      mesh::handleEsp32HwcdcEvent(nullptr, 0, ARDUINO_HW_CDC_BUS_RESET_EVENT, nullptr);
    };
    if (scenario == "superseded-before") reset();
    else mesh::at_restore = reset;
    serviceUsbTerminalHostSessionReset();
    assert(usb_terminal_host_reset_completion_pending
           && "a superseding hardware epoch must keep Companion pending");
    assert(!mesh::canAccessEsp32Hwcdc(nullptr) && !mesh::esp32_hwcdc_cleanup_pending);
    assert(mesh::isUsbLoggingTransportRecoveryPending());
    assert(mesh::esp32_hwcdc_allowed_generation.load() != mesh::esp32_hwcdc_access_generation.load());
    assert(the_mesh.input_resets == 1);
    char reply[160];
    assert(mesh::handleUsbLoggingWatchdogCommand("set usb.watchdog off", reply, sizeof(reply)));
    mesh::logging_enabled = false;
    assert(mesh::isUsbLoggingWatchdogArmed());
    if (scenario == "superseded-before") {
      // Both edges were consumed together; no event is left to drive retry.
      mock_watchdog_millis += USB_TRANSPORT_RESET_RETRY_MS - 1;
      serviceUsbTerminalHostSessionReset();
      assert(usb_terminal_host_reset_completion_pending && mesh::detach_calls == 1);
      ++mock_watchdog_millis;
    } else ++mock_watchdog_millis;
    serviceUsbTerminalHostSessionReset();
    assert(!usb_terminal_host_reset_completion_pending && mesh::canAccessEsp32Hwcdc(nullptr));
    assert(!mesh::isUsbLoggingTransportRecoveryPending() && !mesh::isUsbLoggingWatchdogArmed());
    assert(mesh::detach_calls == 2);
    assert(the_mesh.input_resets == (scenario == "superseded-before" ? 1U : 2U));
    assertFreshAppStartSurvives();
  } else if (scenario == "cold-start") {
    Fixture f(2, step, false, true);
    mesh::host_connected = mesh::reader_connected = true;
    assertFreshAppStartSurvives();
    assert(!mesh::esp32_hwcdc_startup_pending.load() && mesh::detach_calls == 0);
    assert(the_mesh.input_resets == 0 && mesh::flush_calls == 0);
  } else if (scenario == "isr") {
    Fixture f(1, step);
    in_isr = true;
    assert(mesh::recoverUsbLoggingTransport(2) == mesh::UsbLoggingRecoveryResult::Deferred);
    assert(!mesh::detach_calls && !mesh::esp32_hwcdc_cleanup_pending && mesh::pads_enabled);
  } else if (scenario == "logging-off") {
    Fixture f(1, step);
    mesh::logging_enabled = false;
    assert(mesh::recoverUsbLoggingTransport(2) == mesh::UsbLoggingRecoveryResult::Unsupported);
    assert(!mesh::detach_calls && !mesh::esp32_hwcdc_cleanup_pending && mesh::pads_enabled);
  } else {
    assert(false && "unknown HWCDC watchdog scenario");
  }
  std::printf("PASS: HWCDC watchdog %s step=%u\n", scenario.c_str(), unsigned(step));
}
