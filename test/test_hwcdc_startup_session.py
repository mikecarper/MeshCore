#!/usr/bin/env python3
"""Execute initial HWCDC enumeration with the real ISR and session cleanup.

The RTOS event queue, USB pads and RX/ring queues are peripheral stubs. The
pinned SDK ISR, MeshCore event handler, session gate and cleanup are production
code. This specifically tests the gap between Serial.begin() and the first
application command service, including delayed framework event delivery.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import test_hwcdc_tx_backport as backport

PATCHED = backport.PATCHED
body = backport.body

ROOT = Path(__file__).resolve().parents[1]

EXTRA = r'''
#include <helpers/UsbAsciiBinarySwitch.h>
#include <iostream>
#include <string>
#define MESH_ESP32_HWCDC_SESSION_GUARD 1
#define MESH_ESP32_USB_CONSOLE_COOPERATIVE 1
#define MESH_HWCDC_PINNED_TX_BACKPORT 1
#define portENTER_CRITICAL(m) portENTER_CRITICAL_SAFE(m)
#define portEXIT_CRITICAL(m) portEXIT_CRITICAL_SAFE(m)
using esp_event_base_t = int;
static bool xPortInIsrContext() { return false; }
static uint32_t clock_ms;
static uint32_t millis() { return clock_ms; }
static void delay(uint32_t ms) { clock_ms += ms; }
static bool pads_enabled = true;
static unsigned phy_detaches = 0;
static std::deque<uint8_t> host_rx;
static bool detachEsp32HwcdcPads() {
  ++phy_detaches;
  const bool previous = pads_enabled;
  pads_enabled = plugged = false;
  return previous;
}
static void restoreEsp32HwcdcPads(bool enabled) {
  pads_enabled = plugged = enabled;
}
static void setPlatformDebugOutputEnabled(bool) {}
static bool isUsbDebugLoggingEnabled() { return false; }
static void clearUsbLoggingClientActivity() {}
static void noteUsbLoggingTxComplete() {}
static std::atomic<uint32_t> esp32_hwcdc_access_generation{0};
static std::atomic<uint32_t> esp32_hwcdc_allowed_generation{0};
static std::atomic<uint32_t> esp32_hwcdc_bus_reset_generation{0};
static std::atomic<bool> esp32_hwcdc_rx_queue_ready{true};
static uint32_t esp32_hwcdc_taken_bus_reset_generation = 0;
static mesh::UsbSelfResetBurstGuard esp32_hwcdc_self_reset_guard;
@STARTUP_STATE@
static std::atomic<bool> esp32_hwcdc_tx_kick_pending{false};
static std::atomic<bool> esp32_hwcdc_tx_primed{false};
static portMUX_TYPE esp32_hwcdc_session_mux;
static std::atomic<size_t> esp32_hwcdc_tx_buffer_capacity{4096};
static bool esp32_hwcdc_cleanup_pending = false;
static bool esp32_hwcdc_restore_pad_enabled = false;
static uint32_t esp32_hwcdc_cleanup_generation = 0;
static std::atomic<uint32_t> usb_logging_tx_progress{0};
static std::atomic<bool> usb_logging_tx_waiting{false};
struct SerialMock {
  unsigned flush_calls = 0;
  int availableForWrite() {
    unsigned waiting = 0;
    vRingbufferGetInfo(&ring, nullptr, nullptr, nullptr, nullptr, &waiting);
    return 4096 - waiting;
  }
  void flush() {
    ++flush_calls;
    if (!ring.data.empty()) ring.data.pop_front();
  }
  int read() {
    if (host_rx.empty()) return -1;
    const int byte = host_rx.front(); host_rx.pop_front(); return byte;
  }
  bool isPlugged() { return plugged; }
  operator bool() { return connected && plugged; }
  void setDebugOutput(bool) {}
} Serial;
struct Exclusive {
  bool tryRunExclusive(void (*function)(void*), void* opaque) {
    function(opaque); return true;
  }
} guarded_esp32_hwcdc_port;
struct Esp32HwcdcPurgeResult { bool tx_empty = false; };
struct UsbLoggingObservation {
  uint32_t tx_progress = 0;
  bool supported = false, host_connected = false, reader_connected = false;
  bool pending = false;
};
@FUNCTIONS@
@OWNER_RESET_HOOK@
static void deliverEvents() {
  while (!queued_events.empty()) {
    const int event = queued_events.front(); queued_events.pop_front();
    handleEsp32HwcdcEvent(nullptr, 0, event, nullptr);
  }
}
static void enumerate() {
  intr_status = USB_SERIAL_JTAG_INTR_BUS_RESET;
  hw_cdc_isr_handler(nullptr);
}
static void sendFirstCommand() {
  host_rx = {'v', 'e', 'r', '\r'};
  queued_events.push_back(ARDUINO_HW_CDC_RX_EVENT);
}
static void firstService() {
  // These are the calls the real role makes before processing commands.
  assert(!takeUsbTerminalSessionReset());
  assert(tryCompleteUsbTerminalSessionReset());
}
static void requireFirstCommand() {
  assert(phy_detaches == 0 && pads_enabled && canAccessEsp32Hwcdc(nullptr));
  assert((host_rx == std::deque<uint8_t>{'v', 'e', 'r', '\r'}));
}
int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string name = argv[1];
  reset(); plugged = true;
  if (name == "early") {
    enumerate(); sendFirstCommand(); deliverEvents(); firstService();
    requireFirstCommand();
  } else if (name == "delayed") {
    enumerate(); sendFirstCommand(); firstService(); deliverEvents();
    assert(tryCompleteUsbTerminalSessionReset()); requireFirstCommand();
  } else if (name == "boot_tx") {
    // Real ISR stages a partial boot diagnostic before the host enumerates.
    ring.data.push_back({'F', 'i', 'r', 'm', 'w', 'a', 'r', 'e', ':', '\r', '\n'});
    pulse(4); assert(mesh_hwcdc_tx_stash_len != 0 && mesh_hwcdc_fifo_pending);
    deliverEvents(); // Boot TX is not application protocol readiness.
    enumerate();
    assert(mesh_hwcdc_tx_stash_len == 0 && !mesh_hwcdc_fifo_pending);
    sendFirstCommand(); firstService(); deliverEvents();
    assert(tryCompleteUsbTerminalSessionReset()); requireFirstCommand();
  } else if (name == "boot_rx") {
    // A browser can submit input before setup finishes. An RX event alone
    // cannot make its subsequent enumeration trigger another PHY detach.
    sendFirstCommand(); deliverEvents(); enumerate();
    firstService(); deliverEvents();
    assert(tryCompleteUsbTerminalSessionReset()); requireFirstCommand();
  } else if (name == "active") {
    firstService(); assert(meshEsp32HwcdcShouldReportBusReset());
    ring.data.push_back({'o', 'l', 'd', '\r', '\n'});
    host_rx = {'o', 'l', 'd', '\r'};
    enumerate(); deliverEvents();
    assert(!canAccessEsp32Hwcdc(nullptr));
    assert(takeUsbTerminalSessionReset());
    assert(tryCompleteUsbTerminalSessionReset());
    assert(phy_detaches == 1 && host_rx.empty() && ring.data.empty());
    assert(canAccessEsp32Hwcdc(nullptr) && mesh_hwcdc_tx_allowed);
  } else if (name == "logging_only") {
    // A BLE-only Companion has no USB parser to close the startup exemption.
    // Its real logging-only service must do so before the next host reset.
    enumerate(); deliverEvents(); serviceUsbLoggingOnlySession();
    assert(meshEsp32HwcdcShouldReportBusReset());
    assert(phy_detaches == 0 && canAccessEsp32Hwcdc(nullptr));
    ring.data.push_back({'o', 'l', 'd', '\r', '\n'});
    host_rx = {'o', 'l', 'd', '\r'};
    enumerate(); deliverEvents();
    assert(!canAccessEsp32Hwcdc(nullptr));
    serviceUsbLoggingOnlySession();
    assert(phy_detaches == 1 && host_rx.empty() && ring.data.empty());
    assert(canAccessEsp32Hwcdc(nullptr));
  } else { assert(false && "Unknown startup case"); }
  std::cout << "PASS " << name << " phy_detaches=" << phy_detaches
            << " retained_rx=" << host_rx.size() << '\n';
}
'''


def application_sources(*, remove_boot_gate=False, remove_owner_hook=False,
                        boot_traffic_arms=False, keep_startup_forever=False,
                        remove_logging_only_service=False):
    source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
    signatures = (
        "static bool canAccessEsp32Hwcdc(void*)",
        "static void handleEsp32HwcdcEvent(",
        "static void serviceEsp32HwcdcTxKickExclusive(",
        "static void purgeEsp32HwcdcQueues(",
        "static bool beginEsp32HwcdcSessionReset(",
        "bool resetUsbCompanionTransport(",
        "UsbLoggingObservation observeUsbLoggingTransport(",
        "bool takeUsbTerminalSessionReset()",
        "bool tryCompleteUsbTerminalSessionReset()",
    )
    functions = "\n".join(body(source, signature) for signature in signatures)
    main = (ROOT / "examples/companion_radio/main.cpp").read_text()
    logging_service = body(main, "static void serviceUsbLoggingOnlySession()")
    # The production session methods are extracted into this flat host shell.
    logging_service = logging_service.replace("mesh::", "")
    if remove_logging_only_service:
        logging_service = logging_service.replace(
            "  (void)takeUsbTerminalSessionReset();\n", "").replace(
                "  (void)tryCompleteUsbTerminalSessionReset();\n", "")
    functions += "\n" + logging_service
    owner = body(source, 'extern "C" bool meshEsp32HwcdcShouldReportBusReset()')
    # The extracted application methods live in a flat test shell. Only their
    # namespace qualification changes; state and the actual owner hook remain.
    owner = owner.replace("mesh::esp32_hwcdc_startup_pending", "esp32_hwcdc_startup_pending")
    state = re.search(r"^static std::atomic<bool> esp32_hwcdc_startup_pending.*;",
                      source, re.MULTILINE)
    assert state is not None
    if remove_boot_gate:
        functions = functions.replace(
            "    if (esp32_hwcdc_startup_pending.load(std::memory_order_acquire)) return;\n", "")
        owner = owner.replace("!esp32_hwcdc_startup_pending.load(std::memory_order_acquire)", "true")
    if remove_owner_hook:
        owner = owner.replace("!esp32_hwcdc_startup_pending.load(std::memory_order_acquire)", "true")
    if boot_traffic_arms:
        functions = functions.replace(
            "    esp32_hwcdc_self_reset_guard.notePostCleanActivity();",
            "    esp32_hwcdc_startup_pending.store(false, std::memory_order_release);\n"
            "    esp32_hwcdc_self_reset_guard.notePostCleanActivity();")
    if keep_startup_forever:
        functions = functions.replace(
            "  esp32_hwcdc_startup_pending.store(false, std::memory_order_release);", "")
    return state[0], functions, owner


def harness(**controls):
    state, functions, owner = application_sources(**controls)
    prefix = backport.BackportTest().harness(PATCHED).split("int main(){", 1)[0]
    prefix = prefix.replace("static std::vector<unsigned> event_lengths;",
                            "static std::vector<unsigned> event_lengths;\n"
                            "static std::deque<int> queued_events;")
    prefix = prefix.replace(
        " if(event==ARDUINO_HW_CDC_TX_EVENT)event_lengths.push_back(data->tx.len);",
        " queued_events.push_back(event);\n"
        " if(event==ARDUINO_HW_CDC_TX_EVENT)event_lengths.push_back(data->tx.len);")
    return prefix + EXTRA.replace("@STARTUP_STATE@", state).replace(
        "@FUNCTIONS@", functions).replace("@OWNER_RESET_HOOK@", owner)


class HwcdcStartupSessionTests(unittest.TestCase):
    def run_case(self, case, *, expect_success=True, **controls):
        with tempfile.TemporaryDirectory(prefix="hwcdc-startup-") as directory:
            directory = Path(directory)
            cpp, binary = directory / "startup.cpp", directory / "startup"
            cpp.write_text(harness(**controls), encoding="ascii")
            compiled = subprocess.run([
                "g++", "-std=c++17", "-pthread", "-Wall", "-Wextra",
                "-Wno-unused-parameter", "-Wno-sign-compare",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-fno-pie", "-no-pie", "-I" + str(ROOT / "src"),
                str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary), case], capture_output=True,
                                    text=True, timeout=10)
            if expect_success:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0, "Negative control did not fail")

    def test_initial_enumeration_retains_command_and_active_reset_still_purges(self):
        for case in ("early", "delayed", "boot_tx", "boot_rx", "active"):
            with self.subTest(case=case):
                self.run_case(case)

    def test_logging_only_companion_ends_startup_and_cleans_active_reset(self):
        self.run_case("logging_only")

    def test_missing_logging_only_service_keeps_startup_pending_negative_control(self):
        self.run_case("logging_only", expect_success=False, remove_logging_only_service=True)

    def test_logging_only_companion_scope_uses_actual_transport_macros(self):
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()
        helper = body(main, "static void serviceUsbLoggingOnlySession()")
        loop = body(main[main.rindex("\nvoid loop()"):], "void loop()")
        self.assertLess(loop.index("serviceUsbLoggingOnlySession();"),
                        loop.index("the_mesh.loop();"))
        self.assertLess(loop.index("serviceUsbLoggingOnlySession();"),
                        loop.index("mesh::serviceUsbLoggingPort();"))
        harness = r'''
#include <helpers/UsbLogging.h>
#include <cassert>
namespace mesh {
static unsigned calls = 0;
bool takeUsbTerminalSessionReset() { ++calls; return true; }
bool tryCompleteUsbTerminalSessionReset() { ++calls; return true; }
}
@HELPER@
int main() {
  serviceUsbLoggingOnlySession();
  assert(mesh::calls == EXPECT_SERVICE * 2);
}
'''.replace("@HELPER@", helper)
        profiles = {
            "hwcdc": ["ESP32=1", "ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=1"],
            "tinyusb": ["ESP32=1", "ARDUINO_USB_MODE=0", "ARDUINO_USB_CDC_ON_BOOT=1"],
            "new_uart": ["ESP32=1", "ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=0"],
            "classic_uart": ["ESP32=1"],
            "nrf52": ["NRF52_PLATFORM=1", "USE_TINYUSB=1"],
            "rp2040": ["RP2040_PLATFORM=1"],
        }
        with tempfile.TemporaryDirectory(prefix="companion-logging-session-") as directory:
            directory = Path(directory)
            cpp = directory / "scope.cpp"
            cpp.write_text(harness, encoding="ascii")
            for profile, flags in profiles.items():
                for usb_protocol in (False, True):
                    with self.subTest(profile=profile, usb_protocol=usb_protocol):
                        binary = directory / (profile + str(usb_protocol))
                        defines = ["-DARDUINO", *("-D" + flag for flag in flags),
                                   "-DEXPECT_SERVICE=" + str(int(
                                       profile in ("hwcdc", "tinyusb") and not usb_protocol))]
                        if usb_protocol:
                            defines.append("-DENABLE_USB_INTERFACE=1")
                        compiled = subprocess.run([
                            "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-isystem", str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
                            *defines, str(cpp), "-o", str(binary)],
                            capture_output=True, text=True, timeout=60)
                        self.assertEqual(compiled.returncode, 0,
                                         compiled.stdout + compiled.stderr)
                        executed = subprocess.run([str(binary)], capture_output=True,
                                                  text=True, timeout=10)
                        self.assertEqual(executed.returncode, 0,
                                         executed.stdout + executed.stderr)

    def test_original_boot_gate_failure_negative_control(self):
        self.run_case("early", expect_success=False, remove_boot_gate=True)

    def test_delayed_owner_event_negative_control(self):
        self.run_case("delayed", expect_success=False, remove_owner_hook=True)

    def test_boot_traffic_cannot_end_startup_negative_controls(self):
        for case in ("boot_tx", "boot_rx"):
            with self.subTest(case=case):
                self.run_case(case, expect_success=False, boot_traffic_arms=True)

    def test_post_ready_quarantine_remains_required_negative_control(self):
        self.run_case("active", expect_success=False, keep_startup_forever=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
