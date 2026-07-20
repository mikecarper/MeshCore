
#define RADIOLIB_STATIC_ONLY 1
#include "RadioLibWrappers.h"

#define STATE_IDLE       0
#define STATE_RX         1
#define STATE_TX_WAIT    3
#define STATE_TX_DONE    4
#define STATE_INT_READY 16

#define NUM_NOISE_FLOOR_SAMPLES  64
#define SAMPLING_THRESHOLD  14

// On-demand noise-floor calibration windows (RX duty-cycle powersaving only).
// Requests are coalesced so retries cannot repeatedly force continuous RX.
#define NF_CALIB_INTERVAL_MS  2000UL    // match the original 2-second refresh cadence
#define NF_CALIB_TIMEOUT_MS   5000UL    // give up on the batch (busy channel)
#define NF_CONTINUOUS_TIMEOUT_MS 1000UL // bound awake time without RX powersaving
#define NF_CALIB_SETTLE_MS    20UL      // frontend/AGC settle after RX entry

static volatile uint8_t state = STATE_IDLE;

// this function is called when a complete packet
// is transmitted by the module
static 
#if defined(ESP8266) || defined(ESP32)
  ICACHE_RAM_ATTR
#endif
void setFlag(void) {
  // we sent a packet, set the flag
  state |= STATE_INT_READY;
}

void RadioLibWrapper::begin() {
  _radio->setPacketReceivedAction(setFlag);  // this is also SentComplete interrupt
  _preamble_sf = getSpreadingFactor();
  _radio->setPreambleLength(preambleLengthForSF(_preamble_sf)); // longer preamble for lower SF improves reliability
  state = STATE_IDLE;

  if (_board->getStartupReason() == BD_STARTUP_RX_PACKET) {  // received a LoRa packet (while in deep sleep)
    setFlag(); // LoRa packet is already received
  }

  _noise_floor = 0;
  _noise_floor_valid = false;
  _threshold = 0;
  _cad_enabled = false;

  // start average out some samples
  _num_floor_samples = 0;
  _floor_sample_sum = 0;
  _nf_calib_active = false;
  _nf_last_calib = 0;
  _nf_sample_from = 0;
  _nf_refresh_requested = true;  // establish one baseline after startup
  // Arm the bounded sampling window only after the radio actually reaches RX.
  // Companion initialization can take longer than the window itself.
  _nf_calib_deadline = 0;
}

uint32_t RadioLibWrapper::getRngSeed() {
  return _radio->random(0x7FFFFFFF);
}

void RadioLibWrapper::setTxPower(int8_t dbm) {
  _cur_dbm = dbm;
  _dbm_valid = true;
  _radio->setOutputPower(dbm);
}

uint8_t RadioLibWrapper::beginReconfigure() {
  const uint8_t base_state = state & ~STATE_INT_READY;
  // On SX126x/LR11xx duty-cycle RX, BUSY may remain asserted during the sleep
  // side of the cycle. Do not issue an IRQ/preamble query over SPI then; due
  // scheduled work will retry as soon as the next safe listen window opens.
  if ((state & STATE_INT_READY) != 0 || base_state == STATE_TX_WAIT
      || isChipBusy() || isReceivingPacket()) {
    return 2;
  }

  const bool resume_rx = base_state == STATE_RX;
  if (_rx_ps_armed) {
    stopReceiveDutyCycle();
  } else if (resume_rx) {
    _radio->standby();
  }
  state = STATE_IDLE;
  return resume_rx;
}

void RadioLibWrapper::endReconfigure(bool resume_rx) {
  if (resume_rx) startRecv();
}

