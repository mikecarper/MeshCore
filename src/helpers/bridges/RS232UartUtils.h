#pragma once

namespace mesh {
namespace bridge {

template <typename UartType, typename PinType>
inline void prepareNrfUart(UartType& uart, PinType rx, PinType tx) {
  uart.end();
  uart.setPins(rx, tx);
}

} // namespace bridge
} // namespace mesh
