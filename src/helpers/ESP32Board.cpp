#ifdef ESP_PLATFORM

#include "ESP32Board.h"
#include "WirelessControl.h"
#include <target.h>
#include "UsbLogging.h"
#include "FileRead.h"
#include "UserGpioPinPolicy.h"

namespace mesh {
namespace esp32_clock {
RTC_NOINIT_ATTR uint32_t rtc_backup_time;
RTC_NOINIT_ATTR uint32_t rtc_backup_magic;
}  // namespace esp32_clock
}  // namespace mesh

namespace {

bool isEsp32SystemPin(uint8_t pin) {
#if defined(CONFIG_IDF_TARGET_ESP32)
  // SPI flash, boot strapping, and (when present) external PSRAM.
  if ((pin >= 6 && pin <= 11) || pin == 0 || pin == 2 || pin == 5 || pin == 12 || pin == 15) return true;
#ifdef BOARD_HAS_PSRAM
  if (pin == 16 || pin == 17) return true;
#endif
#elif defined(CONFIG_IDF_TARGET_ESP32S2)
  if ((pin >= 26 && pin <= 32) || pin == 0 || pin == 45 || pin == 46) return true;
  if (pin == 19 || pin == 20) return true; // native USB
#elif defined(CONFIG_IDF_TARGET_ESP32S3)
  if ((pin >= 26 && pin <= 32) || pin == 0 || pin == 3 || pin == 45 || pin == 46) return true;
#ifdef CONFIG_SPIRAM_MODE_OCT
  if (pin >= 33 && pin <= 37) return true;
#endif
  if (pin == 19 || pin == 20) return true; // native USB/JTAG
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
  if ((pin >= 12 && pin <= 17) || pin == 2 || pin == 8 || pin == 9) return true;
  if (pin == 18 || pin == 19) return true; // native USB/JTAG
#elif defined(CONFIG_IDF_TARGET_ESP32C6)
  if ((pin >= 24 && pin <= 30) || pin == 4 || pin == 5 || pin == 8 || pin == 9 || pin == 15) return true;
  if (pin == 12 || pin == 13) return true; // native USB/JTAG
#endif
  return false;
}

} // namespace

bool ESP32Board::isUserGpioAvailable(uint8_t pin) const {
  if (!digitalPinCanOutput(pin) || isEsp32SystemPin(pin)) return false;

  // Serial is the local CLI, and Wire is initialized by ESP32Board::begin().
  if (pin == TX || pin == RX) return false;
#if !defined(PIN_BOARD_SDA) || !defined(PIN_BOARD_SCL)
  if (pin == SDA || pin == SCL) return false;
#endif

  return !UserGpioPinPolicy::isFirmwareReserved(pin);
}

#if !defined(LIGHTWEIGHT_WIFI_OTA) || \
    (!defined(ADMIN_PASSWORD) && !defined(COMPANION_RADIO_FULL))
bool ESP32Board::getOTAIdentity(char reply[], size_t max_len) {
  if (reply && max_len) snprintf(reply, max_len,
      "ERR: bound WiFi OTA identity unavailable; use supported firmware or USB");
  return false;
}
#endif

#if defined(LIGHTWEIGHT_WIFI_OTA) && \
    (defined(ADMIN_PASSWORD) || defined(COMPANION_RADIO_FULL))
#include <WiFi.h>
#include <helpers/esp32/WiFiRadioPolicy.h>
#include <helpers/esp32/WiFiAccessPointPolicy.h>
#include <helpers/esp32/StaticHtml.h>
#include <Update.h>
#include <esp_ota_ops.h>
#if __has_include(<esp_mac.h>)
#include <esp_mac.h>
#endif
#include <helpers/ota/OtaSelf.h>
#include <helpers/esp32/WiFiOtaStreamValidator.h>
#include <SHA256.h>
#include <SPIFFS.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <strings.h>

// Full Companion retains its established uploader URL on port 8080.
// Infrastructure uses port 80; CLI startup hands WebConfig's WiFi to OTA.
#if defined(COMPANION_RADIO_FULL)
static constexpr uint16_t LIGHTWEIGHT_OTA_PORT = 8080;
#else
static constexpr uint16_t LIGHTWEIGHT_OTA_PORT = 80;
#endif

class LightweightOTAServer {
  WiFiServer server{LIGHTWEIGHT_OTA_PORT};
  TaskHandle_t task = nullptr;
  volatile bool running = false;
  ESP32Board* board = nullptr;
  mesh::wifi::OtaSessionIdentity identity;

  static void sendResponse(WiFiClient& client, int code, const char* reason,
                           const char* content_type, const char* body, size_t length) {
    client.printf("HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %u\r\n"
                  "Connection: close\r\nCache-Control: no-store\r\n\r\n",
                  code, reason, content_type, static_cast<unsigned>(length));
    if (length) client.write(reinterpret_cast<const uint8_t*>(body), length);
  }

  bool readLine(WiFiClient& client, char* line, size_t capacity) {
    size_t length = 0;
    uint32_t deadline = millis() + 5000;
    while (running && client.connected()) {
      while (client.available()) {
        int c = client.read();
        if (c < 0) break;
        if (c == '\n') {
          if (!length || line[length - 1] != '\r') return false;
          length--;
          line[length] = 0;
          return true;
        }
        if (c == 0 || (c < 32 && c != '\r' && c != '\t') ||
            length + 1 >= capacity) return false;
        if (length && line[length - 1] == '\r') return false;
        line[length++] = static_cast<char>(c);
      }
      if (static_cast<int32_t>(millis() - deadline) >= 0) break;
      delay(1);
    }
    return false;
  }

  void sendPage(WiFiClient& client) {
    client.printf("HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                  "Content-Encoding: gzip\r\nContent-Length: %u\r\n"
                  "Connection: close\r\nCache-Control: no-store\r\n\r\n",
                  static_cast<unsigned>(MESH_HTML_LIGHTWEIGHT_OTA_LEN));
    client.write(MESH_HTML_LIGHTWEIGHT_OTA, MESH_HTML_LIGHTWEIGHT_OTA_LEN);
  }

  void sendInfo(WiFiClient& client) {
    char json[256];
    if (!identity.writeJson(json, sizeof(json))) {
      static const char missing[] = "bound OTA identity unavailable";
      sendResponse(client, 503, "Service Unavailable", "text/plain", missing, sizeof(missing) - 1);
      return;
    }
    sendResponse(client, 200, "OK", "application/json", json, strlen(json));
  }

  void sendLog(WiFiClient& client) {
    File log = mesh::openFileRead(&SPIFFS, "/packet_log");
    if (!log) {
      static const char missing[] = "packet log not found";
      sendResponse(client, 404, "Not Found", "text/plain", missing, sizeof(missing) - 1);
      return;
    }

    client.printf("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %u\r\n"
                  "Connection: close\r\nCache-Control: no-store\r\n\r\n",
                  static_cast<unsigned>(log.size()));
    uint8_t buf[1024];
    while (running && log.available() && client.connected()) {
      size_t count = log.read(buf, sizeof(buf));
      if (!count || client.write(buf, count) != count) break;
    }
    log.close();
  }

  void sendUpdateError(WiFiClient& client, int code, const char* reason) {
    char error[96];
    snprintf(error, sizeof(error), "OTA error: %s", Update.errorString());
    sendResponse(client, code, reason, "text/plain", error, strlen(error));
  }

