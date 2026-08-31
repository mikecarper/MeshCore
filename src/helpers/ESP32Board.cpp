#ifdef ESP_PLATFORM

#include "ESP32Board.h"
#include <target.h>
#include "UsbLogging.h"
#include "UserGpioPinPolicy.h"

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

#if defined(ADMIN_PASSWORD) && defined(LIGHTWEIGHT_WIFI_OTA)
#include <WiFi.h>
#include <Update.h>
#include <SPIFFS.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <strings.h>

static bool lightweight_ota_started_ap;

static const char LIGHTWEIGHT_OTA_PAGE[] = R"HTML(<!doctype html>
<html><head><meta name="viewport" content="width=device-width"><title>MeshCore OTA</title>
<style>body{font-family:sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem}button,input{font-size:1rem;margin:.5rem 0}pre{white-space:pre-wrap}</style></head>
<body><h2>MeshCore firmware update</h2><input id="file" type="file" accept=".bin,application/octet-stream"><br>
<button id="upload">Upload and reboot</button><pre id="status"></pre><script>
const f=document.getElementById('file'),s=document.getElementById('status'),b=document.getElementById('upload');
b.onclick=()=>{if(!f.files.length){s.textContent='Select a firmware .bin file.';return}b.disabled=true;
const x=new XMLHttpRequest();x.open('POST','/update');x.setRequestHeader('Content-Type','application/octet-stream');
x.upload.onprogress=e=>{if(e.lengthComputable)s.textContent='Uploading '+Math.round(e.loaded*100/e.total)+'%'};
x.onload=()=>{s.textContent=x.responseText;b.disabled=false};x.onerror=()=>{s.textContent='Upload failed';b.disabled=false};x.send(f.files[0])};
</script></body></html>)HTML";

class LightweightOTAServer {
  WiFiServer server{80};
  TaskHandle_t task = nullptr;
  volatile bool running = false;
  ESP32Board* board = nullptr;

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
          if (length && line[length - 1] == '\r') length--;
          line[length] = 0;
          return true;
        }
        if (length + 1 < capacity) line[length++] = static_cast<char>(c);
      }
      if (static_cast<int32_t>(millis() - deadline) >= 0) break;
      delay(1);
    }
    return false;
  }

  void sendPage(WiFiClient& client) {
    sendResponse(client, 200, "OK", "text/html", LIGHTWEIGHT_OTA_PAGE,
                 strlen(LIGHTWEIGHT_OTA_PAGE));
  }

  void sendLog(WiFiClient& client) {
    File log = SPIFFS.open("/packet_log", FILE_READ);
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
      if (Update.write(buf, static_cast<size_t>(received)) != static_cast<size_t>(received)) {
        sendUpdateError(client, 500, "Internal Server Error");
        Update.abort();
        return;
      }
      received_total += static_cast<size_t>(received);
    }

    if (received_total != total) {
      Update.abort();
      static const char incomplete[] = "firmware upload incomplete";
      sendResponse(client, 408, "Request Timeout", "text/plain", incomplete, sizeof(incomplete) - 1);
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
    if (!readLine(client, line, sizeof(line))) return;
    bool get_page = strncmp(line, "GET / ", 6) == 0 || strncmp(line, "GET /update ", 12) == 0;
    bool get_log = strncmp(line, "GET /log ", 9) == 0;
    bool post_update = strncmp(line, "POST /update ", 13) == 0;

    size_t content_length = 0;
    do {
      if (!readLine(client, line, sizeof(line))) return;
      if (strncasecmp(line, "Content-Length:", 15) == 0) {
        content_length = static_cast<size_t>(strtoul(line + 15, nullptr, 10));
      }
    } while (line[0]);

    if (get_page) sendPage(client);
    else if (get_log) sendLog(client);
    else if (post_update) receiveUpdate(client, content_length);
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
    board = owner;
    server.begin();
    server.setNoDelay(true);
    running = true;
    if (xTaskCreate(taskEntry, "ota-http", 6144, this, 4, &task) != pdPASS) {
      running = false;
      server.stop();
      task = nullptr;
      return false;
    }
    return true;
  }

  void end() {
    running = false;
    server.stop();
    for (unsigned i = 0; task != nullptr && i < 100; i++) delay(10);
    board = nullptr;
  }
};

