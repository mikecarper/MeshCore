#pragma once
#include <stdint.h>

namespace mesh {

// Timing observations only: never change radio admission or the scan policy.
class RadioProfileScanHealth {
 public:
  enum Hold : uint8_t { Receive = 1, Transmit = 2, Other = 4 };
  struct Profile {
    uint32_t idle_gap_us = 0, busy_gap_us = 0;
    uint32_t overdue_us = 0, late_visits = 0;
    uint32_t rx_holds = 0, tx_holds = 0;
  };
  Profile profile[2];
  uint32_t service_gap_us = 0;

  // Preserve fractional RTC2 ticks when normalizing the nRF52 scan clock.
  uint32_t timestamp(uint32_t raw, uint32_t numerator = 1, uint16_t denominator = 1) {
    if (_clock_seen) {
      const uint64_t scaled = uint64_t(uint32_t(raw - _clock_raw)) * numerator + _fraction;
      _clock_us += uint32_t(scaled / denominator);
      _fraction = uint16_t(scaled % denominator);
    }
    _clock_raw = raw;
    _clock_seen = true;
    return _clock_us;
  }
  void pause() {
    _tracking = _clock_seen = false;
    _seen = _busy[0] = _busy[1] = 0;
    _clock_us = _fraction = 0;
  }
  void service(uint32_t now, uint8_t current) {
    if (current > 1) return;
    if (_tracking) maximum(service_gap_us, now - _last_service);
    else {
      _tracking = true;
      visit(current, now);
    }
    _last_service = now;
  }
  void hold(uint8_t reason) {
    if (_tracking) { _busy[0] |= reason; _busy[1] |= reason; }
  }
  void visit(uint8_t current, uint32_t now) {
    if (!_tracking || current > 1) return;
    const uint8_t bit = uint8_t(1U << current);
    if (_seen & bit) {
      const uint32_t gap = now - _last_visit[current];
      auto& p = profile[current];
      maximum(_busy[current] ? p.busy_gap_us : p.idle_gap_us, gap);
      if (_busy[current] & Receive) increment(p.rx_holds);
      if (_busy[current] & Transmit) increment(p.tx_holds);
    }
    _seen |= bit;
    _busy[current] = 0;
    _last_visit[current] = now;
  }
  void finishVisit(uint8_t current, uint32_t elapsed_us, uint32_t requested_us) {
    if (!_tracking || current > 1 || !requested_us || elapsed_us <= requested_us
        || _busy[current]) return;
    auto& p = profile[current];
    maximum(p.overdue_us, elapsed_us - requested_us);
    increment(p.late_visits);
  }

 private:
  uint32_t _last_visit[2] = {}, _last_service = 0;
  uint32_t _clock_raw = 0, _clock_us = 0;
  uint16_t _fraction = 0;
  uint8_t _seen = 0, _busy[2] = {};
  bool _tracking = false, _clock_seen = false;
  static void maximum(uint32_t& value, uint32_t sample) { if (sample > value) value = sample; }
  static void increment(uint32_t& value) { if (value != UINT32_MAX) ++value; }
};

}  // namespace mesh