mesh::RadioParamApplyResult RadioLibWrapper::trySetParams(float freq, float bw, uint8_t sf, uint8_t cr,
                                                          const uint32_t* rx_ps_timings) {
  if (rx_ps_timings != NULL && !supportsRxPowerSaving()) {
    return mesh::RadioParamApplyResult::FAILED;
  }

  uint8_t resume_rx = beginReconfigure();
  if (resume_rx > 1) return mesh::RadioParamApplyResult::BUSY;

  const bool had_previous_params = _params_valid;
  const float previous_freq = _cur_freq;
  const float previous_bw = _cur_bw;
  const uint8_t previous_sf = _cur_sf;
  const uint8_t previous_cr = _cur_cr;

  bool success = applyParams(freq, bw, sf, cr);
  if (success) {
    cacheParams(freq, bw, sf, cr);
    if (rx_ps_timings != NULL) {
      _rx_ps_enabled = true;
      _rx_ps_rx_us = rx_ps_timings[0];
      _rx_ps_sleep_us = rx_ps_timings[1];
    }
  } else {
    bool restored = had_previous_params
      && applyParams(previous_freq, previous_bw, previous_sf, previous_cr);

    if (!restored && radioDeepInit()) {
      _rx_ps_armed = false;
      state = STATE_IDLE;
      _radio->setPacketReceivedAction(setFlag);
      if (had_previous_params) {
        restored = applyParams(previous_freq, previous_bw, previous_sf, previous_cr);
      } else {
        _preamble_sf = getSpreadingFactor();
        restored = _radio->setPreambleLength(preambleLengthForSF(_preamble_sf)) == RADIOLIB_ERR_NONE;
      }
      if (_dbm_valid) _radio->setOutputPower(_cur_dbm);
      if (_rx_boosted_gain_valid) applyRxBoostedGainMode(_cur_rx_boosted_gain);
    }

    if (!restored) {
      MESH_DEBUG_PRINTLN("RadioLibWrapper: failed to restore radio parameters after apply failure");
    }
  }

  endReconfigure(resume_rx);
  return success ? mesh::RadioParamApplyResult::APPLIED : mesh::RadioParamApplyResult::FAILED;
}

bool RadioLibWrapper::setParams(float freq, float bw, uint8_t sf, uint8_t cr,
                                const uint32_t* rx_ps_timings) {
  return trySetParams(freq, bw, sf, cr, rx_ps_timings) == mesh::RadioParamApplyResult::APPLIED;
}

bool RadioLibWrapper::setRxBoostedGainMode(bool enabled) {
  uint8_t resume_rx = beginReconfigure();
  if (resume_rx > 1) return false;

  bool success = applyRxBoostedGainMode(enabled);
  if (success) {
    _cur_rx_boosted_gain = enabled;
    _rx_boosted_gain_valid = true;
  }

  endReconfigure(resume_rx);
  return success;
}

void RadioLibWrapper::idle() {
  _radio->standby();
  state = STATE_IDLE;   // need another startReceive()
}

void RadioLibWrapper::triggerNoiseFloorCalibrate(int threshold) {
  _threshold = threshold;
  // Calibration is independent of interference detection. Callers such as the
  // Dispatcher and KISS modem use a zero threshold but still expect a fresh
  // floor measurement on every scheduled request.
  requestNoiseFloorRefresh();
}

void RadioLibWrapper::requestNoiseFloorRefresh() {
  if (_nf_refresh_requested) return;
  _nf_refresh_requested = true;
  _num_floor_samples = 0;
  _floor_sample_sum = 0;
  _nf_calib_deadline = 0;  // starts when continuous RX is actually available
}

void RadioLibWrapper::doResetAGC() {
  _radio->sleep();  // warm sleep to reset analog frontend
}

void RadioLibWrapper::resetAGC() {
  // make sure we're not mid-receiving and mid-sending of packet!
  if ((state & STATE_INT_READY) != 0 || isReceivingPacket() || (state == STATE_TX_WAIT)) return;

  doResetAGC();
  state = STATE_IDLE;   // trigger a startReceive()
  if (_rx_boosted_gain_valid) applyRxBoostedGainMode(_cur_rx_boosted_gain);

  // Reset noise floor sampling so it reconverges from scratch.
  // Without this, a stuck _noise_floor of -120 makes the sampling threshold
  // too low (-106) to accept normal samples (~-105), self-reinforcing the
  // stuck value even after the receiver has recovered.
  _noise_floor = 0;
  _noise_floor_valid = false;
  _nf_calib_active = false;
  _nf_refresh_requested = true;
  _nf_last_calib = 0;
  _nf_sample_from = 0;
  _num_floor_samples = 0;
  _floor_sample_sum = 0;
  _nf_calib_deadline = 0;  // starts after reset recovery reaches RX
}