  void receiveUpdate(WiFiClient& client, size_t total) {
    if (total == 0) {
      static const char empty[] = "empty firmware image";
      sendResponse(client, 400, "Bad Request", "text/plain", empty, sizeof(empty) - 1);
      return;
    }

    mesh::wifi::OtaStreamValidator<SHA256> validator(identity, total);
    if (!validator.valid()) {
      static const char invalid[] = "firmware image does not fit the bound OTA session";
      sendResponse(client, 400, "Bad Request", "text/plain", invalid, sizeof(invalid) - 1);
      return;
    }

    board->setInhibitSleep(true);
    if (!Update.begin(total, U_FLASH)) {
      sendUpdateError(client, 400, "Bad Request");
      return;
    }

    uint8_t buf[1024];
    size_t received_total = 0;
    uint32_t deadline = millis() + 15000;
    while (running && received_total < total && client.connected()) {
      int available = client.available();
      if (available <= 0) {
        if (static_cast<int32_t>(millis() - deadline) >= 0) break;
        delay(1);
        continue;
      }
      size_t wanted = total - received_total;
      if (wanted > sizeof(buf)) wanted = sizeof(buf);
      if (wanted > static_cast<size_t>(available)) wanted = static_cast<size_t>(available);
      int received = client.read(buf, wanted);
      if (received <= 0) break;
      deadline = millis() + 15000;
      if (!validator.update(buf, static_cast<size_t>(received))) {
        Update.abort();
        static const char invalid[] = "firmware application header, chip or checksum is invalid";
        sendResponse(client, 400, "Bad Request", "text/plain", invalid, sizeof(invalid) - 1);
        return;
      }
      if (Update.write(buf, static_cast<size_t>(received)) != static_cast<size_t>(received)) {
        sendUpdateError(client, 500, "Internal Server Error");
        Update.abort();
        return;
      }
      received_total += static_cast<size_t>(received);
    }

    if (received_total != total || client.available() > 0) {
      Update.abort();
      static const char incomplete[] = "firmware upload incomplete or trailing data";
      sendResponse(client, 408, "Request Timeout", "text/plain", incomplete, sizeof(incomplete) - 1);
      return;
    }
    if (!validator.finish()) {
      Update.abort();
      static const char invalid[] = "firmware EndF identity, body hash or image structure is invalid; use exact-board USB";
      sendResponse(client, 400, "Bad Request", "text/plain", invalid, sizeof(invalid) - 1);
      return;
    }
    if (!Update.end()) {
      sendUpdateError(client, 500, "Internal Server Error");
      return;
    }

    static const char success[] = "OK - firmware installed; rebooting";
    sendResponse(client, 200, "OK", "text/plain", success, sizeof(success) - 1);
    delay(500);
    client.stop();
    esp_restart();
  }

  void handleClient(WiFiClient& client) {
    char line[256];
    static const char malformed[] = "invalid or unsupported OTA HTTP framing";
    if (!readLine(client, line, sizeof(line))) {
      sendResponse(client, 400, "Bad Request", "text/plain", malformed, sizeof(malformed) - 1);
      return;
    }
    const auto requestIs = [&](const char* method_path) {
      const size_t length = strlen(method_path);
      return strncmp(line, method_path, length) == 0 &&
          (strcmp(line + length, "HTTP/1.1") == 0 || strcmp(line + length, "HTTP/1.0") == 0);
    };
    const bool get_page = requestIs("GET / ") || requestIs("GET /update ");
    const bool get_log = requestIs("GET /log ");
    const bool get_info = requestIs("GET /ota/info ");
    const bool post_update = requestIs("POST /update ");
    mesh::wifi::OtaRequestHeaders headers;
    do {
      if (!readLine(client, line, sizeof(line)) || (line[0] && !headers.add(line))) {
        sendResponse(client, 400, "Bad Request", "text/plain", malformed, sizeof(malformed) - 1);
        return;
      }
    } while (line[0]);

    if (!running) return;
    if (post_update) {
      const int available = client.available();
      if (!headers.allowsUpdate(identity, available > 0 ? static_cast<size_t>(available) : 0)) {
        static const char rejected[] = "OTA session, length or content type does not match this node";
        sendResponse(client, 403, "Forbidden", "text/plain", rejected, sizeof(rejected) - 1);
        return;
      }
      receiveUpdate(client, headers.content_length);
    } else if (headers.has_length && headers.content_length != 0) {
      sendResponse(client, 400, "Bad Request", "text/plain", malformed, sizeof(malformed) - 1);
    } else if (get_page) sendPage(client);
    else if (get_log) sendLog(client);
    else if (get_info) sendInfo(client);
    else {
      static const char missing[] = "not found";
      sendResponse(client, 404, "Not Found", "text/plain", missing, sizeof(missing) - 1);
    }
  }

  static void taskEntry(void* arg) {
    LightweightOTAServer* self = static_cast<LightweightOTAServer*>(arg);
    while (self->running) {
      WiFiClient client = self->server.available();
      if (client) {
        client.setTimeout(15000);
        self->handleClient(client);
        client.stop();
      } else {
        delay(10);
      }
    }
    self->task = nullptr;
    vTaskDelete(nullptr);
  }

public:
  bool begin(ESP32Board* owner) {
    if (running) return true;
    if (task != nullptr) return false;
    char identity_reply[160];
    if (!owner || !owner->getOTAIdentity(identity_reply, sizeof(identity_reply))) return false;
    identity = owner->ota_identity;
    board = owner;
    server.begin();
    server.setNoDelay(true);
    running = true;
    if (xTaskCreate(taskEntry, "ota-http", 6144, this, 4, &task) != pdPASS) {
      running = false;
      server.stop();
      task = nullptr;
      board = nullptr;
      identity.clear();
      return false;
    }
    return true;
  }

  bool isRunning() const { return running; }

  bool end() {
    running = false;
    server.stop();
    for (unsigned i = 0; task != nullptr && i < 100; i++) delay(10);
    // A slow client can still be returning from a socket write. Keep its
    // board/session alive until the task exits; a retry can finish cleanup.
    if (task != nullptr) return false;
    board = nullptr;
    identity.clear();
    return true;
  }
};

static LightweightOTAServer lightweight_ota_server;

bool ESP32Board::getOTAIdentity(char reply[], size_t max_len) {
  if (!reply || !max_len) return false;
  if (ota_server != nullptr && !lightweight_ota_server.isRunning()) {
    snprintf(reply, max_len, "ERR: OTA stopping; retry stop ota first");
    return false;
  }
  const esp_partition_t* active = esp_ota_get_running_partition();
  const esp_partition_t* next = esp_ota_get_next_update_partition(nullptr);
  if (!active || !next || active->address == next->address || !next->size) {
    snprintf(reply, max_len, "ERR: bound OTA needs two app partitions; use USB");
    return false;
  }
  mesh::ota::SelfFwInfo self;
  if (!mesh::ota::ota_self_firmware(self) || !self.valid ||
      !mesh::wifi::OtaSessionIdentity::validHardware(self.hw_id)) {
    snprintf(reply, max_len, "ERR: running firmware has no canonical OTA hardware identity");
    return false;
  }
  uint16_t chip;
#if defined(CONFIG_IDF_TARGET_ESP32)
  chip = 0;
#elif defined(CONFIG_IDF_TARGET_ESP32S2)
  chip = 2;
#elif defined(CONFIG_IDF_TARGET_ESP32S3)
  chip = 9;
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
  chip = 5;
#elif defined(CONFIG_IDF_TARGET_ESP32C6)
  chip = 13;
#else
  snprintf(reply, max_len, "ERR: unsupported OTA MCU identity");
  return false;
#endif
  uint8_t mac[6], nonce[16] = {};
  if (esp_efuse_mac_get_default(mac) != ESP_OK) {
    snprintf(reply, max_len, "ERR: physical OTA device identity unavailable");
    return false;
  }
  if (!ota_identity.prepared()) esp_fill_random(nonce, sizeof(nonce));
  if (!ota_identity.prepare(nonce, mac, chip, next->size, self.hw_id) ||
      !ota_identity.writeCli(reply, max_len)) {
    snprintf(reply, max_len, "ERR: OTA identity changed or reply buffer too small; stop ota");
    return false;
  }
  return true;
}

