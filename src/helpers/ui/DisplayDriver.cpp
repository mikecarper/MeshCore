#include "DisplayDriver.h"

#if defined(ESP32_PLATFORM) && defined(MESHCORE_HAS_REAL_DISPLAY)

#include <qrcode.h>

bool DisplayDriver::drawQrCode(const char* text, int x, int y, int size) {
  if (text == nullptr || text[0] == 0 || x < 0 || y < 0 || size <= 0
      || size > width() || size > height()
      || x > width() - size || y > height() - size) {
    return false;
  }

  // A 64-pixel square can hold at most Version 9 with the QR-standard
  // four-module quiet zone. Setup AP payloads are deliberately kept at
  // Version 1 on small screens so each module is 2x2 physical pixels.
  static constexpr uint8_t max_version = 9;
  static constexpr uint8_t max_modules = 4 * max_version + 17;
  uint8_t modules[(max_modules * max_modules + 7) / 8];
  QRCode qr = {};
  bool initialized = false;
  for (uint8_t version = 1; version <= max_version; ++version) {
    if (qrcode_initText(&qr, modules, version, ECC_LOW, text) == 0) {
      initialized = true;
      break;
    }
  }
  if (!initialized) return false;

  static constexpr int quiet_zone = 4;
  const int module_size = size / (qr.size + quiet_zone * 2);
  if (module_size <= 0) return false;
  const int used_size = module_size * (qr.size + quiet_zone * 2);
  const int left = x + (size - used_size) / 2;
  const int top = y + (size - used_size) / 2;
  const int matrix_left = left + quiet_zone * module_size;
  const int matrix_top = top + quiet_zone * module_size;

  // Dark-theme OLED/TFT drivers use a dark window and light popup text;
  // e-paper reverses that convention. Always emit the conventional dark
  // matrix on a light background instead of relying on scanner inversion.
  const ColorVal light = isEink() ? UIColor::window_bkg : UIColor::popup_txt;
  const ColorVal dark = isEink() ? UIColor::primary_txt : UIColor::window_bkg;
  setColor(light);
  fillRect(x, y, size, size);
  setColor(dark);
  for (uint8_t row = 0; row < qr.size; ++row) {
    for (uint8_t column = 0; column < qr.size; ++column) {
      if (qrcode_getModule(&qr, column, row)) {
        fillRect(matrix_left + column * module_size,
                 matrix_top + row * module_size,
                 module_size, module_size);
      }
    }
  }
  return true;
}

#endif
