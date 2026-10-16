#include "Dispatcher.h"

#if MESH_ENABLE_TX_BEACONS
#include <new>

namespace mesh {

struct Dispatcher::BeaconTransmit {
  enum Phase : uint8_t { Pending, Sending, Restoring } phase = Pending;
  RadioProfileParams radio;
  uint8_t bytes[MAX_TRANS_UNIT];
  uint16_t length = 0;
  int8_t power = 0;
  uint32_t retry_at = 0, prepare_expiry = 0, start = 0, expiry = 0;
};

Dispatcher::~Dispatcher() { delete beacon_tx; }

bool Dispatcher::queueBeaconRadioFrame(const uint8_t* bytes, size_t length,
    const RadioProfileParams& params, int8_t power) {
  if (!bytes || !length || length > MAX_TRANS_UNIT || beacon_tx || outbound
      || !radio_available || _radio->isCarrierWaveActive()
      || _radio->isBeaconRadioOwned()
      || _radio->isMorseTxActive() || _radio->isMorseRxActive()
      || !RadioProfiles::valid(params) || !_radio->validateProfile(params)) return false;
#if MESH_ENABLE_FOREIGN_RADIO
  if (hasForeignRadioFrame()) return false;
#endif
  const uint32_t now = _ms->getMillis();
  // Admit a due slot before checking the actual RF-start guard below. CAD
  // and tuning made the preceding start slightly later than its scheduled
  // hour; rejecting here would silently drop the next hourly slot.
  auto* frame = new (std::nothrow) BeaconTransmit;
  if (!frame) return false;
  frame->radio = params;
  frame->power = power;
  frame->length = uint16_t(length);
  memcpy(frame->bytes, bytes, length);
  frame->retry_at = now;
  frame->prepare_expiry = now + 30000UL;
  beacon_tx = frame;
  return true;
}

void Dispatcher::accountBeaconAirtime() {
  const uint32_t elapsed = uint32_t(_ms->getMillis()) - beacon_tx->start;
  total_air_time += elapsed;
  updateTxBudget();
  tx_budget_ms = elapsed >= tx_budget_ms ? 0 : tx_budget_ms - elapsed;
  if (tx_budget_ms < 100) {
    const float duty = 1.0f / (1.0f + getAirtimeBudgetFactor());
    next_tx_time = futureMillis((unsigned long)((100 - tx_budget_ms) / duty));
  } else next_tx_time = _ms->getMillis();
}

void Dispatcher::cancelBeaconRadioFrame() {
  if (!beacon_tx) return;
  if (!radio_available) {
    // Suspension never performs SPI against an unavailable peripheral.
    if (!_radio->isBeaconRadioOwned()) { delete beacon_tx; beacon_tx = nullptr; }
    return;
  }
  if (beacon_tx->phase == BeaconTransmit::Sending) {
    _radio->onSendFinished();
    accountBeaconAirtime();
  }
  beacon_tx->phase = BeaconTransmit::Restoring;
  serviceBeaconRadioFrame();
}

bool Dispatcher::serviceBeaconRadioFrame() {
  if (!beacon_tx || beacon_tx->phase == BeaconTransmit::Pending) return false;
  if (beacon_tx->phase == BeaconTransmit::Sending) {
    const bool complete = _radio->isSendComplete();
    if (!complete && int32_t(uint32_t(_ms->getMillis()) - beacon_tx->expiry) < 0) return true;
    _radio->onSendFinished();
    accountBeaconAirtime();
    beacon_tx->phase = BeaconTransmit::Restoring;
    if (!complete) {
      _err_flags |= ERR_EVENT_RADIO_WATCHDOG;
      _radio->recoverRadio(true);
    }
  }
  // Never let normal TX, AGC, or RX metadata processing use the beacon tuple.
  if (_radio->restoreBeaconReceive() != RadioParamApplyResult::APPLIED) return true;
  delete beacon_tx;
  beacon_tx = nullptr;
  radio_nonrx_start = _ms->getMillis();
  return false;
}

bool Dispatcher::checkBeaconRadioSend() {
  if (!beacon_tx || outbound || beacon_tx->phase != BeaconTransmit::Pending) return false;
  const uint32_t now = _ms->getMillis();
  if (int32_t(now - beacon_tx->prepare_expiry) >= 0) {
    cancelBeaconRadioFrame();
    return beacon_tx != nullptr;
  }
  if (int32_t(now - beacon_tx->retry_at) < 0) return false;
  // Beacons are background work, never ahead of due MeshCore traffic.
  if (_mgr->getOutboundCount(now) || !restoreOutboundTxOverrides()
      || _radio->isCarrierWaveActive() || _radio->isMorseTxActive()
      || _radio->isMorseRxActive() || _radio->isCalibratingNoiseFloor()) {
    beacon_tx->retry_at = now + 101;
    return false;
  }
  updateTxBudget();
  const uint32_t airtime = _radio->getBeaconAirtime(beacon_tx->radio, beacon_tx->length);
  if (!airtime || airtime > UINT32_MAX / 1000) {
    cancelBeaconRadioFrame();
    return beacon_tx != nullptr;
  }
  if (tx_budget_ms < airtime / 2 || !millisHasNowPassed(next_tx_time)
      || (beacon_has_started && uint32_t(now - beacon_last_start) < 3600000UL)) {
    beacon_tx->retry_at = now + 101;
    return false;
  }
  const auto prepared = _radio->prepareBeaconTransmit(beacon_tx->radio, beacon_tx->power);
  if (prepared == RadioParamApplyResult::BUSY) {
    if (_radio->isBeaconRadioOwned()) {
      cancelBeaconRadioFrame();
      return beacon_tx != nullptr;
    }
    // Coprime retry avoids repeatedly probing one RXPS sleep phase.
    beacon_tx->retry_at = now + 11;
    return false;
  }
  if (prepared != RadioParamApplyResult::APPLIED || _radio->isBeaconChannelBusy()) {
    cancelBeaconRadioFrame();
    return beacon_tx != nullptr;
  }
  // Count attempts, not delivery: no automatic retry after an RF start.
  beacon_has_started = true;
  beacon_last_start = beacon_tx->start = _ms->getMillis();
  if (!_radio->startSendRaw(beacon_tx->bytes, beacon_tx->length)) {
    cancelBeaconRadioFrame();
    return beacon_tx != nullptr;
  }
  beacon_tx->phase = BeaconTransmit::Sending;
  beacon_tx->expiry = beacon_tx->start + airtime * 3 / 2 + 1000;
  return true;
}

} // namespace mesh
#endif
