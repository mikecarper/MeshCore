#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

namespace mesh { namespace lr2021 {

// Retain the first native failure while its rollback restores the old profile.
// A successful rollback must not hide the command which actually failed.
struct ApplyStatus {
  enum class Stage : uint8_t {
    Idle, Admitted, Applied, Interrupt, Transmitting, Busy, Receiving,
    BeaconOwned, CarrierOwned, MorseOwned,
    Standby, FrequencyRail, Frequency, Power, SpreadingFactor, Bandwidth,
    CodingRate, Preamble, SideDetectors, Protocol, Receive, Gain, Trim, Restore, Calibration
  };

  Stage stage = Stage::Idle;
  int16_t code = 0;
  bool failed = false;

  void begin() { stage = Stage::Admitted; code = 0; failed = false; }
  int16_t record(Stage next, int16_t native_code) {
    if (native_code != 0 && !failed) {
      stage = next;
      code = native_code;
      failed = native_code < 0;
    }
    return native_code;
  }
  void applied() { if (!failed) { stage = Stage::Applied; code = 0; } }

  static const char* name(Stage value) {
    switch (value) {
      case Stage::Idle: return "idle";
      case Stage::Admitted: return "admitted";
      case Stage::Applied: return "applied";
      case Stage::Interrupt: return "pending-irq";
      case Stage::Transmitting: return "transmitting";
      case Stage::Busy: return "chip-busy";
      case Stage::Receiving: return "receiving";
      case Stage::BeaconOwned: return "beacon-owned";
      case Stage::CarrierOwned: return "carrier-owned";
      case Stage::MorseOwned: return "morse-owned";
      case Stage::Standby: return "standby";
      case Stage::FrequencyRail: return "frequency-rail";
      case Stage::Frequency: return "frequency";
      case Stage::Power: return "power";
      case Stage::SpreadingFactor: return "sf";
      case Stage::Bandwidth: return "bandwidth";
      case Stage::CodingRate: return "coding-rate";
      case Stage::Preamble: return "preamble";
      case Stage::SideDetectors: return "side-detectors";
      case Stage::Protocol: return "protocol";
      case Stage::Receive: return "receive-start";
      case Stage::Gain: return "rx-gain";
      case Stage::Trim: return "crystal-trim";
      case Stage::Restore: return "restore";
      case Stage::Calibration: return "calibration";
    }
    return "unknown";
  }
};

} } // namespace mesh::lr2021
