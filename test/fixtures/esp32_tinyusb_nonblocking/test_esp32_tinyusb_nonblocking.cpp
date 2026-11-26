#include <algorithm>
#include <cassert>
#include <iostream>
#include <string>
#include <atomic>
#include <thread>
#include "helpers/UsbLogging.h"
#include "helpers/UsbLoggingClientActivity.h"
#include "MeshCore.h"
#include "helpers/SerialPacketLog.h"

namespace mesh { bool usbDataLocked() { return false; } }

MockSerial Serial;
static bool connected = true;
static bool mounted = true, attached = true, arm_packets = false;
static std::string endpoint;
static unsigned detach_calls = 0, attach_calls = 0;
static bool auto_drain = false;
static std::string fifo;
static std::string host;
static unsigned write_calls = 0;
static unsigned flush_calls = 0;
static unsigned clear_calls = 0;
static void (*during_write)() = nullptr;
static void (*during_disconnect)() = nullptr;

extern "C" void meshEsp32TinyUsbCdcLineState(bool dtr);
extern "C" void meshEsp32TinyUsbTxComplete();
extern "C" bool meshEsp32TinyUsbAcceptRx();
extern "C" void meshEsp32TinyUsbDeviceSessionBoundary(bool mounted);
extern "C" bool meshEsp32TinyUsbTxPending() { return !endpoint.empty(); }
bool tud_mounted() { return mounted; }
bool tud_disconnect() {
  ++detach_calls;
  if (during_disconnect) during_disconnect();
  attached = false;
  connected = false;
  return true;
}
bool tud_connect() { ++attach_calls; attached = true; return true; }
void tud_cdc_n_read_flush(uint8_t instance) {
  assert(instance == 0);
  assert(mounted && "RX flush must not rearm a reset/unopened OUT endpoint");
}

bool tud_cdc_n_connected(uint8_t instance) {
  assert(!mock_isr);
  assert(instance == 0);
  return connected;
}
uint32_t tud_cdc_n_write_available(uint8_t instance) {
  assert(!mock_isr);
  assert(instance == 0);
  return 64 - fifo.size();
}
uint32_t tud_cdc_n_write(uint8_t instance, const void* data, uint32_t size) {
  assert(!mock_isr);
  assert(instance == 0);
  assert(size <= 64 - fifo.size());
  ++write_calls;
  if (during_write) during_write();
  fifo.append(static_cast<const char*>(data), size);
  return size;
}
uint32_t tud_cdc_n_write_flush(uint8_t instance) {
  assert(!mock_isr);
  assert(instance == 0);
  ++flush_calls;
  if (arm_packets) {
    if (!endpoint.empty()) return 0;
    const auto size = fifo.size();
    endpoint.swap(fifo);
    return size;
  }
  if (!auto_drain) return 0;
  const auto size = fifo.size();
  host += fifo;
  fifo.clear();
  return size;
}
bool tud_cdc_n_write_clear(uint8_t instance) {
  assert(!mock_isr);
  assert(instance == 0);
  ++clear_calls;
  fifo.clear();
  return true;
}

static size_t put(Stream& port, const std::string& value) {
  return port.write(reinterpret_cast<const uint8_t*>(value.data()), value.size());
}

#if MESH_ESP32_TINYUSB_NONBLOCKING
static void drain_all() {
  for (unsigned turn = 0; turn != 200; ++turn) {
    host += fifo;
    fifo.clear();
    mesh::serviceUsbTerminalPort();
    if (fifo.empty() && !mesh::hasPendingUsbTerminalOutput()) return;
  }
  assert(false && "finite queue should drain in bounded service calls");
}

static void fresh_session() {
  connected = false;
  meshEsp32TinyUsbCdcLineState(false); // synchronous TinyUSB owner hook
  assert(!meshEsp32TinyUsbAcceptRx()); // late old RX cannot refill Arduino queue
  connected = true;  // fast close/reopen, without an intervening service poll
  meshEsp32TinyUsbCdcLineState(true);
  mesh::serviceUsbLoggingPort();
  assert(mesh::takeUsbTerminalSessionReset());
  assert(!mesh::takeUsbTerminalSessionReset());
  assert(mesh::tryCompleteUsbTerminalSessionReset());
  fifo.clear();
  host.clear();
  auto_drain = false;
}

