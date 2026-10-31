"""USB packet log: a repeater or room server that receives a packet on radio2
(MediaMesh) prints `RADIO: radio2` between the packet's RAW and RX lines. For
a packet it sends, it prints the frame as sent (RAW) ahead of the TX line, and
the same marker when the packet goes out on radio2.

Compiles the real MyMesh::logRxRaw of both roles with small stand-ins, then
checks the lines against the patterns meshcoretomqtt (Cisien/meshcoretomqtt,
bridge/message_parser.py) uses: its RAW handling and RX pattern are untouched,
and the extra line matches neither. The shared Dispatcher (and so every
Companion build) must not carry the marker.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

# meshcoretomqtt's patterns, verbatim (bridge/message_parser.py @ 9b4e022).
MCTOMQTT_RAW = re.compile(r"(\d{2}:\d{2}:\d{2}) - (\d{1,2}/\d{1,2}/\d{4}) U RAW: (.*)")
MCTOMQTT_PACKET = re.compile(
    r"(\d{2}:\d{2}:\d{2}) - (\d{1,2}/\d{1,2}/\d{4}) U: (RX|TX), len=(\d+) \(type=(\d+), route=([A-Z]), payload_len=(\d+)\)"
    r"(?: SNR=(-?\d+) RSSI=(-?\d+) score=(\d+)(?: time=(\d+))?)?"
    r"(?: hash=([0-9A-F]+))?"
    r"(?: \[(.*)\])?$"
)

HARNESS = r'''
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <string>
#define MESH_PACKET_LOGGING 1
#define MESH_PACKET_LOGGING_COMPACT 0
static std::string out;
struct Stream {};
static Stream port;
namespace mesh {
inline Stream& usbLoggingPort() { return port; }
inline bool isUsbPacketLoggingAllowed() { return true; }
inline bool serialLogCompactPacket(Stream&, char, const uint8_t*, size_t) { return true; }
template <size_t CAP = 256> struct SerialLogLine {
  std::string s;
  void printf(const char* f, ...) { char b[CAP]; va_list a; va_start(a, f); vsnprintf(b, sizeof(b), f, a); va_end(a); s += b; }
  void hex(const uint8_t* d, size_t n) { char b[3]; for (size_t i = 0; i < n; i++) { snprintf(b, 3, "%02X", d[i]); s += b; } }
  bool flush(Stream&) { out += s + "\n"; return true; }
};
}
struct Radio { uint8_t profile = 0; uint8_t receiveProfile() const { return profile; } };
struct MyMesh {
  Radio radio; Radio* _radio = &radio;
  const char* getLogDateTime() { return "12:34:56 - 29/10/2026 U"; }
  void logRxRaw(float snr, float rssi, const uint8_t raw[], int len);
};
@METHOD@
int main() {
  MyMesh m;
  const uint8_t raw[] = {0x15, 0xA3, 0x01};
  m.logRxRaw(9.0f, -71.0f, raw, sizeof(raw));
  out += "--\n";
  m.radio.profile = 1;
  m.logRxRaw(9.0f, -71.0f, raw, sizeof(raw));
  fputs(out.c_str(), stdout);
}
'''


TX_HARNESS = r'''
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <string>
#define MESH_PACKET_LOGGING 1
#define MESH_PACKET_LOGGING_COMPACT 0
static std::string out;
struct Stream {};
static Stream port;
namespace mesh {
inline Stream& usbLoggingPort() { return port; }
template <size_t CAP = 256> struct SerialLogLine {
  std::string s;
  void printf(const char* f, ...) { char b[CAP]; va_list a; va_start(a, f); vsnprintf(b, sizeof(b), f, a); va_end(a); s += b; }
  void hex(const uint8_t* d, size_t n) { char b[3]; for (size_t i = 0; i < n; i++) { snprintf(b, 3, "%02X", d[i]); s += b; } }
  bool flush(Stream&) { out += s + "\n"; return true; }
};
struct Packet { uint8_t radio_profile = 0; };
void usbLogTxFrame(const char* log_time, const Packet* packet, const uint8_t raw[], int len);
}
@HOOK@
int main() {
  mesh::Packet pkt;
  const uint8_t raw[] = {0x15, 0xA3, 0x02, 0x7C};
  mesh::usbLogTxFrame("12:34:57 - 29/10/2026 U", &pkt, raw, sizeof(raw));
  out += "--\n";
  pkt.radio_profile = 1;
  mesh::usbLogTxFrame("12:34:57 - 29/10/2026 U", &pkt, raw, sizeof(raw));
  fputs(out.c_str(), stdout);
}
'''

TX_LINE = "12:34:57 - 29/10/2026 U: TX, len=4 (type=5, route=F, payload_len=1)"


def mctomqtt_publish(lines):
    """meshcoretomqtt's state machine for these lines (message_parser.py with
    the radio2 tagging): RAW sets the frame and clears a pending radio id,
    RADIO sets it, and each RX/TX line is published with both."""
    radio_re = re.compile(r"\d{2}:\d{2}:\d{2} - \d{1,2}/\d{1,2}/\d{4} U RADIO: ([A-Za-z0-9_-]{1,16})$")
    last_raw, pending, published = None, None, []
    for line in lines:
        m = radio_re.match(line)
        if m:
            pending = m.group(1)
            continue
        if "U RAW:" in line:
            last_raw, pending = line.split("U RAW:")[1].strip(), None
        m = MCTOMQTT_PACKET.match(line)
        if m:
            direction = m.group(3).lower()
            msg = {"direction": direction, "raw": last_raw}
            if pending:
                msg[direction + "_radio_id"] = pending
                if pending == "radio2":
                    msg["mediamesh"] = True
            pending = None
            published.append(msg)
    return published


class UsbLogRadio2Test(unittest.TestCase):
    def run_role(self, role, compact=False):
        source = (ROOT / "examples" / role / "MyMesh.cpp").read_text()
        method = extract_braced(source, "void MyMesh::logRxRaw(")
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="meshcore-usb-radio2-") as tmp:
            src = Path(tmp) / "log.cpp"
            harness = HARNESS.replace("#define MESH_PACKET_LOGGING_COMPACT 0",
                                      "#define MESH_PACKET_LOGGING_COMPACT " + str(int(compact)))
            src.write_text(harness.replace("@METHOD@", method))
            exe = Path(tmp) / "log"
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Werror", "-Wno-unused-parameter",
                                    str(src), "-o", str(exe)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stderr)
        main, radio2 = ran.stdout.split("--\n")
        return main.splitlines(), radio2.splitlines()

    def check_role(self, role):
        main, radio2 = self.run_role(role)
        # Main radio: just the RAW line, as before.
        self.assertEqual(main, ["12:34:56 - 29/10/2026 U RAW: 15A301"])
        # radio2: the same RAW line, then the marker.
        self.assertEqual(radio2, ["12:34:56 - 29/10/2026 U RAW: 15A301",
                                  "12:34:56 - 29/10/2026 U RADIO: radio2"])
        marker = radio2[1]
        # meshcoretomqtt: not a packet, not RAW, not DEBUG, so it is skipped.
        self.assertIsNone(MCTOMQTT_PACKET.match(marker))
        self.assertNotIn("U RAW:", marker)
        self.assertFalse(marker.startswith("DEBUG"))

    def test_repeater(self):
        self.check_role("simple_repeater")

    def test_room_server(self):
        self.check_role("simple_room_server")

    def test_compact_room_keeps_existing_raw_without_radio_marker(self):
        main, radio2 = self.run_role("simple_room_server", compact=True)
        expected = ["12:34:56 - 29/10/2026 U RAW: 15A301"]
        self.assertEqual(main, expected)
        self.assertEqual(radio2, expected)

    def run_tx_hook(self, role):
        source = (ROOT / "examples" / role / "MyMesh.cpp").read_text()
        hook = extract_braced(source, "void mesh::usbLogTxFrame(")
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="meshcore-usb-tx-radio2-") as tmp:
            src = Path(tmp) / "tx.cpp"
            src.write_text(TX_HARNESS.replace("@HOOK@", hook))
            exe = Path(tmp) / "tx"
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Werror", "-Wno-unused-parameter",
                                    str(src), "-o", str(exe)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stderr)
        main, radio2 = ran.stdout.split("--\n")
        return main.splitlines(), radio2.splitlines()

    def check_tx_role(self, role):
        main, radio2 = self.run_tx_hook(role)
        self.assertEqual(main, ["12:34:57 - 29/10/2026 U RAW: 15A3027C"])
        self.assertEqual(radio2, ["12:34:57 - 29/10/2026 U RAW: 15A3027C",
                                  "12:34:57 - 29/10/2026 U RADIO: radio2"])
        self.assertIsNotNone(MCTOMQTT_RAW.match(main[0]))
        # As meshcoretomqtt publishes it: after a received frame, the TX now
        # carries the frame it sent, not the last one it heard, and the radio2
        # tag goes on the TX alone.
        rx = ["12:34:56 - 29/10/2026 U RAW: 15A301",
              "12:34:56 - 29/10/2026 U: RX, len=3 (type=5, route=F, payload_len=1) SNR=9 RSSI=-71 score=1000"]
        self.assertEqual(mctomqtt_publish(rx + main + [TX_LINE]),
                         [{"direction": "rx", "raw": "15A301"}, {"direction": "tx", "raw": "15A3027C"}])
        self.assertEqual(mctomqtt_publish(rx + radio2 + [TX_LINE]),
                         [{"direction": "rx", "raw": "15A301"},
                          {"direction": "tx", "raw": "15A3027C", "tx_radio_id": "radio2", "mediamesh": True}])

    def test_repeater_tx(self):
        self.check_tx_role("simple_repeater")

    def test_room_server_tx(self):
        self.check_tx_role("simple_room_server")

    def test_dispatcher_calls_the_tx_hook_just_before_the_tx_line(self):
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
        self.assertIn('if (usbLogTxFrame) usbLogTxFrame(getLogDateTime(), outbound, raw, len);\n'
                      '    logPacketLine("TX", outbound, len, false, 0.0f, 0);', dispatcher)
        header = (ROOT / "src/Dispatcher.h").read_text()
        self.assertRegex(header, r"void usbLogTxFrame\([^;]*\)\s*__attribute__\(\(weak\)\);")

    def test_shared_dispatcher_and_companions_are_unchanged(self):
        # Full Companion images are packed to the byte; the marker lives only
        # in the repeater and room server hooks.
        self.assertNotIn("RADIO: radio2", (ROOT / "src/Dispatcher.cpp").read_text())
        for path in (ROOT / "examples/companion_radio").rglob("*.cpp"):
            text = path.read_text(errors="ignore")
            self.assertNotIn("RADIO: radio2", text, str(path))
            self.assertNotIn("usbLogTxFrame", text, str(path))


if __name__ == "__main__":
    unittest.main()
