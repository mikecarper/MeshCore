#!/usr/bin/env python3
"""Run the actual task wrapper and BLE startup failure handling."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]

RTOS = r'''
#pragma once
#include <cstdint>
using BaseType_t = int;
using UBaseType_t = unsigned;
using TaskHandle_t = void*;
using TaskFunction_t = void (*)(void*);
using configSTACK_DEPTH_TYPE = uint16_t;
constexpr BaseType_t pdPASS = 1;
'''

HARNESS = r'''
#include "Arduino.h"
#include <cassert>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <helpers/BluetoothMac.h>
#include <helpers/BleMotaProtocol.h>
#include <helpers/nrf52/SecuritySessionTimer.h>
#ifndef COMPANION_FEATURE_BLE_MOTA_SOURCE
#define COMPANION_FEATURE_BLE_MOTA_SOURCE 1
#endif
#ifndef COMPANION_FEATURE_BLE_DFU
#define COMPANION_FEATURE_BLE_DFU 1
#endif
#ifndef COMPANION_BLE_PRPH_MTU
#define COMPANION_BLE_PRPH_MTU 247
#endif
#ifndef COMPANION_BLE_PRPH_EVENT_LENGTH
#define COMPANION_BLE_PRPH_EVENT_LENGTH 100
#endif
#ifndef COMPANION_BLE_PRPH_HVN_QUEUE
#define COMPANION_BLE_PRPH_HVN_QUEUE 3
#endif
#ifndef COMPANION_BLE_PRPH_WRCMD_QUEUE
#define COMPANION_BLE_PRPH_WRCMD_QUEUE 1
#endif
#define BLE_DEBUG_PRINTLN(...) do {} while (0)
constexpr int BANDWIDTH_MAX = 3, NRF_SUCCESS = 0, ERROR_NONE = 0;
constexpr int NRF_ERROR_INVALID_STATE = 8;
constexpr int BLE_GAP_ADDR_TYPE_RANDOM_STATIC = 1, BLE_TX_POWER = 4;
constexpr int BLE_MIN_CONN_INTERVAL = 12, BLE_MAX_CONN_INTERVAL = 24;
constexpr int BLE_SLAVE_LATENCY = 4, BLE_CONN_SUP_TIMEOUT = 200;
constexpr int SECMODE_ENC_WITH_MITM = 1, SECMODE_NO_ACCESS = 0;
constexpr int CHR_PROPS_NOTIFY = 1, CHR_PROPS_WRITE = 2;
constexpr int BLE_GAP_ADV_TYPE_CONNECTABLE_SCANNABLE_UNDIRECTED = 0;
constexpr int BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE = 2;
constexpr int BLE_ADV_INTERVAL_MIN = 32, BLE_ADV_INTERVAL_MAX = 244, BLE_ADV_FAST_TIMEOUT = 30;
struct ble_gap_addr_t { int addr_type; uint8_t addr[6]; };
struct ble_gap_conn_params_t { unsigned min_conn_interval, max_conn_interval, slave_latency, conn_sup_timeout; };
int sd_ble_gap_addr_set(ble_gap_addr_t*) { return NRF_SUCCESS; }
int sd_ble_gap_addr_get(ble_gap_addr_t* a) { memset(a, 0, sizeof(*a)); return NRF_SUCCESS; }
@FORMAT_DEVICE_NAME@
int sd_softdevice_is_enabled(uint8_t* enabled) { *enabled = 0; return NRF_SUCCESS; }
namespace mesh_nrf52 {
int softdeviceIsEnabled(uint8_t& enabled) {
  return sd_softdevice_is_enabled(&enabled);
}
}
int sd_ble_gap_ppcp_set(ble_gap_conn_params_t*) { return NRF_SUCCESS; }
namespace mesh {
struct Logging { void println(const char*) {} };
Logging& usbLoggingPort() { static Logging port; return port; }
}

static int fail_service = 0;
static bool fail_softdevice = false;
static unsigned last_depth;
extern "C" BaseType_t __wrap_xTaskCreate(TaskFunction_t, const char*,
    configSTACK_DEPTH_TYPE, void*, UBaseType_t, TaskHandle_t*);
extern "C" BaseType_t __real_xTaskCreate(TaskFunction_t, const char* name,
    configSTACK_DEPTH_TYPE depth, void*, UBaseType_t, TaskHandle_t* out) {
  last_depth = depth;
  if (out) *out = reinterpret_cast<void*>(1);
  return pdPASS;
}
struct Settings {
  template<class... T> void clearBonds(T...) {}
  template<class... T> void setMITM(T...) {}
  template<class... T> void setPIN(T...) {}
  template<class... T> void setIOCaps(T...) {}
  template<class... T> void setPairPasskeyCallback(T...) {}
  template<class... T> void setPairCompleteCallback(T...) {}
  template<class... T> void setConnectCallback(T...) {}
  template<class... T> void setDisconnectCallback(T...) {}
  template<class... T> void setSecuredCallback(T...) {}
  template<class... T> void setType(T...) {}
  template<class... T> void addFlags(T...) {}
  template<class... T> void addTxPower(T...) {}
  template<class... T> void addService(T...) {}
  template<class... T> void addName(T...) {}
  template<class... T> void setInterval(T...) {}
  template<class... T> void setFastTimeout(T...) {}
  template<class... T> void restartOnDisconnect(T...) {}
};
struct BluefruitStub {
  Settings Security, Periph, Advertising, ScanResponse;
  int starts = 0;
  uint16_t mtu = 0, event_length = 0;
  uint8_t hvn_queue = 0, wrcmd_queue = 0;
  void configPrphConn(uint16_t configured_mtu, uint16_t configured_event_length,
                      uint8_t configured_hvn_queue,
                      uint8_t configured_wrcmd_queue) {
    mtu = configured_mtu;
    event_length = configured_event_length;
    hvn_queue = configured_hvn_queue;
    wrcmd_queue = configured_wrcmd_queue;
  }
  bool begin() {
    ++starts;
    if (fail_softdevice) return false;
    TaskHandle_t task;
    // The pinned LTO core resolves these internal calls directly, rather than
    // through the application's linker wrapper.
    __wrap_xTaskCreate(nullptr, "BLE", 1280, nullptr, 3, &task);
    __wrap_xTaskCreate(nullptr, "SOC", 200, nullptr, 3, &task);
    return true;
  }
  void setTxPower(int) {}
  void setName(const char*) {}
  template<class T> void setEventCallback(T) {}
} Bluefruit;
struct Service {
  int id;
  int begin() { return fail_service == id ? 1 : ERROR_NONE; }
  template<class... T> void setPermission(T...) {}
  template<class... T> void setRxCallback(T...) {}
  template<class... T> void setProperties(T...) {}
  template<class... T> void setMaxLen(T...) {}
  template<class... T> void setUserDescriptor(T...) {}
  template<class... T> void setWriteCallback(T...) {}
};
struct MotaStream {
  template<class... T> void setSender(T...) {}
  void setActive(bool) {}
};
struct SerialBLEInterface {
  @STARTUP_FIELDS@
  std::atomic<bool> _companionDataSeen{false};
  SecuritySessionTimer _companion_start_timer;
  std::atomic<bool> _successfulConnectionPending{false};
  std::atomic<uint32_t> _successfulConnectionStarted{0};
  std::atomic<bool> _bondedOnlyRecoveryPending{false}, _advertisingSuppressed{false};
  bool _stealth_pair_once = false, _bonded_only = false, _bonded_only_configure_pending = false;
  mesh::companion::BluetoothPeerIdentity _pending_bonded_peer;
  Service bleuart{1}, _mota_service{2}, _mota_request{3}, _mota_response{4}, bledfu{5};
  MotaStream _mota_stream;
  static void onPairingPasskey() {}
  static void onPairingComplete() {}
  static void onConnect() {}
  static void onDisconnect() {}
  static void onSecured() {}
  static void onBLEEvent() {}
  static void onBleUartRX() {}
  static void sendMotaRequest() {}
  static void onMotaResponse() {}
  void configureBondedOnlyAdvertising(const mesh::companion::BluetoothPeerIdentity&, bool) {}
  bool begin(const char*, const char*, uint32_t, const uint8_t* = nullptr,
             bool = false, bool = false,
             const mesh::companion::BluetoothPeerIdentity* = nullptr);
};
static SerialBLEInterface* instance = nullptr;
@BEGIN@
int main() {
  __wrap_xTaskCreate(nullptr, "loop", 1024, nullptr, 1, nullptr);
  assert(last_depth == 2048);
  __wrap_xTaskCreate(nullptr, "callback", 768, nullptr, 1, nullptr);
  assert(last_depth == 768);
  __wrap_xTaskCreate(nullptr, nullptr, 100, nullptr, 1, nullptr);
  assert(last_depth == 100);
  for (int fault = 0; fault <= 6; ++fault) {
#if !COMPANION_FEATURE_BLE_MOTA_SOURCE
    if (fault >= 3 && fault <= 5) continue;
#endif
    fail_softdevice = fault == 1;
    fail_service = fault >= 2 ? fault - 1 : 0;
    SerialBLEInterface port;
    const int starts = Bluefruit.starts;
    // UART is the required Companion transport.  The mOTA and Nordic DFU
    // services are extensions: registration failure must leave UART BLE
    // available for pairing, diagnostics, and recovery.
    const bool expected = fault != 1 && fault != 2;
    assert(port.begin("MC-", "T096", 123456) == expected);
    assert(Bluefruit.mtu == COMPANION_BLE_PRPH_MTU);
    assert(Bluefruit.event_length == COMPANION_BLE_PRPH_EVENT_LENGTH);
    assert(Bluefruit.hvn_queue == COMPANION_BLE_PRPH_HVN_QUEUE);
    assert(Bluefruit.wrcmd_queue == COMPANION_BLE_PRPH_WRCMD_QUEUE);
    assert(Bluefruit.starts == starts + 1);
    assert((instance == &port) == expected);
    // Retrying a failed partial init must never allocate another stack/FIFO.
    for (int retry = 0; retry < 4; ++retry) {
      assert(port.begin("MC-", "T096", 123456) == expected);
      assert(Bluefruit.starts == starts + 1);
    }
  }
}
'''


class Nrf52BleStartupTest(unittest.TestCase):
    def test_reported_startup_failures_are_not_reinitialized(self):
        serial_source = (ROOT / "src/helpers/nrf52/SerialBLEInterface.cpp").read_text()
        formatter = method(serial_source, "static bool formatDeviceName(")
        begin = method(serial_source, "bool SerialBLEInterface::begin(")
        header = (ROOT / "src/helpers/nrf52/SerialBLEInterface.h").read_text()
        # Use the real in-class startup initializers, too.
        fields = "\n".join(line for line in header.splitlines()
                           if "bool _begin_" in line
                           or "bool _mota_available" in line
                           or "char _begin_failure" in line
                           or "char _active_name" in line)
        self.assertTrue(fields)
        source = (HARNESS.replace("@BEGIN@", begin)
                  .replace("@STARTUP_FIELDS@", fields)
                  .replace("@FORMAT_DEVICE_NAME@", formatter))
        with tempfile.TemporaryDirectory(prefix="meshcore-ble-start-") as temp:
            temp = Path(temp)
            (temp / "Arduino.h").write_text(RTOS)
            for name in ("FreeRTOS.h", "task.h"):
                (temp / name).write_text('#include "Arduino.h"\n')
            cpp = temp / "startup.cpp"
            cpp.write_text(source)
            binary = temp / "startup"
            for mota in (0, 1):
                with self.subTest(mota=mota):
                    built = subprocess.run([
                        "c++", "-std=c++17", "-DNRF52_PLATFORM=1",
                        f"-DCOMPANION_FEATURE_BLE_MOTA_SOURCE={mota}", "-I" + str(temp),
                        "-I" + str(ROOT / "src"), str(cpp), str(ROOT / "src/Nrf52LoopStack.cpp"),
                        "-o", str(binary)], text=True, capture_output=True)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    result = subprocess.run([str(binary)], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
