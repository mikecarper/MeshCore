#pragma once
#include "RadioFastCode.h"
#include "SX126xReceiveMode.h"

#include "CustomSX1262.h"
#include "SX126xPulseWrapper.h"
#include "ProfileSwitchTiming.h"
#include "SX126xReset.h"
#include "SX126xMorseRamp.h"
#include "SX126xMorseReceiver.h"
#include "RxpsPulseTiming.h"
#if MESH_RAK3401_CAD_RXPS
#include "CadRxPowerSaving.h"
#endif

#ifndef USE_SX1262
#define USE_SX1262
#endif

class CustomSX1262Wrapper : public SX126xPulseWrapper<CustomSX1262> {
  bool _profile_standby_held = false;
  bool _saved_standby_xosc = false;

#if MESH_RAK3401_CAD_RXPS
  static constexpr uint16_t CadHit = RADIOLIB_SX126X_IRQ_CAD_DETECTED;
  static constexpr uint16_t CadReceiveEnd = RADIOLIB_SX126X_IRQ_RX_DONE
      | RADIOLIB_SX126X_IRQ_TIMEOUT | RADIOLIB_SX126X_IRQ_HEADER_ERR
      | RADIOLIB_SX126X_IRQ_CRC_ERR;
  static constexpr uint32_t CadReceiveTimeoutUs = 1500000;
  bool _cad_rxps_armed = false;
  bool _cad_receiving = false;
  uint32_t _cad_receive_started = 0;

  int16_t armCadRxPowerSaving() {
    auto* radio = static_cast<CustomSX1262*>(_radio);
    int16_t err = radio->standby(RADIOLIB_SX126X_STANDBY_RC);
    if (err == RADIOLIB_ERR_NONE) err = radio->setDioIrqParams(
        RADIOLIB_SX126X_IRQ_ALL, CadHit | CadReceiveEnd);
    if (err == RADIOLIB_ERR_NONE) err = radio->clearIrqStatus();
    constexpr uint32_t timeout_ticks = CadReceiveTimeoutUs * 8 / 125;
    const uint8_t cad_params[] = {RADIOLIB_SX126X_CAD_ON_2_SYMB, 22, 10,
        RADIOLIB_SX126X_CAD_GOTO_RX, uint8_t(timeout_ticks >> 16),
        uint8_t(timeout_ticks >> 8), uint8_t(timeout_ticks)};
    // RadioLib's setCad() sends SetCad immediately after setting these
    // parameters. Leave the first scan to PPI so it cannot race sleep.
    if (err == RADIOLIB_ERR_NONE) err = radio->getMod()->SPIwriteStream(
        RADIOLIB_SX126X_CMD_SET_CAD_PARAMS, cad_params, sizeof(cad_params));
    // Enter warm sleep through the driver once so it invalidates its modem
    // cache and any optional in-radio retune patch before PPI takes over.
    if (err == RADIOLIB_ERR_NONE) err = radio->sleep(true);
    if (err != RADIOLIB_ERR_NONE) return err;
    radio->getMod()->setRfSwitchState(Module::MODE_RX);
    _cad_rxps_armed = rak3401::cadRxps::start();
    _cad_receiving = false;
    return _cad_rxps_armed ? RADIOLIB_ERR_NONE : RADIOLIB_ERR_SPI_CMD_FAILED;
  }

