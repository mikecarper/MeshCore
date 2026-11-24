#pragma once

#include <stdint.h>
#include <string.h>

namespace mesh {
namespace ui {

// Both display encoders choose one mode for the whole string. Choose the
// version before encoding because they can accept a version that is too small.
inline uint8_t lowEccQrVersion(const char* text) {
  if (text == nullptr || text[0] == 0) return 0;

  // Maximum characters per mode for QR versions 1 through 10 at ECC level L.
  enum { Numeric, Alphanumeric, Byte } mode = Byte;
  static constexpr uint16_t capacities[][3] = {
    { 41,  25,  17}, { 77,  47,  32}, {127,  77,  53}, {187, 114,  78},
    {255, 154, 106}, {322, 195, 134}, {370, 224, 154}, {461, 279, 192},
    {552, 335, 230}, {652, 395, 271},
  };
  const size_t length = strlen(text);
  if (strspn(text, "0123456789") == length) {
    mode = Numeric;
  } else if (strspn(text, "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:") == length) {
    mode = Alphanumeric;
  }
  for (size_t i = 0; i < sizeof(capacities) / sizeof(capacities[0]); ++i) {
    if (length <= capacities[i][mode]) return i + 1;
  }
  return 0;
}

constexpr int qrModuleCount(uint8_t version) { return 17 + 4 * version; }

}  // namespace ui
}  // namespace mesh
