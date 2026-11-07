#pragma once

#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY

#include <type_traits>
#include <helpers/LoRaRadioFamily.h>
#include "RadioLibWrappers.h"
#include "CustomSX1262Wrapper.h"
#if MESH_RADIO_AUTO_SX1268
#include "CustomSX1268Wrapper.h"
#endif
#if MESH_RADIO_AUTO_SX127X
#include "CustomSX1276Wrapper.h"
#endif
#if MESH_RADIO_AUTO_LR1121
#include "CustomLR1121Wrapper.h"
#endif

// Mesh and Dispatcher retain this address before setup(). Only the selected
// backend is constructed, after the board powers and identifies its radio.
// Existing wrappers continue to own IRQs, recovery, profiles and modem state.
class UniversalLoRaRadio : public mesh::Radio {
  class SX1262Backend : public CustomSX1262Wrapper {
   public:
    SX1262Backend(CustomSX1262& radio, mesh::MainBoard& board)
        : CustomSX1262Wrapper(radio, board) {}
#if MESH_RADIO_AUTO_LR1121
    bool validateProfile(const mesh::RadioProfileParams& params) const override;
    float rxPowerSavingCaptureCostSymbols() const override {
      return RX_POWERSAVING_CAPTURE_COST_SYMBOLS;
    }
#endif
   protected:
    bool preserveActiveReceiveOnLateIrq() const override { return true; }
  };
#if MESH_RADIO_AUTO_SX1268
  class SX1268Backend : public CustomSX1268Wrapper {
   public:
    SX1268Backend(CustomSX1268& radio, mesh::MainBoard& board)
        : CustomSX1268Wrapper(radio, board) {}
    bool validateProfile(const mesh::RadioProfileParams& params) const override;
   protected:
    bool preserveActiveReceiveOnLateIrq() const override { return true; }
    float initialProfileFrequency(float preferred) const override {
      return mesh::autoLoRaInitialFrequency(mesh::LoRaRadioFamily::SX1268, preferred);
    }
  };
#endif
#if MESH_RADIO_AUTO_SX127X
  class SX127Backend : public CustomSX1276Wrapper {
   public:
    SX127Backend(CustomSX1276& radio, mesh::MainBoard& board)
        : CustomSX1276Wrapper(radio, board) {}
    bool validateProfile(const mesh::RadioProfileParams& params) const override;
   protected:
    bool checkBeaconOutputPower(float frequency_mhz, int8_t power) const override;
  };
#endif

#if MESH_RADIO_AUTO_LR1121
  class LR1121Backend : public CustomLR1121Wrapper {
   public:
    LR1121Backend(CustomLR1121& radio, mesh::MainBoard& board)
        : CustomLR1121Wrapper(radio, board) {}
    bool validateProfile(const mesh::RadioProfileParams& params) const override;
   protected:
    bool usesLR1121Policy() const override { return true; }
    bool checkBeaconOutputPower(float frequency_mhz, int8_t power) const override;
  };
#endif

  using ChipStorage = typename std::aligned_union<0, CustomSX1262
#if MESH_RADIO_AUTO_SX1268
      , CustomSX1268
#endif
#if MESH_RADIO_AUTO_SX127X
      , CustomSX1276
#endif
#if MESH_RADIO_AUTO_LR1121
      , CustomLR1121
#endif
      >::type;
  using DriverStorage = typename std::aligned_union<0, SX1262Backend
#if MESH_RADIO_AUTO_SX1268
      , SX1268Backend
#endif
#if MESH_RADIO_AUTO_SX127X
      , SX127Backend
#endif
#if MESH_RADIO_AUTO_LR1121
      , LR1121Backend
#endif
      >::type;
  ChipStorage chip_storage_;
  DriverStorage driver_storage_;
  Module& sx126_module_;
  Module& sx127_module_;
#if MESH_RADIO_AUTO_LR1121
  Module* lr1121_module_;
  using LR1121Initializer = bool (*)(CustomLR1121&, SPIClass*);
  LR1121Initializer lr1121_initializer_ = nullptr;
  bool (*lr1121_deep_init_)() = nullptr;
#endif
  mesh::MainBoard& board_;
  PhysicalLayer* physical_ = nullptr;
  RadioLibWrapper* backend_ = nullptr;
  mesh::LoRaRadioFamily family_ = mesh::LoRaRadioFamily::None;