static LightweightOTAServer lightweight_ota_server;

bool ESP32Board::startOTAUpdate(const char* id, char reply[], bool force_ap) {
  (void)id;
  inhibit_sleep = true;

  IPAddress ip;
  if (!force_ap && WiFi.status() == WL_CONNECTED) {
    ip = WiFi.localIP();
  } else {
    if (!lightweight_ota_started_ap) {
      const IPAddress ap_ip(192, 168, 4, 1);
      const IPAddress ap_mask(255, 255, 255, 0);
      lightweight_ota_started_ap = WiFi.softAPConfig(ap_ip, ap_ip, ap_mask)
          && WiFi.softAP("MeshCore-OTA", nullptr);
    }
    if (!lightweight_ota_started_ap) {
      inhibit_sleep = false;
      strcpy(reply, "ERR: OTA WiFi failed");
      return false;
    }
    ip = WiFi.softAPIP();
  }

  if (ota_server == nullptr) {
    if (!lightweight_ota_server.begin(this)) {
      if (lightweight_ota_started_ap) WiFi.softAPdisconnect(true);
      lightweight_ota_started_ap = false;
      inhibit_sleep = false;
      strcpy(reply, "ERR: OTA server failed");
      return false;
    }
    ota_server = &lightweight_ota_server;
  }

  snprintf(reply, 160, "Started: http://%s/update", ip.toString().c_str());
  MESH_DEBUG_PRINTLN("startOTAUpdate: %s", reply);
  return true;
}

bool ESP32Board::stopOTAUpdate(char reply[]) {
  if (ota_server == nullptr) {
    strcpy(reply, "OK - OTA not running");
    return true;
  }

  lightweight_ota_server.end();
  ota_server = nullptr;
  if (lightweight_ota_started_ap) WiFi.softAPdisconnect(true);
  lightweight_ota_started_ap = false;
  inhibit_sleep = false;
  strcpy(reply, "OK - OTA stopped");
  MESH_DEBUG_PRINTLN("stopOTAUpdate: %s", reply);
  return true;
}

#elif defined(ADMIN_PASSWORD) && !defined(DISABLE_WIFI_OTA)   // Repeater or Room Server only
#include <WiFi.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <AsyncElegantOTA.h>

#include <SPIFFS.h>

bool ESP32Board::startOTAUpdate(const char* id, char reply[], bool force_ap) {
  inhibit_sleep = true;   // prevent sleep during OTA

  if (ota_server != nullptr) {   // already running (idempotent restart)
    IPAddress cur_ip = (WiFi.status() == WL_CONNECTED) ? WiFi.localIP() : WiFi.softAPIP();
    sprintf(reply, "Started: http://%s/update", cur_ip.toString().c_str());
    return true;
  }

  // If the device is already on a WiFi network (e.g. an observer joined in STA
  // mode), serve ElegantOTA on the station IP so it's reachable from the LAN
  // without joining a separate AP. Otherwise raise the MeshCore-OTA SoftAP.
  // force_ap ("start ota ap") always raises the SoftAP, so the OTA UI stays
  // reachable even when the joined network applies client isolation and the
  // station IP can't be reached.
  IPAddress ip;
  if (!force_ap && WiFi.status() == WL_CONNECTED) {
    ip = WiFi.localIP();
  } else {
    const IPAddress ap_ip(192, 168, 4, 1);
    const IPAddress ap_mask(255, 255, 255, 0);
    if (!WiFi.softAPConfig(ap_ip, ap_ip, ap_mask)
        || !WiFi.softAP("MeshCore-OTA", NULL)) {
      inhibit_sleep = false;
      strcpy(reply, "ERR: OTA WiFi failed");
      return false;
    }
    ip = WiFi.softAPIP();
  }

  sprintf(reply, "Started: http://%s/update", ip.toString().c_str());
  MESH_DEBUG_PRINTLN("startOTAUpdate: %s", reply);

  static char id_buf[60];
  sprintf(id_buf, "%s (%s)", id, getManufacturerName());
  static char home_buf[90];
  sprintf(home_buf, "<H2>Hi! I am a MeshCore Repeater. ID: %s</H2>", id);

  ota_server = new AsyncWebServer(80);

  ota_server->on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send(200, "text/html", home_buf);
  });
  ota_server->on("/log", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send(SPIFFS, "/packet_log", "text/plain");
  });

  AsyncElegantOTA.setID(id_buf);
  AsyncElegantOTA.begin(ota_server);    // Start ElegantOTA
  ota_server->begin();

  return true;
}

