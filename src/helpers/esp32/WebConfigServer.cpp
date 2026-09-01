#include "WebConfigServer.h"

#ifdef WITH_WEBCONFIG

static_assert(sizeof(WEBCONFIG_AP_PREFIX) <= 28,
              "WEBCONFIG_AP_PREFIX must be at most 27 bytes");

#include <WiFi.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <DNSServer.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <esp_wifi.h>
#include <esp_system.h>
#include <esp_heap_caps.h>

#if defined(ESP32_PLATFORM) && defined(ENABLE_OTA) && \
    (defined(WIFI_OTA_SEEDER) || defined(WIFI_SSID))
  #include <helpers/esp32/WiFiOtaSeeder.h>
#endif

#ifdef WITH_MQTT_BRIDGE
  #include <helpers/MQTTPrefs.h>
  #include <helpers/MQTTPresets.h>
  #include <helpers/MQTTPacketFilter.h>
  #include <helpers/bridges/MQTTBridge.h>
#endif

#include "WebConfigHtml.h"
#include "helpers/CLICommandUtils.h"
#include "helpers/UsbLogging.h"
#include "helpers/WebConfigKeys.h"
#include "helpers/WiFiPowerSave.h"
#include "helpers/esp32/WiFiRadioPolicy.h"
#include "helpers/esp32/WiFiStationPolicy.h"

// ESPAsyncWebServer closes every ordinary response, so splitting the UI into
// many HTTP requests exhausts a lossy link's TCP/WiFi resources. Its ordinary
// PROGMEM response also fills the whole TCP send window at once. Keep one
// response open and queue only 512 body bytes after the preceding bytes have
// actually been acknowledged.
class WebConfigPacedProgmemResponse final : public AsyncWebServerResponse {
  static constexpr size_t kBodyChunk = 512;

  const uint8_t* _content;
  String _assembled_headers;
  size_t _written_headers = 0;
  size_t _in_flight = 0;
  size_t _space_before_batch = 0;

  size_t queueNext(AsyncWebServerRequest* request) {
    AsyncClient* client = request ? request->client() : nullptr;
    if (!client) {
      _state = RESPONSE_FAILED;
      return 0;
    }

    // This response has exactly one writer and never queues another batch
    // until the current one is acknowledged. Remembering the send-buffer space
    // here lets _ack() recover if AsyncTCP cannot allocate its SENT event: lwIP
    // has already restored tcp_sndbuf by then, but that event is not replayed.
    const size_t space_before_batch = client->space();
    size_t queued = 0;
    if (_state == RESPONSE_HEADERS) {
      const size_t added = client->add(
          _assembled_headers.c_str() + _written_headers,
          _assembled_headers.length() - _written_headers);
      _written_headers += added;
      _writtenLength += added;
      queued += added;
      if (_written_headers < _assembled_headers.length()) {
        if (queued && client->send()) {
          _in_flight += queued;
          _space_before_batch = space_before_batch;
        } else if (queued) {
          _state = RESPONSE_FAILED;
          client->close();
        }
        return queued;
      }
      _assembled_headers = String();
      _state = RESPONSE_CONTENT;
    }

    if (_state == RESPONSE_CONTENT && _sentLength < _contentLength) {
      size_t amount = _contentLength - _sentLength;
      if (amount > kBodyChunk) amount = kBodyChunk;
      const size_t room = client->space();
      if (amount > room) amount = room;
      if (amount) {
        const size_t added = client->add(
            reinterpret_cast<const char*>(_content + _sentLength), amount);
        _sentLength += added;
        _writtenLength += added;
        queued += added;
      }
      if (_sentLength == _contentLength) _state = RESPONSE_WAIT_ACK;
    }

    if (queued) {
      if (client->send()) {
        _in_flight += queued;
        _space_before_batch = space_before_batch;
      } else {
        _state = RESPONSE_FAILED;
        client->close();
      }
    }
    return queued;
  }

public:
  WebConfigPacedProgmemResponse(const char* content_type,
                               const uint8_t* content, size_t length)
      : _content(content) {
    _code = 200;
    _contentType = content_type;
    _contentLength = length;
  }

  bool _sourceValid() const override {
    return _content != nullptr && _contentLength != 0;
  }

  void _respond(AsyncWebServerRequest* request) override {
    addHeader("Connection", "close", false);
    _assembleHead(_assembled_headers, request->version());
    _state = RESPONSE_HEADERS;
    queueNext(request);
  }

  size_t _ack(AsyncWebServerRequest* request, size_t len,
              uint32_t time) override {
    (void)time;
    AsyncClient* client = request ? request->client() : nullptr;
    if (len == 0 && _in_flight != 0 && client
        && client->space() >= _space_before_batch) {
      // AsyncTCP reports polls as zero-length ACKs. A restored send buffer is
      // unambiguous here because this response has only one batch in flight.
      // Account for an ACK whose AsyncTCP event allocation failed under low
      // internal heap, otherwise the response would remain stuck forever.
      len = _in_flight;
    }
    _ackedLength += len;
    if (len >= _in_flight) _in_flight = 0;
    else _in_flight -= len;
    if (_in_flight != 0) return 0;
    if (_state == RESPONSE_WAIT_ACK) {
      // The full Content-Length has now been acknowledged. ESPAsyncWebServer
      // otherwise performs a graceful active close for every response, and
      // this IDF 4.4 stack retains malloc-backed TCP PCBs in TIME_WAIT for about
      // two minutes. Repeated WebConfig loads consume scarce internal heap and
      // can also prevent AsyncTCP from retiring close events, wedging both
      // infrastructure WiFi and ESP-NOW. Abort only after the final ACK so the
      // peer has the complete page, while freeing this PCB immediately. Keep
      // RESPONSE_WAIT_ACK: abort synchronously dispatches disconnect and the
      // request's scoped owner then destroys this response.
      if (client) client->abort();
      return 0;
    }
    return queueNext(request);
  }
};

static bool bluetoothWiFiCoexistenceRequired() {
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  // This server is never constructed on the exclusive profile's BLE boot.
  // Any running WebConfig instance therefore has no active Bluetooth stack.
  return false;
#elif defined(BLE_PIN_CODE) && defined(WIFI_SSID)
  return true;
#else
  return false;
#endif
}

static uint8_t effectiveWiFiPowerSave(uint8_t configured) {
  return mesh::wifi::effectivePowerSave(
      configured, bluetoothWiFiCoexistenceRequired(),
      mesh::wifi::kPrimaryEspNowRadio);
}

static void stopOwnedWiFiRadio() {
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  // ESP-NOW is this target's mesh radio. Stop only infrastructure-WiFi
  // ownership; powering the driver down would also remove the mesh transport.
  WiFi.setAutoReconnect(false);
  WiFi.disconnect(false, false);
  WiFi.mode(WIFI_STA);
  mesh::wifi::applyProtocolMask(WIFI_IF_STA);
  mesh::wifi::restoreEspNowChannel();
#else
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
#endif
}

// Placeholder sent instead of stored secrets; POSTs carrying it are dropped
// so an untouched password field never overwrites the stored value.
static const char SECRET_SENTINEL[] = "********";

static bool isAllowedSetKey(const char* key, bool has_mqtt) {
  if (!key || !wcIsAllowedSetKey(key)) return false;
#if !defined(MESH_PRIMARY_ESPNOW) || !MESH_PRIMARY_ESPNOW
  // The shared source-level allowlist is host-tested, but only a target whose
  // mesh transport is ESP-NOW has a meaningful primary-radio channel to set.
  if (wcSetKeyRequiresReboot(key)) return false;
#endif
  const bool mqtt_only = strncmp(key, "mqtt.", 5) == 0
                      || strncmp(key, "mqtt", 4) == 0
                      || strncmp(key, "timezone", 8) == 0
                      || strncmp(key, "snmp", 4) == 0;
  return has_mqtt || !mqtt_only;
}

static bool isSecretKey(const char* key) {
  return key && wcIsSecretKey(key);
}

// Commands whose CLI handler never returns would take the node down mid-drain,
// before the client could read a single result. `reboot` is deferred instead:
// it is not passed to the CLI at all, and the batch arms the ordinary reboot
// path once the operator has read the results. The rest (clkreboot, poweroff,
// ota update) do real work on the way down and cannot be faked, so they run
// normally and the connection drops -- the UI warns before sending them.
// CommonCLI dispatches on a 6-byte PREFIX (memcmp(command, "reboot", 6)), so
// `reboot now` and `rebooted` reach Board::reboot() too. Matching exactly here
// let those through to the CLI, which took the node down mid-drain with no
// results and no deferral -- the precise failure the deferral exists to avoid.
// Whatever CommonCLI would treat as a reboot, this must intercept.
static inline bool wcIsDeferredReboot(const char* cmd) {
  return strncmp(cmd, "reboot", 6) == 0;
}

// Commands the CLI reaches but the portal cannot honestly serve. Rejected at
// POST so nothing in the sequence runs, rather than failing halfway with a
// reply that does not explain itself. Returns the reason, or NULL if fine.
static const char* wcCliUnavailable(const char* cmd) {
  // ESP32Board::startOTAUpdate() does `new AsyncWebServer(80)` with no bind
  // check and answers "Started" regardless. The portal already holds port 80,
  // so from here it can only leak the allocation, inhibit sleep, and lie.
  if (strncmp(cmd, "start ota", 9) == 0) {
    return "start ota needs port 80, which this portal is using. "
           "Run it from the serial console, or use `ota update`.";
  }
  // `clock sync` sets the clock from the CALLER's timestamp. Web requests carry
  // none (execCommand passes 0), so CommonCLI always rejects it as moving the
  // clock backwards. `time <epoch>` is the one that works over this transport.
  if (strncmp(cmd, "clock sync", 10) == 0) {
    return "clock sync takes its time from the caller, which a web request has "
           "no way to supply. Use `time <epoch-seconds>` instead.";
  }
  // Both write their real output to Serial and hand back a stub the terminal
  // would render as success. Bare `log` also streams a whole file from the loop
  // task, stalling the mesh and the radio while it does.
  if (strcmp(cmd, "log") == 0) {
    return "log writes the packet log to the serial console, not here, and "
           "blocks the radio while it does. Use `log start` / `log stop`.";
  }
  if (strcmp(cmd, "get acl") == 0) {
    return "get acl writes to the serial console, not here.";
  }
  return NULL;
}
// The `password` command echoes the new password back in its reply, and replies
// are served to the client over the open setup AP. The config path overwrites it
// by key; a CLI entry has no key, so match on the command itself.
static inline bool wcCliEchoesSecret(const char* cmd) {
  return strncmp(cmd, "password ", 9) == 0;
}

// CommonCLI splits its surface by CALLER, not by command: a serial caller
// (sender_timestamp 0, physical access) reads secrets in plaintext, while a
// remote one gets "******** (serial only)". Its own comments say so -- "Serial
// only (WiFi creds grant LAN access); remote sees set/unset".
//
// execCommand passes 0, which is what makes `erase`, `stats-*` and `set freq`
// reachable from the terminal at all. Left alone, that also claims
// physical-access trust for an HTTP request: `get prv.key` would hand this
// node's identity to anyone associated with the open setup AP, and `get
// wifi.pwd` would hand over the operator's network. Reading a secret and
// writing one are not the same capability -- the wizard has always been able to
// REPLACE these; nothing in the portal could ever READ them, because
// /api/config masks them (wcIsSecretKey).
//
// So the command surface stays whole and only the READ is masked, restoring the
// distinction CommonCLI intended for a caller who is not at the serial port.
// Which commands those are lives in WebConfigKeys.h (wcIsSecretReadCommand),
// beside the rest of the secret classification and host-tested with it.

// Keep the set/unset signal, which is the useful part and what CommonCLI itself
// reports remotely; only the value goes. A getter answers "> value".
static void wcMaskSecretReply(char* reply) {
  const char* val = reply;
  if (val[0] == '>' && val[1] == ' ') val += 2;
  const bool unset = (val[0] == 0 || strcmp(val, "(not set)") == 0);
  strcpy(reply, unset ? "> (not set)" : "> ******** (serial only)");
}
// Reply classification lives in WebConfigBatch.h with the rest of the decisions
// (WebConfigBatch::cliReplyIsFailure / cliReplyGatesReboot / cliWriteSucceeded),
// so the shapes CommonCLI actually emits are enumerated in one host-tested place.

// Constant-time-ish comparison so login timing doesn't leak a prefix match.
static bool fixedTimeEquals(const char* a, const char* b, size_t max_len) {
  size_t la = strnlen(a, max_len), lb = strnlen(b, max_len);
  uint8_t diff = (la == lb) ? 0 : 1;
  for (size_t i = 0; i < max_len; i++) {
    char ca = (i < la) ? a[i] : 0;
    char cb = (i < lb) ? b[i] : 0;
    diff |= (uint8_t)(ca ^ cb);
  }
  return diff == 0;
}

static void appendOtaSeederStatus(char* reply, size_t reply_len) {
#if defined(ESP32_PLATFORM) && defined(ENABLE_OTA) && \
    (defined(WIFI_OTA_SEEDER) || defined(WIFI_SSID))
  mesh::ota::WiFiOtaSeeder::appendStatus(reply, reply_len);
#else
  (void)reply;
  (void)reply_len;
#endif
}

