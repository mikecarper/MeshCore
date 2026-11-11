#pragma once

#include "CLIReplyText.h"

#include <atomic>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "EmergencyPowerConfig.h"

// ARM images can omit unsupported transports entirely. Use only global
// build flags, including ENABLE_WIFI_INTERFACE before its WIFI_SSID alias is
// defined. Wired bridge aliases never manufacture a 2.4 GHz capability.
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#if defined(WIFI_SSID) || defined(ENABLE_WIFI_INTERFACE) || defined(WITH_WEBCONFIG) \
    || defined(WITH_MQTT_BRIDGE) || defined(WITH_UDP_BRIDGE)
#define MESH_WIRELESS_BUILD_WIFI 1
#else
#define MESH_WIRELESS_BUILD_WIFI 0
#endif
#if defined(WITH_ESPNOW_BRIDGE) || (defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW)
#define MESH_WIRELESS_BUILD_ESPNOW 1
#else
#define MESH_WIRELESS_BUILD_ESPNOW 0
#endif
#if defined(BLE_PIN_CODE)
#define MESH_WIRELESS_BUILD_BLUETOOTH 1
#else
#define MESH_WIRELESS_BUILD_BLUETOOTH 0
#endif
#else
// Native tests and ESP32/RP2040 consumers retain the generic runtime backend.
#define MESH_WIRELESS_BUILD_WIFI 1
#define MESH_WIRELESS_BUILD_ESPNOW 1
#define MESH_WIRELESS_BUILD_BLUETOOTH 1
#endif

#if !MESH_WIRELESS_BUILD_WIFI && !MESH_WIRELESS_BUILD_ESPNOW && !MESH_WIRELESS_BUILD_BLUETOOTH
#define MESH_WIRELESS_NO_SERVICES 1
#else
#define MESH_WIRELESS_NO_SERVICES 0
#endif

