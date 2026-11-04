#pragma once

// Shared WiFi and bridge arbitration for ESP32 infrastructure roles (Repeater
// and Room Server).
//
// Infrastructure nodes can run multiple 2.4 GHz consumers concurrently on the
// same ESP32 radio peripheral:
//   - MQTTBridge (background FreeRTOS worker on Core 0)
//   - WebConfigServer (HTTP portal and setup SoftAP on Core 1)
//   - UDPBridge (LAN multicast/unicast packet bridge on Core 1)
//   - ESPNowBridge (2.4 GHz Long-Range mesh bridge on Core 1)
//   - OTA (pull-OTA and browser/ElegantOTA uploads)
//
// This header centralizes three ownership rules shared between Repeater and
// Room Server:
//   1. Single-owner STA management: WebConfigServer only mutates the WiFi
//      station (WiFi.begin/disconnect/power-save) when neither MQTTBridge (in
//      its Running or asynchronous Stopping state) nor UDPBridge owns the link.
//   2. Unconfigured Setup AP handoff: When opening the initial setup SoftAP
//      with no saved WiFi credentials, running ESP-NOW and MQTT bridges are
//      suspended so proprietary LR PHY bits or channel locks do not hide the
//      SoftAP from phones and laptops.
//   3. Idle WiFi driver deinit: The ESP-IDF WiFi driver is only stopped and
//      deinitialized when all coordinator leases, bridges, WebConfig, and OTA
//      paths are quiescent.

#include <stdint.h>
#include <helpers/esp32/WiFiCoordinator.h>

namespace mesh {
namespace wifi {

// True while the MQTT worker task on Core 0 owns or is still tearing down its
// TLS/socket state on the station interface.
template <typename WorkerT>
inline bool mqttWorkerOwnsStation(const WorkerT* worker) {
  return worker != nullptr && (worker->isRunning() || worker->isStopping());
}

// Returns true only when WebConfigServer is the sole manager of the WiFi STA
// interface. When false, WebConfigServer attaches its HTTP server passively
// without issuing competing WiFi.begin() or WiFi.disconnect() calls.
inline bool infrastructureWebConfigOwnsStation(bool mqtt_running,
                                               bool mqtt_stopping,
                                               bool udp_enabled = false) {
  return !mqtt_running && !mqtt_stopping && !udp_enabled;
}

template <typename WorkerT>
inline bool infrastructureWebConfigOwnsStation(const WorkerT* mqtt_worker,
                                               bool udp_enabled = false) {
  return !mqttWorkerOwnsStation(mqtt_worker) && !udp_enabled;
}

// Suspends active ESP-NOW and MQTT bridges before raising an unconfigured
// first-boot setup SoftAP, recording that they should be resumed once setup
// finishes.
template <typename EspNowT, typename MqttT>
inline void suspendUnconfiguredBridgesForSetup(bool has_configured_wifi,
                                               bool& setup_suspended,
                                               EspNowT* espnow_bridge,
                                               MqttT* mqtt_bridge) {
  if (has_configured_wifi) return;
  setup_suspended = true;
  if (espnow_bridge && espnow_bridge->isRunning()) espnow_bridge->end();
  if (mqtt_bridge && mqtt_bridge->isRunning()) mqtt_bridge->end();
}

template <typename StopEspNowFn, typename StopMqttFn>
inline void suspendUnconfiguredBridgesForSetup(bool has_configured_wifi,
                                               bool& setup_suspended,
                                               StopEspNowFn&& stop_espnow,
                                               StopMqttFn&& stop_mqtt) {
  if (has_configured_wifi) return;
  setup_suspended = true;
  stop_espnow();
  stop_mqtt();
}

// Returns true when no infrastructure service, bridge, setup portal, or OTA
// session needs the 2.4 GHz radio and the ESP-IDF WiFi driver can be safely
// deinitialized to save power and internal DRAM.
inline bool canDeinitInfrastructureIdleWiFi(bool ota_running,
                                            bool webconfig_active,
                                            bool deferred_ota_pending,
                                            bool mqtt_running,
                                            bool mqtt_stopping,
                                            bool espnow_running,
                                            bool udp_enabled = false) {
  if (ota_running) return false;
  if (coordinator().anyLease()) return false;
  if (webconfig_active) return false;
  if (deferred_ota_pending) return false;
  if (mqtt_running || mqtt_stopping) return false;
  if (espnow_running) return false;
  if (udp_enabled) return false;
  return true;
}

template <typename MqttT>
inline bool canDeinitInfrastructureIdleWiFi(bool ota_running,
                                            bool webconfig_active,
                                            bool deferred_ota_pending,
                                            const MqttT* mqtt_worker,
                                            bool espnow_running,
                                            bool udp_enabled = false) {
  return canDeinitInfrastructureIdleWiFi(
      ota_running, webconfig_active, deferred_ota_pending,
      mqtt_worker != nullptr && mqtt_worker->isRunning(),
      mqtt_worker != nullptr && mqtt_worker->isStopping(),
      espnow_running, udp_enabled);
}

}  // namespace wifi
}  // namespace mesh
