#include <helpers/radiolib/AgessamanNoiseFloor.h>

#include <assert.h>
#include <initializer_list>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// Independent reference control flow from:
// https://github.com/agessaman/MeshCore/blob/d664d89df66bdacc1319900a2e2d152fa31b9dc9/src/helpers/radiolib/RadioLibWrappers.cpp
// triggerNoiseFloorCalibrate() and loop(), with debug printing omitted.
// MeshCore is distributed under the MIT License (see README.md).
// Keep the upstream names and branches so the production helper is compared
// against its source, rather than a second implementation using that helper.
struct PinnedSource {
  enum { STATE_IDLE = 0, STATE_RX = 1, STATE_TX_WAIT = 3, STATE_INT_READY = 16 };
  enum { NUM_NOISE_FLOOR_SAMPLES = 64, SAMPLING_THRESHOLD = 14 };

  uint8_t state = STATE_RX;
  unsigned _num_floor_samples = 0;
  int32_t _floor_sample_sum = 0;
  int16_t _noise_floor = 0;
  int _threshold = 0;
  bool receiving = false;
  float input = -100;
  unsigned packet_checks = 0, rssi_reads = 0;

  bool isReceivingPacket() { ++packet_checks; return receiving; }
  float getCurrentRSSI() { ++rssi_reads; return input; }

  void triggerNoiseFloorCalibrate(int threshold) {
    _threshold = threshold;
    if (_num_floor_samples >= NUM_NOISE_FLOOR_SAMPLES) {
      _num_floor_samples = 0;
      _floor_sample_sum = 0;
    }
  }

  void loop() {
    if (state == STATE_RX && _num_floor_samples < NUM_NOISE_FLOOR_SAMPLES) {
      if (!isReceivingPacket()) {
        int rssi = getCurrentRSSI();
        if (rssi < _noise_floor + SAMPLING_THRESHOLD) {
          _num_floor_samples++;
          _floor_sample_sum += rssi;
        }
      }
    } else if (_num_floor_samples >= NUM_NOISE_FLOOR_SAMPLES && _floor_sample_sum != 0) {
      _noise_floor = _floor_sample_sum / NUM_NOISE_FLOOR_SAMPLES;
      if (_noise_floor < -120) {
        _noise_floor = -120;
      }
      _floor_sample_sum = 0;
    }
  }
};

struct Pair {
  AgessamanNoiseFloor production;
  PinnedSource source;
  int16_t floor = 0;
  unsigned packet_checks = 0, rssi_reads = 0;

  void compare() const {
    assert(production.count() == source._num_floor_samples);
    assert(production.complete() == (source._num_floor_samples >= 64));
    assert(production.pendingPublication() ==
           (source._num_floor_samples >= 64 && source._floor_sample_sum != 0));
    assert(floor == source._noise_floor);
    assert(packet_checks == source.packet_checks);
    assert(rssi_reads == source.rssi_reads);
  }

  bool step(float input = -100, uint8_t state = PinnedSource::STATE_RX,
            bool receiving = false) {
    source.input = input;
    source.state = state;
    source.receiving = receiving;
    const bool expected_publication = source._num_floor_samples >= 64
                                   && source._floor_sample_sum != 0;
    source.loop();
    const bool published = production.loop(state == PinnedSource::STATE_RX,
        [&]() { ++packet_checks; return receiving; },
        [&]() { ++rssi_reads; return input; }, floor);
    assert(published == expected_publication);
    compare();
    return published;
  }

  void fill(float input = -100) {
    for (unsigned i = 0; i < 64; ++i) assert(!step(input));
  }

  void trigger(int threshold = 7) {
    source.triggerNoiseFloorCalibrate(threshold);
    production.trigger();
    compare();
  }

  // AGC/RF reset integration owns the published floor. The pure collector
  // mirrors the source's count/sum reset without silently changing that floor.
  void reset(bool clear_floor = false) {
    source._num_floor_samples = 0;
    source._floor_sample_sum = 0;
    production.reset();
    if (clear_floor) floor = source._noise_floor = 0;
    compare();
  }
};

static void cadence() {
  Pair p;
  // There is deliberately no clock adapter or delay: 64 consecutive calls
  // consume 64 samples, and the 65th call publishes without another RSSI read.
  p.fill();
  assert(p.production.complete() && p.production.pendingPublication());
  assert(p.floor == 0 && p.rssi_reads == 64 && p.packet_checks == 64);
  assert(p.step());
  assert(p.floor == -100 && p.rssi_reads == 64 && p.packet_checks == 64);
  for (unsigned i = 0; i < 10000; ++i) assert(!p.step(-80));
  assert(p.floor == -100 && p.rssi_reads == 64);
}

static void guards() {
  Pair p;
  for (uint8_t state : {uint8_t(PinnedSource::STATE_IDLE),
                       uint8_t(PinnedSource::STATE_TX_WAIT),
                       uint8_t(PinnedSource::STATE_RX | PinnedSource::STATE_INT_READY)}) {
    assert(!p.step(-100, state));
    assert(p.packet_checks == 0 && p.rssi_reads == 0);
  }
  for (unsigned i = 0; i < 10000; ++i) assert(!p.step(-100, PinnedSource::STATE_RX, true));
  assert(p.production.count() == 0 && p.packet_checks == 10000 && p.rssi_reads == 0);
  assert(!p.step());
  assert(p.production.count() == 1 && p.packet_checks == 10001 && p.rssi_reads == 1);
}