namespace mesh {
namespace wireless {

enum Service : uint8_t { WiFi = 1, Bluetooth = 2, EspNow = 4, All = 7, Independent = 8 };
constexpr uint8_t BuildMask = (MESH_WIRELESS_BUILD_WIFI ? WiFi : 0)
    | (MESH_WIRELESS_BUILD_ESPNOW ? EspNow : 0)
    | (MESH_WIRELESS_BUILD_BLUETOOTH ? Bluetooth : 0);
enum class Result { Done, Pending, Failed };
enum class Action : uint8_t { None, Get, GetPreference, On, OnAll, Off, ForceOff, Invalid };
struct Command { uint8_t scope; Action action; };
static_assert(sizeof(Command) == 2, "CLI parse result must fit a register");

inline const char* skipSpace(const char* s) {
  while (*s == ' ' || *s == '\t') ++s;
  return s;
}
inline bool word(const char* s, const char* expected) {
  const size_t n = strlen(expected);
  return strncmp(s, expected, n) == 0 && *skipSpace(s + n) == 0;
}
inline Command parse(const char* s) {
  if (!s) return {0, Action::None};
  s = skipSpace(s);
  const bool get = strncmp(s, "get ", 4) == 0;
  if (!get && strncmp(s, "set ", 4) != 0) return {0, Action::None};
  s = skipSpace(s + 4);
  const char* key = strncmp(s, "espnow.enabled", 14) == 0 ? "espnow.enabled" : strncmp(s, "2.4ghz", 6) == 0 ? "2.4ghz" : strncmp(s, "espnow", 6) == 0 ? "espnow"
      : strncmp(s, "wifi.enabled", 12) == 0 ? "wifi.enabled" : "wifi";
  const size_t n = strlen(key);
  if (strncmp(s, key, n) || (s[n] && s[n] != ' ' && s[n] != '\t')) return {0, Action::None};
  const uint8_t scope = key[0] == '2' ? All : key[0] == 'e' ? EspNow : WiFi;
  s = skipSpace(s + n);
  if (get) return {scope, *s ? Action::Invalid : (n == 12 || n == 14) ? Action::GetPreference : Action::Get};
  if (word(s, "on") || (scope != All && word(s, "1"))) return {scope, Action::On};
  if (word(s, "off") || (scope != All && word(s, "0"))) return {scope, Action::Off};
  if (!strncmp(s, "off", 3) && (s[3] == ' ' || s[3] == '\t')
      && word(skipSpace(s + 3), "force")) return {scope, Action::ForceOff};
  if (scope != All && s[0] == '0' && (s[1] == ' ' || s[1] == '\t')
      && word(skipSpace(s + 1), "force")) return {scope, Action::ForceOff};
  if (scope == All && !strncmp(s, "on", 2) && (s[2] == ' ' || s[2] == '\t')) {
    s = skipSpace(s + 2);
    if (word(s, "all") || word(s, "force")) return {scope, Action::OnAll};
  }
  return {scope, Action::Invalid};
}

// Match Control::handle when the build has no wireless services, without
// retaining the state-transition machinery on flash-constrained devices.
inline bool handleNoServices(const char* text, char* reply, size_t size) {
  const auto command = parse(text);
  if (command.action == Action::None || !reply || !size) return false;
  if (command.action == Action::Invalid) {
    snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: use set wifi/espnow on|off [force]; set 2.4ghz on [all|force]|off [force]", "Error: set wifi/espnow on|off [force]; set 2.4ghz on [all]|off [force]"));
  } else if (command.action == Action::Get || command.action == Action::GetPreference) {
    if (command.scope == All)
      snprintf(reply, size, "wifi=unavailable, bluetooth=unavailable, espnow=unavailable");
    else
      snprintf(reply, size, "%s unavailable", command.scope == WiFi ? "wifi" : "espnow");
  } else {
    snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: requested wireless service unavailable in this build/boot", "Error: wireless unavailable"));
  }
  return true;
}

class Backend {
public:
  virtual uint8_t available() const = 0; // Services usable in this boot.
  virtual uint8_t enabled() const = 0;
  virtual uint8_t clients() const = 0;   // Open management sessions, not association/power.
  virtual bool replyBusy() const { return false; }
  virtual Result set(uint8_t service, bool on) = 0;
  virtual bool hasWiFiPreference() const { return false; }
  virtual bool wifiPreference() const { return true; }
  virtual bool saveWiFiPreference(bool) { return false; }
  virtual bool wifiRequiresReboot() const { return false; }
  virtual bool hasEspNowPreference() const { return false; }
  virtual bool espNowPreference() const { return true; }
  virtual bool saveEspNowPreference(bool) { return false; }
  virtual bool hasBluetoothPreference() const { return false; }
  virtual bool bluetoothPreference() const { return true; }
  virtual bool saveBluetoothPreference(bool) { return false; }
  virtual bool hasGroupPreference() const { return false; }
  virtual bool groupPreference(bool&, uint8_t&) const { return false; }
  virtual bool saveGroupPreference(bool, uint8_t) { return false; }
};

#if MESH_WIRELESS_NO_SERVICES
// Keep the shared API on LoRa/wired ARM builds without retaining an unused
// controller, rollback state, atomics, backend pointer or emergency snapshot.
class Control {
public:
  void begin(Backend&) {}
  void loadGroupPolicy(bool, uint8_t) {}
  void loadEspNowPolicy(bool) {}
  bool hasWiFiPreference() const { return false; }
  bool wifiPreference() const { return true; }
  bool blocked(uint8_t) const { return false; }
  bool backgroundBlocked(uint8_t) const { return false; }
  bool pending() const { return false; }
  bool pendingDisable(uint8_t) const { return false; }
  bool masterOff() const { return false; }
  bool allowService(uint8_t) { return false; }
  bool allowWiFiForOTA() { return false; }
  void finishWiFiForOTA() {}
  uint8_t enabled() const { return 0; }
#if MESH_ENABLE_EMERGENCY_POWER
  bool suspendForEmergency() { return true; }
  bool resumeAfterEmergency() { return true; }
#endif
  bool handle(const char* text, char* reply, size_t size, uint32_t, uint8_t) {
    return handleNoServices(text, reply, size);
  }
  void service(uint32_t) {}
};
#else
class Control {
  Backend* backend_ = nullptr;
  std::atomic<uint8_t> blocked_{0};
  uint8_t scope_ = 0, target_ = 0, saved_ = 0;
  bool pending_ = false, started_ = false, force_ = false, independent_requester_ = false;
  bool saved_valid_ = false, master_off_ = false;
#if MESH_WIRELESS_BUILD_WIFI
  // 0: no saved WiFi setting; 1: saved off; 2: saved on.
  std::atomic<uint8_t> wifi_policy_{0};
  std::atomic<bool> wifi_runtime_override_{false};
  // OTA may borrow a saved-off radio. Its task only requests cleanup; the
  // ordinary main-loop service owns teardown and reply-drain state.
  std::atomic<uint8_t> wifi_ota_override_{0}; // 0 none, 1 owned, 2 cleanup requested
  bool wifi_rollback_pending_ = false, wifi_previous_ = true;
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
  bool espnow_rollback_pending_ = false, espnow_previous_ = true;
  bool espnow_runtime_override_ = false;
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
  bool bluetooth_rollback_pending_ = false, bluetooth_previous_ = true;
#endif
  bool group_rollback_pending_ = false, group_previous_off_ = false;
  bool group_previous_valid_ = false, saved_change_ = false, group_request_ = false;
  uint8_t group_previous_saved_ = 0;
#if MESH_ENABLE_EMERGENCY_POWER
  bool emergency_suspended_ = false;
  uint8_t emergency_enabled_ = 0, emergency_blocked_ = 0;
#endif
  uint32_t due_ = 0;
  const char* error_ = nullptr;

