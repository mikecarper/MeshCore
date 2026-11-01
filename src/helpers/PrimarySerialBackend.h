#pragma once

// Some nRF52 boards expose a USB-to-UART bridge instead of native USB CDC.
// The explicit build flag names that hardware stream in every translation
// unit; a board-local `#define Serial Serial1` cannot select shared helpers.
#if defined(MESH_PRIMARY_UART_PORT)
  #define MESH_PRIMARY_SERIAL_IS_UART 1
#else
  #define MESH_PRIMARY_SERIAL_IS_UART 0
#endif

#if defined(ARDUINO) && MESH_PRIMARY_SERIAL_IS_UART
#include <Arduino.h>
namespace mesh {
inline Stream& primarySerialPort() {
  return MESH_PRIMARY_UART_PORT;
}
}  // namespace mesh
#endif
