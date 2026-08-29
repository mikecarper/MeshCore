#pragma once

#include <Arduino.h>
#include <cstdint>

#include <helpers/CommonRadioPrefs.h>
#include <helpers/DynamicConfigSerializer.h>

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

// Companion preferences remain stored by DataStore's explicit, append-only
// binary field list. The adapter objects are appended after every persisted
// field, and node latitude/longitude deliberately remain separate DataStore
// arguments, so the established field offsets and on-device format do not move.
class CompanionNodePrefs {
public:
  float airtime_factor = 0;
  char node_name[32] = {};
  float freq = 0;
  uint8_t sf = 0;
  uint8_t cr = 0;
  uint8_t multi_acks = 0;
  uint8_t manual_add_contacts = 0;
  float bw = 0;
  int8_t tx_power_dbm = 0;
  uint8_t telemetry_mode_base = 0;
  uint8_t telemetry_mode_loc = 0;
  uint8_t telemetry_mode_env = 0;
  float rx_delay_base = 0;
  uint32_t ble_pin = 0;
  uint8_t advert_loc_policy = 0;
  uint8_t buzzer_quiet = 0;
  uint8_t gps_enabled = 0;       // GPS enabled flag (0=disabled, 1=enabled)
  uint32_t gps_interval = 0;     // seconds; 0 uses the 1-second default
  uint8_t autoadd_config = 0;    // bitmask for auto-add contacts config
  uint8_t rx_boosted_gain = 0;   // SX126x RX boosted gain mode
  uint8_t radio_fem_rxgain = 0;  // external LoRa FEM RX gain
  uint8_t client_repeat = 0;
  uint8_t path_hash_mode = 0;
  uint8_t autoadd_max_hops = 0;
  char default_scope_name[31] = {};
  uint8_t default_scope_key[16] = {};

  // Append-only binary preference tail. Do not insert persisted fields above
  // or between these entries without updating DataStore's compatibility map.
  uint8_t radio_fem_rxgain_override = 0;
  uint8_t vibe_quiet = 0;
  uint8_t radio_fem_txgain = 0;
  uint8_t rx_powersaving_enabled = 0;
  uint32_t rx_ps_rx_us = 0;
  uint32_t rx_ps_sleep_us = 0;
  uint8_t rx_ps_level = 0;
  uint8_t rx_ps_preamble = 0;
  uint8_t powersaving_enabled = 0;
  uint8_t wifi_enabled = 0;
  uint8_t powersaving_policy_version = 0;
  uint8_t usb_logging_enabled = 0;
  char bluetooth_name[mesh::companion::BLUETOOTH_NAME_SIZE] = {};
  uint16_t display_rotation_degrees = 0;
  uint8_t cad_enabled = 0;
  uint16_t cad_scan_timeout_ms = 0;
  uint16_t cad_retry_delay_ms = 0;
  uint16_t cad_max_duration_ms = 0;

private:
  class RadioPrefs : public CommonRadioPrefs {
    CompanionNodePrefs* _parent;

  protected:
    void structure() override {
      def("freq", _parent->freq);
      def("bw", _parent->bw);
      def("sf", _parent->sf);
      def("cr", _parent->cr);
      def("cad", _parent->cad_enabled);
      def("rxgain", _parent->rx_boosted_gain);
      def("fem_rxgain", _parent->radio_fem_rxgain);
      def("fem_txgain", _parent->radio_fem_txgain);
      def("tx", _parent->tx_power_dbm);
      def("af", _parent->airtime_factor);
      def("rxdelay", _parent->rx_delay_base);
      def("hash_mode", _parent->path_hash_mode);
      def("multi_ack", _parent->multi_acks);
    }

  public:
    explicit RadioPrefs(CompanionNodePrefs* parent) : _parent(parent) { }

    float getFreq() const override { return _parent->freq; }
    void setFreq(float value) override {
      _parent->freq = value;
      markDirty();
    }
    float getBandwidth() const override { return _parent->bw; }
    void setBandwidth(float value) override {
      _parent->bw = value;
      markDirty();
    }
    uint8_t getSpreadFactor() const override { return _parent->sf; }
    void setSpreadFactor(uint8_t value) override {
      _parent->sf = value;
      markDirty();
    }
    uint8_t getCodingRate() const override { return _parent->cr; }
    void setCodingRate(uint8_t value) override {
      _parent->cr = value;
      markDirty();
    }
    float getAirtimeFactor() const override { return _parent->airtime_factor; }
    void setAirtimeFactor(float value) override {
      _parent->airtime_factor = value;
      markDirty();
    }
    bool isCadEnabled() const override { return _parent->cad_enabled != 0; }
    void setCadEnabled(bool enabled) override {
      _parent->cad_enabled = enabled ? 1 : 0;
      markDirty();
    }
    uint8_t getIntThresh() const override { return 0; }
    void setIntThresh(uint8_t) override { }
    uint8_t getRxGain() const override { return _parent->rx_boosted_gain; }
    void setRxGain(uint8_t value) override {
      _parent->rx_boosted_gain = value;
      markDirty();
    }
    int8_t getTxPower() const override { return _parent->tx_power_dbm; }
    void setTxPower(int8_t value) override {
      _parent->tx_power_dbm = value;
      markDirty();
    }
    float getRxDelay() const override { return _parent->rx_delay_base; }
    void setRxDelay(float value) override {
      _parent->rx_delay_base = value;
      markDirty();
    }
    uint8_t getAgcResetInt() const override { return 0; }
    void setAgcResetInt(uint8_t) override { }
    uint8_t getHashMode() const override { return _parent->path_hash_mode; }
    void setHashMode(uint8_t value) override {
      _parent->path_hash_mode = value;
      markDirty();
    }
    uint8_t getMultiAcks() const override { return _parent->multi_acks; }
    void setMultiAcks(uint8_t value) override {
      _parent->multi_acks = value;
      markDirty();
    }
    float getFloodTxDelay() const override { return 0.5f; }
    void setFloodTxDelay(float) override { }
    float getDirectTxDelay() const override { return 0.2f; }
    void setDirectTxDelay(float) override { }
    uint8_t getFEMRxGain() const override { return _parent->radio_fem_rxgain; }
    void setFEMRxGain(uint8_t value) override {
      _parent->radio_fem_rxgain = value;
      _parent->radio_fem_rxgain_override = 1;
      markDirty();
    }
    uint8_t getFEMTxGain() const override { return _parent->radio_fem_txgain; }
    void setFEMTxGain(uint8_t value) override {
      _parent->radio_fem_txgain = value;
      markDirty();
    }
  };

  // Runtime-only adapters must remain after the persisted field block above.
  RadioPrefs radio;
  DynamicConfigSerializer custom;

public:
  CompanionNodePrefs() : radio(this), custom(&radio) { }

  bool isRepeatEn() const { return client_repeat != 0; }
  void setRepeatEn(bool enabled) { client_repeat = enabled ? 1 : 0; }

  CommonRadioPrefs* getRadioPrefs() { return &radio; }
  KeyValueStore* getCustom() { return &custom; }

  bool isDirty() const {
    return radio.isDirty() || custom.isDirty();
  }

  void clearDirty() {
    radio.clearDirty();
    custom.clearDirty();
  }
};

inline bool migrateCompanionPowerSavingDefault(CompanionNodePrefs& prefs) {
  if (prefs.powersaving_policy_version == COMPANION_POWERSAVING_POLICY_VERSION) {
    return false;
  }

  prefs.powersaving_enabled = 1;
  prefs.powersaving_policy_version = COMPANION_POWERSAVING_POLICY_VERSION;
  return true;
}
