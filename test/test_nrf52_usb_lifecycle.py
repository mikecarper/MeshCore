#!/usr/bin/env python3
"""Run actual nRF52 USB lifecycle code with independent FIFO/endpoint state."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
USB = (ROOT / "src/helpers/UsbLogging.cpp").read_text()


def function(signature):
    start = USB.index(signature)
    end = USB.index("{", start) + 1
    depth = 1
    while depth:
        depth += (USB[end] == "{") - (USB[end] == "}")
        end += 1
    return USB[start:end]


class Nrf52UsbLifecycleTest(unittest.TestCase):
    def run_native(self, source):
        compiler = shutil.which("g++")
        if compiler is None:
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "lifecycle.cpp"
            binary = Path(directory) / "lifecycle"
            cpp.write_text(source)
            compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                                       str(cpp), "-o", str(binary)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_framework_detach_queues_local_unmount_without_vbus_edge(self):
        pinned_driver = (ROOT / "test/fixtures/nrf52_usb_power_original.c").read_text()
        start = pinned_driver.index("void dcd_disconnect(")
        disconnect = pinned_driver[start:pinned_driver.index("\n}\n", start) + 3]
        self.run_native(r'''
#include <cassert>
#include <cstdint>
static bool mounted = true, vbus = true;
static unsigned owner_events = 0, reset_count = 0;
static constexpr unsigned DCD_EVENT_UNPLUGGED = 0;
struct Registers { unsigned USBPULLUP = 1; } registers;
#define NRF_USBD (&registers)
static void dcd_event_bus_signal(unsigned port, unsigned event, bool isr) {
  assert(port == 0 && event == DCD_EVENT_UNPLUGGED && !isr);
  ++owner_events; // enqueue, do not automatically fake a VBUS/unmount edge
}
@DISCONNECT@
// Actual usbd.c wrapper and Adafruit_USBD_Device.h inline entry point.
static bool tud_disconnect() { dcd_disconnect(0); return true; }
struct Device { bool detach() { return tud_disconnect(); } } TinyUSBDevice;
static void usbd_reset(unsigned port) {
  assert(port == 0); mounted = false; ++reset_count;
}
static void tud_umount_cb() {}
static void owner_task() {
  assert(owner_events == 1); --owner_events;
  // Exact DCD_EVENT_UNPLUGGED owner branch from the pinned usbd.c.
  usbd_reset(0);
  tud_umount_cb();
}
int main() {
  assert(TinyUSBDevice.detach());
  assert(vbus && registers.USBPULLUP == 0 && mounted);
  assert(owner_events == 1); // without this, recovery would wait forever
  owner_task();
  assert(vbus && !mounted && reset_count == 1);
}
'''.replace("@DISCONNECT@", disconnect))

    def test_descriptor_mutation_is_detached_even_before_mount(self):
        source = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#define MESH_DUAL_CDC_LOGGING 1
static bool attached = true, mounted = false, descriptor_fetched = false;
static unsigned detach_count = 0, attach_count = 0, descriptor_count = 1;
static unsigned delay_ms = 0;
struct Device {
  bool mounted() { return ::mounted; }
  void detach() { attached = false; ++detach_count; }
  void attach() {
    assert(descriptor_count == 2 && delay_ms >= 10);
    attached = true; ++attach_count;
    // Fresh enumeration replaces a host's old cached one-port descriptor.
    descriptor_fetched = false;
  }
} TinyUSBDevice;
static void delay(unsigned value) { assert(!attached); delay_ms += value; }
namespace mesh {
static bool usbDataLocked() { return false; }
static std::atomic<bool> usb_logging_preference_known{false};
static bool enabled = false, dedicated_usb_logging_port_started = false;
static bool dedicated_usb_logging_port_configured = false;
static bool isUsbLoggingEnabled() { return enabled; }
static bool isUsbLoggingPacketStream() { return false; }
static bool isUsbDebugLoggingEnabled() { return false; }
static void setPlatformDebugOutputEnabled(bool) {}
struct Port {
  void begin(unsigned baud) {
    assert(baud == 115200 && !attached && delay_ms >= 10);
    ++descriptor_count;
  }
} dedicated_usb_logging_port;
@BEGIN@
}
int main() {
  // No descriptor or reconnect when the preference is unknown/disabled.
  mesh::beginUsbLoggingPort();
  mesh::usb_logging_preference_known = true;
  mesh::beginUsbLoggingPort();
  assert(detach_count == 0);
  // Host fetched the old descriptor but has not SET_CONFIGURATION yet.
  descriptor_fetched = true;
  assert(!mounted);
  mesh::enabled = true;
  mesh::beginUsbLoggingPort();
  assert(detach_count == 1 && attach_count == 1 && attached);
  assert(!descriptor_fetched && descriptor_count == 2);
  assert(mesh::dedicated_usb_logging_port_configured);
  mesh::beginUsbLoggingPort();
  assert(detach_count == 1 && attach_count == 1); // idempotent
}
'''.replace("@BEGIN@", function("void beginUsbLoggingPort()"))
        self.run_native(source)

    def test_full_owner_event_queue_defers_stack_detach_but_quarantines_packet(self):
        start = USB.index("static std::atomic<uint32_t> primary_usb_reset_generation")
        gates = USB[start:USB.index("static void endPrimaryUsbHostSession(", start)]
        pinned_driver = (ROOT / "test/fixtures/nrf52_usb_power_original.c").read_text()
        start = pinned_driver.index("void dcd_disconnect(")
        disconnect = pinned_driver[start:pinned_driver.index("\n}\n", start) + 3]
        source = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#define ENABLE_USB_INTERFACE 1
static bool mounted = true, armed[2] = {false, true};
static uint32_t now = 100, fifo[2] = {0, 32};
static bool owner_callback = false;
static constexpr unsigned DCD_EVENT_UNPLUGGED = 1, queue_capacity = 4;
static unsigned events[queue_capacity] = {0, 0, 0, 0};
static unsigned queue_head = 0, queue_tail = 0, queue_count = queue_capacity;
static unsigned event_sends = 0, detach_count = 0, attach_count = 0;
static unsigned owner_resets = 0;
struct Registers { unsigned USBPULLUP = 1; } registers;
#define NRF_USBD (&registers)
static void __ISB() {}
static void __DSB() {}
static uint32_t millis() { return now; }
static bool tud_mounted() { return mounted; }
static uint32_t tud_cdc_n_available(uint8_t) { return 0; }
static void tud_cdc_n_read_flush(uint8_t) {}
static bool tud_cdc_n_write_clear(uint8_t n) { fifo[n] = 0; return true; }
static bool mesh_tud_cdc_n_tx_pending(uint8_t n) { return armed[n]; }
static void owner_dispatch_one() {
  assert(!owner_callback && queue_count != 0);
  owner_callback = true;
  const unsigned event = events[queue_tail];
  queue_tail = (queue_tail + 1) % queue_capacity;
  --queue_count;
  if (event == DCD_EVENT_UNPLUGGED) {
    mounted = false;
    armed[0] = armed[1] = false;
    ++owner_resets;
  }
  owner_callback = false;
}
static void dcd_event_bus_signal(unsigned port, unsigned event, bool isr) {
  assert(port == 0 && event == DCD_EVENT_UNPLUGGED && !isr);
  // Real osal_queue_send uses an infinite FreeRTOS timeout here. If called
  // by its sole consumer while full, it could never return. Fail immediately
  // rather than hanging the test on precisely that regression.
  assert(!owner_callback);
  ++event_sends;
  if (queue_count == queue_capacity) {
    // A blocked application sender yields to the independent USB owner,
    // which drains an existing event and releases a slot for UNPLUGGED.
    owner_dispatch_one();
  }
  assert(queue_count < queue_capacity);
  events[queue_head] = event;
  queue_head = (queue_head + 1) % queue_capacity;
  ++queue_count;
}
@DISCONNECT@
struct Device {
  void detach() { ++detach_count; dcd_disconnect(0); }
  void attach() { ++attach_count; registers.USBPULLUP = 1; }
} TinyUSBDevice;
namespace mesh {
@GATES@
}
int main() {
  // The owner is servicing a close/SOF callback while IRQ events fill every
  // available queue slot. The old data is armed in the controller, not FIFO.
  owner_callback = true;
  mesh::clearNrf52UsbTxForSession(1);
  assert(queue_count == queue_capacity && event_sends == 0);
  assert(registers.USBPULLUP == 0 && armed[1] && fifo[1] == 0);
  assert(!mesh::canAccessPrimaryUsbSession(nullptr));
  mesh::requestNrf52UsbSessionReenumeration(); // repeated callback is harmless
  assert(detach_count == 0 && event_sends == 0);
  owner_callback = false;

  // A busy application loop can service much later. Its first pass only
  // issues the stack detach; it must not reuse an expired callback deadline.
  now += 400;
  mesh::serviceNrf52UsbSessionReenumeration();
  assert(detach_count == 1 && event_sends == 1 && attach_count == 0);
  mesh::serviceNrf52UsbSessionReenumeration();
  assert(detach_count == 1 && attach_count == 0);
  now += 20;
  mesh::serviceNrf52UsbSessionReenumeration();
  assert(attach_count == 0); // owner has not consumed UNPLUGGED yet
  while (queue_count != 0) owner_dispatch_one();
  assert(owner_resets == 1 && !mounted && !armed[1]);
  mesh::serviceNrf52UsbSessionReenumeration();
  assert(attach_count == 1 && registers.USBPULLUP == 1);
  mesh::serviceNrf52UsbSessionReenumeration();
  assert(detach_count == 1 && attach_count == 1); // recovery is one-shot
}
'''
        self.run_native(source.replace("@DISCONNECT@", disconnect).replace("@GATES@", gates))

    def test_armed_logging_packet_and_racing_primary_packet_force_clean_bus(self):
        start = USB.index("static std::atomic<uint32_t> primary_usb_reset_generation")
        end_start = USB.index("static void endPrimaryUsbHostSession(", start)
        gates = USB[start:end_start] + function("static void endPrimaryUsbHostSession(")
        source = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#define ENABLE_USB_INTERFACE 1
static bool mounted = true, attached = true, armed[2] = {false, true};
static uint32_t fifo[2] = {0, 32}, now = UINT32_MAX - 10;
static unsigned detach_count = 0, attach_count = 0, reset_count = 0;
struct Registers { unsigned USBPULLUP = 1; } registers;
#define NRF_USBD (&registers)
static void __ISB() {}
static void __DSB() {}
static uint32_t millis() { return now; }
static bool tud_mounted() { return mounted; }
static uint32_t tud_cdc_n_available(uint8_t) { return 0; }
static void tud_cdc_n_read_flush(uint8_t) {}
static bool tud_cdc_n_write_clear(uint8_t n) { fifo[n] = 0; return true; }
static bool mesh_tud_cdc_n_tx_pending(uint8_t n) { return armed[n]; }
struct Device {
  void detach() { ++detach_count; attached = false; registers.USBPULLUP = 0; }
  void attach() { ++attach_count; attached = true; registers.USBPULLUP = 1; }
} TinyUSBDevice;
namespace mesh {
static void clearUsbLoggingClientActivity() {}
static bool isUsbLoggingPacketStream() { return false; }
@DEDICATED@
@GATES@
static std::atomic<bool> dedicated_usb_logging_port_connected{true};
static std::atomic<uint32_t> dedicated_usb_logging_reset_generation{0};
static uint8_t dedicated_usb_logging_quiet_sofs = 0;
static constexpr uint8_t dedicated_usb_logging_host_settle_sofs = 50;
struct StatsParser { void reset() {} } dedicated_usb_logging_stats_parser;
static uint32_t primary_usb_terminal_taken_reset_generation = 0;
static void resetDedicatedUsbLoggingUsbTaskState() { ++reset_count; }
@RESTART@
@COMPLETE@
}
int main() {
  // CDC1 old data is in endpoint RAM, not the software FIFO. No 50-SOF
  // waiting/clearing policy can retract it without re-enumeration.
  mesh::restartDedicatedUsbLoggingHostSession();
  assert(fifo[1] == 0 && armed[1] && registers.USBPULLUP == 0);
  assert(attached && detach_count == 0); // no owner-task event send
  assert(!mesh::dedicated_usb_logging_port_connected && reset_count == 1);
  assert(!mesh::canAccessPrimaryUsbSession(nullptr)); // both ports gated
  mesh::serviceNrf52UsbSessionReenumeration();
  assert(!attached && detach_count == 1);
  mesh::restartDedicatedUsbLoggingHostSession();
  assert(detach_count == 1); // repeated control requests do not reset timer
  now += 19; mounted = false; armed[1] = false;
  mesh::serviceNrf52UsbSessionReenumeration();
  assert(attach_count == 0); // wrap-safe bounded detach interval
  ++now;
  mesh::serviceNrf52UsbSessionReenumeration();
  assert(attach_count == 1 && attached);
  mesh::serviceNrf52UsbSessionReenumeration();
  assert(attach_count == 1);
  // The final exclusive purge also catches an old producer that submitted
  // a primary IN packet after the owner callback's first FIFO clear.
  mounted = true; armed[0] = true; fifo[0] = 16;
  mesh::primary_usb_terminal_taken_reset_generation =
      mesh::primaryUsbSessionGeneration();
  mesh::completePrimaryUsbSessionReset(nullptr);
  assert(fifo[0] == 0 && armed[0] && registers.USBPULLUP == 0);
  assert(attached && detach_count == 1);
  assert(!mesh::canAccessPrimaryUsbSession(nullptr));
  mesh::serviceNrf52UsbSessionReenumeration();
  assert(!attached && detach_count == 2);
}
'''
        source = source.replace("@GATES@", gates)
        source = source.replace("@DEDICATED@", function("bool hasDedicatedUsbLoggingPort("))
        source = source.replace("@RESTART@", function("static void restartDedicatedUsbLoggingHostSession()"))
        source = source.replace("@COMPLETE@", function("static void completePrimaryUsbSessionReset("))
        self.run_native(source)


if __name__ == "__main__":
    unittest.main()