bool ESP32Board::startOTAUpdate(const char* id, char reply[], bool force_ap) {
  (void)id;
  if (ota_server != nullptr && !lightweight_ota_server.isRunning()) {
    strcpy(reply, "ERR: OTA stopping; retry stop ota first");
    return false;
  }
#if defined(COMPANION_RADIO_FULL)
  const esp_partition_t* running = esp_ota_get_running_partition();
  const esp_partition_t* next = esp_ota_get_next_update_partition(nullptr);
  if (!running || !next || running->address == next->address) {
    strcpy(reply, "ERR: this partition layout requires a USB firmware update");
    return false;
  }
#endif
  if (!mesh::wireless::control().allowWiFiForOTA()) {
    strcpy(reply, "ERR: wireless change pending; retry start ota");
    return false;
  }
  inhibit_sleep = true;

  IPAddress ip;
  const bool use_ap = force_ap || WiFi.status() != WL_CONNECTED
      || static_cast<uint32_t>(WiFi.localIP()) == 0;
  if (!use_ap) {
    ip = WiFi.localIP();
  } else {
    if (!mesh::wifi::accessPointCompatibleWithLongRange()) {
      inhibit_sleep = ota_server != nullptr;
      strcpy(reply, "ERR: stop ESP-NOW before starting an OTA AP");
      return false;
    }
    ota_started_radio = ota_started_radio || WiFi.getMode() == WIFI_OFF;
    ota_started_ap = mesh::wifi::startOpenAccessPoint("MeshCore-OTA", ota_started_ap);
    if (!ota_started_ap) {
      inhibit_sleep = ota_server != nullptr;
      if (!inhibit_sleep) {
        mesh::wifi::stopTemporaryAccessPointRadio(ota_started_radio);
        ota_started_radio = false;
      }
      strcpy(reply, "ERR: OTA WiFi failed");
      return false;
    }
    ip = WiFi.softAPIP();
  }

  if (ota_server == nullptr) {
    if (!lightweight_ota_server.begin(this)) {
      if (ota_started_ap) WiFi.softAPdisconnect(true);
      ota_started_ap = false;
      mesh::wifi::stopTemporaryAccessPointRadio(ota_started_radio);
      ota_started_radio = false;
      inhibit_sleep = false;
      strcpy(reply, "ERR: OTA server failed");
      return false;
    }
    ota_server = &lightweight_ota_server;
  }

  const char* network = use_ap ? "Join WiFi MeshCore-OTA" : "Use same WiFi/LAN";
  if (LIGHTWEIGHT_OTA_PORT == 80) {
    snprintf(reply, 160, "Started: http://%s/update - WiFi on; %s", ip.toString().c_str(), network);
  } else {
    snprintf(reply, 160, "Started: http://%s:%u/update - WiFi on; %s", ip.toString().c_str(),
             static_cast<unsigned>(LIGHTWEIGHT_OTA_PORT), network);
  }
  MESH_DEBUG_PRINTLN("startOTAUpdate: %s", reply);
  return true;
}

bool ESP32Board::stopOTAUpdate(char reply[]) {
  if (ota_server == nullptr) {
    if (ota_started_ap) WiFi.softAPdisconnect(true);
    ota_started_ap = false;
    mesh::wifi::stopTemporaryAccessPointRadio(ota_started_radio);
    ota_started_radio = false;
    inhibit_sleep = false;
    ota_identity.clear();
    strcpy(reply, "OK - OTA not running");
    return true;
  }

  if (!lightweight_ota_server.end()) {
    strcpy(reply, "ERR: OTA stopping; retry stop ota");
    return false;
  }
  ota_server = nullptr;
  ota_identity.clear();
  if (ota_started_ap) WiFi.softAPdisconnect(true);
  ota_started_ap = false;
  mesh::wifi::stopTemporaryAccessPointRadio(ota_started_radio);
  ota_started_radio = false;
  inhibit_sleep = false;
  strcpy(reply, "OK - OTA stopped");
  MESH_DEBUG_PRINTLN("stopOTAUpdate: %s", reply);
  return true;
}

#elif defined(ADMIN_PASSWORD) && !defined(DISABLE_WIFI_OTA)   // Repeater or Room Server only
#include <WiFi.h>
#include <helpers/esp32/WiFiRadioPolicy.h>
#include <helpers/esp32/WiFiAccessPointPolicy.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include "../../arch/esp32/AsyncElegantOTA/src/AsyncElegantOTA.h"
#include <new>

#include <SPIFFS.h>

// Requests retain raw pointers to the AsyncWebServer and its route handlers.
// Keep this one host alive across stops; end() only closes its listener.
static AsyncWebServer* async_ota_host = nullptr;

