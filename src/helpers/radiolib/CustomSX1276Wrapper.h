#pragma once

#include "CustomSX1276.h"
#include "RadioLibWrappers.h"
#include "RxpsPulseTiming.h"

#ifndef USE_SX1276
#define USE_SX1276
#endif

class CustomSX1276Wrapper : public RadioLibWrapper {
public:
  ~CustomSX1276Wrapper() { unregisterInterruptAction(); }
  CustomSX1276Wrapper(CustomSX1276& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) {
#if MESH_WARM_RX_REFRESH
    _rxps_pulse.host_timed=true;
#endif
  }
#if MESH_WARM_RX_REFRESH
  bool supportsWarmRxRefresh() const override { return true; }
  uint32_t rxPowerSavingTransitionUs() const override { return 1000UL; }
#endif
#if MESH_ENABLE_HOST_KISS
  bool supportsHostKiss() const override { return true; }
#endif
  bool supportsCarrierWave() const override { return supportsRadioDeepInit(); }
  bool supportsMorseRx() const override { return MESH_ENABLE_MORSE_RX && supportsRadioDeepInit(); }
#if MESH_ENABLE_MESHTEXT_WIRE
  bool supportsMeshtext() const override { return true; }
#endif
#if MESH_ENABLE_ETHERNET_WIRE
  bool supportsEthernetTunnel() const override { return true; }
#endif