static void check_logging_watchdog_transport_observations_and_recovery() {
  fresh_session();
  mesh::setUsbLoggingEnabled(true);
  const auto initial = mesh::observeUsbLoggingTransport();
  assert(initial.supported && initial.host_connected && initial.reader_connected);
  assert(!initial.pending);
  assert(!mesh::isUsbLoggingClientActive()); // An open CDC is not an app poll.
  mesh::noteUsbLoggingStatsCommand("stats-core");
  assert(mesh::isUsbLoggingClientActive());
  fresh_session(); // Even a close/reopen between service calls revokes it.
  assert(!mesh::isUsbLoggingClientActive());
  mesh::noteUsbLoggingStatsCommand("stats-radio");
  mesh::setUsbLoggingEnabled(false);
  assert(!mesh::isUsbLoggingClientActive());
  mesh::setUsbLoggingEnabled(true);
  mesh::probeUsbLoggingTransport();
  const auto queued = mesh::observeUsbLoggingTransport();
  assert(queued.pending && queued.tx_progress == initial.tx_progress);
  assert(mesh::recoverUsbLoggingTransport(1) == mesh::UsbLoggingRecoveryResult::Attempted);
  // Purge and probe merely change queues; neither is host acknowledgement.
  assert(mesh::observeUsbLoggingTransport().tx_progress == initial.tx_progress);
  host += fifo; fifo.clear();
  assert(Serial.callback == nullptr); // No delayed event owns health/session state.
  meshEsp32TinyUsbTxComplete();
  const auto completed = mesh::observeUsbLoggingTransport();
  assert(completed.tx_progress == initial.tx_progress + 1);
  assert(!completed.pending);
  const auto detached_at = millis();
  const auto previous_detach = detach_calls, previous_attach = attach_calls;
  assert(mesh::recoverUsbLoggingTransport(2) == mesh::UsbLoggingRecoveryResult::Attempted);
  assert(detach_calls == previous_detach + 1 && attach_calls == previous_attach);
  assert(mesh::isUsbLoggingTransportRecoveryPending());
  assert(millis() == detached_at); // No blocking wait or descriptor rebuild.
  mounted = false;
  meshEsp32TinyUsbDeviceSessionBoundary(false);
  g_mock_millis += 49;
  mesh::serviceUsbLoggingPort();
  assert(attach_calls == previous_attach);
  ++g_mock_millis;
  mesh::serviceUsbLoggingPort();
  assert(attach_calls == previous_attach + 1 && !mesh::observeUsbLoggingTransport().host_connected);
  assert(!mesh::isUsbLoggingTransportRecoveryPending());
  assert(!meshEsp32TinyUsbAcceptRx()); // Await fresh owner mount, not queue admission.
  mock_isr = true;
  assert(mesh::recoverUsbLoggingTransport(1) == mesh::UsbLoggingRecoveryResult::Deferred);
  mesh::probeUsbLoggingTransport();
  mock_isr = false;
  mounted = connected = true;
  meshEsp32TinyUsbDeviceSessionBoundary(true);
  fresh_session();
}

static void check_native_short_writes_and_mota() {
  const unsigned before = write_calls;
  assert(put(mesh::usbCompanionPort(), std::string(200, 'B')) == 64);
  assert(write_calls == before + 1);
  assert(put(mesh::usbCompanionPort(), "more") == 0);
  const unsigned flushed = flush_calls;
  mesh::usbCompanionPort().flush();
  assert(flush_calls == flushed);
  fifo.resize(59); // only 5 bytes free: an 11-byte mOTA record must not split
  assert(put(mesh::usbMotaPort(), std::string(11, 'M')) == 0);
  assert(write_calls == before + 1);
  fifo.resize(50);
  assert(put(mesh::usbMotaPort(), std::string(11, 'M')) == 11);
  assert(write_calls == before + 2);
  fresh_session();
}