  bool hasEspNowPreference() const {
#if MESH_WIRELESS_BUILD_ESPNOW
    return backend_ && backend_->hasEspNowPreference();
#else
    return false;
#endif
  }
  bool hasBluetoothPreference() const {
#if MESH_WIRELESS_BUILD_BLUETOOTH
    return backend_ && backend_->hasBluetoothPreference();
#else
    return false;
#endif
  }
  bool wifiRequiresReboot() const {
#if MESH_WIRELESS_BUILD_WIFI
    return backend_ && backend_->wifiRequiresReboot();
#else
    return false;
#endif
  }
  bool preferenceRollbackPending() const {
    return group_rollback_pending_
#if MESH_WIRELESS_BUILD_WIFI
        || wifi_rollback_pending_
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
        || espnow_rollback_pending_
#endif
        ;
  }
  void clearPreferenceRollback() {
#if MESH_WIRELESS_BUILD_WIFI
    wifi_rollback_pending_ = false;
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
    espnow_rollback_pending_ = false;
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
    bluetooth_rollback_pending_ = false;
#endif
    group_rollback_pending_ = false;
  }
  bool rollbackWiFiPreference() {
#if MESH_WIRELESS_BUILD_WIFI
    if (!wifi_rollback_pending_) return true;
    wifi_rollback_pending_ = false;
    if (!backend_->saveWiFiPreference(wifi_previous_)) return false;
    wifi_policy_.store(wifi_previous_ ? 2 : 1);
    return true;
#else
    return true;
#endif
  }

