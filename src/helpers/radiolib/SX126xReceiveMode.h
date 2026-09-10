#pragma once
#include <RadioLib.h>

// -1: no reliable observation, 0: confirmed non-RX mode, 1: RX.
// GetStatus is C0 followed by one response byte (Semtech sx126x driver).
// RadioLib's pinned getStatus() requests zero payload bytes and returns zero.
template <typename Radio>
inline int8_t sx126xReceiveMode(Radio* radio) {
  Module* module = radio->getMod();
  const uint32_t busy = module->getGpio();
  if (busy != RADIOLIB_NC && module->hal->digitalRead(busy)) return -1;

  // GetStatus has no separate status-prefix byte. Use the normal SPI transport
  // with that command's layout, then restore the layout used by other commands.
  const Module::BitWidth_t status_width = module->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS];
  module->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] = Module::BITS_0;
  uint8_t status = 0;
  const int16_t result = module->SPIreadStream(
      RADIOLIB_SX126X_CMD_GET_STATUS, &status, 1, false, false);
  module->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] = status_width;
  if (result != RADIOLIB_ERR_NONE || status == 0 || status == 0xFF) return -1;
  const uint8_t mode = (status >> 4) & 7;
  if (mode < 2 || mode > 6) return -1;
  return mode == 5 ? 1 : 0;
}