 public:
  // Compile both policies; the runtime capability still rejects RXPS on SX127x.
  static constexpr bool SupportsRxPowerSaving = true;
  UniversalLoRaRadio(Module& sx126_module, Module& sx127_module, mesh::MainBoard& board
#if MESH_RADIO_AUTO_LR1121
      , Module* lr1121_module = nullptr
#endif
      );
  ~UniversalLoRaRadio();
  UniversalLoRaRadio(const UniversalLoRaRadio&) = delete;
  UniversalLoRaRadio& operator=(const UniversalLoRaRadio&) = delete;
  bool selectFamily(mesh::LoRaRadioFamily family);
  bool stdInit(SPIClass* spi);
#if MESH_RADIO_AUTO_LR1121
  // Board wiring owns LR1121's TCXO and RF-switch setup, including recovery.
  // Register before selecting a chip; an active driver cannot be rebound.
  bool setLR1121Initializer(LR1121Initializer initialize, bool (*deep_init)());
#endif
  mesh::LoRaRadioFamily family() const { return family_; }
  PhysicalLayer* physicalLayer() { return physical_; }
  const PhysicalLayer* physicalLayer() const { return physical_; }
  const char* familyName() const;
  float defaultFrequency(float preferred) const {
    return mesh::autoLoRaInitialFrequency(family_, preferred);
  }

  // These small forwarding methods preserve the complete mesh::Radio contract.
  // An unidentified radio never transmits or reports successful reconfiguration.
#define MESH_AUTO_LORA_RET(type, method, params, args, fallback) \
  type method params override { return backend_ ? static_cast<mesh::Radio*>(backend_)->method args : fallback; }
#define MESH_AUTO_LORA_CONST(type, method, params, args, fallback) \
  type method params const override { return backend_ ? static_cast<const mesh::Radio*>(backend_)->method args : fallback; }
#define MESH_AUTO_LORA_VOID(method, params, args) \
  void method params override { if (backend_) static_cast<mesh::Radio*>(backend_)->method args; }
#define MESH_AUTO_LORA_EXTRA(type, method, params, args, fallback) \
  type method params { return backend_ ? backend_->method args : fallback; }
#define MESH_AUTO_LORA_EXTRA_CONST(type, method, params, args, fallback) \
  type method params const { return backend_ ? static_cast<const RadioLibWrapper*>(backend_)->method args : fallback; }