// RAII lock; tolerates a null mutex (allocation failure) by not locking.
struct WCLock {
  SemaphoreHandle_t h;
  explicit WCLock(SemaphoreHandle_t s) : h(s) { if (h) xSemaphoreTake(h, portMAX_DELAY); }
  ~WCLock() { if (h) xSemaphoreGive(h); }
};

WebConfigServer* WebConfigServer::_active = NULL;
AsyncWebServer* WebConfigServer::_host = NULL;
static volatile bool webconfig_button_toggle_requested = false;

// Out-of-line definitions for the in-class-initialised constants. An in-class
// initialiser is only a declaration under C++11 (what the xtensa-esp32
// toolchain builds with), so any use that binds a reference rather than reading
// the value -- ArduinoJson takes its argument as `const T&` -- needs the symbol to
// exist. Comparisons like `count >= MAX_BATCH` never did, which is why this only
// surfaced when MAX_BATCH started being reported in JSON, and then only on the
// targets where the compiler happened not to fold it.
const int WebConfigServer::MAX_BATCH;
const size_t WebConfigServer::MAX_BODY;
const uint32_t WebConfigServer::STOP_WARN_MS;

// Protects the permanent route host's active-session pointer and handler
// references across the loop and async_tcp cores. The critical sections only
// copy a pointer/update a counter; handlers themselves never run under it.
static portMUX_TYPE s_wc_route_mux = portMUX_INITIALIZER_UNLOCKED;

WebConfigServer::WebConfigServer(Callbacks* callbacks, void* mqtt_prefs, bool owns_wifi,
                                 const uint8_t* pub_key, const char* fw_ver,
                                 const char* build_date,
                                 const char* role, const char* board_name)
    : _cb(callbacks), _mqtt_prefs(mqtt_prefs), _owns_wifi(owns_wifi), _pub_key(pub_key),
      _fw_ver(fw_ver), _build_date(build_date), _role(role), _board_name(board_name) {
  _mux = xSemaphoreCreateMutex();
  _cli_enabled = loadCliEnabled(true);

#ifdef WITH_MQTT_BRIDGE
  MQTTPrefs* obs = static_cast<MQTTPrefs*>(_mqtt_prefs);
  if (obs && _owns_wifi) {
    strncpy(_wifi_ssid, obs->wifi_ssid, sizeof(_wifi_ssid) - 1);
    strncpy(_wifi_password, obs->wifi_password, sizeof(_wifi_password) - 1);
    _wifi_power_save = obs->wifi_power_save <= mesh::wifi::kPowerSaveMax
        ? obs->wifi_power_save : mesh::wifi::kDefaultPowerSave;
  } else
#endif
  {
    // Companion and standalone FULL builds keep their canonical connection
    // credentials in mesh-wifi NVS. Do not round-trip a 64-hex raw PSK through
    // the fixed-layout MQTTPrefs wifi_password[64] field.
    const bool loaded_standalone = loadStandaloneWiFi(
        _wifi_ssid, sizeof(_wifi_ssid),
        _wifi_password, sizeof(_wifi_password), &_wifi_power_save);
#ifdef WITH_MQTT_BRIDGE
    // Preserve the upgrade path from older WiFi-MQTT Companion installs that
    // have not written the canonical namespace yet. Once mesh-wifi exists it
    // always wins, including when it contains a 64-hex PSK.
    if (!loaded_standalone && obs) {
      strncpy(_wifi_ssid, obs->wifi_ssid, sizeof(_wifi_ssid) - 1);
      strncpy(_wifi_password, obs->wifi_password, sizeof(_wifi_password) - 1);
      _wifi_power_save = obs->wifi_power_save <= mesh::wifi::kPowerSaveMax
          ? obs->wifi_power_save : mesh::wifi::kDefaultPowerSave;
    }
#endif
  }
  _wifi_power_save = effectiveWiFiPowerSave(_wifi_power_save);
}

WebConfigServer::~WebConfigServer() {
  detachRoutes();
  if (_mux) vSemaphoreDelete(_mux);
}

bool WebConfigServer::loadEnabled(bool default_value) {
  Preferences nvs;
  // A missing namespace is the normal erased-device state. Read-write creates
  // it once instead of making Arduino Preferences log NOT_FOUND to Serial.
  if (!nvs.begin("mesh-webui", false)) return default_value;
  bool enabled = nvs.isKey("enabled")
      ? nvs.getBool("enabled", default_value) : default_value;
  nvs.end();
  return enabled;
}

bool WebConfigServer::saveEnabled(bool enabled) {
  Preferences nvs;
  if (!nvs.begin("mesh-webui", false)) return false;
  size_t written = nvs.putBool("enabled", enabled);
  nvs.end();
  return written == sizeof(uint8_t);
}

bool WebConfigServer::loadCliEnabled(bool default_value) {
  Preferences nvs;
  if (!nvs.begin("mesh-webui", false)) return default_value;
  bool enabled = nvs.isKey("cli")
      ? nvs.getBool("cli", default_value) : default_value;
  nvs.end();
  return enabled;
}

bool WebConfigServer::saveCliEnabled(bool enabled) {
  Preferences nvs;
  if (!nvs.begin("mesh-webui", false)) return false;
  size_t written = nvs.putBool("cli", enabled);
  nvs.end();
  return written == sizeof(uint8_t);
}

bool WebConfigServer::loadStandaloneWiFi(char* ssid, size_t ssid_len,
                                         char* password, size_t password_len,
                                         uint8_t* power_save) {
  if (!ssid || ssid_len == 0 || !password || password_len == 0) return false;
  ssid[0] = 0;
  password[0] = 0;
  Preferences nvs;
  if (!nvs.begin("mesh-wifi", false)) return false;
  // Preferences::getString() logs an error for a missing key even when the
  // caller supplied a default. Missing credentials are expected on first boot.
  String stored_ssid = nvs.isKey("ssid")
      ? nvs.getString("ssid", "") : String();
  String stored_password = nvs.isKey("password")
      ? nvs.getString("password", "") : String();
  uint8_t stored_ps = nvs.isKey("powersave")
      ? nvs.getUChar("powersave", mesh::wifi::kDefaultPowerSave)
      : mesh::wifi::kDefaultPowerSave;
  nvs.end();
  if (stored_ssid.length() >= ssid_len
      || stored_password.length() >= password_len
      || !mesh::cli::standaloneWiFiPasswordValid(stored_password.c_str())) {
    return false;
  }
  strncpy(ssid, stored_ssid.c_str(), ssid_len - 1);
  ssid[ssid_len - 1] = 0;
  strncpy(password, stored_password.c_str(), password_len - 1);
  password[password_len - 1] = 0;
  if (power_save) *power_save = effectiveWiFiPowerSave(stored_ps);
  return stored_ssid.length() != 0;
}

bool WebConfigServer::saveStandaloneWiFi(const char* ssid, const char* password,
                                         uint8_t power_save) {
  power_save = effectiveWiFiPowerSave(power_save);
  const char* pwd = password ? password : "";
  if (!ssid || !ssid[0] || strlen(ssid) >= 32
      || !mesh::cli::standaloneWiFiPasswordValid(pwd)
      || power_save > mesh::wifi::kPowerSaveMax) {
    return false;
  }
  Preferences nvs;
  if (!nvs.begin("mesh-wifi", false)) return false;
  bool ok = nvs.putString("ssid", ssid) == strlen(ssid);
  nvs.putString("password", pwd);  // empty String legitimately writes zero bytes
  ok = ok && nvs.getString("password", "\x01") == pwd;
  ok = ok && nvs.putUChar("powersave", power_save) == sizeof(uint8_t);
  nvs.end();
  return ok;
}

bool WebConfigServer::setStandaloneWiFiSSID(const char* value, char* reply,
                                             size_t reply_len) {
  if (!reply || reply_len == 0) return false;
  if (!mesh::cli::standaloneWiFiSSIDValid(value)) {
    snprintf(reply, reply_len, "Error: WiFi SSID must be 1-31 characters");
    return false;
  }

  Preferences nvs;
  if (!nvs.begin("mesh-wifi", false)) {
    snprintf(reply, reply_len, "Error: failed to open WiFi settings");
    return false;
  }
  const bool ok = nvs.putString("ssid", value) == strlen(value);
  nvs.end();
  snprintf(reply, reply_len, ok ? "OK - WiFi SSID saved"
                                : "Error: failed to save WiFi SSID");
  return ok;
}

bool WebConfigServer::setStandaloneWiFiPassword(const char* value, char* reply,
                                                 size_t reply_len) {
  if (!reply || reply_len == 0) return false;
  if (!mesh::cli::standaloneWiFiPasswordValid(value)) {
    snprintf(reply, reply_len,
             "Error: WiFi password must be 0-63 characters or 64 hex characters");
    return false;
  }

  Preferences nvs;
  if (!nvs.begin("mesh-wifi", false)) {
    snprintf(reply, reply_len, "Error: failed to open WiFi settings");
    return false;
  }
  nvs.putString("password", value);
  const bool ok = nvs.getString("password", "\x01") == value;
  nvs.end();
  snprintf(reply, reply_len, ok ? "OK - WiFi password saved"
                                : "Error: failed to save WiFi password");
  return ok;
}

bool WebConfigServer::setStandaloneWiFiPowerSave(const char* value, char* reply,
                                                  size_t reply_len) {
  if (!reply || reply_len == 0) return false;
  uint8_t power_save = mesh::wifi::kDefaultPowerSave;
  if (!mesh::cli::parseStandaloneWiFiPowerSave(value, power_save)) {
    snprintf(reply, reply_len, "Error: power save must be none, min, or max");
    return false;
  }
  if (mesh::wifi::kPrimaryEspNowRadio
      && power_save == mesh::wifi::kPowerSaveMax) {
    snprintf(reply, reply_len,
             "Error: power save max is unavailable while ESP-NOW is the primary radio");
    return false;
  }
  if (effectiveWiFiPowerSave(power_save) != power_save) {
    snprintf(reply, reply_len,
             "Error: power save none is unavailable while Bluetooth is active");
    return false;
  }
  Preferences nvs;
  if (!nvs.begin("mesh-wifi", false)) {
    snprintf(reply, reply_len, "Error: failed to open WiFi settings");
    return false;
  }
  const bool ok =
      nvs.putUChar("powersave", power_save) == sizeof(uint8_t);
  nvs.end();
  if (!ok) {
    snprintf(reply, reply_len, "Error: failed to save WiFi power save");
    return false;
  }

  esp_err_t apply_result = ESP_OK;
  if (WiFi.getMode() != WIFI_OFF) {
    const wifi_ps_type_t ps_mode =
        power_save == mesh::wifi::kPowerSaveNone ? WIFI_PS_NONE
        : power_save == mesh::wifi::kPowerSaveMax ? WIFI_PS_MAX_MODEM
                                                   : WIFI_PS_MIN_MODEM;
    apply_result = esp_wifi_set_ps(ps_mode);
  }
  if (apply_result == ESP_OK) {
    snprintf(reply, reply_len, "OK - WiFi power save set to %s", value);
  } else {
    snprintf(reply, reply_len,
             "OK - saved; WiFi power save applies on next connection");
  }
  return true;
}

bool WebConfigServer::setWiFiCliEnabled(const char* value, char* reply,
                                        size_t reply_len) {
  if (!reply || reply_len == 0) return false;
  if (!value || (strcmp(value, "on") != 0 && strcmp(value, "off") != 0)) {
    snprintf(reply, reply_len, "Error: use set wifi.cli on|off");
    return false;
  }
  const bool enabled = strcmp(value, "on") == 0;
  if (!saveCliEnabled(enabled)) {
    snprintf(reply, reply_len, "Error: failed to save WiFi CLI setting");
    return false;
  }
  WebConfigServer* active = _active;
  if (active) active->_cli_enabled = enabled;
  snprintf(reply, reply_len,
           enabled ? "OK - WiFi CLI on; active with WiFi client"
                   : "OK - WiFi CLI off");
  return true;
}

bool WebConfigServer::reloadStandaloneWiFi() {
  return loadStandaloneWiFi(
      _wifi_ssid, sizeof(_wifi_ssid),
      _wifi_password, sizeof(_wifi_password), &_wifi_power_save);
}

bool WebConfigServer::formatWiFiSSID(char* reply, size_t reply_len) {
  if (!reply || reply_len == 0) return false;

  char ssid[32] = "";
  char password[65] = "";
  uint8_t power_save = mesh::wifi::kDefaultPowerSave;
  bool configured = loadStandaloneWiFi(
      ssid, sizeof(ssid), password, sizeof(password), &power_save);
  if (_active && _active->_wifi_ssid[0]) {
    strncpy(ssid, _active->_wifi_ssid, sizeof(ssid) - 1);
    ssid[sizeof(ssid) - 1] = 0;
    configured = true;
  }

  snprintf(reply, reply_len, configured ? "> %s" : "> (not configured)", ssid);
  return true;
}

bool WebConfigServer::formatWiFiPowerSave(char* reply, size_t reply_len) {
  if (!reply || reply_len == 0) return false;

  uint8_t power_save = mesh::wifi::kDefaultPowerSave;
  if (_active) {
    power_save = _active->_wifi_power_save;
  } else {
    char ssid[32] = "";
    char password[65] = "";
    loadStandaloneWiFi(
        ssid, sizeof(ssid), password, sizeof(password), &power_save);
  }

  const char* name = "none";
  if (power_save == mesh::wifi::kPowerSaveMin) {
    name = "min";
  } else if (power_save == mesh::wifi::kPowerSaveMax) {
    name = "max";
  }
  snprintf(reply, reply_len, "> %s", name);
  return true;
}

