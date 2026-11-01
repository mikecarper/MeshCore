// Run the production station policy and Arduino event adapter together. SDK
// doubles fail if callbacks invoke a driver or formatting runs under the lock.
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <Arduino.h>
#include <WiFi.h>

#if defined(WITH_MQTT_BRIDGE)
#error This regression must also work without an MQTT event listener
#endif

int checkedSnprintf(char* output, size_t capacity, const char* format, ...) {
  assert(lock_depth == 0);
  va_list args;
  va_start(args, format);
  const int result = vsnprintf(output, capacity, format, args);
  va_end(args);
  return result;
}
#define snprintf checkedSnprintf
#include <helpers/esp32/WiFiStationPolicy.h>
#include <helpers/esp32/WiFiStationDiagnostics.cpp>
#undef snprintf

using namespace mesh::wifi;

void deliver(int id, const char* ssid = "iot", uint8_t reason = 0) {
  assert(WiFi.callback && lock_depth == 0);
  arduino_event_t event{};
  event.event_id = id;
  if (id == ARDUINO_EVENT_WIFI_STA_CONNECTED) {
    auto& data = event.event_info.wifi_sta_connected;
    data.ssid_len = strlen(ssid);
    memcpy(data.ssid, ssid, data.ssid_len);
  } else if (id == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    auto& data = event.event_info.wifi_sta_disconnected;
    data.ssid_len = strlen(ssid);
    memcpy(data.ssid, ssid, data.ssid_len);
    data.reason = reason;
  }
  const unsigned before = locks_entered;
  if (id == ARDUINO_EVENT_WIFI_STA_GOT_IP) WiFi.current_status = WL_CONNECTED;
  if (id == ARDUINO_EVENT_WIFI_STA_DISCONNECTED
      || id == ARDUINO_EVENT_WIFI_STA_LOST_IP
      || id == ARDUINO_EVENT_WIFI_STA_STOP) WiFi.current_status = WL_IDLE_STATUS;
  WiFi.callback(&event);
  assert(lock_depth == 0 && locks_entered == before + 1);
}

ConnectionDiagnosticsSnapshot snapshot() {
  const unsigned before = locks_entered;
  auto value = stationDiagnosticsSnapshot();
  assert(lock_depth == 0 && locks_entered == before + 1);
  return value;
}

std::string report() {
  char output[157];
  assert(formatStationDiagnostics(output, sizeof(output)));
  assert(lock_depth == 0);
  return output;
}

