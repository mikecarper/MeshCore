#pragma once

// Included by the infrastructure main files after the mesh and transport objects.
#include <helpers/WirelessControl.h>
#if defined(WITH_WEBCONFIG) || defined(WITH_MQTT_BRIDGE) || defined(WITH_UDP_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || (defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW)
#define MESH_INFRASTRUCTURE_WIRELESS_POLICY 1
#include <helpers/WirelessPolicyBackend.h>
#endif
#if defined(ESP32) && (defined(WITH_WEBCONFIG) || defined(WITH_MQTT_BRIDGE) || defined(WITH_UDP_BRIDGE) || (defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW))
#include <WiFi.h>
#include <helpers/esp32/WiFiRadioPolicy.h>
#endif
#if defined(ESP32) && (defined(WITH_WEBCONFIG) || defined(WITH_MQTT_BRIDGE) || defined(WITH_UDP_BRIDGE))
#include <helpers/esp32/InfrastructureWiFiPreference.h>
#endif

class InfrastructureWirelessBackend : public
#ifdef MESH_INFRASTRUCTURE_WIRELESS_POLICY
    mesh::wireless::PolicyBackend
#else
    mesh::wireless::Backend
#endif
{
  bool wifi_saved_ = false, resume_web_ = false, resume_mqtt_ = false;
#ifdef WITH_UDP_BRIDGE
  bool resume_udp_ = false;
#endif
public:
#if defined(ESP32) && (defined(WITH_WEBCONFIG) || defined(WITH_MQTT_BRIDGE) || defined(WITH_UDP_BRIDGE))
  bool hasWiFiPreference() const override { return true; }
  bool wifiPreference() const override {
    return mesh::wifi::infrastructureWiFiEnabled();
  }
  bool saveWiFiPreference(bool enabled) override {
    if (!mesh::wifi::saveInfrastructureWiFiEnabled(enabled)) return false;
    if (enabled) {
      // A persistent On uses today's saved service choices. Only transient
      // group restores should replay the old live-service snapshot.
      wifi_saved_ = resume_web_ = resume_mqtt_ = false;
#ifdef WITH_UDP_BRIDGE
      resume_udp_ = false;
#endif
    }
    return true;
  }
#endif
#if defined(WITH_ESPNOW_BRIDGE) || (defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW)
  bool hasEspNowPreference() const override { return true; }
  bool espNowPreference() const override {
#ifdef WITH_ESPNOW_BRIDGE
    return the_mesh.isEspNowEnabledPreference();
#else
    return primaryEspNowPreference();
#endif
  }
  bool saveEspNowPreference(bool enabled) override {
#ifdef WITH_ESPNOW_BRIDGE
    return the_mesh.setEspNowEnabledPreference(enabled);
#else
    return savePrimaryEspNowPreference(enabled);
#endif
  }
#endif
  uint8_t available() const override {
    uint8_t mask = 0;
#if defined(ESP32) && (defined(WITH_WEBCONFIG) || defined(WITH_MQTT_BRIDGE) || defined(WITH_UDP_BRIDGE))
    mask |= mesh::wireless::WiFi;
#endif
#if defined(WITH_ESPNOW_BRIDGE) || (defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW)
    mask |= mesh::wireless::EspNow;
#endif
    return mask;
  }
  uint8_t enabled() const override {
    uint8_t mask = 0;
#ifdef ESP32
    if (board.isOTAUpdateRunning()) mask |= mesh::wireless::WiFi;
#endif
#ifdef WITH_WEBCONFIG
    if (the_mesh.isWebConfigActive()) mask |= mesh::wireless::WiFi;
#endif
#ifdef WITH_MQTT_BRIDGE
    if (the_mesh.isMqttBridgeRunning() || the_mesh.isMqttBridgeStartPending())
      mask |= mesh::wireless::WiFi;
#endif
#ifdef WITH_UDP_BRIDGE
    if (the_mesh.isUDPBridgeNetworkEnabled()) mask |= mesh::wireless::WiFi;
#endif
#ifdef WITH_ESPNOW_BRIDGE
    if (the_mesh.isEspNowBridgeRunning()) mask |= mesh::wireless::EspNow;
#endif
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
    if (radio_driver.isEnabled()) mask |= mesh::wireless::EspNow;
#endif
    return mask;
  }
  uint8_t clients() const override {
    uint8_t mask = 0;
#ifdef ETHERNET_ENABLED
    if (ethernet_client.connected()) mask |= mesh::wireless::Independent;
#endif
#ifdef WITH_WEBCONFIG
    if (the_mesh.hasWirelessNetworkClient()) mask |= mesh::wireless::WiFi;
#endif
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM) \
    || (defined(ESP32) && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT \
        && (!defined(ARDUINO_USB_MODE) || ARDUINO_USB_MODE == 0))
    if (board.isUsbDataConnected()) mask |= mesh::wireless::Independent;
#endif
    return mask;
  }
  mesh::wireless::Result set(uint8_t service, bool on) override {
    using mesh::wireless::Result;
#if defined(ESP32) && (defined(WITH_WEBCONFIG) || defined(WITH_MQTT_BRIDGE) || defined(WITH_UDP_BRIDGE))
    if (service == mesh::wireless::WiFi) {
      if (!on) {
        if (board.isOTAUpdateRunning()) {
          char reply[160];
          if (!board.stopOTAUpdate(reply)) return Result::Failed;
        }
        if (!wifi_saved_) {
#ifdef WITH_WEBCONFIG
          resume_web_ = the_mesh.isWebConfigActive();
#endif
#ifdef WITH_MQTT_BRIDGE
          resume_mqtt_ = the_mesh.isMqttBridgeRunning()
              || the_mesh.isMqttBridgeStartPending();
#endif
#ifdef WITH_UDP_BRIDGE
          resume_udp_ = the_mesh.isUDPBridgeConfigured();
#endif
          wifi_saved_ = true;
        }
#ifdef WITH_UDP_BRIDGE
        if (!the_mesh.setUDPBridgeNetworkState(false)) return Result::Failed;
#endif
#ifdef WITH_MQTT_BRIDGE
        if (!the_mesh.setMqttBridgeState(false)) return Result::Failed;
#endif
#ifdef WITH_WEBCONFIG
        if (the_mesh.isWebConfigActive()) {
          char reply[160];
          the_mesh.stopWebConfig(reply);
          return Result::Pending;
        }
#endif
        WiFi.setAutoReconnect(false);
        if (enabled() & mesh::wireless::EspNow) {
          WiFi.disconnect(false, false);
          WiFi.mode(WIFI_STA);
          mesh::wifi::applyProtocolMask(WIFI_IF_STA);
          mesh::wifi::restoreEspNowChannel();
        } else
        {
          WiFi.disconnect(true, false);
          WiFi.mode(WIFI_OFF);
        }
        return Result::Done;
      }
#ifdef WITH_MQTT_BRIDGE
#ifdef WITH_WEBCONFIG
      const bool mqtt_configured = WebConfigServer::hasConfiguredWiFi(the_mesh.getObserverPrefs());
#else
      const bool mqtt_configured = the_mesh.getObserverPrefs()->wifi_ssid[0] != 0;
#endif
#endif
#ifdef WITH_WEBCONFIG
      if (the_mesh.isWebConfigStopping()) return Result::Pending;
      bool default_setup = false;
#ifdef WITH_MQTT_BRIDGE
      default_setup = the_mesh.getNodePrefs()->bridge_enabled && !mqtt_configured;
#endif
      // A transient group restore replays its live snapshot. A persistent On
      // uses the saved WebUI choice instead of inventing a setup portal.
      if (wifi_saved_ ? resume_web_ : WebConfigServer::loadEnabled(default_setup)) {
        char reply[160];
        if (!the_mesh.startWebConfig(false, reply)) return Result::Failed;
      }
#endif
#ifdef WITH_MQTT_BRIDGE
      if (the_mesh.getNodePrefs()->bridge_enabled
          && (resume_mqtt_ || (!wifi_saved_ && mqtt_configured))) {
        if (!the_mesh.setMqttBridgeState(true)) return Result::Failed;
      }
#endif
#ifdef WITH_UDP_BRIDGE
      if ((resume_udp_ || (!wifi_saved_ && the_mesh.isUDPBridgeConfigured()))
          && !the_mesh.setUDPBridgeNetworkState(true)) return Result::Failed;
      resume_udp_ = false;
#endif
      wifi_saved_ = resume_web_ = resume_mqtt_ = false;
      // Enabled is saved permission, not a requirement to invent an AP when
      // every individual WiFi service is disabled or not yet configured.
      return Result::Done;
    }
#endif
#ifdef WITH_ESPNOW_BRIDGE
    if (service == mesh::wireless::EspNow)
      return the_mesh.setEspNowBridgeState(on) ? Result::Done : Result::Failed;
#endif
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
    if (service == mesh::wireless::EspNow) {
      if (on) radio_driver.init();
      else {
        radio_driver.end();
        if (!(enabled() & mesh::wireless::WiFi)) {
          WiFi.setAutoReconnect(false);
          WiFi.mode(WIFI_OFF);
        }
      }
      return radio_driver.isEnabled() == on ? Result::Done : Result::Failed;
    }
#endif
    return Result::Failed;
  }
};
static InfrastructureWirelessBackend infrastructure_wireless;
