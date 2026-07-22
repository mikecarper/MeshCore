#include "WebConfigServer.h"

#ifdef WITH_WEBCONFIG

#include <WiFi.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <DNSServer.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <esp_wifi.h>
#include <esp_system.h>
#include <esp_heap_caps.h>

#ifdef WITH_MQTT_BRIDGE
  #include <helpers/MQTTPrefs.h>
  #include <helpers/MQTTPresets.h>
  #include <helpers/bridges/MQTTBridge.h>
#endif

#include "WebConfigHtml.h"

// Placeholder sent instead of stored secrets; POSTs carrying it are dropped
// so an untouched password field never overwrites the stored value.
static const char SECRET_SENTINEL[] = "********";

// Keys the web UI may drive through the CLI `set` handlers. Everything else
// is rejected, so a crafted request can't reach arbitrary commands (`erase`,
// `password`, ...) through the batch.
static const char* const ALLOWED_SET_KEYS[] = {
  // NodePrefs (radio / node)
  "name", "lat", "lon", "radio", "tx", "af", "rxdelay", "txdelay",
  "cad", "radio.rxgain", "radio.fem.rxgain", "repeat", "advert.interval",
  "flood.advert.interval",
  "flood.max", "flood.max.advert", "flood.max.unscoped", "loop.detect",
  // MQTTPrefs (WiFi / MQTT / misc observer)
  "wifi.ssid", "wifi.pwd", "wifi.powersave",
  "mqtt.origin", "mqtt.iata", "mqtt.status", "mqtt.packets", "mqtt.raw",
  "mqtt.tx", "mqtt.rx", "mqtt.interval", "mqtt.ntp", "mqtt.owner", "mqtt.email",
  "timezone", "timezone.offset", "snmp", "snmp.community",
};
static const char* const ALLOWED_SLOT_KEYS[] = {
  "preset", "server", "port", "username", "password", "token", "topic", "audience",
};

static bool isAllowedSetKey(const char* key, bool has_mqtt) {
  if (!key) return false;
  for (size_t i = 0; i < sizeof(ALLOWED_SET_KEYS) / sizeof(ALLOWED_SET_KEYS[0]); i++) {
    if (strcmp(key, ALLOWED_SET_KEYS[i]) == 0) {
      const bool mqtt_only = strncmp(key, "mqtt.", 5) == 0
                          || strncmp(key, "timezone", 8) == 0
                          || strncmp(key, "snmp", 4) == 0;
      return has_mqtt || !mqtt_only;
    }
  }
  // mqtt<1-6>.<field>
#ifdef WITH_MQTT_BRIDGE
  if (!has_mqtt) return false;
  if (strlen(key) > 6 && strncmp(key, "mqtt", 4) == 0
      && key[4] >= '1' && key[4] <= ('0' + MAX_MQTT_SLOTS)
      && key[5] == '.') {
    for (size_t i = 0; i < sizeof(ALLOWED_SLOT_KEYS) / sizeof(ALLOWED_SLOT_KEYS[0]); i++) {
      if (strcmp(&key[6], ALLOWED_SLOT_KEYS[i]) == 0) return true;
    }
  }
#else
  (void)has_mqtt;
#endif
  return false;
}

static bool isSecretKey(const char* key) {
  if (!key) return false;
  if (strcmp(key, "wifi.pwd") == 0) return true;
  if (strlen(key) > 6 && strncmp(key, "mqtt", 4) == 0 && key[5] == '.'
      && (strcmp(&key[6], "password") == 0 || strcmp(&key[6], "token") == 0)) return true;
  return false;
}

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

// RAII lock; tolerates a null mutex (allocation failure) by not locking.
struct WCLock {
  SemaphoreHandle_t h;
  explicit WCLock(SemaphoreHandle_t s) : h(s) { if (h) xSemaphoreTake(h, portMAX_DELAY); }
  ~WCLock() { if (h) xSemaphoreGive(h); }
};

WebConfigServer* WebConfigServer::_active = NULL;
static volatile bool webconfig_button_toggle_requested = false;