  bool rollbackPreferences() {
    bool ok = rollbackWiFiPreference();
#if MESH_WIRELESS_BUILD_ESPNOW
    if (espnow_rollback_pending_) {
      espnow_rollback_pending_ = false;
      ok = backend_->saveEspNowPreference(espnow_previous_) && ok;
    }
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
    if (bluetooth_rollback_pending_) {
      bluetooth_rollback_pending_ = false;
      ok = backend_->saveBluetoothPreference(bluetooth_previous_) && ok;
    }
#endif
    if (group_rollback_pending_) {
      group_rollback_pending_ = false;
      const bool restored = backend_->saveGroupPreference(group_previous_off_, group_previous_saved_);
      if (restored) {
        master_off_ = group_previous_off_;
        saved_valid_ = group_previous_valid_;
        saved_ = group_previous_saved_;
      }
      ok = restored && ok;
    }
    return ok;
  }

public:
  // Load the group gate immediately after mounting settings, before the
  // primary ESP-NOW driver or Bluetooth registration can start a radio.
  void loadGroupPolicy(bool off, uint8_t restore) {
    master_off_ = off;
    saved_valid_ = off;
    saved_ = restore & BuildMask;
    if (off) blocked_.store(blocked_.load() | BuildMask);
  }
  void loadEspNowPolicy(bool on) {
#if MESH_WIRELESS_BUILD_ESPNOW
    if (!on) blocked_.store(blocked_.load() | EspNow);
#else
    (void)on;
#endif
  }
  void begin(Backend& backend) {
    backend_ = &backend;
#if MESH_WIRELESS_BUILD_WIFI
    const bool saved = backend.hasWiFiPreference();
    const bool on = !saved || backend.wifiPreference();
    wifi_policy_.store(saved ? (on ? 2 : 1) : 0);
    if (!on) blocked_.store(blocked_.load() | WiFi);
#endif
    bool group_off = false;
    uint8_t restore = 0;
    if (backend.groupPreference(group_off, restore)) loadGroupPolicy(group_off, restore);
  }
  bool hasWiFiPreference() const {
#if MESH_WIRELESS_BUILD_WIFI
    return wifi_policy_.load() != 0;
#else
    return false;
#endif
  }
  bool wifiPreference() const {
#if MESH_WIRELESS_BUILD_WIFI
    return wifi_policy_.load() != 1;
#else
    return true;
#endif
  }
  bool blocked(uint8_t service) const {
    service &= BuildMask;
    return (blocked_.load() & service) != 0
#if MESH_WIRELESS_BUILD_ESPNOW
        || ((service & EspNow) && hasEspNowPreference()
            && !backend_->espNowPreference() && !espnow_runtime_override_)
#endif
#if MESH_WIRELESS_BUILD_WIFI
        || ((service & WiFi) && (master_off_ || !wifiPreference()) && wifi_ota_override_.load() == 2)
#endif
        ;
  }
  // OTA may own WiFi temporarily while saved policy keeps ordinary sockets
  // and bridges off. Only its uploader may borrow that radio permission.
  bool backgroundBlocked(uint8_t service) const {
    service &= BuildMask;
    if (!service) return false;
    if (master_off_ || blocked(service)) return true;
#if MESH_WIRELESS_BUILD_WIFI
    return (service & WiFi) && hasWiFiPreference() && !wifiPreference()
        && !(backend_ && wifiRequiresReboot() && wifi_runtime_override_
             && wifi_ota_override_.load() == 0);
#else
    return false;
#endif
  }
  bool pending() const { return pending_; }
  // New background owners must honor the reply-drain grace period before
  // the backend actually shuts down a requested service.
  bool pendingDisable(uint8_t service) const {
    return pending_ && (scope_ & service) && !(target_ & service);
  }
  bool masterOff() const { return master_off_; }
  bool allowService(uint8_t service) {
    if (!service || (service & ~BuildMask)) return false;
#if MESH_ENABLE_EMERGENCY_POWER
    if (emergency_suspended_) return false;
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
    if ((service & EspNow) && hasEspNowPreference()
        && !backend_->espNowPreference() && !espnow_runtime_override_) return false;
#endif
    if (master_off_ || pending_) return false;
#if MESH_WIRELESS_BUILD_WIFI
    if ((service & WiFi) && (master_off_ || !wifiPreference()) && wifi_ota_override_.load() == 2) return false;
    if ((service & WiFi) && !wifi_runtime_override_
        && (!wifiPreference() || (backend_ && wifiRequiresReboot() && blocked(WiFi)))) return false;
#endif
    blocked_.store(blocked_.load() & ~service);
    return true;
  }
  // An explicit OTA start owns its WiFi startup, including after a user has
  // disabled 2.4 GHz for this boot. Leave the other services disabled. Do not
  // race a pending shutdown, which could otherwise turn OTA off afterwards.
  bool allowWiFiForOTA() {
#if MESH_WIRELESS_BUILD_WIFI
#if MESH_ENABLE_EMERGENCY_POWER
    if (emergency_suspended_) return false;
#endif
    if (pending_) return false;
    if (master_off_ || (!wifiPreference() && (!wifi_runtime_override_ || wifi_ota_override_.load() == 2)))
      wifi_ota_override_.store(1);
    blocked_.store(blocked_.load() & ~WiFi);
    wifi_runtime_override_ = true;
    // OTA borrows WiFi alone; the persisted group latch and its ESP-NOW/BLE
    // snapshot remain authoritative for every other service.
    error_ = nullptr;
    return true;
#else
    return false;
#endif
  }
  void finishWiFiForOTA() {
#if MESH_WIRELESS_BUILD_WIFI
    uint8_t owned = 1;
    wifi_ota_override_.compare_exchange_strong(owned, 2);
#endif
  }
  uint8_t enabled() const { return backend_ ? backend_->enabled() & BuildMask : 0; }

#if MESH_ENABLE_EMERGENCY_POWER
  // A scheduled battery sleep borrows the live services without changing the
  // user's saved wireless policy or the ordinary off/on restore snapshot.
  bool suspendForEmergency() {
    if (!backend_) return true;
    if (pending_) return false;
    if (!emergency_suspended_) {
      emergency_enabled_ = enabled();
      emergency_blocked_ = blocked_.load();
      emergency_suspended_ = true;
      blocked_.store(emergency_blocked_ | BuildMask);
    }
    const uint8_t order[] = {
#if MESH_WIRELESS_BUILD_WIFI
      WiFi,
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
      EspNow,
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
      Bluetooth,
#endif
    };
    for (uint8_t service : order) {
      if ((emergency_enabled_ & service) && backend_->set(service, false) != Result::Done)
        return false;
    }
    return enabled() == 0;
  }

