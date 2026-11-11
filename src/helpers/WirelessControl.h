#pragma once

#include "CLIReplyText.h"

#include <atomic>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "EmergencyPowerConfig.h"

namespace mesh {
namespace wireless {

enum Service : uint8_t { WiFi = 1, Bluetooth = 2, EspNow = 4, All = 7, Independent = 8 };
enum class Result { Done, Pending, Failed };
enum class Action { None, Get, GetPreference, On, OnAll, Off, ForceOff, Invalid };
struct Command { uint8_t scope; Action action; };

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
  const char* key = strncmp(s, "2.4ghz", 6) == 0 ? "2.4ghz" : strncmp(s, "espnow", 6) == 0 ? "espnow"
      : strncmp(s, "wifi.enabled", 12) == 0 ? "wifi.enabled" : "wifi";
  const size_t n = strlen(key);
  if (strncmp(s, key, n) || (s[n] && s[n] != ' ' && s[n] != '\t')) return {0, Action::None};
  const uint8_t scope = key[0] == '2' ? All : key[0] == 'e' ? EspNow : WiFi;
  s = skipSpace(s + n);
  if (get) return {scope, *s ? Action::Invalid : n == 12 ? Action::GetPreference : Action::Get};
  if (word(s, "on") || (scope == WiFi && word(s, "1"))) return {scope, Action::On};
  if (word(s, "off") || (scope == WiFi && word(s, "0"))) return {scope, Action::Off};
  if (!strncmp(s, "off", 3) && (s[3] == ' ' || s[3] == '\t')
      && word(skipSpace(s + 3), "force")) return {scope, Action::ForceOff};
  if (scope == WiFi && s[0] == '0' && (s[1] == ' ' || s[1] == '\t')
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
    snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: use set wifi/espnow on|off [force]; set 2.4ghz on [all|force]|off [force]", "Error: set wifi/espnow on|off [force]; set 2.4ghz on [all|force]|off [force]"));
  } else if (command.action == Action::Get || command.action == Action::GetPreference) {
    if (command.scope == All)
      snprintf(reply, size, "wifi=unavailable, bluetooth=unavailable, espnow=unavailable");
    else
      snprintf(reply, size, "%s unavailable", command.scope == WiFi ? "wifi" : "espnow");
  } else {
    snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: requested wireless service unavailable in this build/boot", "Error: wireless unavailable this build/boot"));
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
};

class Control {
  Backend* backend_ = nullptr;
  std::atomic<uint8_t> blocked_{0};
  uint8_t scope_ = 0, target_ = 0, saved_ = 0;
  bool pending_ = false, started_ = false, force_ = false, independent_requester_ = false;
  bool saved_valid_ = false, master_off_ = false;
  // 0: no saved WiFi setting; 1: saved off; 2: saved on.
  std::atomic<uint8_t> wifi_policy_{0};
  std::atomic<bool> wifi_runtime_override_{false};
  // OTA may borrow a saved-off radio. Its task only requests cleanup; the
  // ordinary main-loop service owns teardown and reply-drain state.
  std::atomic<uint8_t> wifi_ota_override_{0}; // 0 none, 1 owned, 2 cleanup requested
  bool wifi_rollback_pending_ = false, wifi_previous_ = true;
#if MESH_ENABLE_EMERGENCY_POWER
  bool emergency_suspended_ = false;
  uint8_t emergency_enabled_ = 0, emergency_blocked_ = 0;
#endif
  uint32_t due_ = 0;
  const char* error_ = nullptr;

