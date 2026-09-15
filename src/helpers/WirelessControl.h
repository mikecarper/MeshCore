#pragma once

#include <atomic>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace wireless {

enum Service : uint8_t { WiFi = 1, Bluetooth = 2, EspNow = 4, All = 7, Independent = 8 };
enum class Result { Done, Pending, Failed };
enum class Action { None, Get, On, OnAll, Off, ForceOff, Invalid };
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
  const char* key = strncmp(s, "2.4ghz", 6) == 0 ? "2.4ghz" : strncmp(s, "espnow", 6) == 0 ? "espnow" : "wifi";
  const size_t n = strlen(key);
  if (strncmp(s, key, n) || (s[n] && s[n] != ' ' && s[n] != '\t')) return {0, Action::None};
  const uint8_t scope = key[0] == '2' ? All : key[0] == 'e' ? EspNow : WiFi;
  s = skipSpace(s + n);
  if (get) return {scope, *s ? Action::Invalid : Action::Get};
  if (word(s, "on")) return {scope, Action::On};
  if (word(s, "off")) return {scope, Action::Off};
  if (!strncmp(s, "off", 3) && (s[3] == ' ' || s[3] == '\t')
      && word(skipSpace(s + 3), "force")) return {scope, Action::ForceOff};
  if (scope == All && !strncmp(s, "on", 2) && (s[2] == ' ' || s[2] == '\t')) {
    s = skipSpace(s + 2);
    if (word(s, "all") || word(s, "force")) return {scope, Action::OnAll};
  }
  return {scope, Action::Invalid};
}

class Backend {
public:
  virtual uint8_t available() const = 0; // Services usable in this boot.
  virtual uint8_t enabled() const = 0;
  virtual uint8_t clients() const = 0;   // Open management sessions, not association/power.
  virtual bool replyBusy() const { return false; }
  virtual Result set(uint8_t service, bool on) = 0;
};

class Control {
  Backend* backend_ = nullptr;
  std::atomic<uint8_t> blocked_{0};
  uint8_t scope_ = 0, target_ = 0, saved_ = 0;
  bool pending_ = false, started_ = false, force_ = false, retained_requester_ = false;
  bool saved_valid_ = false, master_off_ = false;
  uint32_t due_ = 0;
  const char* error_ = nullptr;

public:
  void begin(Backend& backend) { backend_ = &backend; }
  bool blocked(uint8_t service) const { return (blocked_.load() & service) != 0; }
  bool pending() const { return pending_; }
  bool masterOff() const { return master_off_; }
  bool allowService(uint8_t service) {
    if (master_off_ || pending_) return false;
    blocked_.store(blocked_.load() & ~service);
    return true;
  }
  uint8_t enabled() const { return backend_ ? backend_->enabled() : 0; }

  bool handle(const char* text, char* reply, size_t size, uint32_t now, uint8_t requester) {
    const auto command = parse(text);
    if (command.action == Action::None || !reply || !size) return false;
    const uint8_t available = backend_ ? backend_->available() : 0;
    const char* name = command.scope == All ? "2.4ghz" : command.scope == EspNow ? "espnow" : "wifi";
    if (command.action == Action::Invalid) {
      snprintf(reply, size, "Error: use set wifi/espnow on|off [force]; set 2.4ghz on [all|force]|off [force]");
      return true;
    }
    const uint8_t current = enabled();
    if (command.action == Action::Get) {
      const auto state = [=](uint8_t bit) { return !(available & bit) ? "unavailable" : current & bit ? "on" : "off"; };
      if (command.scope != All) snprintf(reply, size, "%s %s%s%s", name, state(command.scope), pending_ ? " (pending)" : "", error_ ? error_ : "");
      else snprintf(reply, size, "wifi=%s, bluetooth=%s, espnow=%s%s%s", state(WiFi), state(Bluetooth), state(EspNow), pending_ ? " (pending)" : "", error_ ? error_ : "");
      return true;
    }
    if (!(available & command.scope)) {
      snprintf(reply, size, "Error: requested wireless service unavailable in this build/boot");
      return true;
    }
    const bool on = command.action == Action::On || command.action == Action::OnAll;
    if (pending_ && command.scope != All && (command.scope & available) != scope_) {
      snprintf(reply, size, "Error: wireless change pending; get 2.4ghz for status");
      return true;
    }
    if (command.scope != All && on && master_off_) {
      snprintf(reply, size, "Error: 2.4ghz is off; use set 2.4ghz on first");
      return true;
    }
    const uint8_t scope = command.scope & available;
    const bool retained = (requester & ~scope) != 0;
    const bool force = command.action == Action::ForceOff;
    if (!on && (current & scope) && !force && !retained && !(backend_->clients() & ~scope)) {
      snprintf(reply, size, "Error: no remaining management connection; use off force or USB/Ethernet");
      return true;
    }
    if (command.scope == All && !on && !saved_valid_) {
      saved_ = current;
      saved_valid_ = true;
    }
    scope_ = scope;
    target_ = !on ? 0 : command.scope == All && command.action != Action::OnAll
        ? (saved_valid_ ? saved_ : current) & available : scope;
    force_ = force;
    retained_requester_ = retained;
    pending_ = true;
    started_ = false;
    error_ = nullptr;
    due_ = now + 250;
    // Keep the group latch independent of the set of services available on a board.
    if (command.scope == All) master_off_ = !on;
    snprintf(reply, size, "OK - %s %s requested (this boot); get %s for status",
             name, on ? "on" : "off", name);
    return true;
  }

  void service(uint32_t now) {
    if (!pending_ || !backend_ || int32_t(now - due_) < 0) return;
    if (!started_) {
      if (!target_ && (enabled() & scope_) && !force_ && !retained_requester_ && !(backend_->clients() & ~scope_)) {
        pending_ = false;
        if (master_off_) { master_off_ = false; saved_valid_ = false; }
        error_ = " (cancelled: connection lost)";
        return;
      }
      if (backend_->replyBusy() && uint32_t(now - due_) < 1750) return;
      blocked_.store((blocked_.load() & ~scope_) | (scope_ & ~target_));
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
