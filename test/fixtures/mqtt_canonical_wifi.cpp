#include <atomic>
#include <cassert>
#include <cstring>
#include <string>
#include "helpers/esp32/WiFiCredentials.h"
#include "helpers/esp32/WiFiCompatibility.h"
#include "helpers/MQTTPrefsStorage.h"

using mesh::wifi::CredentialState;
using mesh::wifi::Credentials;
namespace mesh { namespace wifi {
constexpr bool kPrimaryEspNowRadio = false;
std::string joined_ssid, joined_password;
int joins = 0;
void beginStation(const char* ssid, const char* password) {
  joined_ssid = ssid; joined_password = password; ++joins;
}
}}
@WEB_POLICY@
enum { WIFI_OFF, WIFI_STA, WIFI_PS_NONE, WIFI_PS_MAX_MODEM, WIFI_PS_MIN_MODEM };
struct { int getMode() { return WIFI_STA; } } WiFi;
using wifi_ps_type_t = int;
using wifi_mode_t = int;
using esp_err_t = int;
constexpr int ESP_OK = 0, ESP_ERR_WIFI_NOT_INIT = -1;
constexpr int WIFI_MODE_NULL = WIFI_OFF, WIFI_MODE_AP = 2;
int esp_wifi_get_mode(wifi_mode_t* mode) { *mode = WIFI_STA; return ESP_OK; }
int esp_wifi_set_ps(int) { return 0; }

size_t strlcpy(char* to, const char* from, size_t size) {
  const size_t len = strlen(from);
  if (size) { memcpy(to, from, len < size ? len + 1 : size - 1); to[size - 1] = 0; }
  return len;
}

struct WebConfigServer {
  inline static WebConfigServer* _active = nullptr;
  char _wifi_ssid[32] = {}, _wifi_password[65] = {};
  uint8_t _wifi_power_save = mesh::wifi::kDefaultPowerSave;
  void* _mqtt_prefs = nullptr;
  bool _standalone_wifi = true, _standalone_wifi_dirty = false, _batch_all_ok = true;
  struct Entry { const char* key; char reply[160] = {}; };
  Entry _batch[3] = {{"wifi.ssid"}, {"wifi.pwd"}, {"wifi.powersave"}};
  int _batch_count = 3;
  bool reloadStandaloneWiFi();
  bool acceptWiFi(Entry& e, const char* value) {
@CREDENTIAL_BATCH@
      return false;
    }
    return true;
  }
  bool commitWiFi() {
@COMMIT_BATCH@
    return _batch_all_ok;
  }
  static CredentialState resolveWiFi(Credentials&, const void* = nullptr);
  static bool hasConfiguredWiFi(const void* = nullptr);
  static bool loadStandaloneWiFi(char*, size_t, char*, size_t, uint8_t* = nullptr,
                                  const void* = nullptr);
  static bool saveStandaloneWiFi(const char*, const char*, uint8_t);
  static bool setStandaloneWiFiSSID(const char*, char*, size_t, const void* = nullptr);
  static bool setStandaloneWiFiPassword(const char*, char*, size_t, const void* = nullptr);
  static bool setStandaloneWiFiPowerSave(const char*, char*, size_t, const void* = nullptr);
  static bool formatWiFiPassword(char*, size_t, const void* = nullptr);
  static bool formatWiFiPowerSave(char*, size_t, const void* = nullptr);
};
@WEB_METHODS@