bool WebConfigServer::formatWiFiCliStatus(char* reply, size_t reply_len) {
  if (!reply || reply_len == 0) return false;
  WebConfigServer* active = _active;
  const bool enabled = active ? active->_cli_enabled : loadCliEnabled(true);
  const bool available = active && active->_cb->supportsCliTerminal();
  const bool running = enabled && available && active->_mode == MODE_LAN
      && WiFi.status() == WL_CONNECTED;
  if (!enabled) {
    snprintf(reply, reply_len, "> off");
  } else if (running) {
    snprintf(reply, reply_len, "> on, active");
  } else {
    snprintf(reply, reply_len, "> on, waiting for WiFi client");
  }
  return true;
}

bool WebConfigServer::formatWiFiStatus(char* reply, size_t reply_len) {
  if (!reply || reply_len == 0) return false;

  char ssid[32] = "";
  char password[65] = "";
  uint8_t power_save = mesh::wifi::kDefaultPowerSave;
  bool configured = loadStandaloneWiFi(
      ssid, sizeof(ssid), password, sizeof(password), &power_save);
  WebConfigServer* active = _active;
  if (active && active->_wifi_ssid[0]) {
    strncpy(ssid, active->_wifi_ssid, sizeof(ssid) - 1);
    ssid[sizeof(ssid) - 1] = 0;
    configured = true;
  }

  if (active && active->_mode == MODE_SETUP) {
    char ap_ssid[33] = "";
    char ip[16] = "";
    getSetupInfo(ap_ssid, sizeof(ap_ssid), ip, sizeof(ip));
    snprintf(reply, reply_len, "> setup AP, SSID: %s, IP: %s", ap_ssid, ip);
    appendOtaSeederStatus(reply, reply_len);
    return true;
  }
  if (active && active->_mode == MODE_CONNECTING) {
    snprintf(reply, reply_len, "> connecting, SSID: %s",
             configured ? ssid : "(not configured)");
    appendOtaSeederStatus(reply, reply_len);
    return true;
  }

  const wl_status_t status = WiFi.status();
  if (status == WL_CONNECTED) {
    snprintf(reply, reply_len, "> connected, SSID: %s, IP: %s, RSSI: %d dBm",
             WiFi.SSID().c_str(),
             WiFi.localIP().toString().c_str(), WiFi.RSSI());
    appendOtaSeederStatus(reply, reply_len);
    return true;
  }
  if (!configured) {
    strcpy(reply, "> not configured; run 'start webconfig'");
    appendOtaSeederStatus(reply, reply_len);
    return true;
  }

  switch (status) {
    case WL_NO_SSID_AVAIL:
      snprintf(reply, reply_len, "> failed, SSID not found: %s", ssid);
      break;
    case WL_CONNECT_FAILED:
      snprintf(reply, reply_len, "> failed to connect, SSID: %s", ssid);
      break;
    case WL_CONNECTION_LOST:
      snprintf(reply, reply_len, "> connection lost, SSID: %s", ssid);
      break;
    default:
      if (active && active->_mode == MODE_LAN) {
        snprintf(reply, reply_len, "> disconnected, SSID: %s", ssid);
      } else {
        snprintf(reply, reply_len,
                 "> off, configured SSID: %s; run 'start webconfig'", ssid);
      }
      break;
  }
  appendOtaSeederStatus(reply, reply_len);
  return true;
}

void WebConfigServer::requestToggleFromButton() {
  webconfig_button_toggle_requested = true;
}

bool WebConfigServer::takeButtonToggleRequest() {
  if (!webconfig_button_toggle_requested) return false;
  webconfig_button_toggle_requested = false;
  return true;
}

bool WebConfigServer::isRebootPending() {
  WebConfigServer* w = _active;
  return w != NULL && WebConfigBatch::isConfigRebootPending(
                          w->_reboot_at, w->_batch_reboot,
                          toSpecState(w->_batch_state));
}

