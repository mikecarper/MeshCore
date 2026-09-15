"""Exercise WiFi session cancellation against production route ownership code."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "examples/companion_radio/main.cpp"
MESH = ROOT / "examples/companion_radio/MyMesh.cpp"


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <deque>
#include <vector>
#include <helpers/MultiSerialInterface.h>

struct FakeInterface : BaseSerialInterface {
  bool enabled = false;
  bool connected = true;
  std::deque<std::vector<uint8_t>> input;
  void enable() override { enabled = true; }
  void disable() override { enabled = false; }
  bool isEnabled() const override { return enabled; }
  bool isConnected() const override { return connected; }
  bool isReadBusy() const override { return false; }
  bool isWriteBusy() const override { return false; }
  size_t writeFrame(const uint8_t[], size_t len) override { return len; }
  size_t checkRecvFrame(uint8_t dest[]) override {
    if (input.empty()) return 0;
    auto frame = input.front(); input.pop_front();
    memcpy(dest, frame.data(), frame.size());
    return frame.size();
  }
} wifi_interface, usb_interface, bluetooth_interface;
MultiSerialInterface interface_manager;

constexpr int EXPECTED_ACK_TABLE_SIZE = 6;
struct MyMesh {
  bool streaming = false;
  int stream_cancels = 0, pending_cancels = 0, radio_cancels = 0;
  int trace_cancels = 0, signing_cancels = 0, expirations = 0;
  BaseSerialInterface* pending_serial_reply_route = nullptr;
  BaseSerialInterface* command_radio_reply_route = nullptr;
  BaseSerialInterface* binary_trace_reply_route = nullptr;
  BaseSerialInterface* sign_data_reply_route = nullptr;
  struct Ack { BaseSerialInterface* reply_route; bool radio_retry; };
  Ack expected_ack_table[EXPECTED_ACK_TABLE_SIZE] = {};
  void cancelSerialResponseStream() {
    ++stream_cancels;
    if (streaming) { streaming = false; interface_manager.unlockReplyRoute(); }
  }
  void clearPendingReqs() { ++pending_cancels; pending_serial_reply_route = nullptr; }
  void cancelPendingRadioParamApply() { ++radio_cancels; command_radio_reply_route = nullptr; }
  void clearBinaryTraceReply() { ++trace_cancels; binary_trace_reply_route = nullptr; }
  void cancelSigningSession() { ++signing_cancels; sign_data_reply_route = nullptr; }
  void expireExpectedAcks() { ++expirations; }
  void cancelSerialOperationsForRoute(BaseSerialInterface*);
} the_mesh;

@CANCEL_OPERATIONS@
@CANCEL_SESSION@

int main() {
  assert(interface_manager.addInterface(InterfaceType::WiFi, &wifi_interface));
  assert(interface_manager.addInterface(InterfaceType::USB, &usb_interface));
  assert(interface_manager.addInterface(InterfaceType::Bluetooth, &bluetooth_interface));
  FakeInterface* routes[] = {&wifi_interface, &usb_interface, &bluetooth_interface};
  for (auto owner : routes) {
    interface_manager.enable();
    the_mesh = MyMesh();
    owner->input.push_back({0x04});
    uint8_t command[MAX_FRAME_SIZE] = {};
    assert(interface_manager.checkRecvFrame(command) == 1);
    interface_manager.lockReplyRoute();
    the_mesh.streaming = true;
    the_mesh.pending_serial_reply_route = owner;
    the_mesh.command_radio_reply_route = owner;
    the_mesh.binary_trace_reply_route = owner;
    the_mesh.sign_data_reply_route = owner;
    for (unsigned i = 0; i < EXPECTED_ACK_TABLE_SIZE; ++i) {
      the_mesh.expected_ack_table[i] = {routes[i % 3], true};
    }

    // The transport marks itself disconnected during the synchronous callback.
    wifi_interface.connected = false;
    cancelCompanionWiFiSession(nullptr);
    wifi_interface.connected = true;
    const bool cancelled = owner == &wifi_interface;
    assert(the_mesh.stream_cancels == int(cancelled));
    assert(the_mesh.streaming == !cancelled);
    assert(the_mesh.pending_cancels == int(cancelled));
    assert(the_mesh.radio_cancels == int(cancelled));
    assert(the_mesh.trace_cancels == int(cancelled));
    assert(the_mesh.signing_cancels == int(cancelled));
    assert(the_mesh.pending_serial_reply_route == (cancelled ? nullptr : owner));
    assert(the_mesh.command_radio_reply_route == (cancelled ? nullptr : owner));
    assert(the_mesh.binary_trace_reply_route == (cancelled ? nullptr : owner));
    assert(the_mesh.sign_data_reply_route == (cancelled ? nullptr : owner));
    assert(!interface_manager.isReplyRouteFor(&wifi_interface));
    if (!cancelled) assert(interface_manager.isReplyRouteFor(owner));
    for (unsigned i = 0; i < EXPECTED_ACK_TABLE_SIZE; ++i) {
      // Detach WiFi's notification, not the radio's accepted message/retry.
      assert(the_mesh.expected_ack_table[i].reply_route ==
             (i % 3 == 0 ? nullptr : routes[i % 3]));
      assert(the_mesh.expected_ack_table[i].radio_retry);
    }
    assert(the_mesh.expirations == 1);
  }
}
'''


class CompanionWiFiSessionTest(unittest.TestCase):
    def test_callback_is_registered_before_wifi_interface_can_start(self):
        source = MAIN.read_text(encoding="utf-8")
        registration = source.index(
            "wifi_interface.setSessionChangedCallback(cancelCompanionWiFiSession, nullptr)"
        )
        setup = source.index("void setup()")
        add = source.index("interface_manager.addInterface(InterfaceType::WiFi", setup)
        start = source.index("startCompanionWiFi();", add)
        self.assertLess(registration, add)
        self.assertLess(add, start)

    def test_session_reset_cancels_only_wifi_owned_work(self):
        self.check_session_reset("WiFi")

    def test_session_reset_cancels_only_ethernet_owned_work(self):
        self.check_session_reset("Ethernet")

    def test_callback_is_registered_before_ethernet_interface_can_start(self):
        source = MAIN.read_text(encoding="utf-8")
        self.assertLess(
            source.index("ethernet_interface.setSessionChangedCallback(cancelCompanionEthernetSession, nullptr)"),
            source.index("ethernet_interface.begin();"),
        )

    def test_ethernet_callback_guards_cover_profiles_without_usb_or_wifi(self):
        # Preserve the real main.cpp conditional nesting, not just an extracted
        # callback body: Ethernet-only builds must see its definition as well
        # as its setup registration. Headers and unrelated code are unnecessary
        # for this preprocessor test and would require the firmware toolchain.
        markers = []
        continuation = False
        for line in MAIN.read_text(encoding="utf-8").splitlines(keepends=True):
            conditional = re.match(r"^\s*#\s*(?:if|ifdef|ifndef|elif|else|endif)\b", line)
            if continuation or conditional:
                markers.append(line)
                continuation = line.rstrip().endswith("\\")
            elif "static void cancelCompanionEthernetSession(" in line:
                markers.append("ETHERNET_SESSION_CALLBACK_DECLARED\n")
            elif "ethernet_interface.setSessionChangedCallback(" in line:
                markers.append("ETHERNET_SESSION_CALLBACK_REGISTERED\n")
        source = "".join(markers)
        for platform in (("ESP32_PLATFORM", "ESP32"), ("NRF52_PLATFORM",)):
            for ethernet in (False, True):
                for usb in (False, True):
                    with self.subTest(platform=platform, ethernet=ethernet, usb=usb):
                        flags = [f"-D{name}=1" for name in platform]
                        if ethernet:
                            flags.append("-DETHERNET_ENABLED=1")
                        if usb:
                            flags.append("-DENABLE_USB_INTERFACE=1")
                        result = subprocess.run(
                            [os.environ.get("CXX", "g++"), "-E", "-P", "-x", "c++",
                             *flags, "-"], input=source, text=True, capture_output=True,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        expected = ("ETHERNET_SESSION_CALLBACK_DECLARED\n"
                                    "ETHERNET_SESSION_CALLBACK_REGISTERED") if ethernet else ""
                        self.assertEqual(result.stdout.strip(), expected)

    def check_session_reset(self, transport):
        main = MAIN.read_text(encoding="utf-8")
        mesh = MESH.read_text(encoding="utf-8")
        callback = extract_braced(main, f"static void cancelCompanion{transport}Session(")
        operations = extract_braced(mesh, "void MyMesh::cancelSerialOperationsForRoute(")
        harness = HARNESS.replace("WiFi", transport).replace("wifi", transport.lower())
        source = harness.replace("@CANCEL_OPERATIONS@", operations).replace(
            "@CANCEL_SESSION@", callback
        )
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("wifi_session.exe" if os.name == "nt" else "wifi_session")
            result = subprocess.run(
                [os.environ.get("CXX", "g++"), "-std=c++17", "-Werror",
                 f"-I{ROOT / 'test/mocks'}", f"-I{ROOT / 'src'}",
                 "-x", "c++", "-", "-o", str(executable)],
                input=source, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
