#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <time.h>
#include <esp_sntp.h>
#include <esp_bt.h>
#include <esp_bt_main.h>
#include <esp_gap_ble_api.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <helpers/tracker/HomeBeacon.h>
#include <helpers/tracker/HomeBeaconCrypto.h>
#include "HomeBeaconController.h"
#include "Provisioning.h"

using mesh::home_beacon::Controller;
using mesh::home_beacon::Provisioning;
using mesh::home_beacon::Settings;
using mesh::tracker::HomeBeacon;

namespace {
Settings settings{};
Provisioning provisioning;
Controller policy;
Preferences preferences;
bool configured = false, was_connected = false, sntp_started = false;
volatile bool sntp_event = false;
char line_buffer[65]{};
size_t line_length = 0;
bool line_overflow = false;

enum class BleState { Idle, Configuring, Starting, Running, Stopping };
BleState ble_state = BleState::Idle;
uint32_t operation_started = 0, packet_slot = 0, retry_at = 0;
uint8_t advertisement[29]{};
struct GapEvent { uint8_t kind; int status; };
QueueHandle_t gap_queue = nullptr;
volatile bool gap_overflow = false;

void clean(void* ptr, size_t n) {
  volatile uint8_t* p = static_cast<volatile uint8_t*>(ptr);
  while (n--) *p++ = 0;
}
void clearLine() { clean(line_buffer, sizeof(line_buffer)); line_length = 0; line_overflow = false; }
void noteTime(struct timeval*) { sntp_event = true; }

void gapCallback(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t* param) {
  GapEvent e{};
  switch (event) {
    case ESP_GAP_BLE_ADV_DATA_RAW_SET_COMPLETE_EVT:
      e = {1, int(param->adv_data_raw_cmpl.status)}; break;
    case ESP_GAP_BLE_ADV_START_COMPLETE_EVT:
      e = {2, int(param->adv_start_cmpl.status)}; break;
    case ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT:
      e = {3, int(param->adv_stop_cmpl.status)}; break;
    default: return;
  }
  if (xQueueSend(gap_queue, &e, 0) != pdTRUE) gap_overflow = true;
}

bool initBle() {
  gap_queue = xQueueCreate(8, sizeof(GapEvent));
  if (!gap_queue) return false;
  esp_bt_controller_config_t config = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
  if (esp_bt_controller_init(&config) != ESP_OK ||
      esp_bt_controller_enable(ESP_BT_MODE_BLE) != ESP_OK ||
      esp_bluedroid_init() != ESP_OK || esp_bluedroid_enable() != ESP_OK ||
      esp_ble_gap_register_callback(gapCallback) != ESP_OK) return false;
  return true;
}

void showPrompt() {
  switch (provisioning.stage()) {
    case Provisioning::Stage::Ssid: Serial.println("SSID:"); break;
    case Provisioning::Stage::Password: Serial.println("Wi-Fi password (8-63 bytes):"); break;
    case Provisioning::Stage::Id: Serial.println("Beacon ID (8 hex characters):"); break;
    case Provisioning::Stage::Key: Serial.println("Random shared key (32 or 64 hex characters):"); break;
    default: break;
  }
}

void processLine() {
  if (line_overflow) {
    Serial.println("Input too long; try again.");
  } else if (provisioning.stage() == Provisioning::Stage::Idle) {
    if (strcmp(line_buffer, "provision") == 0) {
      // Reboot before provisioning so BLE and any existing Wi-Fi are stopped.
      preferences.putBool("setup", true);
      Serial.println("Starting setup; disable local terminal echo.");
      clearLine(); ESP.restart(); return;
    }
    Serial.println("Commands: provision, status");
    if (strcmp(line_buffer, "status") == 0) {
      Serial.println(configured && WiFi.status() == WL_CONNECTED ? "Wi-Fi connected" : "Wi-Fi disconnected");
      if (policy.clockRollback())
        Serial.println("Clock moved backwards; beacon disabled until restart.");
    }
  } else {
    const auto result = provisioning.line(line_buffer, line_length);
    if (result == Provisioning::Result::Invalid) Serial.println("Invalid input; try again.");
    if (result == Provisioning::Result::Complete) {
      // One NVS blob prevents partially changed settings from being accepted.
      const Settings& s = provisioning.settings();
      const bool saved = preferences.putBytes("config", &s, sizeof(s)) == sizeof(s);
      if (saved) preferences.putBool("setup", false);
      provisioning.clear(); clearLine();
      Serial.println(saved ? "Saved; restarting." : "Save failed; restart to retry setup.");
      if (saved) ESP.restart();
      return;
    }
    showPrompt();
  }
  clearLine();
}

void serviceSerial() {
  while (Serial.available()) {
    char c = char(Serial.read());
    if (c == '\r') continue;
    if (c == '\n') { processLine(); continue; }
    if (line_length < sizeof(line_buffer) - 1) line_buffer[line_length++] = c;
    else line_overflow = true;
  }
}

void requestStop() {
  if (esp_ble_gap_stop_advertising() == ESP_OK) {
    ble_state = BleState::Stopping; operation_started = millis();
  } else {
    // Never assume a failed stop succeeded; retry while treating it as active.
    retry_at = millis() + 1000;
  }
}

bool due(uint32_t now, uint32_t deadline) { return int32_t(now - deadline) >= 0; }

void serviceBle(bool connected, uint64_t now_utc) {
  const uint32_t now = millis();
  if (gap_overflow) ESP.restart(); // Lost completion: fail closed by resetting BLE.
  GapEvent e{};
  while (xQueueReceive(gap_queue, &e, 0) == pdTRUE) {
    if (e.kind == 1 && ble_state == BleState::Configuring) {
      const auto current = policy.evaluate(now, now_utc, connected);
      if (e.status == ESP_BT_STATUS_SUCCESS && current.action == Controller::Action::Update && current.slot == packet_slot) {
        esp_ble_adv_params_t params{};
        params.adv_int_min = 0x0190; // 250 ms, plus controller's random delay
        params.adv_int_max = 0x0190;
        params.adv_type = ADV_TYPE_NONCONN_IND;
        params.own_addr_type = BLE_ADDR_TYPE_PUBLIC;
        params.channel_map = ADV_CHNL_ALL;
        params.adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY;
        if (esp_ble_gap_start_advertising(&params) == ESP_OK) {
          ble_state = BleState::Starting; operation_started = now; continue;
        }
      }
      ble_state = BleState::Idle; retry_at = now + 1000;
    } else if (e.kind == 2 && ble_state == BleState::Starting) {
      if (e.status == ESP_BT_STATUS_SUCCESS) { policy.started(packet_slot); ble_state = BleState::Running; }
      else { ble_state = BleState::Idle; retry_at = now + 1000; }
    } else if (e.kind == 3 && ble_state == BleState::Stopping) {
      if (e.status == ESP_BT_STATUS_SUCCESS) { policy.stopped(); ble_state = BleState::Idle; }
      else { ble_state = BleState::Running; retry_at = now + 1000; }
    }
  }
  if (ble_state != BleState::Idle && ble_state != BleState::Running &&
      uint32_t(now - operation_started) > 5000) ESP.restart();
  const auto decision = policy.evaluate(now, now_utc, connected);
  if (!due(now, retry_at)) return;
  if (ble_state == BleState::Running && decision.action != Controller::Action::None) {
    requestStop();
  } else if (ble_state == BleState::Idle && decision.action == Controller::Action::Update) {
    HomeBeacon encoder(settings.id, settings.key, settings.key_len, mesh::tracker::homeBeaconHmacSha256);
    // Manufacturer-specific AD: 0xFFFF is reserved for local testing. No
    // flags field fits alongside the 25-byte packet in legacy advertising.
    advertisement[0] = 28; advertisement[1] = 0xff;
    advertisement[2] = 0xff; advertisement[3] = 0xff;
    if (!encoder.encode(now_utc, true, advertisement + 4)) { retry_at = now + 1000; return; }
    packet_slot = decision.slot;
    if (esp_ble_gap_config_adv_data_raw(advertisement, sizeof(advertisement)) == ESP_OK) {
      ble_state = BleState::Configuring; operation_started = now;
    } else retry_at = now + 1000;
  }
}
} // namespace