void start() {
  assert(beginStation("iot", "NEVER_PRINT_THIS_PASSWORD") == WL_IDLE_STATUS);
  assert(WiFi.listeners == 1 && WiFi.begins == 1);
  assert(snapshot().phase == ConnectionPhase::Connecting);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario(argv[1]);
  if (scenario == "registration") {
    assert(report().find("idle") != std::string::npos && WiFi.listeners == 0);
    start();
    assert(!snapshot().has_disconnect && snapshot().attempts == 1);
    assert(report().find("no driver reason recorded") != std::string::npos);
    // Repeated joins neither add nor replace a permanent event callback.
    auto callback = WiFi.callback;
    fake_now = 16000;
    WiFi.emit_connected_on_begin = true;
    assert(beginStation("iot", "changed password") == WL_IDLE_STATUS);
    assert(WiFi.listeners == 1 && WiFi.callback == callback && WiFi.begins == 2);
    assert(snapshot().attempts == 2 && snapshot().attempt_ms == 16000);
    assert(snapshot().phase == ConnectionPhase::WaitingIP);
    assert(report().find("NEVER_PRINT_THIS_PASSWORD") == std::string::npos);
  } else if (scenario == "events") {
    start();
    deliver(ARDUINO_EVENT_WIFI_STA_CONNECTED, "different network");
    deliver(ARDUINO_EVENT_WIFI_STA_GOT_IP);
    deliver(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, "different network", 202);
    assert(snapshot().phase == ConnectionPhase::Connecting);
    assert(!snapshot().has_disconnect);
    fake_now = 3000;
    deliver(ARDUINO_EVENT_WIFI_STA_CONNECTED);
    assert(snapshot().phase == ConnectionPhase::WaitingIP);
    assert(report().find("associated, awaiting IP") != std::string::npos);
    fake_now = 4000;
    deliver(ARDUINO_EVENT_WIFI_STA_GOT_IP);
    assert(snapshot().phase == ConnectionPhase::Online);
    deliver(ARDUINO_EVENT_WIFI_STA_LOST_IP);
    assert(snapshot().phase == ConnectionPhase::WaitingIP);
    deliver(ARDUINO_EVENT_WIFI_STA_GOT_IP);
    assert(snapshot().phase == ConnectionPhase::Online);
  } else if (scenario == "remote-disconnect") {
    start();
    deliver(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, "iot", 202);
    assert(beginStation("iot", "secret") == WL_IDLE_STATUS);
    deliver(ARDUINO_EVENT_WIFI_STA_CONNECTED);
    deliver(ARDUINO_EVENT_WIFI_STA_GOT_IP);
    assert(snapshot().phase == ConnectionPhase::Online);
    assert(report().find("> WiFi: connected;") == 0);
    // ASSOC_LEAVE is also sent by an AP. The current link must not appear
    // online merely because this ambiguous event preserves prior history.
    deliver(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, "iot", 8);
    assert(snapshot().last_reason == 202);
    assert(report().find("> WiFi: disconnected;") == 0);
    assert(report().find("driver 202 auth failed") != std::string::npos);
    // Report reconciliation is a copy: a status read cannot rewrite history.
    assert(snapshot().phase == ConnectionPhase::Online);
    WiFi.current_status = WL_CONNECTED;
    assert(report().find("> WiFi: connected;") == 0);
  } else if (scenario == "recovery") {
    start();
    fake_now = 4000;
    deliver(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, "iot", 202);
    assert(snapshot().phase == ConnectionPhase::Disconnected);
    noteStationDiagnosticPhase(ConnectionPhase::TimedOut);
    fake_now = 16000;
    // Firmware timeout/AP fallback must preserve the original driver failure.
    deliver(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, "iot", 8);
    deliver(ARDUINO_EVENT_WIFI_STA_STOP);
    assert(snapshot().phase == ConnectionPhase::TimedOut);
    assert(snapshot().last_reason == 202 && snapshot().last_disconnect_ms == 4000);
    auto text = report();
    assert(text.find("timed out") != std::string::npos);
    assert(text.find("driver 202 auth failed") != std::string::npos);
    assert(text.find("12s ago") != std::string::npos);
    assert(beginStation("iot", "secret") == WL_IDLE_STATUS);
    assert(snapshot().phase == ConnectionPhase::Connecting && snapshot().attempts == 2);
    assert(snapshot().last_reason == 202);
    assert(beginStation("new network", "secret") == WL_IDLE_STATUS);
    assert(!snapshot().has_disconnect && snapshot().attempts == 1);
    deliver(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, "iot", 201);
    assert(!snapshot().has_disconnect);
  } else if (scenario == "timeout") {
    start();
    fake_now = 16000;
    noteStationDiagnosticPhase(ConnectionPhase::TimedOut);
    deliver(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, "iot", 8);
    deliver(ARDUINO_EVENT_WIFI_STA_STOP);
    assert(snapshot().phase == ConnectionPhase::TimedOut && !snapshot().has_disconnect);
    assert(report().find("no driver reason recorded") != std::string::npos);
    assert(report().find("driver 8") == std::string::npos);
  } else if (scenario == "preflight") {
    assert(beginStation(nullptr, "secret") == WL_CONNECT_FAILED);
    assert(snapshot().phase == ConnectionPhase::NoSSID && WiFi.begins == 0);
    blocked = true;
    assert(beginStation("iot", "secret") == WL_CONNECT_FAILED);
    assert(snapshot().phase == ConnectionPhase::Blocked && WiFi.begins == 0);
    blocked = false;
    constrained = true;
    WiFi.scan_state = WIFI_SCAN_RUNNING;
    assert(beginStation("iot", "secret") == WL_IDLE_STATUS);
    assert(snapshot().phase == ConnectionPhase::ScanBusy && WiFi.begins == 0);
    WiFi.scan_state = -2;
    WiFi.networks = {{"iot", 11, -30}, {"other", 6, -20}};
    assert(beginStation("iot", "secret") == WL_NO_SSID_AVAIL);
    assert(snapshot().phase == ConnectionPhase::APNotOnChannel && WiFi.begins == 0);
    protocol_result = -1;
    assert(beginStation("iot", "secret") == WL_CONNECT_FAILED);
    assert(snapshot().phase == ConnectionPhase::PolicyFailed && WiFi.begins == 0);
    protocol_result = 0;
    WiFi.networks = {{"iot", 6, -55}, {"iot", 6, -40}};
    assert(beginStation("iot", "secret") == WL_IDLE_STATUS);
    assert(snapshot().phase == ConnectionPhase::Connecting && WiFi.begins == 1);
    assert(WiFi.join_channel == 6 && WiFi.selected_bssid == 2 && !WiFi.autoreconnect);
    assert(WiFi.listeners == 1);
  } else if (scenario == "hint") {
    start();
    deliver(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, "iot", 202);
    char output[157] = "> WiFi: setup hotspot; open http://192.168.4.1/";
    const std::string original(output);
    appendStationDiagnosticsHint(output, sizeof(output), "new network");
    assert(output == original);
    appendStationDiagnosticsHint(output, sizeof(output), "iot");
    assert(std::string(output).find("last drop: 202 auth failed") != std::string::npos);
    assert(std::string(output).find(original) == 0);
    WiFi.current_status = WL_CONNECTED;
    strcpy(output, original.c_str());
    appendStationDiagnosticsHint(output, sizeof(output), "iot");
    assert(output == original);
    WiFi.current_status = WL_IDLE_STATUS;
    // Every destination preserves the complete setup address and complete
    // suffix; only the detail-command pointer is allowed when history cannot fit.
    for (size_t capacity = original.size() + 1; capacity < sizeof(output); ++capacity) {
      unsigned char guarded[161]; memset(guarded, 0x5a, sizeof(guarded));
      char* destination = reinterpret_cast<char*>(guarded + 2);
      memcpy(destination, original.c_str(), original.size() + 1);
      appendStationDiagnosticsHint(destination, capacity, "iot");
      assert(guarded[0] == 0x5a && guarded[1] == 0x5a);
      for (size_t i = capacity + 2; i < sizeof(guarded); ++i) assert(guarded[i] == 0x5a);
      assert(std::string(destination) == original
          || std::string(destination) == original + "; get wifi.diag"
          || strstr(destination, "last drop: 202 auth failed"));
      assert(std::string(destination).find(original) == 0);
    }
  } else return 2;
  assert(lock_depth == 0 && locks_entered == locks_exited);
}
