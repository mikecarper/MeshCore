#!/usr/bin/env python3
"""Execute MQTT's production WiFi/SNTP outage handoff with controlled SDK calls.

The actual bridge methods and shared policies run without hardware, network,
Arduino, or PlatformIO. SDK calls observe suspension publication ordering and
can fail independently of MQTT and NTP state.
"""

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
#include <helpers/WiFiNetworkPowerPolicy.h>
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
constexpr int ESP_OK = 0, ESP_FAIL = -1, WIFI_MODE_NULL = 0;
constexpr int WIFI_STA = 1, WIFI_MODE_AP = 2, WIFI_OFF = 0;
constexpr int WL_CONNECTED = 3, WL_DISCONNECTED = 0, RUNTIME_MQTT_SLOTS = 3;
constexpr int SNTP_SYNC_STATUS_RESET = 0, SNTP_SYNC_STATUS_COMPLETED = 1;
static constexpr uint32_t MQTT_NTP_REFRESH_TIMEOUT_MS = 15000U;
static constexpr uint32_t MQTT_NTP_NON_ESP_SETTLE_MS = 5000U;
static constexpr uint32_t MQTT_NTP_MIN_VALID_EPOCH = 1767225600U;
static uint32_t clock_ms = 100U;
static int64_t system_epoch = 0;
static int sdk_mode = WIFI_STA, sdk_mode_result = ESP_OK;
static bool station_mutation_allowed = true, espnow_constrained = false;
static int ntp_status = SNTP_SYNC_STATUS_RESET, ntp_configs = 0;
static int ntp_stops = 0, channel_restores = 0;
static int channel_restore_result = ESP_OK;
static bool require_quiescent_wifi_stop = false;
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

int esp_wifi_get_mode(wifi_mode_t* mode) {
  *mode = sdk_mode;
  return sdk_mode_result;
}
bool mqttStationWiFiMutationAllowed() {
  return station_mutation_allowed && sdk_mode_result == ESP_OK
      && !(sdk_mode & WIFI_MODE_AP);
}
namespace mesh { namespace wifi {
bool espNowChannelConstrained() { return espnow_constrained; }
int restoreEspNowChannel() {
  ++channel_restores;
  events.push_back("espnow");
  return channel_restore_result;
}
} }

struct WiFiModel {
  int state = WL_CONNECTED;
  bool reconnect = true;
  int disconnects = 0, offs = 0;
  int status() const { return state; }
  int getMode() const { return sdk_mode; }
  void setAutoReconnect(bool value) {
    reconnect = value;
    if (!value && require_quiescent_wifi_stop) {
      require(mesh::wifi::networkPower().backgroundSuspended(),
              "WiFi stopped before worker published transport quiescence");
    }
  }
  void disconnect(bool stop, bool erase) {
    require(!stop && !erase, "suspension erased credentials or ESP-NOW driver");
    ++disconnects;
    state = WL_DISCONNECTED;
    events.push_back("wifi-disconnect");
  }
  void mode(int value) {
    require(value == WIFI_OFF, "unexpected station mode change");
    if (require_quiescent_wifi_stop) {
      require(mesh::wifi::networkPower().backgroundSuspended(),
              "WiFi driver stopped before the safe handoff");
    }
    sdk_mode = value;
    ++offs;
    events.push_back("wifi-off");
  }
} WiFi;

