#pragma once

#include "DisplayDriver.h"
#include "DisplayTextLayout.h"
#include "QrCodeVersion.h"
#include "WiFiSetupQrPayload.h"
#if defined(ESP_PLATFORM) || defined(ESP32_PLATFORM)
#include "../esp32/WiFiAccessPointPolicy.h"
#endif

namespace mesh {
namespace ui {

inline void drawWiFiSetupChoice(DisplayDriver& display, const char* ssid, bool open,
                                bool starting, const char* reply) {
  display.setTextSize(1);
  display.setColor(UIColor::primary_txt);
  const int row = display.textLineHeight() + 1;
  display.drawTextLeftAlign(0, 0, open ? "Wi-Fi Setup: Open" : "Wi-Fi Setup: WPA2");
  if (starting) {
    drawTextWrapped(display, 0, row, display.width(), row, display.height() / row - 1, "Turning on Wi-Fi...");
    return;
  }
  display.drawTextEllipsized(0, row, display.width(), ssid);
  display.drawTextLeftAlign(0, 2 * row, "Hold to start");
  if (!reply[0]) reply = open ? "Admin login in browser" : "Tap for open Wi-Fi";
  drawTextWrapped(display, 0, 3 * row, display.width(), row, display.height() / row - 3, reply);
}

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

// Keep the setup address beside the QR code on a 128x64 display.
inline bool drawWiFiSetupQr(DisplayDriver& display, const char* ssid,
                            const char* address, bool compact_border = false,
                            const char* action = nullptr) {
  char payload[256];
  if (!buildCurrentWiFiSetupQrPayload(payload, sizeof(payload), ssid)) {
    return false;
  }

  int qr_size = display.height() < display.width() / 2
      ? display.height() : display.width() / 2;
  const bool small_screen = display.width() == 128 && display.height() == 64;
  if (small_screen || compact_border) {
    const uint8_t version = lowEccQrVersion(payload);
    if (version == 0) return false;
    const int modules = qrModuleCount(version);
    // 60 pixels fit a WPA setup code at 2x scale, a one-pixel white border,
    // and leave a two-pixel gap before the 66-pixel setup address.
    const int available = small_screen ? 60 : qr_size;
    const int scale = (available - 2) / modules;
    if (scale < 1) return false;
    qr_size = modules * scale + 2;
    compact_border = true;
  }
  const bool rendered = compact_border
      ? display.drawQrCodeWithBorder(payload, 0, 0, qr_size, 1)
      : display.drawQrCode(payload, 0, 0, qr_size);
  if (!rendered) return false;

  const int text_x = qr_size + (small_screen ? 2 : 4);
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