WebConfigServer::WebConfigServer(Callbacks* callbacks, void* mqtt_prefs, bool owns_wifi,
                                 const uint8_t* pub_key, const char* fw_ver,
                                 const char* role, const char* board_name)
    : _cb(callbacks), _mqtt_prefs(mqtt_prefs), _owns_wifi(owns_wifi), _pub_key(pub_key),
      _fw_ver(fw_ver), _role(role), _board_name(board_name) {
  _mux = xSemaphoreCreateMutex();
  _active = this;

#ifdef WITH_MQTT_BRIDGE
  MQTTPrefs* obs = static_cast<MQTTPrefs*>(_mqtt_prefs);
  if (obs) {
    strncpy(_wifi_ssid, obs->wifi_ssid, sizeof(_wifi_ssid) - 1);
    strncpy(_wifi_password, obs->wifi_password, sizeof(_wifi_password) - 1);
    _wifi_power_save = obs->wifi_power_save <= 2 ? obs->wifi_power_save : 1;
    // Companion builds keep their canonical connection credentials in the
    // shared mesh-wifi namespace. A fresh MQTT configuration can therefore
    // have an empty MQTTPrefs WiFi field even while the companion is already
    // connected; use the canonical copy until the first WebUI save syncs both.
    if (_wifi_ssid[0] == 0 && !_owns_wifi) {
      loadStandaloneWiFi(_wifi_ssid, sizeof(_wifi_ssid),
                         _wifi_password, sizeof(_wifi_password), &_wifi_power_save);
    }
  } else
#endif
  {
    loadStandaloneWiFi(_wifi_ssid, sizeof(_wifi_ssid),
                       _wifi_password, sizeof(_wifi_password), &_wifi_power_save);
  }
}

WebConfigServer::~WebConfigServer() {
  if (_active == this) _active = NULL;
  if (_mux) vSemaphoreDelete(_mux);
}