bool WebConfigServer::getSetupInfo(char* ssid, size_t ssid_len, char* ip, size_t ip_len) {
  WebConfigServer* w = _active;
  if (w == NULL || w->_mode != MODE_SETUP || w->_stopping) return false;
  if (ssid && ssid_len > 0) {
    strncpy(ssid, w->_ap_ssid, ssid_len - 1);
    ssid[ssid_len - 1] = 0;
  }
  if (ip && ip_len > 0) {
    snprintf(ip, ip_len, "%s", WiFi.softAPIP().toString().c_str());
  }
  return true;
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

bool WebConfigServer::startSetupMode(char reply[]) {
  const bool promote_lan = _mode == MODE_LAN && !_owns_wifi && !_stopping;
  if ((_mode != MODE_OFF && !promote_lan) || _stopping) {
    strcpy(reply, "Err: webconfig busy");
    return false;
  }
  _retry_saved_wifi_in_setup = false;
  _setup_reconnect_in_progress = false;
  _setup_reconnect_deadline = 0;
  _setup_started_at = 0;
  // AP_STA (not pure AP) so the WiFi scan for the SSID picker works while
  // the AP is up. STA stays unconnected - the bridge won't touch WiFi
  // while wifi_ssid is empty, and `start webconfig ap` requires it stopped.
  bool mode_ok = WiFi.mode(WIFI_AP_STA);
  // Setup mode has no login. Drop any STA association so the open setup API is
  // reachable only from the setup AP, not from the operator's LAN.
  WiFi.setAutoReconnect(false);
  bool disconnect_ok = WiFi.disconnect(false, true);
  delay(100);
  snprintf(_ap_ssid, sizeof(_ap_ssid), "%s-%02X%02X",
           WEBCONFIG_AP_PREFIX, _pub_key[0], _pub_key[1]);
  bool ap_ok = false;
  for (uint8_t attempt = 1; attempt <= 6 && !ap_ok; ++attempt) {
#ifdef WEBCONFIG_AP_PASSWORD
    ap_ok = WiFi.softAP(_ap_ssid, WEBCONFIG_AP_PASSWORD,
                        mesh::wifi::accessPointChannel());
#else
    ap_ok = WiFi.softAP(_ap_ssid, nullptr,
                        mesh::wifi::accessPointChannel());
#endif
    if (ap_ok) {
      // ESP-NOW can leave the persistent AP protocol mask with the proprietary
      // LR bit set. The driver then reports a healthy SoftAP, but ordinary
      // phones and laptops cannot discover its beacon. A WiFi companion must
      // advertise using the interoperable 2.4 GHz protocol set.
      const esp_err_t ap_protocol_result =
          mesh::wifi::applyAccessPointProtocolMask();
      const esp_err_t sta_protocol_result = esp_wifi_set_protocol(
          WIFI_IF_STA, mesh::wifi::kProtocolMask);
      if (ap_protocol_result == ESP_OK && sta_protocol_result == ESP_OK) break;
      mesh::usbLoggingPort().printf(
          "WebConfig protocol reset failed: AP=%d STA=%d\n",
          (int)ap_protocol_result, (int)sta_protocol_result);
      ap_ok = false;
    }

    mesh::usbLoggingPort().printf(
        "WebConfig AP attempt %u failed: mode_ok=%d disconnect_ok=%d mode=%d heap=%u largest=%u\n",
        (unsigned)attempt, mode_ok, disconnect_ok, (int)WiFi.getMode(),
        (unsigned)ESP.getFreeHeap(),
        (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
    WiFi.softAPdisconnect(true);
    delay(250);
    mode_ok = WiFi.mode(WIFI_AP_STA);
  }
  if (!ap_ok) {
    if (!promote_lan) stopOwnedWiFiRadio();
    strcpy(reply, "Err: failed to start AP");
    return false;
  }
  delay(100);  // let the AP netif settle before reading its IP
  IPAddress ip = WiFi.softAPIP();

  _dns = new DNSServer();
  _dns->start(53, "*", ip);  // captive portal: every name resolves to us

  _mode = MODE_SETUP;
  if (!promote_lan) createServer();
  _was_setup_ap = true;
  NodeSnapshot node = {};
  _cb->getNodeSnapshot(node);
  _initial_setup = _wifi_ssid[0] == 0 && node.admin_password[0] != 0;
  _setup_started_at = millis();
  _last_activity = _setup_started_at;
  // A primary ESP-NOW radio cannot leave its selected channel while scanning.
  // Ordinary WiFi targets retain the zero-channel all-band scan.
  WiFi.scanNetworks(true, false, false, 300,
                    mesh::wifi::stationScanChannel());

  sprintf(reply, "WebConfig AP started: join '%s' then open http://%s/", _ap_ssid, ip.toString().c_str());
  return true;
}

bool WebConfigServer::startLanMode(char reply[]) {
  if (_mode != MODE_OFF || _stopping) {
    strcpy(reply, "Err: webconfig busy");
    return false;
  }
  if (WiFi.status() != WL_CONNECTED) {
    strcpy(reply, "Err: WiFi not connected");
    return false;
  }
  _mode = MODE_LAN;
  _setup_started_at = 0;
  _wifi_reconnect_tracker.noteConnected();
  _retry_saved_wifi_in_setup = false;
  _setup_reconnect_in_progress = false;
  _setup_reconnect_deadline = 0;
  createServer();
  _last_activity = millis();

  NodeSnapshot node = {};
  _cb->getNodeSnapshot(node);
  int pos = snprintf(reply, 160, "WebConfig started: http://%s/%s",
                     WiFi.localIP().toString().c_str(),
                     node.admin_password[0] ? " (admin password login)" : "");
  if (heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL) < 60 * 1024) {
    sprintf(reply + pos, " WARN: low heap");
  }
  return true;
}

bool WebConfigServer::startAutoMode(char reply[]) {
  if (_wifi_ssid[0] == 0) return startSetupMode(reply);
  if (WiFi.status() == WL_CONNECTED) return startLanMode(reply);
  if (_mode != MODE_OFF || _stopping) {
    strcpy(reply, "Err: webconfig busy");
    return false;
  }

  WiFi.mode(WIFI_STA);
  if (mesh::wifi::applyProtocolMask(WIFI_IF_STA) != ESP_OK) {
    strcpy(reply, "Err: failed to reset WiFi station protocol");
    return false;
  }
  mesh::wifi::setStationAutoReconnect(true);
  _retry_saved_wifi_in_setup = false;
  _setup_reconnect_in_progress = false;
  _setup_reconnect_deadline = 0;
  _setup_started_at = 0;
  _wifi_reconnect_tracker.noteDisconnected(millis());
  mesh::wifi::beginStation(_wifi_ssid, _wifi_password);
  _wifi_power_save = effectiveWiFiPowerSave(_wifi_power_save);
  wifi_ps_type_t ps_mode =
      _wifi_power_save == mesh::wifi::kPowerSaveNone ? WIFI_PS_NONE
      : _wifi_power_save == mesh::wifi::kPowerSaveMax ? WIFI_PS_MAX_MODEM
                                                       : WIFI_PS_MIN_MODEM;
  esp_wifi_set_ps(ps_mode);
  _mode = MODE_CONNECTING;
  _connect_deadline = millis() + 15000;
  if (_connect_deadline == 0) _connect_deadline = 1;
  snprintf(reply, 160, "WebConfig connecting to '%s'; use 'get webui' for its IP", _wifi_ssid);
  return true;
}

void WebConfigServer::createServer() {
  if (_host == NULL) {
    _host = new AsyncWebServer(80);
    _server = _host;
    registerRoutes();
  } else {
    _server = _host;
  }
  // iOS caches plain-HTTP GETs aggressively (keyed by URL, surviving even a
  // device reflash behind the same IP), which poisons /api/config/result and
  // friends with stale responses from earlier sessions. Forbid caching on
  // every response; the HTML is small enough to refetch per visit.
  static bool cache_header_added = false;
  if (!cache_header_added) {
    DefaultHeaders::Instance().addHeader("Cache-Control", "no-store");
    cache_header_added = true;
  }
  attachRoutes();
  _server->begin();
}

void WebConfigServer::requestStop() {
  if (_mode == MODE_OFF && !_stopping) return;
  detachRoutes();
  if (_server) _server->end();
  if (_dns) _dns->stop();
  _mode = MODE_OFF;
  _stopping = true;
  _stop_warn_at = WebConfigBatch::scheduleAt(millis(), STOP_WARN_MS);
  _stop_warned = false;
}

void WebConfigServer::finalizeTeardown() {
  // Async requests keep a pointer to their server until disconnect. Retain the
  // listener and route table for the firmware lifetime; only detach and reclaim
  // the per-session state.
  _server = NULL;
  delete _dns;
  _dns = NULL;
  const bool was_setup_ap = _was_setup_ap;
  if (was_setup_ap) {
    WiFi.softAPdisconnect(true);
    // Nothing else owns WiFi when we raised the AP: either the node is
    // unconfigured, or `start webconfig ap` required the bridge stopped.
    if (_wifi_ssid[0] == 0 || _owns_wifi) {
      stopOwnedWiFiRadio();
    } else {
      WiFi.mode(WIFI_STA);
    }
    _was_setup_ap = false;
  }
  if (_owns_wifi && !was_setup_ap) {
    stopOwnedWiFiRadio();
  }
  _initial_setup = false;
  _setup_started_at = 0;
  _stopping = false;
  _stop_warn_at = 0;
  _stop_warned = false;
  _connect_deadline = 0;
  _retry_saved_wifi_in_setup = false;
  _setup_reconnect_in_progress = false;
  _setup_reconnect_deadline = 0;
  _reboot_at = 0;
  _batch_state = BATCH_IDLE;
  _batch_next = 0;
  _batch_reboot_armed = false;
  _setup_wifi_handoff_pending = false;
  _setup_wifi_handoff_deadline = 0;
  _setup_wifi_handoff_ip[0] = 0;
  _batch_kind = BATCH_CONFIG;
  _admin_pwd_set = false;
  _session_token[0] = 0;
  _stats_json[0] = 0;
  if (_cb) _cb->onWebConfigStopped();
}

void WebConfigServer::tick(uint32_t now) {
  if (_stopping) {
    uint32_t refs = handlerRefCount();
    switch (WebConfigBatch::stopStep(refs, _stop_warned, _stop_warn_at, now)) {
      case WebConfigBatch::StopAction::Finalize:
        finalizeTeardown();
        break;
      case WebConfigBatch::StopAction::Warn:
        _stop_warned = true;
        mesh::usbLoggingPort().printf(
            "WC: stop waiting for %lu handler(s); retaining session safely\n",
            (unsigned long)refs);
        break;
      case WebConfigBatch::StopAction::Wait:
        break;
    }
    return;
  }
  if (_mode == MODE_OFF) return;

  // A primary ESP-NOW radio and its WiFi station cannot remain on different
  // channels. Reject a router-driven channel move (for example a CSA) and put
  // the mesh radio back on its boot channel before any reconnect work runs.
  if (!mesh::wifi::enforceStationChannel()) {
    _wifi_reconnect_tracker.noteDisconnected(now);
  }

  if (_mode == MODE_CONNECTING) {
    if (WiFi.status() == WL_CONNECTED) {
      _wifi_reconnect_tracker.noteConnected();
      _mode = MODE_LAN;
      createServer();
      _connect_deadline = 0;
      _last_activity = now;
      mesh::usbLoggingPort().printf(
          "WebConfig ready: http://%s/\n",
          WiFi.localIP().toString().c_str());
    } else if (_connect_deadline && (int32_t)(now - _connect_deadline) >= 0) {
      mesh::usbLoggingPort().printf(
          "WebConfig: WiFi '%s' unavailable; opening setup AP\n", _wifi_ssid);
      const bool retry_saved_wifi = _wifi_ssid[0] != 0;
      // Keep the WiFi driver running while changing from STA to AP+STA.
      // Powering it off here and starting an AP immediately can race the
      // asynchronous ESP32-S3 netif teardown and make softAP() return false.
      WiFi.disconnect(false, false);
      delay(50);
      _mode = MODE_OFF;
      _connect_deadline = 0;
      char ignored[160];
      if (startSetupMode(ignored)) {
        _retry_saved_wifi_in_setup = retry_saved_wifi;
      }
      mesh::usbLoggingPort().println(ignored);
    }
    return;
  }

  // MQTT and companion runtimes manage their own station connection. For
  // standalone WiFi/WebUI builds, do not rely solely on ESP auto-reconnect:
  // after five minutes offline, explicitly reassert the saved credentials and
  // repeat at that interval until the router returns.
  if (_mode == MODE_LAN && _owns_wifi && _wifi_ssid[0]) {
    if (WiFi.status() == WL_CONNECTED) {
      _wifi_reconnect_tracker.noteConnected();
    } else {
      _wifi_reconnect_tracker.noteDisconnected(now);
      if (_wifi_reconnect_tracker.retryDue(now)) {
        _wifi_reconnect_tracker.noteAttempt(now);
        mesh::usbLoggingPort().printf(
            "WebConfig: WiFi still unavailable; retrying '%s'\n",
            _wifi_ssid);
        WiFi.mode(WIFI_STA);
        mesh::wifi::setStationAutoReconnect(true);
        WiFi.disconnect(false, false);
        mesh::wifi::beginStation(_wifi_ssid, _wifi_password);
      }
    }
  }

  // If boot occurred while the configured router was down, startAutoMode()
  // leaves a setup AP available instead of going dark. Keep that AP running,
  // but retry the saved station credentials on the same five-minute cadence.
  // A manually requested setup AP does not set this flag and is never closed
  // by a background reconnect.
  if (_mode == MODE_SETUP && _retry_saved_wifi_in_setup && _wifi_ssid[0]) {
    // An external owner (MQTT or the companion runtime) can restore the link
    // too. Promote as soon as any owner succeeds; only initiate retries here
    // when WebConfig itself owns WiFi.
    if (WiFi.status() == WL_CONNECTED) {
      if (_dns) {
        _dns->stop();
        delete _dns;
        _dns = NULL;
      }
      WiFi.softAPdisconnect(true);
      WiFi.mode(WIFI_STA);
      mesh::wifi::setStationAutoReconnect(true);
      _was_setup_ap = false;
      _initial_setup = false;
      _setup_started_at = 0;
      _retry_saved_wifi_in_setup = false;
      _setup_reconnect_in_progress = false;
      _setup_reconnect_deadline = 0;
      _wifi_reconnect_tracker.noteConnected();
      _mode = MODE_LAN;
      _last_activity = now;
      mesh::usbLoggingPort().printf(
          "WebConfig: saved WiFi recovered; ready at http://%s/\n",
          WiFi.localIP().toString().c_str());
    } else if (_owns_wifi && _setup_reconnect_in_progress
               && (int32_t)(now - _setup_reconnect_deadline) >= 0) {
      WiFi.disconnect(false, false);
      _setup_reconnect_in_progress = false;
      _setup_reconnect_deadline = 0;
      mesh::usbLoggingPort().println(
          "WebConfig: saved WiFi still unavailable; setup AP remains active");
    } else if (_owns_wifi && !_setup_reconnect_in_progress
               && _wifi_reconnect_tracker.retryDue(now)) {
      _wifi_reconnect_tracker.noteAttempt(now);
      _setup_reconnect_in_progress = true;
      _setup_reconnect_deadline = now + 20000UL;
      mesh::usbLoggingPort().printf(
          "WebConfig: retrying saved WiFi '%s'\n", _wifi_ssid);
      mesh::wifi::beginStation(_wifi_ssid, _wifi_password);
    }
  }

  if (_dns) _dns->processNextRequest();

  if (_setup_wifi_handoff_pending) serviceSetupWiFiHandoff(now);
  if (_batch_state == BATCH_PENDING && !_setup_wifi_handoff_pending) {
    drainBatch(now);
  }

  if (WebConfigBatch::rebootDue(_reboot_at, now)) {
    mesh::usbLoggingPort().printf(
        "WC: rebooting now (%s)\n",
        _batch_reboot_armed ? "confirmed" : "fallback");
    _cb->rebootNow();  // does not return
  }

  if ((int32_t)(_diag_until - now) > 0 && (now - _diag_last) >= 1000) {
    _diag_last = now;
    mesh::usbLoggingPort().printf(
        "WC: diag sta=%d heap=%u batch=%d/%d state=%d\n",
        (int)WiFi.softAPgetStationNum(), (unsigned)ESP.getFreeHeap(),
        (int)_batch_next, (int)_batch_count, (int)_batch_state);
  }

  // Refresh the stats snapshot only while a client is actually polling.
  if ((int32_t)(_stats_wanted_until - now) > 0 && (now - _stats_built_at) >= 2000) {
    WCLock lock(_mux);
    _cb->buildStatsJson(_stats_json, sizeof(_stats_json));
    _stats_built_at = now;
  }

  // A FULL image with no saved SSID gets one absolute setup window per boot.
  // Browser activity and an attached station cannot extend it. requestStop()
  // tears down the AP and finalizeTeardown() powers WiFi fully off; the saved
  // bridge/output preference is deliberately left unchanged for the next boot.
  if (WebConfigBatch::unconfiguredSetupWindowExpired(
          _mode == MODE_SETUP, _wifi_ssid[0] != 0, now,
          _setup_started_at,
          (uint32_t)WEBCONFIG_UNCONFIGURED_SETUP_TIMEOUT_MS)) {
    mesh::usbLoggingPort().printf(
        "WebConfig: WiFi still unconfigured after %lu minutes; powering off until reboot or explicit restart\n",
        (unsigned long)((uint32_t)WEBCONFIG_UNCONFIGURED_SETUP_TIMEOUT_MS / 60000UL));
    requestStop();
    return;
  }

  // Idle timeout: only the setup AP auto-stops (a deployed node must not be
  // left broadcasting an open AP). LAN mode runs until `stop webconfig`.
  if (_mode == MODE_SETUP && WiFi.softAPgetStationNum() == 0 &&
      (now - _last_activity) > WEBCONFIG_AP_IDLE_TIMEOUT_MS) {
    if (_retry_saved_wifi_in_setup && _wifi_ssid[0]) {
      // Close the open AP, but keep the WebConfig object alive in LAN mode so
      // its (or an external owner's) saved-network retries continue forever.
      // The listener becomes reachable again as soon as the router returns.
      if (_dns) {
        _dns->stop();
        delete _dns;
        _dns = NULL;
      }
      WiFi.softAPdisconnect(true);
      WiFi.mode(WIFI_STA);
      mesh::wifi::setStationAutoReconnect(true);
      _was_setup_ap = false;
      _initial_setup = false;
      _setup_started_at = 0;
      _retry_saved_wifi_in_setup = false;
      _setup_reconnect_in_progress = false;
      _setup_reconnect_deadline = 0;
      _mode = MODE_LAN;
      _last_activity = now;
      mesh::usbLoggingPort().println(
          "WebConfig: setup AP idle; saved WiFi recovery continues");
    } else {
      requestStop();
    }
  }
}

void WebConfigServer::finishBatch(uint32_t now) {
  WCLock lock(_mux);
  _batch_state = BATCH_DONE;
  const uint32_t reboot_at =
      WebConfigBatch::finishRebootAt(_batch_reboot, _batch_all_ok, now);
  if (reboot_at != 0) {
    // Fallback only: a successful result read replaces this with the shorter
    // confirmation delay. Failed batches remain available for correction.
    _reboot_at = reboot_at;
  }
}

void WebConfigServer::serviceSetupWiFiHandoff(uint32_t now) {
  if (!_setup_wifi_handoff_pending) return;

  IPAddress station_ip = WiFi.localIP();
  if (WiFi.status() == WL_CONNECTED && static_cast<uint32_t>(station_ip) != 0) {
    char ip[16];
    snprintf(ip, sizeof(ip), "%s", station_ip.toString().c_str());
    {
      WCLock lock(_mux);
      if (!_setup_wifi_handoff_pending) return;
      strncpy(_setup_wifi_handoff_ip, ip,
              sizeof(_setup_wifi_handoff_ip) - 1);
      _setup_wifi_handoff_ip[sizeof(_setup_wifi_handoff_ip) - 1] = 0;
      _setup_wifi_handoff_pending = false;
      _setup_wifi_handoff_deadline = 0;
    }
    mesh::usbLoggingPort().printf(
        "WebConfig: joined '%s' at %s; waiting for browser handoff\n",
        _wifi_ssid, ip);
    finishBatch(now);
    return;
  }

  if (!WebConfigBatch::deadlineReached(now, _setup_wifi_handoff_deadline)) {
    return;
  }

  WiFi.disconnect(false, false);  // stop STA attempt; leave the setup AP up
  {
    WCLock lock(_mux);
    if (!_setup_wifi_handoff_pending) return;
    for (int i = _batch_count - 1; i >= 0; i--) {
      if (strcmp(_batch[i].key, "wifi.ssid") == 0
          || strcmp(_batch[i].key, "wifi.pwd") == 0) {
        snprintf(_batch[i].reply, sizeof(_batch[i].reply),
                 "Error: could not join WiFi '%s'; setup AP remains active",
                 _wifi_ssid);
        break;
      }
    }
    _batch_all_ok = false;
    _setup_wifi_handoff_pending = false;
    _setup_wifi_handoff_deadline = 0;
    _setup_wifi_handoff_ip[0] = 0;
  }
  mesh::usbLoggingPort().printf(
      "WebConfig: could not join '%s'; setup AP remains active\n",
      _wifi_ssid);
  finishBatch(now);
}

void WebConfigServer::drainBatch(uint32_t now) {
  // One command per call, spaced out. Each `set` persists prefs with a flash
  // write, and flash writes stall the WiFi task (flash cache off); running a
  // whole batch back-to-back starves the softAP of beacons long enough for
  // clients (iPhones especially) to drop off mid-save.
  if (_batch_next == 0) {
    WCLock prefs_lock(_mux);
    _cb->onConfigBatchStart();
  } else if (WebConfigBatch::drainMustWait(_batch_next, _batch_count,
                                            now, _batch_last_cmd)) {
    return;  // let the WiFi task breathe between flash writes
  }
  if (_batch_next < _batch_count) {
    WCLock prefs_lock(_mux);
    BatchEntry& e = _batch[_batch_next++];
    e.reply[0] = 0;
    uint32_t t0 = millis();
    if (_batch_kind == BATCH_CLI) {
      if (wcIsDeferredReboot(e.cmd)) {
        strcpy(e.reply, "OK - reboot queued");
      } else {
        _cb->execAdminCommand(e.cmd, e.reply);
      }
      if (e.reply[0] == 0) strcpy(e.reply, "(no reply)");
      const bool set_admin_pwd = wcCliEchoesSecret(e.cmd);
      if (set_admin_pwd) {
        strcpy(e.reply, "OK");
        _admin_pwd_set = true;
      }
      if (wcIsSecretReadCommand(e.cmd)) wcMaskSecretReply(e.reply);
      if (WebConfigBatch::cliReplyGatesReboot(e.cmd)) {
        _batch_all_ok = WebConfigBatch::nextAllOk(
            _batch_all_ok, WebConfigBatch::cliWriteSucceeded(e.reply));
      }
    } else {
      const bool admin_pwd = wcIsAdminPasswordKey(e.key);
      const char* value = admin_pwd ? e.cmd + strlen("password ")
                                    : e.cmd + strlen("set ") + strlen(e.key) + 1;
      if ((_mqtt_prefs == NULL || !_owns_wifi) && strcmp(e.key, "wifi.ssid") == 0) {
      if (!value[0] || strlen(value) >= sizeof(_wifi_ssid)) {
        strcpy(e.reply, "Error: WiFi SSID must be 1-31 characters");
      } else {
        strncpy(_wifi_ssid, value, sizeof(_wifi_ssid) - 1);
        _wifi_ssid[sizeof(_wifi_ssid) - 1] = 0;
        _standalone_wifi_dirty = true;
        strcpy(e.reply, "OK");
      }
    } else if ((_mqtt_prefs == NULL || !_owns_wifi) && strcmp(e.key, "wifi.pwd") == 0) {
      if (!mesh::cli::standaloneWiFiPasswordValid(value)) {
        strcpy(e.reply,
               "Error: WiFi password must be 0-63 characters or 64 hex characters");
      } else {
        strncpy(_wifi_password, value, sizeof(_wifi_password) - 1);
        _wifi_password[sizeof(_wifi_password) - 1] = 0;
        _standalone_wifi_dirty = true;
        strcpy(e.reply, "OK");
      }
    } else if ((_mqtt_prefs == NULL || !_owns_wifi) && strcmp(e.key, "wifi.powersave") == 0) {
      if (strcmp(value, "min") == 0) _wifi_power_save = 0;
      else if (strcmp(value, "none") == 0
               && bluetoothWiFiCoexistenceRequired()) {
        strcpy(e.reply,
               "Error: power save none is unavailable while Bluetooth is active");
      } else if (strcmp(value, "none") == 0) _wifi_power_save = 1;
      else if (strcmp(value, "max") == 0
               && mesh::wifi::kPrimaryEspNowRadio) {
        strcpy(e.reply,
               "Error: power save max is unavailable while ESP-NOW is the primary radio");
      } else if (strcmp(value, "max") == 0) _wifi_power_save = 2;
      else strcpy(e.reply, "Error: must be none, min, or max");
      if (e.reply[0] == 0) {
        _standalone_wifi_dirty = true;
        strcpy(e.reply, "OK");
      }
    } else {
      _cb->execCommand(e.cmd, e.reply);
      // The CLI password command echoes the new secret. Never return it to a
      // browser client, especially over the open setup AP.
      if (admin_pwd) strcpy(e.reply, "OK");
      // Keep the response snapshot current after MQTT/companion CLI handlers
      // persist a WiFi field. The browser can then soft-reload without a stale
      // value even before the requested reboot happens.
      if (strncmp(e.reply, "OK", 2) == 0 && strcmp(e.key, "wifi.ssid") == 0) {
        strncpy(_wifi_ssid, value, sizeof(_wifi_ssid) - 1);
        _wifi_ssid[sizeof(_wifi_ssid) - 1] = 0;
      } else if (strncmp(e.reply, "OK", 2) == 0 && strcmp(e.key, "wifi.pwd") == 0) {
        strncpy(_wifi_password, value, sizeof(_wifi_password) - 1);
        _wifi_password[sizeof(_wifi_password) - 1] = 0;
      } else if (strncmp(e.reply, "OK", 2) == 0 && strcmp(e.key, "wifi.powersave") == 0) {
        _wifi_power_save = strcmp(value, "max") == 0 ? 2 : strcmp(value, "min") == 0 ? 0 : 1;
      }
      }
      if (e.reply[0] == 0) strcpy(e.reply, "OK");
      _batch_all_ok = WebConfigBatch::nextAllOk(
          _batch_all_ok, strncmp(e.reply, "OK", 2) == 0);
    }
    _batch_last_cmd = millis();
    // Config entries are named by their (non-secret) key. A CLI command is
    // deliberately not logged: the operator can see what they typed, and a
    // `set wifi.pwd` or `password` from the terminal must not reach the serial
    // log, which is a different audience from the browser session.
    if (_batch_kind == BATCH_CLI) {
      mesh::usbLoggingPort().printf(
          "WC: cli %d/%d took %lums\n", (int)_batch_next,
          (int)_batch_count, (unsigned long)(_batch_last_cmd - t0));
    } else {
      mesh::usbLoggingPort().printf(
          "WC: cmd %d/%d '%s' took %lums\n", (int)_batch_next,
          (int)_batch_count, e.key,
          (unsigned long)(_batch_last_cmd - t0));
    }
    if (!WebConfigBatch::drainFinished(_batch_next, _batch_count)) {
      return;  // more commands next tick
    }
  }
  if (_standalone_wifi_dirty) {
    _standalone_wifi_dirty = false;
    if (!saveStandaloneWiFi(_wifi_ssid, _wifi_password, _wifi_power_save)) {
      // Attribute the persistence failure to the last WiFi field so it is
      // visible beside a concrete input in the UI.
      for (int i = _batch_count - 1; i >= 0; i--) {
        if (strncmp(_batch[i].key, "wifi.", 5) == 0) {
          strcpy(_batch[i].reply, "Error: failed to save WiFi settings");
          break;
        }
      }
      _batch_all_ok = false;
    }
  }
  {
    WCLock prefs_lock(_mux);
    _cb->onConfigBatchEnd();
  }

  bool wifi_credentials_changed = false;
  bool espnow_channel_changed = false;
  for (int i = 0; i < _batch_count; i++) {
    if (strcmp(_batch[i].key, "wifi.ssid") == 0
        || strcmp(_batch[i].key, "wifi.pwd") == 0) {
      wifi_credentials_changed = true;
    } else if (wcSetKeyRequiresReboot(_batch[i].key)) {
      espnow_channel_changed = true;
    }
  }
  if (WebConfigBatch::shouldStartSetupWiFiHandoff(
          _mode == MODE_SETUP, _batch_reboot, _batch_all_ok,
          wifi_credentials_changed, _wifi_ssid[0] != 0,
          espnow_channel_changed)) {
    {
      WCLock lock(_mux);
      _setup_wifi_handoff_pending = true;
      _setup_wifi_handoff_deadline =
          WebConfigBatch::setupWiFiConnectDeadline(now);
      _setup_wifi_handoff_ip[0] = 0;
    }
    // AP+STA keeps the captive page reachable while DHCP assigns the address
    // that will be shown to the operator before the setup AP is shut down.
    WiFi.mode(WIFI_AP_STA);
    WiFi.setAutoReconnect(false);
    mesh::usbLoggingPort().printf(
        "WebConfig: testing saved WiFi '%s' before reboot\n", _wifi_ssid);
    mesh::wifi::beginStation(_wifi_ssid, _wifi_password);
    return;
  }
  finishBatch(now);
}

// ---------------------------------------------------------------------------
// Routes / auth
// ---------------------------------------------------------------------------

// Diagnostic trace of every request that reaches the server (async_tcp task).
// Distinguishes "client stopped sending" from "server stopped accepting" when
// a save's confirmation polls go missing on hardware.
static void wcLogReq(AsyncWebServerRequest* r) {
  mesh::usbLoggingPort().printf(
      "WC: http %s %s\n", r->methodToString(), r->url().c_str());
}

void WebConfigServer::attachRoutes() {
  portENTER_CRITICAL(&s_wc_route_mux);
  _active = this;
  portEXIT_CRITICAL(&s_wc_route_mux);
}

void WebConfigServer::detachRoutes() {
  portENTER_CRITICAL(&s_wc_route_mux);
  if (_active == this) _active = NULL;
  portEXIT_CRITICAL(&s_wc_route_mux);
}

uint32_t WebConfigServer::handlerRefCount() const {
  portENTER_CRITICAL(&s_wc_route_mux);
  uint32_t refs = _handler_refs;
  portEXIT_CRITICAL(&s_wc_route_mux);
  return refs;
}

void WebConfigServer::dispatchRequest(AsyncWebServerRequest* req,
                                      RequestHandler handler) {
  wcLogReq(req);
  WebConfigServer* target = NULL;
  portENTER_CRITICAL(&s_wc_route_mux);
  if (_active != NULL) {
    target = _active;
    target->_handler_refs++;
  }
  portEXIT_CRITICAL(&s_wc_route_mux);

  if (target == NULL) {
    req->send(503, "application/json", "{\"error\":\"webconfig stopped\"}");
    return;
  }

  (target->*handler)(req);

  portENTER_CRITICAL(&s_wc_route_mux);
  if (target->_handler_refs > 0) target->_handler_refs--;
  portEXIT_CRITICAL(&s_wc_route_mux);
}

void WebConfigServer::registerRoutes() {
  _server->on("/ui", HTTP_GET, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleUi);
  });
  _server->on("/", HTTP_GET, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleRoot);
  });
  _server->on("/api/status", HTTP_GET, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleStatus);
  });
  _server->on("/api/presets", HTTP_GET, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handlePresets);
  });
  _server->on("/api/login", HTTP_POST, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleLogin);
  },
              NULL, collectBody);
  _server->on("/api/logout", HTTP_POST, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleLogout);
  });
  // NB: plain-string routes match sub-paths too ("/api/config" matches
  // "/api/config/result") and handlers run in registration order, so the more
  // specific route MUST be registered first or it never fires. This was why
  // save confirmations were lost: result polls were answered with config JSON.
  _server->on("/api/config/result", HTTP_GET, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleConfigResult);
  });
  _server->on("/api/config", HTTP_GET, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleConfigGet);
  });
  _server->on("/api/config", HTTP_POST, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleConfigPost);
  },
              NULL, collectBody);
  _server->on("/api/cli/result", HTTP_GET, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleCliResult);
  });
  _server->on("/api/cli", HTTP_POST, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleCliPost);
  },
              NULL, collectBody);
  _server->on("/api/stats", HTTP_GET, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleStats);
  });
  _server->on("/api/scan", HTTP_GET, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleScan);
  });
  _server->on("/api/reboot", HTTP_POST, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleReboot);
  });
  _server->on("/api/portal/exit", HTTP_POST, [](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handlePortalExit);
  });
  _server->onNotFound([](AsyncWebServerRequest* r) {
    dispatchRequest(r, &WebConfigServer::handleNotFound);
  });
}