  int recvCadRxPowerSaving(uint8_t* bytes, int sz) {
    namespace cad = rak3401::cadRxps;
    if (cad::interruptAsserted()) {
      cad::stop();
      auto* radio = static_cast<CustomSX1262*>(_radio);
      uint8_t data[2] = {};
      const int16_t err = radio->getMod()->SPIreadStream(
          RADIOLIB_SX126X_CMD_GET_IRQ_STATUS, data, sizeof(data));
      const uint16_t irq = (uint16_t(data[0]) << 8) | data[1];
      if (err == RADIOLIB_ERR_NONE && (irq & CadReceiveEnd)) {
        _cad_receiving = false;
        _loop_event_pending = true;
        return RadioLibWrapper::recvRaw(bytes, sz);
      }
      // A detection also raises DIO1. Acknowledge only that bit so RX Done
      // can produce a distinct edge, including if it races this SPI write.
      if (err == RADIOLIB_ERR_NONE && (irq & CadHit)) {
        if (!_cad_receiving) {
          _cad_receiving = true;
          _cad_receive_started = millis();
        }
        if (uint32_t(millis() - _cad_receive_started) < CadReceiveTimeoutUs / 1000 + 100) {
          claimRadioInterrupt();
          radio->clearIrqFlags(CadHit);
          return 0;
        }
      }
      // After a failed probe, the ordinary reader retains its bounded IRQ
      // retry and still verifies RX_DONE before reading the FIFO.
      _loop_event_pending = true;
      if (err != RADIOLIB_ERR_NONE) _cad_receiving = false;
      return RadioLibWrapper::recvRaw(bytes, sz);
    }
    if (_cad_receiving) {
      // The radio owns RX until a terminal IRQ. Allow a bounded recovery if
      // its receive timeout interrupt is absent.
      if (uint32_t(millis() - _cad_receive_started) < CadReceiveTimeoutUs / 1000 + 100) return 0;
    } else if (cad::ownsBus()) {
      if (cad::exhausted()) {
        cad::stop();
        if (cad::start() || cad::interruptAsserted()) return 0;
      } else if (!cad::overdue()) {
        return 0;
      }
      stopReceiveDutyCycle();
      _rx_ps_last_error = RADIOLIB_ERR_SPI_CMD_FAILED;
      ++_rx_ps_arm_failures;
      _rx_ps_arm_retry.recordFailure(millis());
    }
    requestRestartRecv();
    return RadioLibWrapper::recvRaw(bytes, sz);
  }
#endif

protected:
  uint8_t getDriverSwitchFeatures() const override {
    return static_cast<const CustomSX1262*>(_radio)->getProfileSwitchFeatures();
  }
  uint8_t driverSwitchCapabilities() const override {
    return static_cast<const CustomSX1262*>(_radio)->profileSwitchCapabilities();
  }
  void setDriverSwitchFeatures(uint8_t features) override {
    static_cast<CustomSX1262*>(_radio)->setProfileSwitchFeatures(features);
  }
  int8_t readReceiveMode() override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.owns()) return -1;
#endif
    return sx126xReceiveMode((SX126x*)_radio);
  }
  void beginProfileRetune(bool continuousRx) override {
    static_cast<CustomSX1262*>(_radio)->beginProfileSwitch(continuousRx);
  }
  void endProfileRetune(bool success) override {
    static_cast<CustomSX1262*>(_radio)->endProfileSwitch(success);
  }
#if (defined(MC_NRF_RADIO_PROFILE_PRIORITY) && MC_NRF_RADIO_PROFILE_PRIORITY) \
    || (defined(MC_NRF_RADIO_PROFILE_USB_IRQ_GUARD) && MC_NRF_RADIO_PROFILE_USB_IRQ_GUARD)
  bool ownsWarmProfileRetune() const override {
    return static_cast<const CustomSX1262*>(_radio)->hilOwnsWarmProfileRetune();
  }
#endif

  void setProfileStandbyWarm(bool enabled) override {
    if (enabled == _profile_standby_held) return;
    auto* radio = static_cast<CustomSX1262*>(_radio);
    if (enabled) {
      _saved_standby_xosc = radio->standbyXOSC;
      radio->standbyXOSC = true;
    } else {
      radio->standbyXOSC = _saved_standby_xosc;
    }
    _profile_standby_held = enabled;
  }

public:
  bool lastRxHadCRC() override {
    bool crc = false;
    return static_cast<CustomSX1262*>(_radio)->getLoRaRxHeaderInfo(nullptr, &crc) == RADIOLIB_ERR_NONE
        && crc;
  }
#if MESH_WARM_RX_REFRESH
  bool supportsWarmRxRefresh() const override { return true; }
