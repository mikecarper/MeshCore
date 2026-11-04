#pragma once

#include <hal/Arduino/ArduinoHal.h>
#include "../RadioSwitchTuning.h"
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
#include "W12PramLabTrace.h"
#endif

// A radio keeps the board-selected SPI controller and its original lifetime
// owner. Runtime tuning only changes transaction settings and transfer style;
// it must never remap an nRF controller or restart a shared storage/display bus.
class TunableRadioHal : public ArduinoHal {
  TunableRadioHal* _next;
  const uint32_t _defaultHz;
  const uint8_t _spiClocks;
  const bool _canBuffer;
  uint8_t _spiMHz = 0;
  bool _bufferEnabled;
  // The pinned C6 core calculates SPI dividers from 40 MHz and cannot select
  // the 80/9 divider. Do not advertise a clock its HAL would round away.
#if defined(ESP32_PLATFORM) && !defined(CONFIG_IDF_TARGET_ESP32C6)
  static constexpr uint32_t DefaultMaxSpiHz = 8888888;
#else
  static constexpr uint32_t DefaultMaxSpiHz = 8000000;
#endif

  static TunableRadioHal*& registry() {
    static TunableRadioHal* head = nullptr;
    return head;
  }

 protected:
  bool bufferedTransfersEnabled() const { return _bufferEnabled; }

 public:
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
  void spiTransfer(uint8_t* out, size_t len, uint8_t* in) override {
    if (out && len >= 2) mesh::w12pram::spiTrace().add(uint16_t(out[0]) << 8 | out[1]);
    ArduinoHal::spiTransfer(out, len, in);
  }
#endif
  explicit TunableRadioHal(SPIClass& bus, uint32_t hz = 8000000,
      bool initializeBus = false, bool canBuffer = false,
      uint32_t maxHz = DefaultMaxSpiHz)
      : ArduinoHal(bus, SPISettings(hz, MSBFIRST, SPI_MODE0)),
        _next(registry()), _defaultHz(hz),
        _spiClocks(2 | 4 | 8 | (maxHz >= 16000000 ? 16 : 0)
#if defined(ESP32_PLATFORM) && !defined(CONFIG_IDF_TARGET_ESP32C6)
            | (maxHz >= 8888888 ? mesh::RadioSwitchCapabilities::Spi9 : 0)
#endif
            ),
        _canBuffer(canBuffer), _bufferEnabled(canBuffer) {
    initInterface = initializeBus;
    registry() = this;
  }

  ~TunableRadioHal() override {
    TunableRadioHal** entry = &registry();
    while (*entry && *entry != this) entry = &(*entry)->_next;
    if (*entry) *entry = _next;
  }

  TunableRadioHal(const TunableRadioHal&) = delete;
  TunableRadioHal& operator=(const TunableRadioHal&) = delete;

  // Pointer identity avoids RTTI and safely rejects unrelated board HALs.
  static TunableRadioHal* find(RadioLibHal* hal) {
    for (TunableRadioHal* entry = registry(); entry; entry = entry->_next)
      if (static_cast<RadioLibHal*>(entry) == hal) return entry;
    return nullptr;
  }

  mesh::RadioSwitchTuning getTuning() const {
    mesh::RadioSwitchTuning result;
    result.flags = _bufferEnabled ? mesh::RadioSwitchTuning::Buffer : 0;
    result.spi_mhz = _spiMHz;
    return result;
  }

  mesh::RadioSwitchCapabilities capabilities() const {
    mesh::RadioSwitchCapabilities result;
    result.flags = _canBuffer ? mesh::RadioSwitchTuning::Buffer : 0;
    result.spi_clocks = _spiClocks;
    return result;
  }

  bool setTuning(const mesh::RadioSwitchTuning& tuning) {
    mesh::RadioSwitchTuning value = tuning;
    value.flags &= mesh::RadioSwitchTuning::Buffer;
    if (!mesh::validRadioSwitchTuning(value, capabilities())) return false;
    _spiMHz = value.spi_mhz;
    _bufferEnabled = value.flags & mesh::RadioSwitchTuning::Buffer;
    spiSettings = SPISettings(effectiveHz(), MSBFIRST, SPI_MODE0);
    return true;
  }

  uint32_t defaultHz() const { return _defaultHz; }
  uint32_t effectiveHz() const {
    if (_spiMHz == 9) return 8888888;
    return _spiMHz ? static_cast<uint32_t>(_spiMHz) * 1000000UL : _defaultHz;
  }
};