// Accumulate a small JSON body into request->_tempObject (freed automatically
// by the request destructor). Oversized bodies are left unbuffered and
// rejected in the completion handler.
void WebConfigServer::collectBody(AsyncWebServerRequest* req, uint8_t* data, size_t len,
                                  size_t index, size_t total) {
  if (total == 0 || total > MAX_BODY) return;
  if (index == 0) {
    req->_tempObject = malloc(total + 1);
    if (req->_tempObject) ((char*)req->_tempObject)[total] = 0;
  }
  if (req->_tempObject) memcpy((uint8_t*)req->_tempObject + index, data, len);
}

bool WebConfigServer::checkAuth(AsyncWebServerRequest* req) {
  _last_activity = millis();
  if (_mode == MODE_SETUP) {
    // AP+STA is used for scans and saved-network recovery. Keep the unauthenticated
    // setup API reachable only through the physical setup AP, never through a
    // briefly recovered LAN interface before tick() promotes the mode.
    return req && req->client()
        && req->client()->localIP() == WiFi.softAPIP();
  }
  if (_mode != MODE_LAN) return false;
  NodeSnapshot node = {};
  {
    WCLock lock(_mux);
    _cb->getNodeSnapshot(node);
  }
  if (node.admin_password[0] == 0) return true;
  if (_session_token[0] == 0) return false;
  if (!req->hasHeader("Cookie")) return false;
  const String& cookies = req->getHeader("Cookie")->value();
  int idx = cookies.indexOf("wcs=");
  if (idx < 0 || (int)cookies.length() < idx + 4 + 32) return false;
  String token = cookies.substring(idx + 4, idx + 4 + 32);
  uint32_t now = millis();
  if ((uint32_t)(now - _session_last_seen) > WEBCONFIG_SESSION_TTL_MS) return false;
  if (!fixedTimeEquals(token.c_str(), _session_token, 32)) return false;
  _session_last_seen = now;  // sliding expiry
  return true;
}

// ---------------------------------------------------------------------------
// Handlers (async_tcp task - no CLI/prefs writes, no radio access)
// ---------------------------------------------------------------------------

void WebConfigServer::handleRoot(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  _last_activity = millis();
  if (req->hasHeader("If-None-Match") &&
      req->getHeader("If-None-Match")->value() == WEBCONFIG_HTML_ETAG) {
    req->send(304);
    return;
  }
  AsyncWebServerResponse* res =
      req->beginResponse(200, "text/html", WEBCONFIG_HTML_LOADER,
                         WEBCONFIG_HTML_LOADER_LEN);
  res->addHeader("ETag", WEBCONFIG_HTML_ETAG);
  req->send(res);
}