void RadioLibWrapper::rxPsWatchdogCheck() {
  // don't interfere mid-transmit or with a completed-but-unread packet
  // (a pending DIO1 event is itself proof the radio is alive; recvRaw() will
  // re-arm and re-base the watchdog)
  if ((state & STATE_INT_READY) != 0 || (state & ~STATE_INT_READY) == STATE_TX_WAIT) {
    _wd_observe_until = 0;
    return;
  }

  unsigned long now = millis();
  bool tripped = false;

  if (_rx_ps_armed && state == STATE_RX && _wd_stuck_thresh > 0) {
    bool busy = isChipBusy();
    if (busy != _wd_last_busy) {
      // the sleep/listen wave is present -> radio healthy
      _wd_last_busy = busy;
      _wd_last_transition = now;
      _wd_stage = 0;
      _wd_strikes = 0;
      _wd_observe_until = 0;
    } else if (_wd_observe_until != 0) {
      // active observation window in progress (MCU kept awake via
      // isWatchdogObserving()); a healthy chip must toggle BUSY within it
      if ((long)(now - _wd_observe_until) >= 0) {
        _wd_observe_until = 0;
        if (!busy && isReceivingPacket()) {
          // BUSY held low by an ongoing reception (extended RX) - alive
          _wd_last_transition = now;
          _wd_strikes = 0;
        } else if (++_wd_strikes >= 2) {
          _wd_strikes = 0;
          tripped = true;
        } else {
          _wd_last_transition = now;   // full threshold before the next window
        }
      }
    } else if (now - _wd_last_transition > _wd_stuck_thresh) {
      // no proof of life for too long: actively watch one full cycle
      _wd_observe_until = now + _wd_observe_ms;
      if (_wd_observe_until == 0) _wd_observe_until = 1;  // 0 means "off"
    }
  } else {
    _wd_observe_until = 0;
  }
  if (_startrx_fails >= 3) tripped = true;  // can't even re-arm receive mode

  if (!tripped) return;

  _wd_last_transition = now;   // grace period before the next escalation
  _startrx_fails = 0;
  _wd_observe_until = 0;

  if (_wd_stage == 0) {
    _wd_stage = 1;
    n_wd_soft++;
    MESH_DEBUG_PRINTLN("RadioLibWrapper: watchdog: RX duty-cycle stuck, soft re-arm");
    state = STATE_IDLE;   // next recvRaw() re-arms receive mode
  } else {
    _wd_stage = 2;
    n_wd_hard++;
    MESH_DEBUG_PRINTLN("RadioLibWrapper: watchdog: still stuck, hard radio reset");
    if (radioDeepInit()) {
      _rx_ps_armed = false;   // chip is factory-fresh after NRST
      state = STATE_IDLE;
      _radio->setPacketReceivedAction(setFlag);
      if (_params_valid) setParams(_cur_freq, _cur_bw, _cur_sf, _cur_cr);
      if (_dbm_valid) _radio->setOutputPower(_cur_dbm);
      if (_rx_boosted_gain_valid) applyRxBoostedGainMode(_cur_rx_boosted_gain);
    }
    state = STATE_IDLE;   // re-arm (rx powersaving settings are kept in members)
  }
}

// On-demand noise-floor calibration, active only with RX duty-cycle powersaving:
// a duty-cycled receiver can't be sampled reliably (the frontend is off in the
// sleep windows and settling right after each wake), so a requested refresh
// drops receive mode to plain continuous RX, collects a fresh sample batch,
// and re-arms the duty cycle. Requests are rate-limited and coalesced.
void RadioLibWrapper::noiseFloorCalibCheck(unsigned long now) {
  if (_nf_calib_active) {
    if (!_rx_ps_enabled
        || ((long)(now - _nf_calib_deadline) >= 0
            && _num_floor_samples < NUM_NOISE_FLOOR_SAMPLES)) {
      // powersaving turned off mid-window, or the batch couldn't complete
      // (busy channel / stuck filter) - keep the previous floor
      endNoiseFloorCalib(now);
    }
  } else if (_nf_refresh_requested && _rx_ps_enabled && _rx_ps_armed && state == STATE_RX
             && ((!_noise_floor_valid && _nf_last_calib == 0)
                 || now - _nf_last_calib >= NF_CALIB_INTERVAL_MS)
             && !isReceivingPacket()) {
    // never interrupt an ongoing reception to calibrate (a TX in flight is
    // already excluded by state == STATE_RX); retries next loop iteration
    _nf_calib_active = true;
    _nf_calib_deadline = now + NF_CALIB_TIMEOUT_MS;
    _nf_sample_from = now + NF_CALIB_SETTLE_MS;
    _num_floor_samples = 0;   // start a fresh batch for this window
    _floor_sample_sum = 0;
    state = STATE_IDLE;   // recvRaw() re-arms; startReceiveMode() sees the
                          // active flag and starts continuous RX, not duty-cycle
  }
}