  bool resumeAfterEmergency() {
    if (!emergency_suspended_) return true;
    const uint8_t order[] = {
#if MESH_WIRELESS_BUILD_ESPNOW
      EspNow,
#endif
#if MESH_WIRELESS_BUILD_WIFI
      WiFi,
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
      Bluetooth,
#endif
    };
    for (uint8_t service : order) {
      if (!(emergency_enabled_ & service) || master_off_) continue;
#if MESH_WIRELESS_BUILD_ESPNOW
      if (service == EspNow && hasEspNowPreference()
          && !backend_->espNowPreference() && !espnow_runtime_override_) {
        emergency_blocked_ |= EspNow;
        continue;
      }
#endif
#if MESH_WIRELESS_BUILD_WIFI
      if (service == WiFi && !wifiPreference()
          && (!wifi_runtime_override_ || wifi_ota_override_.load() == 2)) {
        emergency_blocked_ |= WiFi;
        continue;
      }
#endif
      blocked_.store(blocked_.load() & ~service);
      if (backend_->set(service, true) != Result::Done) return false;
    }
    blocked_.store(emergency_blocked_);
    emergency_suspended_ = false;
    return true;
  }
#endif

  bool handle(const char* text, char* reply, size_t size, uint32_t now, uint8_t requester) {
    const auto command = parse(text);
    if (command.action == Action::None || !reply || !size) return false;
    const uint8_t available = backend_ ? backend_->available() & BuildMask : 0;
    const char* name = command.scope == All ? "2.4ghz" : command.scope == EspNow ? "espnow" : "wifi";
    if (command.action == Action::Invalid) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: use set wifi/espnow on|off [force]; set 2.4ghz on [all|force]|off [force]", "Error: set wifi/espnow on|off [force]; set 2.4ghz on [all]|off [force]"));
      return true;
    }
    const uint8_t current = enabled();
    if (command.action == Action::GetPreference) {
      if (command.scope == EspNow && backend_ && hasEspNowPreference())
        snprintf(reply, size, "> %s", backend_->espNowPreference() ? "on" : "off");
      else if (command.scope == WiFi && hasWiFiPreference())
        snprintf(reply, size, "> %u", wifiPreference() ? 1u : 0u);
      else snprintf(reply, size, "%s unavailable", name);
      return true;
    }
    if (command.action == Action::Get) {
      const auto state = [=](uint8_t bit) { return !(available & bit) ? "unavailable" : current & bit ? "on" : "off"; };
      if (command.scope != All) snprintf(reply, size, "%s %s%s%s", name, state(command.scope), pending_ ? " (pending)" : "", error_ ? error_ : "");
      else snprintf(reply, size, "wifi=%s, bluetooth=%s, espnow=%s%s%s", state(WiFi), state(Bluetooth), state(EspNow), pending_ ? " (pending)" : "", error_ ? error_ : "");
      return true;
    }
#if MESH_ENABLE_EMERGENCY_POWER
    if (emergency_suspended_) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: scheduled sleep; use set power.awake <minutes> first", "Error: asleep; set power.awake <minutes>"));
      return true;
    }
#endif
    const bool enable_all = command.action == Action::OnAll && backend_ && backend_->hasGroupPreference();
    const bool persistent_wifi = (command.scope == WiFi || (enable_all && (available & WiFi))) && hasWiFiPreference();
    const bool persistent_espnow = (command.scope == EspNow || (enable_all && (available & EspNow)))
        && backend_ && hasEspNowPreference();
#if MESH_WIRELESS_BUILD_BLUETOOTH
    const bool persistent_bluetooth = enable_all && (available & Bluetooth)
        && hasBluetoothPreference();
#endif
    const bool persistent_group = command.scope == All && backend_ && backend_->hasGroupPreference();
    const bool reboot_wifi = command.scope == WiFi && persistent_wifi && wifiRequiresReboot();
    if (!(available & command.scope) && !reboot_wifi) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: requested wireless service unavailable in this build/boot", "Error: wireless unavailable"));
      return true;
    }
    const bool on = command.action == Action::On || command.action == Action::OnAll;
    if (pending_ && !(command.scope == All && group_request_)
        && (persistent_wifi || persistent_espnow || persistent_group || preferenceRollbackPending())) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: wireless change pending; get 2.4ghz for status", "Error: pending; get 2.4ghz"));
      return true;
    }
    if (pending_ && command.scope != All && (command.scope & available) != scope_) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: wireless change pending; get 2.4ghz for status", "Error: pending; get 2.4ghz"));
      return true;
    }
    if (command.scope != All && on && master_off_) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: 2.4ghz is off; use set 2.4ghz on first", "Error: 2.4ghz off; set 2.4ghz on"));
      return true;
    }
    const uint8_t scope = reboot_wifi ? uint8_t(WiFi) : command.scope & available;
    uint8_t target = !on ? 0 : command.scope == All && command.action != Action::OnAll
        ? (saved_valid_ ? saved_ : current) & available : scope;
    if (command.scope == All && on && command.action != Action::OnAll) {
      // A preference edited while the group was paused takes precedence over
      // its older live snapshot. Temporary OTA sessions are not restored.
      if (hasWiFiPreference() && !wifiPreference()) target &= ~WiFi;
      if (hasEspNowPreference() && !backend_->espNowPreference()) target &= ~EspNow;
      if (hasBluetoothPreference() && !backend_->bluetoothPreference()) target &= ~Bluetooth;
    }
    // Restoring a saved subset may also switch off services enabled since a
    // failed restore. Protect the actual removals, not just commands named off.
    const uint8_t disabling = scope & ~target;
    const bool retained = (requester & ~disabling) != 0;
    const bool force = command.action == Action::ForceOff;
    if ((current & disabling) && !force && !retained && !(backend_->clients() & ~disabling)) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: no remaining management connection; use %s or USB/Ethernet", "Error: no remaining link; use %s/USB/Ethernet"),
               on ? "on all" : "off force");
      return true;
    }
    if (enable_all && (((available & WiFi) && !hasWiFiPreference())
        || ((available & EspNow) && !hasEspNowPreference())
        || ((available & Bluetooth) && !hasBluetoothPreference()))) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: enable-all preference unavailable; use set 2.4ghz on", "Error: cannot save all; set 2.4ghz on"));
      return true;
    }
    // A replacement group command may fail admission. Keep the old pending
    // transaction's cancellation record until the new request is fully saved.
    struct AdmissionRollback {
      Control& c;
      const bool replacing, before_start;
#if MESH_WIRELESS_BUILD_WIFI
      const bool wifi, wifi_previous;
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
      const bool espnow, espnow_previous;
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
      const bool bluetooth, bluetooth_previous;
#endif
      const bool group, group_off, group_valid;
      const uint8_t group_saved;
      bool accepted = false;
      AdmissionRollback(Control& owner, bool replace)
        : c(owner), replacing(replace), before_start(!c.started_)
#if MESH_WIRELESS_BUILD_WIFI
          , wifi(c.wifi_rollback_pending_), wifi_previous(c.wifi_previous_)
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
          , espnow(c.espnow_rollback_pending_), espnow_previous(c.espnow_previous_)
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
          , bluetooth(c.bluetooth_rollback_pending_), bluetooth_previous(c.bluetooth_previous_)
#endif
          , group(c.group_rollback_pending_), group_off(c.group_previous_off_)
          , group_valid(c.group_previous_valid_), group_saved(c.group_previous_saved_) {
        if (replacing) c.clearPreferenceRollback();
      }
      void accept() {
        if (replacing && before_start) {
          // Repeated Off still depends on the first request's live fallback.
          // Cancellation before any teardown restores that original baseline.
#if MESH_WIRELESS_BUILD_WIFI
          if (wifi) {c.wifi_rollback_pending_=true;c.wifi_previous_=wifi_previous;}
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
          if (espnow) {c.espnow_rollback_pending_=true;c.espnow_previous_=espnow_previous;}
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
          if (bluetooth) {c.bluetooth_rollback_pending_=true;c.bluetooth_previous_=bluetooth_previous;}
#endif
          if (group) {
            c.group_rollback_pending_=true;c.group_previous_off_=group_off;
            c.group_previous_valid_=group_valid;c.group_previous_saved_=group_saved;
          }
        }
        accepted = true;
      }
      ~AdmissionRollback() {
        if (!replacing || accepted) return;
#if MESH_WIRELESS_BUILD_WIFI
        c.wifi_rollback_pending_=wifi; c.wifi_previous_=wifi_previous;
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
        c.espnow_rollback_pending_=espnow; c.espnow_previous_=espnow_previous;
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
        c.bluetooth_rollback_pending_=bluetooth; c.bluetooth_previous_=bluetooth_previous;
#endif
        c.group_rollback_pending_=group; c.group_previous_off_=group_off;
        c.group_previous_valid_=group_valid; c.group_previous_saved_=group_saved;
      }
    } admission(*this, pending_ && command.scope == All && group_request_);