struct MQTTNodeInfo { bool canonical_wifi = false; };
constexpr int RUNTIME_MQTT_SLOTS = 2;
constexpr const char* MQTT_PRESET_NONE = "none";
constexpr const char* MQTT_PRESET_CUSTOM = "custom";
const void* findMQTTPreset(const char* name) {
  return strcmp(name, "test") == 0 ? reinterpret_cast<void*>(1) : nullptr;
}
#define MQTT_DEBUG_PRINTLN(...) {}
static bool isWiFiConfigValid(const MQTTPrefs*, bool);
struct MQTTBridge {
  MQTTPrefs* _obs;
  MQTTNodeInfo _node_info;
  bool _manage_wifi = true, _wifi_configured = false, _initialized = false;
  char _wifi_ssid[32] = {}, _wifi_password[65] = {};
  uint8_t _wifi_power_save = 0;
  std::atomic<bool> _stop_requested{false};
  explicit MQTTBridge(MQTTPrefs& obs, bool canonical = true, bool managed = true)
      : _obs(&obs), _node_info{canonical}, _manage_wifi(managed) {}
  bool prepareWiFiCredentials();
  void beginWiFiStation();
  bool isReady() const;
  static bool isConfigValid(const MQTTPrefs*, bool = false);
  void begin() {
@BEGIN_GATE@
    _initialized = true;
    if (_manage_wifi) beginWiFiStation();
  }
  void reconnect() {
@RECONNECT@
  }
};
@MQTT_METHODS@

struct CmdCallbacks {
  MQTTPrefs* legacy;
  bool canonical = true;
  bool usesCanonicalWiFi() const { return canonical; }
  bool setWiFiSSID(const char* value, char* reply) { return WebConfigServer::setStandaloneWiFiSSID(value, reply, 160, legacy); }
  bool setWiFiPassword(const char* value, char* reply) { return WebConfigServer::setStandaloneWiFiPassword(value, reply, 160, legacy); }
  bool setWiFiPowerSave(const char* value, char* reply) { return WebConfigServer::setStandaloneWiFiPowerSave(value, reply, 160, legacy); }
  bool getWiFiPassword(char* reply) { return WebConfigServer::formatWiFiPassword(reply, 160, legacy); }
  bool getWiFiSSID(char* reply) {
    Credentials credentials; WebConfigServer::resolveWiFi(credentials, legacy);
    snprintf(reply, 160, "> %s", credentials.ssid); return true;
  }
  bool getWiFiStatus(char* reply) { strcpy(reply, "status"); return true; }
  bool getWiFiPowerSave(char* reply) { strcpy(reply, "power save"); return true; }
};
struct CommonCLI {
  CmdCallbacks* _callbacks;
  bool handleObserverSetCmd(uint32_t, const char* config, char* reply) {
@CLI_SET@
    return false; // untouched observer fallback follows this production block
  }
  bool handleObserverGetCmd(uint32_t sender_timestamp, const char* config, char* reply) {
@CLI_GET@
    return false;
  }
};

void assert_selected(MQTTBridge& bridge, const char* ssid, const char* password) {
  assert(bridge.isReady());
  assert(std::string(bridge._wifi_ssid) == ssid);
  assert(std::string(bridge._wifi_password) == password);
  const int old_joins = mesh::wifi::joins;
  bridge.reconnect();
  assert(mesh::wifi::joins == old_joins + 1);
  assert(mesh::wifi::joined_ssid == ssid && mesh::wifi::joined_password == password);
  const int opens = nvs_opens;
  for (int i = 0; i < 100; ++i) assert(bridge.isReady());
  assert(nvs_opens == opens); // no NVS/String operations on the hot path
}

