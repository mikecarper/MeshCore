#pragma once

#include "FindMyConfig.h"
#include "FindMyPacket.h"

namespace mesh { namespace findmy {

// The sensor loop owns this controller and is the only writer of its config.
// Backend is deliberately injected so storage and lifecycle can be tested
// without a Bluetooth controller.
template <typename Filesystem, typename Backend>
class SensorFindMy {
  Filesystem* fs_ = nullptr;
  Config config_;
  Backend backend_;
  bool storage_fault_ = false;
  bool stop_fault_ = false;
  bool suspended_ = false;
  bool escalated_ = false;
  bool attempted_ = false;
  uint32_t last_attempt_ = 0;
  uint16_t active_interval_ms_ = 0;

 public:
  void begin(Filesystem* fs) {
    fs_ = fs;
    const StoreResult loaded = loadConfig(fs_, config_);
    storage_fault_ = loaded != StoreResult::Success && loaded != StoreResult::NotFound;
    if (storage_fault_) config_ = Config();
  }

  // Do not keep broadcasting a key from RAM after its settings are erased.
  bool prepareErase() {
    config_ = Config();
    storage_fault_ = true;
    attempted_ = false;
    stop_fault_ = !backend_.stop();
    return !stop_fault_;
  }

  void finishErase(bool success) {
    config_ = Config();
    storage_fault_ = !success;
  }

  bool handleCommand(const char* command, char* reply, size_t capacity, uint32_t now) {
    Config candidate = config_;
    const CommandResult result = parseCommand(command, candidate, reply, capacity);
    if (result == CommandResult::Unhandled) return false;
    if (result == CommandResult::Query) {
      const size_t used = strlen(reply);
      if (used < capacity) snprintf(reply + used, capacity - used, " beacon=%s rate=%s%s",
          stop_fault_ ? "stop-error" : (backend_.isActive() ? "active" : (suspended_ ? "suspended" : "inactive")),
          escalated_ ? "fast" : "lazy",
          storage_fault_ ? " storage=error" : "");
      return true;
    }
    if (result == CommandResult::Error) return true;
    if (storage_fault_ || saveConfig(fs_, candidate) != StoreResult::Success) {
      // A failed save can be ambiguous after publication. Never keep using a
      // possibly stale key or resume until a clean reboot reloads the store.
      stop_fault_ = !backend_.stop();
      storage_fault_ = true;
      snprintf(reply, capacity, stop_fault_ ? "ERR: save and Bluetooth stop failed"
                                            : "ERR: findmy save failed; beacon stopped");
      return true;
    }
    config_ = candidate;
    stop_fault_ = !backend_.stop();
    attempted_ = false;
    if (stop_fault_) {
      snprintf(reply, capacity, "ERR: findmy saved; Bluetooth stop failed");
      return true;
    }
    service(suspended_, now, escalated_);
    if (!config_.enabled) {
      snprintf(reply, capacity, backend_.isActive()
          ? "ERR: findmy saved off; Bluetooth stop failed" : "OK - findmy off");
    } else {
      snprintf(reply, capacity, "OK - findmy saved; beacon=%s",
          stop_fault_ ? "stop-error" : (backend_.isActive() ? "active" : (suspended_ ? "suspended" : "unavailable")));
    }
    return true;
  }

  void service(bool suspend, uint32_t now, bool escalated = false) {
    suspended_ = suspend;
    escalated_ = escalated;
    if (storage_fault_ || suspend || !config_.enabled || !config_.has_key) {
      stop_fault_ = !backend_.stop();
      if (!stop_fault_) active_interval_ms_ = 0;
      attempted_ = false;
      return;
    }
    if (stop_fault_) {
      if (!backend_.stop()) return;
      stop_fault_ = false;
      active_interval_ms_ = 0;
      attempted_ = false;
    }
    // The long interval is intentionally bounded by legacy BLE's 10.24 s
    // maximum. This alone does not guarantee low power on every platform.
    const uint16_t requested_interval_ms = escalated ? 1280 : 10000;
    if (backend_.isActive()) {
      if (active_interval_ms_ == requested_interval_ms) return;
      if (!backend_.stop()) { stop_fault_ = true; return; }
      active_interval_ms_ = 0;
      attempted_ = false;
    }
    if (attempted_ && static_cast<uint32_t>(now - last_attempt_) < 60000u) return;
    attempted_ = true;
    last_attempt_ = now;
    uint8_t addr[6], payload[29];
    buildPacket(config_.key, addr, payload);
    if (!backend_.start(addr, payload, requested_interval_ms)) {
      // Startup may fail after acquiring the controller, and the backend's
      // own cleanup can fail too. Retry teardown before any later start.
      stop_fault_ = !backend_.stop();
      active_interval_ms_ = 0;
    } else {
      active_interval_ms_ = requested_interval_ms;
    }
  }
};

}} // namespace mesh::findmy