static void check_watchdog_restarts_abandoned_mount() {
  fresh_session();
  const unsigned old_detaches = detach_calls, old_attaches = attach_calls;
  assert(mesh::recoverUsbLoggingTransport(2) == mesh::UsbLoggingRecoveryResult::Attempted);
  mounted = false;
  meshEsp32TinyUsbDeviceSessionBoundary(false);
  delay(50);
  mesh::serviceUsbLoggingPort();
  assert(detach_calls == old_detaches + 1 && attach_calls == old_attaches + 1);
  assert(!meshEsp32TinyUsbAcceptRx()); // Host never sends fresh mount.
  // A later watchdog stage must not get permanently stuck behind the old
  // awaiting-mount quarantine, nor attach before its own full 50 ms deadline.
  assert(mesh::recoverUsbLoggingTransport(2) == mesh::UsbLoggingRecoveryResult::Attempted);
  assert(detach_calls == old_detaches + 2);
  delay(49);
  mesh::serviceUsbLoggingPort();
  assert(attach_calls == old_attaches + 1);
  delay(1);
  mesh::serviceUsbLoggingPort();
  assert(attach_calls == old_attaches + 2);
  assert(!meshEsp32TinyUsbAcceptRx() && !mesh::isUsbLoggingTransportRecoveryPending());
  mounted = true;
  meshEsp32TinyUsbDeviceSessionBoundary(true);
  fresh_session();
}

static void check_ordered_text_and_functional_reserve() {
  const std::string log = "RAW: " + std::string(545, 'L') + "\r\n";
  const std::string reply = "  -> " + std::string(2177, 'R') + "\r\n";
  assert(put(mesh::usbLoggingPort(), log) == log.size());
  assert(fifo.size() == 64);
  assert(mesh::hasPendingUsbTerminalOutput());
  assert(mesh::canAcceptUsbConsoleCommand());  // logs cannot starve CLI input
  assert(put(mesh::usbConsolePort(), reply) == reply.size());
  assert(!mesh::canAcceptUsbConsoleCommand()); // previous reply needs draining
  assert(mesh::usbLoggingPort().availableForWrite() == 0);
  assert(put(mesh::usbLoggingPort(), std::string(900, 'X')) == 0);
  drain_all();
  assert(host == log + reply);  // no 64-byte log/reply interleaving
  assert(mesh::canAcceptUsbConsoleCommand());
  assert(mesh::usbTerminalDroppedBytes() == 0);
  fresh_session();
}

static void check_stalled_host_and_visible_overflow() {
  const std::string fill(4096, 'F');
  assert(put(mesh::usbConsolePort(), fill) == fill.size());
  assert(put(mesh::usbConsolePort(), std::string(64, 'T')) == 64);
  assert(!mesh::canAcceptUsbConsoleCommand());
  const unsigned before = write_calls;
  for (unsigned i = 0; i != 1000; ++i) mesh::serviceUsbLoggingPort();
  assert(write_calls == before);  // no retry loop enters a full USB FIFO
  assert(put(mesh::usbConsolePort(), "!") == 0);
  assert(mesh::usbTerminalDroppedBytes() == 1);
  drain_all();
  assert(host.find(fill + std::string(64, 'T')) == 0);
  assert(host.find("[USB terminal output dropped 1 bytes]") != std::string::npos);
  fresh_session();
}

