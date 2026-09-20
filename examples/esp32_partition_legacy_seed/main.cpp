// Disposable-hardware test seed for the ESP32 Wi-Fi partition migrator.
//
// This is intentionally not a deployable MeshCore role.  It models the two
// properties that matter before an inaccessible legacy node is migrated: an
// ordinary 1.25 MiB Arduino OTA partition table and the historical 96-byte
// MeshCore identity file.  The keypair below is MeshCore's known-valid test
// identity; it makes a full production image able to load the preserved key
// after the migration without exposing a real user's private key.

#include <Arduino.h>
#include <WiFi.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <AsyncElegantOTA.h>
#include <SPIFFS.h>

#include <helpers/esp32/WiFiRadioPolicy.h>

namespace {

constexpr char kApSsid[] = "MeshCore-Legacy-Seed";
constexpr char kApPassword[] = "meshcore-seed";
constexpr char kIdentityPath[] = "/identity/_main.id";

// File order is historical MeshCore order: public key first, then private key.
constexpr uint8_t kIdentity[96] = {
  0x1e, 0xc7, 0x71, 0x75, 0xb0, 0x91, 0x8e, 0xd2,
  0x06, 0xf9, 0xae, 0x04, 0xec, 0x13, 0x6d, 0x6d,
  0x5d, 0x43, 0x15, 0xbb, 0x26, 0x30, 0x54, 0x27,
  0xf6, 0x45, 0xb4, 0x92, 0xe9, 0x35, 0x0c, 0x10,
  0x70, 0x65, 0xe1, 0x8f, 0xd9, 0xfa, 0xbb, 0x70,
  0xc1, 0xed, 0x90, 0xdc, 0xa1, 0x99, 0x07, 0xde,
  0x69, 0x8c, 0x88, 0xb7, 0x09, 0xea, 0x14, 0x6e,
  0xaf, 0xd9, 0x3d, 0x9b, 0x83, 0x0c, 0x7b, 0x60,
  0xc4, 0x68, 0x11, 0x93, 0xc7, 0x9b, 0xbc, 0x39,
  0x94, 0x5b, 0xa8, 0x06, 0x41, 0x04, 0xbb, 0x61,
  0x8f, 0x8f, 0xd7, 0xa8, 0x4a, 0x0a, 0xf6, 0xf5,
  0x70, 0x33, 0xd6, 0xe8, 0xdd, 0xcd, 0x64, 0x71,
};

AsyncWebServer server(80);

bool installIdentity() {
  if (!SPIFFS.begin(true)) return false;
  if (!SPIFFS.exists("/identity") && !SPIFFS.mkdir("/identity")) {
    SPIFFS.end();
    return false;
  }
  File existing = SPIFFS.open(kIdentityPath, "r");
  bool already_valid = existing && existing.size() == sizeof(kIdentity);
  if (already_valid) {
    uint8_t check[sizeof(kIdentity)];
    already_valid = existing.read(check, sizeof(check)) == sizeof(check)
        && memcmp(check, kIdentity, sizeof(check)) == 0;
  }
  if (existing) existing.close();
  if (!already_valid) {
    File file = SPIFFS.open(kIdentityPath, "w");
    const bool wrote = file && file.write(kIdentity, sizeof(kIdentity)) == sizeof(kIdentity);
    if (file) {
      file.flush();
      file.close();
    }
    if (!wrote) {
      SPIFFS.end();
      return false;
    }
  }
  SPIFFS.end();
  return true;
}

void startAccessPoint() {
  WiFi.mode(WIFI_AP_STA);
  WiFi.setSleep(false);
  const IPAddress address(192, 168, 4, 1);
  const IPAddress netmask(255, 255, 255, 0);
  if (!WiFi.softAPConfig(address, address, netmask)
      || !WiFi.softAP(kApSsid, kApPassword, mesh::wifi::accessPointChannel())
      || mesh::wifi::applyAccessPointProtocolMask() != ESP_OK
      || esp_wifi_set_protocol(WIFI_IF_STA, mesh::wifi::kProtocolMask) != ESP_OK
      || esp_wifi_set_max_tx_power(78) != ESP_OK) {
    Serial.println("Legacy seed AP failed");
    return;
  }
  server.on("/", HTTP_GET, [](AsyncWebServerRequest* request) {
    request->send(200, "text/html",
        "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<h2>MeshCore legacy OTA seed</h2>"
        "<p>1.25 MiB A/B layout with a valid 96-byte identity file.</p>"
        "<p><a href='/update'>Upload the partition migrator</a></p>");
  });
  AsyncElegantOTA.begin(&server);
  server.begin();
  Serial.printf("Legacy seed ready. Join %s and open http://192.168.4.1/\n", kApSsid);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(250);
  const bool identity_ok = installIdentity();
  Serial.printf("Legacy seed identity: %s, public key "
                "1EC77175B0918ED206F9AE04EC136D6D5D4315BB26305427F645B492E9350C10\n",
                identity_ok ? "ready" : "FAILED");
  if (identity_ok) startAccessPoint();
}

void loop() {
  delay(20);
}
