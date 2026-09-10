#pragma once

#include <stdint.h>
#include <math.h>

// PR #2933's spaced median and bounded rise hold, retaining Cascade's
// fractional dBm and 25% previous / 75% new weighting. No radio or clock I/O.
class NoiseFloorEstimator {
public:
  static constexpr uint16_t SAMPLE_COUNT = 64;
  static constexpr uint32_t SAMPLE_INTERVAL_MS = 50;
  static constexpr uint32_t WINDOW_TIMEOUT_MS = 10000;
  static constexpr uint8_t MAX_HELD_BLOCKS = 3;
  static constexpr int32_t MAX_RISE_CENTI_DB = 1500;

private:
  int16_t _samples[SAMPLE_COUNT] = {};
  uint16_t _count = 0;
  uint32_t _last_sample_at = 0;
  uint8_t _held_blocks = 0;

public:
  uint16_t count() const { return _count; }
  bool complete() const { return _count == SAMPLE_COUNT; }
  bool ready(uint32_t now) const {
    return !complete() && (_count == 0 || uint32_t(now - _last_sample_at) >= SAMPLE_INTERVAL_MS);
  }
  uint8_t heldBlocks() const { return _held_blocks; }

  // A timeout discards a partial block. Only completed blocks count toward
  // persistent-rise recovery. Radio/gain resets also clear contamination history.
  void reset(bool clear_history = false) {
    _count = 0;
    if (clear_history) _held_blocks = 0;
  }

  bool add(float rssi, uint32_t now) {
    if (!ready(now)
        || !isfinite(rssi) || rssi < -200.0f || rssi >= 0.0f) return false;
    _samples[_count++] = static_cast<int16_t>(rssi * 100.0f - 0.5f);
    _last_sample_at = now;
    return true;
  }

  // Call once per complete block. A held block leaves the published value intact.
  bool publish(int32_t& floor_centi_dbm, bool previous_valid) {
    if (!complete()) return false;
    for (uint16_t i = 1; i < SAMPLE_COUNT; ++i) {
      const int16_t key = _samples[i];
      uint16_t j = i;
      while (j > 0 && _samples[j - 1] > key) {
        _samples[j] = _samples[j - 1];
        --j;
      }
      _samples[j] = key;
    }
    int32_t median = (int32_t(_samples[SAMPLE_COUNT / 2 - 1])
        + _samples[SAMPLE_COUNT / 2]) / 2;
    if (median < -12000) median = -12000;
    if (previous_valid && median > floor_centi_dbm + MAX_RISE_CENTI_DB) {
      if (++_held_blocks < MAX_HELD_BLOCKS) return false;
    }
    _held_blocks = 0;
    if (previous_valid) {
      const int32_t weighted = floor_centi_dbm + 3 * median;
      floor_centi_dbm = (weighted - 2) / 4;
    } else {
      floor_centi_dbm = median;
    }
    return true;
  }
};