  bool rollbackWiFiPreference() {
    if (!wifi_rollback_pending_) return true;
    wifi_rollback_pending_ = false;
    if (!backend_->saveWiFiPreference(wifi_previous_)) return false;
    wifi_policy_.store(wifi_previous_ ? 2 : 1);
    return true;
  }

public:
  void begin(Backend& backend) {
    backend_ = &backend;
    const bool saved = backend.hasWiFiPreference();
    const bool on = !saved || backend.wifiPreference();
    wifi_policy_.store(saved ? (on ? 2 : 1) : 0);
    if (!on) blocked_.store(blocked_.load() | WiFi);
  }
  bool hasWiFiPreference() const { return wifi_policy_.load() != 0; }
  bool wifiPreference() const { return wifi_policy_.load() != 1; }
  bool blocked(uint8_t service) const {
    return (blocked_.load() & service) != 0
        || ((service & WiFi) && !wifiPreference() && wifi_ota_override_.load() == 2);
  }
  bool pending() const { return pending_; }
  // New background owners must honor the reply-drain grace period before
  // the backend actually shuts down a requested service.
  bool pendingDisable(uint8_t service) const {
    return pending_ && (scope_ & service) && !(target_ & service);
  }
  bool masterOff() const { return master_off_; }
  bool allowService(uint8_t service) {
#if MESH_ENABLE_EMERGENCY_POWER
    if (emergency_suspended_) return false;
#endif
    if (master_off_ || pending_
        || ((service & WiFi) && !wifiPreference() && wifi_ota_override_.load() == 2)) return false;
    if ((service & WiFi) && !wifi_runtime_override_
        && (!wifiPreference() || (backend_ && backend_->wifiRequiresReboot() && blocked(WiFi)))) return false;
    blocked_.store(blocked_.load() & ~service);
    return true;
  }
  // An explicit OTA start owns its WiFi startup, including after a user has
  // disabled 2.4 GHz for this boot. Leave the other services disabled. Do not
  // race a pending shutdown, which could otherwise turn OTA off afterwards.
  bool allowWiFiForOTA() {
#if MESH_ENABLE_EMERGENCY_POWER
    if (emergency_suspended_) return false;
#endif
    if (pending_) return false;
    if (!wifiPreference() && (!wifi_runtime_override_ || wifi_ota_override_.load() == 2))
      wifi_ota_override_.store(1);
    blocked_.store(blocked_.load() & ~WiFi);
    wifi_runtime_override_ = true;
    master_off_ = false;
    saved_valid_ = false;
    error_ = nullptr;
    return true;
  }
  void finishWiFiForOTA() {
    uint8_t owned = 1;
    wifi_ota_override_.compare_exchange_strong(owned, 2);
  }
  uint8_t enabled() const { return backend_ ? backend_->enabled() : 0; }

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
      blocked_.store(emergency_blocked_ | All);
    }
    const uint8_t order[] = {WiFi, EspNow, Bluetooth};
    for (uint8_t service : order) {
      if ((emergency_enabled_ & service) && backend_->set(service, false) != Result::Done)
        return false;
    }
    return enabled() == 0;
  }