bool ESP32Board::startOTAUpdate(const char* id, char reply[], bool force_ap) {
  if (!mesh::wireless::control().allowWiFiForOTA()) {
    strcpy(reply, "ERR: wireless change pending; retry start ota");
    return false;
  }
  inhibit_sleep = true;   // prevent sleep during OTA

  // If the device is already on a WiFi network (e.g. an observer joined in STA
  // mode), serve ElegantOTA on the station IP so it's reachable from the LAN
  // without joining a separate AP. Otherwise raise the MeshCore-OTA SoftAP.
  // force_ap ("start ota ap") always raises the SoftAP, so the OTA UI stays
  // reachable even when the joined network applies client isolation and the
  // station IP can't be reached.
  IPAddress ip;
  const bool use_ap = force_ap || WiFi.status() != WL_CONNECTED
      || static_cast<uint32_t>(WiFi.localIP()) == 0;
  if (!use_ap) {
    ip = WiFi.localIP();
  } else {
    if (!mesh::wifi::accessPointCompatibleWithLongRange()) {
      inhibit_sleep = ota_server != nullptr;
      strcpy(reply, "ERR: stop ESP-NOW before starting an OTA AP");
      return false;
    }
    ota_started_radio = ota_started_radio || WiFi.getMode() == WIFI_OFF;
    ota_started_ap = mesh::wifi::startOpenAccessPoint("MeshCore-OTA", ota_started_ap);
    if (!ota_started_ap) {
      inhibit_sleep = ota_server != nullptr;
      if (!inhibit_sleep) {
        mesh::wifi::stopTemporaryAccessPointRadio(ota_started_radio);
        ota_started_radio = false;
      }
      strcpy(reply, "ERR: OTA WiFi failed");
      return false;
    }
    ip = WiFi.softAPIP();
  }

  snprintf(reply, 160, "Started: http://%s/update - WiFi on; %s", ip.toString().c_str(),
           use_ap ? "Join WiFi MeshCore-OTA" : "Use same WiFi/LAN");
  MESH_DEBUG_PRINTLN("startOTAUpdate: %s", reply);

  // Select the requested network even when the HTTP listener already exists.
  // In particular, `start ota ap` must work after an earlier LAN-mode start.
  if (ota_server != nullptr) {
    if (ota_server->state() != LISTEN) ota_server->begin();
    if (ota_server->state() != LISTEN) {
      strcpy(reply, "ERR: OTA server failed; retry start ota");
      return false;
    }
    return true;
  }

  static char id_buf[60];
  snprintf(id_buf, sizeof(id_buf), "%s (%s)", id, getManufacturerName());
  static char home_buf[90];
  snprintf(home_buf, sizeof(home_buf), "<H2>Hi! I am a MeshCore Repeater. ID: %s</H2>", id);

  if (async_ota_host == nullptr) {
    async_ota_host = new (std::nothrow) AsyncWebServer(80);
    if (async_ota_host == nullptr) {
      if (ota_started_ap) WiFi.softAPdisconnect(true);
      ota_started_ap = false;
      mesh::wifi::stopTemporaryAccessPointRadio(ota_started_radio);
      ota_started_radio = false;
      inhibit_sleep = false;
      strcpy(reply, "ERR: OTA server allocation failed");
      return false;
    }
    async_ota_host->on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
      request->send(200, "text/html", home_buf);
    });
    async_ota_host->on("/log", HTTP_GET, [](AsyncWebServerRequest *request) {
      request->send(SPIFFS, "/packet_log", "text/plain");
    });
    AsyncElegantOTA.begin(async_ota_host);
  }
  ota_server = async_ota_host;
  AsyncElegantOTA.setID(id_buf);
  AsyncElegantOTA.setEnabled(true);
  ota_server->begin();
  if (ota_server->state() != LISTEN) {
    // begin() returns void even when the port could not be bound.
    AsyncElegantOTA.setEnabled(false);
    ota_server->end();
    ota_server = nullptr;
    if (ota_started_ap) WiFi.softAPdisconnect(true);
    ota_started_ap = false;
    mesh::wifi::stopTemporaryAccessPointRadio(ota_started_radio);
    ota_started_radio = false;
    inhibit_sleep = false;
    strcpy(reply, "ERR: OTA server failed; retry start ota");
    return false;
  }

  return true;
}

bool ESP32Board::stopOTAUpdate(char reply[]) {
  if (ota_server == nullptr) {
    if (ota_started_ap) WiFi.softAPdisconnect(true);
    ota_started_ap = false;
    mesh::wifi::stopTemporaryAccessPointRadio(ota_started_radio);
    ota_started_radio = false;
    inhibit_sleep = false;
    strcpy(reply, "OK - OTA not running");
    return true;
  }

  if (!AsyncElegantOTA.setEnabled(false)) {
    strcpy(reply, "ERR: OTA upload active; retry stop ota after it finishes");
    return false;
  }
  ota_server->end();
  ota_server = nullptr;
  if (ota_started_ap) WiFi.softAPdisconnect(true);
  ota_started_ap = false;
  mesh::wifi::stopTemporaryAccessPointRadio(ota_started_radio);
  ota_started_radio = false;
  inhibit_sleep = false;

  strcpy(reply, "OK - OTA stopped");
  MESH_DEBUG_PRINTLN("stopOTAUpdate: %s", reply);

  return true;
}

#else
bool ESP32Board::startOTAUpdate(const char* id, char reply[], bool force_ap) {
  return false; // not supported
}

bool ESP32Board::stopOTAUpdate(char reply[]) {
  return false; // not supported
}
#endif

// ---------------------------------------------------------------------------
// Manifest-driven pull OTA (observer / MQTT-bridge builds only)
//
// The observer already holds a live WiFi station connection (for the MQTT
// bridge) and embeds a root-CA bundle, so it can fetch its own firmware. The
// caller (CommonCLI) stops the MQTT bridge first to free heap/TLS, then calls
// this. We read the web-flasher manifest (config.json), find the `flash-update`
// (app-only) build for our own variant, refuse partition-change releases (OTA
// can't rewrite the partition table), skip if already up to date, then stream
// the .bin straight into the inactive OTA slot.
// ---------------------------------------------------------------------------
#if defined(WITH_MQTT_BRIDGE)
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <Update.h>
#include <ArduinoJson.h>
#include <atomic>
#include <helpers/esp32/SntpOperationCoordinator.h>
#include <helpers/esp32/TlsClockValidity.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_partition.h>
#include <esp_sntp.h>
#include <strings.h>
#include <time.h>
#include "NetworkLink.h"
#include "OtaChannel.h"

// Embedded CA bundle (produced by board_build.embed_files). Weak so non-bundle
// builds still link; we check for presence at runtime.
extern const uint8_t rootca_crt_bundle_start[] asm("_binary_src_certs_x509_crt_bundle_bin_start") __attribute__((weak));
extern const uint8_t rootca_crt_bundle_end[] asm("_binary_src_certs_x509_crt_bundle_bin_end") __attribute__((weak));

static constexpr uint32_t OTA_NTP_SYNC_WAIT_MS = 15000UL;
static constexpr uint32_t OTA_TLS_PROOF_MAX_AGE_MS = 300000UL;
static std::atomic<bool> ota_tls_fresh_ntp_received{false};
static std::atomic<uint32_t> ota_tls_proof_millis{0};
static std::atomic<uint32_t> ota_tls_proof_generation{0};
static std::atomic<uint32_t> ota_tls_expected_generation{0};
static std::atomic<uint32_t> ota_sntp_operation_generation{0};

static void ota_clearNtpCallback() {
  // OperationLease invokes this before publishing the global coordinator as
  // free, so a stale OTA teardown cannot erase another service's callback.
  ota_sntp_operation_generation.store(0, std::memory_order_release);
  sntp_set_time_sync_notification_cb(nullptr);
}

static void ota_noteNtpTime(struct timeval* value) {
  const uint32_t generation =
      ota_sntp_operation_generation.load(std::memory_order_acquire);
  if (!mesh::sntp_coord::processWideCoordinator().owns(generation)) return;
  if (value != nullptr && mesh::tls_clock::timeIsValid(value->tv_sec)) {
    ota_tls_proof_millis.store(millis(), std::memory_order_release);
    ota_tls_proof_generation.store(generation, std::memory_order_release);
    ota_tls_fresh_ntp_received.store(true, std::memory_order_release);
  }
}

static bool ota_tlsClockProofValid() {
  const bool fresh = ota_tls_fresh_ntp_received.load(std::memory_order_acquire);
  const uint32_t proven_at =
      ota_tls_proof_millis.load(std::memory_order_acquire);
  const uint32_t proof_generation =
      ota_tls_proof_generation.load(std::memory_order_acquire);
  const uint32_t expected_generation =
      ota_tls_expected_generation.load(std::memory_order_acquire);
  return mesh::tls_clock::proofGenerationIsValid(
             fresh, proof_generation, expected_generation)
      && mesh::tls_clock::proofIsValid(
             fresh, WiFi.status() == WL_CONNECTED, time(nullptr))
      && mesh::tls_clock::proofAgeIsValid(
             fresh, millis(), proven_at, OTA_TLS_PROOF_MAX_AGE_MS);
}

