#include "WiFiStationDiagnostics.h"

#if defined(ESP_PLATFORM)
#include <Arduino.h>
#include <WiFi.h>

namespace mesh {
namespace wifi {
namespace {

portMUX_TYPE diagnostics_mux = portMUX_INITIALIZER_UNLOCKED;
ConnectionDiagnostics diagnostics;

void stationEvent(arduino_event_t* event) {
  const uint32_t now = millis();
  // Arduino delivers these on its event task. Never call back into WiFi, log,
  // allocate, or reconnect here, including while another owner changes mode.
  portENTER_CRITICAL(&diagnostics_mux);
  switch (event->event_id) {
    case ARDUINO_EVENT_WIFI_STA_CONNECTED: {
      const auto& station = event->event_info.wifi_sta_connected;
      diagnostics.associated(station.ssid, station.ssid_len, now);
      break;
    }
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      diagnostics.gotIP(now);
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED: {
      const auto& station = event->event_info.wifi_sta_disconnected;
      diagnostics.disconnected(station.ssid, station.ssid_len, station.reason, now);
      break;
    }
    case ARDUINO_EVENT_WIFI_STA_LOST_IP:
      diagnostics.lostIP(now);
      break;
    case ARDUINO_EVENT_WIFI_STA_STOP:
      diagnostics.stopped(now);
      break;
    default:
      break;
  }
  portEXIT_CRITICAL(&diagnostics_mux);
}

} // namespace

void beginStationDiagnostics(const char* ssid) {
  // Permanent registration also covers WebConfig-only builds where MQTT never
  // starts. Do not add/remove event handlers while callbacks are being read.
  static const wifi_event_id_t listener = WiFi.onEvent(stationEvent);
  (void)listener;
  const uint32_t now = millis();
  portENTER_CRITICAL(&diagnostics_mux);
  diagnostics.begin(ssid, now);
  portEXIT_CRITICAL(&diagnostics_mux);
}

void noteStationDiagnosticPhase(ConnectionPhase phase) {
  const uint32_t now = millis();
  portENTER_CRITICAL(&diagnostics_mux);
  if (phase == ConnectionPhase::Stopped) diagnostics.stopped(now);
  else diagnostics.phase(phase, now);
  portEXIT_CRITICAL(&diagnostics_mux);
}

ConnectionDiagnosticsSnapshot stationDiagnosticsSnapshot() {
  portENTER_CRITICAL(&diagnostics_mux);
  const auto snapshot = diagnostics.snapshot();
  portEXIT_CRITICAL(&diagnostics_mux);
  return snapshot;
}

bool formatStationDiagnostics(char* reply, size_t capacity) {
  auto snapshot = stationDiagnosticsSnapshot();
  // Local leave events can also be queued after a fresh attempt, so they do
  // not replace failure history. The current driver status is authoritative
  // when displaying readiness, including an AP-originated reason-8 drop.
  if (WiFi.status() == WL_CONNECTED) snapshot.phase = ConnectionPhase::Online;
  else if (snapshot.phase == ConnectionPhase::Online) snapshot.phase = ConnectionPhase::Disconnected;
  return formatConnectionDiagnostics(reply, capacity, snapshot, millis());
}

void appendStationDiagnosticsHint(char* reply, size_t capacity, const char* ssid) {
  if (WiFi.status() == WL_CONNECTED || !ssid || !ssid[0]) return;
  const auto snapshot = stationDiagnosticsSnapshot();
  // A saved-credential edit must not label the new network with an old failure.
  if (strcmp(snapshot.ssid, ssid) != 0) return;
  if (!snapshot.has_disconnect
      || appendConnectionFailureHint(reply, capacity, snapshot, millis())) return;
  // Longer SSIDs or an OTA status suffix may leave room only for a pointer to
  // the bounded detail command. Preserve the hotspot address and other fields.
  static const char detail[] = "; get wifi.diag";
  if (!reply || !capacity) return;
  const size_t used = strnlen(reply, capacity);
  if (used < capacity && sizeof(detail) <= capacity - used) {
    memcpy(reply + used, detail, sizeof(detail));
  }
}

} // namespace wifi
} // namespace mesh
#endif