static void arithmetic() {
  {
    Pair p;
    for (unsigned i = 0; i < 64; ++i) assert(!p.step(i < 32 ? -101.9f : -100.9f));
    assert(p.step() && p.floor == -100); // per-sample and mean truncation toward zero
    p.trigger();
    assert(!p.step(-86));
    assert(!p.step(-86.9f)); // truncates to the excluded -86 boundary
    assert(!p.step(-85));
    assert(p.production.count() == 0);
    for (unsigned i = 0; i < 64; ++i) assert(!p.step(-87));
    assert(p.step() && p.floor == -87);
  }
  {
    Pair p;
    for (unsigned i = 0; i < 64; ++i) assert(!p.step(i < 16 ? -104 : -96));
    assert(p.step() && p.floor == -98); // integer mean, no percentile or blending
    p.trigger(); p.fill(-100);
    assert(p.step() && p.floor == -100);
  }
  {
    Pair p;
    p.fill(-127);
    assert(p.step() && p.floor == -120);
    p.trigger();
    for (unsigned i = 0; i < 10000; ++i) assert(!p.step(-105));
    assert(p.production.count() == 0 && p.floor == -120);
    // No timeout or retry count clears the gate. A caller reset does.
    p.reset(true); p.fill(-105);
    assert(p.step() && p.floor == -105);
  }
  {
    Pair p;
    p.fill(-201); // finite readings have no additional range filter in source
    assert(p.step() && p.floor == -120);
  }
}

static void triggers() {
  Pair p;
  for (unsigned i = 0; i < 32; ++i) {
    assert(!p.step(-100)); p.trigger(int(i));
    assert(p.production.count() == i + 1 && p.source._threshold == int(i));
  }
  for (unsigned i = 0; i < 32; ++i) assert(!p.step(-80));
  assert(p.production.pendingPublication());
  p.trigger(42); // a completed but unpublished block is discarded in source
  assert(p.production.count() == 0 && p.floor == 0 && p.source._threshold == 42);
  p.fill(-90); assert(p.step() && p.floor == -90);
  p.trigger(9);
  assert(p.production.count() == 0 && p.floor == -90);
  p.fill(-100); assert(p.step() && p.floor == -100);
}

static void reset() {
  Pair p;
  p.fill(-110); assert(p.step() && p.floor == -110);
  p.trigger();
  for (unsigned i = 0; i < 20; ++i) assert(!p.step(-100));
  p.reset();
  assert(p.production.count() == 0 && p.floor == -110);
  p.fill(-105); assert(p.step() && p.floor == -105);
  p.reset(true); p.fill(-70);
  assert(p.step() && p.floor == -70);
}

static void publication() {
  {
    Pair p;
    p.fill(0);
    assert(p.production.complete() && !p.production.pendingPublication());
    for (unsigned i = 0; i < 100; ++i) assert(!p.step(-100));
    assert(p.floor == 0 && p.rssi_reads == 64);
    p.trigger(); p.fill(-100);
    assert(p.step(-20, PinnedSource::STATE_IDLE, true) && p.floor == -100);
    assert(p.packet_checks == 128 && p.rssi_reads == 128);
  }
  for (uint8_t state : {uint8_t(PinnedSource::STATE_TX_WAIT),
                       uint8_t(PinnedSource::STATE_RX | PinnedSource::STATE_INT_READY)}) {
    Pair p; p.fill(-100);
    assert(p.step(-20, state, true) && p.floor == -100);
    assert(p.packet_checks == 64 && p.rssi_reads == 64);
  }
}

static uint32_t next(uint32_t& seed) {
  seed = seed * 1664525U + 1013904223U;
  return seed;
}

static void traces() {
  for (uint32_t initial : {1U, 0x12345678U, 0xffffffffU}) {
    Pair p;
    uint32_t seed = initial;
    for (unsigned i = 0; i < 50000; ++i) {
      const uint32_t event = next(seed);
      if ((event & 255U) < 3) p.trigger(int(event >> 24));
      else if ((event & 255U) == 3) p.reset();
      else if ((event & 255U) == 4) p.reset(true);
      const uint8_t state = (event & 0x700U) == 0 ? PinnedSource::STATE_IDLE
          : (event & 0x700U) == 0x100U ? PinnedSource::STATE_TX_WAIT
          : (event & 0x700U) == 0x200U ? PinnedSource::STATE_RX | PinnedSource::STATE_INT_READY
          : PinnedSource::STATE_RX;
      const float input = -float(40U + ((event >> 16) % 110U))
                        - float((event >> 12) & 3U) * 0.25f;
      p.step(input, state, (event & 0x800U) != 0);
    }
  }
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const char* scenario = argv[1];
  if (!strcmp(scenario, "cadence")) cadence();
  else if (!strcmp(scenario, "guards")) guards();
  else if (!strcmp(scenario, "arithmetic")) arithmetic();
  else if (!strcmp(scenario, "triggers")) triggers();
  else if (!strcmp(scenario, "reset")) reset();
  else if (!strcmp(scenario, "publication")) publication();
  else if (!strcmp(scenario, "traces")) traces();
  else assert(false);
  printf("PASS: %s\n", scenario);
}