void RadioLibWrapper::endNoiseFloorCalib(unsigned long now) {
  _nf_calib_active = false;
  _nf_refresh_requested = false;
  _nf_last_calib = now;
  _nf_calib_deadline = 0;
  // force a receive re-arm back into duty-cycle mode, but don't clobber a
  // completed-but-unread packet or an in-flight TX (recvRaw()/onSendFinished()
  // will re-arm right after those anyway; same guard style as setRxPowerSaving)
  if ((state & STATE_INT_READY) == 0 && (state & ~STATE_INT_READY) != STATE_TX_WAIT) {
    state = STATE_IDLE;
  }
}

void RadioLibWrapper::loop() {
  if (_rx_ps_enabled) {
    rxPsWatchdogCheck();
  }
  unsigned long now = millis();
  if (_nf_calib_active || _nf_refresh_requested) {
    noiseFloorCalibCheck(now);
  }
  if (_nf_refresh_requested && !_rx_ps_enabled && _nf_calib_deadline == 0
      && state == STATE_RX) {
    // Measure the awake-time bound from actual continuous RX, not from begin()
    // or a request made while the radio is idle, transmitting, or starting up.
    _nf_calib_deadline = now + NF_CONTINUOUS_TIMEOUT_MS;
  }
  if (_nf_refresh_requested && _num_floor_samples >= NUM_NOISE_FLOOR_SAMPLES
      && _floor_sample_sum != 0) {
    int16_t sampled_floor = _floor_sample_sum / NUM_NOISE_FLOOR_SAMPLES;
    if (sampled_floor < -120) {
      sampled_floor = -120;    // clamp to lower bound of -120dBi
    }
    if (_noise_floor_valid) {
      // Favor the fresh high-rate batch while retaining a small amount of
      // history: 25% previous floor + 75% newly sampled floor. Round the
      // negative dBm result to the nearest integer instead of toward zero.
      int32_t weighted_floor = (int32_t)_noise_floor + 3L * sampled_floor;
      _noise_floor = weighted_floor < 0 ? (weighted_floor - 2) / 4
                                        : (weighted_floor + 2) / 4;
    } else {
      _noise_floor = sampled_floor;
    }
    _floor_sample_sum = 0;
    _noise_floor_valid = true;
    _nf_refresh_requested = false;
    _nf_last_calib = now;
    _nf_calib_deadline = 0;

    MESH_DEBUG_PRINTLN("RadioLibWrapper: noise_floor = %d", (int)_noise_floor);

    if (_nf_calib_active) {
      endNoiseFloorCalib(now);   // fresh floor published - back to duty cycle
    }
    return;
  }

  if (_nf_refresh_requested && !_rx_ps_enabled && _nf_calib_deadline != 0
      && (long)(now - _nf_calib_deadline) >= 0) {
    // A continuously busy channel can reject every candidate sample. Do not
    // keep the MCU awake indefinitely; retain the previous floor and let the
    // next scheduled calibration request retry an invalid startup baseline.
    _nf_refresh_requested = false;
    _nf_last_calib = now;
    _nf_calib_deadline = 0;
    _num_floor_samples = 0;
    _floor_sample_sum = 0;
  }

  if (_nf_refresh_requested && state == STATE_RX
      && _num_floor_samples < NUM_NOISE_FLOOR_SAMPLES) {
    // Noise floor is only sampled outside RX duty-cycle mode: continuously in
    // plain RX (powersaving off), or inside an on-demand calibration window
    // (powersaving on), skipping the first moments after RX entry there while
    // the frontend/AGC settles (unsettled GetRssiInst reads ~-127 dBm garbage).
    if (!_rx_ps_armed
        && !(_nf_calib_active && (long)(now - _nf_sample_from) < 0)
        && !isReceivingPacket()) {
      int rssi = getCurrentRSSI();
      if (rssi < _noise_floor + SAMPLING_THRESHOLD) {  // only consider samples below current floor + sampling THRESHOLD
        _num_floor_samples++;
        _floor_sample_sum += rssi;
      }
    }
  }
}