#if MESH_WIRELESS_BUILD_WIFI
    if (persistent_wifi) {
      const bool previous = wifiPreference();
      const bool was_blocked = blocked(WiFi);
      if (!backend_->saveWiFiPreference(on)) {
        snprintf(reply, size, "Error: WiFi setting could not be saved");
        return true;
      }
      wifi_policy_.store(on ? 2 : 1);
      if (reboot_wifi) {
        // These builds keep their selected transport until restart. Saving
        // the next boot must neither stop today's WiFi nor start an unavailable
        // stack in a Bluetooth-only boot.
        wifi_runtime_override_ = !was_blocked;
        wifi_ota_override_.store(0);
        snprintf(reply, size, MESH_CLI_REPLY_TEXT("OK - WiFi setting saved; reboot to apply", "OK - saved; reboot to apply"));
        return true;
      }
      wifi_previous_ = previous;
      wifi_rollback_pending_ = previous != on;
    }
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
    if (persistent_espnow) {
      espnow_previous_ = backend_->espNowPreference();
      if (!backend_->saveEspNowPreference(on)) {
        snprintf(reply, size, "%s", rollbackPreferences()
            ? MESH_CLI_REPLY_TEXT("Error: ESP-NOW setting could not be saved", "Error: save unconfirmed")
            : MESH_CLI_REPLY_TEXT("Error: ESP-NOW save failed; previous settings restore unconfirmed", "Error: save/restore unconfirmed"));
        return true;
      }
      espnow_rollback_pending_ = espnow_previous_ != on;
    }
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
    if (persistent_bluetooth) {
      bluetooth_previous_ = backend_->bluetoothPreference();
      if (!backend_->saveBluetoothPreference(on)) {
        snprintf(reply, size, "%s", rollbackPreferences()
            ? MESH_CLI_REPLY_TEXT("Error: Bluetooth setting could not be saved", "Error: save unconfirmed")
            : MESH_CLI_REPLY_TEXT("Error: Bluetooth save failed; previous settings restore unconfirmed", "Error: save/restore unconfirmed"));
        return true;
      }
      bluetooth_rollback_pending_ = bluetooth_previous_ != on;
    }
