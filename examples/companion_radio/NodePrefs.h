#pragma once
#include <Arduino.h>
#include <cstdint> // For uint8_t, uint32_t
#include "BluetoothName.h"

#define TELEM_MODE_DENY            0
#define TELEM_MODE_ALLOW_FLAGS     1     // use contact.flags
#define TELEM_MODE_ALLOW_ALL       2

#define ADVERT_LOC_NONE       0
#define ADVERT_LOC_SHARE      1

// Increment when a release must migrate the persisted Companion device-power
// policy. Version 1 corrects the short-lived default-off regression: old
// preference files are upgraded to power saving on exactly once, after which
// an explicit user choice remains persistent.
#define COMPANION_POWERSAVING_POLICY_VERSION 1

struct CompanionNodePrefs {  // persisted to file
  float airtime_factor;
  char node_name[32];
  float freq;
  uint8_t sf;
  uint8_t cr;
  uint8_t multi_acks;
  uint8_t manual_add_contacts;
  float bw;
  int8_t tx_power_dbm;
  uint8_t telemetry_mode_base;
  uint8_t telemetry_mode_loc;
  uint8_t telemetry_mode_env;
  float rx_delay_base;
  uint32_t ble_pin;
  uint8_t  advert_loc_policy;
  uint8_t  buzzer_quiet;
  uint8_t  gps_enabled;      // GPS enabled flag (0=disabled, 1=enabled)
  uint32_t gps_interval;     // GPS read interval in seconds; 0 uses the 1-second default
  uint8_t autoadd_config;    // bitmask for auto-add contacts config
  uint8_t rx_boosted_gain; // SX126x RX boosted gain mode (0=power saving, 1=boosted)
  uint8_t radio_fem_rxgain;  // LoRa FEM RX gain setting
  uint8_t client_repeat;
  uint8_t path_hash_mode;    // which path mode to use when sending
  uint8_t autoadd_max_hops;  // 0 = no limit, 1 = direct (0 hops), N = up to N-1 hops (max 64)
  char default_scope_name[31];
  uint8_t default_scope_key[16];
  uint8_t radio_fem_rxgain_override; // 1 once the user overrides the build default
  uint8_t vibe_quiet;          // haptic quiet mode; appended for prefs compatibility
  uint8_t radio_fem_txgain;    // LoRa FEM TX gain; appended for prefs compatibility
  uint8_t rx_powersaving_enabled; // SX126x/LR11xx receive duty cycling
  uint32_t rx_ps_rx_us;           // receive window in microseconds
  uint32_t rx_ps_sleep_us;        // sleep window in microseconds
  uint8_t rx_ps_level;            // 0=manual timings, 1..10=level-derived
  uint8_t rx_ps_preamble;         // 0=auto from SF, otherwise 16 or 32
  uint8_t powersaving_enabled;    // device CPU/GPS idle power saving
  uint8_t wifi_enabled;           // Companion WiFi radio and services
  uint8_t powersaving_policy_version; // one-time default migration marker
  uint8_t usb_logging_enabled;    // live USB packet/debug output
  char bluetooth_name[mesh::companion::BLUETOOTH_NAME_SIZE];
                                  // exact BLE name; empty uses BLE_NAME_PREFIX + node_name
  uint16_t display_rotation_degrees; // 0=board default; otherwise 90/180/270

  // Keep the upstream repeat API while retaining the existing binary prefs
  // layout used by this branch.
  bool isRepeatEn() const { return client_repeat != 0; }
  void setRepeatEn(bool enabled) { client_repeat = enabled ? 1 : 0; }
};

inline bool migrateCompanionPowerSavingDefault(CompanionNodePrefs& prefs) {
  if (prefs.powersaving_policy_version == COMPANION_POWERSAVING_POLICY_VERSION) {
    return false;
  }

  prefs.powersaving_enabled = 1;
  prefs.powersaving_policy_version = COMPANION_POWERSAVING_POLICY_VERSION;
  return true;
}