static void check_disconnect_cleanup_keeps_new_host_input() {
  assert(put(mesh::usbConsolePort(), std::string(1000, 'O')) == 1000);
  const auto before = clear_calls;
  Serial.rx_count = 12; // old owner queued commands while output was paused
  connected = false;
  meshEsp32TinyUsbCdcLineState(false);
  assert(Serial.rx_count == 0);
  connected = true;
  meshEsp32TinyUsbCdcLineState(true);
  Serial.rx_count = 4; // new host sends its first query BEFORE main-loop cleanup
  mesh::serviceUsbLoggingPort();
  assert(mesh::takeUsbTerminalSessionReset());
  assert(mesh::tryCompleteUsbTerminalSessionReset());
  host.clear();
  assert(clear_calls > before);
  assert(!mesh::hasPendingUsbTerminalOutput());
  assert(mesh::usbConsolePort().available() == 4);
  assert(mesh::usbConsolePort().peek() == 'v');
  assert(mesh::usbConsolePort().read() == 'v');
  assert(put(mesh::usbConsolePort(), "new\r\n") == 5);
  drain_all();
  assert(host == "new\r\n");
  fresh_session();
}

static void check_armed_endpoint_reenumerates_without_leaking() {
  fresh_session();
  arm_packets = true;
  const unsigned disconnected_before = detach_calls, connected_before = attach_calls;
  assert(put(mesh::usbConsolePort(), "old packet") == 10);
  assert(endpoint == "old packet" && fifo.empty());
  Serial.rx_count = 12;
  connected = false;
  meshEsp32TinyUsbCdcLineState(false);
  assert(detach_calls == disconnected_before + 1 && !attached);
  assert(Serial.rx_count == 0 && !meshEsp32TinyUsbAcceptRx());
  assert(put(mesh::usbCompanionPort(), "racing old writer") == 0);
  mesh::serviceUsbLoggingPort();
  assert(attach_calls == connected_before); // real detach interval, no wait loop
  delay(21);
  mesh::serviceUsbLoggingPort();
  assert(attach_calls == connected_before + 1 && attached);
  assert(!meshEsp32TinyUsbAcceptRx()); // retain quarantine until reset/mount
  endpoint.clear(); // actual bus reset cancels controller packet, not write_clear
  fifo.clear();
  meshEsp32TinyUsbDeviceSessionBoundary(true);
  connected = true;
  meshEsp32TinyUsbCdcLineState(true);
  Serial.rx_count = 4;
  assert(meshEsp32TinyUsbAcceptRx());
  mesh::serviceUsbLoggingPort();
  assert(mesh::takeUsbTerminalSessionReset());
  assert(mesh::tryCompleteUsbTerminalSessionReset());
  assert(mesh::usbConsolePort().available() == 4);
  arm_packets = false;
  drain_all();
  assert(host.find("old packet") == std::string::npos);
  fresh_session();
}

static void check_unmount_purges_rx_without_rearming_endpoint() {
  fresh_session();
  Serial.rx_count = 12;
  mounted = false;
  connected = false;
  meshEsp32TinyUsbDeviceSessionBoundary(false);
  assert(Serial.rx_count == 0);
  assert(!meshEsp32TinyUsbAcceptRx());
  mesh::serviceUsbLoggingPort();
  mounted = true;
  meshEsp32TinyUsbDeviceSessionBoundary(true);
  connected = true;
  meshEsp32TinyUsbCdcLineState(true);
  Serial.rx_count = 4;
  mesh::serviceUsbLoggingPort();
  assert(mesh::takeUsbTerminalSessionReset());
  assert(mesh::tryCompleteUsbTerminalSessionReset());
  assert(mesh::usbConsolePort().available() == 4);
  fresh_session();
}

static void check_logging_disable_retains_functional_reply() {
  fresh_session();
  const std::string reply(500, 'R');
  assert(put(mesh::usbConsolePort(), reply) == reply.size());
  mesh::setUsbLoggingEnabled(false);
  assert(mesh::hasPendingUsbTerminalOutput());
  assert(put(mesh::usbConsolePort(), "next reply") == 10);
  drain_all();
  assert(host == reply + "next reply");
  mesh::setUsbLoggingEnabled(true);
  fresh_session();
}