#endif
  static constexpr bool SupportsRxPowerSaving = true;
  uint32_t rxPowerSavingTransitionUs() const override {
    return static_cast<CustomSX1262*>(_radio)->getTcxoDelay() + 1000UL;
  }
  uint8_t regulatorModes() const override { return static_cast<CustomSX1262*>(_radio)->regulatorModes(); }
  mesh::power::RegulatorMode defaultRegulatorMode() const override {
    return static_cast<CustomSX1262*>(_radio)->defaultRegulatorMode();
  }
  bool configureBootRegulator(mesh::power::RegulatorMode mode) override {
    return static_cast<CustomSX1262*>(_radio)->configureBootRegulator(mode);
  }
  mesh::power::RegulatorMode activeRegulatorMode() const override {
    return static_cast<CustomSX1262*>(_radio)->activeRegulatorMode();
  }
  ~CustomSX1262Wrapper() { unregisterInterruptAction(); }
  CustomSX1262Wrapper(CustomSX1262& radio, mesh::MainBoard& board) : SX126xPulseWrapper<CustomSX1262>(radio, board) { }
#if MESH_ENABLE_HOST_KISS
  bool supportsHostKiss() const override { return true; }
#endif
  bool supportsCarrierWave() const override { return true; }
  bool supportsMorseRx() const override { return MESH_ENABLE_MORSE_RX && supportsRadioDeepInit(); }
#if MESH_ENABLE_MESHTEXT_WIRE
  bool supportsMeshtext() const override { return true; }
#endif
#if MESH_ENABLE_ETHERNET_WIRE
  bool supportsEthernetTunnel() const override { return true; }
#endif

protected:
  float morseReceiverBandwidthKHz() const override { return 4.8f; }
  int16_t prepareMorseReceiver(float frequency_mhz) override {
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    _morse_rx_fsk_mode = true; // partial reset/configuration also needs full restoration
    if (!prepareRadioHardReset()) return RADIOLIB_ERR_UNKNOWN;
    auto* radio = static_cast<CustomSX1262*>(_radio);
    radio->invalidateMorseModemCache();
    const int16_t status = prepareSX126xMorseReceiver(radio, frequency_mhz);
    return status == RADIOLIB_ERR_NONE && !_board->finishRadioHardReset()
        ? RADIOLIB_ERR_UNKNOWN : status;
  }
  float readMorseRssi() override { return static_cast<CustomSX1262*>(_radio)->getMorseRssi(); }
  int16_t prepareMorseCarrier(float frequency_mhz) override {
    const int16_t status = RadioLibWrapper::prepareMorseCarrier(frequency_mhz);
    return status == RADIOLIB_ERR_NONE
        ? setSX126xMorseRamp(static_cast<CustomSX1262*>(_radio)) : status;
  }
  int16_t leaveMorseCarrier() override {
    return leaveSX126xMorseCarrier(static_cast<CustomSX1262*>(_radio));
  }
  bool restoreCarrierWaveModem() override {
    return restoreSX126xPacketRamp(static_cast<CustomSX1262*>(_radio)) == RADIOLIB_ERR_NONE
        && RadioLibWrapper::restoreCarrierWaveModem();
  }
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
  bool applyProtocol(mesh::RadioProfileProtocol protocol) override {
    uint8_t sync = 0x12;
    if (protocol == mesh::RadioProfileProtocol::Meshtext) {
      if (!supportsMeshtext()) return false;
      sync = 0x2b;
#if MESH_ENABLE_ETHERNET_WIRE
    } else if (protocol == mesh::RadioProfileProtocol::Ethernet) {
      if (!supportsEthernetTunnel()) return false;
      sync = 0x16;
#endif
#if MESH_ENABLE_HOST_KISS
    } else if (protocol == mesh::RadioProfileProtocol::Kiss) {
      if (!supportsHostKiss()) return false;
#endif
    } else if (protocol != mesh::RadioProfileProtocol::MeshCore) return false;
    // No alternate wire implementation means no modem write is needed.
    // Ethernet-only builds must still restore the MeshCore syncword.
#if !MESH_ENABLE_ETHERNET_WIRE && !MESH_ENABLE_HOST_KISS
    if (!supportsMeshtext()) return protocol == mesh::RadioProfileProtocol::MeshCore;
#endif
    auto* radio = static_cast<CustomSX1262*>(_radio);
    // Stage shared packet invariants only in a proven owned RX hop; normal
    // initialization/rollback still repairs the complete RadioLib state.
    return radio->setProfileProtocol(sync) == RADIOLIB_ERR_NONE;
  }