bool WebConfigServer::loadEnabled(bool default_value) {
  Preferences nvs;
  if (!nvs.begin("mesh-webui", true)) return default_value;
  bool enabled = nvs.getBool("enabled", default_value);
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

bool WebConfigServer::loadStandaloneWiFi(char* ssid, size_t ssid_len,
                                         char* password, size_t password_len,
                                         uint8_t* power_save) {
  if (!ssid || ssid_len == 0 || !password || password_len == 0) return false;
  ssid[0] = 0;
  password[0] = 0;
  Preferences nvs;
  if (!nvs.begin("mesh-wifi", true)) return false;
  String stored_ssid = nvs.getString("ssid", "");
  String stored_password = nvs.getString("password", "");
  uint8_t stored_ps = nvs.getUChar("powersave", 1);
  nvs.end();
  if (stored_ssid.length() == 0 || stored_ssid.length() >= ssid_len
      || stored_password.length() >= password_len) return false;
  strncpy(ssid, stored_ssid.c_str(), ssid_len - 1);
  ssid[ssid_len - 1] = 0;
  strncpy(password, stored_password.c_str(), password_len - 1);
  password[password_len - 1] = 0;
  if (power_save) *power_save = stored_ps <= 2 ? stored_ps : 1;
  return true;
}

bool WebConfigServer::saveStandaloneWiFi(const char* ssid, const char* password,
                                         uint8_t power_save) {
  if (!ssid || !ssid[0] || strlen(ssid) >= 32
      || (password && strlen(password) >= 64) || power_save > 2) {
    return false;
  }
  Preferences nvs;
  if (!nvs.begin("mesh-wifi", false)) return false;
  const char* pwd = password ? password : "";
  bool ok = nvs.putString("ssid", ssid) == strlen(ssid);
  nvs.putString("password", pwd);  // empty String legitimately writes zero bytes
  ok = ok && nvs.getString("password", "\x01") == pwd;
  ok = ok && nvs.putUChar("powersave", power_save) == sizeof(uint8_t);
  nvs.end();
  return ok;
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
  return w != NULL && w->_reboot_at != 0 && w->_batch_reboot &&
         w->_batch_state == BATCH_DONE;
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
  // AP_STA (not pure AP) so the WiFi scan for the SSID picker works while
  // the AP is up. STA stays unconnected - the bridge won't touch WiFi
  // while wifi_ssid is empty, and `start webconfig ap` requires it stopped.
  WiFi.mode(WIFI_AP_STA);
  snprintf(_ap_ssid, sizeof(_ap_ssid), "MeshCore-Setup-%02X%02X", _pub_key[0], _pub_key[1]);
#ifdef WEBCONFIG_AP_PASSWORD
  bool ap_ok = WiFi.softAP(_ap_ssid, WEBCONFIG_AP_PASSWORD);
#else
  bool ap_ok = WiFi.softAP(_ap_ssid);
#endif
  if (!ap_ok) {
    if (!promote_lan) WiFi.mode(WIFI_OFF);
    strcpy(reply, "Err: failed to start AP");
    return false;
  }
  delay(100);  // let the AP netif settle before reading its IP
  IPAddress ip = WiFi.softAPIP();

  _dns = new DNSServer();
  _dns->start(53, "*", ip);  // captive portal: every name resolves to us

  if (!promote_lan) createServer();
  _mode = MODE_SETUP;
  _was_setup_ap = true;
  _last_activity = millis();
  WiFi.scanNetworks(true);  // pre-populate the SSID picker

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
  createServer();
  _mode = MODE_LAN;
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
  WiFi.setAutoReconnect(true);
  WiFi.begin(_wifi_ssid, _wifi_password);
  wifi_ps_type_t ps_mode = _wifi_power_save == 1 ? WIFI_PS_NONE
                            : _wifi_power_save == 2 ? WIFI_PS_MAX_MODEM
                            : WIFI_PS_MIN_MODEM;
  esp_wifi_set_ps(ps_mode);
  _mode = MODE_CONNECTING;
  _connect_deadline = millis() + 15000;
  if (_connect_deadline == 0) _connect_deadline = 1;
  snprintf(reply, 160, "WebConfig connecting to '%s'; use 'get webui' for its IP", _wifi_ssid);
  return true;
}

void WebConfigServer::createServer() {
  _server = new AsyncWebServer(80);
  // iOS caches plain-HTTP GETs aggressively (keyed by URL, surviving even a
  // device reflash behind the same IP), which poisons /api/config/result and
  // friends with stale responses from earlier sessions. Forbid caching on
  // every response; the HTML is small enough to refetch per visit.
  // DefaultHeaders is process-global and survives a stop/start cycle. Adding
  // this on every start would retain duplicate header nodes indefinitely.
  static bool cache_header_added = false;
  if (!cache_header_added) {
    DefaultHeaders::Instance().addHeader("Cache-Control", "no-store");
    cache_header_added = true;
  }
  registerRoutes();
  _server->begin();
}

void WebConfigServer::requestStop() {
  if (_mode == MODE_OFF && !_stopping) return;
  if (_server) _server->end();
  if (_dns) _dns->stop();
  _mode = MODE_OFF;
  _stopping = true;
  // Deleting an AsyncWebServer with live connections is a known crash source;
  // give in-flight responses a grace period before freeing.
  _delete_at = millis() + 2000;
  if (_delete_at == 0) _delete_at = 1;
}

void WebConfigServer::finalizeTeardown() {
  delete _server;
  _server = NULL;
  delete _dns;
  _dns = NULL;
  const bool was_setup_ap = _was_setup_ap;
  if (was_setup_ap) {
    WiFi.softAPdisconnect(true);
    // Nothing else owns WiFi when we raised the AP: either the node is
    // unconfigured, or `start webconfig ap` required the bridge stopped.
    if (_wifi_ssid[0] == 0 || _owns_wifi) {
      WiFi.mode(WIFI_OFF);
    } else {
      WiFi.mode(WIFI_STA);
    }
    _was_setup_ap = false;
  }
  if (_owns_wifi && !was_setup_ap) {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
  }
  _stopping = false;
  _delete_at = 0;
  _connect_deadline = 0;
  _reboot_at = 0;
  _batch_state = BATCH_IDLE;
  _batch_next = 0;
  _batch_reboot_armed = false;
  _session_token[0] = 0;
  _stats_json[0] = 0;
  if (_cb) _cb->onWebConfigStopped();
}

void WebConfigServer::tick(uint32_t now) {
  if (_stopping) {
    if (_delete_at && (int32_t)(now - _delete_at) >= 0) finalizeTeardown();
    return;
  }
  if (_mode == MODE_OFF) return;

  if (_mode == MODE_CONNECTING) {
    if (WiFi.status() == WL_CONNECTED) {
      createServer();
      _mode = MODE_LAN;
      _connect_deadline = 0;
      _last_activity = now;
      Serial.printf("WebConfig ready: http://%s/\n", WiFi.localIP().toString().c_str());
    } else if (_connect_deadline && (int32_t)(now - _connect_deadline) >= 0) {
      Serial.printf("WebConfig: WiFi '%s' unavailable; opening setup AP\n", _wifi_ssid);
      WiFi.disconnect(true);
      _mode = MODE_OFF;
      _connect_deadline = 0;
      char ignored[160];
      startSetupMode(ignored);
      Serial.println(ignored);
    }
    return;
  }

  if (_dns) _dns->processNextRequest();

  if (_batch_state == BATCH_PENDING) drainBatch(now);

  if (_reboot_at && (int32_t)(now - _reboot_at) >= 0) {
    Serial.printf("WC: rebooting now (%s)\n", _batch_reboot_armed ? "confirmed" : "fallback");
    _cb->rebootNow();  // does not return
  }

  if ((int32_t)(_diag_until - now) > 0 && (now - _diag_last) >= 1000) {
    _diag_last = now;
    Serial.printf("WC: diag sta=%d heap=%u batch=%d/%d state=%d\n",
                  (int)WiFi.softAPgetStationNum(), (unsigned)ESP.getFreeHeap(),
                  (int)_batch_next, (int)_batch_count, (int)_batch_state);
  }

  // Refresh the stats snapshot only while a client is actually polling.
  if ((int32_t)(_stats_wanted_until - now) > 0 && (now - _stats_built_at) >= 2000) {
    WCLock lock(_mux);
    _cb->buildStatsJson(_stats_json, sizeof(_stats_json));
    _stats_built_at = now;
  }

  // Idle timeout: only the setup AP auto-stops (a deployed node must not be
  // left broadcasting an open AP). LAN mode runs until `stop webconfig`.
  if (_mode == MODE_SETUP && WiFi.softAPgetStationNum() == 0 &&
      (now - _last_activity) > WEBCONFIG_AP_IDLE_TIMEOUT_MS) {
    requestStop();
  }
}

void WebConfigServer::drainBatch(uint32_t now) {
  // One command per call, spaced out. Each `set` persists prefs with a flash
  // write, and flash writes stall the WiFi task (flash cache off); running a
  // whole batch back-to-back starves the softAP of beacons long enough for
  // clients (iPhones especially) to drop off mid-save.
  if (_batch_next == 0) {
    WCLock prefs_lock(_mux);
    _cb->onConfigBatchStart();
  } else if (_batch_next < _batch_count && (int32_t)(now - _batch_last_cmd) < 25) {
    return;  // let the WiFi task breathe between flash writes
  }
  if (_batch_next < _batch_count) {
    WCLock prefs_lock(_mux);
    BatchEntry& e = _batch[_batch_next++];
    e.reply[0] = 0;
    uint32_t t0 = millis();
    const char* value = e.cmd + strlen("set ") + strlen(e.key) + 1;
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
      if (strlen(value) >= sizeof(_wifi_password)) {
        strcpy(e.reply, "Error: WiFi password must be at most 63 characters");
      } else {
        strncpy(_wifi_password, value, sizeof(_wifi_password) - 1);
        _wifi_password[sizeof(_wifi_password) - 1] = 0;
        _standalone_wifi_dirty = true;
        strcpy(e.reply, "OK");
      }
    } else if ((_mqtt_prefs == NULL || !_owns_wifi) && strcmp(e.key, "wifi.powersave") == 0) {
      if (strcmp(value, "min") == 0) _wifi_power_save = 0;
      else if (strcmp(value, "none") == 0) _wifi_power_save = 1;
      else if (strcmp(value, "max") == 0) _wifi_power_save = 2;
      else strcpy(e.reply, "Error: must be none, min, or max");
      if (e.reply[0] == 0) {
        _standalone_wifi_dirty = true;
        strcpy(e.reply, "OK");
      }
    } else {
      _cb->execCommand(e.cmd, e.reply);
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
    // A wizard save must not reboot away from a rejected setting before the
    // operator can correct it. A reboot-only batch (zero commands) is still
    // allowed; otherwise every command must report success.
    if (_batch_reboot && strncmp(e.reply, "OK", 2) != 0) _batch_reboot = false;
    _batch_last_cmd = millis();
    Serial.printf("WC: cmd %d/%d '%s' took %lums\n", (int)_batch_next, (int)_batch_count,
                  e.key, (unsigned long)(_batch_last_cmd - t0));
    if (_batch_next < _batch_count) return;  // more commands next tick
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
      _batch_reboot = false;
    }
  }
  {
    WCLock prefs_lock(_mux);
    _cb->onConfigBatchEnd();
  }
  WCLock lock(_mux);
  _batch_state = BATCH_DONE;
  if (_batch_reboot) {
    // Fallback only: the real 3 s reboot timer is armed when the client reads
    // /api/config/result (handleConfigResult), so the browser gets its
    // confirmation before the AP/WiFi drops. This covers a client that
    // disconnected and never polls - generous enough for a phone that got
    // bounced off the AP mid-save to rejoin and fetch its confirmation.
    _reboot_at = now + 30000;
    if (_reboot_at == 0) _reboot_at = 1;
  }
}