int main() {
  static_assert(sizeof(MQTTPrefs::wifi_password) == 64, "persistent codec unchanged");
  MQTTPrefs obs{};
  strcpy(obs.mqtt_slot_preset[0], "test");
  const MQTTPrefs untouched = obs;
  char reply[160] = {}, ssid[32] = {};
  reset_nvs();

  // Fresh Full portal-only provisioning; the frozen observer store is empty.
  assert(!MQTTBridge::isConfigValid(&obs, true));
  MQTTBridge empty(obs); empty.begin(); assert(!empty.isReady());
  WebConfigServer fresh; fresh._mqtt_prefs = &obs;
  assert(fresh.acceptWiFi(fresh._batch[0], "portal"));
  assert(fresh.acceptWiFi(fresh._batch[1], "password"));
  assert(fresh.acceptWiFi(fresh._batch[2], "max"));
  assert(fresh.commitWiFi());
  assert(memcmp(&obs, &untouched, sizeof(obs)) == 0);
  assert(WebConfigServer::hasConfiguredWiFi(&obs));
  assert(WebConfigServer::loadStandaloneWiFi(ssid, sizeof(ssid), nullptr, 0));
  assert(std::string(ssid) == "portal");
  assert(MQTTBridge::isConfigValid(&obs, true));
  assert(!MQTTBridge::isConfigValid(&obs, false));
  MQTTBridge portal(obs); portal.begin();
  assert(portal._wifi_power_save == 2); assert_selected(portal, "portal", "password");

  // Saved changes affect a new boot/restart, never a live worker snapshot.
  assert(WebConfigServer::saveStandaloneWiFi("changed", "new-password", 0));
  assert_selected(portal, "portal", "password");
  MQTTBridge reboot(obs); reboot.begin();
  assert(reboot._wifi_power_save == 0); assert_selected(reboot, "changed", "new-password");
  std::string raw(64, 'A');
  assert(WebConfigServer::saveStandaloneWiFi("raw", raw.c_str(), 1));
  MQTTBridge raw_bridge(obs); raw_bridge.begin(); assert_selected(raw_bridge, "raw", raw.c_str());
  assert(WebConfigServer::formatWiFiPassword(reply, sizeof(reply), &obs));
  assert(std::string(reply) == "> " + raw);
  assert(!WebConfigServer::saveStandaloneWiFi("bad", std::string(64, 'Z').c_str(), 1));
  assert(!WebConfigServer::saveStandaloneWiFi("bad", std::string(65, 'A').c_str(), 1));
  assert(!WebConfigServer::saveStandaloneWiFi(std::string(32, 'a').c_str(), "", 1));
  MQTTBridge unchanged(obs); unchanged.begin(); assert_selected(unchanged, "raw", raw.c_str());

  // Production command routing must reach the canonical callbacks before the
  // legacy observer handler, retain remote secret masking, and consume errors.
  CmdCallbacks callbacks{&obs}; CommonCLI cli{&callbacks};
  assert(cli.handleObserverSetCmd(0, "wifi.ssid routed", reply));
  const std::string raw_command = "wifi.pwd " + raw;
  assert(cli.handleObserverSetCmd(0, raw_command.c_str(), reply));
  assert(cli.handleObserverGetCmd(0, "wifi.pwd", reply));
  assert(std::string(reply) == "> " + raw);
  assert(cli.handleObserverGetCmd(1, "wifi.pwd", reply));
  assert(std::string(reply) == "> ******** (local connection only)");
  assert(cli.handleObserverSetCmd(0, "wifi.powersave min", reply));
  assert(cli.handleObserverSetCmd(0, "wifi.pwd invalid-64", reply));
  assert(memcmp(&obs, &untouched, sizeof(obs)) == 0);
  callbacks.canonical = false;
  assert(!cli.handleObserverSetCmd(0, "wifi.ssid legacy-route", reply));
  assert(!cli.handleObserverGetCmd(0, "wifi.pwd", reply));

  // Older observer-only installs fall back, and first CLI write promotes a pair.
  reset_nvs(); strcpy(obs.wifi_ssid, "legacy"); strcpy(obs.wifi_password, "legacy-password");
  obs.wifi_power_save = 2;
  MQTTBridge legacy(obs); legacy.begin(); assert_selected(legacy, "legacy", "legacy-password");
  const MQTTPrefs legacy_untouched = obs;
  assert(WebConfigServer::setStandaloneWiFiSSID("promoted", reply, sizeof(reply), &obs));
  assert(memcmp(&obs, &legacy_untouched, sizeof(obs)) == 0);
  MQTTBridge promoted(obs); promoted.begin(); assert_selected(promoted, "promoted", "legacy-password");
  reset_nvs();
  assert(WebConfigServer::setStandaloneWiFiPassword(raw.c_str(), reply, sizeof(reply), &obs));
  MQTTBridge password_first(obs); password_first.begin(); assert_selected(password_first, "legacy", raw.c_str());

  // Fresh password-first and SSID-first CLI setup, including open networks.
  reset_nvs(); obs.wifi_ssid[0] = obs.wifi_password[0] = 0;
  assert(WebConfigServer::setStandaloneWiFiPassword("first-password", reply, sizeof(reply), &obs));
  assert(!WebConfigServer::hasConfiguredWiFi(&obs));
  assert(WebConfigServer::setStandaloneWiFiSSID("first-ssid", reply, sizeof(reply), &obs));
  MQTTBridge first(obs); first.begin(); assert_selected(first, "first-ssid", "first-password");
  reset_nvs();
  assert(WebConfigServer::setStandaloneWiFiSSID("open", reply, sizeof(reply), &obs));
  MQTTBridge open(obs); open.begin(); assert_selected(open, "open", "");
  reset_nvs(); nvs_values["ssid"] = {PT_STR, "old-open", 0};
  MQTTBridge old_open(obs); old_open.begin(); assert_selected(old_open, "old-open", "");

  // Clearing MQTT CLI SSID preserves its historical recovery command while
  // making the canonical empty value authoritative over old observer settings.
  assert(WebConfigServer::setStandaloneWiFiSSID("", reply, sizeof(reply), &obs));
  assert(!WebConfigServer::hasConfiguredWiFi(&obs));
  assert(!WebConfigServer::setStandaloneWiFiSSID("", reply, sizeof(reply)));

  // Canonical empty, malformed, missing SSID, interrupted or inaccessible data
  // must never revive valid observer credentials.
  strcpy(obs.wifi_ssid, "legacy"); strcpy(obs.wifi_password, "legacy-password");
  for (int fault = 0; fault < 7; ++fault) {
    reset_nvs();
    nvs_values["ssid"] = {PT_STR, fault == 0 ? "" : "canonical", 0};
    nvs_values["password"] = {PT_STR, "pwd", 0};
    if (fault == 1) nvs_values["password"].string = std::string(64, 'Z');
    if (fault == 2) nvs_values["ssid"].string = std::string(32, 'a');
    if (fault == 3) nvs_values["ssid"].type = PT_U8;
    if (fault == 4) nvs_values.erase("ssid");
    if (fault == 5) nvs_values["pending"] = {PT_U8, {}, 1};
    if (fault == 6) nvs_available = false;
    assert(!MQTTBridge::isConfigValid(&obs, true));
    MQTTBridge broken(obs); broken.begin(); assert(!broken.isReady());
    assert(!WebConfigServer::hasConfiguredWiFi(&obs));
  }

  // Companion-managed and default legacy/Pico semantics ignore infrastructure NVS.
  reset_nvs(); assert(WebConfigServer::saveStandaloneWiFi("infra", raw.c_str(), 1));
  MQTTBridge companion(obs, true, false); companion.begin();
  assert(std::string(companion._wifi_ssid) == "legacy");
  assert(std::string(companion._wifi_password) == "legacy-password");
  MQTTBridge legacy_role(obs, false); legacy_role.begin();
  assert_selected(legacy_role, "legacy", "legacy-password");

  // Every individual failed NVS write rolls back a committed pair. Persistent
  // failures/interrupted saves stay closed; a failed repair cannot clear pending.
  for (int failed_write = 1; failed_write <= 5; ++failed_write) {
    reset_nvs(); assert(WebConfigServer::saveStandaloneWiFi("before", "before-pwd", 1));
    nvs_writes = 0; nvs_fail_write = failed_write;
    assert(!WebConfigServer::saveStandaloneWiFi("after", "after-pwd", 2));
    nvs_fail_write = -1;
    MQTTBridge rolled_back(obs); rolled_back.begin();
    assert_selected(rolled_back, "before", "before-pwd");
  }
  // A failed portal batch reloads the committed cache; it cannot later join
  // the edited-but-unsaved tuple after its AP remains open.
  reset_nvs(); assert(WebConfigServer::saveStandaloneWiFi("before", "before-pwd", 1));
  WebConfigServer failed_portal; failed_portal._mqtt_prefs = &obs;
  assert(failed_portal.reloadStandaloneWiFi());
  assert(failed_portal.acceptWiFi(failed_portal._batch[0], "unsaved"));
  assert(failed_portal.acceptWiFi(failed_portal._batch[1], "unsaved-password"));
  nvs_writes = 0; nvs_fail_write = 3;
  assert(!failed_portal.commitWiFi());
  assert(std::string(failed_portal._wifi_ssid) == "before");
  assert(std::string(failed_portal._wifi_password) == "before-pwd");
  nvs_fail_write = -1;
  // A final marker commit may change the value before returning failure.
  // Rollback must re-establish its barrier; if that fails, leave the complete
  // candidate unchanged instead of exposing a mixed credential pair.
  reset_nvs(); assert(WebConfigServer::saveStandaloneWiFi("before", "before-pwd", 1));
  nvs_writes = 0; nvs_fail_write = nvs_side_effect_write = 5;
  assert(!WebConfigServer::saveStandaloneWiFi("after", "after-pwd", 2));
  nvs_fail_write = nvs_side_effect_write = -1;
  MQTTBridge side_effect(obs); side_effect.begin(); assert_selected(side_effect, "before", "before-pwd");
  reset_nvs(); assert(WebConfigServer::saveStandaloneWiFi("before", "before-pwd", 1));
  nvs_writes = 0; nvs_fail_write = nvs_side_effect_write = 5; nvs_fail_write_again = 6;
  assert(!WebConfigServer::saveStandaloneWiFi("after", "after-pwd", 2));
  nvs_fail_write = nvs_side_effect_write = nvs_fail_write_again = -1;
  MQTTBridge no_barrier(obs); no_barrier.begin(); assert_selected(no_barrier, "after", "after-pwd");
  reset_nvs(); assert(WebConfigServer::saveStandaloneWiFi("before", "before-pwd", 1));
  nvs_writes = 0; nvs_fail_write = 3; nvs_fail_forever = true;
  assert(!WebConfigServer::saveStandaloneWiFi("after", "after-pwd", 2));
  nvs_fail_write = -1; nvs_fail_forever = false;
  MQTTBridge partial(obs); partial.begin(); assert(!partial.isReady());
  nvs_values["pending"] = {PT_U8, {}, 1};
  nvs_writes = 0; nvs_fail_write = 3;
  assert(!WebConfigServer::saveStandaloneWiFi("repair", "repair-pwd", 1));
  nvs_fail_write = -1;
  MQTTBridge failed_repair(obs); failed_repair.begin(); assert(!failed_repair.isReady());
  assert(WebConfigServer::saveStandaloneWiFi("repair", "repair-pwd", 1));
  MQTTBridge repair(obs); repair.begin(); assert_selected(repair, "repair", "repair-pwd");
  // No-canonical Companion reads retain compiled/live credentials, whereas
  // explicit invalid or empty canonical settings cannot revive those secrets.
  reset_nvs();
#ifdef WIFI_PWD
  assert(WebConfigServer::formatWiFiPassword(reply, sizeof(reply)));
  assert(std::string(reply) == "> compiled-password");
#endif
  WebConfigServer live;
  strcpy(live._wifi_password, "live-password"); WebConfigServer::_active = &live;
  assert(WebConfigServer::formatWiFiPassword(reply, sizeof(reply)));
  assert(std::string(reply) == "> live-password");
  nvs_values["ssid"] = {PT_STR, "", 0};
  nvs_values["password"] = {PT_STR, "", 0};
  assert(WebConfigServer::formatWiFiPassword(reply, sizeof(reply)));
  assert(std::string(reply) == "> ");
  nvs_values["pending"] = {PT_U8, {}, 1};
  assert(WebConfigServer::formatWiFiPassword(reply, sizeof(reply)));
  assert(std::string(reply) == "> ");
  WebConfigServer::_active = nullptr;

}