#endif
  int16_t applyCachedTxPower(int8_t dbm) override {
#ifdef SX126X_PA_RAMP_TIME
    // External-PA boards such as the T-Beam 1W require a slower PA ramp. Use
    // the same ramp for runtime power changes and post-reset restoration;
    // SX1262::setOutputPower() would otherwise restore RadioLib's default.
    return ((CustomSX1262 *)_radio)->setTxParams(
        dbm, static_cast<uint8_t>(SX126X_PA_RAMP_TIME));
#else
    return RadioLibWrapper::applyCachedTxPower(dbm);
#endif
  }

  MC_RADIO_FAST_CODE_ATTR bool applyProfileModulation(float bw, uint8_t sf, uint8_t cr) {
    auto* radio = static_cast<CustomSX1262*>(_radio);
    if (getDriverSwitchFeatures() & mesh::RadioSwitchTuning::Batch)
      return radio->setLoRaModulationParams(bw, sf, cr) == RADIOLIB_ERR_NONE;
    return radio->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && radio->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && radio->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  MC_RADIO_FAST_CODE_ATTR bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    if (static_cast<CustomSX1262*>(_radio)->profileSwitchFailed()) return false;
#if MESH_HIL_PROFILE_TIMING
    bool success = ((CustomSX1262 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE;
    mesh::profiletiming::mark(mesh::profiletiming::Frequency, mesh::profiletiming::readClock());
    if (success) {
      success = applyProfileModulation(bw, sf, cr);
      mesh::profiletiming::mark(mesh::profiletiming::Modulation, mesh::profiletiming::readClock());
    }
    if (success) {
      success = updatePreamble(sf, bw);
      mesh::profiletiming::mark(mesh::profiletiming::Preamble, mesh::profiletiming::readClock());
    }
#else
    bool success = ((CustomSX1262 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && applyProfileModulation(bw, sf, cr)
        && updatePreamble(sf, bw);
#endif
    if (!success) return false;

    PacketMillis pm = calcMaxPacketMillis(sf, bw, cr, preambleLengthForParams(sf, bw));
    ((CustomSX1262 *)_radio)->setPreambleMillis(pm.preambleMillis);
    ((CustomSX1262 *)_radio)->setMaxPayloadMillis(pm.payloadMillis);
#if MESH_HIL_PROFILE_TIMING
    mesh::profiletiming::mark(mesh::profiletiming::GuardMath, mesh::profiletiming::readClock());
#endif
    return true;
  }

public:
  bool setCodingRate(uint8_t cr) override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.pending) return false;
#endif
#if MESH_ENABLE_TX_BEACONS
    if (isBeaconRadioOwned()) return false;
#endif
    if (isCarrierWaveActive()) return false;
    return ((CustomSX1262 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

#if MESH_RAK3401_CAD_RXPS
  int recvRaw(uint8_t* bytes, int sz) override {
    if (_cad_rxps_armed && isInRecvMode()) return recvCadRxPowerSaving(bytes, sz);
    return RadioLibWrapper::recvRaw(bytes, sz);
  }
#endif

  bool isReceivingPacket() override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.owns()) return true;
#endif
#if MESH_RAK3401_CAD_RXPS
    if (_cad_rxps_armed) return _cad_receiving || rak3401::cadRxps::interruptAsserted();
#endif
    if (_rx_ps_armed && isChipBusy()) return false;
    return ((CustomSX1262 *)_radio)->isReceiving();
  }
  bool isChipBusy() override {
#if MESH_RAK3401_CAD_RXPS
    // Defer reconfiguration during CAD. Between checks a host transition
    // can reclaim SPI and wake the radio by asserting NSS.
    if (rak3401::cadRxps::ownsBus()) return rak3401::cadRxps::inScanWindow();
#endif
    return ((CustomSX1262 *)_radio)->isChipBusy();
  }
  float getCurrentRSSI() override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.owns()) return NAN;
#endif
#if MESH_RAK3401_CAD_RXPS
    if (_cad_rxps_armed) return NAN;
#endif
    return ((CustomSX1262 *)_radio)->getRSSI(false);
  }

  float packetScore(float snr, int packet_len) override {
    int sf = ((CustomSX1262 *)_radio)->spreadingFactor;
    return packetScoreInt(snr, sf, packet_len);
  }
  uint8_t getSpreadingFactor() const override { return ((CustomSX1262 *)_radio)->spreadingFactor; }
  int16_t performChannelScan() override {
    if (_profiles.enabled()) return RadioLibWrapper::performChannelScan();

    // Shorten the fixed-channel pre-TX check. Keep RadioLib's thresholds
    // and CAD-only exit so the caller can restore the configured RX mode.
    ChannelScanConfig_t scan = {};
    scan.cad.symNum = RADIOLIB_SX126X_CAD_ON_2_SYMB;
    scan.cad.detPeak = RADIOLIB_SX126X_CAD_PARAM_DEFAULT;
    scan.cad.detMin = RADIOLIB_SX126X_CAD_PARAM_DEFAULT;
    scan.cad.exitMode = RADIOLIB_SX126X_CAD_GOTO_STDBY;
    scan.cad.irqFlags = RADIOLIB_IRQ_CAD_DEFAULT_FLAGS;
    scan.cad.irqMask = RADIOLIB_IRQ_CAD_DEFAULT_MASK;
    return runChannelScan(cadScanTimeoutMillis(), &scan);
  }

  void powerOff() {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.pending) return;
#endif
    if (isCarrierWaveActive()) setCarrierWave(0, 0);
    if (_rx_ps_armed) stopReceiveDutyCycle();
    ((CustomSX1262 *)_radio)->standby();
    ((CustomSX1262 *)_radio)->sleep(false);
  }

  bool supportsRxPowerSaving() const override { return SupportsRxPowerSaving; }
  void onReceiveProcessed() override { finishReceiveProcessing(); }

  bool supportsRxPowerSavingRfRxDisable() const override {
  #if defined(SX126X_RXEN)
    return SX126X_RXEN != RADIOLIB_NC;
  #else
    return false;
  #endif
  }

  bool setRxPowerSavingRfRxDisabled(bool disabled) override {
    return trySetRxPowerSavingRfRxDisabled(disabled) == mesh::RadioParamApplyResult::APPLIED;
  }

  mesh::RadioParamApplyResult trySetRxPowerSavingRfRxDisabled(bool disabled) override {
    using Result = mesh::RadioParamApplyResult;
    if (!supportsRxPowerSavingRfRxDisable()) return Result::FAILED;
#if MESH_ENABLE_TX_BEACONS
    if (isBeaconRadioOwned()) return Result::BUSY;
#endif
    if (isCarrierWaveActive()) return Result::BUSY;
    if (isRxPowerSavingRfRxDisabled() == disabled) return Result::APPLIED;

    // Do not stop duty-cycle RX or change its policy before admission. A BUSY
    // sleep window, TX, or unread RX completion still owns the current mode.
    const uint8_t resume_rx = beginReconfigure();
    if (resume_rx > 1) return resume_rx == 2 ? Result::BUSY : Result::FAILED;

    const bool previous = isRxPowerSavingRfRxDisabled();
    ((CustomSX1262 *)_radio)->setRxPowerSavingRfRxDisabled(disabled);
    endReconfigure(resume_rx);
    if (resume_rx && !isInRecvMode()) {
      // startRecv already retained its bounded retry/backoff. That retry must
      // use the old RXEN policy; never report a failed RX restoration as OK.
      ((CustomSX1262 *)_radio)->setRxPowerSavingRfRxDisabled(previous);
      return Result::FAILED;
    }
    return Result::APPLIED;
  }

  bool isRxPowerSavingRfRxDisabled() const override {
    return ((CustomSX1262 *)_radio)->isRxPowerSavingRfRxDisabled();
  }

  // Reconfigure through the normal bounded radio transition so RXEN is never
  // asserted while a transmission or unread packet owns the radio.  The
  // selected table is then honored by every RadioLib mode transition.
  bool setExternalRxLnaEnabled(bool enabled) {
  #if defined(SX126X_RXEN)
    if (SX126X_RXEN == RADIOLIB_NC) return false;

    uint8_t resume_rx = beginReconfigure();
    if (resume_rx > 1) return false;

    bool success = ((CustomSX1262 *)_radio)->setExternalRxLnaEnabled(enabled);
    endReconfigure(resume_rx);
    return success;
  #else
    (void)enabled;
    return false;
  #endif
  }