bool ESP32Board::stopOTAUpdate(char reply[]) {
  if (ota_server == nullptr) {
    strcpy(reply, "OK - OTA not running");
    return true;
  }

  ota_server->end();
  delete ota_server;
  ota_server = nullptr;
  WiFi.softAPdisconnect(true);
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
    if (received <= 0) continue;
    deadline = millis() + 20000;
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
  const char* current_ver;
  bool dry_run;
  char* reply;
  volatile bool result;
  volatile bool done;
};

static void ota_task_entry(void* param) {
  OtaTaskArgs* a = static_cast<OtaTaskArgs*>(param);
  a->result = a->self->otaFromManifestImpl(a->current_ver, a->dry_run, a->reply);
  a->done = true;        // on a successful `ota update` we reboot before reaching here
  vTaskDelete(nullptr);
}

bool ESP32Board::otaFromManifest(const char* current_ver, bool dry_run, char reply[]) {
  // The TLS handshake (cert-bundle verify), JSON parse, and flash stream use far more
  // stack than the ~8 KB loop task offers - especially when reached via the deep
  // mesh-receive call chain (it overflows the loopTask canary). Run the work in a
  // dedicated 24 KB-stack task and block here until it finishes. The big stack is
  // freed when the task exits; on a successful update the chip reboots inside it.
  OtaTaskArgs args = { this, current_ver, dry_run, reply, false, false };
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

bool ESP32Board::otaFromManifestImpl(const char* current_ver, bool dry_run, char reply[]) {
#if !defined(OTA_MANIFEST_BASE) || !defined(OTA_VARIANT)
  strcpy(reply, "ERR: OTA not configured (build via build.sh)");
  return false;
#else
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
    if (strncmp(OTA_MANIFEST_BASE, "https://", 8) == 0) {
      snprintf(murl, sizeof(murl), "http://%s/%s.json", OTA_MANIFEST_BASE + 8, OTA_VARIANT);
    } else {
      snprintf(murl, sizeof(murl), "%s/%s.json", OTA_MANIFEST_BASE, OTA_VARIANT);
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
    snprintf(murl, sizeof(murl), "%s/%s.json", OTA_MANIFEST_BASE, OTA_VARIANT);
    mesh::usbLoggingPort().printf("OTA: downloading manifest %s\n", murl);
    if (!ota_fetchManifest(mclient, murl, true, doc, reply)) {
      return false;
    }
  }

  // Copy fields out before the document is reused/cleared.
  char file_url[200] = {0}, avail_version[40] = {0}, avail_base[40] = {0}, avail_hash[24] = {0};
  strncpy(file_url, doc["file"] | "", sizeof(file_url) - 1);
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

  int behind = 0;
  bool up_to_date;
  if (same_base && have_builds) {
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
bool ESP32Board::otaFromManifest(const char* current_ver, bool dry_run, char reply[]) {
  strcpy(reply, "ERR: not supported");
  return false;
}
#endif  // WITH_MQTT_BRIDGE

void ESP32Board::powerOff() {
  enterDeepSleep(0); // Do not wakeup
}

void ESP32Board::enterDeepSleep(uint32_t secs) {
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

  // Flush serial buffers
  Serial.flush();
  delay(100);

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
