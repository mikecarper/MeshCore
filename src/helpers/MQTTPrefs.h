#pragma once

#ifdef WITH_MQTT_BRIDGE

#include <stdint.h>
#include <helpers/MQTTPresets.h>

// MQTT preferences are kept separate from role-specific NodePrefs. Companion
// and infrastructure roles intentionally use different NodePrefs layouts, but
// they can safely share this MQTT configuration structure.
struct MQTTPrefs {
  char mqtt_origin[32];
  char mqtt_iata[8];
  uint8_t mqtt_status_enabled;
  uint8_t mqtt_packets_enabled;
  uint8_t mqtt_raw_enabled;
  uint8_t mqtt_tx_enabled;
  uint32_t mqtt_status_interval;

  char wifi_ssid[32];
  char wifi_password[64];
  uint8_t wifi_power_save;

  char timezone_string[32];
  int8_t timezone_offset;

  char mqtt_slot_preset[MAX_MQTT_SLOTS][24];
  char mqtt_slot_host[MAX_MQTT_SLOTS][64];
  uint16_t mqtt_slot_port[MAX_MQTT_SLOTS];
  char mqtt_slot_username[MAX_MQTT_SLOTS][32];
  char mqtt_slot_password[MAX_MQTT_SLOTS][64];

  char mqtt_owner_public_key[65];
  char mqtt_email[64];

  char mqtt_slot_token[MAX_MQTT_SLOTS][48];
  char mqtt_slot_topic[MAX_MQTT_SLOTS][96];
  char mqtt_slot_audience[MAX_MQTT_SLOTS][64];

  uint8_t mqtt_rx_enabled;
  char mqtt_ntp_server[64];

  uint8_t snmp_enabled;
  char snmp_community[24];
  uint8_t radio_watchdog_minutes;
  uint8_t alert_enabled;
  char alert_psk_hex[33];
  uint16_t alert_wifi_minutes;
  uint16_t alert_mqtt_minutes;
  uint16_t alert_min_interval_min;
  char alert_hashtag[24];
  char alert_region[31];
};

// /mqtt_prefs is written with an 8-byte header so the format is
// self-describing. This is also used by the companion NVS wrapper to reject
// incompatible payloads cleanly.
static const uint8_t MQTT_PREFS_MAGIC[4] = {0xF5, 'M', 'Q', 'P'};
static const uint16_t MQTT_PREFS_VERSION = 1;

struct MQTTPrefsHeader {
  uint8_t magic[4];
  uint16_t version;
  uint16_t payload_len;
};

#endif // WITH_MQTT_BRIDGE