protected:
#if MESH_WARM_RX_REFRESH && !MESH_COMPACT_STM32_RADIO
  bool warmRxRefreshUsesFs() const override {
    const auto* radio=static_cast<const CustomSX1262*>(_radio);
    const bool enabled=(radio->getProfileSwitchFeatures()&mesh::RadioSwitchTuning::Fs)!=0;
#ifdef MC_SX1262_HIL
    return enabled && radio->hilUseFsOnRetune;
#else
    return enabled;
#endif
  }
  WarmRxRefresh::Outcome performWarmRxRefresh(WarmRxRefresh::Reason& reason) override {
    auto* radio=static_cast<CustomSX1262*>(_radio);
    if(!radio->warmRxRefreshReady()) { reason=WarmRxRefresh::Reason::Cache; return WarmRxRefresh::Outcome::Deferred; }
    const bool saved_warm=radio->standbyXOSC;
    radio->standbyXOSC=true;
    beginProfileRetune(true);
    radio->trackWarmRxRefresh(true);
    struct Scope {
      CustomSX1262* radio; bool saved; bool success=true;
      Scope(CustomSX1262* r,bool previous):radio(r),saved(previous) {}
      ~Scope() { radio->trackWarmRxRefresh(false); radio->endProfileSwitch(success); radio->standbyXOSC=saved; }
    } scope(radio,saved_warm);
    _warm_refresh.attempting=true;
    const uint8_t resume=beginReconfigure();
    _warm_refresh.attempting=false;
    if(resume==2) {
      // Preserve a FIFO completing during standby. Never rearm on this path.
      reason=WarmRxRefresh::Reason::Packet;
      return WarmRxRefresh::Outcome::Deferred;
    }
    if(resume!=1) {
      scope.success=false; reason=WarmRxRefresh::Reason::Command;
      const int16_t error=radio->profileSwitchFailed()?radio->warmRxRefreshError():WarmRxRefresh::ContextError;
      _warm_refresh.error(error,radio->profileSwitchFailed()?radio->warmRxRefreshStep():WarmRxRefresh::Step::Context);
      // This transaction never changes the tuple. Even a failed standby can
      // race a completion of that same tuple: let recvRaw verify/read it before
      // ordinary reentry, rather than generic repair claiming its FIFO.
      const bool packet_pending=hasPendingRadioInterrupt();
      if(packet_pending) state=1; // STATE_RX owns the unchanged-tuple event.
      _profile_refresh_required=_profile_repair_required=!packet_pending;
      _profile_retry_at=millis()+250UL;
      return WarmRxRefresh::Outcome::Failed;
    }
    const int16_t error=radio->startWarmRxRefreshReceive();
    if(error!=RADIOLIB_ERR_NONE) {
      scope.success=false; reason=WarmRxRefresh::Reason::Command;
      _warm_refresh.error(error,radio->warmRxRefreshStep());
      const bool packet_pending=hasPendingRadioInterrupt();
      state=packet_pending?1:0; // Preserve a completion before bounded repair.
      _profile_refresh_required=_profile_repair_required=!packet_pending;
      _profile_retry_at=millis()+250UL;
      return WarmRxRefresh::Outcome::Failed;
    }
    _warm_refresh.checkedWarmReceive(radio->warmRxRefreshReturnedUs());
    state=1; // STATE_RX; checked fast SetRx succeeded, preserving IRQ events.
    _startrx_fails=0; _rx_retry_pending=false; _rx_retry_delay_ms=0;
    reason=WarmRxRefresh::Reason::None;
    return WarmRxRefresh::Outcome::Applied;
  }