  bool resumeAfterEmergency() {
    if (!emergency_suspended_) return true;
    const uint8_t order[] = {EspNow, WiFi, Bluetooth};
    for (uint8_t service : order) {
      if (!(emergency_enabled_ & service)) continue;
      if (service == WiFi && !wifiPreference()
          && (!wifi_runtime_override_ || wifi_ota_override_.load() == 2)) {
        emergency_blocked_ |= WiFi;
        continue;
      }
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
    const uint8_t available = backend_ ? backend_->available() : 0;
    const char* name = command.scope == All ? "2.4ghz" : command.scope == EspNow ? "espnow" : "wifi";
    if (command.action == Action::Invalid) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: use set wifi/espnow on|off [force]; set 2.4ghz on [all|force]|off [force]", "Error: set wifi/espnow on|off [force]; set 2.4ghz on [all|force]|off [force]"));
      return true;
    }
    const uint8_t current = enabled();
    if (command.action == Action::GetPreference) {
      if (hasWiFiPreference()) snprintf(reply, size, "> %u", wifiPreference() ? 1u : 0u);
      else snprintf(reply, size, "wifi unavailable");
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
      snprintf(reply, size, "Error: scheduled sleep; use set power.awake <minutes> first");
      return true;
    }
#endif
    const bool persistent_wifi = command.scope == WiFi && hasWiFiPreference();
    const bool reboot_wifi = persistent_wifi && backend_->wifiRequiresReboot();
    if (!(available & command.scope) && !reboot_wifi) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: requested wireless service unavailable in this build/boot", "Error: wireless unavailable this build/boot"));
      return true;
    }
    const bool on = command.action == Action::On || command.action == Action::OnAll;
    if (pending_ && (persistent_wifi || wifi_rollback_pending_)) {
      snprintf(reply, size, "Error: wireless change pending; get 2.4ghz for status");
      return true;
    }
    if (pending_ && command.scope != All && (command.scope & available) != scope_) {
      snprintf(reply, size, "Error: wireless change pending; get 2.4ghz for status");
      return true;
    }
    if (command.scope != All && on && master_off_) {
      snprintf(reply, size, MESH_CLI_REPLY_TEXT("Error: 2.4ghz is off; use set 2.4ghz on first", "Error: 2.4ghz off; set 2.4ghz on first"));
      return true;
    }
    const uint8_t scope = reboot_wifi ? uint8_t(WiFi) : command.scope & available;
    const uint8_t target = !on ? 0 : command.scope == All && command.action != Action::OnAll
        ? (saved_valid_ ? saved_ : current) & available : scope;
    // Restoring a saved subset may also switch off services enabled since a
    // failed restore. Protect the actual removals, not just commands named off.
    const uint8_t disabling = scope & ~target;
    const bool retained = (requester & ~disabling) != 0;
    const bool force = command.action == Action::ForceOff;
    if ((current & disabling) && !force && !retained && !(backend_->clients() & ~disabling)) {
      snprintf(reply, size, "Error: no remaining management connection; use %s or USB/Ethernet",
               on ? "on all" : "off force");
      return true;
    }
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
        snprintf(reply, size, "OK - WiFi setting saved; reboot to apply");
        return true;
      }
      wifi_previous_ = previous;
      wifi_rollback_pending_ = previous != on;
    }
    if (command.scope == All && !on && !saved_valid_) {
      saved_ = current;
      saved_valid_ = true;
    }
    if (scope & WiFi) wifi_ota_override_.store(0);
    scope_ = scope;
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
          ? "OK - WiFi enabled (saved); use get wifi.status to check connection"
          : "OK - WiFi disabled (saved)");
      else snprintf(reply, size, "%s", on
          ? "OK - WiFi enabled for this boot; use get wifi.status to check connection"
          : "OK - WiFi disabled for this boot");
    } else
#endif
    {
      if (persistent_wifi) snprintf(reply, size, "OK - WiFi %s (saved)", on ? "enabled" : "disabled");
      else snprintf(reply, size, "OK - %s %s requested (this boot); get %s for status",
                    name, on ? "on" : "off", name);
    }
    return true;
  }

  void service(uint32_t now) {
    if (!backend_) return;
    if (!pending_ && wifi_ota_override_.load() == 2) {
      if (!wifiPreference()) {
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
    if (!pending_ || int32_t(now - due_) < 0) return;
    if (!started_) {
      const uint8_t disabling = scope_ & ~target_;
      if ((enabled() & disabling) && !force_ && !independent_requester_ && !(backend_->clients() & ~disabling)) {
        pending_ = false;
        if (master_off_) { master_off_ = false; saved_valid_ = false; }
        error_ = rollbackWiFiPreference() ? " (cancelled: connection lost)"
            : " (cancelled: saved WiFi setting could not be restored)";
        return;
      }
      if (backend_->replyBusy() && uint32_t(now - due_) < 1750) return;
      blocked_.store((blocked_.load() & ~scope_) | (scope_ & ~target_));
      if (scope_ & WiFi) {
        wifi_runtime_override_ = (target_ & WiFi) != 0;
        wifi_ota_override_.store(0);
      }
      wifi_rollback_pending_ = false;
      started_ = true;
    }
    // Infrastructure services own tasks and sockets: stop them before ESP-NOW's
    // WiFi driver. Restore ESP-NOW first so infrastructure WiFi can coexist.
    const uint8_t order[] = {WiFi, EspNow, Bluetooth, EspNow, WiFi, Bluetooth};
    for (unsigned i = 0; i < sizeof(order); ++i) {
      const uint8_t bit = order[i];
      const bool on = (target_ & bit) != 0;
      if (!(scope_ & bit) || on != (i >= 3)) continue;
      const auto result = backend_->set(bit, on);
      if (result == Result::Pending && uint32_t(now - due_) < 30000) return;
      if (result != Result::Done) {
        pending_ = false;
        error_ = " (failed: retry command; check startup/teardown)";
        return;
      }
    }
    pending_ = false;
    if (!master_off_) saved_valid_ = false;
  }
};

inline Control& control() { static Control instance; return instance; }

} // namespace wireless
} // namespace mesh
