#pragma once

#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <string.h>

// This FT5x06-compatible controller exposes valid point data at register 0x02,
// but some units return zero for all optional identity registers. Probe the
// point register instead of rejecting those units by vendor ID.
class IndicatorTouch : public lgfx::Touch_FT5x06 {
  static constexpr size_t POINT_DATA_SIZE = 5;

  bool readPointData(uint8_t* data, size_t size) {
    const uint8_t point_count_reg = 0x02;
    return lgfx::i2c::transactionWriteRead(
               _cfg.i2c_port, _cfg.i2c_addr, &point_count_reg, 1,
               data, size, _cfg.freq)
        .has_value();
  }

  bool readStablePointData(uint8_t* data) {
    uint8_t samples[2][POINT_DATA_SIZE] = {};
    if (!readPointData(samples[0], sizeof(samples[0]))) return false;
    if ((samples[0][0] & 0x0F) == 0) {
      memcpy(data, samples[0], POINT_DATA_SIZE);
      return true;
    }

    // Coordinates may change while the controller is being read. Match two
    // consecutive snapshots, as the standard driver does, so X/Y bytes from
    // different positions cannot reverse a gesture. If the finger keeps
    // moving through every retry, use the newest complete snapshot.
    uint8_t current = 0;
    for (uint8_t attempt = 0; attempt < 5; ++attempt) {
      const uint8_t next = current ^ 1U;
      if (!readPointData(samples[next], sizeof(samples[next]))) return false;
      if (memcmp(samples[current], samples[next], POINT_DATA_SIZE) == 0) {
        memcpy(data, samples[next], POINT_DATA_SIZE);
        return true;
      }
      current = next;
    }
    memcpy(data, samples[current], POINT_DATA_SIZE);
    return true;
  }

public:
  bool init() override {
    _inited = false;
    if (!lgfx::i2c::init(_cfg.i2c_port, _cfg.pin_sda, _cfg.pin_scl)
             .has_value()) {
      return false;
    }

    uint8_t point_count = 0;
    if (!readPointData(&point_count, 1)) {
      return false;
    }

    // Select normal operation and polling mode, matching the generic driver.
    if (!lgfx::i2c::writeRegister8(
             _cfg.i2c_port, _cfg.i2c_addr, 0x00, 0x00, 0, _cfg.freq)
             .has_value()
        || !lgfx::i2c::writeRegister8(
                _cfg.i2c_port, _cfg.i2c_addr, 0xA4, 0x00, 0, _cfg.freq)
                .has_value()) {
      return false;
    }

    _inited = true;
    return true;
  }

  uint_fast8_t getTouchRaw(lgfx::touch_point_t* point,
                           uint_fast8_t count) override {
    if (point == nullptr || count == 0 || (!_inited && !init())) return 0;

    uint8_t data[POINT_DATA_SIZE] = {};
    if (!readStablePointData(data)) {
      _inited = false;
      return 0;
    }
    if ((data[0] & 0x0F) == 0) return 0;

    point[0].x = ((data[1] & 0x0F) << 8) | data[2];
    point[0].y = ((data[3] & 0x0F) << 8) | data[4];
    point[0].size = 1;
    point[0].id = data[3] >> 4;
    return 1;
  }
};
