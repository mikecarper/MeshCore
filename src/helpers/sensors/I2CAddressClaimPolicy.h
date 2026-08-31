#pragma once

#include <stdint.h>

namespace mesh {

enum class I2cIdentityProbeResult : uint8_t {
  NoResponse,
  Match,
  NotMatch,
  Inconclusive,
};

enum class I2cRegisterProbeStatus : uint8_t {
  NoResponse,
  Success,
  Inconclusive,
};

inline bool isValidI2cPeripheralAddress(uint32_t address) {
  // 0x00..0x07 and 0x78..0x7f are reserved in the 7-bit address space.
  return address >= 0x08 && address <= 0x77;
}

inline bool isValidI2cPinPair(int32_t sda, int32_t scl,
                              uint32_t pin_count) {
  return sda >= 0 && scl >= 0
      && static_cast<uint32_t>(sda) < pin_count
      && static_cast<uint32_t>(scl) < pin_count
      && sda != scl;
}

inline I2cIdentityProbeResult classifyIna3221Identity(
    I2cRegisterProbeStatus first_status, uint16_t first_value,
    I2cRegisterProbeStatus second_status, uint16_t second_value,
    uint16_t expected_manufacturer, uint16_t expected_die) {
  if (first_status == I2cRegisterProbeStatus::NoResponse) {
    return I2cIdentityProbeResult::NoResponse;
  }
  if (first_status != I2cRegisterProbeStatus::Success
      || second_status != I2cRegisterProbeStatus::Success) {
    return I2cIdentityProbeResult::Inconclusive;
  }
  if (first_value == expected_manufacturer) {
    return second_value == expected_die
        ? I2cIdentityProbeResult::Match
        : I2cIdentityProbeResult::Inconclusive;
  }
  return second_value != expected_manufacturer
      ? I2cIdentityProbeResult::NotMatch
      : I2cIdentityProbeResult::Inconclusive;
}

// Once a device-specific probe has positively identified the I2C GPS, do not
// hand the same address to a table-driven sensor initializer. An ACK scan can
// prove only that something answered; it cannot distinguish two devices wired
// to the same address or make that electrical configuration safe.
inline bool shouldSkipSensorAtClaimedGpsAddress(bool gps_claimed,
                                                bool same_bus,
                                                uint8_t sensor_address,
                                                uint8_t gps_address) {
  return gps_claimed && same_bus && sensor_address == gps_address;
}

// A universal RAK image may support either an INA3221 or an I2C u-blox GPS at
// the GPS address. Check the actual device there first, independent of where
// the telemetry table expected an INA3221. An incomplete identity transaction
// is not proof that the device is safe to receive u-blox traffic.
inline bool shouldProbeI2cGps(I2cIdentityProbeResult identity) {
  // Absence/NACK can be transient while an INA powers up. Only a stable,
  // definitive non-INA result permits the device-specific u-blox exchange.
  return identity == I2cIdentityProbeResult::NotMatch;
}

}  // namespace mesh
