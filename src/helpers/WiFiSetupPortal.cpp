#include "WiFiSetupPortal.h"

#if defined(ESP32_PLATFORM) && defined(WIFI_SSID)

#include <WiFi.h>
#include <helpers/esp32/StaticHtml.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <esp_wifi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <strings.h>
#include <helpers/CLICommandUtils.h>
#include <helpers/UsbLogging.h>
#include <helpers/esp32/WiFiRadioPolicy.h>
#include <helpers/esp32/WiFiAccessPointPolicy.h>
#include <helpers/WirelessControl.h>
#include <helpers/esp32/WiFiStationPolicy.h>
#include <helpers/esp32/ManagementPassword.h>

namespace {

static const IPAddress SETUP_IP(192, 168, 4, 1);
static const uint32_t CONNECT_TIMEOUT_MS = 20000;
// The rendered success page remains in the browser after the setup SSID
// disappears; allow one second for the HTTP response to flush first.
static const uint32_t AP_CLOSE_DELAY_MS = 1000;

struct PortalImpl {
  WiFiServer server{80};
  DNSServer dns;
  TaskHandle_t task = nullptr;
  bool started_radio = false;
  WiFiSetupPortal::SaveCallback save_callback = nullptr;
  void* callback_context = nullptr;
  volatile bool* active = nullptr;
  uint32_t close_ap_at = 0;
  char ap_name[33] = "MeshCore-Setup";
  char recovery_ssid[32] = "";
  char recovery_password[65] = "";
  uint32_t recovery_interval_ms = 0;
  uint32_t next_recovery_at = 0;
  uint32_t recovery_deadline = 0;
  bool recovery_connecting = false;
  uint32_t ap_password_generation = 0;
  uint32_t rekey_generation = 0;
  uint32_t rekey_at = 0;
};

static bool readLine(WiFiClient& client, char* line, size_t capacity) {
  size_t length = 0;
  uint32_t deadline = millis() + 5000;
  while (client.connected()) {
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

static void sendResponse(WiFiClient& client, int code, const char* reason,
                         const char* content_type, const char* body) {
  size_t length = strlen(body);
  client.printf("HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %u\r\n"
                "Connection: close\r\nCache-Control: no-store\r\n\r\n",
                code, reason, content_type, static_cast<unsigned>(length));
  client.write(reinterpret_cast<const uint8_t*>(body), length);
}

static int hexValue(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

static bool decodeFormValue(const char* encoded, size_t encoded_len,
                            char* output, size_t output_size) {
  if (!output || output_size == 0) return false;
  size_t out = 0;
  for (size_t i = 0; i < encoded_len; i++) {
    char c = encoded[i];
    if (c == '+') {
      c = ' ';
    } else if (c == '%' && i + 2 < encoded_len) {
      int hi = hexValue(encoded[i + 1]);
      int lo = hexValue(encoded[i + 2]);
      if (hi < 0 || lo < 0) return false;
      c = static_cast<char>((hi << 4) | lo);
      i += 2;
    }
    if (c == 0 || out + 1 >= output_size) return false;
    output[out++] = c;
  }
  output[out] = 0;
  return true;
}

static bool getFormField(const char* body, const char* name,
                         char* output, size_t output_size) {
  size_t name_len = strlen(name);
  const char* field = body;
  while (field && *field) {
    const char* end = strchr(field, '&');
    size_t field_len = end ? static_cast<size_t>(end - field) : strlen(field);
    if (field_len >= name_len + 1 && field[name_len] == '='
        && strncmp(field, name, name_len) == 0) {
      return decodeFormValue(field + name_len + 1, field_len - name_len - 1,
                             output, output_size);
    }
    field = end ? end + 1 : nullptr;
  }
  if (output_size) output[0] = 0;
  return false;
}

static bool hasFormField(const char* body, const char* name) {
  if (!body || !name) return false;
  const size_t name_len = strlen(name);
  const char* field = body;
  while (*field) {
    const char* end = strchr(field, '&');
    const size_t field_len = end
        ? static_cast<size_t>(end - field) : strlen(field);
    if (field_len >= name_len + 1 && field[name_len] == '='
        && strncmp(field, name, name_len) == 0) {
      return true;
    }
    field = end ? end + 1 : nullptr;
    if (!field) break;
  }
  return false;
}

static void handleSave(PortalImpl* impl, WiFiClient& client, const char* body) {
  char ssid[32];
  char password[65];
  if (!getFormField(body, "ssid", ssid, sizeof(ssid)) || ssid[0] == 0) {
    sendResponse(client, 400, "Bad Request", "text/plain", "A valid SSID is required.");
    return;
  }
  // Browsers omit an empty password only if the form is modified. Treat either
  // an empty field or an absent one as an open network.
  if (hasFormField(body, "password")) {
    if (!getFormField(body, "password", password, sizeof(password))) {
      sendResponse(client, 400, "Bad Request", "text/plain",
                   "WiFi password must be 0-63 characters or exactly 64 hexadecimal characters.");
      return;
    }
  } else {
    password[0] = 0;
  }
  if (!mesh::cli::standaloneWiFiPasswordValid(password)) {
    sendResponse(client, 400, "Bad Request", "text/plain",
                 "WiFi password must be 0-63 characters or exactly 64 hexadecimal characters.");
    return;
  }

  // A submitted configuration takes priority over a background retry of the
  // previously saved network.
  impl->recovery_connecting = false;
  mesh::usbLoggingPort().printf(
      "WiFi setup: connecting to '%s'...\n", ssid);
  mesh::wifi::beginStation(ssid, password);
  uint32_t deadline = millis() + CONNECT_TIMEOUT_MS;
  while (impl->active && *impl->active && WiFi.status() != WL_CONNECTED
         && static_cast<int32_t>(millis() - deadline) < 0) {
    impl->dns.processNextRequest();
    delay(50);
  }

  if (WiFi.status() != WL_CONNECTED) {
    WiFi.disconnect();
    sendResponse(client, 200, "OK", "text/html",
                 "<!doctype html><meta name=viewport content='width=device-width'><h2>Connection failed</h2>"
                 "<p>Check the SSID and password, then <a href='/'>try again</a>.</p>");
    mesh::usbLoggingPort().println(
        "WiFi setup: connection failed; setup AP remains active");
    return;
  }

  if (impl->save_callback && !impl->save_callback(impl->callback_context, ssid, password)) {
    WiFi.disconnect();
    sendResponse(client, 500, "Internal Server Error", "text/plain",
                 "Connected, but the credentials could not be saved.");
    mesh::usbLoggingPort().println(
        "WiFi setup: connected, but credential persistence failed");
    return;
  }

  char response[600];
  String ip = WiFi.localIP().toString();
#ifdef WITH_MQTT_BRIDGE
  snprintf(response, sizeof(response),
           "<!doctype html><meta name=viewport content='width=device-width'><h2>WiFi connected</h2>"
           "<p>MeshCore joined the selected network.</p><p>IP address: <strong>%s</strong></p>"
           "<p>Reconnect this phone or computer to that WiFi network, then open "
           "<strong>http://%s/</strong> to finish MQTT setup.</p>"
           "<p>The setup SSID will close now; this page and address will remain visible.</p>",
           ip.c_str(), ip.c_str());
#else
  snprintf(response, sizeof(response),
           "<!doctype html><meta name=viewport content='width=device-width'><h2>Connected</h2>"
           "<p>MeshCore joined the WiFi network.</p>"
           "<p>New network address: <strong>http://%s/</strong></p>"
           "<p>The setup SSID will close now; this page and address will remain visible.</p>",
           ip.c_str());
#endif
  sendResponse(client, 200, "OK", "text/html", response);
  mesh::usbLoggingPort().printf(
      "WiFi setup: connected; IP %s\n", ip.c_str());
  impl->close_ap_at = millis() + AP_CLOSE_DELAY_MS;
}

static void handleClient(PortalImpl* impl, WiFiClient& client) {
  char line[256];
  if (!readLine(client, line, sizeof(line))) return;
  bool post_save = strncmp(line, "POST /save ", 11) == 0;
  bool get_request = strncmp(line, "GET ", 4) == 0;
  size_t content_length = 0;
  char authorization[96] = {};
  bool has_authorization = false;
  do {
    if (!readLine(client, line, sizeof(line))) return;
    if (strncasecmp(line, "Content-Length:", 15) == 0) {
      content_length = static_cast<size_t>(strtoul(line + 15, nullptr, 10));
    } else if (strncasecmp(line, "Authorization:", 14) == 0) {
      const char* value = line + 14;
      while (*value == ' ' || *value == '\t') ++value;
      if (has_authorization || strlen(value) >= sizeof(authorization)) {
        strcpy(authorization, "invalid");
      } else {
        memcpy(authorization, value, strlen(value) + 1);
      }
      has_authorization = true;
    }
  } while (line[0]);

  using namespace mesh::esp32::managementPassword;
  uint32_t authenticated_generation = 0;
  const AuthResult auth = authenticateBasic(authorization, millis(), &authenticated_generation);
  memset(authorization, 0, sizeof(authorization));
  if (auth != AuthResult::Authorized) {
    if (auth == AuthResult::Throttled) {
      sendResponse(client, 429, "Too Many Requests", "text/plain", "Wait 30 seconds before signing in again.");
    } else if (auth == AuthResult::Unavailable) {
      sendResponse(client, 503, "Service Unavailable", "text/plain", "Set the node admin password on USB, then restart setup WiFi.");
    } else {
      static const char message[] = "Sign in as admin using the node admin password.";
      client.printf("HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm=\"MeshCore\", charset=\"UTF-8\"\r\n"
                    "Content-Type: text/plain\r\nContent-Length: %u\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n%s",
                    static_cast<unsigned>(sizeof(message) - 1), message);
    }
    return;
  }

  if (post_save) {
    // Keep the accepted request bound to the authenticated credential while
    // association and persistence run. A concurrent password change retries
    // after this operation rather than authorizing a stale request midway.
    ChangeGuard request;
    Snapshot current;
    if (!request.acquired() || !snapshot(current)
        || current.generation != authenticated_generation) {
      sendResponse(client, 409, "Conflict", "text/plain", "Settings changed or are busy; sign in again and retry.");
      return;
    }
    if (content_length == 0 || content_length >= 256) {
      sendResponse(client, 400, "Bad Request", "text/plain", "Invalid form data.");
      return;
    }
    char body[256];
    size_t received = client.readBytes(body, content_length);
    if (received != content_length) {
      sendResponse(client, 408, "Request Timeout", "text/plain", "Incomplete form data.");
      return;
    }
    body[received] = 0;
    handleSave(impl, client, body);
  } else if (get_request) {
    // Serve the form for every GET path. Together with wildcard DNS this makes
    // common captive-portal probes land on the setup page.
    client.printf("HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                  "Content-Encoding: gzip\r\nContent-Length: %u\r\n"
                  "Connection: close\r\nCache-Control: no-store\r\n\r\n",
                  static_cast<unsigned>(MESH_HTML_WIFI_SETUP_LEN));
    client.write(MESH_HTML_WIFI_SETUP, MESH_HTML_WIFI_SETUP_LEN);
  } else {
    sendResponse(client, 404, "Not Found", "text/plain", "Not found.");
  }
}

static void portalTask(void* arg) {
  PortalImpl* impl = static_cast<PortalImpl*>(arg);
  while (impl->active && *impl->active) {
    impl->dns.processNextRequest();
    {
      using namespace mesh::esp32::managementPassword;
      Snapshot current;
      if (snapshot(current) && current.generation != impl->ap_password_generation) {
        if (impl->rekey_generation != current.generation) {
          impl->rekey_generation = current.generation;
          impl->rekey_at = millis();
        } else if (uint32_t(millis() - impl->rekey_at) >= 750U) {
          ChangeGuard rekey;
          if (rekey.acquired()) {
            if (!snapshot(current)) break;
            if (!mesh::wifi::startManagementAccessPoint(impl->ap_name, false)) break;
            impl->ap_password_generation = current.generation;
            impl->rekey_generation = 0;
            mesh::usbLoggingPort().println("Setup WiFi updated; reconnect using the admin password");
          }
        }
      }
    }
    WiFiClient client = impl->server.available();
    if (client) {
      client.setTimeout(5000);
      handleClient(impl, client);
      client.stop();
    }

    uint32_t now = millis();
    if (impl->recovery_connecting) {
      if (WiFi.status() == WL_CONNECTED) {
        String ip = WiFi.localIP().toString();
        mesh::usbLoggingPort().printf(
            "WiFi setup: saved network recovered; IP %s\n", ip.c_str());
        impl->recovery_connecting = false;
        break;
      }
      if (static_cast<int32_t>(now - impl->recovery_deadline) >= 0) {
        WiFi.disconnect();
        impl->recovery_connecting = false;
        mesh::usbLoggingPort().println(
            "WiFi setup: saved network still unavailable; setup AP remains active");
      }
    } else if (impl->recovery_ssid[0] && impl->recovery_interval_ms
               && static_cast<int32_t>(now - impl->next_recovery_at) >= 0) {
      mesh::usbLoggingPort().printf(
          "WiFi setup: retrying saved network '%s'...\n",
          impl->recovery_ssid);
      mesh::wifi::beginStation(
          impl->recovery_ssid, impl->recovery_password);
      impl->recovery_connecting = true;
      impl->recovery_deadline = now + CONNECT_TIMEOUT_MS;
      impl->next_recovery_at = now + impl->recovery_interval_ms;
    }
    if (impl->close_ap_at != 0
        && static_cast<int32_t>(now - impl->close_ap_at) >= 0) {
      break;
    }
    delay(10);
  }

  impl->dns.stop();
  impl->server.stop();
  WiFi.softAPdisconnect(true);
  mesh::wifi::stopTemporaryAccessPointRadio(impl->started_radio);
  impl->started_radio = false;
  if (impl->active) *impl->active = false;
  impl->task = nullptr;
  vTaskDelete(nullptr);
}

} // namespace

WiFiSetupPortal::WiFiSetupPortal() : _active(false), _impl(nullptr) {}

bool WiFiSetupPortal::begin(const char* ap_name, SaveCallback save_callback, void* context) {
  if (mesh::wireless::control().blocked(mesh::wireless::WiFi)) return false;
  if (_active) return true;
  PortalImpl* impl = static_cast<PortalImpl*>(_impl);
  if (!impl) {
    impl = new PortalImpl();
    if (!impl) return false;
    _impl = impl;
  }
  if (impl->task != nullptr) return false;
  mesh::esp32::managementPassword::ChangeGuard start;
  if (!start.acquired()) return false;
  mesh::esp32::managementPassword::Snapshot admin;
  if (!mesh::esp32::managementPassword::snapshot(admin) || !admin.password[0]) return false;

  impl->save_callback = save_callback;
  impl->callback_context = context;
  impl->active = &_active;
  impl->close_ap_at = 0;
  impl->recovery_ssid[0] = 0;
  impl->recovery_interval_ms = 0;
  impl->recovery_connecting = false;
  strncpy(impl->ap_name, ap_name && ap_name[0] ? ap_name : "MeshCore-Setup",
          sizeof(impl->ap_name) - 1);
  impl->ap_name[sizeof(impl->ap_name) - 1] = 0;

  impl->started_radio = WiFi.getMode() == WIFI_OFF;
  if (!mesh::wifi::startManagementAccessPoint(impl->ap_name, false)) {
    WiFi.softAPdisconnect(true);
    mesh::wifi::stopTemporaryAccessPointRadio(impl->started_radio);
    impl->started_radio = false;
    return false;
  }
  impl->ap_password_generation = admin.generation;
  impl->rekey_generation = 0;
  if (!impl->dns.start(53, "*", SETUP_IP)) {
    WiFi.softAPdisconnect(true);
    mesh::wifi::stopTemporaryAccessPointRadio(impl->started_radio);
    impl->started_radio = false;
    return false;
  }
  impl->server.begin();
  impl->server.setNoDelay(true);
  _active = true;
  if (xTaskCreatePinnedToCore(portalTask, "wifi-setup", 6144, impl, 1,
                              &impl->task, 0) != pdPASS) {
    _active = false;
    impl->dns.stop();
    impl->server.stop();
    WiFi.softAPdisconnect(true);
    mesh::wifi::stopTemporaryAccessPointRadio(impl->started_radio);
    impl->started_radio = false;
    impl->task = nullptr;
    return false;
  }
  mesh::usbLoggingPort().printf(
      "WiFi setup: join open AP '%s' and open http://%s/\n",
      impl->ap_name, SETUP_IP.toString().c_str());
  return true;
}

void WiFiSetupPortal::stop() {
  _active = false;
}

bool WiFiSetupPortal::isStopping() const {
  const PortalImpl* impl = static_cast<const PortalImpl*>(_impl);
  return impl && impl->task != nullptr;
}

void WiFiSetupPortal::configureRecovery(const char* ssid, const char* password,
                                        uint32_t interval_ms,
                                        uint32_t initial_delay_ms) {
  PortalImpl* impl = static_cast<PortalImpl*>(_impl);
  if (!impl) return;
  impl->recovery_ssid[0] = 0;
  strncpy(impl->recovery_password, password ? password : "", sizeof(impl->recovery_password) - 1);
  impl->recovery_password[sizeof(impl->recovery_password) - 1] = 0;
  impl->recovery_interval_ms = interval_ms;
  impl->recovery_connecting = false;
  impl->next_recovery_at = millis()
      + (initial_delay_ms ? initial_delay_ms : interval_ms);
  if (ssid) {
    strncpy(impl->recovery_ssid, ssid, sizeof(impl->recovery_ssid) - 1);
    impl->recovery_ssid[sizeof(impl->recovery_ssid) - 1] = 0;
  }
}

bool WiFiSetupPortal::loadStoredCredentials(char* ssid, size_t ssid_size,
                                            char* password, size_t password_size) {
  if (!ssid || ssid_size == 0 || !password || password_size == 0) return false;
  Preferences prefs;
  // Opening read-only reports ESP_ERR_NVS_NOT_FOUND on a normal fresh install.
  // Read-write creates the namespace once; guard absent String keys because
  // Preferences::getString() also logs NOT_FOUND instead of returning quietly.
  if (!prefs.begin("mesh-wifi", false)) return false;
  String stored_ssid = prefs.isKey("ssid")
      ? prefs.getString("ssid", "") : String();
  String stored_password = prefs.isKey("password")
      ? prefs.getString("password", "") : String();
  prefs.end();
  if (stored_ssid.length() == 0 || stored_ssid.length() >= ssid_size
      || stored_password.length() >= password_size
      || !mesh::cli::standaloneWiFiPasswordValid(stored_password.c_str())) return false;
  strncpy(ssid, stored_ssid.c_str(), ssid_size - 1);
  ssid[ssid_size - 1] = 0;
  strncpy(password, stored_password.c_str(), password_size - 1);
  password[password_size - 1] = 0;
  return true;
}

bool WiFiSetupPortal::saveStoredCredentials(const char* ssid, const char* password) {
  const char* saved_password = password ? password : "";
  if (!ssid || !ssid[0] || strlen(ssid) > 31
      || !mesh::cli::standaloneWiFiPasswordValid(saved_password)) return false;
  Preferences prefs;
  if (!prefs.begin("mesh-wifi", false)) return false;
  bool ok = prefs.putString("ssid", ssid) == strlen(ssid);
  prefs.putString("password", saved_password);  // An empty String reports zero bytes even on success.
  ok = ok && prefs.getString("password", "\x01") == saved_password;
  prefs.end();
  return ok;
}

bool WiFiSetupPortal::isPlaceholderSSID(const char* ssid) {
  if (!ssid || !ssid[0]) return true;
  return strcasecmp(ssid, "ssid") == 0
      || strcasecmp(ssid, "myssid") == 0
      || strcasecmp(ssid, "wifi_ssid") == 0
      || strcasecmp(ssid, "yourssid") == 0;
}

WiFiSetupPortal& wifiSetupPortal() {
  static WiFiSetupPortal portal;
  return portal;
}

#endif
