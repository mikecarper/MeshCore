#pragma once

#include "DisplayDriver.h"
#include "WiFiSetupQrPayload.h"

namespace mesh {
namespace ui {

// Render a setup-AP join QR at the largest size that still leaves the right
// half of compact displays available for a short textual fallback/reference.
// Returns false when the payload or panel geometry cannot be represented.
inline bool drawWiFiSetupQr(DisplayDriver& display, const char* ssid,
                            const char* address) {
  char payload[256];
  const char* password = nullptr;
#ifdef WEBCONFIG_AP_PASSWORD
  password = WEBCONFIG_AP_PASSWORD;
#endif
  if (!buildWiFiSetupQrPayload(
          payload, sizeof(payload), ssid, password)) {
    return false;
  }

  const int qr_size = display.height() < display.width() / 2
      ? display.height() : display.width() / 2;
  if (!display.drawQrCode(payload, 0, 0, qr_size)) return false;

  const int text_x = qr_size + 4;
  display.setTextSize(1);
  display.setColor(UIColor::primary_txt);
  display.setCursor(text_x, 2);
  display.print("SCAN TO");
  display.setCursor(text_x, 12);
  display.print("JOIN:");
  display.drawTextEllipsized(
      text_x, 22, display.width() - text_x, ssid);
  display.setCursor(text_x, 37);
  display.print("THEN OPEN");
  display.drawTextEllipsized(
      text_x, 48, display.width() - text_x, address);
  return true;
}

}  // namespace ui
}  // namespace mesh