void WebConfigServer::handleUi(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  _last_activity = millis();
  if (!req->hasParam("v") ||
      req->getParam("v")->value() != WEBCONFIG_HTML_VERSION) {
    // Part URLs are immutable. Never serve a new layout under an old loader's
    // cache key; a 409 tells the loader to refresh itself first.
    AsyncWebServerResponse* res =
        req->beginResponse(409, "text/plain", "WebConfig version changed");
    res->addHeader("Cache-Control", "no-store");
    req->send(res);
    return;
  }
  AsyncWebServerResponse* res =
      new WebConfigPacedProgmemResponse("text/html; charset=utf-8",
                                       WEBCONFIG_HTML_GZ,
                                       WEBCONFIG_HTML_GZ_LEN);
  res->addHeader("Content-Encoding", "gzip");
  res->addHeader("Cache-Control", "public, max-age=31536000, immutable");
  req->send(res);
}

void WebConfigServer::handleStatus(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  bool authed = checkAuth(req);

  NodeSnapshot node = {};
  {
    WCLock lock(_mux);
    _cb->getNodeSnapshot(node);
  }
  const bool has_mqtt = _mqtt_prefs != NULL;

  DynamicJsonDocument doc(768);
  doc["mode"] = (_mode == MODE_SETUP) ? "setup" : "lan";
  doc["auth"] = authed;
  doc["needs_setup"] = (_wifi_ssid[0] == 0);
  doc["needs_password"] = _initial_setup;
  doc["password_supported"] = node.admin_password[0] != 0;
  doc["name"] = (const char*)node.name;
  char node_id[17];
  for (int i = 0; i < 8; i++) sprintf(&node_id[i * 2], "%02x", _pub_key[i]);
  doc["node_id"] = node_id;
  doc["fw"] = _fw_ver;
  // The page shows a trimmed version -- base + build number + channel -- and
  // pairs it with this, the way `ver` does. Both come from the same defines.
  doc["build_date"] = _build_date;
  doc["role"] = _role;
  doc["board"] = _board_name;
  doc["uptime_s"] = millis() / 1000;
#ifdef WITH_MQTT_BRIDGE
  doc["runtime_slots"] = has_mqtt ? RUNTIME_MQTT_SLOTS : 0;
  doc["max_slots"] = has_mqtt ? MAX_MQTT_SLOTS : 0;
  doc["active_slots"] = has_mqtt ? MQTTBridge::getMaxActiveSlots() : 0;
#else
  doc["runtime_slots"] = 0;
  doc["max_slots"] = 0;
  doc["active_slots"] = 0;
#endif
  doc["mqtt"] = has_mqtt;
#ifdef MESHCORE_USA_RADIO_PRESET
  // USA-targeted images already carry the intended regional radio defaults,
  // so first-boot setup may preserve them without forcing another selection.
  doc["radio_optional"] = true;
#else
  doc["radio_optional"] = false;
#endif
  // The fixed-layout MQTTPrefs path remains limited to 63 characters.
  // Standalone mesh-wifi NVS can also hold a standards-defined 64-hex PSK.
  doc["wifi_psk64"] = (_mqtt_prefs == NULL || !_owns_wifi);
  doc["cli"] = _cli_enabled && _mode == MODE_LAN
      && WiFi.status() == WL_CONNECTED && _cb->supportsCliTerminal();
  doc["capabilities"] = node.capabilities;
  // Commands the terminal may submit at once. The CLI shares the config
  // batch's fixed slot, so the cap is MAX_BATCH -- reported rather than
  // duplicated in the page, which cannot know how this build was sized.
  doc["max_cmds"] = MAX_BATCH;

  AsyncResponseStream* res = req->beginResponseStream("application/json");
  serializeJson(doc, *res);
  req->send(res);
}

void WebConfigServer::handleLogin(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  _last_activity = millis();
  if (_mode == MODE_SETUP) {  // no auth in setup mode
    req->send(200, "application/json", "{\"ok\":true}");
    return;
  }
  NodeSnapshot node = {};
  {
    WCLock lock(_mux);
    _cb->getNodeSnapshot(node);
  }
  if (node.admin_password[0] == 0) {
    req->send(200, "application/json", "{\"ok\":true}");
    return;
  }
  uint32_t now = millis();
  if (_login_lock_until && (int32_t)(now - _login_lock_until) < 0) {
    req->send(429, "application/json", "{\"error\":\"locked, retry in 30s\"}");
    return;
  }
  const char* body = (const char*)req->_tempObject;
  DynamicJsonDocument doc(256);
  if (!body || deserializeJson(doc, body) != DeserializationError::Ok) {
    req->send(400, "application/json", "{\"error\":\"bad request\"}");
    return;
  }
  const char* pwd = doc["password"] | "";
  if (!fixedTimeEquals(pwd, node.admin_password, sizeof(node.admin_password))) {
    if (++_login_fails >= 5) {
      _login_lock_until = now + 30000;
      if (_login_lock_until == 0) _login_lock_until = 1;
      _login_fails = 0;
    }
    req->send(401, "application/json", "{\"error\":\"wrong password\"}");
    return;
  }
  _login_fails = 0;
  _login_lock_until = 0;
  for (int i = 0; i < 4; i++) sprintf(&_session_token[i * 8], "%08lx", (unsigned long)esp_random());
  _session_last_seen = now;

  AsyncWebServerResponse* res = req->beginResponse(200, "application/json", "{\"ok\":true}");
  char cookie[80];
  sprintf(cookie, "wcs=%s; HttpOnly; SameSite=Lax; Path=/", _session_token);
  res->addHeader("Set-Cookie", cookie);
  req->send(res);
}

void WebConfigServer::handleLogout(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  _session_token[0] = 0;
  AsyncWebServerResponse* res = req->beginResponse(200, "application/json", "{\"ok\":true}");
  res->addHeader("Set-Cookie", "wcs=; Max-Age=0; Path=/");
  req->send(res);
}

void WebConfigServer::handleConfigGet(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  if (!checkAuth(req)) { req->send(401, "application/json", "{\"error\":\"auth\"}"); return; }

  DynamicJsonDocument doc(6144);
  {
    WCLock lock(_mux);
    NodeSnapshot node = {};
    _cb->getNodeSnapshot(node);

    JsonObject radio = doc.createNestedObject("radio");
    // round via double so float error doesn't leak into the JSON
    // (910.525f would otherwise serialize as 910.5250244)
    radio["freq"] = (double)roundf(node.freq * 1000.0f) / 1000.0;
    radio["bw"] = (double)roundf(node.bw * 100.0f) / 100.0;
    radio["sf"] = node.sf;
    radio["cr"] = node.cr;
    radio["tx"] = node.tx_power;
    radio["af"] = node.airtime_factor;
    radio["rxdelay"] = node.rx_delay;
    radio["txdelay"] = node.tx_delay;
    radio["cad"] = (bool)node.cad;
    radio["rxgain"] = (bool)node.rx_gain;
    radio["fem_rxgain"] = (bool)node.fem_rx_gain;
    radio["rxps_enabled"] = (bool)node.rx_ps_enabled;
    radio["rxps_level"] = node.rx_ps_level;
    radio["rxps_preamble"] = node.rx_ps_preamble;
    radio["rxps_rx_us"] = node.rx_ps_rx_us;
    radio["rxps_sleep_us"] = node.rx_ps_sleep_us;
    radio["powersaving"] = (bool)node.power_saving;
    radio["repeat"] = (bool)node.repeat;
    radio["flood_max"] = node.flood_max;
    radio["flood_max_advert"] = node.flood_max_advert;
    radio["flood_max_unscoped"] = node.flood_max_unscoped;
    static const char* const LOOP_MODES[] = { "off", "minimal", "moderate", "strict" };
    radio["loop_detect"] = LOOP_MODES[node.loop_detect <= 3 ? node.loop_detect : 0];
    radio["name"] = (const char*)node.name;
    radio["bluetooth_name"] = (const char*)node.bluetooth_name;
    radio["lat"] = node.lat;
    radio["lon"] = node.lon;
    radio["advert_interval"] = node.advert_interval;
    radio["flood_advert_interval"] = node.flood_advert_interval;
    radio["capabilities"] = node.capabilities;

    JsonObject wifi = doc.createNestedObject("wifi");
    wifi["ssid"] = (const char*)_wifi_ssid;
    wifi["pwd"] = _wifi_password[0] ? SECRET_SENTINEL : "";
    wifi["powersave"] = _wifi_power_save == 0 ? "min"
                        : _wifi_power_save == 2 ? "max" : "none";
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
    wifi["espnow_channel"] = mesh::wifi::loadConfiguredEspNowChannel();
#endif

#ifdef WITH_MQTT_BRIDGE
    MQTTPrefs* obs = static_cast<MQTTPrefs*>(_mqtt_prefs);
    if (obs) {
    JsonObject mqtt = doc.createNestedObject("mqtt");
    mqtt["origin"] = (const char*)obs->mqtt_origin;
    mqtt["iata"] = (const char*)obs->mqtt_iata;
    mqtt["status"] = (bool)obs->mqtt_status_enabled;
    mqtt["packets"] = (bool)obs->mqtt_packets_enabled;
    mqtt["raw"] = (bool)obs->mqtt_raw_enabled;
    mqtt["tx"] = obs->mqtt_tx_enabled == 2 ? "advert"
                 : obs->mqtt_tx_enabled == 1 ? "on" : "off";
    mqtt["rx"] = (bool)obs->mqtt_rx_enabled;
    mqtt["interval"] = obs->mqtt_status_interval / 60000;
    mqtt["neighbors"] = (bool)obs->mqtt_neighbors_enabled;
    mqtt["neighbors_interval"] = obs->mqtt_neighbors_interval / 3600000UL;
    mqtt["timezone"] = (const char*)obs->timezone_string;
    mqtt["timezone_offset"] = obs->timezone_offset;
    mqtt["ntp"] = (const char*)obs->mqtt_ntp_server;
    mqtt["owner"] = (const char*)obs->mqtt_owner_public_key;
    mqtt["email"] = (const char*)obs->mqtt_email;
    mqtt["snmp"] = (bool)obs->snmp_enabled;
    mqtt["snmp_community"] = (const char*)obs->snmp_community;

    JsonArray slots = mqtt.createNestedArray("slots");
    for (int i = 0; i < MAX_MQTT_SLOTS; i++) {
      JsonObject s = slots.createNestedObject();
      s["preset"] = (const char*)obs->mqtt_slot_preset[i];
      s["server"] = (const char*)obs->mqtt_slot_host[i];
      s["port"] = obs->mqtt_slot_port[i];
      s["username"] = (const char*)obs->mqtt_slot_username[i];
      s["password"] = obs->mqtt_slot_password[i][0] ? SECRET_SENTINEL : "";
      s["token"] = obs->mqtt_slot_token[i][0] ? SECRET_SENTINEL : "";
      s["topic"] = (const char*)obs->mqtt_slot_topic[i];
      s["audience"] = (const char*)obs->mqtt_slot_audience[i];
      char filter_text[MQTTPacketFilter::kFilterTextSize];
      if (MQTTPacketFilter::format(obs->mqtt_slot_packet_filter[i],
                                   filter_text, sizeof(filter_text))) {
        // Mutable char input is copied into the ArduinoJson document; the
        // stack buffer is reused on the next slot.
        s["filter"] = filter_text;
      } else {
        s["filter"] = "all";
      }
    }
    }
#endif
  }

  AsyncResponseStream* res = req->beginResponseStream("application/json");
  serializeJson(doc, *res);
  req->send(res);
}

