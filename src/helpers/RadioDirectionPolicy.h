#pragma once

#include <stdint.h>

#ifndef MESH_ENABLE_RADIO_DIRECTION
#if defined(ESP32_PLATFORM)
#define MESH_ENABLE_RADIO_DIRECTION 1
#else
#define MESH_ENABLE_RADIO_DIRECTION 0
#endif
#endif

namespace mesh { namespace radioDirection {

enum class Mode : uint8_t { Both = 0, Rx = 1, Tx = 2, Off = 3 };

#if MESH_ENABLE_RADIO_DIRECTION
Mode mode();
#else
inline Mode mode() { return Mode::Both; }
#endif

inline bool radioReceiveAllowed() {
  const Mode value = mode();
  return value == Mode::Both || value == Mode::Rx;
}

inline bool radioTransmitAllowed() {
  const Mode value = mode();
  return value == Mode::Both || value == Mode::Tx;
}

}} // namespace mesh::radioDirection
