#pragma once

#include <Mesh.h>
#include <RadioLib.h>
#include "CadTiming.h"
#include "RadioAirtime.h"
#include "RXPowerSaving.h"

// Fallback RX powersaving timings, only used until setRxPowerSaving() is called
// (begin() always applies the persisted values). The authoritative defaults live
// in RXPowerSaving.h as RX_POWERSAVING_DEFAULT_RX_US / _SLEEP_US and are
// delivered via NodePrefs; keep these mirrored.
#define RX_PS_FALLBACK_RX_US    65625UL
#define RX_PS_FALLBACK_SLEEP_US 60000UL

#ifdef USE_CC310_HW_CRYPTO
#include "../NRF52Crypto.h"
#endif
#ifdef ESP32_PLATFORM
#include "../ESP32TrueRandom.h"
#endif
struct PacketMillis {
  uint32_t preambleMillis;  // preamble-detect -> header-valid deadline
  uint32_t payloadMillis;   // header-valid   -> rx-done deadline
};

class RadioLibWrapper : public mesh::Radio {
protected:
  PhysicalLayer* _radio;
  mesh::MainBoard* _board;
  uint32_t n_recv, n_sent, n_recv_errors;
  int16_t _noise_floor, _threshold;
  float _last_rssi, _last_snr;
  bool _cad_enabled;
  bool _noise_floor_valid;
  bool _nf_refresh_requested;
  uint16_t _num_floor_samples;
  int32_t _floor_sample_sum;
  unsigned long last_recv_millis;
  unsigned long last_radio_interrupt_millis;  // updated on any ISR event, even CRC errors
  bool _rx_ps_enabled;
  bool _rx_ps_armed;      // radio is currently in RX duty-cycle mode
  bool _rx_ps_continuous_fallback; // requested RXPS is receiving continuously for this tuple
  bool _rx_hold_continuous; // keep plain RX active until Dispatcher consumes cached metadata
  uint32_t _rx_ps_rx_us;
  uint32_t _rx_ps_sleep_us;

  // RX duty-cycle watchdog: a healthy duty cycle shows a square wave on the
  // BUSY pin (high in the sleep window / TCXO warmup, low while listening).
  // If the wave stops, the chip fell out of the cycle without an IRQ.
  // Passive sampling only works while the main loop spins; on MCUs that light
  // sleep between wakeups the watchdog instead opens an "active observation"
  // window (isWatchdogObserving() keeps the MCU awake) spanning one full radio
  // cycle - a healthy chip must toggle BUSY within it.
  bool _wd_last_busy;
  uint8_t _wd_stage;                  // 0 = healthy, 1 = soft re-arm done, 2 = hard reset done
  uint8_t _wd_strikes;                // consecutive failed observation windows
  uint8_t _startrx_fails;             // consecutive startReceiveMode() failures
  unsigned long _wd_last_transition;  // millis of last BUSY level change (proof of life)
  unsigned long _wd_stuck_thresh;     // ms without proof of life before observing
  unsigned long _wd_observe_until;    // 0 = not observing, else millis deadline
  uint32_t _wd_observe_ms;            // observation window: one full cycle + margin
  uint32_t n_wd_soft, n_wd_hard;

  // last applied radio settings, reapplied after a hard radio reset
  float _cur_freq, _cur_bw;
  uint8_t _cur_sf, _cur_cr;
  int8_t _cur_dbm;
  bool _cur_rx_boosted_gain;
  bool _params_valid, _dbm_valid, _rx_boosted_gain_valid;

  // On-demand noise-floor calibration while RX duty-cycle powersaving is
  // armed. A duty-cycled receiver can't be sampled reliably, so a requested
  // refresh briefly drops to continuous RX, publishes an average, then re-arms
  // the duty cycle.
  bool _nf_calib_active;
  unsigned long _nf_last_calib;       // millis of last completed/attempted window
  unsigned long _nf_calib_deadline;   // abort window if the batch can't complete
  unsigned long _nf_sample_from;      // no samples before this (RX entry settle)