static bool ota_prepareTlsClock(char reply[]) {
  if (WiFi.status() != WL_CONNECTED) {
    strcpy(reply, "ERR: WiFi disconnected before NTP");
    return false;
  }

  mesh::sntp_coord::OperationLease sntp_operation(
      mesh::sntp_coord::processWideCoordinator(), ota_clearNtpCallback);
  if (!sntp_operation.tryAcquire()) {
    strcpy(reply, "ERR: NTP busy in another firmware service");
    return false;
  }

  // The MQTT bridge has stopped for an actual update, but other firmware
  // services can still need SNTP. The process-wide lease makes this callback
  // and configTime() sequence exclusive. A plausible retained RTC is
  // intentionally not enough: observe a response during this operation.
  ota_tls_fresh_ntp_received.store(false, std::memory_order_release);
  ota_tls_proof_millis.store(0, std::memory_order_release);
  ota_tls_proof_generation.store(0, std::memory_order_release);
  ota_tls_expected_generation.store(
      sntp_operation.generation(), std::memory_order_release);
  ota_sntp_operation_generation.store(
      sntp_operation.generation(), std::memory_order_release);
  sntp_set_time_sync_notification_cb(ota_noteNtpTime);
  sntp_set_sync_status(SNTP_SYNC_STATUS_RESET);
  mesh::usbLoggingPort().println(
      "OTA: requesting fresh NTP time before HTTPS");
  configTime(0, 0, "time.cloudflare.com", "time.google.com", "pool.ntp.org");

  const uint32_t started = millis();
  while (millis() - started < OTA_NTP_SYNC_WAIT_MS) {
    if (WiFi.status() != WL_CONNECTED) {
      strcpy(reply, "ERR: WiFi disconnected during NTP");
      return false;
    }
    const time_t now = time(nullptr);
    if (ota_tlsClockProofValid()) {
      mesh::usbLoggingPort().printf(
          "OTA: fresh NTP time %lld received in %lu ms\n",
          (long long)now, (unsigned long)(millis() - started));
      return true;
    }
    delay(100);
  }

  strcpy(reply, "ERR: fresh NTP time unavailable");
  return false;
}

struct OtaHttpResponse {
  int status;
  size_t content_length;
  bool has_content_length;
};

static bool ota_parseHttpUrl(const char* url, bool require_tls,
                             char* host, size_t host_size,
                             char* path, size_t path_size,
                             uint16_t& port) {
  const char* cursor;
  bool tls;
  if (strncmp(url, "https://", 8) == 0) {
    cursor = url + 8;
    tls = true;
    port = 443;
  } else if (strncmp(url, "http://", 7) == 0) {
    cursor = url + 7;
    tls = false;
    port = 80;
  } else {
    return false;
  }
  if (tls != require_tls) return false;

  const char* slash = strchr(cursor, '/');
  const char* authority_end = slash ? slash : cursor + strlen(cursor);
  const char* colon = nullptr;
  for (const char* p = cursor; p < authority_end; p++) {
    if (*p == ':') colon = p;
  }
  const char* host_end = colon ? colon : authority_end;
  size_t host_len = (size_t)(host_end - cursor);
  if (host_len == 0 || host_len >= host_size) return false;
  memcpy(host, cursor, host_len);
  host[host_len] = 0;

  if (colon) {
    uint32_t parsed_port = 0;
    const char* p = colon + 1;
    if (p == authority_end) return false;
    while (p < authority_end) {
      if (*p < '0' || *p > '9') return false;
      parsed_port = parsed_port * 10U + (uint32_t)(*p++ - '0');
      if (parsed_port > 65535U) return false;
    }
    if (parsed_port == 0) return false;
    port = (uint16_t)parsed_port;
  }

  const char* source_path = slash ? slash : "/";
  size_t path_len = strlen(source_path);
  if (path_len >= path_size) return false;
  memcpy(path, source_path, path_len + 1);
  return true;
}

static bool ota_readHttpLine(Client& client, char* line, size_t line_size) {
  size_t length = 0;
  uint32_t deadline = millis() + 20000;
  while (client.connected() || client.available()) {
    while (client.available()) {
      int c = client.read();
      if (c < 0) break;
      if (c == '\n') {
        if (length && line[length - 1] == '\r') length--;
        line[length] = 0;
        return true;
      }
      if (length + 1 < line_size) line[length++] = (char)c;
    }
    if ((int32_t)(millis() - deadline) >= 0) break;
    delay(1);
  }
  return false;
}

static bool ota_openHttp(Client& client, const char* url, bool require_tls,
                         OtaHttpResponse& response, char reply[]) {
  char host[96];
  char path[224];
  uint16_t port;
  if (!ota_parseHttpUrl(url, require_tls, host, sizeof(host),
                        path, sizeof(path), port)) {
    strcpy(reply, require_tls ? "ERR: invalid HTTPS URL" : "ERR: invalid HTTP URL");
    return false;
  }

  client.setTimeout(20000);
  if (require_tls && !ota_tlsClockProofValid()) {
    strcpy(reply, "ERR: TLS clock proof invalid");
    return false;
  }
  if (!client.connect(host, port)) {
    strcpy(reply, "ERR: HTTP connect failed");
    return false;
  }
  client.printf("GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: MeshCore-OTA\r\n"
                "Accept: */*\r\nAccept-Encoding: identity\r\n"
                "Cache-Control: no-cache\r\nConnection: close\r\n\r\n",
                path, host);

  char line[192];
  if (!ota_readHttpLine(client, line, sizeof(line))) {
    strcpy(reply, "ERR: HTTP response timeout");
    client.stop();
    return false;
  }
  char* status_text = strchr(line, ' ');
  if (strncmp(line, "HTTP/", 5) != 0 || status_text == nullptr) {
    strcpy(reply, "ERR: invalid HTTP response");
    client.stop();
    return false;
  }

  response.status = atoi(status_text + 1);
  response.content_length = 0;
  response.has_content_length = false;
  bool chunked = false;
  bool headers_complete = false;
  while (ota_readHttpLine(client, line, sizeof(line))) {
    if (line[0] == 0) {
      headers_complete = true;
      break;
    }
    if (strncasecmp(line, "Content-Length:", 15) == 0) {
      const char* value = line + 15;
      while (*value == ' ') value++;
      char* end = nullptr;
      unsigned long parsed = strtoul(value, &end, 10);
      while (end != nullptr && *end == ' ') end++;
      if (value == end || end == nullptr || *end != 0) {
        strcpy(reply, "ERR: invalid HTTP length");
        client.stop();
        return false;
      }
      response.content_length = (size_t)parsed;
      response.has_content_length = true;
    } else if (strncasecmp(line, "Transfer-Encoding:", 18) == 0) {
      const char* value = line + 18;
      while (*value == ' ') value++;
      chunked = strncasecmp(value, "chunked", 7) == 0;
    }
  }
  if (!headers_complete) {
    strcpy(reply, "ERR: incomplete HTTP headers");
    client.stop();
    return false;
  }
  if (chunked) {
    strcpy(reply, "ERR: chunked HTTP unsupported");
    client.stop();
    return false;
  }
  return true;
}