void RadioLibWrapper::startRecv() {
  int err = startReceiveMode();
  if (err == RADIOLIB_ERR_NONE) {
    state = STATE_RX;
    _startrx_fails = 0;
    if (_rx_ps_armed) {
      // (re)base the duty-cycle watchdog on the freshly armed cycle
      _wd_last_busy = isChipBusy();
      _wd_last_transition = millis();
      // Longest legitimate silence on the BUSY pin: one full cycle, plus the
      // extended RX after a (possibly false) preamble detect (2*rx + sleep),
      // plus a worst-case packet airtime, plus margin for TCXO/transitions.
      // Floored at 60s so a light-sleeping MCU (ESP32 wakes every ~30s) opens
      // an observation window every couple of wakeups instead of on each one.
      uint32_t rx_ms = _rx_ps_rx_us / 1000, sleep_ms = _rx_ps_sleep_us / 1000;
      _wd_stuck_thresh = (rx_ms + sleep_ms) + 2 * (2 * rx_ms + sleep_ms)
                         + getEstAirtimeFor(MAX_TRANS_UNIT) + 1000;
      if (_wd_stuck_thresh < 60000) _wd_stuck_thresh = 60000;
      // active observation window must cover one full duty cycle
      _wd_observe_ms = rx_ms + sleep_ms + 50;
      if (_wd_observe_ms > 1500) _wd_observe_ms = 1500;
    }
  } else {
    if (_startrx_fails < 255) _startrx_fails++;
    MESH_DEBUG_PRINTLN("RadioLibWrapper: error: startReceiveMode(%d)", err);
  }
}

int RadioLibWrapper::startReceiveMode() {
  return _radio->startReceive();
}

void RadioLibWrapper::stopReceiveDutyCycle() {
  // The duty-cycle sequencer only stops on RxDone or an explicit standby;
  // issuing other mode commands while it runs leads to undefined behaviour.
  _radio->standby();
  _rx_ps_armed = false;
}

bool RadioLibWrapper::isPacketReady() {
  if (!_rx_ps_armed) return true;   // continuous RX: DIO1 only fires for RxDone/TxDone here

  // In duty-cycle RX the DIO1 interrupt also fires for RX timeout (false
  // preamble detect) and header errors. GetRxBufferStatus still reports the
  // *previous* packet's length then, so reading the buffer would re-deliver
  // stale bytes as a ghost packet. Only read when the radio reports RxDone.
  // (checkIrq errors are treated as ready, falling back to old behaviour.)
  return _radio->checkIrq(RADIOLIB_IRQ_RX_DONE) != 0;
}

bool RadioLibWrapper::isInRecvMode() const {
  return (state & ~STATE_INT_READY) == STATE_RX;
}

bool RadioLibWrapper::setRxPowerSaving(bool enabled, uint32_t rx_us, uint32_t sleep_us) {
  if (enabled && !supportsRxPowerSaving()) {
    return false;
  }

  uint8_t resume_rx = beginReconfigure();
  if (resume_rx > 1) return false;

  _rx_ps_enabled = enabled;
  _rx_ps_rx_us = rx_us;
  _rx_ps_sleep_us = sleep_us;
  endReconfigure(resume_rx);
  return true;
}

int RadioLibWrapper::recvRaw(uint8_t* bytes, int sz) {
  int len = 0;
  if (state & STATE_INT_READY) {
    last_radio_interrupt_millis = millis();   // ISR fired → radio hardware is alive
    if (isPacketReady()) {
      len = _radio->getPacketLength();
      if (len > 0) {
        if (len > sz) { len = sz; }
        int err = _radio->readData(bytes, len);
        if (err != RADIOLIB_ERR_NONE) {
          MESH_DEBUG_PRINTLN("RadioLibWrapper: error: readData(%d)", err);
          len = 0;
          n_recv_errors++;
        } else {
        //  Serial.print("  readData() -> "); Serial.println(len);
          n_recv++;
          last_recv_millis = millis();
        }
      }
    }
    state = STATE_IDLE;   // need another startReceive()
  }

  if (state != STATE_RX) {
    startRecv();
  }
  return len;
}

uint32_t RadioLibWrapper::getEstAirtimeFor(int len_bytes) {
  return _radio->getTimeOnAir(len_bytes) / 1000;
}