  MESH_AUTO_LORA_CONST(uint8_t, regulatorModes, (), (), 0)
  MESH_AUTO_LORA_CONST(mesh::power::RegulatorMode, defaultRegulatorMode, (), (), mesh::power::RegulatorMode::Default)
  MESH_AUTO_LORA_RET(bool, configureBootRegulator, (mesh::power::RegulatorMode mode), (mode), false)
  MESH_AUTO_LORA_CONST(mesh::power::RegulatorMode, activeRegulatorMode, (), (), mesh::power::RegulatorMode::Default)
  MESH_AUTO_LORA_CONST(bool, supportsCarrierWave, (), (), false)
  MESH_AUTO_LORA_RET(mesh::RadioParamApplyResult, setCarrierWave, (uint8_t profile, uint32_t duration_ms), (profile, duration_ms), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_CONST(bool, isCarrierWaveActive, (), (), false)
  MESH_AUTO_LORA_CONST(uint8_t, carrierWaveProfile, (), (), 0)
  MESH_AUTO_LORA_CONST(uint32_t, carrierWaveRemainingMillis, (), (), 0)
  MESH_AUTO_LORA_CONST(bool, supportsMorseTx, (), (), false)
  MESH_AUTO_LORA_RET(mesh::MorseTxResult, startMorseTx, (float frequency_mhz, uint8_t wpm, const char* text), (frequency_mhz, wpm, text), mesh::MorseTxResult::Unsupported)
  MESH_AUTO_LORA_RET(mesh::MorseTxResult, stopMorseTx, (), (), mesh::MorseTxResult::Unsupported)
  MESH_AUTO_LORA_CONST(bool, isMorseTxActive, (), (), false)
  MESH_AUTO_LORA_CONST(float, morseTxFrequency, (), (), mesh::MorseTx::DefaultFrequencyMHz)
  MESH_AUTO_LORA_CONST(uint8_t, morseTxWpm, (), (), mesh::MorseTx::DefaultWpm)
  MESH_AUTO_LORA_CONST(uint32_t, morseTxRemainingMillis, (), (), 0)
  MESH_AUTO_LORA_CONST(bool, supportsMorseRx, (), (), false)
  MESH_AUTO_LORA_RET(mesh::MorseRxResult, startMorseRx, (float frequency_mhz, uint8_t wpm, uint32_t duration_ms), (frequency_mhz, wpm, duration_ms), mesh::MorseRxResult::Unsupported)
  MESH_AUTO_LORA_RET(mesh::MorseRxResult, stopMorseRx, (), (), mesh::MorseRxResult::Unsupported)
  MESH_AUTO_LORA_CONST(bool, isMorseRxActive, (), (), false)
  MESH_AUTO_LORA_CONST(bool, morseRxRecovering, (), (), false)
  MESH_AUTO_LORA_CONST(bool, morseRxLearning, (), (), false)
  MESH_AUTO_LORA_CONST(float, morseRxFrequency, (), (), mesh::MorseTx::DefaultFrequencyMHz)
  MESH_AUTO_LORA_CONST(float, morseRxBandwidthKHz, (), (), 0.0f)
  MESH_AUTO_LORA_CONST(uint32_t, morseRxRemainingMillis, (), (), 0)
  MESH_AUTO_LORA_CONST(uint32_t, morseCaptureGeneration, (), (), 0)
  bool morseRxDiagnostics(mesh::MorseRxDiagnostics& out) const override {
    if (backend_) return backend_->morseRxDiagnostics(out);
    out = {};
    return false;
  }
  MESH_AUTO_LORA_CONST(size_t, morseCaptureLength, (), (), 0)
  MESH_AUTO_LORA_CONST(bool, morseCaptureTruncated, (), (), false)
  MESH_AUTO_LORA_CONST(const char*, morseCaptureText, (), (), "")
  size_t readMorseCapture(size_t offset, char* out, size_t capacity) const override {
    if (backend_) return backend_->readMorseCapture(offset, out, capacity);
    if (out && capacity) out[0] = 0;
    return 0;
  }
  MESH_AUTO_LORA_VOID(clearMorseCapture, (), ())
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL
  MESH_AUTO_LORA_CONST(size_t, morseTraceLength, (), (), 0)
  MESH_AUTO_LORA_CONST(uint32_t, morseTraceTotal, (), (), 0)
  MESH_AUTO_LORA_CONST(bool, morseTraceTruncated, (), (), false)
  MESH_AUTO_LORA_CONST(bool, readMorseTrace, (size_t offset, mesh::MorseRssiTraceEntry& out), (offset, out), false)
#endif

  MESH_AUTO_LORA_VOID(begin, (), ())
  MESH_AUTO_LORA_RET(mesh::RadioProfiles*, profiles, (), (), nullptr)
  MESH_AUTO_LORA_CONST(const mesh::RadioProfiles*, profiles, (), (), nullptr)
#if MESH_RADIO_SWITCH_TUNING
  MESH_AUTO_LORA_CONST(mesh::RadioSwitchCapabilities, switchTuningCapabilities, (), (), mesh::RadioSwitchCapabilities{})
  MESH_AUTO_LORA_CONST(mesh::RadioSwitchTuning, switchTuning, (), (), mesh::RadioSwitchTuning{})
  MESH_AUTO_LORA_CONST(uint32_t, switchSpiHz, (), (), 0)
  MESH_AUTO_LORA_RET(bool, configureBootSwitchTuning, (const mesh::RadioSwitchTuning& tuning), (tuning), false)
  MESH_AUTO_LORA_RET(mesh::RadioParamApplyResult, trySetSwitchTuning,
      (const mesh::RadioSwitchTuning& tuning,
       bool (*persist)(void*, const mesh::RadioSwitchTuning&) = nullptr, void* context = nullptr),
      (tuning, persist, context), mesh::RadioParamApplyResult::FAILED)
#endif
  MESH_AUTO_LORA_CONST(bool, validateProfile, (const mesh::RadioProfileParams& params), (params), false)
  MESH_AUTO_LORA_CONST(bool, supportsMeshtext, (), (), false)
  MESH_AUTO_LORA_CONST(bool, supportsEthernetTunnel, (), (), false)
#if MESH_ENABLE_HOST_KISS
  MESH_AUTO_LORA_CONST(bool, supportsHostKiss, (), (), false)
#endif
#if MESH_ENABLE_TX_BEACONS
  MESH_AUTO_LORA_CONST(bool, validateBeaconPower, (float frequency_mhz, int8_t power), (frequency_mhz, power), false)
  MESH_AUTO_LORA_RET(mesh::RadioParamApplyResult, prepareBeaconTransmit, (const mesh::RadioProfileParams& params, int8_t power), (params, power), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_RET(mesh::RadioParamApplyResult, restoreBeaconReceive, (), (), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_CONST(bool, isBeaconRadioOwned, (), (), false)
  MESH_AUTO_LORA_RET(bool, isBeaconChannelBusy, (), (), true)
  MESH_AUTO_LORA_RET(uint32_t, getBeaconAirtime, (const mesh::RadioProfileParams& params, int length), (params, length), 0)
#endif
  MESH_AUTO_LORA_CONST(uint8_t, receiveProfile, (), (), 0)
  MESH_AUTO_LORA_CONST(uint32_t, receiveProfileGeneration, (), (), 0)
  MESH_AUTO_LORA_RET(mesh::RadioParamApplyResult, prepareTransmitProfile, (uint8_t profile), (profile), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_RET(mesh::RadioParamApplyResult, prepareTransmitProfile, (uint8_t profile, bool reply_rx_override), (profile, reply_rx_override), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_CONST(uint16_t, profilePreamble, (uint8_t profile), (profile), 0)
  MESH_AUTO_LORA_RET(uint32_t, getProfileAirtime, (uint8_t profile, int len_bytes, uint8_t cr = 0), (profile, len_bytes, cr), 0)
  MESH_AUTO_LORA_RET(mesh::RadioParamApplyResult, trySetPrimaryParams, (const mesh::RadioProfileParams& params, bool temporary, const uint32_t* timings = nullptr), (params, temporary, timings), mesh::RadioParamApplyResult::FAILED)

  MESH_AUTO_LORA_RET(int, recvRaw, (uint8_t* bytes, int size), (bytes, size), 0)
  MESH_AUTO_LORA_VOID(onReceiveProcessed, (), ())
  MESH_AUTO_LORA_RET(uint32_t, getEstAirtimeFor, (int len_bytes), (len_bytes), 0)
  MESH_AUTO_LORA_RET(float, packetScore, (float snr, int packet_len), (snr, packet_len), 0.0f)
  MESH_AUTO_LORA_RET(bool, startSendRaw, (const uint8_t* bytes, int length), (bytes, length), false)
  MESH_AUTO_LORA_RET(bool, setCodingRate, (uint8_t cr), (cr), false)
  MESH_AUTO_LORA_RET(mesh::RadioParamApplyResult, tryRestoreCodingRate, (uint8_t cr), (cr), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_RET(bool, isSendComplete, (), (), false)
  MESH_AUTO_LORA_VOID(onSendFinished, (), ())
  MESH_AUTO_LORA_VOID(loop, (), ())
  MESH_AUTO_LORA_VOID(idle, (), ())
  MESH_AUTO_LORA_VOID(startRecv, (), ())
  MESH_AUTO_LORA_RET(bool, setTrackerSleep, (bool sleeping), (sleeping), false)
  MESH_AUTO_LORA_RET(bool, recoverRadio, (bool hard), (hard), false)

  MESH_AUTO_LORA_CONST(int, getNoiseFloor, (), (), 0)
  MESH_AUTO_LORA_CONST(float, getNoiseFloorDbm, (), (), 0.0f)
  MESH_AUTO_LORA_CONST(float, getNoiseFloorDbm, (uint8_t profile), (profile), 0.0f)
#if MESH_ENABLE_TELEMETRY_HISTORY
  MESH_AUTO_LORA_CONST(bool, getNoiseFloorReading, (uint8_t profile, float& dbm, uint32_t& published_at_ms), (profile, dbm, published_at_ms), false)
  MESH_AUTO_LORA_VOID(requestNoiseFloorMeasurement, (), ())
#endif
  MESH_AUTO_LORA_CONST(float, getNoiseFloorCalibrationSecondsRemaining, (), (), 0.0f)
  MESH_AUTO_LORA_CONST(float, getNoiseFloorCalibrationSecondsRemaining, (uint8_t profile), (profile), 0.0f)
  MESH_AUTO_LORA_CONST(bool, isCalibratingNoiseFloor, (), (), false)
  MESH_AUTO_LORA_CONST(bool, getNoiseFloorWakeDelay, (uint32_t now, uint32_t& delay_ms), (now, delay_ms), false)
  MESH_AUTO_LORA_CONST(bool, getReceiveRetryDelay, (uint32_t now, uint32_t& delay_ms), (now, delay_ms), false)
  MESH_AUTO_LORA_CONST(bool, getRxPowerSavingRetryDelay, (uint32_t now, uint32_t& delay_ms), (now, delay_ms), false)
  MESH_AUTO_LORA_CONST(bool, getProfileIdleDelay, (uint32_t& delay_ms), (delay_ms), false)
  MESH_AUTO_LORA_VOID(triggerNoiseFloorCalibrate, (int threshold), (threshold))
  MESH_AUTO_LORA_VOID(recalibrateNoiseFloor, (), ())
  MESH_AUTO_LORA_VOID(setCADEnabled, (bool enabled), (enabled))
  MESH_AUTO_LORA_EXTRA(bool, setCADScanTimeoutMillis, (uint32_t timeout_ms), (timeout_ms), false)
  MESH_AUTO_LORA_EXTRA_CONST(uint32_t, getCADScanTimeoutMillis, (), (), 0)
  MESH_AUTO_LORA_VOID(resetAGC, (), ())
  MESH_AUTO_LORA_RET(bool, tryResetAGC, (), (), false)
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
  MESH_AUTO_LORA_CONST(bool, formatAGCTimingDiagnostics, (uint8_t page, char* reply, size_t capacity), (page, reply, capacity), false)
#endif
  MESH_AUTO_LORA_CONST(uint32_t, getRecoveryCount, (), (), 0)
  MESH_AUTO_LORA_CONST(bool, supportsWarmRxRefresh, (), (), false)
  MESH_AUTO_LORA_CONST(bool, isAGCMaintenanceActive, (), (), false)
  MESH_AUTO_LORA_CONST(bool, isAGCMaintenancePending, (), (), false)
  MESH_AUTO_LORA_CONST(bool, hasAGCMaintenanceWork, (), (), false)
  MESH_AUTO_LORA_RET(bool, serviceAGCMaintenanceFastLane, (), (), false)
  MESH_AUTO_LORA_VOID(configureAGCMaintenance, (uint32_t seconds), (seconds))
  MESH_AUTO_LORA_VOID(cancelAGCMaintenance, (), ())
  MESH_AUTO_LORA_RET(bool, setAGCPulseSleep, (uint32_t microseconds), (microseconds), false)
  MESH_AUTO_LORA_CONST(uint32_t, getAGCPulseSleep, (), (), 0)
#if MESH_WARM_RX_LAB_TIMER
  MESH_AUTO_LORA_RET(bool, setWarmRxLabInterval, (bool enabled, uint32_t seconds), (enabled, seconds), false)
  MESH_AUTO_LORA_CONST(bool, getWarmRxLabInterval, (uint32_t& seconds), (seconds), false)
  MESH_AUTO_LORA_CONST(uint32_t, warmRxLabTimerRevision, (), (), 0)
#endif
  MESH_AUTO_LORA_CONST(bool, supportsOldAGC, (), (), false)
  MESH_AUTO_LORA_RET(bool, setOldAGCInterval, (uint32_t seconds), (seconds), false)
  MESH_AUTO_LORA_CONST(uint32_t, getOldAGCInterval, (), (), 0)
  MESH_AUTO_LORA_CONST(bool, formatWarmRxRefresh, (uint8_t page, uint32_t seconds, char* reply, size_t capacity), (page, seconds, reply, capacity), false)
  MESH_AUTO_LORA_RET(mesh::AGCMaintenanceResult, periodicAGCMaintenance, (bool adaptive), (adaptive), mesh::AGCMaintenanceResult::Deferred)
  MESH_AUTO_LORA_CONST(uint32_t, agcMaintenanceRetryMs, (), (), 50)
  MESH_AUTO_LORA_RET(mesh::AGCMaintenanceResult, periodicOldAGCMaintenance, (), (), mesh::AGCMaintenanceResult::Deferred)
  MESH_AUTO_LORA_CONST(uint8_t, getRadioState, (), (), 0)
  MESH_AUTO_LORA_CONST(unsigned long, getLastRecvMillis, (), (), 0)
  MESH_AUTO_LORA_CONST(unsigned long, getLastRadioInterruptMillis, (), (), 0)
  MESH_AUTO_LORA_CONST(bool, isInRecvMode, (), (), false)
  MESH_AUTO_LORA_CONST(bool, isRxPowerSavingEnabled, (), (), false)
  MESH_AUTO_LORA_CONST(bool, supportsRxPowerSaving, (), (), false)
  MESH_AUTO_LORA_RET(bool, setRxPowerSaving, (bool enabled, uint32_t rx_us, uint32_t sleep_us), (enabled, rx_us, sleep_us), false)
  MESH_AUTO_LORA_CONST(bool, isRxPowerSavingContinuousFallback, (), (), false)
  MESH_AUTO_LORA_CONST(bool, supportsRxPowerSavingRfRxDisable, (), (), false)
  MESH_AUTO_LORA_RET(bool, setRxPowerSavingRfRxDisabled, (bool disabled), (disabled), false)
  MESH_AUTO_LORA_CONST(bool, isRxPowerSavingRfRxDisabled, (), (), false)
  MESH_AUTO_LORA_RET(mesh::RadioParamApplyResult, trySetParams, (float frequency, float bandwidth, uint8_t sf, uint8_t cr, const uint32_t* timings = nullptr), (frequency, bandwidth, sf, cr, timings), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_RET(bool, isReceiving, (), (), false)
  MESH_AUTO_LORA_RET(bool, isReceivingPassive, (int interference_margin_db), (interference_margin_db), false)
  MESH_AUTO_LORA_CONST(float, getLastRSSI, (), (), 0.0f)
  MESH_AUTO_LORA_CONST(float, getLastSNR, (), (), 0.0f)
  MESH_AUTO_LORA_CONST(uint32_t, getPacketsRecvErrors, (), (), 0)

  // Existing roles use these RadioLibWrapper helpers in addition to mesh::Radio.
  MESH_AUTO_LORA_EXTRA(mesh::RadioParamApplyResult, trySetRxPowerSaving, (bool enabled, uint32_t rx_us, uint32_t sleep_us), (enabled, rx_us, sleep_us), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_EXTRA_CONST(RxPowerSavingStatus, getRxPowerSavingStatus, (), (), RxPowerSavingStatus{})
  uint32_t rxPowerSavingTransitionUs() const {
    return backend_ ? backend_->rxPowerSavingTransitionUs() : rxPowerSavingDefaultTransitionUs();
  }
#if MESH_RADIO_AUTO_LR1121
  float rxPowerSavingCaptureCostSymbols() const {
    return backend_ ? backend_->rxPowerSavingCaptureCostSymbols() : rxPowerSavingDefaultCaptureCost();
  }
#endif
  void getRxPowerSavingTimings(uint32_t& rx_us, uint32_t& sleep_us) const {
    if (backend_) backend_->getRxPowerSavingTimings(rx_us, sleep_us);
    else { rx_us = 0; sleep_us = 0; }
  }
  MESH_AUTO_LORA_EXTRA(mesh::RadioParamApplyResult, trySetRxPowerSavingRfRxDisabled, (bool disabled), (disabled), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_EXTRA(bool, isChannelActive, (), (), false)
  MESH_AUTO_LORA_EXTRA(bool, setParams, (float frequency, float bandwidth, uint8_t sf, uint8_t cr, const uint32_t* timings = nullptr), (frequency, bandwidth, sf, cr, timings), false)
  MESH_AUTO_LORA_EXTRA(uint32_t, getRngSeed, (), (), 0)
  MESH_AUTO_LORA_EXTRA_CONST(bool, isTxPowerValid, (int8_t power), (power), false)
  MESH_AUTO_LORA_EXTRA_CONST(bool, isTxPowerApplied, (int8_t power), (power), false)
  MESH_AUTO_LORA_EXTRA(bool, setTxPower, (int8_t power), (power), false)
  MESH_AUTO_LORA_EXTRA(mesh::RadioParamApplyResult, trySetTxPower, (int8_t power), (power), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_EXTRA(float, getCurrentRSSI, (), (), NAN)
  MESH_AUTO_LORA_EXTRA_CONST(uint8_t, getSpreadingFactor, (), (), 0)
  MESH_AUTO_LORA_EXTRA_CONST(uint16_t, preambleLengthForParams, (uint8_t sf, float bandwidth), (sf, bandwidth), 0)
  MESH_AUTO_LORA_EXTRA_CONST(uint16_t, currentPreambleLength, (), (), 0)
  MESH_AUTO_LORA_EXTRA(bool, updatePreamble, (uint8_t sf, float bandwidth), (sf, bandwidth), false)
  MESH_AUTO_LORA_EXTRA(PacketMillis, calcMaxPacketMillis, (uint8_t sf, float bandwidth, uint8_t cr, uint16_t preamble), (sf, bandwidth, cr, preamble), (PacketMillis{0, 0}))
  MESH_AUTO_LORA_EXTRA(int16_t, performChannelScan, (), (), RADIOLIB_ERR_UNSUPPORTED)
  MESH_AUTO_LORA_EXTRA_CONST(uint32_t, getPacketsRecv, (), (), 0)
  MESH_AUTO_LORA_EXTRA_CONST(uint32_t, getPacketsSent, (), (), 0)
  MESH_AUTO_LORA_EXTRA_CONST(uint32_t, getRxPsWatchdogSoftCount, (), (), 0)
  MESH_AUTO_LORA_EXTRA_CONST(uint32_t, getRxPsWatchdogHardCount, (), (), 0)
  MESH_AUTO_LORA_EXTRA_CONST(bool, isWatchdogObserving, (), (), false)
  void resetStats() { if (backend_) backend_->resetStats(); }
  MESH_AUTO_LORA_EXTRA(bool, setRxBoostedGainMode, (bool enabled), (enabled), false)
  MESH_AUTO_LORA_EXTRA(mesh::RadioParamApplyResult, trySetRxBoostedGainMode, (bool enabled), (enabled), mesh::RadioParamApplyResult::FAILED)
  MESH_AUTO_LORA_EXTRA_CONST(bool, supportsRxBoostedGainMode, (), (), false)
  MESH_AUTO_LORA_EXTRA_CONST(bool, getRxBoostedGainMode, (), (), false)
  MESH_AUTO_LORA_EXTRA(bool, configSideDetectors, (const uint8_t* spreading_factors, uint8_t count, float bandwidth), (spreading_factors, count, bandwidth), false)
  MESH_AUTO_LORA_EXTRA(mesh::RadioParamApplyResult, tryConfigSideDetectors, (const uint8_t* spreading_factors, uint8_t count, float bandwidth), (spreading_factors, count, bandwidth), mesh::RadioParamApplyResult::FAILED)
  void powerOff();

#undef MESH_AUTO_LORA_RET
#undef MESH_AUTO_LORA_CONST
#undef MESH_AUTO_LORA_VOID
#undef MESH_AUTO_LORA_EXTRA
#undef MESH_AUTO_LORA_EXTRA_CONST
};

#endif
