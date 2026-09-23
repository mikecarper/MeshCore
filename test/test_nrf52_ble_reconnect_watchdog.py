#!/usr/bin/env python3
"""Exercise the real nRF52 BLE loop's stalled-link recovery decisions."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_t096_full_memory import method


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/nrf52/SerialBLEInterface.cpp"

HARNESS = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <helpers/nrf52/SecuritySessionTimer.h>

#define BLE_HEALTH_CHECK_INTERVAL 10000
#define BLE_COMPANION_START_TIMEOUT_MS 15000
#define BLE_CONN_HANDLE_INVALID 0xffff
#define BLE_DEBUG_PRINTLN(...) do {} while (0)

static uint32_t current_millis;
uint32_t millis() { return current_millis; }

struct Recovery {
  bool active = false;
  bool pending() const { return active; }
};

struct SerialBLEInterface {
  bool _isEnabled = false;
  bool _isDeviceConnected = false;
  uint16_t _conn_handle = BLE_CONN_HANDLE_INVALID;
  uint32_t _last_health_check = 0;
  bool allow_advertising = true;
  bool advertising = false;
  std::atomic<bool> _companionDataSeen{false};
  SecuritySessionTimer _security_timer;
  SecuritySessionTimer _companion_start_timer;
  Recovery _tx_disconnect_recovery;
  int disconnects = 0;
  int recoveries = 0;
  int advertisements = 0;
  int recovery_services = 0;
  void serviceBondedOnlyTransition() {}
  void serviceTxRecovery(uint32_t) { ++recovery_services; }
  void disconnect() { ++disconnects; }
  void recoverStalledTx(const char*) {
    ++recoveries;
    _tx_disconnect_recovery.active = true;
  }
  bool advertisingAllowed() const { return allow_advertising; }
  bool isAdvertising() const { return advertising; }
  bool startAdvertising(const char*) { ++advertisements; advertising = true; return true; }
  void loop();
};

@LOOP@

int main() {
  SerialBLEInterface pending_security;
  pending_security._isEnabled = true;
  pending_security._conn_handle = 1;
  pending_security._security_timer.start(100);
  current_millis = 120099;
  pending_security.loop();
  assert(pending_security.disconnects == 0);
  current_millis = 120100;
  pending_security.loop();
  assert(pending_security.disconnects == 1);

  SerialBLEInterface no_app_data;
  no_app_data._isEnabled = true;
  no_app_data._isDeviceConnected = true;
  no_app_data._conn_handle = 2;
  no_app_data._companion_start_timer.start(300);
  current_millis = 15299;
  no_app_data.loop();
  assert(no_app_data.recoveries == 0);
  current_millis = 15300;
  no_app_data.loop();
  assert(no_app_data.recoveries == 1);
  no_app_data.loop();
  assert(no_app_data.recovery_services == 1);

  SerialBLEInterface app_started;
  app_started._isEnabled = true;
  app_started._isDeviceConnected = true;
  app_started._conn_handle = 3;
  app_started._companion_start_timer.start(100);
  app_started._companionDataSeen.store(true);
  current_millis = 120000;
  app_started.loop();
  assert(app_started.recoveries == 0);
  assert(!app_started._companion_start_timer.pending());

  SerialBLEInterface disconnected;
  disconnected._isEnabled = true;
  current_millis = 9999;
  disconnected.loop();
  assert(disconnected.advertisements == 0);
  current_millis = 10000;
  disconnected.loop();
  assert(disconnected.advertisements == 1);
  disconnected.loop();
  assert(disconnected.advertisements == 1);
}
'''


class Nrf52BleReconnectWatchdogTest(unittest.TestCase):
    def test_stalled_link_releases_and_advertising_restores(self):
        loop = method(SOURCE.read_text(), "void SerialBLEInterface::loop()")
        source = HARNESS.replace("@LOOP@", loop)
        with tempfile.TemporaryDirectory(prefix="meshcore-ble-watchdog-") as temp:
            cpp = Path(temp) / "watchdog.cpp"
            binary = Path(temp) / "watchdog"
            cpp.write_text(source)
            built = subprocess.run(
                ["c++", "-std=c++17", "-I" + str(ROOT / "src"),
                 str(cpp), "-o", str(binary)],
                capture_output=True, text=True,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