static std::atomic<bool> writer_started{false}, writer_release{false};
static std::thread* racing_writer = nullptr;
static unsigned race_attach_baseline = 0;
static void check_detach_publication_waits_for_physical_disconnect() {
  fresh_session();
  writer_started = false;
  writer_release = false;
  during_write = [] {
    writer_started.store(true, std::memory_order_release);
    while (!writer_release.load(std::memory_order_acquire)) std::this_thread::yield();
  };
  std::thread writer([] { assert(put(mesh::usbCompanionPort(), "racing old") == 10); });
  racing_writer = &writer;
  while (!writer_started.load(std::memory_order_acquire)) std::this_thread::yield();
  race_attach_baseline = attach_calls;
  during_disconnect = [] {
    // The writer finishes just after the owner's failed exclusive attempt.
    writer_release.store(true, std::memory_order_release);
    racing_writer->join();
    delay(25); // stale deadline would now allow the other core to reconnect
    mesh::serviceUsbLoggingPort();
    assert(attach_calls == race_attach_baseline);
  };
  connected = false;
  meshEsp32TinyUsbCdcLineState(false);
  during_disconnect = nullptr;
  during_write = nullptr;
  assert(!attached && !meshEsp32TinyUsbAcceptRx());
  mesh::serviceUsbLoggingPort();
  assert(attach_calls == race_attach_baseline);
  delay(21);
  mesh::serviceUsbLoggingPort();
  assert(attach_calls == race_attach_baseline + 1 && fifo.empty());
  meshEsp32TinyUsbDeviceSessionBoundary(true);
  connected = true;
  meshEsp32TinyUsbCdcLineState(true);
  mesh::serviceUsbLoggingPort();
  assert(mesh::takeUsbTerminalSessionReset());
  assert(mesh::tryCompleteUsbTerminalSessionReset());
  assert(host.find("racing old") == std::string::npos);
  fresh_session();
}

static void check_debug_formatter_and_reentrancy() {
  mesh::setUsbDebugEnabled(true);
  auto_drain = true;
  const std::string long_text(1000, 'D');
  assert(mesh::nrf52DebugPrintf("%s\n", long_text.c_str()) == 255);
  drain_all();
  assert(host.size() == 255);
  assert(host.substr(host.size() - 4) == "...\n");
  host.clear();
  during_write = [] {
    assert(mesh::nrf52DebugPrintf("must not recurse\n") == 0);
  };
  assert(mesh::nrf52DebugPrintf("outer\n") == 6);
  during_write = nullptr;
  drain_all();
  assert(host == "outer\n");
  fresh_session();
  mesh::setUsbDebugEnabled(false);
}

static void check_independent_debug_gate() {
  assert(!mesh::isUsbDebugEnabled());
  assert(mesh::isUsbLoggingEnabled());
  const auto before = write_calls;
  MESH_DEBUG_PRINTLN("default-off debug");
  assert(mesh::nrf52DebugPrintf("also quiet\n") == 0);
  assert(write_calls == before && !mesh::hasPendingUsbTerminalOutput());
  mesh::SerialLogLine<> raw;
  raw.printf("12:34:56 - 3/10/2026 U RAW: 0102");
  assert(raw.flush(mesh::usbLoggingPort()));
  drain_all();
  assert(host == "12:34:56 - 3/10/2026 U RAW: 0102\r\n");
  fresh_session();

  mesh::setUsbDebugEnabled(true);
  assert(mesh::isUsbDebugEnabled() && mesh::isUsbDebugLoggingEnabled());
  MESH_DEBUG_PRINTLN("explicit debug");
  drain_all();
  assert(host == "DEBUG: explicit debug\n");
  fresh_session();
  mesh::setUsbLoggingEnabled(false);
  assert(mesh::isUsbDebugEnabled() && !mesh::isUsbDebugLoggingEnabled());
  const auto gated = write_calls;
  MESH_DEBUG_PRINTLN("master is off");
  assert(write_calls == gated && !mesh::hasPendingUsbTerminalOutput());
  mesh::setUsbLoggingEnabled(true);
  assert(mesh::isUsbDebugLoggingEnabled());
  mesh::setUsbDebugEnabled(false);
  assert(mesh::isUsbLoggingEnabled() && !mesh::isUsbDebugLoggingEnabled());
  assert(!Serial.debug_enabled); // native raw framework output stays forbidden
}