  void idle() override;
  void startRecv() override;
  void rxPsWatchdogCheck();
  void requestNoiseFloorRefresh();
  void requestRestartRecv();
  bool isPacketPendingOrReceiving();
  void noiseFloorCalibCheck(unsigned long now);
  void endNoiseFloorCalib(unsigned long now);
  void finishReceiveProcessing();
  void cacheParams(float freq, float bw, uint8_t sf, uint8_t cr) {
    _cur_freq = freq; _cur_bw = bw; _cur_sf = sf; _cur_cr = cr; _params_valid = true;
  }
  unsigned long cadScanTimeoutMillis() const {
    const uint8_t sf = _params_valid ? _cur_sf : getSpreadingFactor();
    const float bw = _params_valid ? _cur_bw : static_cast<float>(LORA_BW);
    return mesh::calculateCadScanTimeoutMillis(sf, bw);
  }
  int16_t performChannelScanWithTimeout(unsigned long timeout_ms);
  virtual int startReceiveMode();
  virtual void stopReceiveDutyCycle();
  virtual bool isPacketReady();
  // true while the chip cannot service SPI (RX duty-cycle sleep window, or
  // briefly while processing a command); radios expose this via the BUSY pin
  virtual bool isChipBusy() { return false; }
  // full radio recovery: hardware reset (NRST) + re-init to boot defaults;
  // returns false if unsupported. Caller reapplies cached runtime params.
  virtual bool radioDeepInit() { return false; }
  virtual bool supportsRadioDeepInit() const { return false; }
  virtual bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) = 0;
  virtual bool applyRxBoostedGainMode(bool) { return false; }
  // 0 = reconfigure from idle, 1 = resume RX afterwards, 2 = currently busy.
  uint8_t beginReconfigure();
  void endReconfigure(bool resume_rx);
  bool restoreAfterDeepInit();
  float packetScoreInt(float snr, int sf, int packet_len);
  virtual bool isReceivingPacket() =0;
  virtual void doResetAGC();