static bool ota_fetchManifest(Client& client, const char* url, bool require_tls,
                              JsonDocument& doc, char reply[]) {
  OtaHttpResponse response;
  if (!ota_openHttp(client, url, require_tls, response, reply)) return false;
  if (response.status != 200) {
    snprintf(reply, 160, "ERR: manifest HTTP %d", response.status);
    client.stop();
    return false;
  }
  if (response.has_content_length && response.content_length > 2048) {
    strcpy(reply, "ERR: manifest too large");
    client.stop();
    return false;
  }

  DeserializationError err = deserializeJson(doc, client);
  client.stop();
  if (err) {
    snprintf(reply, 160, "ERR: manifest parse (%s)", err.c_str());
    return false;
  }
  return true;
}

static bool ota_streamFirmware(Client& client, size_t content_length, char reply[]) {
  if (content_length == 0) {
    strcpy(reply, "ERR: empty firmware image");
    return false;
  }
  if (!Update.begin(content_length, U_FLASH)) {
    snprintf(reply, 160, "ERR: OTA begin: %s", Update.errorString());
    return false;
  }

  uint8_t buffer[2048];
  OtaCompatScanner compat;
  size_t received_total = 0;
  int progress_decile = -1;
  uint32_t deadline = millis() + 20000;
  while (received_total < content_length) {
    int available = client.available();
    if (available <= 0) {
      if (!client.connected()) {
        strcpy(reply, "ERR: firmware download incomplete");
        Update.abort();
        return false;
      }
      if ((int32_t)(millis() - deadline) >= 0) {
        strcpy(reply, "ERR: firmware download timeout");
        Update.abort();
        return false;
      }
      delay(1);
      continue;
    }

    size_t wanted = content_length - received_total;
    if (wanted > sizeof(buffer)) wanted = sizeof(buffer);
    if (wanted > (size_t)available) wanted = (size_t)available;
    int received = client.read(buffer, wanted);
    if (received <= 0) {
      if (!client.connected()) {
        strcpy(reply, "ERR: firmware download incomplete");
        Update.abort();
        return false;
      }
      if ((int32_t)(millis() - deadline) >= 0) {
        strcpy(reply, "ERR: firmware download timeout");
        Update.abort();
        return false;
      }
      delay(1);
      continue;
    }
    deadline = millis() + 20000;
    compat.consume(buffer, (size_t)received);
    if (Update.write(buffer, (size_t)received) != (size_t)received) {
      snprintf(reply, 160, "ERR: OTA write: %s", Update.errorString());
      Update.abort();
      return false;
    }
    received_total += (size_t)received;
    int decile = (int)((received_total * 10U) / content_length);
    if (decile != progress_decile) {
      progress_decile = decile;
      mesh::usbLoggingPort().printf("OTA: %d%%\n", decile * 10);
    }
  }

  {
    OtaCompat own = {0, 0}, target = {0, 0};
    const char* volatile own_tag = ota_compat_tag;  // retain the tag in the linked image
    const bool own_valid = ota_compat_parse(own_tag + sizeof(OTA_COMPAT_TAG) - 1, &own);
    const bool tagged = compat.finish(&target);
    if (!own_valid || !tagged || !ota_compat_ok(own, target)) {
      // Update.end() validates the image and selects its boot slot. Refuse before
      // that commit, so even a reset or power loss cannot boot an unsafe target.
      Update.abort();
      if (!own_valid || !tagged) {
        strcpy(reply, "ERR: OTA refused: invalid or missing compatibility tag; cable flash");
      } else {
        snprintf(reply, 160, "ERR: OTA refused: target compat %d/%x cannot preserve node %d/%x; cable flash",
                 target.gen, (unsigned)target.caps, own.gen, (unsigned)own.caps);
      }
      return false;
    }
  }

  if (!Update.end()) {
    snprintf(reply, 160, "ERR: OTA finish: %s", Update.errorString());
    return false;
  }
  return true;
}

// Extract the trailing build hash. For a filename we first drop a ".bin"
// suffix, then take the token after the last '-'. Works for both the manifest
// asset name ("...-v1.16.0-8b084d5.bin" -> "8b084d5") and the embedded
// FIRMWARE_VERSION ("v1.16.0-observer-8b084d5" -> "8b084d5").
static void ota_extractHash(const char* s, char* out, size_t out_sz) {
  if (!s) { if (out_sz) out[0] = 0; return; }
  size_t len = strlen(s);
  if (len > 4 && strcmp(s + len - 4, ".bin") == 0) len -= 4;
  size_t i = len;
  while (i > 0 && s[i - 1] != '-') i--;
  size_t n = len - i;
  if (n >= out_sz) n = out_sz - 1;
  memcpy(out, s + i, n);
  out[n] = 0;
}

// Split a version token "vMAJOR.MINOR.PATCH[.BUILD]" into its base
// ("vMAJOR.MINOR.PATCH") and build number (BUILD, or -1 if there's no 4th
// component). The base has exactly two dots; a third dot introduces the build.
static void ota_parseVersion(const char* ver, char* base_out, size_t base_sz, int* build_out) {
  *build_out = -1;
  if (base_sz) base_out[0] = 0;
  if (!ver) return;
  int dots = 0, third_dot = -1;
  for (int j = 0; ver[j]; j++) {
    if (ver[j] == '.' && ++dots == 3) { third_dot = j; break; }
  }
  if (third_dot >= 0) {
    size_t n = (size_t)third_dot;
    if (n >= base_sz) n = base_sz - 1;
    memcpy(base_out, ver, n);
    base_out[n] = 0;
    *build_out = atoi(ver + third_dot + 1);
  } else {
    strncpy(base_out, ver, base_sz - 1);
    base_out[base_sz - 1] = 0;
  }
}

// Canonical signature of the FLASHED partition table - MUST match
// scripts/partition_signature.py: each entry "type:subtype:offset:size" in
// lowercase hex, sorted by offset, joined by ','. Lets `ota update` compare the
// target build's partition layout (carried in the manifest as partSig) against
// what's actually on this device, instead of a blanket per-variant flag.
static void ota_partitionSignature(char* out, size_t out_sz) {
  struct PE { uint8_t type, subtype; uint32_t off, size; } e[24];
  int n = 0;
  esp_partition_iterator_t it =
      esp_partition_find(ESP_PARTITION_TYPE_ANY, ESP_PARTITION_SUBTYPE_ANY, nullptr);
  while (it != nullptr && n < (int)(sizeof(e) / sizeof(e[0]))) {
    const esp_partition_t* p = esp_partition_get(it);
    e[n].type = (uint8_t)p->type;
    e[n].subtype = (uint8_t)p->subtype;
    e[n].off = p->address;
    e[n].size = p->size;
    n++;
    it = esp_partition_next(it);
  }
  esp_partition_iterator_release(it);  // safe on NULL (loop exhausted)
  // insertion sort by offset (matches the script's sort key)
  for (int i = 1; i < n; i++) {
    PE k = e[i];
    int j = i - 1;
    while (j >= 0 && e[j].off > k.off) { e[j + 1] = e[j]; j--; }
    e[j + 1] = k;
  }
  size_t pos = 0;
  if (out_sz) out[0] = 0;
  for (int i = 0; i < n && pos + 1 < out_sz; i++) {
    pos += snprintf(out + pos, out_sz - pos, "%s%x:%x:%x:%x",
                    i ? "," : "", e[i].type, e[i].subtype, (unsigned)e[i].off, (unsigned)e[i].size);
  }
}

