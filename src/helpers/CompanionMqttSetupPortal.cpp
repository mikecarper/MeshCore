#include "CompanionMqttSetupPortal.h"
#include "CompanionMqttPrefsNvs.h"

#if defined(ESP32_PLATFORM) && defined(WIFI_SSID) && defined(WITH_MQTT_BRIDGE)

#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <helpers/MQTTDefaults.h>
#include <helpers/MQTTPresets.h>
#include <strings.h>

namespace {

static const char* NVS_NAMESPACE = "mesh-mqtt";
static const char* NVS_VERSION_KEY = "version";
static const char* NVS_PREFS_KEY = "prefs";
static const size_t MAX_FORM_BODY = 2048;

struct PortalImpl {
  WiFiServer server{80};
  MQTTPrefs* prefs = nullptr;
  MQTTPrefs page_prefs{};
  TaskHandle_t task = nullptr;
  volatile bool* active = nullptr;
  volatile bool saved = false;
  portMUX_TYPE prefs_lock = portMUX_INITIALIZER_UNLOCKED;
};

template <size_t N>
static bool hasTerminator(const char (&value)[N]) {
  return memchr(value, 0, N) != nullptr;
}

static bool storedStringsAreValid(const MQTTPrefs& prefs) {
  if (!hasTerminator(prefs.mqtt_origin)
      || !hasTerminator(prefs.mqtt_iata)
      || !hasTerminator(prefs.wifi_ssid)
      || !hasTerminator(prefs.wifi_password)
      || !hasTerminator(prefs.timezone_string)
      || !hasTerminator(prefs.mqtt_owner_public_key)
      || !hasTerminator(prefs.mqtt_email)
      || !hasTerminator(prefs.mqtt_ntp_server)
      || !hasTerminator(prefs.snmp_community)
      || !hasTerminator(prefs.alert_psk_hex)
      || !hasTerminator(prefs.alert_hashtag)
      || !hasTerminator(prefs.alert_region)) {
    return false;
  }
  for (int i = 0; i < MAX_MQTT_SLOTS; i++) {
    if (!hasTerminator(prefs.mqtt_slot_preset[i])
        || !hasTerminator(prefs.mqtt_slot_host[i])
        || !hasTerminator(prefs.mqtt_slot_username[i])
        || !hasTerminator(prefs.mqtt_slot_password[i])
        || !hasTerminator(prefs.mqtt_slot_token[i])
        || !hasTerminator(prefs.mqtt_slot_topic[i])
        || !hasTerminator(prefs.mqtt_slot_audience[i])) {
      return false;
    }
  }
  return true;
}

static bool readLine(WiFiClient& client, char* line, size_t capacity) {
  size_t length = 0;
  const uint32_t deadline = millis() + 5000;
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
      const int hi = hexValue(encoded[i + 1]);
      const int lo = hexValue(encoded[i + 2]);
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
  const size_t name_len = strlen(name);
  const char* field = body;
  while (field && *field) {
    const char* end = strchr(field, '&');
    const size_t field_len = end ? static_cast<size_t>(end - field) : strlen(field);
    if (field_len >= name_len + 1 && field[name_len] == '='
        && strncmp(field, name, name_len) == 0) {
      return decodeFormValue(field + name_len + 1, field_len - name_len - 1,
                             output, output_size);
    }
    field = end ? end + 1 : nullptr;
  }
  if (output && output_size) output[0] = 0;
  return false;
}

static bool hasFormField(const char* body, const char* name) {
  char value[8];
  return getFormField(body, name, value, sizeof(value));
}

static void sendHeader(WiFiClient& client, int code, const char* reason,
                       const char* content_type = "text/html") {
  client.printf("HTTP/1.1 %d %s\r\nContent-Type: %s\r\n"
                "Connection: close\r\nCache-Control: no-store\r\n\r\n",
                code, reason, content_type);
}

static void printHtmlEscaped(WiFiClient& client, const char* value) {
  if (!value) return;
  while (*value) {
    switch (*value++) {
      case '&': client.print(F("&amp;")); break;
      case '<': client.print(F("&lt;")); break;
      case '>': client.print(F("&gt;")); break;
      case '\"': client.print(F("&quot;")); break;
      case '\'': client.print(F("&#39;")); break;
      default: client.write(static_cast<uint8_t>(value[-1])); break;
    }
  }
}

static void printValueAttribute(WiFiClient& client, const char* value) {
  client.print(F(" value=\""));
  printHtmlEscaped(client, value);
  client.print('\"');
}

static void snapshotPagePrefs(PortalImpl* impl, MQTTPrefs& snapshot) {
  portENTER_CRITICAL(&impl->prefs_lock);
  snapshot = impl->page_prefs;
  portEXIT_CRITICAL(&impl->prefs_lock);
}

static uint16_t presetPort(const MQTTPresetDef& preset) {
  const char* scheme = strstr(preset.server_url, "://");
  if (!scheme) return 0;
  const char* host = scheme + 3;
  const char* colon = strchr(host, ':');
  if (colon) {
    char* end = nullptr;
    const unsigned long port = strtoul(colon + 1, &end, 10);
    if (end != colon + 1 && port <= 65535) return static_cast<uint16_t>(port);
  }
  if (strncmp(preset.server_url, "mqtt://", 7) == 0) return 1883;
  if (strncmp(preset.server_url, "mqtts://", 8) == 0) return 8883;
  if (strncmp(preset.server_url, "ws://", 5) == 0) return 80;
  return 443;
}

static void sendConfigPage(WiFiClient& client, const MQTTPrefs& prefs) {
  const char* selected_preset = prefs.mqtt_slot_preset[0][0]
      ? prefs.mqtt_slot_preset[0] : MQTT_PRESET_CUSTOM;
  sendHeader(client, 200, "OK");
  client.print(F(R"HTML(<!doctype html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MeshCore MQTT setup</title><style>
body{font-family:sans-serif;max-width:38rem;margin:2rem auto;padding:0 1rem;color:#222}
label{display:block;margin-top:1rem}input,select,button{box-sizing:border-box;width:100%;font-size:1rem;padding:.65rem;margin-top:.3rem}
.checks label{display:inline-block;width:auto;margin-right:1rem}.checks input{width:auto}fieldset{margin-top:1.2rem;border:1px solid #bbb}
small{color:#555}button{margin-top:1.3rem}#iata.iata-missing{border:2px solid #c00}#iata.iata-ready{border:2px solid #111}</style></head><body>
<h2>MeshCore companion MQTT setup</h2>
<p>Configure or update the MQTT destination for this companion.</p>
<form method="post" action="/save">
<label>Preset<select id="preset" name="preset">
)HTML"));
  client.print(F("<option value=\"custom\""));
  if (strcmp(selected_preset, MQTT_PRESET_CUSTOM) == 0) client.print(F(" selected"));
  client.print(F(">Custom broker</option>\n"));
  for (int i = 0; i < MQTT_PRESET_COUNT; i++) {
    const MQTTPresetDef& preset = MQTT_PRESETS[i];
    client.print(F("<option value=\""));
    printHtmlEscaped(client, preset.name);
    client.print(F("\" data-url=\""));
    printHtmlEscaped(client, preset.server_url);
    client.printf("\" data-port=\"%u\" data-user=\"", presetPort(preset));
    printHtmlEscaped(client, preset.userpass_username ? preset.userpass_username : "");
    client.print(F("\" data-audience=\""));
    printHtmlEscaped(client, preset.jwt_audience ? preset.jwt_audience : "");
    client.print('\"');
    if (strcmp(selected_preset, preset.name) == 0) client.print(F(" selected"));
    client.print('>');
    printHtmlEscaped(client, preset.name);
    client.print(F("</option>\n"));
  }
  client.print(F(R"HTML(</select></label>
<small>Selecting a preset fills its endpoint details. Built-in presets use their compiled endpoint and authentication settings.</small>
<fieldset><legend>Identity and routing</legend>
)HTML"));
  client.print(F("<label>Origin name (optional)<input name=\"origin\" maxlength=\"31\" autocomplete=\"off\""));
  printValueAttribute(client, prefs.mqtt_origin);
  client.print(F("></label>\n<label>IATA code<input id=\"iata\" name=\"iata\" maxlength=\"3\" pattern=\"[A-Za-z]{3}\" placeholder=\"SEA\""));
  printValueAttribute(client, prefs.mqtt_iata);
  client.print(F(R"HTML(></label>
<small>A three-letter IATA code is required unless a custom topic template or MeshRank token supplies the route.</small>
)HTML"));
  client.print(F("<label>Owner public key (optional)<input name=\"owner\" maxlength=\"64\" autocomplete=\"off\""));
  printValueAttribute(client, prefs.mqtt_owner_public_key);
  client.print(F("></label>\n<label>Email (optional)<input name=\"email\" type=\"email\" maxlength=\"63\" autocomplete=\"off\""));
  printValueAttribute(client, prefs.mqtt_email);
  client.print(F(R"HTML(></label>
</fieldset>
<fieldset><legend>Custom broker or preset credentials</legend>
)HTML"));
  client.print(F("<label>Broker host or URI<input id=\"host\" name=\"host\" maxlength=\"63\" placeholder=\"mqtt://broker.example:1883\""));
  printValueAttribute(client, prefs.mqtt_slot_host[0]);
  client.print(F("></label>\n<label>Port<input id=\"port\" name=\"port\" type=\"number\" min=\"0\" max=\"65535\""));
  client.printf(" value=\"%u\"></label>\n", prefs.mqtt_slot_port[0]);
  client.print(F("<label>Username<input id=\"username\" name=\"username\" maxlength=\"31\" autocomplete=\"username\""));
  printValueAttribute(client, prefs.mqtt_slot_username[0]);
  client.print(F("></label>\n<label>Password<input id=\"password\" name=\"password\" type=\"password\" maxlength=\"63\" autocomplete=\"new-password\" placeholder=\"Leave blank to keep saved password\"></label>\n"));
  if (prefs.mqtt_slot_password[0][0]) {
    client.print(F("<label><input style=\"width:auto\" type=\"checkbox\" name=\"clear_password\" value=\"1\"> Clear saved password</label>\n"));
  }
  client.print(F("<label>Token (optional)<input id=\"token\" name=\"token\" maxlength=\"47\" autocomplete=\"off\""));
  printValueAttribute(client, prefs.mqtt_slot_token[0]);
  client.print(F("></label>\n<label>Topic template (optional)<input id=\"topic\" name=\"topic\" maxlength=\"95\" placeholder=\"meshcore/{device_id}/packets\""));
  printValueAttribute(client, prefs.mqtt_slot_topic[0]);
  client.print(F("></label>\n<label>JWT audience (custom broker only, optional)<input id=\"audience\" name=\"audience\" maxlength=\"63\""));
  printValueAttribute(client, prefs.mqtt_slot_audience[0]);
  client.print(F(R"HTML(></label>
</fieldset>
<fieldset class="checks"><legend>Publish</legend>
)HTML"));
  client.printf("<label><input type=\"checkbox\" name=\"status\" value=\"1\"%s> Status</label>\n",
                prefs.mqtt_status_enabled ? " checked" : "");
  client.printf("<label><input type=\"checkbox\" name=\"packets\" value=\"1\"%s> Packets</label>\n",
                prefs.mqtt_packets_enabled ? " checked" : "");
  client.printf("<label><input type=\"checkbox\" name=\"raw\" value=\"1\"%s> Raw</label>\n",
                prefs.mqtt_raw_enabled ? " checked" : "");
  client.printf("<label><input type=\"checkbox\" name=\"rx\" value=\"1\"%s> RX</label>\n",
                prefs.mqtt_rx_enabled ? " checked" : "");
  client.print(F("<label>TX<select name=\"tx\"><option value=\"2\""));
  if (prefs.mqtt_tx_enabled == 2) client.print(F(" selected"));
  client.print(F(">Self adverts</option><option value=\"1\""));
  if (prefs.mqtt_tx_enabled == 1) client.print(F(" selected"));
  client.print(F(">All TX</option><option value=\"0\""));
  if (prefs.mqtt_tx_enabled == 0) client.print(F(" selected"));
  client.print(F(R"HTML(>Off</option></select></label>
</fieldset>
<button type="submit">Save and apply MQTT settings</button></form>
<p><small>This page remains available on the joined WiFi network at this node's IP address.</small></p>
<script>
const preset=document.getElementById('preset');
const iata=document.getElementById('iata');
function updateIataBox(){
 const ready=/^[A-Za-z]{3}$/.test(iata.value.trim());
 iata.classList.toggle('iata-ready',ready);
 iata.classList.toggle('iata-missing',!ready);
}
function fillPreset(){
 const o=preset.options[preset.selectedIndex], custom=o.value==='custom';
 document.getElementById('host').value=custom?'':(o.dataset.url||'');
 document.getElementById('port').value=custom?'1883':(o.dataset.port||'0');
 document.getElementById('username').value=custom?'':(o.dataset.user||'');
 document.getElementById('password').value='';
 document.getElementById('audience').value=custom?'':(o.dataset.audience||'');
 document.getElementById('topic').value='';
 if(o.value!=='meshrank')document.getElementById('token').value='';
}
preset.addEventListener('change',fillPreset);
iata.addEventListener('input',updateIataBox);
updateIataBox();
if(preset.value!=='custom'&&!document.getElementById('host').value)fillPreset();
</script>
</body></html>)HTML"));
}

static void sendMessage(WiFiClient& client, int code, const char* reason,
                        const char* heading, const char* message) {
  sendHeader(client, code, reason);
  client.printf("<!doctype html><meta name=viewport content='width=device-width'>"
                "<h2>%s</h2><p>%s</p>", heading, message);
  client.print("<p><a href='/'>Return to MQTT settings</a></p>");
}

static bool isThreeLetterCode(const char* value) {
  if (!value || strlen(value) != 3) return false;
  for (int i = 0; i < 3; i++) {
    if (!isalpha(static_cast<unsigned char>(value[i]))) return false;
  }
  return strcasecmp(value, "XXX") != 0;
}

static void copyString(char* dest, size_t dest_size, const char* src) {
  strncpy(dest, src ? src : "", dest_size - 1);
  dest[dest_size - 1] = 0;
}

static bool populatePrefs(const char* body, MQTTPrefs& prefs,
                          char* error, size_t error_size) {
  char previous_preset[sizeof(prefs.mqtt_slot_preset[0])];
  char previous_password[sizeof(prefs.mqtt_slot_password[0])];
  char preset[24];
  char origin[32];
  char iata[8];
  char owner[65];
  char email[64];
  char host[64];
  char port_text[8];
  char username[32];
  char password[64];
  char token[48];
  char topic[96];
  char audience[64];
  char tx_text[4];

  copyString(previous_preset, sizeof(previous_preset), prefs.mqtt_slot_preset[0]);
  copyString(previous_password, sizeof(previous_password), prefs.mqtt_slot_password[0]);

  if (!getFormField(body, "preset", preset, sizeof(preset))) {
    snprintf(error, error_size, "Select an MQTT preset or custom broker.");
    return false;
  }
  const MQTTPresetDef* preset_def = findMQTTPreset(preset);
  const bool custom = strcmp(preset, MQTT_PRESET_CUSTOM) == 0;
  if (!custom && !preset_def) {
    snprintf(error, error_size, "The selected MQTT preset is not valid.");
    return false;
  }

  getFormField(body, "origin", origin, sizeof(origin));
  getFormField(body, "iata", iata, sizeof(iata));
  getFormField(body, "owner", owner, sizeof(owner));
  getFormField(body, "email", email, sizeof(email));
  getFormField(body, "host", host, sizeof(host));
  getFormField(body, "port", port_text, sizeof(port_text));
  getFormField(body, "username", username, sizeof(username));
  getFormField(body, "password", password, sizeof(password));
  getFormField(body, "token", token, sizeof(token));
  getFormField(body, "topic", topic, sizeof(topic));
  getFormField(body, "audience", audience, sizeof(audience));
  getFormField(body, "tx", tx_text, sizeof(tx_text));
  if (!password[0] && !hasFormField(body, "clear_password")
      && strcmp(preset, previous_preset) == 0) {
    copyString(password, sizeof(password), previous_password);
  }

  char* port_end = nullptr;
  const unsigned long parsed_port = strtoul(port_text, &port_end, 10);
  if (!port_text[0] || !port_end || *port_end || parsed_port > 65535) {
    snprintf(error, error_size, "Enter a valid MQTT port from 0 through 65535.");
    return false;
  }
  if (custom && !host[0]) {
    snprintf(error, error_size, "A broker host or URI is required for a custom broker.");
    return false;
  }
  if (custom && parsed_port == 0 && strstr(host, "://") == nullptr) {
    snprintf(error, error_size, "A custom host without a URI scheme needs a nonzero port.");
    return false;
  }
  if (strcmp(preset, "meshrank") == 0 && !token[0]) {
    snprintf(error, error_size, "The MeshRank preset requires an account token.");
    return false;
  }
  if (preset_def && mqttPresetNeedsSlotCredentials(preset_def)
      && (!username[0] || !password[0])) {
    snprintf(error, error_size, "That preset requires a username and password.");
    return false;
  }
  const bool preset_needs_iata = preset_def
      && preset_def->topic_style == MQTT_TOPIC_MESHCORE;
  const bool custom_needs_iata = custom && !topic[0];
  if (preset_needs_iata && !isThreeLetterCode(iata)) {
    snprintf(error, error_size, "That preset requires a valid three-letter IATA code.");
    return false;
  }
  if (custom_needs_iata && !isThreeLetterCode(iata)) {
    snprintf(error, error_size, "Enter a three-letter IATA code, or provide a custom topic template.");
    return false;
  }

  const uint8_t tx_mode = (strcmp(tx_text, "0") == 0) ? 0
      : (strcmp(tx_text, "1") == 0) ? 1 : 2;

  char wifi_ssid[sizeof(prefs.wifi_ssid)];
  char wifi_password[sizeof(prefs.wifi_password)];
  copyString(wifi_ssid, sizeof(wifi_ssid), prefs.wifi_ssid);
  copyString(wifi_password, sizeof(wifi_password), prefs.wifi_password);

  applyMQTTDefaults(&prefs);
  copyString(prefs.wifi_ssid, sizeof(prefs.wifi_ssid), wifi_ssid);
  copyString(prefs.wifi_password, sizeof(prefs.wifi_password), wifi_password);
  for (int i = 0; i < MAX_MQTT_SLOTS; i++) {
    copyString(prefs.mqtt_slot_preset[i], sizeof(prefs.mqtt_slot_preset[i]), MQTT_PRESET_NONE);
  }

  copyString(prefs.mqtt_origin, sizeof(prefs.mqtt_origin), origin);
  copyString(prefs.mqtt_iata, sizeof(prefs.mqtt_iata), iata);
  for (size_t i = 0; prefs.mqtt_iata[i]; i++) {
    prefs.mqtt_iata[i] = static_cast<char>(toupper(static_cast<unsigned char>(prefs.mqtt_iata[i])));
  }
  copyString(prefs.mqtt_owner_public_key, sizeof(prefs.mqtt_owner_public_key), owner);
  copyString(prefs.mqtt_email, sizeof(prefs.mqtt_email), email);
  copyString(prefs.mqtt_slot_preset[0], sizeof(prefs.mqtt_slot_preset[0]), preset);
  copyString(prefs.mqtt_slot_host[0], sizeof(prefs.mqtt_slot_host[0]), host);
  prefs.mqtt_slot_port[0] = static_cast<uint16_t>(parsed_port);
  copyString(prefs.mqtt_slot_username[0], sizeof(prefs.mqtt_slot_username[0]), username);
  copyString(prefs.mqtt_slot_password[0], sizeof(prefs.mqtt_slot_password[0]), password);
  copyString(prefs.mqtt_slot_token[0], sizeof(prefs.mqtt_slot_token[0]), token);
  copyString(prefs.mqtt_slot_topic[0], sizeof(prefs.mqtt_slot_topic[0]), topic);
  copyString(prefs.mqtt_slot_audience[0], sizeof(prefs.mqtt_slot_audience[0]), audience);
  prefs.mqtt_status_enabled = hasFormField(body, "status") ? 1 : 0;
  prefs.mqtt_packets_enabled = hasFormField(body, "packets") ? 1 : 0;
  prefs.mqtt_raw_enabled = hasFormField(body, "raw") ? 1 : 0;
  prefs.mqtt_rx_enabled = hasFormField(body, "rx") ? 1 : 0;
  prefs.mqtt_tx_enabled = tx_mode;
  return true;
}

static bool handleClient(PortalImpl* impl, WiFiClient& client) {
  char line[256];
  if (!readLine(client, line, sizeof(line))) return false;
  const bool post_save = strncmp(line, "POST /save ", 11) == 0;
  const bool get_request = strncmp(line, "GET ", 4) == 0;
  size_t content_length = 0;
  do {
    if (!readLine(client, line, sizeof(line))) return false;
    if (strncasecmp(line, "Content-Length:", 15) == 0) {
      content_length = static_cast<size_t>(strtoul(line + 15, nullptr, 10));
    }
  } while (line[0]);

  if (get_request) {
    sendConfigPage(client, impl->page_prefs);
    return false;
  }
  if (!post_save || content_length == 0 || content_length > MAX_FORM_BODY) {
    sendMessage(client, 400, "Bad Request", "Invalid request", "The MQTT form data was missing or too large.");
    return false;
  }

  char* body = static_cast<char*>(malloc(content_length + 1));
  if (!body) {
    sendMessage(client, 503, "Service Unavailable", "Out of memory", "Reboot the companion and try again.");
    return false;
  }
  const size_t received = client.readBytes(body, content_length);
  body[received] = 0;
  if (received != content_length) {
    free(body);
    sendMessage(client, 408, "Request Timeout", "Incomplete request", "The complete MQTT form was not received.");
    return false;
  }

  MQTTPrefs candidate;
  snapshotPagePrefs(impl, candidate);
  char error[180];
  const bool valid = populatePrefs(body, candidate, error, sizeof(error));
  free(body);
  if (!valid) {
    sendMessage(client, 400, "Bad Request", "MQTT setup needs attention", error);
    return false;
  }
  if (!CompanionMqttSetupPortal::saveStoredConfig(candidate)) {
    sendMessage(client, 500, "Internal Server Error", "Could not save MQTT setup",
                "The configuration was valid but could not be written to flash.");
    return false;
  }

  portENTER_CRITICAL(&impl->prefs_lock);
  impl->page_prefs = candidate;
  impl->saved = true;
  portEXIT_CRITICAL(&impl->prefs_lock);

  sendMessage(client, 200, "OK", "MQTT setup saved",
              "The MQTT client is applying the new settings. This page remains available for later changes.");
  return true;
}

static void portalTask(void* arg) {
  PortalImpl* impl = static_cast<PortalImpl*>(arg);
  while (impl->active && *impl->active
         && WiFi.status() == WL_CONNECTED && WiFi.getMode() == WIFI_STA) {
    WiFiClient client = impl->server.available();
    if (client) {
      client.setTimeout(5000);
      handleClient(impl, client);
      client.stop();
    }
    delay(5);
  }

  impl->server.stop();
  impl->task = nullptr;
  if (impl->active) *impl->active = false;
  vTaskDelete(nullptr);
}

} // namespace

CompanionMqttSetupPortal::CompanionMqttSetupPortal()
    : _active(false), _impl(nullptr) {}

bool CompanionMqttSetupPortal::begin(MQTTPrefs* prefs) {
  if (_active) return true;
  if (!prefs || WiFi.status() != WL_CONNECTED || WiFi.getMode() != WIFI_STA) return false;
  PortalImpl* impl = static_cast<PortalImpl*>(_impl);
  if (!impl) {
    impl = new PortalImpl();
    if (!impl) return false;
    _impl = impl;
  }
  if (impl->task != nullptr) return false;
  impl->prefs = prefs;
  impl->active = &_active;
  impl->page_prefs = *prefs;
  impl->saved = false;
  impl->server.begin();
  impl->server.setNoDelay(true);
  _active = true;
  if (xTaskCreatePinnedToCore(portalTask, "mqtt-setup", 8192, impl, 1,
                              &impl->task, 0) != pdPASS) {
    _active = false;
    impl->server.stop();
    impl->task = nullptr;
    return false;
  }
  Serial.printf("MQTT setup: reconnect to the joined WiFi and open http://%s/\n",
                WiFi.localIP().toString().c_str());
  return true;
}

void CompanionMqttSetupPortal::stop() {
  _active = false;
  PortalImpl* impl = static_cast<PortalImpl*>(_impl);
  if (impl) impl->server.stop();
}

bool CompanionMqttSetupPortal::loop() {
  PortalImpl* impl = static_cast<PortalImpl*>(_impl);
  if (!impl) return false;
  bool saved = false;
  portENTER_CRITICAL(&impl->prefs_lock);
  if (impl->saved && impl->prefs) {
    *impl->prefs = impl->page_prefs;
    impl->saved = false;
    saved = true;
  }
  portEXIT_CRITICAL(&impl->prefs_lock);
  return saved;
}

bool CompanionMqttSetupPortal::loadStoredConfig(MQTTPrefs& prefs) {
  Preferences nvs;
  if (!nvs.begin(NVS_NAMESPACE, true)) return false;
  const uint16_t version = nvs.getUShort(NVS_VERSION_KEY, 0);
  const size_t length = nvs.getBytesLength(NVS_PREFS_KEY);
  // Companion stores this struct directly rather than using the observer's
  // versioned file header. Accept the previously shipped pre-display size and
  // default its append-only display tail so adding observer UI preferences
  // cannot discard an existing Companion MQTT setup after an update.
  const bool shape_ok = CompanionMqttPrefsNvs::accepts(version, length);
  MQTTPrefs loaded;
  applyMQTTDefaults(&loaded);
  const size_t read = shape_ok ? nvs.getBytes(NVS_PREFS_KEY, &loaded, length) : 0;
  nvs.end();
  if (!shape_ok || read != length) return false;

  if (!storedStringsAreValid(loaded)
      || loaded.mqtt_status_enabled > 1
      || loaded.mqtt_packets_enabled > 1
      || loaded.mqtt_raw_enabled > 1
      || loaded.mqtt_rx_enabled > 1
      || loaded.mqtt_tx_enabled > 2
      || loaded.wifi_power_save > 2
      || loaded.display_timeout_secs > DISPLAY_TIMEOUT_MAX_SECS
      || loaded.display_flip > 1) {
    return false;
  }

  const char* preset = loaded.mqtt_slot_preset[0];
  const bool custom = strcmp(preset, MQTT_PRESET_CUSTOM) == 0;
  const MQTTPresetDef* preset_def = custom ? nullptr : findMQTTPreset(preset);
  if (!preset[0] || (!custom && !preset_def)) return false;
  if (custom) {
    const char* host = loaded.mqtt_slot_host[0];
    if (!host[0]
        || (loaded.mqtt_slot_port[0] == 0 && strstr(host, "://") == nullptr)
        || (!loaded.mqtt_slot_topic[0][0] && !isThreeLetterCode(loaded.mqtt_iata))) {
      return false;
    }
  } else {
    if (preset_def->topic_style == MQTT_TOPIC_MESHCORE
        && !isThreeLetterCode(loaded.mqtt_iata)) return false;
    if (preset_def->topic_style == MQTT_TOPIC_MESHRANK
        && !loaded.mqtt_slot_token[0][0]) return false;
    if (mqttPresetNeedsSlotCredentials(preset_def)
        && (!loaded.mqtt_slot_username[0][0] || !loaded.mqtt_slot_password[0][0])) {
      return false;
    }
  }
  prefs = loaded;
  return true;
}

bool CompanionMqttSetupPortal::saveStoredConfig(const MQTTPrefs& prefs) {
  Preferences nvs;
  if (!nvs.begin(NVS_NAMESPACE, false)) return false;
  // Companion does not consume the appended observer display tail. Keeping
  // this record at the pre-display boundary makes a firmware rollback retain
  // the MQTT setup. loadStoredConfig() still accepts the briefly written 2880
  // byte runtime shape so development images remain recoverable.
  const size_t written = nvs.putBytes(
      NVS_PREFS_KEY, &prefs, CompanionMqttPrefsNvs::kWriteSize);
  const size_t version_written = nvs.putUShort(NVS_VERSION_KEY, MQTT_PREFS_VERSION);
  nvs.end();
  return written == CompanionMqttPrefsNvs::kWriteSize
      && version_written == sizeof(uint16_t);
}

#endif
