#pragma once

#include <stdint.h>

// TASK_ACTIVATE communicates with the external NOR and cannot complete while
// the chip is in deep power-down.  Wake it in GPIO SPI mode before handing the
// pins to QSPI.  All supported parts accept an opcode-only 0xAB transaction.
#define OTA_QSPI_WAKE_OPCODE   0xABu
#define OTA_QSPI_WAKE_BITS     8u
#define OTA_QSPI_WAKE_EDGE_US  1u
#define OTA_QSPI_WAKE_GUARD_US 50u

static inline uint8_t ota_qspi_wake_bit(uint8_t bit) {
  return (uint8_t)((OTA_QSPI_WAKE_OPCODE >> (OTA_QSPI_WAKE_BITS - 1u - bit)) & 1u);
}
