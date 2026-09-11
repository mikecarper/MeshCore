#include "DisplayDriver.h"
#include <Arduino.h>

bool DisplayDriver::servicePower(bool usb_power, bool app_connected, bool pairing) {
  _power_policy.update(mesh::ui::displayPowerPrefs(), usb_power, app_connected, pairing, millis());
  const bool changed = isOn() != _power_policy.on();
  if (changed) { if (_power_policy.on()) turnOn(); else turnOff(); }
  return changed;
}

bool DisplayDriver::wake(mesh::ui::DisplayWake reason) {
  if (!_power_policy.wake(reason, millis())) return false;
  if (_power_policy.on() && !isOn()) turnOn();
  return true;
}

void DisplayDriver::dismiss() {
  _power_policy.dismiss();
  if (!_power_policy.on() && isOn()) turnOff();
}

#if defined(ESP32_PLATFORM) && defined(MESHCORE_HAS_REAL_DISPLAY)

#include <qrcode.h>

bool DisplayDriver::drawQrCode(const char* text, int x, int y, int size) {
  return drawQrCodeWithBorder(text, x, y, size, -1);
}

bool DisplayDriver::drawQrCodeWithBorder(const char* text, int x, int y,
                                        int size, int border_pixels) {
  if (text == nullptr || text[0] == 0 || x < 0 || y < 0 || size <= 0
      || size > width() || size > height()
      || x > width() - size || y > height() - size
      || border_pixels < -1 || border_pixels > (size - 1) / 2) {
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
  const int module_size = border_pixels < 0
      ? size / (qr.size + quiet_zone * 2)
      : (size - border_pixels * 2) / qr.size;
  if (module_size <= 0) return false;
  const int border = border_pixels < 0
      ? quiet_zone * module_size : border_pixels;
  const int used_size = module_size * qr.size + border * 2;
  const int left = x + (size - used_size) / 2;
  const int top = y + (size - used_size) / 2;
  const int matrix_left = left + border;
  const int matrix_top = top + border;

  // Dark-theme OLED/TFT drivers use a dark window and light popup text;
  // e-paper reverses that convention. Always emit the conventional dark
  // matrix on a light background instead of relying on scanner inversion.
  const ColorVal light = isEink() ? UIColor::window_bkg : UIColor::popup_txt;
  const ColorVal dark = isEink() ? UIColor::primary_txt : UIColor::window_bkg;
  setColor(light);
  // An explicit border is exact, even if a larger QR version leaves unused
  // space in the requested square. Do not add that space to the white border.
  if (border_pixels < 0) fillRect(x, y, size, size);
  else fillRect(left, top, used_size, used_size);
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
