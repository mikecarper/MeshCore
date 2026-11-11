#pragma once
#include <RadioLib.h>
#include "SX126xStatus.h"

// -1: no reliable observation, 0: confirmed non-RX mode, 1: RX.
// GetStatus is C0 followed by one response byte (Semtech sx126x driver).
// RadioLib 7.8.1 also reads that response directly. This probe additionally
// avoids waiting on BUSY, preserves the SPI layout and checks transport errors.
template <typename Radio>
inline int8_t sx126xReceiveMode(Radio* radio) {
  Module* module = radio->getMod();
  const uint32_t busy = module->getGpio();
  if (busy != RADIOLIB_NC && module->hal->digitalRead(busy)) return -1;

  // GetStatus has no separate status-prefix byte. Use the normal SPI transport
  // with that command's layout, then restore the layout used by other commands.
  uint8_t status=0;
  const int16_t result=sx126xReadStatus(module,status);
  if (result != RADIOLIB_ERR_NONE || status == 0 || status == 0xFF) return -1;
  const uint8_t mode = (status >> 4) & 7;
  if (mode < 2 || mode > 6) return -1;
  return mode == 5 ? 1 : 0;
}
