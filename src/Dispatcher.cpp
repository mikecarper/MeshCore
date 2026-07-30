#include "Dispatcher.h"

#if MESH_PACKET_LOGGING
  #include <Arduino.h>
#endif

#include <math.h>

namespace mesh {

#define MAX_RX_DELAY_MILLIS        32000  // 32 seconds
#define MIN_TX_BUDGET_RESERVE_MS   100    // min budget (ms) required before allowing next TX
#define MIN_TX_BUDGET_AIRTIME_DIV  2      // require at least 1/N of estimated airtime as budget before TX

#ifndef NOISE_FLOOR_CALIB_INTERVAL
  #define NOISE_FLOOR_CALIB_INTERVAL   2000     // refresh every 2 seconds
#endif

#ifndef RADIO_LIVENESS_SOFT_MS
  #define RADIO_LIVENESS_SOFT_MS  (30UL * 60UL * 1000UL)
#endif
#ifndef RADIO_LIVENESS_HARD_MS
  #define RADIO_LIVENESS_HARD_MS  (12UL * 60UL * 60UL * 1000UL)
#endif

#define MIN_CAD_FAIL_RETRY_DELAY_MS       50UL
#define MIN_CAD_FAIL_MAX_DURATION_MS     500UL

static uint32_t scaleCADDelayForQueue(uint32_t normal_delay, int ready_count,
                                     uint32_t minimum_delay) {
  if (ready_count <= 1) return normal_delay;

  uint32_t scaled = normal_delay / (uint32_t)ready_count;
  uint32_t floor = normal_delay < minimum_delay ? normal_delay : minimum_delay;
  return scaled < floor ? floor : scaled;
}

void Dispatcher::begin() {
  n_sent_flood = n_sent_direct = 0;
  n_recv_flood = n_recv_direct = 0;
  _err_flags = 0;
  radio_nonrx_start = _ms->getMillis();

  duty_cycle_window_ms = getDutyCycleWindowMs();
  float duty_cycle = 1.0f / (1.0f + getAirtimeBudgetFactor());
  tx_budget_ms = (unsigned long)(duty_cycle_window_ms * duty_cycle);
  last_budget_update = _ms->getMillis();

  _radio->begin();
  prev_isrecv_mode = _radio->isInRecvMode();
  const unsigned long now = _ms->getMillis();
  last_observed_radio_irq = _radio->getLastRadioInterruptMillis();
#ifdef RADIO_LIVENESS_SOFT_ONLY
  last_radio_activity_ms = now;
#else
  radio_liveness.begin(now);
  nonrx_soft_recovery_attempted = false;
#endif
}

float Dispatcher::getAirtimeBudgetFactor() const {
  return 1.0;
}

void Dispatcher::updateTxBudget() {
  unsigned long now = _ms->getMillis();
  unsigned long elapsed = now - last_budget_update;

  float duty_cycle = 1.0f / (1.0f + getAirtimeBudgetFactor());
  unsigned long max_budget = (unsigned long)(getDutyCycleWindowMs() * duty_cycle);
  unsigned long refill = (unsigned long)(elapsed * duty_cycle);
  
  if (refill > 0) {
    tx_budget_ms += refill;
    if (tx_budget_ms > max_budget) {
      tx_budget_ms = max_budget;
    }
    last_budget_update = now;
  }
}

void Dispatcher::restoreOutboundTxOverrides() {
  if (outbound_restore_cr != 0) {
    _radio->setCodingRate(outbound_restore_cr);
    outbound_restore_cr = 0;
  }
  if (outbound_restore_preamble_len != 0) {
    _radio->setPreambleLength(outbound_restore_preamble_len);
    outbound_restore_preamble_len = 0;
  }
}

int Dispatcher::calcRxDelay(float score, uint32_t air_time) const {
  return (int) ((powf(10.0f, 0.85f - score) - 1.0f) * air_time);
}

uint32_t Dispatcher::getCADFailRetryDelay() const {
  return 200;
}
uint32_t Dispatcher::getCADFailMaxDuration() const {
  return 4000;   // 4 seconds
}

#if MESH_PACKET_LOGGING
void Dispatcher::logPacketStart(const char* direction, const Packet* packet, int len) {
  Serial.print(getLogDateTime());
  Serial.printf(": %s, len=%d (type=%d, route=%s, payload_len=%d)",
                direction, len, packet->getPayloadType(),
                packet->isRouteDirect() ? "D" : "F", packet->payload_len);
}

void Dispatcher::logPacketEnd(const Packet* packet) {
  const uint8_t type = packet->getPayloadType();
  if (packet->payload_len >= 2
      && (type == PAYLOAD_TYPE_PATH || type == PAYLOAD_TYPE_REQ
          || type == PAYLOAD_TYPE_RESPONSE || type == PAYLOAD_TYPE_TXT_MSG)) {
    Serial.printf(" [%02X -> %02X]\n", (uint32_t)packet->payload[1],
                  (uint32_t)packet->payload[0]);
  } else {
    Serial.write((uint8_t)'\n');
  }
}
#endif

bool Dispatcher::getNextQueueWakeDelay(uint32_t& delay_millis) const {
  const uint32_t now = _ms->getMillis();
  bool found = false;
  uint32_t shortest_delay = 0;

  if (outbound != NULL) {
    // TX completion/timeout still needs the normal fast lifecycle path.
    delay_millis = 0;
    return true;
  }

  uint32_t scheduled_for;
  if (_mgr->getNextOutboundTime(now, scheduled_for)) {
    int32_t signed_queue_delay = (int32_t)(scheduled_for - now);
    uint32_t outbound_delay = signed_queue_delay > 0 ? (uint32_t)signed_queue_delay : 0;
    int32_t signed_tx_delay = (int32_t)(next_tx_time - now);
    if (signed_tx_delay > 0 && (uint32_t)signed_tx_delay > outbound_delay) {
      outbound_delay = (uint32_t)signed_tx_delay;
    }
    shortest_delay = outbound_delay;
    found = true;
  }

  if (_mgr->getNextInboundTime(now, scheduled_for)) {
    int32_t signed_inbound_delay = (int32_t)(scheduled_for - now);
    uint32_t inbound_delay = signed_inbound_delay > 0 ? (uint32_t)signed_inbound_delay : 0;
    if (!found || inbound_delay < shortest_delay) {
      shortest_delay = inbound_delay;
      found = true;
    }
  }

  if (found) delay_millis = shortest_delay;
  return found;
}

#ifdef WITH_MQTT_BRIDGE
uint32_t Dispatcher::getRadioWatchdogMillis() const {
  return RADIO_WATCHDOG_MS;
}
#endif

void Dispatcher::loop() {
  if (millisHasNowPassed(next_floor_calib_time)) {
    _radio->triggerNoiseFloorCalibrate(getInterferenceThreshold());
    _radio->setCADEnabled(getCADEnabled());
    next_floor_calib_time = futureMillis(NOISE_FLOOR_CALIB_INTERVAL);
  }
  _radio->loop();

  const unsigned long now = _ms->getMillis();
  const unsigned long latest_irq = _radio->getLastRadioInterruptMillis();
  if (latest_irq != 0 && latest_irq != last_observed_radio_irq) {
    last_observed_radio_irq = latest_irq;
#ifdef RADIO_LIVENESS_SOFT_ONLY
    last_radio_activity_ms = now;
#else
    radio_liveness.noteActivity(now);
#endif
  }

  // check for radio 'stuck' in mode other than Rx
  bool is_recv = _radio->isInRecvMode();
  if (is_recv != prev_isrecv_mode) {
    prev_isrecv_mode = is_recv;
    if (!is_recv) {
      radio_nonrx_start = now;
    }
#ifndef RADIO_LIVENESS_SOFT_ONLY
    else {
      nonrx_soft_recovery_attempted = false;
    }
#endif
  }
  bool recovered_this_loop = false;
  if (!is_recv && outbound == NULL && now - radio_nonrx_start > 8000) {
    _err_flags |= ERR_EVENT_STARTRX_TIMEOUT;
#ifdef RADIO_LIVENESS_SOFT_ONLY
    MESH_DEBUG_PRINTLN("Radio watchdog: radio outside RX for %lu ms; soft recovery",
                       now - radio_nonrx_start);
    if (_radio->recoverRadio(false)) {
      last_radio_activity_ms = now;
      recovered_this_loop = true;
    }
#else
    const bool hard = nonrx_soft_recovery_attempted;
    MESH_DEBUG_PRINTLN("Radio watchdog: radio outside RX for %lu ms; %s recovery",
                       now - radio_nonrx_start, hard ? "hard" : "soft");
    if (_radio->recoverRadio(hard)) recovered_this_loop = true;
    nonrx_soft_recovery_attempted = true;
#endif
    radio_nonrx_start = now; // bounded retry cadence if recovery is unsupported
  }

  // Continuous-RX can fail while the MCU and main loop remain healthy, so a
  // CPU watchdog cannot detect it.  Use packet/IRQ/TX activity as proof of
  // radio life and stage recovery from a harmless re-arm to a radio-only
  // hardware reset when the driver supports one. Observer configuration may
  // request a faster soft check.
  uint32_t soft_liveness_ms = RADIO_LIVENESS_SOFT_MS;
#ifdef WITH_MQTT_BRIDGE
  const uint32_t configured_watchdog_ms = getRadioWatchdogMillis();
  if (configured_watchdog_ms > 0) soft_liveness_ms = configured_watchdog_ms;
#endif
  if (is_recv && outbound == NULL && !recovered_this_loop) {
#ifdef RADIO_LIVENESS_SOFT_ONLY
    if (soft_liveness_ms > 0 && now - last_radio_activity_ms >= soft_liveness_ms) {
      _err_flags |= ERR_EVENT_RADIO_WATCHDOG;
      MESH_DEBUG_PRINTLN("Radio watchdog: no hardware activity for %lu ms, state=%d, soft recovery",
                         now - last_radio_activity_ms, _radio->getRadioState());
      _radio->recoverRadio(false);
      // This radio has no independently resettable RF peripheral. Retry its
      // only safe recovery at the soft interval until IRQ/TX/RX proves life.
      last_radio_activity_ms = now;
    }
#else
    uint32_t hard_liveness_ms = RADIO_LIVENESS_HARD_MS;
    if (soft_liveness_ms > hard_liveness_ms / 2) {
      hard_liveness_ms = soft_liveness_ms <= 0x7FFFFFFFUL
          ? soft_liveness_ms * 2 : 0xFFFFFFFFUL;
    }
    const RadioRecoveryAction action = radio_liveness.poll(
        now, soft_liveness_ms, hard_liveness_ms);
    if (action != RadioRecoveryAction::NONE) {
      const bool hard = action == RadioRecoveryAction::HARD;
      _err_flags |= ERR_EVENT_RADIO_WATCHDOG;
      MESH_DEBUG_PRINTLN("Radio watchdog: no hardware activity for %lu ms, state=%d, %s recovery",
                         now - radio_liveness.lastActivity(), _radio->getRadioState(),
                         hard ? "hard" : "soft");
      const bool recovered = _radio->recoverRadio(hard);
      if (hard) radio_liveness.noteHardRecoveryResult(now, recovered);
    }
#endif
  }

  if (outbound) {  // waiting for outbound send to be completed
    if (_radio->isSendComplete()) {
      long t = _ms->getMillis() - outbound_start;
      total_air_time += t;
      //Serial.print("  airtime="); Serial.println(t);

      updateTxBudget();

      if (t > tx_budget_ms) {
        tx_budget_ms = 0;
      } else {
        tx_budget_ms -= t;
      }

      if (tx_budget_ms < MIN_TX_BUDGET_RESERVE_MS) {
        float duty_cycle = 1.0f / (1.0f + getAirtimeBudgetFactor());
        unsigned long needed = MIN_TX_BUDGET_RESERVE_MS - tx_budget_ms;
        next_tx_time = futureMillis((unsigned long)(needed / duty_cycle));
      } else {
        next_tx_time = _ms->getMillis();
      }

      _radio->onSendFinished();
#ifdef RADIO_LIVENESS_SOFT_ONLY
      last_radio_activity_ms = _ms->getMillis();  // TX success -> radio is alive
#else
      radio_liveness.noteActivity(_ms->getMillis());  // TX success -> radio is alive
#endif
      restoreOutboundTxOverrides();
      logTx(outbound, 2 + outbound->getPathByteLen() + outbound->payload_len);
      onSendComplete(outbound);
      if (outbound->isRouteFlood()) {
        n_sent_flood++;
      } else {
        n_sent_direct++;
      }
      releasePacket(outbound);  // return to pool
      outbound = NULL;
    } else if (millisHasNowPassed(outbound_expiry)) {
      MESH_DEBUG_PRINTLN("%s Dispatcher::loop(): WARNING: outbound packed send timed out!", getLogDateTime());

      _radio->onSendFinished();
      restoreOutboundTxOverrides();
      logTxFail(outbound, 2 + outbound->getPathByteLen() + outbound->payload_len);
      onSendFail(outbound);

      releasePacket(outbound);  // return to pool
      outbound = NULL;
    } else {
      return;  // can't do any more radio activity until send is complete or timed out
    }

    // going back into receive mode now...
    next_agc_reset_time = futureMillis(getAGCResetInterval());
  }

  if (getAGCResetInterval() > 0 && millisHasNowPassed(next_agc_reset_time)) {
    _radio->resetAGC();
    next_agc_reset_time = futureMillis(getAGCResetInterval());
  }

  // check inbound (delayed) queue
  {
    const uint32_t now = _ms->getMillis();
    uint32_t next_inbound;
    // Packet managers with deadline support avoid a full priority scan while
    // every delayed packet is still in the future. Legacy managers retain the
    // original getNextInbound() behavior.
    if (!_mgr->getNextInboundTime(now, next_inbound)
        || (int32_t)(next_inbound - now) <= 0) {
      Packet* pkt = _mgr->getNextInbound(now);
      if (pkt) {
        processRecvPacket(pkt);
      }
    }
  }
  checkRecv();
  checkSend();
  releaseDroppedOutbound();
}

void Dispatcher::releaseDroppedOutbound() {
  Packet* dropped;
  while ((dropped = _mgr->getNextDroppedOutbound()) != NULL) {
    onSendFail(dropped);
    releasePacket(dropped);
  }
}

bool Dispatcher::tryParsePacket(Packet* pkt, const uint8_t* raw, int len) {
  if (pkt == NULL || raw == NULL || len < 2 || len > MAX_TRANS_UNIT) {
    MESH_DEBUG_PRINTLN("%s Dispatcher::checkRecv(): packet length invalid, len=%d", getLogDateTime(), len);
    return false;
  }

  int i = 0;

  pkt->tx_cr = 0;
  pkt->header = raw[i++];
  if (pkt->getPayloadVer() > PAYLOAD_VER_1) {
    MESH_DEBUG_PRINTLN("%s Dispatcher::checkRecv(): unsupported packet version", getLogDateTime());
    return false;
  }

  if (pkt->hasTransportCodes()) {
    if (i + (int)sizeof(pkt->transport_codes) + 1 > len) {
      MESH_DEBUG_PRINTLN("%s Dispatcher::checkRecv(): incomplete transport header, len=%d", getLogDateTime(), len);
      return false;
    }
    memcpy(&pkt->transport_codes[0], &raw[i], 2); i += 2;
    memcpy(&pkt->transport_codes[1], &raw[i], 2); i += 2;
  } else {
    pkt->transport_codes[0] = pkt->transport_codes[1] = 0;
  }

  if (i >= len) {
    MESH_DEBUG_PRINTLN("%s Dispatcher::checkRecv(): missing path header, len=%d", getLogDateTime(), len);
    return false;
  }
  pkt->path_len = raw[i++];
  uint8_t path_mode = pkt->path_len >> 6;  // upper 2 bits (legacy firmware: 00)
  if (path_mode == 3) {   // Reserved for future
    MESH_DEBUG_PRINTLN("%s Dispatcher::checkRecv(): unsupported path mode: 3", getLogDateTime());
    return false;
  }

  uint8_t path_byte_len = (pkt->path_len & 63) * pkt->getPathHashSize();
  if (path_byte_len > MAX_PATH_SIZE || path_byte_len > len - i) {
    MESH_DEBUG_PRINTLN("%s Dispatcher::checkRecv(): partial or corrupt packet received, len=%d", getLogDateTime(), len);
    return false;
  }

  memcpy(pkt->path, &raw[i], path_byte_len); i += path_byte_len;

  pkt->payload_len = len - i;  // payload is remainder
  if (pkt->payload_len > sizeof(pkt->payload)) {
    MESH_DEBUG_PRINTLN("%s Dispatcher::checkRecv(): packet payload too big, payload_len=%d", getLogDateTime(), (uint32_t)pkt->payload_len);
    return false;
  }

  memcpy(pkt->payload, &raw[i], pkt->payload_len);

  return true;  // success
}

void Dispatcher::checkRecv() {
  Packet* pkt;
  float score;
  uint32_t air_time;
  {
    uint8_t raw[MAX_TRANS_UNIT+1];
    int len = _radio->recvRaw(raw, MAX_TRANS_UNIT);
    if (len > 0) {
#ifdef RADIO_LIVENESS_SOFT_ONLY
      last_radio_activity_ms = _ms->getMillis();
#else
      radio_liveness.noteActivity(_ms->getMillis());
#endif
      logRxRaw(_radio->getLastSNR(), _radio->getLastRSSI(), raw, len);

      pkt = _mgr->allocNew();
      if (pkt == NULL) {
        MESH_DEBUG_PRINTLN("%s Dispatcher::checkRecv(): WARNING: received data, no unused packets available!", getLogDateTime());
      } else {
        if (tryParsePacket(pkt, raw, len)) {
          pkt->_snr = _radio->getLastSNR() * 4.0f;
          score = _radio->packetScore(_radio->getLastSNR(), len);
          air_time = _radio->getEstAirtimeFor(len);
          rx_air_time += air_time;
        } else {
          _mgr->free(pkt);  // put back into pool
          pkt = NULL;
        }
      }
    } else {
      pkt = NULL;
    }
  }
  if (pkt) {
    #if MESH_PACKET_LOGGING
    logPacketStart("RX", pkt, pkt->getRawLength());
    Serial.printf(" SNR=%d RSSI=%d score=%d time=%d", (int)pkt->getSNR(),
                  (int)_radio->getLastRSSI(), (int)(score * 1000), air_time);

    static uint8_t packet_hash[MAX_HASH_SIZE];
    pkt->calculatePacketHash(packet_hash);
    Serial.print(" hash=");
    mesh::Utils::printHex(Serial, packet_hash, MAX_HASH_SIZE);
    logPacketEnd(pkt);
    #endif
    logRx(pkt, pkt->getRawLength(), score);   // hook for custom logging

    if (pkt->isRouteFlood()) {
      n_recv_flood++;

      int _delay = calcRxDelayForPacket(pkt, score, air_time);
      if (_delay < 50) {
        MESH_DEBUG_PRINTLN("%s Dispatcher::checkRecv(), score delay below threshold (%d)", getLogDateTime(), _delay);
        processRecvPacket(pkt);   // is below the score delay threshold, so process immediately
      } else {
        MESH_DEBUG_PRINTLN("%s Dispatcher::checkRecv(), score delay is: %d millis", getLogDateTime(), _delay);
        if (_delay > MAX_RX_DELAY_MILLIS) {
          _delay = MAX_RX_DELAY_MILLIS;
        }
        _mgr->queueInbound(pkt, futureMillis(_delay)); // add to delayed inbound queue
      }
    } else {
      n_recv_direct++;
      processRecvPacket(pkt);
    }
  }
}

void Dispatcher::processRecvPacket(Packet* pkt) {
  DispatcherAction action = onRecvPacket(pkt);
  if (action == ACTION_RELEASE) {
    _mgr->free(pkt);
  } else if (action == ACTION_MANUAL_HOLD) {
    // sub-class is wanting to manually hold Packet instance, and call releasePacket() at appropriate time
  } else {   // ACTION_RETRANSMIT*
    uint8_t priority = (action >> 24) - 1;
    uint32_t _delay = action & 0xFFFFFF;

    if (!queueOutboundPacket(pkt, priority, _delay)) {
      onSendFail(pkt);
      releasePacket(pkt);
    } else if (pkt->isRouteDirect() && pkt->getPayloadType() == PAYLOAD_TYPE_TRACE) {
      onTracePacketQueuedForSend(pkt);
    }
  }
}

void Dispatcher::checkSend() {
  const uint32_t now = _ms->getMillis();
  uint32_t next_outbound;
  if (_mgr->getNextOutboundTime(now, next_outbound)) {
    if ((int32_t)(next_outbound - now) > 0) return;
  } else if (_mgr->getOutboundCount(now) == 0) {
    // Compatibility fallback for custom PacketManager implementations that do
    // not provide the optional O(1) deadline query.
    return;
  }
  
  updateTxBudget();
  
  uint32_t est_airtime = _radio->getEstAirtimeFor(MAX_TRANS_UNIT);
  if (tx_budget_ms < est_airtime / MIN_TX_BUDGET_AIRTIME_DIV) {
    float duty_cycle = 1.0f / (1.0f + getAirtimeBudgetFactor());
    unsigned long needed = est_airtime / MIN_TX_BUDGET_AIRTIME_DIV - tx_budget_ms;
    next_tx_time = futureMillis((unsigned long)(needed / duty_cycle));
    return;
  }
  
  if (!millisHasNowPassed(next_tx_time)) return;

  Packet* pending = _mgr->peekNextOutbound(_ms->getMillis());
  bool channel_busy = pending != NULL && usePassiveChannelCheck(pending)
    ? _radio->isReceivingPassive(getRetryInterferenceMargin())
    : _radio->isReceiving();
  if (channel_busy) {
    const uint32_t cad_now = _ms->getMillis();
    const int ready_count = _mgr->getOutboundCount(cad_now);
    if (cad_busy_start == 0) {
      cad_busy_start = cad_now;   // record when CAD busy state started
    }

    const uint32_t max_busy_duration = scaleCADDelayForQueue(
        getCADFailMaxDuration(), ready_count, MIN_CAD_FAIL_MAX_DURATION_MS);
    if (cad_now - cad_busy_start > max_busy_duration) {
      _err_flags |= ERR_EVENT_CAD_TIMEOUT;

      MESH_DEBUG_PRINTLN("%s Dispatcher::checkSend(): CAD busy max duration reached!", getLogDateTime());
      // channel activity has gone on too long... (Radio might be in a bad state)
      // force the pending transmit below...
    } else {
      const uint32_t retry_delay = scaleCADDelayForQueue(
          getCADFailRetryDelay(), ready_count, MIN_CAD_FAIL_RETRY_DELAY_MS);
      next_tx_time = futureMillis(retry_delay);
      return;
    }
  }
  cad_busy_start = 0;  // reset busy state

  outbound = _mgr->getNextOutbound(_ms->getMillis());
  if (outbound) {
    if (!allowPacketTransmit(outbound)) {
      MESH_DEBUG_PRINTLN("%s Dispatcher::checkSend(): packet no longer allowed, type=%u", getLogDateTime(),
                         (uint32_t)outbound->getPayloadType());
      onSendFail(outbound);
      releasePacket(outbound);
      outbound = NULL;
      return;
    }

    int len = 0;
    uint8_t raw[MAX_TRANS_UNIT];

    raw[len++] = outbound->header;
    if (outbound->hasTransportCodes()) {
      memcpy(&raw[len], &outbound->transport_codes[0], 2); len += 2;
      memcpy(&raw[len], &outbound->transport_codes[1], 2); len += 2;
    }
    raw[len++] = outbound->path_len;
    len += Packet::writePath(&raw[len], outbound->path, outbound->path_len);

    if (len + outbound->payload_len > MAX_TRANS_UNIT) {
      MESH_DEBUG_PRINTLN("%s Dispatcher::checkSend(): FATAL: Invalid packet queued... too long, len=%d", getLogDateTime(), len + outbound->payload_len);
      onSendFail(outbound);
      releasePacket(outbound);
      outbound = NULL;
    } else {
      memcpy(&raw[len], outbound->payload, outbound->payload_len); len += outbound->payload_len;

      uint32_t max_airtime = _radio->getEstAirtimeFor(len)*3/2;
      outbound_restore_cr = 0;
      outbound_restore_preamble_len = 0;
      uint8_t default_cr = getDefaultTxCodingRate();
      bool is_retry = outbound->tx_cr >= 4 && outbound->tx_cr <= 8;
      if (outbound->tx_cr >= 4 && outbound->tx_cr <= 8 && default_cr >= 4 && default_cr <= 8
          && outbound->tx_cr != default_cr) {
        if (_radio->setCodingRate(outbound->tx_cr)) {
          outbound_restore_cr = default_cr;
          max_airtime = _radio->getEstAirtimeFor(len)*3/2;
        } else {
          MESH_DEBUG_PRINTLN("%s Dispatcher::checkSend(): WARN: failed to set packet CR%d", getLogDateTime(), (uint32_t)outbound->tx_cr);
        }
      }
      if (is_retry) {
        uint16_t default_preamble_len = _radio->getDefaultPreambleLength();
        if (default_preamble_len != 32 && _radio->setPreambleLength(32)) {
          outbound_restore_preamble_len = default_preamble_len;
          max_airtime = _radio->getEstAirtimeFor(len)*3/2;
        }
      }
      outbound_start = _ms->getMillis();
      bool success = _radio->startSendRaw(raw, len);
      if (!success) {
        MESH_DEBUG_PRINTLN("%s Dispatcher::loop(): ERROR: send start failed!", getLogDateTime());

        restoreOutboundTxOverrides();
        logTxFail(outbound, outbound->getRawLength());
        onSendFail(outbound);
  
        releasePacket(outbound);  // return to pool
        outbound = NULL;
        return;
      }
      outbound_expiry = futureMillis(max_airtime);

    #if MESH_PACKET_LOGGING
      logPacketStart("TX", outbound, len);
      logPacketEnd(outbound);
    #endif
    }
  }
}

Packet* Dispatcher::obtainNewPacket() {
  auto pkt = _mgr->allocNew();  // TODO: zero out all fields
  if (pkt == NULL) {
    _err_flags |= ERR_EVENT_FULL;
  } else {
    pkt->payload_len = pkt->path_len = 0;
    pkt->_snr = 0;
    pkt->tx_cr = 0;
  }
  return pkt;
}

void Dispatcher::releasePacket(Packet* packet) {
  _mgr->free(packet);
}

bool Dispatcher::queueOutboundPacket(Packet* packet, uint8_t priority, uint32_t delay_millis) {
  if (!Packet::isValidPathLen(packet->path_len) || packet->payload_len > MAX_PACKET_PAYLOAD) {
    MESH_DEBUG_PRINTLN("%s Dispatcher::sendPacket(): ERROR: invalid packet... path_len=%d, payload_len=%d", getLogDateTime(), (uint32_t) packet->path_len, (uint32_t) packet->payload_len);
    return false;
  }
  return _mgr->queueOutbound(packet, priority, futureMillis(delay_millis));
}

bool Dispatcher::sendPacket(Packet* packet, uint8_t priority, uint32_t delay_millis) {
  if (!queueOutboundPacket(packet, priority, delay_millis)) {
    onSendFail(packet);
    releasePacket(packet);
    return false;
  }
  if (packet->isRouteDirect() && packet->getPayloadType() == PAYLOAD_TYPE_TRACE) {
    onTracePacketQueuedForSend(packet);
  }
  return true;
}

// Utility function -- handles the case where millis() wraps around back to zero
//   2's complement arithmetic will handle any unsigned subtraction up to HALF the word size (32-bits in this case)
bool Dispatcher::millisHasNowPassed(unsigned long timestamp) const {
  return (long)(_ms->getMillis() - timestamp) > 0;
}

unsigned long Dispatcher::futureMillis(int millis_from_now) const {
  return _ms->getMillis() + millis_from_now;
}

}