void WebConfigServer::handleConfigPost(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  if (!checkAuth(req)) { req->send(401, "application/json", "{\"error\":\"auth\"}"); return; }
  if (req->contentLength() > MAX_BODY) {
    req->send(413, "application/json", "{\"error\":\"body too large\"}");
    return;
  }
  const char* body = (const char*)req->_tempObject;
  DynamicJsonDocument doc(6144);
  if (!body || deserializeJson(doc, body) != DeserializationError::Ok) {
    req->send(400, "application/json", "{\"error\":\"bad json\"}");
    return;
  }
  bool reboot_after = doc["reboot"] | false;
  const char* reqid = doc["reqid"] | "";
  if (!wcIsValidReqId(reqid)) {
    req->send(400, "application/json", "{\"error\":\"bad reqid\"}");
    return;
  }
  JsonObject set = doc["set"];
  for (JsonPair kv : set) {
    if (wcSetKeyRequiresReboot(kv.key().c_str())) {
      // The configured value is intentionally not activated until boot, so a
      // client cannot leave persisted and live ESP-NOW channels disagreeing.
      reboot_after = true;
    }
  }

  WCLock lock(_mux);
  const WebConfigBatch::State bstate = toSpecState(_batch_state);
  const bool reqid_matches = _batch_kind == BATCH_CONFIG
      && strcmp(reqid, _batch_reqid) == 0;
  const WebConfigBatch::PostOutcome pre =
      WebConfigBatch::classifyPost(bstate, reqid_matches, 1, reboot_after);
  if (pre == WebConfigBatch::PostOutcome::Replay) {
    StaticJsonDocument<96> ack;
    ack["state"] = WebConfigBatch::replayStateName(bstate);
    ack["count"] = _batch_count;
    ack["reqid"] = (const char*)_batch_reqid;
    String out;
    serializeJson(ack, out);
    req->send(202, "application/json", out);
    return;
  }
  if (pre == WebConfigBatch::PostOutcome::Busy) {
    StaticJsonDocument<96> busy;
    busy["error"] = "busy";
    busy["reqid"] = (const char*)_batch_reqid;
    String out;
    serializeJson(busy, out);
    req->send(409, "application/json", out);
    return;
  }

  if (_mode == MODE_SETUP && _initial_setup && !set.containsKey("password")
      && (reboot_after || set.containsKey("wifi.ssid"))) {
    req->send(400, "application/json",
              "{\"error\":\"admin password required for initial setup\"}");
    return;
  }

  int count = 0;
  for (JsonPair kv : set) {
    const char* key = kv.key().c_str();
    const char* val = kv.value().as<const char*>();
    const bool admin_pwd = wcIsAdminPasswordKey(key);
    if (!val || (!isAllowedSetKey(key, _mqtt_prefs != NULL) && !admin_pwd)) {
      char safe_key[33];
      strncpy(safe_key, key, sizeof(safe_key) - 1);
      safe_key[sizeof(safe_key) - 1] = 0;
      StaticJsonDocument<128> err;
      err["error"] = "bad key";
      err["key"] = safe_key;
      String out;
      serializeJson(err, out);
      req->send(400, "application/json", out);
      return;
    }
    if (wcSetKeyRequiresReboot(key)) {
      uint8_t channel = 0;
      if (!mesh::wifi::parseEspNowChannel(val, channel)) {
        StaticJsonDocument<128> err;
        err["error"] = "ESP-NOW channel must be 1-13";
        err["key"] = key;
        String out;
        serializeJson(err, out);
        req->send(400, "application/json", out);
        return;
      }
    }
    if (admin_pwd && !wcIsValidAdminPassword(val)) {
      req->send(400, "application/json",
                "{\"error\":\"admin password must be 1-15 characters with no line breaks\"}");
      return;
    }
    if (isSecretKey(key) && strcmp(val, SECRET_SENTINEL) == 0) continue;  // unchanged
    if (count >= MAX_BATCH) {
      req->send(400, "application/json", "{\"error\":\"too many changes\"}");
      return;
    }
    BatchEntry& e = _batch[count];
    strncpy(e.key, key, sizeof(e.key) - 1);
    e.key[sizeof(e.key) - 1] = 0;
    // Build the allowlisted CLI command, stripping CR/LF so a value cannot
    // smuggle in a second command. Admin password uses the top-level command.
    int pos = admin_pwd ? snprintf(e.cmd, sizeof(e.cmd), "password ")
                        : snprintf(e.cmd, sizeof(e.cmd), "set %s ", key);
    bool overflow = false;
    for (const char* p = val; *p; p++) {
      if (*p == '\r' || *p == '\n') continue;
      if (pos >= (int)sizeof(e.cmd) - 1) { overflow = true; break; }
      e.cmd[pos++] = *p;
    }
    e.cmd[pos] = 0;
    // A value that doesn't fit used to be truncated here and applied anyway.
    // For length-checked keys the CLI would reject the remainder, but a key
    // whose grammar stays valid when clipped (mqttN.filter: "advert,2" cut to
    // "advert") would silently persist a *different* setting and still answer
    // OK. Refuse the batch instead -- the caller can shorten and retry.
    if (overflow) {
      // Name the key, like the "bad key" rejection above: a 20-field batch is
      // rejected whole, so without it the operator has nothing to correct.
      char safe_key[33];
      strncpy(safe_key, key, sizeof(safe_key) - 1);
      safe_key[sizeof(safe_key) - 1] = 0;
      StaticJsonDocument<128> ed;
      ed["error"] = "value too long";
      ed["key"] = safe_key;
      String out;
      serializeJson(ed, out);
      req->send(400, "application/json", out);
      return;
    }
    count++;
  }
  if (WebConfigBatch::classifyPost(bstate, reqid_matches, count, reboot_after) ==
      WebConfigBatch::PostOutcome::NoChanges) {
    req->send(400, "application/json", "{\"error\":\"no changes\"}");
    return;
  }
  _batch_kind = BATCH_CONFIG;
  _batch_count = count;
  _batch_next = 0;
  _batch_reboot = reboot_after;
  _batch_reboot_armed = false;
  _batch_all_ok = true;
  strncpy(_batch_reqid, reqid, sizeof(_batch_reqid) - 1);
  _batch_reqid[sizeof(_batch_reqid) - 1] = 0;
  _standalone_wifi_dirty = false;
  _setup_wifi_handoff_pending = false;
  _setup_wifi_handoff_deadline = 0;
  _setup_wifi_handoff_ip[0] = 0;
  _batch_state = BATCH_PENDING;  // tick() picks it up on the loop task
  uint32_t du = millis() + 60000;
  if (du == 0) du = 1;
  _diag_until = du;
  mesh::usbLoggingPort().printf(
      "WC: config POST accepted, %d cmds, reboot=%d\n",
      count, (int)reboot_after);

  StaticJsonDocument<96> ack;
  ack["state"] = "pending";
  ack["count"] = count;
  ack["reqid"] = (const char*)_batch_reqid;
  String out;
  serializeJson(ack, out);
  req->send(202, "application/json", out);
}

void WebConfigServer::handleConfigResult(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) {
    mesh::usbLoggingPort().println("WC: result read -> 503 (mode off)");
    req->send(503);
    return;
  }
  if (!checkAuth(req)) {
    mesh::usbLoggingPort().println("WC: result read -> 401");
    req->send(401, "application/json", "{\"error\":\"auth\"}");
    return;
  }
  if (!req->hasParam("reqid")) {
    req->send(400, "application/json", "{\"error\":\"bad reqid\"}");
    return;
  }
  String requested_reqid = req->getParam("reqid")->value();
  if (!wcIsValidReqId(requested_reqid.c_str())) {
    req->send(400, "application/json", "{\"error\":\"bad reqid\"}");
    return;
  }

  // Entry print BEFORE the lock (racy state read is fine for diag): if this
  // fires but no branch print follows, the handler is blocked on _mux.
  mesh::usbLoggingPort().printf(
      "WC: result entry mode=%d state=%d\n",
      (int)_mode, (int)_batch_state);
  WCLock lock(_mux);
  // A CLI sequence occupying the shared slot is not a config save, whatever the
  // reqid says: its entries have no `key` and its results belong to the
  // terminal's reader. Treat it as unknown here (and vice versa there).
  const bool mine = (_batch_kind == BATCH_CONFIG) &&
                    (strcmp(requested_reqid.c_str(), _batch_reqid) == 0);
  const WebConfigBatch::ResultOutcome outcome =
      WebConfigBatch::classifyResult(toSpecState(_batch_state), mine);
  if (outcome == WebConfigBatch::ResultOutcome::Idle) {
    mesh::usbLoggingPort().println("WC: result read -> idle");
    StaticJsonDocument<64> idle;
    idle["state"] = "idle";
    idle["reqid"] = requested_reqid;
    String out;
    serializeJson(idle, out);
    req->send(200, "application/json", out);
    return;
  }
  if (outcome == WebConfigBatch::ResultOutcome::Unknown) {
    req->send(404, "application/json", "{\"error\":\"unknown request\"}");
    return;
  }
  if (outcome == WebConfigBatch::ResultOutcome::Pending) {
    StaticJsonDocument<96> pending;
    pending["state"] = "pending";
    pending["reqid"] = (const char*)_batch_reqid;
    String out;
    serializeJson(pending, out);
    req->send(200, "application/json", out);
    return;
  }
  mesh::usbLoggingPort().printf(
      "WC: result read -> done (reboot=%d armed=%d all_ok=%d)\n",
      (int)_batch_reboot, (int)_batch_reboot_armed,
      (int)_batch_all_ok);
  DynamicJsonDocument doc(6144);
  doc["state"] = "done";
  doc["reboot"] =
      WebConfigBatch::doneReportsReboot(_batch_reboot, _batch_all_ok);
  doc["all_ok"] = _batch_all_ok;
  doc["reqid"] = (const char*)_batch_reqid;
  if (_setup_wifi_handoff_ip[0]) {
    doc["wifi_ip"] = (const char*)_setup_wifi_handoff_ip;
  }
  JsonArray results = doc.createNestedArray("results");
  for (int i = 0; i < _batch_count; i++) {
    JsonObject r = results.createNestedObject();
    r["key"] = (const char*)_batch[i].key;
    r["reply"] = (const char*)_batch[i].reply;
  }
  // State stays DONE (re-readable) until the next POST claims the slot.
  const uint32_t result_now = millis();
  if (WebConfigBatch::shouldArmConfirmReboot(
          toSpecState(_batch_state), _batch_reboot,
          _batch_all_ok, _batch_reboot_armed)) {
    // Confirmation delivered. The handoff page remains rendered in the
    // browser after the AP disappears, so it only needs a one-second response
    // flush before reboot. Armed once; re-reads cannot push the deadline out.
    _batch_reboot_armed = true;
    const bool setup_handoff = _setup_wifi_handoff_ip[0] != 0;
    const uint32_t reboot_delay = setup_handoff
        ? WebConfigBatch::kSetupHandoffRebootConfirmMs
        : WebConfigBatch::kRebootConfirmMs;
    doc["reboot_in_ms"] = reboot_delay;
    _reboot_at = setup_handoff
        ? WebConfigBatch::confirmSetupHandoffRebootAt(result_now)
        : WebConfigBatch::confirmRebootAt(result_now);
  } else if (WebConfigBatch::doneReportsReboot(
                 _batch_reboot, _batch_all_ok) && _reboot_at != 0) {
    // A retry after a lost HTTP response gets the same IP plus an accurate
    // remaining countdown; reading again never postpones the reboot.
    doc["reboot_in_ms"] = WebConfigBatch::deadlineReached(
        result_now, _reboot_at) ? 1u : _reboot_at - result_now;
  }

  AsyncResponseStream* res = req->beginResponseStream("application/json");
  serializeJson(doc, *res);
  req->send(res);
}

// ---------------------------------------------------------------------------
// CLI terminal (/api/cli). Same 202 + reqid + poll contract as a config save,
// and for the same reason: CommonCLI touches prefs, the radio and the
// filesystem, none of which may be reached from the async_tcp task. The
// commands go into the shared deferred slot and tick() drains them.
//
// Unlike a save this is not allowlisted. It is restricted to authenticated LAN
// mode and uses the remote-admin callback, so serial-only secrets stay hidden.
// ---------------------------------------------------------------------------