// Parameters handed to the worker task; lives on otaFromManifest()'s stack,
// which stays valid because that function blocks until the worker signals done.
struct OtaTaskArgs {
  ESP32Board* self;
  const char* manifest_base;
  const char* current_ver;
  bool dry_run;
  char* reply;
  volatile bool result;
  volatile bool done;
};

static void ota_task_entry(void* param) {
  OtaTaskArgs* a = static_cast<OtaTaskArgs*>(param);
  a->result = a->self->otaFromManifestImpl(a->manifest_base, a->current_ver, a->dry_run, a->reply);
  a->done = true;        // on a successful `ota update` we reboot before reaching here
  vTaskDelete(nullptr);
}

bool ESP32Board::otaFromManifest(const char* manifest_base, const char* current_ver, bool dry_run, char reply[]) {
  // The TLS handshake (cert-bundle verify), JSON parse, and flash stream use far more
  // stack than the ~8 KB loop task offers - especially when reached via the deep
  // mesh-receive call chain (it overflows the loopTask canary). Run the work in a
  // dedicated 24 KB-stack task and block here until it finishes. The big stack is
  // freed when the task exits; on a successful update the chip reboots inside it.
  struct NetworkSwitchGuard {
    NetworkLink& link;
    NetworkSwitchGuard() : link(activeNetworkLink()) { link.lockSwitching(); }
    ~NetworkSwitchGuard() { link.unlockSwitching(); }
  } network_guard;
  OtaTaskArgs args = { this, manifest_base, current_ver, dry_run, reply, false, false };
  TaskHandle_t handle = nullptr;
  BaseType_t ok = xTaskCreatePinnedToCore(ota_task_entry, "ota", 24576, &args, 5, &handle, 1);
  if (ok != pdPASS) {
    strcpy(reply, "ERR: OTA task spawn failed");
    return false;
  }
  while (!args.done) {
    delay(50);  // Arduino delay() yields to other tasks
  }
  return args.result;
}