void setup() {
  Serial.begin(115200);
  preferences.begin("home-beacon", false);
  const bool setup_mode = preferences.getBool("setup", false);
  configured = preferences.getBytesLength("config") == sizeof(settings) &&
               preferences.getBytes("config", &settings, sizeof(settings)) == sizeof(settings) &&
               mesh::home_beacon::validSettings(settings);
  if (setup_mode || !configured) {
    configured = false;
    provisioning.begin();
    Serial.println("Home beacon setup. Disable terminal local echo; input is not echoed by firmware.");
    showPrompt(); return;
  }
  if (!initBle()) { Serial.println("BLE unavailable; beacon disabled."); configured = false; return; }
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setMinSecurity(WIFI_AUTH_WPA2_PSK);
  WiFi.begin(settings.ssid, settings.password);
  Serial.println("Home beacon starting. Type status or provision.");
}

void loop() {
  serviceSerial();
  if (!configured) { delay(10); return; }
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !was_connected) {
    sntp_event = false;
    policy.invalidateClock();
    sntp_set_time_sync_notification_cb(noteTime);
    configTime(0, 0, "time.cloudflare.com", "time.google.com", "pool.ntp.org");
    sntp_started = true;
  } else if (!connected && was_connected) {
    if (sntp_started) esp_sntp_stop();
    sntp_started = false; sntp_event = false; policy.invalidateClock();
  }
  was_connected = connected;
  const time_t now = time(nullptr);
  const uint64_t utc = now > 0 ? uint64_t(now) : 0;
  if (connected && sntp_started && sntp_event) {
    sntp_event = false;
    policy.synchronized(millis(), utc);
  }
  serviceBle(connected, utc);
  delay(10);
}
