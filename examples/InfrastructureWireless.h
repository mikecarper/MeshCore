#pragma once

// Included by the infrastructure main files after the mesh and transport objects.
#include <helpers/WirelessControl.h>
#if defined(ESP32) && (defined(WITH_WEBCONFIG) || defined(WITH_MQTT_BRIDGE) || defined(WITH_UDP_BRIDGE) || (defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW))
#include <WiFi.h>
#include <helpers/esp32/WiFiRadioPolicy.h>
#endif

class InfrastructureWirelessBackend : public mesh::wireless::Backend {
  bool wifi_saved_ = false, resume_web_ = false, resume_mqtt_ = false;
#ifdef WITH_UDP_BRIDGE
  bool resume_udp_ = false;
#endif
public:
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
#ifdef WITH_WEBCONFIG
      if (the_mesh.isWebConfigStopping()) return Result::Pending;
#ifdef WITH_UDP_BRIDGE
      const bool udp_station_requested = resume_udp_ || (!wifi_saved_ && the_mesh.isUDPBridgeConfigured());
#else
      const bool udp_station_requested = false;
#endif
      if (resume_web_ || (!udp_station_requested && ((!wifi_saved_ && !(enabled() & mesh::wireless::WiFi))
          || (wifi_saved_ && !resume_web_ && !resume_mqtt_)))) {
        char reply[160];
        if (!the_mesh.startWebConfig(false, reply)) return Result::Failed;
      }
#endif
#ifdef WITH_MQTT_BRIDGE
      if (resume_mqtt_
#ifndef WITH_WEBCONFIG
          || !(enabled() & mesh::wireless::WiFi)
#endif
          ) {
        if (!the_mesh.setMqttBridgeState(true)) return Result::Failed;
      }
#endif
#ifdef WITH_UDP_BRIDGE
      if ((resume_udp_ || (!wifi_saved_ && the_mesh.isUDPBridgeConfigured()))
          && !the_mesh.setUDPBridgeNetworkState(true)) return Result::Failed;
      resume_udp_ = false;
#endif
      wifi_saved_ = resume_web_ = resume_mqtt_ = false;
      return (enabled() & mesh::wireless::WiFi) ? Result::Done : Result::Failed;
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
