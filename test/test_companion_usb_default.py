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
static bool announced_ascii = false;
static uint32_t millis() { return clock_ms; }
struct Mesh {
  bool terminal = false, stale_input = true;
  bool isTerminalMode() const { return terminal; }
  void resetUsbHostSessionInput() { stale_input = false; }
  void applyUsbLoggingState(bool);
} the_mesh;
struct SerialInterface {
  bool passthrough = false, old_frame = true;
  uint32_t completed = 7;
  void setPassthroughMode(bool value) { passthrough = value; }
  bool isPassthroughMode() const { return passthrough; }
  void resetSessionState() { old_frame = false; }
  uint32_t getCompletedFrameCount() const { return completed; }
} usb_serial_interface;
struct Board { bool isUsbHostConnected() const { return data_connected; } } board;
static bool usb_mota_mode = false, usb_logging_terminal_mode = false;
static bool usb_logging_network_parked = false;
static bool usb_protocol_initialized = true;
static bool usb_logging_reply_hold = false, usb_logging_reply_staged = false;
static bool usb_logging_reply_pending = false, usb_logging_reply_state = false;
static bool usb_host_session_connected = false, usb_terminal_discard_line = true;
static bool usb_terminal_host_reset_completion_pending = false;
static uint32_t usb_terminal_host_reset_retry_at = 0;
static unsigned line_length = 5;
static mesh::UsbBinaryStartupProbe usb_binary_startup_probe;
static mesh::UsbAsciiSessionDefault usb_ascii_session_default;
static mesh::UsbHostPresenceDebouncer usb_hwcdc_host_presence;
static constexpr unsigned USB_TRANSPORT_RESET_RETRY_MS = 1000;
static constexpr unsigned USB_HOST_LOSS_EDGE_MS = 100, USB_HOST_LOSS_GRACE_MS = 2000;
struct Stream {
  int pending = 0;
  int available() const { return pending; }
  int read() { return pending-- > 0 ? 'x' : -1; }
  void print(const char*) {}
} usb_stream;
static Stream& usbTerminalOutput() { return usb_stream; }
namespace mesh {
namespace ota {
struct SerialFolderSource {
  bool pending_response = false;
  unsigned resets = 0;
  bool hasPendingResponse() const { return pending_response; }
  void resetSessionState() { ++resets; pending_response = false; }
};
struct OtaContext {
  static SerialFolderSource& serialFolderSource() {
    static SerialFolderSource source;
    return source;
  }
};
}
Stream& usbCompanionPort() { return usb_stream; }
void discardUsbTerminalOutput() {}
bool takeUsbTerminalSessionReset() { bool value = reset_event; reset_event = false; return value; }
bool resetUsbCompanionTransport() { return transport_ready; }
bool tryCompleteUsbTerminalSessionReset() { return transport_ready; }
bool isUsbLoggingTransportRecoveryPending() { return false; } // Explicit host-edge scenarios.
bool hasDedicatedUsbLoggingPort() { return dedicated_logging; }
bool isUsbLoggingEnabled() { return logging_enabled; }
void setUsbLoggingEnabled(bool enabled) {
  if (enabled && usb_protocol_initialized && !dedicated_logging) {
    assert(usb_serial_interface.passthrough); // Must park before gate opens.
  }
  logging_enabled = enabled;
}
}
static bool isUsbTerminalDataConnected() { return data_connected; }
static bool isNetworkTerminalActive() { return network_active; }
static void clearUsbTerminalLine() { line_length = 0; }
static void cancelUsbSerialOperations() { ++cancelled_routes; }
static void leaveUsbMotaMode(bool) { usb_mota_mode = false; }
static void leaveUsbTerminalMode(bool) { the_mesh.terminal = false; }
static void enterUsbTerminalMode(bool show_banner = true) {
  assert(transport_ready && !network_active);
  ++entered_ascii;
  announced_ascii = show_banner;
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
  serviceUsbLoggingOwnership(logging_enabled);
}
static void boundary(bool event = true) {
  reset_event = event;
  data_connected = false;
  service();
}
int main() {
  auto& source = mesh::ota::OtaContext::serialFolderSource();
  // No Full macro is required: ordinary USB Companions also boot in ASCII.
  beginUsbDefaultSession();
  assert(the_mesh.terminal && usb_serial_interface.passthrough);
  assert(!announced_ascii);
  service(); // observe the open host before it can close
  for (bool binary : {false, true}) {
    the_mesh.terminal = !binary;
    usb_serial_interface.passthrough = !binary;
    the_mesh.stale_input = true;
    usb_serial_interface.old_frame = true;
    line_length = 5;
    usb_binary_startup_probe.start(clock_ms, usb_serial_interface.completed);
    source.pending_response = true;
    const unsigned previous_resets = source.resets;
    boundary();
    assert(!source.hasPendingResponse() && source.resets == previous_resets + 1);
    assert(the_mesh.terminal && usb_serial_interface.passthrough);
    assert(!announced_ascii);
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
  // A network CLI must retain ownership while USB diagnostics exclude frames.
  the_mesh.terminal = false;
  usb_serial_interface.passthrough = false;
  network_active = true;
  logging_enabled = false;
  the_mesh.applyUsbLoggingState(true);
  assert(network_active && !the_mesh.terminal);
  assert(usb_serial_interface.passthrough && usb_logging_network_parked);
  usb_stream.pending = 5;
  serviceUsbLoggingOwnership(logging_enabled);
  assert(usb_stream.pending == 5); // Still log-only; no USB CLI takes ownership.
  the_mesh.applyUsbLoggingState(false);
  assert(network_active && !the_mesh.terminal);
  assert(!usb_serial_interface.passthrough && !usb_logging_network_parked);
  assert(usb_stream.pending == 0);
  logging_enabled = true;
  serviceUsbLoggingOwnership(logging_enabled);
  network_active = false;
  usb_stream.pending = 5;
  serviceUsbLoggingOwnership(logging_enabled);
  assert(the_mesh.terminal && usb_logging_terminal_mode);
  assert(usb_serial_interface.passthrough && !usb_logging_network_parked);
  assert(usb_stream.pending == 0);
  // Software handoff must preserve the late mOTA boundary in both output
  // modes; only a physical session reset may abandon the source response.
  for (bool logging_after_handoff : {false, true}) {
    the_mesh.terminal = false;
    usb_serial_interface.passthrough = false;
    usb_logging_terminal_mode = false;
    network_active = true;
    logging_enabled = true;
    serviceUsbLoggingOwnership(logging_enabled);
    assert(usb_logging_network_parked && usb_serial_interface.passthrough);
    source.pending_response = true;
    const unsigned previous_resets = source.resets;
    usb_stream.pending = 5;
    logging_enabled = logging_after_handoff;
    network_active = false;
    serviceUsbLoggingOwnership(logging_enabled);
    assert(source.hasPendingResponse() && source.resets == previous_resets);
    assert(usb_stream.pending == 5 && usb_serial_interface.passthrough);
    assert(!usb_logging_network_parked);
    assert(the_mesh.terminal == logging_after_handoff);
    // A boundary-aware consumer completing the response can release the
    // software quarantine without pretending it was a physical reset.
    source.pending_response = false;
    usb_stream.pending = 0;
  }
  the_mesh.terminal = true;
  usb_serial_interface.passthrough = true;
  // An ASCII-restoring network handoff must not accidentally reenable frames.
  logging_enabled = false;
  usb_logging_network_parked = true;
  usb_logging_terminal_mode = false;
  serviceUsbLoggingOwnership(logging_enabled);
  assert(the_mesh.terminal && usb_serial_interface.passthrough);
  // Remote logging cancels exclusive mOTA before the next framed dispatch.
  usb_mota_mode = true;
  the_mesh.terminal = false;
  logging_enabled = true;
  serviceUsbLoggingOwnership(logging_enabled);
  assert(!usb_mota_mode && the_mesh.terminal && usb_serial_interface.passthrough);
  logging_enabled = true;
  beginUsbDefaultSession();
  assert(the_mesh.terminal && usb_logging_terminal_mode);
  assert(announced_ascii);
  dedicated_logging = true;
  beginUsbDefaultSession();
  assert(the_mesh.terminal && !usb_logging_terminal_mode);
  assert(!announced_ascii);
  assert(cancelled_routes >= 6);
}
'''


class CompanionUsbDefaultTest(unittest.TestCase):
    def test_wifi_defaults_preserve_a_reachable_cli(self):
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        start = mesh.index("  // Keep WiFi-only compatibility builds reachable")
        end = mesh.index("  memset(_prefs.bluetooth_name", start)
        defaults = mesh[start:end]
        profiles = {
            "esp32-full": (["ESP32=1", "WIFI_SSID=1", "BLE_PIN_CODE=123456",
                            "ENABLE_USB_INTERFACE=1", "COMPANION_RADIO_FULL=1"], 0),
            "esp32-ble-wifi": (["ESP32=1", "WIFI_SSID=1", "BLE_PIN_CODE=123456"], 0),
            "esp32-usb-wifi": (["ESP32=1", "WIFI_SSID=1", "ENABLE_USB_INTERFACE=1"], 0),
            "esp32-wifi-only": (["ESP32=1", "WIFI_SSID=1"], 1),
            "pico-usb-ble-wifi": (["RP2040_PLATFORM=1", "WIFI_SSID=1",
                                  "ENABLE_USB_INTERFACE=1", "BLE_PIN_CODE=123456"], 0),
            "pico-wifi-only": (["RP2040_PLATFORM=1", "WIFI_SSID=1"], 1),
            "ethernet-wifi": (["ESP32=1", "WIFI_SSID=1", "ETHERNET_ENABLED=1"], 0),
            "hardware-serial-wifi": (["ESP32=1", "WIFI_SSID=1", "SERIAL_RX=1"], 0),
            "indicator-exclusive": (["ESP32=1", "WIFI_SSID=1", "BLE_PIN_CODE=123456",
                                    "ENABLE_USB_INTERFACE=1", "COMPANION_EXCLUSIVE_WIFI_BLE=1"], 0),
            "nrf52-ble": (["NRF52_PLATFORM=1", "BLE_PIN_CODE=123456"], 0),
            "usb-only": (["ENABLE_USB_INTERFACE=1"], 0),
        }
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "wireless-defaults"
            for name, (flags, expected) in profiles.items():
                with self.subTest(profile=name):
                    source = ("#include <cassert>\nstruct Prefs { unsigned wifi_enabled = 99; } _prefs;\n"
                              "int main() {\n" + defaults
                              + f"assert(_prefs.wifi_enabled == {expected});\n}}\n")
                    build = subprocess.run(["g++", "-std=c++17", "-x", "c++",
                                            *["-D" + flag for flag in flags],
                                            "-", "-o", str(binary)], input=source,
                                           text=True, capture_output=True)
                    self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                    subprocess.run([str(binary)], check=True)
        self.assertIn("static bool companion_wifi_requested = false;", MAIN)
        self.assertIn("companion_transport_boot_mode =\n      CompanionTransportMode::Bluetooth;", MAIN)

    def test_startup_and_reconnection_for_each_usb_transport(self):
        functions = "\n".join(function("static void " + name + "() {") for name in (
            "resetUsbTerminalHostSession", "serviceUsbTerminalHostSessionReset",
            "serviceUsbAsciiSessionDefault", "beginUsbDefaultSession"))
        functions += "\n" + function("static void serviceUsbLoggingOwnership(bool logging_enabled) {")
        functions += "\n" + function("void MyMesh::applyUsbLoggingState(bool enabled) {").replace("MyMesh::", "Mesh::")
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
        self.assertLess(loop.index("serviceUsbLoggingReplyBarrier();"), loop.index("the_mesh.loop();"))
        self.assertLess(loop.index("serviceUsbLoggingOwnership(mesh::isUsbLoggingEnabled());"), loop.index("the_mesh.loop();"))
        self.assertLess(loop.index("expireUsbBinaryStartupProbeBeforeDispatch();"), loop.index("the_mesh.loop();"))
        # USB defaults apply to ordinary and Full builds. Unrelated WiFi OTA
        # ownership may legitimately use the Full profile elsewhere in main.
        for name in ("beginUsbDefaultSession", "resetUsbTerminalHostSession",
                     "serviceUsbTerminalHostSessionReset", "serviceUsbAsciiSessionDefault"):
            self.assertNotIn("COMPANION_RADIO_FULL", function("static void " + name + "() {"))
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        self.assertNotIn("mesh::setUsbLoggingEnabled(", source)
        logging_reply = source[source.index('if (strncmp(command, "set logging.output ", 19)'):
                               source.index('if ((strncmp(command, "get ", 4)', source.index('if (strncmp(command, "set logging.output ", 19)'))]
        self.assertIn("mesh::cli::loggingOutputName(usb, wifi)", logging_reply)
        terminal = function("static void serviceUsbTerminal() {")
        self.assertLess(terminal.index("serviceUsbLoggingOwnership("), terminal.index("if (usb_mota_mode)"))
        leave = function("static void leaveUsbTerminalMode(bool acknowledge) {")
        self.assertIn("!mesh::hasDedicatedUsbLoggingPort() && mesh::isUsbLoggingEnabled()", leave)
        browser = function("bool MyMesh::beginStreamTerminal(Stream& output) {")
        self.assertIn("serviceUsbLoggingOwnership(mesh::isUsbLoggingEnabled());", browser)
        tcp = function("static void ota_console_loop() {")
        self.assertIn("serviceUsbLoggingOwnership(mesh::isUsbLoggingEnabled());", tcp)


if __name__ == "__main__":
    unittest.main()
