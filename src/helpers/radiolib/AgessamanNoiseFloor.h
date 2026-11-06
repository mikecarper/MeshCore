#pragma once

#include <stdint.h>

// Literal noise-floor collection and publication behavior from MeshCore:
// https://github.com/agessaman/MeshCore/blob/d664d89df66bdacc1319900a2e2d152fa31b9dc9/src/helpers/radiolib/RadioLibWrappers.cpp#L90-L109
// MeshCore is distributed under the MIT License (see README.md).
// Keep the published floor outside this helper, as in the source wrapper.
class AgessamanNoiseFloor {
public:
  static constexpr uint16_t SAMPLE_COUNT = 64;
  static constexpr int16_t SAMPLING_THRESHOLD_DB = 14;

private:
  uint16_t _num_floor_samples = 0;
  int32_t _floor_sample_sum = 0;

public:
  uint16_t count() const { return _num_floor_samples; }
  bool complete() const { return _num_floor_samples >= SAMPLE_COUNT; }
  bool pendingPublication() const {
    return complete() && _floor_sample_sum != 0;
  }

  void reset() {
    _num_floor_samples = 0;
    _floor_sample_sum = 0;
  }

  void trigger() {
    if (_num_floor_samples >= SAMPLE_COUNT) reset();
  }

  template <class PacketCheck, class RssiReader>
  bool loop(bool rx, PacketCheck is_receiving, RssiReader read_rssi,
            int16_t& floor) {
    if (rx && _num_floor_samples < SAMPLE_COUNT) {
      if (!is_receiving()) {
        int rssi = read_rssi();
        if (rssi < floor + SAMPLING_THRESHOLD_DB) {
          _num_floor_samples++;
          _floor_sample_sum += rssi;
        }
      }
    } else if (_num_floor_samples >= SAMPLE_COUNT && _floor_sample_sum != 0) {
      floor = _floor_sample_sum / SAMPLE_COUNT;
      if (floor < -120) floor = -120;
      _floor_sample_sum = 0;
      return true;
    }
    return false;
  }
};