  void powerOff() {
    if (isCarrierWaveActive()) setCarrierWave(0, 0);
    _radio->standby(); _radio->sleep();
  }

protected:
#if MESH_WARM_RX_REFRESH
  // SX127x has no LoRa autonomous duty-cycle/BUSY witness. Its checked idle
  // sleep is host timed; the 1 ms oscillator allowance exceeds the datasheet's
  // typical 250 us crystal startup. Hold its full 1 ms entry reserve plus the
  // >=500 us margin without the blocking delay in RadioLib::sleep().
  uint8_t _sx127x_pulse_mode_prefix=0x80, _sx127x_pulse_resume_stage=0;
  uint32_t _sx127x_pulse_wake_at=0;
  uint32_t agcPulseTimerHz() const override { return 1000000UL; }
  uint32_t defaultAGCPulseSleepUs() const override {
    return mesh::rxpspulse::bufferedSleepRequestUs(rxPowerSavingTransitionUs(),1000000UL,0xFFFFFFUL);
  }
  int16_t pulseSX127xMode(uint8_t& mode) {
    auto* mod=static_cast<CustomSX1276*>(_radio)->getMod();
    const int16_t value=mod->SPIgetRegValue(RADIOLIB_SX127X_REG_OP_MODE);
    if(value<0) return value;
    mode=static_cast<uint8_t>(value);
    return (mode&0xC0)==RADIOLIB_SX127X_LORA ? RADIOLIB_ERR_NONE : RADIOLIB_ERR_WRONG_MODEM;
  }
  int16_t pulseSX127xSetMode(uint8_t mode) {
    auto* mod=static_cast<CustomSX1276*>(_radio)->getMod();
    int16_t rc=mod->SPIsetRegValue(RADIOLIB_SX127X_REG_OP_MODE,mode,2,0,5);
    if(rc) return rc;
    uint8_t actual=0;rc=pulseSX127xMode(actual);
    return rc ? rc : actual==uint8_t(_sx127x_pulse_mode_prefix|mode) ? RADIOLIB_ERR_NONE : RxpsPulse::ModeError;
  }
  int16_t pulseSX127xSnapshot(uint8_t& mode,uint16_t& irq,uint8_t& activity) {
    auto* mod=static_cast<CustomSX1276*>(_radio)->getMod();
    int16_t rc=pulseSX127xMode(mode);if(rc) return rc;
    ++_rxps_pulse.irq_reads;
    const int16_t flags=mod->SPIgetRegValue(RADIOLIB_SX127X_REG_IRQ_FLAGS);
    if(flags<0) return flags;
    irq=static_cast<uint16_t>(flags);
    const int16_t status=mod->SPIgetRegValue(RADIOLIB_SX127X_REG_MODEM_STAT);
    if(status<0) return status;
    activity=static_cast<uint8_t>(status)&0x0F;
    return RADIOLIB_ERR_NONE;
  }
  void pulseSX127xFailure(int16_t rc,RxpsPulse::Step step) {
    _rxps_pulse.noteError(rc,step);
    _sx127x_pulse_resume_stage=0;
    _rxps_pulse.recovering(static_cast<uint32_t>(micros()));
  }
  bool startAGCPulse() override {
    auto* mod=static_cast<CustomSX1276*>(_radio)->getMod();
    uint8_t mode=0,activity=0;uint16_t irq=0;
    const int16_t probe=pulseSX127xSnapshot(mode,irq,activity);
    if(!probe && ((mode&7)!=RADIOLIB_SX127X_RXCONTINUOUS || activity
        || (irq&(RADIOLIB_SX127X_CLEAR_IRQ_FLAG_RX_DONE|RADIOLIB_SX127X_CLEAR_IRQ_FLAG_VALID_HEADER))
        || hasPendingRadioInterrupt())) { _rxps_pulse.deferred();return false; }
    if((mode&0xC0)==RADIOLIB_SX127X_LORA) _sx127x_pulse_mode_prefix=mode&0xF8;
    const uint32_t sleep=getAGCPulseSleep();
    const bool configured=_rxps_pulse.configure(sleep,rxPowerSavingTransitionUs(),agcPulseTimerHz());
    admitAGCPulse(static_cast<uint32_t>(micros()));_rxps_pulse_retries=0;
    _sx127x_pulse_resume_stage=0;
    if(probe) { pulseSX127xFailure(probe,RxpsPulse::Step::Irq);return true; }
    if(!configured) { pulseSX127xFailure(RADIOLIB_ERR_INVALID_SLEEP_PERIOD,RxpsPulse::Step::Stage);return true; }
    _sx127x_pulse_mode_prefix=mode&0xF8;
    int16_t rc=pulseSX127xSetMode(RADIOLIB_SX127X_STANDBY);
    if(rc) { pulseSX127xFailure(rc,RxpsPulse::Step::Standby);return true; }
    // Sleep clears the LoRa FIFO. Standby first freezes RX; a completed frame
    // appearing during that transition must reach Dispatcher without sleep,
    // IRQ acknowledgement, FIFO-pointer reset, or a new RX command.
    rc=pulseSX127xSnapshot(mode,irq,activity);
    if(rc) { pulseSX127xFailure(rc,RxpsPulse::Step::Irq);return true; }
    if((irq&RADIOLIB_SX127X_CLEAR_IRQ_FLAG_RX_DONE) || hasPendingRadioInterrupt()) {
      ++_rxps_pulse.aborts;_rxps_pulse.packetOwned();state=1;return true;
    }
    if(activity || (irq&RADIOLIB_SX127X_CLEAR_IRQ_FLAG_VALID_HEADER)) {
      ++_rxps_pulse.aborts;_rxps_pulse.cancelRequest();_rxps_pulse.phase=RxpsPulse::Phase::Stop;return true;
    }
    mod->setRfSwitchState(Module::MODE_IDLE);
    rc=pulseSX127xSetMode(RADIOLIB_SX127X_SLEEP);
    if(rc) { pulseSX127xFailure(rc,RxpsPulse::Step::Arm);return true; }
    _rxps_pulse.rx_us=0;_rxps_pulse.rx_ticks=0;
    // The host timer includes SX127x's SDK sleep entry reserve. These are
    // actual 1 MHz host ticks, not a radio autonomous timer or BUSY witness.
    _rxps_pulse.sleep_ticks=_rxps_pulse.sleep_us;
    _rxps_pulse.beginHostSleep(static_cast<uint32_t>(micros()),_rxps_pulse.sleep_ticks);
    return true;
  }
  void retireSX127xPulse(uint32_t now) {
    const bool packet=hasPendingRadioInterrupt();
    _rxps_pulse.abandon(now);state=packet?1:0;
    _profile_refresh_required=_profile_repair_required=!packet;
    _profile_retry_at=packet?0:millis()+250UL;
    finishAGCScanLease();
  }
  void serviceAGCPulse() override {
    if(!_rxps_pulse.pending || _rxps_pulse.phase==RxpsPulse::Phase::Packet) return;
    auto* mod=static_cast<CustomSX1276*>(_radio)->getMod();
    const uint32_t now=static_cast<uint32_t>(micros());
    if(_rxps_pulse.phase==RxpsPulse::Phase::Sleep) {
      // Timer only: no SPI, IRQ acknowledgement, RF switch or mode writes.
      if(!_rxps_pulse.hostSleepElapsed(now)) return;
      uint8_t mode=0;const int16_t rc=pulseSX127xMode(mode);
      if(rc || mode!=uint8_t(_sx127x_pulse_mode_prefix|RADIOLIB_SX127X_SLEEP)) {
        pulseSX127xFailure(rc?rc:RxpsPulse::ModeError,RxpsPulse::Step::Witness);return;
      }
      _rxps_pulse.finishHostSleep(now);
    }
    if(_rxps_pulse.phase==RxpsPulse::Phase::Recovery) {
      if(now-_rxps_pulse.retry_at<1000UL) return;
      if(_rxps_pulse_retries>=3) { retireSX127xPulse(now);return; }
      ++_rxps_pulse_retries;
      uint8_t mode=0,activity=0;uint16_t irq=0;
      const int16_t rc=pulseSX127xSnapshot(mode,irq,activity);
      if(rc) { pulseSX127xFailure(rc,RxpsPulse::Step::Recovery);return; }
      _sx127x_pulse_mode_prefix=mode&0xF8;
      if((irq&RADIOLIB_SX127X_CLEAR_IRQ_FLAG_RX_DONE) || hasPendingRadioInterrupt()
          || ((mode&7)==RADIOLIB_SX127X_RXCONTINUOUS && activity)) {
        _rxps_pulse.cancelRequest();_rxps_pulse.packetOwned();state=1;return;
      }
    }
    if(!_sx127x_pulse_resume_stage) {
      const int16_t rc=pulseSX127xSetMode(RADIOLIB_SX127X_STANDBY);
      if(rc) { pulseSX127xFailure(rc,RxpsPulse::Step::Standby);return; }
      mod->setRfSwitchState(Module::MODE_IDLE);
      _sx127x_pulse_resume_stage=1;_sx127x_pulse_wake_at=static_cast<uint32_t>(micros());
      _rxps_pulse.phase=RxpsPulse::Phase::Stop;return;
    }
    if(now-_sx127x_pulse_wake_at<rxPowerSavingTransitionUs()) return;
    if(_sx127x_pulse_resume_stage==1) {
      uint8_t mode=0,activity=0;uint16_t irq=0;
      int16_t rc=pulseSX127xSnapshot(mode,irq,activity);
      if(rc) { pulseSX127xFailure(rc,RxpsPulse::Step::Irq);return; }
      if((irq&RADIOLIB_SX127X_CLEAR_IRQ_FLAG_RX_DONE) || hasPendingRadioInterrupt()) {
        _rxps_pulse.cancelRequest();_rxps_pulse.packetOwned();state=1;return;
      }
      mod->setRfSwitchState(Module::MODE_RX);
      rc=pulseSX127xSetMode(RADIOLIB_SX127X_RXCONTINUOUS);
      if(rc) { pulseSX127xFailure(rc,RxpsPulse::Step::SetRx);return; }
      _sx127x_pulse_resume_stage=2;_sx127x_pulse_wake_at=static_cast<uint32_t>(micros());
      _rxps_pulse.rearming();return;
    }
    uint8_t mode=0;int16_t rc=pulseSX127xMode(mode);
    if(!rc && mode!=uint8_t(_sx127x_pulse_mode_prefix|RADIOLIB_SX127X_RXCONTINUOUS)) rc=RxpsPulse::ModeError;
    const int16_t hop=rc?0:mod->SPIgetRegValue(RADIOLIB_SX127X_REG_HOP_CHANNEL);
    if(!rc && (hop<0 || (hop&0x80))) rc=hop<0?hop:RxpsPulse::ModeError;
    if(rc) { pulseSX127xFailure(rc,RxpsPulse::Step::Mode);return; }
    ++_rxps_pulse.verified;
    validateAGCScanLease();_rxps_pulse.receiveEntry(RADIOLIB_ERR_NONE,static_cast<uint32_t>(micros()));
    state=1;_startrx_fails=0;_rx_retry_pending=false;_rx_retry_delay_ms=0;
    _nf_sample_from=millis()+NoiseFloorSettleMillis;
    finishAGCScanLease();
  }
#endif
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