// ---------------------------------------------------------------------------
// Routes / auth
// ---------------------------------------------------------------------------

// Diagnostic trace of every request that reaches the server (async_tcp task).
// Distinguishes "client stopped sending" from "server stopped accepting" when
// a save's confirmation polls go missing on hardware.
static void wcLogReq(AsyncWebServerRequest* r) {
  Serial.printf("WC: http %s %s\n", r->methodToString(), r->url().c_str());
}

void WebConfigServer::registerRoutes() {
  _server->on("/", HTTP_GET, [this](AsyncWebServerRequest* r) { wcLogReq(r); handleRoot(r); });
  _server->on("/api/status", HTTP_GET, [this](AsyncWebServerRequest* r) { wcLogReq(r); handleStatus(r); });
  _server->on("/api/presets", HTTP_GET, [this](AsyncWebServerRequest* r) { wcLogReq(r); handlePresets(r); });
  _server->on("/api/login", HTTP_POST, [this](AsyncWebServerRequest* r) { wcLogReq(r); handleLogin(r); },
              NULL, collectBody);
  _server->on("/api/logout", HTTP_POST, [this](AsyncWebServerRequest* r) { wcLogReq(r); handleLogout(r); });
  // NB: plain-string routes match sub-paths too ("/api/config" matches
  // "/api/config/result") and handlers run in registration order, so the more
  // specific route MUST be registered first or it never fires. This was why
  // save confirmations were lost: result polls were answered with config JSON.
  _server->on("/api/config/result", HTTP_GET, [this](AsyncWebServerRequest* r) { wcLogReq(r); handleConfigResult(r); });
  _server->on("/api/config", HTTP_GET, [this](AsyncWebServerRequest* r) { wcLogReq(r); handleConfigGet(r); });
  _server->on("/api/config", HTTP_POST, [this](AsyncWebServerRequest* r) { wcLogReq(r); handleConfigPost(r); },
              NULL, collectBody);
  _server->on("/api/stats", HTTP_GET, [this](AsyncWebServerRequest* r) { wcLogReq(r); handleStats(r); });
  _server->on("/api/scan", HTTP_GET, [this](AsyncWebServerRequest* r) { wcLogReq(r); handleScan(r); });
  _server->on("/api/reboot", HTTP_POST, [this](AsyncWebServerRequest* r) { wcLogReq(r); handleReboot(r); });
  _server->on("/api/portal/exit", HTTP_POST, [this](AsyncWebServerRequest* r) { wcLogReq(r); handlePortalExit(r); });
  _server->onNotFound([this](AsyncWebServerRequest* r) { wcLogReq(r); handleNotFound(r); });
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
  if (_mode == MODE_SETUP) return true;  // physical proximity implied, nothing configured
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
      req->beginResponse(200, "text/html", WEBCONFIG_HTML_GZ, WEBCONFIG_HTML_GZ_LEN);
  res->addHeader("Content-Encoding", "gzip");
  res->addHeader("ETag", WEBCONFIG_HTML_ETAG);
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
  doc["name"] = (const char*)node.name;
  char node_id[17];
  for (int i = 0; i < 8; i++) sprintf(&node_id[i * 2], "%02x", _pub_key[i]);
  doc["node_id"] = node_id;
  doc["fw"] = _fw_ver;
  doc["role"] = _role;
  doc["board"] = _board_name;
  doc["uptime_s"] = millis() / 1000;
#ifdef WITH_MQTT_BRIDGE
  doc["runtime_slots"] = has_mqtt ? RUNTIME_MQTT_SLOTS : 0;
  doc["max_slots"] = has_mqtt ? MAX_MQTT_SLOTS : 0;
#else
  doc["runtime_slots"] = 0;
  doc["max_slots"] = 0;
#endif
  doc["mqtt"] = has_mqtt;
  doc["capabilities"] = node.capabilities;

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
    radio["repeat"] = (bool)node.repeat;
    radio["flood_max"] = node.flood_max;
    radio["flood_max_advert"] = node.flood_max_advert;
    radio["flood_max_unscoped"] = node.flood_max_unscoped;
    static const char* const LOOP_MODES[] = { "off", "minimal", "moderate", "strict" };
    radio["loop_detect"] = LOOP_MODES[node.loop_detect <= 3 ? node.loop_detect : 0];
    radio["name"] = (const char*)node.name;
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
  JsonObject set = doc["set"];

  WCLock lock(_mux);
  // A DONE batch stays readable until the next POST claims the slot, so a
  // client that lost the result response can re-poll instead of failing.
  if (_batch_state == BATCH_PENDING) {
    req->send(409, "application/json", "{\"error\":\"busy\"}");
    return;
  }

  int count = 0;
  for (JsonPair kv : set) {
    const char* key = kv.key().c_str();
    const char* val = kv.value().as<const char*>();
    if (!val || !isAllowedSetKey(key, _mqtt_prefs != NULL)) {
      char err[96];
      snprintf(err, sizeof(err), "{\"error\":\"bad key\",\"key\":\"%.32s\"}", key);
      req->send(400, "application/json", err);
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
    // Build "set <key> <value>", stripping CR/LF so a value can't smuggle in
    // a second command.
    int pos = snprintf(e.cmd, sizeof(e.cmd), "set %s ", key);
    for (const char* p = val; *p && pos < (int)sizeof(e.cmd) - 1; p++) {
      if (*p == '\r' || *p == '\n') continue;
      e.cmd[pos++] = *p;
    }
    e.cmd[pos] = 0;
    count++;
  }
  if (count == 0 && !reboot_after) {
    req->send(400, "application/json", "{\"error\":\"no changes\"}");
    return;
  }
  _batch_count = count;
  _batch_next = 0;
  _batch_reboot = reboot_after;
  _batch_reboot_armed = false;
  _standalone_wifi_dirty = false;
  _batch_state = BATCH_PENDING;  // tick() picks it up on the loop task
  uint32_t du = millis() + 60000;
  if (du == 0) du = 1;
  _diag_until = du;
  Serial.printf("WC: config POST accepted, %d cmds, reboot=%d\n", count, (int)reboot_after);

  char msg[64];
  snprintf(msg, sizeof(msg), "{\"state\":\"pending\",\"count\":%d}", count);
  req->send(202, "application/json", msg);
}

void WebConfigServer::handleConfigResult(AsyncWebServerRequest* req) {
  if (_mode == MODE_OFF) { Serial.println("WC: result read -> 503 (mode off)"); req->send(503); return; }
  if (!checkAuth(req)) { Serial.println("WC: result read -> 401"); req->send(401, "application/json", "{\"error\":\"auth\"}"); return; }

  // Entry print BEFORE the lock (racy state read is fine for diag): if this
  // fires but no branch print follows, the handler is blocked on _mux.
  Serial.printf("WC: result entry mode=%d state=%d\n", (int)_mode, (int)_batch_state);
  WCLock lock(_mux);
  if (_batch_state == BATCH_IDLE) {
    Serial.println("WC: result read -> idle");
    req->send(200, "application/json", "{\"state\":\"idle\"}");
    return;
  }
  if (_batch_state == BATCH_PENDING) {
    req->send(200, "application/json", "{\"state\":\"pending\"}");
    return;
  }
  Serial.printf("WC: result read -> done (reboot=%d armed=%d)\n",
                (int)_batch_reboot, (int)_batch_reboot_armed);
  DynamicJsonDocument doc(6144);
  doc["state"] = "done";
  doc["reboot"] = _batch_reboot;
  JsonArray results = doc.createNestedArray("results");
  for (int i = 0; i < _batch_count; i++) {
    JsonObject r = results.createNestedObject();
    r["key"] = (const char*)_batch[i].key;
    r["reply"] = (const char*)_batch[i].reply;
  }
  // State stays DONE (re-readable) until the next POST claims the slot.
  if (_batch_reboot && !_batch_reboot_armed) {
    // Confirmation delivered - reboot 3 s from now (replaces the 30 s
    // drain-time fallback) so the UI can show its countdown first. Armed
    // once; re-reads must not keep pushing the deadline out.
    _batch_reboot_armed = true;
    _reboot_at = millis() + 3000;
    if (_reboot_at == 0) _reboot_at = 1;
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
    WiFi.scanNetworks(true);
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
    } else if (mqttPresetNeedsSlotCredentials(&p)) {
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
