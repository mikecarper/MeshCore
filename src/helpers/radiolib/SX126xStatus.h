#pragma once
#include <RadioLib.h>

// One checked transport for the command's payload-only status layout. The
// caller owns BUSY admission: ordinary probes reject BUSY; exceptional recovery
// may deliberately issue this command to wake, without earning cycle evidence.
template <typename ModuleType>
#if defined(__GNUC__)
__attribute__((noinline))
#endif
inline int16_t sx126xReadStatus(ModuleType* module,uint8_t& status) {
  const auto width=module->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS];
  module->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS]=ModuleType::BITS_0;
  status=0;
  const int16_t rc=module->SPIreadStream(RADIOLIB_SX126X_CMD_GET_STATUS,&status,1,false,false);
  module->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS]=width;
  return rc;
}
