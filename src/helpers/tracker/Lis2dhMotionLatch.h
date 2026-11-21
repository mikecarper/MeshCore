#pragma once

#include <stdint.h>

namespace mesh { namespace tracker { namespace motion {

// Reference: ST LIS2DH12 datasheet and application note AN5005, section
// "Inertial wake-up / Using the HP filter":
// https://www.st.com/resource/en/application_note/dm00365457-lis2dh12-mems-digital-output-motion-sensor-ultralowpower-highperformance-3axis-nano-accelerometer-stmicroelectronics.pdf
//
// This is an opt-in, exclusive-owner driver. It changes the accelerometer's
// output and interrupt settings but neither controls its power rail nor uses
// an MCU interrupt pin. The device and its internal latch behavior must be
// verified on the target hardware before relying on it for tracking.

enum class Observation : uint8_t {
  Unknown,          // No trustworthy observation (missing/reset/error/warmup).
  NoMotionObserved, // No event since the previous successful latch read.
  Motion            // One or more qualifying events since the previous read.
};

// Bus must provide:
//   bool read(uint8_t address, uint8_t reg, uint8_t& value)
//   bool write(uint8_t address, uint8_t reg, uint8_t value)
// Both methods return true only when the complete I2C transaction succeeds.
// Call begin/poll/stop from a single owner; no bus or GPIO interrupt is held.
template <typename Bus>
class Lis2dhMotionLatch {
 public:
  // At +/-2 g INT1_THS is 16 mg per step. The default is about 256 mg.
  // Threshold and sample rate require tuning on the actual collar and dog.
  static constexpr uint8_t kDefaultThresholdSteps = 16;
  static constexpr uint32_t kWarmupMs = 200;  // Two 10 Hz sample periods.

  bool begin(Bus& bus, uint32_t now_ms,
             uint8_t threshold_steps = kDefaultThresholdSteps) {
    if (ready_) return true; // Do not discard a latched event on repeated begin.
    if (!threshold_steps || threshold_steps > 127) return false;
    if (owned_ && !stop(bus)) return false;
    uint8_t value = 0;
    uint8_t address = 0;
    // Most reported Tags use 0x19, but either legal SA0 strap is supported.
    if (bus.read(0x19, kWhoAmI, value) && value == kExpectedId) address = 0x19;
    else if (bus.read(0x18, kWhoAmI, value) && value == kExpectedId) address = 0x18;
    else return false;

    address_ = address;
    owned_ = true;
    threshold_ = threshold_steps;
    // Route no signal to the unknown external INT1 wiring. INT1_SRC is the
    // internal latched source and is polled on existing MCU wakes instead.
    // CTRL1: 10 Hz, low-power, X/Y/Z enabled. CTRL2: high-pass AOI1.
    // CTRL4: +/-2 g. CTRL5: latch AOI1. CFG: X/Y/Z high events ORed.
    if (!bus.write(address_, kInt1Cfg, 0x00)
        || !bus.write(address_, kCtrl1, kCtrl1Value)
        || !bus.write(address_, kCtrl2, kCtrl2Value)
        || !bus.write(address_, kCtrl3, kCtrl3Value)
        || !bus.write(address_, kCtrl4, kCtrl4Value)
        || !bus.write(address_, kCtrl5, kCtrl5Value)
        || !bus.write(address_, kInt1Threshold, threshold_)
        || !bus.write(address_, kInt1Duration, kInt1DurationValue)
        || !bus.read(address_, kReference, value)
        || !bus.read(address_, kInt1Source, value)
        || !bus.write(address_, kInt1Cfg, kInt1CfgValue)) {
      // Only an identified device is touched. If the bus failed, this best
      // effort may also fail; never interpret that outcome as no movement.
      (void)stop(bus);
      return false;
    }
    started_at_ = now_ms;
    ready_ = true;
    return true;
  }

  Observation poll(Bus& bus, uint32_t now_ms) {
    if (!ready_ || static_cast<uint32_t>(now_ms - started_at_) < kWarmupMs)
      return Observation::Unknown;

    // A GPS/shared-rail power cycle resets these registers. Recheck before
    // reading the latch so such a reset cannot look like a quiet animal.
    if (!matches(bus, kWhoAmI, kExpectedId)
        || !matches(bus, kCtrl1, kCtrl1Value)
        || !matches(bus, kCtrl2, kCtrl2Value)
        || !matches(bus, kCtrl3, kCtrl3Value)
        || !matches(bus, kCtrl4, kCtrl4Value)
        || !matches(bus, kCtrl5, kCtrl5Value)
        || !matches(bus, kInt1Cfg, kInt1CfgValue)
        || !matches(bus, kInt1Threshold, threshold_)
        || !matches(bus, kInt1Duration, kInt1DurationValue)) {
      ready_ = false;
      return Observation::Unknown;
    }
    uint8_t source = 0;
    if (!bus.read(address_, kInt1Source, source)) {
      ready_ = false;
      return Observation::Unknown;
    }
    // IA is the interrupt-active bit. Reading INT1_SRC acknowledges the
    // latched event and establishes the next observation window.
    return (source & 0x40) ? Observation::Motion : Observation::NoMotionObserved;
  }

  bool stop(Bus& bus) {
    if (!owned_) return true;
    ready_ = false;
    const bool interrupts_off = bus.write(address_, kInt1Cfg, 0x00);
    const bool power_down = bus.write(address_, kCtrl1, 0x00);
    if (!interrupts_off || !power_down) return false;
    owned_ = false;
    address_ = 0;
    return true;
  }

  bool isReady() const { return ready_; }
  uint8_t address() const { return ready_ ? address_ : 0; }

 private:
  static constexpr uint8_t kWhoAmI = 0x0f, kExpectedId = 0x33;
  static constexpr uint8_t kCtrl1 = 0x20, kCtrl2 = 0x21, kCtrl3 = 0x22;
  static constexpr uint8_t kCtrl4 = 0x23, kCtrl5 = 0x24, kReference = 0x26;
  static constexpr uint8_t kInt1Cfg = 0x30, kInt1Source = 0x31;
  static constexpr uint8_t kInt1Threshold = 0x32, kInt1Duration = 0x33;
  static constexpr uint8_t kCtrl1Value = 0x2f, kCtrl2Value = 0x09;
  static constexpr uint8_t kCtrl3Value = 0x00, kCtrl4Value = 0x00;
  static constexpr uint8_t kCtrl5Value = 0x08, kInt1CfgValue = 0x2a;
  static constexpr uint8_t kInt1DurationValue = 0x00;

  bool matches(Bus& bus, uint8_t reg, uint8_t expected) const {
    uint8_t actual = 0;
    return bus.read(address_, reg, actual) && actual == expected;
  }

  uint8_t address_ = 0;
  uint8_t threshold_ = 0;
  uint32_t started_at_ = 0;
  bool ready_ = false;
  bool owned_ = false;
};

}}} // namespace mesh::tracker::motion