void sntp_set_sync_status(int value) { ntp_status = value; }
int sntp_get_sync_status() { return ntp_status; }
void esp_sntp_stop() {
  if (require_quiescent_wifi_stop) {
    require(!mesh::wifi::networkPower().backgroundSuspended(),
            "SNTP was stopped after publishing transport quiescence");
  }
  ++ntp_stops;
  events.push_back("sntp-stop");
}
void configTime(int, int, const char* primary, const char*, const char*) {
  require(!mesh::wifi::networkPower().backgroundSuspended(),
          "SNTP restarted during suspension");
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
  bool isStarted() const { return started; }
  bool connected() const { return live; }
  void disconnect() {
    require(!mesh::wifi::networkPower().backgroundSuspended(),
            "TLS stopped after handing station off");
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
  uint32_t _network_publish_acks[RUNTIME_MQTT_SLOTS] = {};
  bool _network_espnow_restore_pending = false;
  uint32_t _network_espnow_restore_at = 0;
  mesh::wifi::NetworkPowerPolicy _network_power_policy;
  std::atomic<bool> _network_suspended{false};
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
  bool initializeWiFiInTask() {
    require(!mesh::wifi::networkPower().backgroundSuspended(),
            "station resumed before clearing suspension handoff");
    ++initializes;
    sdk_mode = WIFI_STA;
    WiFi.reconnect = true;
    return true;
  }
  void updateCachedConnectionStatus() { ++cache_updates; }
  bool isRunning() const { return true; }
  bool isNetworkSuspended() const { return _network_suspended.load(); }
  bool requestNtpTimeEstimate();
  bool hasPendingSlotConnection() const;
  void serviceSlotTransportHealth(uint32_t now);
  bool recoverSlotTransport(int index, uint32_t now);
  void updateSlotTransportDiag(int index, uint32_t now);
  bool serviceNetworkPower(uint32_t now);
  void serviceEspNowChannelRestore(uint32_t now);
  void serviceBackgroundNtp(uint32_t now);
  void cancelNtpRefresh();
  void refreshNTP();
  void pollNtpRefresh(uint32_t now);
};

@METHODS@

static void resetEnvironment() {
  auto& power = mesh::wifi::networkPower();
  power.setManaged(false);
  power.setHold(mesh::wifi::NetworkPowerHold::CompanionClient, false);
  power.setHold(mesh::wifi::NetworkPowerHold::WebConfig, false);
  power.setHold(mesh::wifi::NetworkPowerHold::OtaSeeder, false);
  power.consumeProbeRequest();
  power.setManaged(true);
  clock_ms = 100U;
  system_epoch = 0;
  sdk_mode = WIFI_STA;
  sdk_mode_result = ESP_OK;
  station_mutation_allowed = true;
  espnow_constrained = false;
  ntp_status = SNTP_SYNC_STATUS_RESET;
  ntp_configs = ntp_stops = channel_restores = 0;
  channel_restore_result = ESP_OK;
  require_quiescent_wifi_stop = false;
  events.clear();
  ntp_primaries.clear();
  on_client_stop = nullptr;
  WiFi = {};
}

static constexpr uint32_t grace = 300000U, interval = 1800000U, probe = 120000U;

static void failureStopsBothServicesBeforeWifi() {
  resetEnvironment();
  MQTTBridge bridge;
  require(bridge.serviceNetworkPower(clock_ms), "cold bridge was suspended");
  bridge.serviceBackgroundNtp(clock_ms);
  require(ntp_configs == 1 && bridge._ntp_refresh_pending,
          "initial NTP was not asynchronous");
  clock_ms += 15000U;
  bridge.serviceBackgroundNtp(clock_ms);
  require(!bridge._ntp_refresh_pending && ntp_stops == 1,
          "timed-out NTP continued running");
  require(bridge._ntp_refresh_retry_at == clock_ms + interval,
          "failed NTP was not deferred 30 minutes");
  // GOT_IP/NTP retry timers and failing SDK handshakes do not renew the clock.
  for (uint32_t offset = 16000U; offset < grace; offset += 1000U) {
    clock_ms = 100U + offset;
    WiFi.state = WL_CONNECTED;
    require(bridge.serviceNetworkPower(clock_ms), "outage grace ended early");
    bridge.serviceBackgroundNtp(clock_ms);
  }
  require(ntp_configs == 1, "failed SNTP retried during its cooldown");
  // Keep one live SNTP lease at the cutoff to prove teardown publication order.
  bridge._ntp_refresh_retry_at = 0;
  bridge.refreshNTP();
  require(bridge._ntp_refresh_operation.owns(), "SNTP request lacks ownership");
  events.clear();
  require_quiescent_wifi_stop = true;
  clock_ms = 100U + grace;
  require(!bridge.serviceNetworkPower(clock_ms), "failed services kept WiFi on");
  require(bridge._network_suspended && mesh::wifi::networkPower().backgroundSuspended(),
          "worker did not hand off its transport suspension");
  require(events == std::vector<std::string>({"sntp-stop", "tls-stop", "tls-stop",
              "tls-stop", "wifi-disconnect", "wifi-off"}),
          "WiFi was disabled before SNTP/TLS teardown");
  require(!bridge._ntp_refresh_pending && !bridge._ntp_refresh_operation.owns()
          && !bridge._ntp_sync_pending && !WiFi.reconnect && sdk_mode == WIFI_OFF,
          "a failed background service retained radio activity");
  for (int index = 0; index < RUNTIME_MQTT_SLOTS; ++index) {
    require(!bridge.clients[index].started && !bridge._slots[index].connected
            && !bridge._slot_attempt_pending[index] && !bridge._status_publish_pending[index],
            "suspension retained a pending transport or publish");
  }
}

static void failedSdkStopWithholdsHandoff() {
  resetEnvironment();
  MQTTBridge bridge;
  bridge.serviceNetworkPower(100U);
  bridge.clients[1].fail_stop = true;
  require(!bridge.serviceNetworkPower(100U + grace),
          "failed SDK stop permitted more background TLS or NTP work");
  require(!bridge._network_suspended && !mesh::wifi::networkPower().backgroundSuspended()
          && sdk_mode == WIFI_STA && WiFi.disconnects == 0 && WiFi.offs == 0,
          "station owner was allowed to stop a still-running TLS client");
  require(!bridge.clients[0].started && bridge.clients[1].started
          && bridge.clients[2].started, "teardown did not stop at failed client");
  bridge.clients[1].fail_stop = false;
  require(!bridge.serviceNetworkPower(101U + grace),
          "retry after successful SDK stop failed to suspend");
  require(!bridge.clients[1].started && !bridge.clients[2].started && WiFi.offs == 1,
          "successful retry leaked a transport or radio");
}

static void failedSuspendLateConnectCannotAbandonTransportOwnership() {
  resetEnvironment();
  MQTTBridge bridge;
  for (auto& pending : bridge._slot_attempt_pending) pending = false;
  bridge.serviceNetworkPower(clock_ms);
  bridge.clients[1].fail_stop = true;
  clock_ms += grace;
  require(!bridge.serviceNetworkPower(clock_ms), "failed stop did not hold handoff");
  require(bridge._slot_transport_fault_pending[1] && bridge.hasPendingSlotConnection(),
          "failed suspension abandoned a running MQTT transport");
  // A foreground owner wakes networking and a late CONNACK sets a fresh-session
  // latch. Neither proves the failed SDK stop completed or frees admission.
  mesh::wifi::networkPower().setHold(mesh::wifi::NetworkPowerHold::WebConfig, true);
  bridge._slots[1].connected = bridge.clients[1].live = true;
  bridge._slot_attempt_pending[1] = false;
  bridge._slot_health_reset_pending[1] = true;
  require(bridge.serviceNetworkPower(++clock_ms), "foreground owner did not keep radio");
  bridge.serviceSlotTransportHealth(clock_ms);
  require(bridge.clients[1].started && bridge.hasPendingSlotConnection(),
          "foreground wake or late CONNACK released failed-stop reservation");
  require(!bridge._slots[1].connected && !bridge._network_suspended,
          "failed transport was advertised healthy or handed station away");
  require(bridge._slots[1].transport_health.recoveries == 0,
          "failed SDK stop was counted as recovered");
  bridge.clients[1].fail_stop = false;
  bridge.serviceSlotTransportHealth(++clock_ms);
  require(!bridge.clients[1].started && !bridge.hasPendingSlotConnection(),
          "proven successful stop did not release ownership");
  require(bridge._slots[1].transport_health.recoveries == 1,
          "successful owner cleanup was not counted");
  require(WiFi.offs == 0 && WiFi.disconnects == 0,
          "transport recovery stopped foreground WiFi");
}

static void recoveryHasOneBoundedWindow() {
  for (uint32_t start : {100U, UINT32_MAX - 10000U}) {
    resetEnvironment();
    MQTTBridge bridge;
    require(bridge.serviceNetworkPower(start), "initial network did not run");
    const uint32_t suspended = start + grace;
    require(!bridge.serviceNetworkPower(suspended), "network did not suspend");
    for (uint32_t offset = 1000U; offset < interval; offset += 1000U) {
      require(!bridge.serviceNetworkPower(suspended + offset),
              "a failed timer woke WiFi during the cooldown");
    }
    require(bridge.initializes == 0, "cooldown restarted station");
    const uint32_t resumed = suspended + interval;
    require(bridge.serviceNetworkPower(resumed), "30-minute recovery was not started");
    require(bridge.initializes == 1 && !bridge._network_suspended
            && !mesh::wifi::networkPower().backgroundSuspended()
            && sdk_mode == WIFI_STA, "recovery did not release station ownership");
    for (int index = 0; index < RUNTIME_MQTT_SLOTS; ++index) {
      require(!bridge._slots[index].circuit_breaker_tripped
              && MQTTConnectionPolicy::reconnectDue(resumed,
                 bridge._slots[index].last_reconnect_attempt,
                 bridge._slots[index].reconnect_backoff, static_cast<uint8_t>(index)),
              "recovery left brokers waiting behind the old breaker");
    }
    require(bridge._ntp_sync_pending && bridge._ntp_refresh_retry_at == 0,
            "recovery did not admit one new NTP attempt");
    for (uint32_t offset = 1000U; offset < probe; offset += 1000U) {
      require(bridge.serviceNetworkPower(resumed + offset), "probe ended early");
    }
    require(bridge.initializes == 1, "probe restarted station repeatedly");
    require(!bridge.serviceNetworkPower(resumed + probe), "failed probe was unbounded");
    require(!bridge.serviceNetworkPower(resumed + probe + interval - 1U),
            "failed probe did not start a fresh 30-minute cooldown");
  }
}

static void foregroundAndEspNowArePreserved() {
  for (int owner = 0; owner < 6; ++owner) {
    resetEnvironment();
    MQTTBridge bridge;
    bridge.serviceNetworkPower(100U);
    if (owner == 0) mesh::wifi::networkPower().setHold(mesh::wifi::NetworkPowerHold::WebConfig, true);
    if (owner == 1) bridge.board.ota = true;
    if (owner == 2) sdk_mode = WIFI_STA | WIFI_MODE_AP;
    if (owner == 3) bridge._ntp_force_requested = true;
    if (owner == 4) bridge._ntp_diag_requested = true;
    if (owner == 5) bridge.snmp.running = true;
    require(bridge.serviceNetworkPower(100U + grace), "foreground owner lost radio");
    require(WiFi.disconnects == 0 && WiFi.offs == 0,
            "background outage disrupted a foreground owner");
  }
  resetEnvironment();
  {
    MQTTBridge bridge;
    espnow_constrained = true;
    bridge.serviceNetworkPower(100U);
    require(!bridge.serviceNetworkPower(100U + grace), "failed STA did not suspend");
    require(!WiFi.reconnect && WiFi.disconnects == 1 && WiFi.offs == 0
            && sdk_mode == WIFI_STA && channel_restores == 1,
            "suspending failed internet disabled independent ESP-NOW radio");
  }
  // An AP can take ownership during the SDK's yielding client stop calls.
  resetEnvironment();
  {
    MQTTBridge bridge;
    bridge.serviceNetworkPower(100U);
    on_client_stop = [] { sdk_mode = WIFI_STA | WIFI_MODE_AP; };
    require(!bridge.serviceNetworkPower(100U + grace), "worker did not suspend transports");
    require(WiFi.disconnects == 0 && WiFi.offs == 0 && (sdk_mode & WIFI_MODE_AP),
            "foreground AP race lost the shared WiFi driver");
  }
}

static void automaticClockEstimateCannotWakeSuspendedNetwork() {
  resetEnvironment();
  MQTTBridge bridge;
  bridge.serviceNetworkPower(100U);
  bridge._ntp_estimate_requested = true;
  require(!bridge.serviceNetworkPower(100U + grace),
          "automatic clock estimate bypassed combined outage grace");
  require(!bridge.serviceNetworkPower(100U + grace + 1U),
          "automatic clock estimate woke failed network immediately");
  require(!bridge.serviceNetworkPower(100U + grace + interval - 1U),
          "automatic clock estimate bypassed the 30-minute cooldown");
  require(bridge.initializes == 0, "background clock request restarted station");
  bridge._ntp_estimate_requested = false;
  WiFi.state = WL_CONNECTED;  // Even a stale connected facade cannot bypass it.
  require(!bridge.requestNtpTimeEstimate() && !bridge._ntp_estimate_requested,
          "automatic clock bootstrap queued new work during suspension");
  require(bridge.serviceNetworkPower(100U + grace + interval),
          "scheduled recovery did not admit clock work");
  require(bridge.requestNtpTimeEstimate() && bridge._ntp_estimate_requested,
          "scheduled recovery still blocked automatic clock bootstrap");
}

static void deferredEspNowChannelRepair() {
  for (uint32_t start : {100U, UINT32_MAX - 500U, UINT32_MAX - 999U}) {
    resetEnvironment();
    MQTTBridge bridge;
    espnow_constrained = true;
    clock_ms = start - grace;
    bridge.serviceNetworkPower(clock_ms);
    clock_ms = start;
    channel_restore_result = ESP_FAIL;
    require(!bridge.serviceNetworkPower(clock_ms), "failed STA did not suspend");
    require(bridge._network_espnow_restore_pending && channel_restores == 1
            && WiFi.disconnects == 1 && WiFi.offs == 0,
            "failed channel write was not retained independently of WiFi stop");
    for (uint32_t offset : {100U, 250U, 500U, 999U}) {
      clock_ms = start + offset;
      require(!bridge.serviceNetworkPower(clock_ms), "channel repair resumed failed internet");
      require(channel_restores == 1 && WiFi.disconnects == 1,
              "channel repair was unthrottled or repeated disconnect");
    }
    clock_ms = start + 1000U;
    channel_restore_result = ESP_OK;
    bridge.serviceEspNowChannelRestore(clock_ms);
    require(channel_restores == 2 && !bridge._network_espnow_restore_pending
            && WiFi.disconnects == 1 && WiFi.offs == 0,
            "transient channel failure did not recover without toggling WiFi");
    for (uint32_t offset : {1100U, 2000U, 5000U}) {
      clock_ms = start + offset;
      require(!bridge.serviceNetworkPower(clock_ms), "repair canceled the outage cooldown");
      require(channel_restores == 2 && WiFi.disconnects == 1,
              "successful channel restoration retained repeated work");
    }
  }
  resetEnvironment();
  {
    MQTTBridge bridge;
    espnow_constrained = true;
    bridge.serviceNetworkPower(100U);
    clock_ms = 100U + grace;
    channel_restore_result = ESP_FAIL;
    bridge.serviceNetworkPower(clock_ms);
    clock_ms += 1000U;
    mesh::wifi::networkPower().setHold(mesh::wifi::NetworkPowerHold::WebConfig, true);
    bridge.serviceEspNowChannelRestore(clock_ms);
    mesh::wifi::networkPower().setHold(mesh::wifi::NetworkPowerHold::WebConfig, false);
    sdk_mode = WIFI_STA | WIFI_MODE_AP;
    bridge.serviceEspNowChannelRestore(clock_ms);
    sdk_mode = WIFI_STA;
    bridge.board.ota = true;
    bridge.serviceEspNowChannelRestore(clock_ms);
    bridge.board.ota = false;
    sdk_mode_result = ESP_FAIL;
    bridge.serviceEspNowChannelRestore(clock_ms);
    require(channel_restores == 1 && bridge._network_espnow_restore_pending,
            "deferred restoration changed a foreground or unknown driver channel");
    sdk_mode_result = ESP_OK;
    channel_restore_result = ESP_OK;
    bridge.serviceEspNowChannelRestore(clock_ms);
    require(channel_restores == 2 && !bridge._network_espnow_restore_pending,
            "released foreground owner stranded channel restoration");
  }
  resetEnvironment();
  {
    MQTTBridge bridge;
    espnow_constrained = true;
    bridge.serviceNetworkPower(100U);
    clock_ms = 100U + grace;
    channel_restore_result = ESP_FAIL;
    bridge.serviceNetworkPower(clock_ms);
    espnow_constrained = false;
    clock_ms += 1000U;
    bridge.serviceEspNowChannelRestore(clock_ms);
    require(channel_restores == 1 && !bridge._network_espnow_restore_pending,
            "stopped ESP-NOW owner retained a channel mutation");
  }
}

static void stableBrokerCountsButFlappingDoesNot() {
  resetEnvironment();
  {
    MQTTBridge bridge;
    bridge.serviceNetworkPower(100U);
    bridge._slots[0].connected = true;
    bridge._slots[0].connected_at_ms = 101U;
    require(bridge.serviceNetworkPower(30101U), "30-second quiet broker was not useful");
    require(bridge.serviceNetworkPower(100U + 10U * grace), "working broker lost WiFi");
    require(WiFi.offs == 0, "NTP failure overrode working MQTT");
    const uint32_t failed_at = 100U + 10U * grace;
    bridge._slots[0].connected = false;
    require(bridge.serviceNetworkPower(failed_at + grace - 1U),
            "real broker success did not renew outage grace");
    require(!bridge.serviceNetworkPower(failed_at + grace),
            "disconnected formerly healthy broker kept WiFi indefinitely");
  }
  resetEnvironment();
  {
    MQTTBridge bridge;
    bridge.serviceNetworkPower(100U);
    for (uint32_t offset = 1000U; offset < grace; offset += 1000U) {
      bridge._slots[0].connected = true;
      bridge._slots[0].connected_at_ms = 100U + offset - 1U;
      require(bridge.serviceNetworkPower(100U + offset), "grace ended early");
    }
    bridge._slots[0].connected_at_ms = 100U + grace - 1U;
    require(!bridge.serviceNetworkPower(100U + grace),
            "flapping broker repeatedly renewed the background outage");
  }
}

static void publishAcknowledgmentProvesRecoveryImmediately() {
  resetEnvironment();
  MQTTBridge bridge;
  bridge.serviceNetworkPower(100U);
  require(!bridge.serviceNetworkPower(100U + grace), "initial outage did not suspend");
  const uint32_t resumed = 100U + grace + interval;
  require(bridge.serviceNetworkPower(resumed), "recovery probe did not begin");
  // A broker reply during the final second proves useful service immediately,
  // even though it has not met the quiet 30-second connection threshold.
  bridge._slots[0].connected = true;
  bridge._slots[0].connected_at_ms = resumed + probe - 1U;
  bridge.clients[0].publish_acks = 1U;
  require(bridge.serviceNetworkPower(resumed + probe - 1U),
          "PUBACK failed to prove useful network");
  bridge._slots[0].connected = false;
  require(bridge.serviceNetworkPower(resumed + probe),
          "successful recovery retained its old short probe deadline");
  require(bridge.serviceNetworkPower(resumed + probe - 1U + grace - 1U),
          "PUBACK failed to renew a normal outage window");
  require(!bridge.serviceNetworkPower(resumed + probe - 1U + grace),
          "an old PUBACK repeatedly renewed a disconnected broker");
}

static void quietConnectionHasExactThirtySecondHealthBoundary() {
  resetEnvironment();
  {
    MQTTBridge bridge;
    bridge.serviceNetworkPower(100U);
    bridge._slots[0].connected = true;
    bridge._slots[0].connected_at_ms = 200U;
    require(bridge.serviceNetworkPower(30199U), "initial grace ended early");
    bridge._slots[0].connected = false;
    require(!bridge.serviceNetworkPower(100U + grace),
            "29.999-second connection falsely renewed outage grace");
  }
  resetEnvironment();
  {
    MQTTBridge bridge;
    bridge.serviceNetworkPower(100U);
    bridge._slots[0].connected = true;
    bridge._slots[0].connected_at_ms = 200U;
    require(bridge.serviceNetworkPower(30200U), "30-second connection was not admitted");
    bridge._slots[0].connected = false;
    require(bridge.serviceNetworkPower(30200U + grace - 1U),
            "quiet 30-second broker did not renew useful-service grace");
    require(!bridge.serviceNetworkPower(30200U + grace),
            "formerly quiet broker kept failed service awake indefinitely");
  }
  resetEnvironment();
  {
    MQTTBridge bridge;
    bridge.serviceNetworkPower(0U);
    bridge._slots[0].connected = true;
    bridge._slots[0].connected_at_ms = 0U;
    require(bridge.serviceNetworkPower(30000U), "zero-time connection was not admitted");
    bridge._slots[0].connected = false;
    require(bridge.serviceNetworkPower(30000U + grace - 1U),
            "valid zero-time broker connection was mistaken for unset state");
    require(!bridge.serviceNetworkPower(30000U + grace),
            "disconnected zero-time broker retained WiFi indefinitely");
  }
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
  for (uint32_t start : {100U, UINT32_MAX - 4000U, UINT32_MAX - interval + 1U}) {
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
    clock_ms = failed_at + interval - 1U;
    bridge.serviceBackgroundNtp(clock_ms);
    require(ntp_configs == 1, "failed SNTP retried before 30 minutes");
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

static void retainedClockAndFreshNtpDoNotCreditFailedMqtt() {
  resetEnvironment();
  MQTTBridge bridge;
  bridge.rtc.epoch = MQTT_NTP_MIN_VALID_EPOCH + 100U;
  require(bridge.serviceNetworkPower(100U), "initial policy did not run");
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
  require(bridge.serviceNetworkPower(100U + grace - 1U), "outage grace ended early");
  require(!bridge.serviceNetworkPower(100U + grace),
          "successful NTP kept failed MQTT's WiFi on indefinitely");
}

int main(int argc, char** argv) {
  require(argc == 2, "one case is required");
  const std::string name = argv[1];
  try {
    if (name == "handoff") failureStopsBothServicesBeforeWifi();
    else if (name == "failed-stop") failedSdkStopWithholdsHandoff();
    else if (name == "failed-stop-wake") failedSuspendLateConnectCannotAbandonTransportOwnership();
    else if (name == "recovery") recoveryHasOneBoundedWindow();
    else if (name == "owners") foregroundAndEspNowArePreserved();
    else if (name == "background-estimate") automaticClockEstimateCannotWakeSuspendedNetwork();
    else if (name == "channel-repair") deferredEspNowChannelRepair();
    else if (name == "healthy") stableBrokerCountsButFlappingDoesNot();
    else if (name == "puback") publishAcknowledgmentProvesRecoveryImmediately();
    else if (name == "quiet-boundary") quietConnectionHasExactThirtySecondHealthBoundary();
    else if (name == "ntp-retry") sntpFailureAndBusyLeaseRetries();
    else if (name == "ntp-rotation") everyNtpServerGetsTheLeadingTimeoutSlot();
    else if (name == "ntp-proof") retainedClockAndFreshNtpDoNotCreditFailedMqtt();
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
            "ESP32 outage policy changed portable WiFi-failure retry");
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
            "bool MQTTBridge::serviceNetworkPower(",
            "void MQTTBridge::serviceEspNowChannelRestore(",
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
        # The portable stack keeps its previous five-second settle and retry;
        # the new 30-minute failed-network policy is ESP32-only.
        portable_methods = "\n".join(method(BRIDGE, signature) for signature in (
            "void MQTTBridge::cancelNtpRefresh(",
            "void MQTTBridge::refreshNTP(",
            "void MQTTBridge::pollNtpRefresh(",
        ))
        portable_source = PREAMBLE[:PREAMBLE.index("static constexpr uint32_t grace")]
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

    def test_failed_ntp_and_mqtt_quiesce_before_wifi_shutdown_publication(self):
        self.run_case("handoff")

    def test_failed_sdk_transport_stop_keeps_station_owned_and_awake(self):
        self.run_case("failed-stop")

    def test_failed_suspend_late_connect_and_foreground_wake_keep_sdk_reservation(self):
        self.run_case("failed-stop-wake")

    def test_recovery_restarts_once_and_stops_after_bounded_probe_across_wrap(self):
        self.run_case("recovery")

    def test_foreground_and_espnow_owners_preserve_driver_even_during_stop_race(self):
        self.run_case("owners")

    def test_automatic_clock_estimate_cannot_bypass_background_outage_cooldown(self):
        self.run_case("background-estimate")

    def test_failed_espnow_channel_restore_retries_without_wifi_toggles_or_owner_conflicts(self):
        self.run_case("channel-repair")

    def test_stable_mqtt_counts_as_useful_and_flapping_mqtt_cannot_renew(self):
        self.run_case("healthy")

    def test_publish_ack_proves_recovery_immediately_and_is_consumed_once(self):
        self.run_case("puback")

    def test_quiet_broker_health_requires_exactly_thirty_seconds(self):
        self.run_case("quiet-boundary")

    def test_sntp_failure_and_busy_lease_have_different_bounded_retries(self):
        self.run_case("ntp-retry")

    def test_all_six_ntp_servers_lead_a_bounded_request_before_rotation_repeats(self):
        self.run_case("ntp-rotation")

    def test_retained_clock_or_fresh_ntp_cannot_keep_failed_mqtt_wifi_active(self):
        self.run_case("ntp-proof")

    def test_portable_non_esp_stack_preserves_settle_and_short_retry_policy(self):
        checked = subprocess.run([str(self.portable_binary)], capture_output=True,
                                 text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn("PASS: portable", checked.stdout)


if __name__ == "__main__":
    unittest.main()