static void check_cached_logging_gate_and_isr() {
  Stream& cached = mesh::usbLoggingPort();
  mesh::setUsbLoggingEnabled(false);
  assert(put(cached, "off") == 0);
  mesh::setUsbLoggingEnabled(true);
  mock_isr = true;
  const auto before = write_calls;
  assert(put(mesh::usbCompanionPort(), "isr") == 0);
  assert(put(mesh::usbConsolePort(), "isr") == 0);
  mesh::serviceUsbTerminalPort();
  assert(write_calls == before);
  mock_isr = false;
}

static void check_protocol_switch_cancels_text_and_overflow_marker() {
  fresh_session();
  assert(put(mesh::usbConsolePort(), std::string(5000, 'X')) == 0);
  assert(put(mesh::usbLoggingPort(), std::string(550, 'L')) == 550);
  mesh::setUsbLoggingEnabled(false);
  assert(mesh::hasPendingUsbTerminalOutput()); // no protocol switch yet
  // A protocol owner may discard application text without resetting USB. The
  // already accepted FIFO prefix stays ordered before the new binary frame.
  mesh::discardUsbTerminalOutput();
  assert(!mesh::hasPendingUsbTerminalOutput());
  host += fifo;
  fifo.clear();
  assert(put(mesh::usbCompanionPort(), "binary") == 6);
  drain_all();
  assert(host == std::string(64, 'L') + "binary");
  // In particular, service must not inject a delayed ASCII overflow notice
  // into Binary or mOTA after the owner has discarded the terminal epoch.
  host.clear();
  assert(put(mesh::usbMotaPort(), std::string(11, 'M')) == 11);
  drain_all();
  assert(host == std::string(11, 'M'));
  mesh::setUsbLoggingEnabled(true);
  fresh_session();
}
#endif

int main() {
#if MESH_ESP32_TINYUSB_NONBLOCKING
  mesh::beginUsbLoggingPort();
  mesh::beginUsbLoggingPort();
  meshEsp32TinyUsbDeviceSessionBoundary(true);
  meshEsp32TinyUsbCdcLineState(true);
  mesh::setUsbLoggingEnabled(true);
  assert(Serial.registrations == 0); // delayed Arduino events cannot purge new RX
  assert(!Serial.debug_enabled);
  mesh::serviceUsbLoggingPort();
  assert(&mesh::usbConsolePort() == &mesh::usbTerminalPort());
  check_logging_watchdog_transport_observations_and_recovery();
  check_watchdog_restarts_abandoned_mount();
  check_independent_debug_gate();
  check_native_short_writes_and_mota();
  check_ordered_text_and_functional_reserve();
  check_stalled_host_and_visible_overflow();
  check_disconnect_cleanup_keeps_new_host_input();
  check_armed_endpoint_reenumerates_without_leaking();
  check_unmount_purges_rx_without_rearming_endpoint();
  check_logging_disable_retains_functional_reply();
  check_detach_publication_waits_for_physical_disconnect();
  check_debug_formatter_and_reentrancy();
  check_cached_logging_gate_and_isr();
  check_protocol_switch_cancels_text_and_overflow_marker();
  assert(Serial.blocking_calls == 0);
#else
  assert(&mesh::usbConsolePort() == &Serial);
  assert(mesh::canAcceptUsbConsoleCommand());
  assert(mesh::usbTerminalDroppedBytes() == 0);
  assert(!mesh::observeUsbLoggingTransport().supported);
  assert(mesh::recoverUsbLoggingTransport(1) == mesh::UsbLoggingRecoveryResult::Unsupported);
  mesh::probeUsbLoggingTransport();
#endif
  std::cout << "ESP32 TinyUSB transport checks passed\n";
}