bool RadioLibWrapper::startSendRaw(const uint8_t* bytes, int len) {
  if (_rx_ps_armed) {
    // stop the duty-cycle sequencer before SetTx, otherwise its next RTC
    // event can fire mid-transmission and abort the TX
    stopReceiveDutyCycle();
  }
  _board->onBeforeTransmit();
  int err = _radio->startTransmit((uint8_t *) bytes, len);
  if (err == RADIOLIB_ERR_NONE) {
    state = STATE_TX_WAIT;
    return true;
  }
  MESH_DEBUG_PRINTLN("RadioLibWrapper: error: startTransmit(%d)", err);
  idle();   // trigger another startRecv()
  _board->onAfterTransmit();
  return false;
}

bool RadioLibWrapper::isSendComplete() {
  if (state & STATE_INT_READY) {
    state = STATE_IDLE;
    n_sent++;
    return true;
  }
  return false;
}

void RadioLibWrapper::onSendFinished() {
  _radio->finishTransmit();
  _board->onAfterTransmit();
  state = STATE_IDLE;
}

int16_t RadioLibWrapper::performChannelScan() {
  return _radio->scanChannel();
}

bool RadioLibWrapper::isChannelActive() {
  // int.thresh: RSSI-based interference detection (relative to noise floor).
  // In RX duty-cycle mode only checked while the chip is in a listen window
  // (during the sleep window the frontend is off and the read would stall).
  if (_threshold != 0 && !(_rx_ps_armed && isChipBusy())
      && getCurrentRSSI() > _noise_floor + _threshold) return true;

  // cad: hardware channel activity detection
  if (_cad_enabled) {
    int16_t result = performChannelScan();
    // scanChannel() triggers DIO interrupt (CAD done) which sets STATE_INT_READY
    // via setFlag() ISR. Clear it before restarting RX so recvRaw() doesn't
    // try to read a non-existent packet and count a spurious recv error.
    state = STATE_IDLE;
    startRecv();
    if (result != RADIOLIB_CHANNEL_FREE) {
      _board->n_cad_busy++;
      return true;
    }
  }

  return false;
}

bool RadioLibWrapper::isReceivingPassive(int interference_margin_db) {
  // RX duty-cycle radios hold BUSY high while asleep. Do not attempt an SPI
  // IRQ/RSSI read until the next listen window. Treat the unknown channel as
  // busy so the retry is deferred instead of transmitting blind; Dispatcher
  // retains its bounded busy timeout as a last-resort escape.
  if (isChipBusy()) return true;
  if (isReceivingPacket()) return true;

  unsigned long now = millis();
  if ((!_noise_floor_valid && _nf_last_calib == 0)
      || now - _nf_last_calib >= NF_CALIB_INTERVAL_MS) {
    requestNoiseFloorRefresh();
  }
  // Until the startup baseline is ready, defer rather than compare against the
  // zero-initialized floor and transmit blind. Dispatcher retains its bounded
  // busy timeout if sampling cannot complete.
  if (!_noise_floor_valid) return true;

  // Use a fixed margin for retries even when the normal interference threshold
  // is disabled. This is passive (RSSI only): unlike CAD it does not restart RX
  // and cannot erase the forwarding echo that would cancel the retry.
  return interference_margin_db > 0
      && getCurrentRSSI() - _noise_floor >= interference_margin_db;
}

uint8_t RadioLibWrapper::getRadioState() const {
  return state;
}

float RadioLibWrapper::getLastRSSI() const {
  return _radio->getRSSI();
}
float RadioLibWrapper::getLastSNR() const {
  return _radio->getSNR();
}

// Approximate SNR threshold per SF for successful reception (based on Semtech datasheets)
static float snr_threshold[] = {
    -7.5,  // SF7 needs at least -7.5 dB SNR
    -10,   // SF8 needs at least -10 dB SNR
    -12.5, // SF9 needs at least -12.5 dB SNR
    -15,  // SF10 needs at least -15 dB SNR
    -17.5,// SF11 needs at least -17.5 dB SNR
    -20   // SF12 needs at least -20 dB SNR
};
  
float RadioLibWrapper::packetScoreInt(float snr, int sf, int packet_len) {
  if (sf < 7) return 0.0f;
  
  if (snr < snr_threshold[sf - 7]) return 0.0f;    // Below threshold, no chance of success

  auto success_rate_based_on_snr = (snr - snr_threshold[sf - 7]) / 10.0;
  auto collision_penalty = 1 - (packet_len / 256.0);   // Assuming max packet of 256 bytes

  return max(0.0, min(1.0, success_rate_based_on_snr * collision_penalty));
}