#endif
  int startReceiveMode() override {
#if MESH_RAK3401_CAD_RXPS
    // Successful reconfiguration can retire base RXPS via standby directly.
    // Its normal SPI path already stopped PPI, so discard the CAD receive
    // state before starting continuous or native duty-cycle RX.
    if (!_rx_ps_armed && _cad_rxps_armed) {
      rak3401::cadRxps::stop();
      _cad_rxps_armed = _cad_receiving = false;
    }
#endif
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.pending && _rxps_pulse.phase==RxpsPulse::Phase::Packet) return pulseContinuous();
#endif
    return startReceivePowerSaving();
  }
  int16_t armDutyCycle(RadioLibIrqFlags_t irq_flags, RadioLibIrqFlags_t irq_mask,
      uint32_t* effective_rx_us, uint32_t* effective_sleep_us) override {
#if MESH_RAK3401_CAD_RXPS
    if (rxPowerSavingUsesCad(_rx_ps_rx_us, _rx_ps_sleep_us)) {
      if (_profiles.enabled() || _cur_sf != 7 || _cur_bw != 62.5f || currentPreambleLength() != 32) {
        return RX_POWERSAVING_ERR_TIMING;
      }
      *effective_rx_us = RX_POWERSAVING_CAD_LISTEN_US;
      *effective_sleep_us = RX_POWERSAVING_CAD_PERIOD_US - RX_POWERSAVING_CAD_LISTEN_US;
      return armCadRxPowerSaving();
    }
#endif
    *effective_rx_us = _rx_ps_rx_us;
    *effective_sleep_us = _rx_ps_sleep_us;
    return ((CustomSX1262*)_radio)->startReceiveDutyCycle(
        _rx_ps_rx_us, _rx_ps_sleep_us, irq_flags, irq_mask);
  }

  int16_t stopDutyCycleHardware() override {
#if MESH_RAK3401_CAD_RXPS
    if (_cad_rxps_armed) {
      _cad_rxps_armed = _cad_receiving = false;
      rak3401::cadRxps::stop();
      return _radio->standby();
    }
#endif
    const int16_t standby_error = _radio->standby();
    const int16_t rtc_error = ((CustomSX1262*)_radio)->stopRTC();
    return standby_error != RADIOLIB_ERR_NONE ? standby_error : rtc_error;
  }
  bool radioDeepInit() override {
    // A board hook takes precedence over direct NRST because it may also need
    // to recover a regulator, SPI bus, or I/O expander. With neither mechanism
    // available, do not report a normal re-init as a successful hard reset.
    if (!prepareRadioHardReset()) return false;

    // std_init() re-runs RadioLib begin(). It pulses a direct NRST itself;
    // expander-reset boards enter here with the radio already reset.
    bool success = ((CustomSX1262 *)_radio)->std_init();
#if defined(TBEAM_1W)
    // std_init() restores RadioLib's default RXEN table. Reapply the T-Beam
    // 1W's persisted external-LNA selection before receive mode is re-armed.
    if (success && _board->canControlLoRaFemLna()) {
      success = ((CustomSX1262 *)_radio)->setExternalRxLnaEnabled(
          _board->isLoRaFemLnaEnabled());
    }
#endif
    return success && _board->finishRadioHardReset();
  }
  bool supportsRadioDeepInit() const override {
    return supportsRadioHardResetPath();
  }

protected:
  bool applyRxBoostedGainMode(bool en) override {
    return ((CustomSX1262 *)_radio)->setRxBoostedGainMode(en) == RADIOLIB_ERR_NONE;
  }
public:
  bool supportsRxBoostedGainMode() const override { return true; }
  bool getRxBoostedGainMode() const override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.pending) return _cur_rx_boosted_gain;
#endif
    return ((CustomSX1262 *)_radio)->getRxBoostedGainMode();
  }

  void doResetAGC() override {
    sx126xResetAGC<true>((SX126x *)_radio, getRxBoostedGainMode()
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
        , &_agc_timing
#endif
    );
  }
};
