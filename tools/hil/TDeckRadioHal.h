#pragma once

#include <helpers/radiolib/ESP32BufferedRadioHal.h>
#include "TDeckSpiClockTiming.h"

#if defined(CONFIG_IDF_TARGET_ESP32S3) && CONFIG_IDF_TARGET_ESP32S3
#include <esp_arduino_version.h>
#if ESP_ARDUINO_VERSION == ESP_ARDUINO_VERSION_VAL(2, 0, 17)
#include <soc/spi_struct.h>
#define MESH_TDECK_CLOCK_TIMING_SUPPORTED 1
#endif
#endif

// Experimental HIL-only divider comparison; hardware tests rejected this
// correction for production because it increased SPI read errors.
// The T-Deck radio and display both use SPIClass(HSPI), which is GPSPI3 on
// ESP32-S3. This helper must not be used with an FSPI radio bus. Correction is
// opt-in and limited to the pinned Arduino core whose divider was inspected.
class TDeckRadioHal : public ESP32BufferedRadioHal {
  bool _correct_clock_timing = false;
  bool _transaction_open = false;
  bool _restore_clock = false;
  uint32_t _original_clock = 0;
  uint32_t _corrected_clock = 0;

public:
  explicit TDeckRadioHal(SPIClass& bus, uint32_t hz = MC_SX1262_SPI_HZ)
      : ESP32BufferedRadioHal(bus, hz) {}

  static constexpr bool supportsClockTimingCorrection() {
#ifdef MESH_TDECK_CLOCK_TIMING_SUPPORTED
    return true;
#else
    return false;
#endif
  }

  // Change policy only between transactions. Return false if enabling is
  // unsupported or a transaction is already open; retain the previous policy.
  bool setClockTimingCorrection(bool enabled) {
    if (_transaction_open || (enabled && !supportsClockTimingCorrection())) return false;
    _correct_clock_timing = enabled;
    return true;
  }
  bool isClockTimingCorrectionActive() const { return _correct_clock_timing; }
  // Last transaction's raw values remain available after it ends. They are
  // zero on unsupported cores, where this class retains the ordinary HAL.
  uint32_t getOriginalClockTiming() const { return _original_clock; }
  uint32_t getCorrectedClockTiming() const { return _corrected_clock; }

  void spiBeginTransaction() override {
    ArduinoHal::spiBeginTransaction();
    _transaction_open = true;
    _restore_clock = false;
#ifdef MESH_TDECK_CLOCK_TIMING_SUPPORTED
    _original_clock = GPSPI3.clock.val;
    _corrected_clock = _correct_clock_timing
        ? TDeckSpiClockTiming::corrected(_original_clock) : _original_clock;
    if (_corrected_clock != _original_clock) {
      // The base call already owns both SPI locks. Calling setClockDivider()
      // or spiSetClockDiv() here would try to acquire a held mutex again.
      GPSPI3.clock.val = _corrected_clock;
      GPSPI3.cmd.update = 1;
      while (GPSPI3.cmd.update) {}
      _restore_clock = true;
    }
#endif
  }

  void spiEndTransaction() override {
#ifdef MESH_TDECK_CLOCK_TIMING_SUPPORTED
    if (_restore_clock) {
      // Module has released radio CS. Restore the exact original divider while
      // still owning the bus, before the TFT's independent SPIClass can use it.
      GPSPI3.clock.val = _original_clock;
      GPSPI3.cmd.update = 1;
      while (GPSPI3.cmd.update) {}
    }
#endif
    _restore_clock = false;
    _transaction_open = false;
    ArduinoHal::spiEndTransaction();
  }
};

// Correct phase fields do not make every nominal clock valid for the slave:
// 80 MHz / 5 gives 16 MHz with 25 ns high and 37.5 ns low, whereas / 6 gives
// 13.333 MHz with 37.5 ns in each phase. Check the SX1262 minimum phase times
// independently; do not select 16 MHz for production based on frequency alone.