#endif
    if (persistent_group) {
      group_previous_off_ = master_off_;
      group_previous_valid_ = saved_valid_;
      group_previous_saved_ = saved_;
      const uint8_t restore = saved_valid_ ? saved_ : current;
      if (!backend_->saveGroupPreference(!on, on ? 0 : restore)) {
        snprintf(reply, size, "%s", rollbackPreferences()
            ? MESH_CLI_REPLY_TEXT("Error: 2.4ghz setting could not be saved", "Error: save unconfirmed")
            : MESH_CLI_REPLY_TEXT("Error: 2.4ghz save failed; previous settings restore unconfirmed", "Error: save/restore unconfirmed"));
        return true;
      }
      group_rollback_pending_ = true;
    }
    if (command.scope == All && !on && !saved_valid_) {
      saved_ = current;
      saved_valid_ = true;
    }
    admission.accept();
    saved_change_ = persistent_wifi || persistent_espnow || persistent_group;
#if MESH_WIRELESS_BUILD_WIFI
    if (scope & WiFi) wifi_ota_override_.store(0);
#endif
    scope_ = scope;
    group_request_ = command.scope == All;
    target_ = target;
    force_ = force;
    // An actual USB/UART command proves a host session even on adapters with
    // no DTR reporting. Wireless requesters can disconnect during reply drain;
    // only their live management sessions may authorize the delayed shutdown.
    independent_requester_ = (requester & Independent) != 0;
    pending_ = true;
    started_ = false;
    error_ = nullptr;
    due_ = now + 250;
    // Keep the group latch independent of the set of services available on a board.
    if (command.scope == All) master_off_ = !on;
#if defined(ESP_PLATFORM)
    if (command.scope == WiFi) {
      if (persistent_wifi) snprintf(reply, size, "%s", on
          ? MESH_CLI_REPLY_TEXT("OK - WiFi enabled (saved); use get wifi.status to check connection", "OK - WiFi on (saved); get wifi.status")
          : MESH_CLI_REPLY_TEXT("OK - WiFi disabled (saved)", "OK - WiFi off (saved)"));
      else snprintf(reply, size, "%s", on
          ? MESH_CLI_REPLY_TEXT("OK - WiFi enabled for this boot; use get wifi.status to check connection", "OK - WiFi on (boot); get wifi.status")
          : MESH_CLI_REPLY_TEXT("OK - WiFi disabled for this boot", "OK - WiFi off (boot)"));
    } else
