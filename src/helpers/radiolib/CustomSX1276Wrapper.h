#pragma once

#include "CustomSX1276.h"
#include "RadioLibWrappers.h"

#ifndef USE_SX1276
#define USE_SX1276
#endif

class CustomSX1276Wrapper : public RadioLibWrapper {
public:
  ~CustomSX1276Wrapper() { unregisterInterruptAction(); }
  CustomSX1276Wrapper(CustomSX1276& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }
  bool supportsCarrierWave() const override { return supportsRadioDeepInit(); }
  bool supportsMorseRx() const override { return MESH_ENABLE_MORSE_RX && supportsRadioDeepInit(); }
#if MESH_ENABLE_MESHTEXT_WIRE
  bool supportsMeshtext() const override { return true; }
#endif

  void powerOff() {
    if (isCarrierWaveActive()) setCarrierWave(0, 0);
    _radio->standby(); _radio->sleep();
  }

protected:
#if MESH_ENABLE_MESHTEXT_WIRE
  bool applyProtocol(mesh::RadioProfileProtocol protocol) override {
    if (protocol != mesh::RadioProfileProtocol::MeshCore
        && protocol != mesh::RadioProfileProtocol::Meshtext) return false;
    const uint8_t sync = protocol == mesh::RadioProfileProtocol::Meshtext ? 0x2b : 0x12;
    return static_cast<CustomSX1276*>(_radio)->setProfileProtocol(sync) == RADIOLIB_ERR_NONE;
  }
#endif
  int16_t startMorseReceiver() override {
    return static_cast<CustomSX1276*>(_radio)->receiveDirect();
  }
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    return ((CustomSX1276 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && ((CustomSX1276 *)_radio)->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && ((CustomSX1276 *)_radio)->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && ((CustomSX1276 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf, bw);
  }

public:
  bool setCodingRate(uint8_t cr) override {
    if (isCarrierWaveActive()) return false;
    return ((CustomSX1276 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool isReceivingPacket() override { 
    return ((CustomSX1276 *)_radio)->isReceiving();
  }
  float getCurrentRSSI() override {
    return ((CustomSX1276 *)_radio)->getRSSI(false);
  }
  float packetScore(float snr, int packet_len) override {
    int sf = ((CustomSX1276 *)_radio)->spreadingFactor;
    return packetScoreInt(snr, sf, packet_len);
  }
  uint8_t getSpreadingFactor() const override { return ((CustomSX1276 *)_radio)->spreadingFactor; }

protected:
  float morseReceiverBandwidthKHz() const override { return 2.6f; }
  float readMorseRssi() override { return static_cast<CustomSX1276*>(_radio)->getMorseRssi(); }
  int16_t prepareMorseReceiver(float frequency_mhz) override {
    if (!supportsRadioDeepInit()) return RADIOLIB_ERR_UNSUPPORTED;
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    _morse_rx_fsk_mode = true;
    if (!prepareRadioHardReset()) return RADIOLIB_ERR_CHIP_NOT_FOUND;
    const int16_t status = static_cast<CustomSX1276*>(_radio)->beginMorseReceive(frequency_mhz);
    const bool finished = _board->finishRadioHardReset();
    return status == RADIOLIB_ERR_NONE && !finished ? RADIOLIB_ERR_UNKNOWN : status;
  }
  bool validateMorseFrequency(float frequency_mhz) const override {
    return isfinite(frequency_mhz) && frequency_mhz >= 137.0f && frequency_mhz <= 1020.0f;
  }

  int16_t prepareMorseCarrier(float frequency_mhz) override {
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    auto* radio = static_cast<CustomSX1276*>(_radio);
    // Zero deviation produces an unmodulated carrier regardless of DIO2.
    // RX bandwidth is immaterial for this TX-only operation. FSK is set up
    // once per message, never once per dot/dash.
    int16_t status = radio->beginFSK(frequency_mhz, 4.8, 0.0, 125.0, 2, 16, false);
    if (status != RADIOLIB_ERR_NONE) return status;
    status = radio->setMorsePaRamp();
    if (status != RADIOLIB_ERR_NONE) return status;
#ifdef SX127X_CURRENT_LIMIT
    status = radio->setCurrentLimit(SX127X_CURRENT_LIMIT);
    if (status != RADIOLIB_ERR_NONE) return status;
#endif
    return applyCachedTxPower(_dbm_valid ? _cur_dbm : LORA_TX_POWER);
  }

  int16_t enterCarrierWave() override {
    auto* radio = static_cast<CustomSX1276*>(_radio);
    // Direct TX needs FSK. Zero deviation prevents a floating DIO2 data input
    // from moving the carrier. Start at the PA_BOOST floor, while still idle;
    // user-facing power may be an external PA level, not chip drive power.
    int16_t status = radio->beginFSK(_cur_freq, 4.8, 0.0, 125.0, 2, 16, false);
    if (status != RADIOLIB_ERR_NONE) return status;
#ifdef SX127X_CURRENT_LIMIT
    status = radio->setCurrentLimit(SX127X_CURRENT_LIMIT);
    if (status != RADIOLIB_ERR_NONE) return status;
#endif
    status = applyCachedTxPower(_dbm_valid ? _cur_dbm : LORA_TX_POWER);
    if (status != RADIOLIB_ERR_NONE) return status;
    return radio->transmitDirect();
  }

  bool restoreCarrierWaveModem() override {
    // Restores LoRa, runtime profile/preamble, IRQ mapping and PA calibration.
    // Shared cleanup retains the stop/retry guard if any restoration fails.
    const bool restored = restoreAfterDeepInit();
    if (restored) _morse_rx_fsk_mode = false;
    return restored;
  }

  int16_t performChannelScan() override {
    return ((CustomSX1276 *)_radio)->tryScanChannel(cadScanTimeoutMillis(), *_board);
  }
#if MESH_ENABLE_TX_BEACONS
  int16_t performChannelScanWithTimeout(unsigned long timeout_ms) override {
    // Beacon CAD has its own modulation-derived deadline. Preserve SX127x's
    // DIO0 completion rule instead of the generic UNKNOWN-result poll.
    return static_cast<CustomSX1276*>(_radio)->tryScanChannel(timeout_ms, *_board);
  }
#endif

  bool radioDeepInit() override {
    if (!prepareRadioHardReset()) return false;
    return ((CustomSX1276 *)_radio)->std_init() && _board->finishRadioHardReset();
  }
  bool supportsRadioDeepInit() const override { return supportsRadioHardResetPath(); }
};