bool ESP32Board::otaFromManifestImpl(const char* manifest_base, const char* current_ver, bool dry_run, char reply[]) {
#if !defined(OTA_MANIFEST_BASE) || !defined(OTA_VARIANT)
  strcpy(reply, "ERR: OTA not configured (build via build.sh)");
  return false;
#else
  if (manifest_base == nullptr || *manifest_base == 0) {
    strcpy(reply, "ERR: OTA manifest base missing");
    return false;
  }
  if (WiFi.status() != WL_CONNECTED) {
    strcpy(reply, "ERR: WiFi not connected");
    return false;
  }

  size_t bundle_len = 0;
  if (rootca_crt_bundle_start != nullptr && rootca_crt_bundle_end != nullptr &&
      rootca_crt_bundle_end > rootca_crt_bundle_start) {
    bundle_len = (size_t)(rootca_crt_bundle_end - rootca_crt_bundle_start);
  }
  if (!dry_run && bundle_len == 0) {
    strcpy(reply, "ERR: no embedded cert bundle");
    return false;
  }
  if (!dry_run && !ota_prepareTlsClock(reply)) {
    return false;
  }

  // --- Fetch this variant's slim manifest ----------------------------------
  // <OTA_MANIFEST_BASE>/<OTA_VARIANT>.json - a ~180 byte per-variant file, not the
  // full config.json.
  char murl[200];
  JsonDocument doc;

  if (dry_run) {
    // `ota check`: fetch over PLAIN HTTP. With no TLS handshake the fetch costs
    // negligible heap, so the check runs with the MQTT bridge UP even on no-PSRAM
    // - where the cert-bundle TLS verify would otherwise exhaust internal heap
    // alongside the two live MQTT TLS sessions (free heap collapses to a few KB
    // and the handshake + the bridge both fail). This only reads version info; the
    // firmware download below (ota update) is always TLS-verified. Requires the
    // manifest host to serve /v over HTTP (no forced HTTPS redirect).
    int url_len;
    if (strncmp(manifest_base, "https://", 8) == 0) {
      url_len = snprintf(murl, sizeof(murl), "http://%s/%s.json", manifest_base + 8, OTA_VARIANT);
    } else {
      url_len = snprintf(murl, sizeof(murl), "%s/%s.json", manifest_base, OTA_VARIANT);
    }
    if (url_len < 0 || (size_t)url_len >= sizeof(murl)) {
      strcpy(reply, "ERR: manifest URL too long");
      return false;
    }
    WiFiClient mclient;
    if (!ota_fetchManifest(mclient, murl, false, doc, reply)) {
      return false;
    }
  } else {
    // `ota update`: HTTPS. The bridge is torn down for an update so heap is free,
    // and integrity matters because we're about to flash.
    WiFiClientSecure mclient;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    mclient.setCACertBundle(rootca_crt_bundle_start, bundle_len);
#else
    mclient.setCACertBundle(rootca_crt_bundle_start);
#endif
    const int url_len = snprintf(murl, sizeof(murl), "%s/%s.json", manifest_base, OTA_VARIANT);
    if (url_len < 0 || (size_t)url_len >= sizeof(murl)) {
      strcpy(reply, "ERR: manifest URL too long");
      return false;
    }
    mesh::usbLoggingPort().printf("OTA: downloading manifest %s\n", murl);
    if (!ota_fetchManifest(mclient, murl, true, doc, reply)) {
      return false;
    }
  }

  // Copy fields out before the document is reused/cleared.
  char file_url[200] = {0}, avail_version[40] = {0}, avail_base[40] = {0}, avail_hash[24] = {0};
  const char* manifest_file = doc["file"] | "";
  if (strlen(manifest_file) >= sizeof(file_url)) {
    strcpy(reply, "ERR: firmware URL too long");
    return false;
  }
  strncpy(file_url, manifest_file, sizeof(file_url) - 1);
  strncpy(avail_version, doc["version"] | "", sizeof(avail_version) - 1);
  strncpy(avail_base, doc["baseVersion"] | "", sizeof(avail_base) - 1);
  strncpy(avail_hash, doc["hash"] | "", sizeof(avail_hash) - 1);
  int avail_build = doc["build"] | -1;
  bool legacy_partition_change = doc["partitionChange"] | false;
  char manifest_partsig[256] = {0};
  strncpy(manifest_partsig, doc["partSig"] | "", sizeof(manifest_partsig) - 1);
  doc.clear();

  if (!file_url[0]) {
    strcpy(reply, "ERR: manifest missing file");
    return false;
  }

  // Partition compatibility: prefer the precise per-build signature - compare the
  // target build's partition layout (manifest partSig) to what's actually flashed
  // on THIS device. Refuse only on a real mismatch (OTA can't rewrite the table).
  // Fall back to the legacy bool for manifests that predate partSig.
  bool partition_change;
  if (manifest_partsig[0]) {
    char dev_partsig[256];
    ota_partitionSignature(dev_partsig, sizeof(dev_partsig));
    partition_change = (strcmp(dev_partsig, manifest_partsig) != 0);
  } else {
    partition_change = legacy_partition_change;
  }

  // --- Determine current-vs-available --------------------------------------
  // Our running version token (e.g. "v1.16.0.5"), i.e. current_ver up to the
  // first '-' (which precedes "-observer-<hash>").
  char own_version[40] = {0};
  for (size_t i = 0; current_ver && current_ver[i] && current_ver[i] != '-' && i < sizeof(own_version) - 1; i++) {
    own_version[i] = current_ver[i];
  }
  char own_base[40];
  int own_build;
  ota_parseVersion(own_version, own_base, sizeof(own_base), &own_build);

  // Fallback identity by commit hash (handles pre-build-number / local images that
  // carry no 4th version component). Shared-prefix compare absorbs the 7- vs 8-char
  // git abbreviation difference.
  char cur_hash[24];
  ota_extractHash(current_ver, cur_hash, sizeof(cur_hash));
  size_t lh = strlen(avail_hash), lc = strlen(cur_hash);
  size_t m = (lh < lc) ? lh : lc;
  bool hash_equal = (m >= 7 && strncmp(avail_hash, cur_hash, m) == 0);

  bool same_base = (own_base[0] && avail_base[0] && strcmp(own_base, avail_base) == 0);
  bool have_builds = (own_build >= 0 && avail_build >= 0);
  bool diff_base = (own_base[0] && avail_base[0] && !same_base);
  // Another channel's image always differs (its native base does), even from the same
  // commit; build counters are per channel, so build numbers only compare natively.
  bool cross_channel = (strcmp(manifest_base, OTA_MANIFEST_BASE) != 0);

  int behind = 0;
  bool up_to_date;
  if (cross_channel) {
    up_to_date = false;
  } else if (same_base && have_builds) {
    behind = avail_build - own_build;
    up_to_date = (behind <= 0);
  } else if (diff_base) {
    up_to_date = false;  // different base version is always an update
  } else {
    up_to_date = hash_equal;  // unknown build numbers -> fall back to hash
  }

  // Display strings carry the short commit hash in the same form as the asset
  // filename, e.g. "v1.16.0.2 (5acfdd7)" (or just the hash if there's no version).
  char avail_disp[72], own_disp[72];
  if (avail_version[0]) snprintf(avail_disp, sizeof(avail_disp), "%s (%s)", avail_version, avail_hash);
  else                  snprintf(avail_disp, sizeof(avail_disp), "%s", avail_hash);
  if (own_version[0])   snprintf(own_disp, sizeof(own_disp), "%s (%s)", own_version, cur_hash);
  else                  snprintf(own_disp, sizeof(own_disp), "%s", cur_hash);
  const char* pc_note = partition_change ? " [partition change: cable flash]" : "";

  // --- Report (dry run / `ota check`) --------------------------------------
  // Returns true iff an OTA-applicable update is available (not up-to-date and not
  // a partition-change build). `ota check` ignores the return and just shows the
  // reply; `ota update` uses it to decide whether to actually schedule the flash.
  if (dry_run) {
    if (up_to_date) {
      snprintf(reply, 160, "up to date: %s", avail_disp);
    } else if (cross_channel) {
      snprintf(reply, 160, "update available: %s -> %s (channel switch)%s", own_disp, avail_disp, pc_note);
    } else if (same_base && have_builds) {
      snprintf(reply, 160, "update available: %s -> %s (%d behind)%s", own_disp, avail_disp, behind, pc_note);
    } else if (diff_base) {
      snprintf(reply, 160, "update available: %s -> %s (new base)%s", own_disp, avail_disp, pc_note);
    } else {
      snprintf(reply, 160, "update available: %s -> %s%s", own_disp, avail_disp, pc_note);
    }
    return (!up_to_date && !partition_change);
  }

  // --- Gates (real `ota update`) -------------------------------------------
  if (partition_change) {
    snprintf(reply, 160, "ERR: %s needs cable flash (partition change)", avail_disp);
    return false;
  }
  if (up_to_date) {
    snprintf(reply, 160, "OK: already up to date: %s", avail_disp);
    return false;
  }

  // --- Stream the .bin (the manifest's full URL) into the inactive OTA slot -
  mesh::usbLoggingPort().printf(
      "OTA: update %s -> %s\n", own_disp, avail_disp);
  mesh::usbLoggingPort().printf("OTA: downloading %s\n", file_url);
  inhibit_sleep = true;  // keep awake through the flash

  WiFiClientSecure uclient;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  uclient.setCACertBundle(rootca_crt_bundle_start, bundle_len);
#else
  uclient.setCACertBundle(rootca_crt_bundle_start);
#endif

  OtaHttpResponse response;
  if (!ota_openHttp(uclient, file_url, true, response, reply)) {
    inhibit_sleep = false;
    mesh::usbLoggingPort().printf("OTA: FAILED - %s\n", reply);
    return false;
  }
  if (response.status != 200) {
    inhibit_sleep = false;
    snprintf(reply, 160, "ERR: firmware HTTP %d", response.status);
    uclient.stop();
    mesh::usbLoggingPort().printf("OTA: FAILED - %s\n", reply);
    return false;
  }
  if (!response.has_content_length) {
    inhibit_sleep = false;
    strcpy(reply, "ERR: firmware size missing");
    uclient.stop();
    mesh::usbLoggingPort().printf("OTA: FAILED - %s\n", reply);
    return false;
  }
  if (!ota_streamFirmware(uclient, response.content_length, reply)) {
    inhibit_sleep = false;
    uclient.stop();
    mesh::usbLoggingPort().printf("OTA: FAILED - %s\n", reply);
    return false;
  }

  uclient.stop();
  mesh::usbLoggingPort().println("OTA: write complete, rebooting...");
  delay(250);
  esp_restart();
  return true;  // unreachable
#endif  // OTA_MANIFEST_BASE && OTA_VARIANT
}
#else
bool ESP32Board::otaFromManifest(const char* manifest_base, const char* current_ver, bool dry_run, char reply[]) {
  strcpy(reply, "ERR: not supported");
  return false;
}
#endif  // WITH_MQTT_BRIDGE

void ESP32Board::shutdownPeripherals() {
  // Power off the display if any
#ifdef DISPLAY_CLASS
  display.turnOff();
#endif

  // Power off LoRa
  radio_driver.powerOff();

  #ifdef P_LORA_NSS
  // Keep LoRa inactive during deepsleep
  digitalWrite(P_LORA_NSS, HIGH);
#if defined(CONFIG_IDF_TARGET_ESP32C3) || defined(CONFIG_IDF_TARGET_ESP32C6)
  gpio_hold_en((gpio_num_t)P_LORA_NSS);
#else
  rtc_gpio_hold_en((gpio_num_t)P_LORA_NSS);
#endif
  #endif

  // Power off GPS if any
  if (sensors.getLocationProvider() != NULL) {
    sensors.getLocationProvider()->stop();
  }

  // Native USB must not wait for a host which has stopped reading.
  // Keep the original flush behavior for UART targets.
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
  mesh::serviceUsbTerminalPort();
#else
  Serial.flush();
#endif
  delay(100);
}

void ESP32Board::powerOff() {
  enterDeepSleep(0); // Do not wakeup
}

void ESP32Board::enterDeepSleep(uint32_t secs) {
  shutdownPeripherals();

  // Clear stale wakeup sources to avoid ghost wakeup
  // This is required when Power Management and automatic lightsleep are enabled
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);

  if (secs > 0) {
    esp_sleep_enable_timer_wakeup(secs * 1000000ULL);
  }

  // Finally set ESP32 into deepsleep
  esp_deep_sleep_start(); // CPU halts here and never returns!
}
#endif
