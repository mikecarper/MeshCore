#pragma once

#include <stdint.h>

namespace mesh {
namespace ota {

// Stream::flush() does not have consistent semantics across Arduino transports.
// Hardware serial implementations wait for queued TX bytes, while ESP32
// WiFiClient and nRF52 BLEUart discard queued RX bytes. Require every mOTA
// stream caller to choose explicitly so a new transport cannot inherit an
// unsafe default.
enum class MotaStreamWritePolicy : uint8_t {
  FlushTransmit,
  NoFlush,
};

} // namespace ota
} // namespace mesh
