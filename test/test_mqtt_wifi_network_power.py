#!/usr/bin/env python3
"""Execute MQTT's production SNTP rotation, retry, and transport health recovery."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_mqtt_transport_results import method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = "src/helpers/bridges/MQTTBridge.cpp"

PREAMBLE = r'''
#include <helpers/WiFiConnectionDiagnostics.h>
#include <helpers/MQTTConnectionPolicy.h>
#include <helpers/MQTTConnectionHealth.h>
#include <helpers/MQTTTransportHealth.h>
#include <helpers/esp32/SntpOperationCoordinator.h>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <ctime>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/time.h>
#include <vector>

#define ESP_PLATFORM 1
#define WITH_SNMP 1
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
using wifi_mode_t = int;
constexpr int ESP_OK = 0;
constexpr int WIFI_STA = 1;
constexpr int WL_CONNECTED = 3, WL_DISCONNECTED = 0, RUNTIME_MQTT_SLOTS = 3;
constexpr int SNTP_SYNC_STATUS_RESET = 0;
#ifdef ESP_PLATFORM
constexpr int SNTP_SYNC_STATUS_COMPLETED = 1;
#endif
static constexpr uint32_t MQTT_NTP_REFRESH_TIMEOUT_MS = 15000U;
#ifndef ESP_PLATFORM
static constexpr uint32_t MQTT_NTP_NON_ESP_SETTLE_MS = 5000U;
#endif
static constexpr uint32_t MQTT_NTP_MIN_VALID_EPOCH = 1767225600U;
static uint32_t clock_ms = 100U;
static int64_t system_epoch = 0;
static int sdk_mode = WIFI_STA, sdk_mode_result = ESP_OK;
static int ntp_status = SNTP_SYNC_STATUS_RESET, ntp_configs = 0;
static int ntp_stops = 0;
static std::vector<std::string> events;
static std::vector<std::string> ntp_primaries;
static std::function<void()> on_client_stop;

static void require(bool value, const char* reason) {
  if (!value) throw std::runtime_error(reason);
}
uint32_t millis() { return clock_ms; }
time_t fake_time(void*) { return static_cast<time_t>(system_epoch); }
int fake_settimeofday(const timeval* value, void*) {
  system_epoch = value->tv_sec;
  return 0;
}
#define time fake_time
#define settimeofday fake_settimeofday

struct WiFiModel {
  int state = WL_CONNECTED;
  bool reconnect = true;
  int disconnects = 0, offs = 0;
  int status() const { return state; }
  int getMode() const { return sdk_mode; }
  void setAutoReconnect(bool value) { reconnect = value; }
  void disconnect(bool, bool) {
    ++disconnects;
    state = WL_DISCONNECTED;
    events.push_back("wifi-disconnect");
  }
  void mode(int value) {
    sdk_mode = value;
    ++offs;
    events.push_back("wifi-off");
  }
} WiFi;

void sntp_set_sync_status(int value) { ntp_status = value; }
int sntp_get_sync_status() { return ntp_status; }
void esp_sntp_stop() {
  ++ntp_stops;
  events.push_back("sntp-stop");
}
void configTime(int, int, const char* primary, const char*, const char*) {
  ++ntp_configs;
  ntp_primaries.push_back(primary);
  events.push_back("sntp-start");
}
void configTime(int a, int b, const char* primary) {
  configTime(a, b, primary, nullptr, nullptr);
}

struct Client {
  bool started = true, fail_stop = false, live = false;
  int stops = 0;
  uint32_t publish_acks = 0;
  uint32_t getPublishAck() const { return publish_acks; }
  void serviceOutboxDiagnostics() {}
  bool isStarted() const { return started; }
  bool connected() const { return live; }
  void disconnect() {
    ++stops;
    events.push_back("tls-stop");
    if (!fail_stop) started = live = false;
    if (on_client_stop) on_client_stop();
  }
};
struct Board {
  bool ota = false;
  bool isOTAUpdateRunning() const { return ota; }
};
struct Snmp {
  bool running = false;
  bool isRunning() const { return running; }
};
struct Rtc {
  uint32_t epoch = 0;
  uint32_t getCurrentTime() const { return epoch; }
  void setCurrentTime(uint32_t value) { epoch = value; }
};
struct Prefs {};
const char* effectiveNtpPrimary(const Prefs*) { return "custom"; }
#ifdef ESP_PLATFORM
static void fillNtpServerList(const Prefs*, const char* servers[], int& count) {
  static const char* names[] = {"custom", "pool", "google", "cloudflare", "aws", "nist"};
  count = 6;
  for (int index = 0; index < count; ++index) servers[index] = names[index];
}
#endif

@HELPERS@

struct MQTTBridge {
  struct PublishMemory { void invalidate() {} } _publish_memory;
  static constexpr int kMaxNtpServers = 6;
  struct Slot {
    bool enabled = true, connected = false, circuit_breaker_tripped = true;
    uint32_t connected_at_ms = 0, last_reconnect_attempt = 200U;
    uint8_t reconnect_backoff = 5;
    Client* client = nullptr;
    uint32_t connect_failures = 0, current_outage_started_ms = 0;
    MQTTTransportHealth::State transport_health;
  } _slots[RUNTIME_MQTT_SLOTS];
  using MQTTSlot = Slot;
  Client clients[RUNTIME_MQTT_SLOTS];
  std::atomic<bool> _slot_attempt_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _slot_transport_fault_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _slot_health_reset_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _slot_health_recovering[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<uint32_t> _slot_health_recoveries[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<uint8_t> _slot_health_reason[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<uint32_t> _slot_health_ack_wait_secs[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _status_publish_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _ntp_synced{false}, _fresh_ntp_this_boot{false};
  std::atomic<uint32_t> _backward_clock_reset_epoch{0};
  bool _manage_wifi = true, _wifi_status_initialized = true;
  bool _ntp_force_requested = false, _ntp_diag_requested = false;
  bool _ntp_estimate_requested = false, _ntp_sync_pending = true;
  bool _ntp_estimate_done = false, _ntp_estimate_ok = false;
  uint32_t _ntp_estimate_epoch = 0;
  std::atomic<bool> _stop_requested{false};
  bool _ntp_refresh_pending = false;
  uint32_t _ntp_refresh_started_at = 0, _ntp_refresh_previous_epoch = 0;
  uint32_t _ntp_refresh_retry_at = 0, _last_ntp_sync = 0;
  uint32_t _last_wifi_check = 0, _last_slot_reconnect_ms = 0;
  uint8_t _background_ntp_server = 0;
  Board board;
  Board* _board = &board;
  Snmp snmp;
  Snmp* _snmp_agent = &snmp;
  Rtc rtc;
  Rtc* _rtc = &rtc;
  Prefs prefs;
  Prefs* _obs = &prefs;
  int initializes = 0, cache_updates = 0;
  mesh::sntp_coord::OperationLease _ntp_refresh_operation{
      mesh::sntp_coord::processWideCoordinator(), stopMqttSntpOperation};
  MQTTBridge() {
    for (int index = 0; index < RUNTIME_MQTT_SLOTS; ++index) {
      _slots[index].client = &clients[index];
      _slot_attempt_pending[index] = true;
      _status_publish_pending[index] = true;
    }
  }
  void updateCachedConnectionStatus() { ++cache_updates; }
  bool isRunning() const { return true; }
  bool requestNtpTimeEstimate();
  bool hasPendingSlotConnection() const;
  void serviceSlotTransportHealth(uint32_t now);
  bool recoverSlotTransport(int index, uint32_t now);
  void updateSlotTransportDiag(int index, uint32_t now);
  void serviceBackgroundNtp(uint32_t now);
  void cancelNtpRefresh();
  void refreshNTP();
  void pollNtpRefresh(uint32_t now);
};

@METHODS@

static void resetEnvironment() {
  clock_ms = 100U;
  system_epoch = 0;
  sdk_mode = WIFI_STA;
  sdk_mode_result = ESP_OK;
  ntp_status = SNTP_SYNC_STATUS_RESET;
  ntp_configs = ntp_stops = 0;
  events.clear();
  ntp_primaries.clear();
  on_client_stop = nullptr;
  WiFi = {};
}

static constexpr uint32_t ntp_retry_interval = 5000U;

static void failedSdkStopKeepsSlotReservationUntilStopSucceeds() {
  resetEnvironment();
  MQTTBridge bridge;
  for (auto& pending : bridge._slot_attempt_pending) pending = false;
  bridge.clients[1].fail_stop = true;
  bridge._slot_transport_fault_pending[1] = true;
  bridge.serviceSlotTransportHealth(clock_ms);
  require(bridge._slot_transport_fault_pending[1] && bridge.hasPendingSlotConnection(),
          "failed SDK stop abandoned a running MQTT transport");
  // A late CONNACK sets a fresh-session latch; it does not prove the failed
  // SDK stop completed or free admission.
  bridge._slots[1].connected = bridge.clients[1].live = true;
  bridge._slot_attempt_pending[1] = false;
  bridge._slot_health_reset_pending[1] = true;
  bridge.serviceSlotTransportHealth(++clock_ms);
  require(bridge.clients[1].started && bridge.hasPendingSlotConnection(),
          "late CONNACK released failed-stop reservation");
  require(!bridge._slots[1].connected,
          "failed transport was advertised healthy");
  require(bridge._slots[1].transport_health.recoveries == 0,
          "failed SDK stop was counted as recovered");
  bridge.clients[1].fail_stop = false;
  bridge.serviceSlotTransportHealth(++clock_ms);
  require(!bridge.clients[1].started && !bridge.hasPendingSlotConnection(),
          "proven successful stop did not release ownership");
  require(bridge._slots[1].transport_health.recoveries == 1,
          "successful owner cleanup was not counted");
  require(WiFi.offs == 0 && WiFi.disconnects == 0,
          "transport recovery stopped WiFi");
}

static void everyNtpServerGetsTheLeadingTimeoutSlot() {
  resetEnvironment();
  MQTTBridge bridge;
  const char* expected[] = {"custom", "pool", "google", "cloudflare", "aws", "nist", "custom"};
  for (const char* primary : expected) {
    bridge.refreshNTP();
    require(ntp_primaries.back() == primary,
            "a blackholed leading server starved configured NTP fallback servers");
    clock_ms += 15000U;
    bridge.pollNtpRefresh(clock_ms);
    require(!bridge._ntp_refresh_pending,
            "failed rotating NTP request exceeded its bounded timeout");
    clock_ms = bridge._ntp_refresh_retry_at;
  }
  require(ntp_configs == 7, "NTP rotation skipped or repeated a timeout slot");
}

static void sntpFailureAndBusyLeaseRetries() {
  for (uint32_t start : {100U, UINT32_MAX - 4000U, UINT32_MAX - ntp_retry_interval + 1U}) {
    resetEnvironment();
    MQTTBridge bridge;
    clock_ms = start;
    bridge.serviceBackgroundNtp(clock_ms);
    require(ntp_configs == 1 && bridge._ntp_refresh_pending, "boot NTP did not start");
    clock_ms += 15000U;
    bridge.serviceBackgroundNtp(clock_ms);
    const uint32_t failed_at = clock_ms;
    const uint32_t retry = mqttNtpFailureRetryAt(failed_at);
    require(bridge._ntp_refresh_retry_at == retry && !bridge._ntp_refresh_pending,
            "failed SNTP kept running or lost its bounded retry");
    clock_ms = failed_at + ntp_retry_interval - 1U;
    bridge.serviceBackgroundNtp(clock_ms);
    require(ntp_configs == 1, "failed SNTP retried before retry deadline");
    clock_ms = retry;
    bridge.serviceBackgroundNtp(clock_ms);
    require(ntp_configs == 2 && bridge._ntp_refresh_pending, "SNTP retry never became due");
  }
  resetEnvironment();
  {
    MQTTBridge bridge;
    mesh::sntp_coord::OperationLease foreground_ntp(mesh::sntp_coord::processWideCoordinator());
    require(foreground_ntp.tryAcquire(), "foreground SNTP owner did not acquire");
    bridge.serviceBackgroundNtp(clock_ms);
    require(ntp_configs == 0 && !bridge._ntp_refresh_pending
            && bridge._ntp_refresh_retry_at == clock_ms + 5000U,
            "SNTP contention was treated as failed internet or busy-waited");
    clock_ms += 4999U;
    foreground_ntp.release();
    bridge.serviceBackgroundNtp(clock_ms);
    require(ntp_configs == 0, "SNTP busy-owner retry was not bounded");
    clock_ms += 1U;
    bridge.serviceBackgroundNtp(clock_ms);
    require(ntp_configs == 1, "released SNTP owner never admitted retry");
  }
}

static void retainedClockAndFreshNtpSyncUpdateRtcAndReleaseLease() {
  resetEnvironment();
  MQTTBridge bridge;
  bridge.rtc.epoch = MQTT_NTP_MIN_VALID_EPOCH + 100U;
  bridge.serviceBackgroundNtp(100U);
  require(bridge._ntp_synced && !bridge._fresh_ntp_this_boot,
          "retained RTC became fake proof of fresh internet");
  require(ntp_configs == 1 && bridge._ntp_refresh_pending,
          "retained clock suppressed fresh NTP request");
  system_epoch = MQTT_NTP_MIN_VALID_EPOCH + 200U;
  ntp_status = SNTP_SYNC_STATUS_COMPLETED;
  clock_ms = 101U;
  bridge.serviceBackgroundNtp(clock_ms);
  require(bridge._fresh_ntp_this_boot && bridge._last_ntp_sync == clock_ms
          && !bridge._ntp_refresh_pending && ntp_stops == 1,
          "fresh NTP did not update the RTC and release SNTP");
  require(WiFi.offs == 0 && WiFi.disconnects == 0,
          "NTP or MQTT state mutated WiFi power");
}

int main(int argc, char** argv) {
  require(argc == 2, "one case is required");
  const std::string name = argv[1];
  try {
    if (name == "failed-stop-wake") failedSdkStopKeepsSlotReservationUntilStopSucceeds();
    else if (name == "ntp-retry") sntpFailureAndBusyLeaseRetries();
    else if (name == "ntp-rotation") everyNtpServerGetsTheLeadingTimeoutSlot();
    else if (name == "ntp-proof") retainedClockAndFreshNtpSyncUpdateRtcAndReleaseLease();
    else return 2;
  } catch (const std::exception& error) {
    std::cerr << name << ": " << error.what() << '\n';
    return 1;
  }
  std::cout << "PASS: " << name << '\n';
  return 0;
}
'''

PORTABLE_MAIN = r'''
int main() {
  try {
    resetEnvironment();
    MQTTBridge bridge;
    clock_ms = UINT32_MAX - 1000U;
    bridge.refreshNTP();
    require(ntp_configs == 1 && bridge._ntp_refresh_pending,
            "portable async SNTP request did not start");
    require(!bridge._ntp_refresh_operation.owns(),
            "portable stack unexpectedly acquired ESP32 SNTP lease");
    clock_ms += 4999U;
    bridge.pollNtpRefresh(clock_ms);
    require(bridge._ntp_refresh_pending && !bridge._ntp_synced,
            "portable stack accepted clock before five-second settlement");
    system_epoch = MQTT_NTP_MIN_VALID_EPOCH + 200U;
    clock_ms += 1U;
    bridge.pollNtpRefresh(clock_ms);
    require(!bridge._ntp_refresh_pending && bridge._ntp_synced
            && bridge._fresh_ntp_this_boot && bridge.rtc.epoch == system_epoch
            && bridge._last_ntp_sync == clock_ms,
            "portable stack lost its existing settled-clock behavior");
    bridge.refreshNTP();
    WiFi.state = WL_DISCONNECTED;
    bridge.pollNtpRefresh(clock_ms);
    require(!bridge._ntp_refresh_pending
            && bridge._ntp_refresh_retry_at == clock_ms + 5000U,
            "portable WiFi-failure retry changed");
    require(ntp_stops == 0, "portable SNTP called ESP32 SDK cleanup");
    require(mqttNtpFailureRetryAt(UINT32_MAX - 4999U) == 1U,
            "portable retry lost its wrap-safe zero-sentinel handling");
    require(mqttNtpRetryAt(clock_ms) == clock_ms + 5000U,
            "portable busy-owner retry unexpectedly changed");
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  std::cout << "PASS: portable\n";
  return 0;
}
'''


class MqttWiFiNetworkPowerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("a host C++ compiler is required")
        helpers = "\n".join(method(BRIDGE, signature) for signature in (
            "static uint32_t mqttNtpFailureRetryAt(",
            "static bool mqttNtpRefreshDue(",
            "static uint32_t mqttNtpRetryAt(",
            "static void stopMqttSntpOperation(",
        ))
        methods = "\n".join(method(BRIDGE, signature) for signature in (
            "void MQTTBridge::serviceBackgroundNtp(",
            "void MQTTBridge::cancelNtpRefresh(",
            "void MQTTBridge::refreshNTP(",
            "void MQTTBridge::pollNtpRefresh(",
            "bool MQTTBridge::requestNtpTimeEstimate(",
            "bool MQTTBridge::hasPendingSlotConnection(",
            "void MQTTBridge::serviceSlotTransportHealth(",
            "bool MQTTBridge::recoverSlotTransport(",
            "void MQTTBridge::updateSlotTransportDiag(",
        ))
        cls.temporary = tempfile.TemporaryDirectory(prefix="meshcore-mqtt-network-power-")
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        cpp = directory / "power.cpp"
        source = PREAMBLE.replace("@HELPERS@", helpers).replace("@METHODS@", methods)
        cpp.write_text(source, encoding="ascii")
        cls.binary = directory / "power"
        built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                "-I", str(ROOT / "src"), str(cpp), "-o", str(cls.binary)],
                               capture_output=True, text=True, timeout=60)
        if built.returncode:
            raise RuntimeError(built.stdout + built.stderr)
        portable_methods = "\n".join(method(BRIDGE, signature) for signature in (
            "void MQTTBridge::cancelNtpRefresh(",
            "void MQTTBridge::refreshNTP(",
            "void MQTTBridge::pollNtpRefresh(",
        ))
        portable_source = PREAMBLE[:PREAMBLE.index("static constexpr uint32_t ntp_retry_interval")]
        portable_source = (portable_source.replace("#define ESP_PLATFORM 1", "")
                           .replace("@HELPERS@", helpers)
                           .replace("@METHODS@", portable_methods) + PORTABLE_MAIN)
        portable_cpp = directory / "portable.cpp"
        portable_cpp.write_text(portable_source, encoding="ascii")
        cls.portable_binary = directory / "portable"
        built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                "-I", str(ROOT / "src"), str(portable_cpp),
                                "-o", str(cls.portable_binary)],
                               capture_output=True, text=True, timeout=60)
        if built.returncode:
            raise RuntimeError(built.stdout + built.stderr)

    def run_case(self, name):
        checked = subprocess.run([str(self.binary), name], capture_output=True,
                                 text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn("PASS: " + name, checked.stdout)

    def test_failed_suspend_late_connect_and_foreground_wake_keep_sdk_reservation(self):
        self.run_case("failed-stop-wake")

    def test_sntp_failure_and_busy_lease_have_different_bounded_retries(self):
        self.run_case("ntp-retry")

    def test_all_six_ntp_servers_lead_a_bounded_request_before_rotation_repeats(self):
        self.run_case("ntp-rotation")

    def test_retained_clock_and_fresh_ntp_update_rtc_without_disabling_wifi(self):
        self.run_case("ntp-proof")

    def test_portable_non_esp_stack_preserves_settle_and_short_retry_policy(self):
        checked = subprocess.run([str(self.portable_binary)], capture_output=True,
                                 text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn("PASS: portable", checked.stdout)


if __name__ == "__main__":
    unittest.main()
