"""Exercise the production WiFi diagnostic state and bounded text on the host."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <helpers/WiFiConnectionDiagnostics.h>
using namespace mesh::wifi;

const uint8_t network[] = {'i', 'o', 't'};
const uint8_t other[] = {'o', 'l', 'd'};

std::string format(const ConnectionDiagnosticsSnapshot& value, uint32_t now) {
  char output[157];
  assert(formatConnectionDiagnostics(output, sizeof(output), value, now));
  return output;
}

void stateAndReasons() {
  ConnectionDiagnostics tracker;
  auto value = tracker.snapshot();
  assert(value.phase == ConnectionPhase::Idle && value.attempts == 0);
  assert(!value.has_disconnect && !value.ssid[0]);
  assert(format(value, 0) == "> WiFi: idle; tries: 0; last try: never\n"
      "Last drop: no driver reason recorded");
  tracker.gotIP(10); // A stale event with no matching association proves nothing.
  assert(tracker.snapshot().phase == ConnectionPhase::Idle);
  tracker.begin("iot", 100);
  value = tracker.snapshot();
  assert(value.phase == ConnectionPhase::Connecting && value.attempts == 1);
  assert(value.attempt_ms == 100 && value.phase_ms == 100);
  assert(!value.has_disconnect && strcmp(value.ssid, "iot") == 0);
  tracker.gotIP(110);
  tracker.associated(other, sizeof(other), 111);
  tracker.disconnected(other, sizeof(other), 201, 112);
  tracker.associated(nullptr, 3, 113);
  tracker.associated(network, 0, 114);
  tracker.associated(network, 33, 115);
  assert(tracker.snapshot().phase == ConnectionPhase::Connecting);
  assert(!tracker.snapshot().has_disconnect);

  tracker.associated(network, sizeof(network), 200);
  value = tracker.snapshot();
  assert(value.phase == ConnectionPhase::WaitingIP && value.phase_ms == 200);
  assert(format(value, 500).find("associated, awaiting IP") != std::string::npos);
  tracker.gotIP(1000);
  assert(tracker.snapshot().phase == ConnectionPhase::Online);
  assert(tracker.snapshot().phase_ms == 1000);
  tracker.gotIP(1500); // An IP renewal must not restart the phase clock.
  assert(tracker.snapshot().phase_ms == 1000);
  tracker.lostIP(2000);
  assert(tracker.snapshot().phase == ConnectionPhase::WaitingIP);
  tracker.gotIP(2500);
  tracker.disconnected(network, sizeof(network), 202, 3000);
  value = tracker.snapshot();
  assert(value.phase == ConnectionPhase::Disconnected && value.has_disconnect);
  assert(value.last_reason == 202 && value.last_disconnect_ms == 3000);
  assert(format(value, 8000).find(
      "Last drop: driver 202 auth failed (check password); 5s ago") != std::string::npos);

  tracker.begin("iot", 9000);
  value = tracker.snapshot();
  assert(value.attempts == 2 && value.attempt_ms == 9000);
  assert(value.last_reason == 202 && value.last_disconnect_ms == 3000);
  tracker.disconnected(network, sizeof(network), 8, 9050);
  assert(tracker.snapshot().phase == ConnectionPhase::Connecting);
  tracker.associated(network, sizeof(network), 9100);
  tracker.disconnected(network, sizeof(network), 8, 9150);
  assert(tracker.snapshot().phase == ConnectionPhase::WaitingIP);
  tracker.gotIP(9200);
  tracker.disconnected(network, sizeof(network), 8, 9250);
  value = tracker.snapshot();
  assert(value.phase == ConnectionPhase::Online && value.last_reason == 202);
  assert(value.last_disconnect_ms == 3000);

  tracker.disconnected(network, sizeof(network), 201, 10000);
  assert(format(tracker.snapshot(), 11000).find(
      "Last drop: driver 201 SSID not found; 1s ago") != std::string::npos);
  tracker.begin("iot", 12000);
  tracker.disconnected(network, sizeof(network), 254, 13000);
  assert(format(tracker.snapshot(), 14000).find(
      "Last drop: driver 254 unknown reason; 1s ago") != std::string::npos);

  tracker.begin("old", 15000);
  value = tracker.snapshot();
  assert(value.attempts == 1 && !value.has_disconnect && !value.last_reason);
  assert(value.last_disconnect_ms == 0 && strcmp(value.ssid, "old") == 0);
  tracker.disconnected(network, sizeof(network), 202, 15100);
  tracker.associated(network, sizeof(network), 15200);
  tracker.gotIP(15300);
  assert(!tracker.snapshot().has_disconnect);
  assert(tracker.snapshot().phase == ConnectionPhase::Connecting);
  tracker.associated(other, sizeof(other), 15400);
  tracker.gotIP(15500);
  assert(tracker.snapshot().phase == ConnectionPhase::Online);
}

void explicitStopsAndWraparound() {
  const ConnectionPhase outcomes[] = {
    ConnectionPhase::TimedOut, ConnectionPhase::ScanBusy,
    ConnectionPhase::Blocked, ConnectionPhase::NoSSID,
    ConnectionPhase::PolicyFailed, ConnectionPhase::APNotOnChannel
  };
  for (auto outcome : outcomes) {
    ConnectionDiagnostics tracker;
    tracker.begin("iot", 100);
    tracker.phase(outcome, 200);
    tracker.phase(ConnectionPhase::TimedOut, 250);
    assert(tracker.snapshot().phase == outcome);
    tracker.stopped(300);
    tracker.associated(network, sizeof(network), 400);
    tracker.gotIP(500);
    tracker.disconnected(network, sizeof(network), 8, 600);
    assert(tracker.snapshot().phase == outcome);
    assert(tracker.snapshot().phase_ms == 200);
    // A queued real drop may enrich history, while the explicit result stays.
    tracker.disconnected(network, sizeof(network), 202, 700);
    assert(tracker.snapshot().has_disconnect);
    assert(tracker.snapshot().phase == outcome);
    assert(tracker.snapshot().phase_ms == 200);
  }
  ConnectionDiagnostics stopped;
  stopped.begin("iot", 0);
  stopped.stopped(10);
  stopped.associated(network, sizeof(network), 20);
  stopped.gotIP(30);
  assert(stopped.snapshot().phase == ConnectionPhase::Stopped);

  ConnectionDiagnostics noReason;
  noReason.begin("iot", 0);
  noReason.disconnected(network, sizeof(network), 8, 10);
  assert(!noReason.snapshot().has_disconnect);
  noReason.phase(ConnectionPhase::TimedOut, 15000);
  noReason.stopped(15001);
  assert(format(noReason.snapshot(), 15001).find("connection timed out") != std::string::npos);
  assert(format(noReason.snapshot(), 15001).find("no driver reason recorded") != std::string::npos);

  ConnectionDiagnostics wrapped;
  wrapped.begin("iot", UINT32_MAX - 2000);
  wrapped.disconnected(network, sizeof(network), 201, UINT32_MAX - 1000);
  const auto text = format(wrapped.snapshot(), 499);
  assert(text.find("last try: 2s ago") != std::string::npos);
  assert(text.find("SSID not found; 1s ago") != std::string::npos);

  const std::string maximum(32, 'S');
  ConnectionDiagnostics fullSSID;
  fullSSID.begin(maximum.c_str(), 0);
  const auto* bytes = reinterpret_cast<const uint8_t*>(maximum.data());
  fullSSID.associated(bytes, 32, 1);
  assert(fullSSID.snapshot().phase == ConnectionPhase::WaitingIP);
  assert(strlen(fullSSID.snapshot().ssid) == 32);
  fullSSID.disconnected(bytes, 32, 202, 2);
  assert(fullSSID.snapshot().has_disconnect);
  fullSSID.begin(nullptr, 3);
  assert(!fullSSID.snapshot().ssid[0] && !fullSSID.snapshot().has_disconnect);
  fullSSID.associated(bytes, 32, 4);
  assert(fullSSID.snapshot().phase == ConnectionPhase::Connecting);
}

void formatterBoundaries() {
  ConnectionDiagnosticsSnapshot value;
  value.attempts = UINT32_MAX;
  value.has_disconnect = true;
  const ConnectionPhase phases[] = {
    ConnectionPhase::Idle, ConnectionPhase::Connecting, ConnectionPhase::WaitingIP,
    ConnectionPhase::Online, ConnectionPhase::Disconnected, ConnectionPhase::TimedOut,
    ConnectionPhase::Stopped, ConnectionPhase::ScanBusy, ConnectionPhase::Blocked,
    ConnectionPhase::NoSSID, ConnectionPhase::PolicyFailed, ConnectionPhase::APNotOnChannel
  };
  for (auto phase : phases) for (unsigned reason = 0; reason <= UINT8_MAX; ++reason) {
    value.phase = phase;
    value.last_reason = static_cast<uint8_t>(reason);
    char output[157];
    assert(formatConnectionDiagnostics(output, sizeof(output), value, UINT32_MAX));
    const std::string complete(output);
    assert(complete.size() < 157);
    assert(complete.find("tries: 4294967295; last try: 4294967s ago") != std::string::npos);
    assert(complete.find("Last drop: driver " + std::to_string(reason) + " ") != std::string::npos);
    assert(complete.find('\n') == complete.rfind('\n'));
    for (size_t capacity = 1; capacity <= 157; ++capacity) {
      unsigned char guarded[161]; memset(guarded, 0x5a, sizeof(guarded));
      char* destination = reinterpret_cast<char*>(guarded + 2);
      const bool fits = formatConnectionDiagnostics(destination, capacity, value, UINT32_MAX);
      assert(fits == (capacity > complete.size()));
      assert(guarded[0] == 0x5a && guarded[1] == 0x5a);
      for (size_t i = 2 + capacity; i < sizeof(guarded); ++i) assert(guarded[i] == 0x5a);
      assert(std::string(destination) == complete.substr(0, capacity - 1));
    }
  }
  char untouched = 'X';
  assert(!formatConnectionDiagnostics(&untouched, 0, value, 0) && untouched == 'X');
  assert(!formatConnectionDiagnostics(nullptr, 157, value, 0));
}

void hintsNeverDamageStatus() {
  ConnectionDiagnosticsSnapshot value;
  value.has_disconnect = true;
  value.last_reason = 202;
  const std::string hint = "; last drop: 202 auth failed; 4s ago";
  for (size_t capacity = 1; capacity <= 157; ++capacity) {
    unsigned char guarded[161]; memset(guarded, 0x5a, sizeof(guarded));
    char* destination = reinterpret_cast<char*>(guarded + 2);
    const std::string original(capacity > 101 ? 100 : capacity - 1, 'S');
    memcpy(destination, original.c_str(), original.size() + 1);
    const bool appended = appendConnectionFailureHint(destination, capacity, value, 4000);
    assert(appended == (original.size() + hint.size() < capacity));
    assert(std::string(destination) == original + (appended ? hint : ""));
    assert(guarded[0] == 0x5a && guarded[1] == 0x5a);
    for (size_t i = 2 + capacity; i < sizeof(guarded); ++i) assert(guarded[i] == 0x5a);
  }
  char status[157] = "> WiFi: failed; setup hotspot 'MC-D177' at http://192.168.4.1/";
  const std::string original(status);
  assert(appendConnectionFailureHint(status, sizeof(status), value, 4000));
  assert(std::string(status) == original + hint);
  strcpy(status, original.c_str());
  value.has_disconnect = false;
  assert(!appendConnectionFailureHint(status, sizeof(status), value, 0));
  assert(std::string(status) == original);
  value.has_disconnect = true;
  char unterminated[2] = {'X', 'Y'};
  assert(!appendConnectionFailureHint(unterminated, sizeof(unterminated), value, 0));
  assert(unterminated[0] == 'X' && unterminated[1] == 'Y');
  assert(!appendConnectionFailureHint(nullptr, 157, value, 0));
  assert(!appendConnectionFailureHint(status, 0, value, 0));
  assert(std::string(status) == original);
}

int main() {
  stateAndReasons();
  explicitStopsAndWraparound();
  formatterBoundaries();
  hintsNeverDamageStatus();
}
'''


class WiFiConnectionDiagnosticsTest(unittest.TestCase):
    def test_tracker_driver_reasons_and_bounded_output(self):
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler, "A host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="wifi-connection-diagnostics-") as directory:
            folder = Path(directory)
            source = folder / "test.cpp"
            source.write_text(HARNESS)
            binary = folder / ("test.exe" if os.name == "nt" else "test")
            flags = [] if os.name == "nt" else [
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"
            ]
            built = subprocess.run([
                compiler, "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror", *flags,
                "-I", str(ROOT / "src"), str(source), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