public:
  RadioLibWrapper(PhysicalLayer& radio, mesh::MainBoard& board)
      : _radio(&radio), _board(&board), _noise_floor_valid(false), _nf_refresh_requested(true),
        _rx_ps_enabled(false), _rx_ps_armed(false),
        _rx_ps_continuous_fallback(false), _rx_hold_continuous(false),
        _rx_ps_rx_us(RX_PS_FALLBACK_RX_US), _rx_ps_sleep_us(RX_PS_FALLBACK_SLEEP_US),
        _wd_last_busy(false), _wd_stage(0), _wd_strikes(0), _startrx_fails(0), _wd_last_transition(0),
        _wd_stuck_thresh(0), _wd_observe_until(0), _wd_observe_ms(0),
        _cur_freq(0), _cur_bw(0), _cur_sf(0), _cur_cr(0), _cur_dbm(0),
        _cur_rx_boosted_gain(false), _params_valid(false), _dbm_valid(false), _rx_boosted_gain_valid(false),
        _nf_calib_active(false), _nf_last_calib(0), _nf_calib_deadline(0), _nf_sample_from(0)
        {
          n_recv = n_sent = n_recv_errors = n_wd_soft = n_wd_hard = 0;
          _last_rssi = _last_snr = 0;
          last_recv_millis = 0;
          last_radio_interrupt_millis = 0;
          _cad_enabled = false;
        }

  void begin() override;
  int recvRaw(uint8_t* bytes, int sz) override;
  uint32_t getEstAirtimeFor(int len_bytes) override;
  bool startSendRaw(const uint8_t* bytes, int len) override;
  bool isSendComplete() override;
  void onSendFinished() override;
  bool isInRecvMode() const override;
  bool setRxPowerSaving(bool enabled, uint32_t rx_us, uint32_t sleep_us) override;
  bool isRxPowerSavingContinuousFallback() const override {
    return _rx_ps_enabled && _rx_ps_continuous_fallback;
  }
  virtual bool supportsRxPowerSavingRfRxDisable() const { return false; }
  virtual bool setRxPowerSavingRfRxDisabled(bool) { return false; }
  virtual bool isRxPowerSavingRfRxDisabled() const { return false; }
  bool isChannelActive();

  bool isReceiving() override {
    if (isReceivingPacket()) return true;

    return isChannelActive();
  }
  bool isReceivingPassive(int interference_margin_db) override;

  // When rx_ps_timings is supplied, update and re-arm the RX duty cycle in the
  // same standby transition as the modulation change. Values are {rx, sleep}.
  mesh::RadioParamApplyResult trySetParams(float freq, float bw, uint8_t sf, uint8_t cr,
                                           const uint32_t* rx_ps_timings = NULL) override;
  bool setParams(float freq, float bw, uint8_t sf, uint8_t cr,
                 const uint32_t* rx_ps_timings = NULL);
  uint32_t getRngSeed();
  void setTxPower(int8_t dbm);

  virtual float getCurrentRSSI() =0;
  virtual uint8_t getSpreadingFactor() const { return LORA_SF; }
  static uint16_t preambleLengthForParams(uint8_t sf, float bw) {
    return rxPowerSavingPreambleForParams(sf, bw);
  }
  uint16_t currentPreambleLength() const {
    return _params_valid
        ? preambleLengthForParams(_cur_sf, _cur_bw)
        : preambleLengthForParams(getSpreadingFactor(), static_cast<float>(LORA_BW));
  }
  bool updatePreamble(uint8_t sf, float bw) {
    return _radio->setPreambleLength(preambleLengthForParams(sf, bw)) == RADIOLIB_ERR_NONE;
  }
  PacketMillis calcMaxPacketMillis(uint8_t sf, float bw, uint8_t cr, uint8_t preambleSymbols);
  virtual int16_t performChannelScan();

  int getNoiseFloor() const override { return _noise_floor; }
  void triggerNoiseFloorCalibrate(int threshold) override;
  void recalibrateNoiseFloor() override;
  void setCADEnabled(bool enable) override { _cad_enabled = enable; }
  void resetAGC() override;

  void loop() override;
  bool recoverRadio(bool hard) override;

  uint32_t getPacketsRecv() const { return n_recv; }
  uint32_t getPacketsRecvErrors() const override { return n_recv_errors; }
  uint32_t getPacketsSent() const { return n_sent; }
  uint32_t getRxPsWatchdogSoftCount() const { return n_wd_soft; }
  uint32_t getRxPsWatchdogHardCount() const { return n_wd_hard; }
  // true while the watchdog is actively watching for BUSY transitions; used by
  // the app's hasPendingWork() to keep the MCU out of light sleep for the window
  bool isWatchdogObserving() const { return _wd_observe_until != 0; }
  // true while a noise-floor batch needs prompt loop service; the app's
  // hasPendingWork() keeps the MCU awake only for this short sample burst.
  bool isCalibratingNoiseFloor() const {
    return _nf_calib_active
        || (_nf_refresh_requested
            && (!_rx_ps_enabled || _rx_ps_continuous_fallback));
  }
  void resetStats() { n_recv = n_sent = n_recv_errors = 0; }

  uint8_t getRadioState() const override;
  unsigned long getLastRecvMillis() const override { return last_recv_millis; }
  unsigned long getLastRadioInterruptMillis() const override { return last_radio_interrupt_millis; }

  float getLastRSSI() const override final;
  float getLastSNR() const override final;

  float packetScore(float snr, int packet_len) override { return packetScoreInt(snr, 10, packet_len); }  // assume sf=10

  bool setRxBoostedGainMode(bool enabled);
  virtual bool supportsRxBoostedGainMode() const { return false; }
  virtual bool getRxBoostedGainMode() const { return false; }

  virtual bool configSideDetectors(const uint8_t sideDetSFs[], uint8_t num, float bw) { return false; }
};

/**
 * \brief  an RNG impl using the noise from the LoRa radio as entropy.
 *         NOTE: this is VERY SLOW!  Use only for things like creating new LocalIdentity
*/
class RadioNoiseListener : public mesh::RNG {
  PhysicalLayer* _radio;
public:
  RadioNoiseListener(PhysicalLayer& radio): _radio(&radio) { }

  void random(uint8_t* dest, size_t sz) override {
    // Preserve the existing radio/PRNG entropy on every platform. Independent
    // hardware entropy is mixed in without becoming the sole source.
    for (size_t i = 0; i < sz; i++) {
      dest[i] = _radio->randomByte() ^ (::random(0, 256) & 0xFF);
    }
#ifdef USE_CC310_HW_CRYPTO
    mesh::mixCC310Random(dest, sz);
#endif
#ifdef ESP32_PLATFORM
    mesh::mixESP32TrueRandom(dest, sz);
#endif
  }
};