#endif
    {
      if (persistent_wifi && command.scope == WiFi) snprintf(reply, size, "OK - WiFi %s (saved)", on ? "enabled" : "disabled");
      else if (persistent_espnow || persistent_group)
        snprintf(reply, size, "OK - %s %s (saved)", name, on ? "on" : "off");
      else snprintf(reply, size, MESH_CLI_REPLY_TEXT("OK - %s %s requested (this boot); get %s for status", "OK - %s %s (boot); get %s"),
                    name, on ? "on" : "off", name);
    }
    return true;
  }

  void service(uint32_t now) {
    if (!backend_) return;
#if MESH_WIRELESS_BUILD_WIFI
    if (!pending_ && wifi_ota_override_.load() == 2) {
      if (master_off_ || !wifiPreference()) {
        scope_ = WiFi & backend_->available();
        target_ = 0;
        force_ = independent_requester_ = true;
        pending_ = scope_ != 0;
        started_ = false;
        due_ = now + 250;
        error_ = nullptr;
        if (!scope_) {
          blocked_.store(blocked_.load() | WiFi);
          wifi_runtime_override_ = false;
          wifi_ota_override_.store(0);
        }
      } else {
        wifi_ota_override_.store(0);
      }
    }
#endif
    if (!pending_ || int32_t(now - due_) < 0) return;
    if (!started_) {
      const uint8_t disabling = scope_ & ~target_;
      if ((enabled() & disabling) && !force_ && !independent_requester_ && !(backend_->clients() & ~disabling)) {
        pending_ = false;
        const bool persistent = group_rollback_pending_;
        if (!persistent && master_off_) { master_off_ = false; saved_valid_ = false; }
        error_ = rollbackPreferences() ? MESH_CLI_REPLY_TEXT(" (cancelled: connection lost)", " (cancelled: link lost)")
            : MESH_CLI_REPLY_TEXT(" (cancelled: saved setting could not be restored)", " (cancelled: saved unconfirmed)");
        return;
      }
      if (backend_->replyBusy() && uint32_t(now - due_) < 1750) return;
      blocked_.store((blocked_.load() & ~scope_) | (scope_ & ~target_));
#if MESH_WIRELESS_BUILD_WIFI
      if (scope_ & WiFi) {
        wifi_runtime_override_ = (target_ & WiFi) != 0;
        wifi_ota_override_.store(0);
      }
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
      if (scope_ & EspNow) espnow_runtime_override_ = (target_ & EspNow) != 0;
#endif
      clearPreferenceRollback();
      started_ = true;
    }
    // Infrastructure services own tasks and sockets: stop them before ESP-NOW's
    // WiFi driver. Restore ESP-NOW first so infrastructure WiFi can coexist.
    const uint8_t order[] = {
#if MESH_WIRELESS_BUILD_WIFI
      WiFi,
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
      EspNow,
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
      Bluetooth,
#endif
#if MESH_WIRELESS_BUILD_ESPNOW
      EspNow,
#endif
#if MESH_WIRELESS_BUILD_WIFI
      WiFi,
#endif
#if MESH_WIRELESS_BUILD_BLUETOOTH
      Bluetooth,
#endif
    };
    for (unsigned i = 0; i < sizeof(order); ++i) {
      const uint8_t bit = order[i];
      const bool on = (target_ & bit) != 0;
      if (!(scope_ & bit) || on != (i >= sizeof(order) / 2)) continue;
      const auto result = backend_->set(bit, on);
      if (result == Result::Pending && uint32_t(now - due_) < 30000) return;
      if (result != Result::Done) {
        pending_ = false;
        error_ = saved_change_ ? MESH_CLI_REPLY_TEXT(" (runtime apply failed; setting saved)", " (apply failed; setting saved)")
            : MESH_CLI_REPLY_TEXT(" (runtime apply failed)", " (apply failed)");
        return;
      }
    }
    pending_ = false;
    clearPreferenceRollback();
    if (!master_off_) saved_valid_ = false;
  }
};

#endif // MESH_WIRELESS_NO_SERVICES

inline Control& control() { static Control instance; return instance; }

} // namespace wireless
} // namespace mesh
