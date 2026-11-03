#pragma once

#include <Arduino.h>
#include <Wire.h>

namespace mesh { namespace tracker { namespace motion {

// Uses the board's already-initialized Wire bus. It never changes GPS or
// sensor-rail power, and never assumes a motion interrupt pin is connected.
struct WireBus {
  bool read(uint8_t address, uint8_t reg, uint8_t& value) {
    Wire.beginTransmission(address);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0 || Wire.requestFrom(address, uint8_t(1)) != 1) return false;
    value = static_cast<uint8_t>(Wire.read());
    return true;
  }
  bool write(uint8_t address, uint8_t reg, uint8_t value) {
    Wire.beginTransmission(address);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == 0;
  }
};

}}} // namespace mesh::tracker::motion
