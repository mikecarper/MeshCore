#!/usr/bin/env python3
"""Execute the firmware's startup and host-reset functions on each USB type."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "examples/companion_radio/main.cpp").read_text()


def function(signature):
    start = MAIN.index(signature)
    end = MAIN.index("{", start) + 1
    depth = 1
    while depth:
        depth += (MAIN[end] == "{") - (MAIN[end] == "}")
        end += 1
    return MAIN[start:end]


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include "helpers/UsbAsciiBinarySwitch.h"
#define COMPANION_FEATURE_USB_MOTA_SOURCE 1
#define COMPANION_FEATURE_NETWORK_TERMINAL 1
#define MESH_USB_LOGGING_AVAILABLE 1
static bool data_connected = true, reset_event = false, transport_ready = true;
static bool network_active = false, logging_enabled = false, dedicated_logging = false;
static uint32_t clock_ms = 2000;
static unsigned cancelled_routes = 0, entered_ascii = 0;
static uint32_t millis() { return clock_ms; }
struct Mesh {
  bool terminal = false, stale_input = true;
  bool isTerminalMode() const { return terminal; }
  void resetUsbHostSessionInput() { stale_input = false; }
} the_mesh;
struct SerialInterface {
  bool passthrough = false, old_frame = true;
  uint32_t completed = 7;
  void setPassthroughMode(bool value) { passthrough = value; }
  void resetSessionState() { old_frame = false; }
  uint32_t getCompletedFrameCount() const { return completed; }
} usb_serial_interface;
struct Board { bool isUsbHostConnected() const { return data_connected; } } board;
static bool usb_mota_mode = false, usb_logging_terminal_mode = false;
static bool usb_host_session_connected = false, usb_terminal_discard_line = true;
static bool usb_terminal_host_reset_completion_pending = false;
static uint32_t usb_terminal_host_reset_retry_at = 0;
static unsigned line_length = 5;
static mesh::UsbBinaryStartupProbe usb_binary_startup_probe;
static mesh::UsbAsciiSessionDefault usb_ascii_session_default;
static mesh::UsbHostPresenceDebouncer usb_hwcdc_host_presence;
static constexpr unsigned USB_TRANSPORT_RESET_RETRY_MS = 1000;
static constexpr unsigned USB_HOST_LOSS_EDGE_MS = 100, USB_HOST_LOSS_GRACE_MS = 2000;
namespace mesh {
void discardUsbTerminalOutput() {}
bool takeUsbTerminalSessionReset() { bool value = reset_event; reset_event = false; return value; }
bool resetUsbCompanionTransport() { return transport_ready; }
bool tryCompleteUsbTerminalSessionReset() { return transport_ready; }
bool hasDedicatedUsbLoggingPort() { return dedicated_logging; }
bool isUsbLoggingEnabled() { return logging_enabled; }
}
static bool isUsbTerminalDataConnected() { return data_connected; }
static bool isNetworkTerminalActive() { return network_active; }
static void clearUsbTerminalLine() { line_length = 0; }
static void cancelUsbSerialOperations() { ++cancelled_routes; }
static void leaveUsbMotaMode(bool) { usb_mota_mode = false; }
static void leaveUsbTerminalMode(bool) { the_mesh.terminal = false; }
static void enterUsbTerminalMode() {
  assert(transport_ready && !network_active);
  ++entered_ascii;
  the_mesh.terminal = true;
  usb_serial_interface.passthrough = true;
  usb_binary_startup_probe.cancel();
  usb_ascii_session_default.cancel();
  usb_logging_terminal_mode = false;
}
static void enterUsbLoggingTerminalMode() {
  enterUsbTerminalMode();
  usb_logging_terminal_mode = true;
}
@FUNCTIONS@
static void service() {
  serviceUsbTerminalHostSessionReset();
  serviceUsbAsciiSessionDefault();
}
static void boundary(bool event = true) {
  reset_event = event;
  data_connected = false;
  service();
}
int main() {
  // No Full macro is required: ordinary USB Companions also boot in ASCII.
  beginUsbDefaultSession();
  assert(the_mesh.terminal && usb_serial_interface.passthrough);
  service(); // observe the open host before it can close
  for (bool binary : {false, true}) {
    the_mesh.terminal = !binary;
    usb_serial_interface.passthrough = !binary;
    the_mesh.stale_input = true;
    usb_serial_interface.old_frame = true;
    line_length = 5;
    usb_binary_startup_probe.start(clock_ms, usb_serial_interface.completed);
    boundary();
    assert(the_mesh.terminal && usb_serial_interface.passthrough);
    assert(!the_mesh.stale_input && !usb_serial_interface.old_frame);
    assert(line_length == 0 && !usb_binary_startup_probe.isActive());
    data_connected = true;
    service();
  }
  // Close/reopen can happen entirely between polls; the generation event wins.
  the_mesh.terminal = false;
  reset_event = true;
  data_connected = true;
  service();
  assert(the_mesh.terminal);
  // Do not expose ASCII while a native USB queue is still being purged.
  transport_ready = false;
  the_mesh.terminal = false;
  boundary();
  assert(!the_mesh.terminal && usb_terminal_host_reset_completion_pending);
  transport_ready = true;
  clock_ms += USB_TRANSPORT_RESET_RETRY_MS;
  service();
  assert(the_mesh.terminal && !usb_terminal_host_reset_completion_pending);
  // A reset ends mOTA's exclusive USB attachment, but leaves network CLI alone.
  usb_mota_mode = true;
  the_mesh.terminal = false;
  network_active = true;
  boundary();
  assert(!usb_mota_mode && !the_mesh.terminal);
  network_active = false;
  service();
  assert(the_mesh.terminal);
  // A fresh binary client during a network session keeps its selected mode.
  the_mesh.terminal = false;
  network_active = true;
  boundary();
  ++usb_serial_interface.completed;
  service();
  network_active = false;
  service();
  assert(!the_mesh.terminal);
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM) || MESH_ESP32_TINYUSB_NONBLOCKING
  // DTR fallback also works from Binary when no owner-task event is available.
  data_connected = true;
  service();
  boundary(false);
  assert(the_mesh.terminal);
#endif
  logging_enabled = true;
  beginUsbDefaultSession();
  assert(the_mesh.terminal && usb_logging_terminal_mode);
  dedicated_logging = true;
  beginUsbDefaultSession();
  assert(the_mesh.terminal && !usb_logging_terminal_mode);
  assert(cancelled_routes >= 6);
}
'''


class CompanionUsbDefaultTest(unittest.TestCase):
    def test_startup_and_reconnection_for_each_usb_transport(self):
        functions = "\n".join(function("static void " + name + "(") for name in (
            "resetUsbTerminalHostSession", "serviceUsbTerminalHostSessionReset",
            "serviceUsbAsciiSessionDefault", "beginUsbDefaultSession"))
        profiles = {
            "nrf52": ["NRF52_PLATFORM=1"],
            "esp32-tinyusb": ["ESP32=1", "ARDUINO_USB_MODE=0", "ARDUINO_USB_CDC_ON_BOOT=1", "MESH_ESP32_TINYUSB_NONBLOCKING=1"],
            "esp32-hwcdc": ["ESP32=1", "ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=1"],
            "rp2040": ["RP2040_PLATFORM=1"],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "usb-default.cpp"
            source.write_text(HARNESS.replace("@FUNCTIONS@", functions))
            for name, defines in profiles.items():
                for full in (False, True):
                    with self.subTest(transport=name, full=full):
                        binary = Path(directory) / (name + str(full))
                        flags = ["-D" + flag for flag in defines]
                        if full:
                            flags.append("-DCOMPANION_RADIO_FULL=1")
                        subprocess.run(["g++", "-std=c++17", "-I", str(ROOT / "src"),
                                        *flags, str(source), "-o", str(binary)], check=True)
                        subprocess.run([str(binary)], check=True)

    def test_startup_and_reconnect_selection_run_before_binary_dispatch(self):
        setup = function("void setup() {")
        self.assertIn("beginUsbDefaultSession();", setup)
        loop = function("\nvoid loop() {")
        self.assertLess(loop.index("serviceUsbTerminalHostSessionReset();"), loop.index("serviceUsbAsciiSessionDefault();"))
        self.assertLess(loop.index("serviceUsbAsciiSessionDefault();"), loop.index("the_mesh.loop();"))
        self.assertLess(loop.index("expireUsbBinaryStartupProbeBeforeDispatch();"), loop.index("the_mesh.loop();"))
        self.assertNotIn("COMPANION_RADIO_FULL", MAIN)


if __name__ == "__main__":
    unittest.main()
