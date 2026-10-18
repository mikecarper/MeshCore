#pragma once

#include "DisplayDriver.h"
#include "WiFiSetupQrPayload.h"
#if defined(ESP_PLATFORM) || defined(ESP32_PLATFORM)
#include "../esp32/WiFiAccessPointPolicy.h"
#endif

namespace mesh {
namespace ui {

inline bool buildCurrentWiFiSetupQrPayload(char* payload, size_t payload_size,
                                          const char* ssid) {
#if defined(ESP_PLATFORM) || defined(ESP32_PLATFORM)
  char password[65];
  if (!mesh::wifi::accessPointJoinPassword(ssid, password, sizeof(password)))
    return false;
  return buildWiFiSetupQrPayload(payload, payload_size, ssid, password);
#else
  const char* password = nullptr;
#ifdef WEBCONFIG_AP_PASSWORD
  password = WEBCONFIG_AP_PASSWORD;
#endif
  return buildWiFiSetupQrPayload(payload, payload_size, ssid, password);
#endif
}

// Render a setup-AP join QR at the largest size that still leaves the right
// half of compact displays available for a short textual fallback/reference.
// A Version 1 QR, including its four-module quiet zone, is exactly 58 pixels
// at two pixels per module. Keep that exact upper-left footprint on 128x64
// panels so a normal setup IP has the full 66-pixel text row beside it.
// Returns false when the payload or panel geometry cannot be represented.
inline bool drawWiFiSetupQr(DisplayDriver& display, const char* ssid,
                            const char* address, bool compact_border = false,
                            const char* action = nullptr) {
  char payload[256];
  if (!buildCurrentWiFiSetupQrPayload(payload, sizeof(payload), ssid)) {
    return false;
  }

  int qr_size = display.height() < display.width() / 2
      ? display.height() : display.width() / 2;
  static constexpr int compact_qr_size = (21 + 4 * 2) * 2;
  if (compact_border && qr_size >= 44) {
    // Version 1: 21 modules at 2 pixels, plus an exact one-pixel border.
    qr_size = 44;
  } else if (display.height() == 64 && qr_size >= compact_qr_size) {
    qr_size = compact_qr_size;
  }
  const bool rendered = compact_border
      ? display.drawQrCodeWithBorder(payload, 0, 0, qr_size, 1)
      : display.drawQrCode(payload, 0, 0, qr_size);
  if (!rendered) return false;

  const int text_x = qr_size + 4;
  display.setTextSize(1);
  display.setColor(UIColor::primary_txt);
  display.setCursor(text_x, 2);
  display.print(action ? "SCAN JOIN" : "SCAN TO");
  display.setCursor(text_x, 12);
  display.print(action ? action : "JOIN:");
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