// Commands whose CLI handler never returns would take the node down mid-drain,
// before the client could read a single result. `reboot` is deferred instead:
// it is not passed to the CLI at all, and the batch arms the ordinary reboot
// path once the operator has read the results. The rest (clkreboot, poweroff,
// ota update) do real work on the way down and cannot be faked, so they run
// normally and the connection drops -- the UI warns before sending them.
void WebConfigServer::handleCliPost(AsyncWebServerRequest* req) {
  if (_mode != MODE_LAN) {
    req->send(403, "application/json",
              "{\"error\":\"terminal requires LAN mode\"}");
    return;
  }
  if (!_cb->supportsCliTerminal()) {
    req->send(404, "application/json",
              "{\"error\":\"terminal unavailable on this role\"}");
    return;
  }
  if (!_cli_enabled) {
    req->send(403, "application/json",
              "{\"error\":\"terminal disabled; set wifi.cli on\"}");
    return;
  }
  if (!checkAuth(req)) { req->send(401, "application/json", "{\"error\":\"auth\"}"); return; }
  if (req->contentLength() > MAX_BODY || req->_tempObject == NULL) {
    req->send(413, "application/json", "{\"error\":\"body too large\"}");
    return;
  }
  const char* body = (const char*)req->_tempObject;
  DynamicJsonDocument doc(6144);
  if (!body || deserializeJson(doc, body) != DeserializationError::Ok) {
    req->send(400, "application/json", "{\"error\":\"bad json\"}");
    return;
  }
  const char* reqid = doc["reqid"] | "";
  if (!wcIsValidReqId(reqid)) {
    req->send(400, "application/json", "{\"error\":\"bad reqid\"}");
    return;
  }
  JsonArray cmds = doc["cmds"];
  if (cmds.isNull()) {
    req->send(400, "application/json", "{\"error\":\"no commands\"}");
    return;
  }

  WCLock lock(_mux);
  // Replay/Busy exactly as a config save classifies them: a repeated POST is
  // acknowledged rather than executed twice, and a different sequence while one
  // is still draining is refused.
  const WebConfigBatch::State bstate = toSpecState(_batch_state);
  const bool reqid_matches = _batch_kind == BATCH_CLI
      && strcmp(reqid, _batch_reqid) == 0;
  const WebConfigBatch::PostOutcome pre =
      WebConfigBatch::classifyPost(bstate, reqid_matches, 1 /* count unknown yet */, false);
  if (pre == WebConfigBatch::PostOutcome::Replay) {
    StaticJsonDocument<96> ack;
    ack["state"] = (bstate == WebConfigBatch::State::Done) ? "done" : "running";
    ack["total"] = _batch_count;
    ack["reqid"] = (const char*)_batch_reqid;
    String out;
    serializeJson(ack, out);
    req->send(202, "application/json", out);
    return;
  }
  if (pre == WebConfigBatch::PostOutcome::Busy) {
    StaticJsonDocument<96> bd;
    bd["error"] = "busy";
    bd["reqid"] = (const char*)_batch_reqid;
    String out;
    serializeJson(bd, out);
    req->send(409, "application/json", out);
    return;
  }

  int count = 0;
  bool defer_reboot = false, seq_sets_pwd = false, seq_sets_ssid = false;
  for (JsonVariant v : cmds) {
    const char* raw = v.as<const char*>();
    if (!raw) continue;
    if (count >= MAX_BATCH) {
      StaticJsonDocument<96> ed;
      ed["error"] = "too many commands";
      ed["max"] = MAX_BATCH;
      String out;
      serializeJson(ed, out);
      req->send(413, "application/json", out);
      return;
    }
    // Strip CR/LF so one entry cannot smuggle a second command past the
    // operator's confirmation, and skip whatever is left blank.
    BatchEntry& e = _batch[count];
    int pos = 0;
    for (const char* p = raw; *p; p++) {
      if (*p == '\r' || *p == '\n') continue;
      if (pos == 0 && (*p == ' ' || *p == '\t')) continue;   // leading space
      if (pos >= (int)sizeof(e.cmd) - 1) {
        req->send(400, "application/json", "{\"error\":\"command too long\"}");
        return;
      }
      e.cmd[pos++] = *p;
    }
    while (pos > 0 && (e.cmd[pos - 1] == ' ' || e.cmd[pos - 1] == '\t')) pos--;
    e.cmd[pos] = 0;
    if (pos == 0) continue;
    // Reject before anything runs, so a sequence never half-applies and then
    // stops on a command that was never going to work here.
    const char* why = wcCliUnavailable(e.cmd);
    if (why) {
      StaticJsonDocument<256> ed;
      ed["error"] = why;
      String out;
      serializeJson(ed, out);
      req->send(400, "application/json", out);
      return;
    }
    e.key[0] = 0;                       // CLI entries have no config key
    if (wcIsDeferredReboot(e.cmd)) defer_reboot = true;
    if (strncmp(e.cmd, "password ", 9) == 0) seq_sets_pwd = true;
    if (strncmp(e.cmd, "set wifi.ssid ", 14) == 0) seq_sets_ssid = true;
    count++;
  }
  if (count == 0) {
    req->send(400, "application/json", "{\"error\":\"no commands\"}");
    return;
  }

  // The same invariant handleConfigPost enforces, and for the same reason: the
  // reboot is what commits first onboarding, and a node that reboots onto the
  // LAN still holding the factory password is a known credential on someone
  // else's network. The terminal warned about this client-side, which is a
  // reminder, not a rule -- a pasted script or a direct POST ignored it.
  if (_mode == MODE_SETUP && _initial_setup && !seq_sets_pwd && !_admin_pwd_set &&
      (defer_reboot || seq_sets_ssid)) {
    req->send(400, "application/json",
              "{\"error\":\"admin password required for initial setup -- "
              "run `password <new-password>` first\"}");
    return;
  }

  _batch_kind = BATCH_CLI;
  _batch_count = count;
  _batch_next = 0;
  _batch_reboot = defer_reboot;
  _batch_reboot_armed = false;
  _batch_all_ok = true;
  strncpy(_batch_reqid, reqid, sizeof(_batch_reqid) - 1);
  _batch_reqid[sizeof(_batch_reqid) - 1] = 0;
  _batch_state = BATCH_PENDING;         // tick() picks it up on the loop task
  mesh::usbLoggingPort().printf(
      "WC: cli POST accepted, %d cmds, reboot=%d\n",
      count, (int)defer_reboot);

  StaticJsonDocument<96> ack;
  ack["state"] = "running";
  ack["total"] = count;
  ack["reqid"] = (const char*)_batch_reqid;
  String out;
  serializeJson(ack, out);
  req->send(202, "application/json", out);
}

void WebConfigServer::handleCliResult(AsyncWebServerRequest* req) {
  if (_mode != MODE_LAN) {
    req->send(403, "application/json",
              "{\"error\":\"terminal requires LAN mode\"}");
    return;
  }
  if (!_cb->supportsCliTerminal()) {
    req->send(404, "application/json",
              "{\"error\":\"terminal unavailable on this role\"}");
    return;
  }
  if (!_cli_enabled) {
    req->send(403, "application/json",
              "{\"error\":\"terminal disabled; set wifi.cli on\"}");
    return;
  }
  if (!checkAuth(req)) { req->send(401, "application/json", "{\"error\":\"auth\"}"); return; }
  if (!req->hasParam("reqid")) {
    req->send(400, "application/json", "{\"error\":\"bad reqid\"}");
    return;
  }
  String requested_reqid = req->getParam("reqid")->value();
  if (!wcIsValidReqId(requested_reqid.c_str())) {
    req->send(400, "application/json", "{\"error\":\"bad reqid\"}");
    return;
  }
  int from = 0;
  if (req->hasParam("from")) {
    from = req->getParam("from")->value().toInt();
    if (from < 0) from = 0;
  }

  WCLock lock(_mux);
  // A config save occupying the slot is not this client's sequence, whatever
  // the reqid says; treat it as unknown rather than serving `set` results
  // through the terminal's reader.
  const bool mine = (_batch_kind == BATCH_CLI) &&
                    (strcmp(requested_reqid.c_str(), _batch_reqid) == 0);
  const WebConfigBatch::ResultOutcome outcome =
      WebConfigBatch::classifyResult(toSpecState(_batch_state), mine);
  if (outcome == WebConfigBatch::ResultOutcome::Idle) {
    StaticJsonDocument<64> idle;
    idle["state"] = "idle";
    idle["reqid"] = requested_reqid;
    String out;
    serializeJson(idle, out);
    req->send(200, "application/json", out);
    return;
  }
  if (outcome == WebConfigBatch::ResultOutcome::Unknown) {
    req->send(404, "application/json", "{\"error\":\"unknown request\"}");
    return;
  }

  // Results stream: hand back whatever has drained since the client's cursor,
  // capped so the document stays small on the async_tcp task.
  const int produced = _batch_next;
  const int page = WebConfigBatch::cliPageCount(from, produced, WebConfigBatch::kCliResultPage);
  const bool final_read = WebConfigBatch::cliReadIsFinal(toSpecState(_batch_state),
                                                         from, page, _batch_count);
  DynamicJsonDocument doc(4096);
  doc["state"] = final_read ? "done" : "running";
  doc["reqid"] = (const char*)_batch_reqid;
  doc["total"] = _batch_count;
  doc["from"] = from;
  JsonArray results = doc.createNestedArray("results");
  for (int i = from; i < from + page; i++) {
    JsonObject r = results.createNestedObject();
    // The command is deliberately NOT echoed: it may hold a password or token,
    // and the client already has the sequence it sent. It matches by index.
    r["ok"] = !WebConfigBatch::cliReplyIsFailure(_batch[i].reply);
    r["reply"] = (const char*)_batch[i].reply;
  }
  if (final_read) {
    doc["all_ok"] = _batch_all_ok;
    const bool rebooting = WebConfigBatch::cliRebootAllowed(_batch_reboot, _batch_all_ok);
    doc["reboot"] = rebooting;
    // Tell the operator why a `reboot` they asked for is not happening.
    if (_batch_reboot && !_batch_all_ok) doc["reboot_withheld"] = true;
    if (WebConfigBatch::shouldArmConfirmReboot(toSpecState(_batch_state), _batch_reboot,
                                               _batch_all_ok, _batch_reboot_armed)) {
      _batch_reboot_armed = true;
      _reboot_at = WebConfigBatch::confirmRebootAt(millis());
    }
  }
  AsyncResponseStream* res = req->beginResponseStream("application/json");
  serializeJson(doc, *res);
  req->send(res);
}

void WebConfigServer::handleStats(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  if (!checkAuth(req)) { req->send(401, "application/json", "{\"error\":\"auth\"}"); return; }
  uint32_t until = millis() + 15000;
  if (until == 0) until = 1;
  _stats_wanted_until = until;  // tick() refreshes the snapshot while polled

  WCLock lock(_mux);
  if (_stats_json[0] == 0) {
    req->send(200, "application/json", "{\"state\":\"pending\"}");
    return;
  }
  req->send(200, "application/json", _stats_json);
}

void WebConfigServer::handleScan(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  if (!checkAuth(req)) { req->send(401, "application/json", "{\"error\":\"auth\"}"); return; }

  int n = WiFi.scanComplete();
  if (req->hasParam("rescan") && n >= 0) {
    WiFi.scanDelete();
    n = WIFI_SCAN_FAILED;
  }
  if (n == WIFI_SCAN_FAILED) {
    WiFi.scanNetworks(true, false, false, 300,
                      mesh::wifi::stationScanChannel());
    req->send(200, "application/json", "{\"state\":\"scanning\"}");
    return;
  }
  if (n < 0) {  // WIFI_SCAN_RUNNING
    req->send(200, "application/json", "{\"state\":\"scanning\"}");
    return;
  }
  DynamicJsonDocument doc(3072);
  doc["state"] = "done";
  JsonArray nets = doc.createNestedArray("networks");
  for (int i = 0; i < n && i < 20; i++) {
    JsonObject net = nets.createNestedObject();
    net["ssid"] = WiFi.SSID(i);
    net["rssi"] = WiFi.RSSI(i);
    net["enc"] = WiFi.encryptionType(i) != WIFI_AUTH_OPEN;
    net["channel"] = WiFi.channel(i);
  }
  AsyncResponseStream* res = req->beginResponseStream("application/json");
  serializeJson(doc, *res);
  req->send(res);
}

void WebConfigServer::handlePresets(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  _last_activity = millis();
#ifdef WITH_MQTT_BRIDGE
  if (_mqtt_prefs == NULL) {
    req->send(200, "application/json", "{\"presets\":[]}");
    return;
  }
  DynamicJsonDocument doc(3072);
  JsonArray arr = doc.createNestedArray("presets");
  for (int i = 0; i < MQTT_PRESET_COUNT; i++) {
    const MQTTPresetDef& p = MQTT_PRESETS[i];
    JsonObject o = arr.createNestedObject();
    o["name"] = p.name;
    // What the UI must collect for this preset to connect
    if (p.topic_style == MQTT_TOPIC_MESHRANK) {
      o["needs"] = "token";
    } else if (mqttPresetNeedsSlotUsername(&p) && mqttPresetNeedsSlotPassword(&p)) {
      o["needs"] = "userpass";
    } else if (mqttPresetNeedsSlotPassword(&p)) {
      o["needs"] = "password";
    } else if (mqttPresetNeedsSlotUsername(&p)) {
      o["needs"] = "userpass";
    } else {
      o["needs"] = "none";
    }
  }
  AsyncResponseStream* res = req->beginResponseStream("application/json");
  serializeJson(doc, *res);
  req->send(res);
#else
  req->send(200, "application/json", "{\"presets\":[]}");
#endif
}

void WebConfigServer::handleReboot(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { req->send(503); return; }
  if (!checkAuth(req)) { req->send(401, "application/json", "{\"error\":\"auth\"}"); return; }
  _reboot_at = millis() + 1500;
  if (_reboot_at == 0) _reboot_at = 1;
  req->send(200, "application/json", "{\"ok\":true}");
}

void WebConfigServer::handlePortalExit(AsyncWebServerRequest* req) {
  // Setup mode only: switch captive probes to native "success" replies so the
  // OS sign-in sheet can be dismissed (iOS: "Done") without dropping the WiFi;
  // the user then continues at http://<softAP IP>/ in their real browser,
  // which survives the phone sleeping (the captive sheet does not).
  if (_mode != MODE_SETUP) { req->send(404); return; }
  _captive_release = true;
  _last_activity = millis();
  char body[64];
  snprintf(body, sizeof(body), "{\"ok\":true,\"url\":\"http://%s/\"}",
           WiFi.softAPIP().toString().c_str());
  req->send(200, "application/json", body);
}

void WebConfigServer::handleNotFound(AsyncWebServerRequest* req) {
  // Captive-portal probes (/generate_204, /hotspot-detect.html, /ncsi.txt,
  // /connecttest.txt, ...) all land here; a redirect to the portal makes the
  // phone pop its sign-in sheet.
  if (_mode == MODE_SETUP && req->method() == HTTP_GET) {
    if (_captive_release) {
      // Answer each OS's connectivity check natively so the sheet reports
      // success and can be closed. Deliberately does NOT bump _last_activity:
      // background probes must not hold the portal open past the idle timeout.
      const String& url = req->url();
      if (url.indexOf("generate_204") >= 0 || url.indexOf("gen_204") >= 0) {
        req->send(204);                                       // Android
      } else if (url.indexOf("hotspot-detect") >= 0 || url.indexOf("success") >= 0) {
        req->send(200, "text/html",                            // Apple CNA
                  "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>");
      } else if (url.indexOf("ncsi.txt") >= 0) {
        req->send(200, "text/plain", "Microsoft NCSI");        // Windows
      } else if (url.indexOf("connecttest.txt") >= 0) {
        req->send(200, "text/plain", "Microsoft Connect Test");
      } else {
        req->send(404);
      }
      return;
    }
    _last_activity = millis();
    req->redirect(String("http://") + WiFi.softAPIP().toString() + "/");
    return;
  }
  req->send(404);
}

#endif  // WITH_WEBCONFIG
