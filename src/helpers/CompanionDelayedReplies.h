#pragma once

#include "BaseSerialInterface.h"
#include <stdint.h>

namespace mesh {

// Fixed producer storage, independent of the transport's bounded frame queue.
// History is volatile and finite: it does not authenticate a host process or
// disambiguate arbitrarily late login replies, which have no reflected tag.
class CompanionDelayedReplies {
public:
  CompanionDelayedReplies() noexcept;
  enum Kind : uint8_t { None, Login, Status, Telemetry, Discovery, Binary, Trace };
  enum Phase : uint8_t { Empty, Reserved, AwaitRadio, AwaitAdmission };
  static constexpr uint8_t HISTORY_SIZE = 8;
  static constexpr uint8_t NO_HISTORY = 255;
  static constexpr uint32_t DELIVERY_GRACE_MS = 10000;
  static constexpr uint32_t HOST_EXTRA_TIMEOUT_MS = 5000;
  static constexpr uint32_t LOGIN_QUARANTINE_MS = 30000;
  static constexpr uint32_t LOGIN_INTERVAL_MS = 3000;
  static constexpr uint32_t LOGIN_HOLD_MS = 3000;
  static constexpr uint32_t LOGIN_SERVICE_GRACE_MS = 100;

  struct Reply {
    BaseSerialInterface* route = nullptr;
    uint32_t tag = 0, auth = 0;
    uint32_t timeout = 0, radio_deadline = 0;
    uint32_t sent_deadline = 0, delivery_deadline = 0;
    uint16_t frame_len = 0;
    Kind kind = None;
    Phase phase = Empty;
    uint8_t history = NO_HISTORY;
    bool terminal = false, sent_pending = false, flood = false;
    bool repeat_login = false;
    uint8_t frame[MAX_FRAME_SIZE] = {};
  };

  Reply request, trace;
  struct DeferredLogin {
    BaseSerialInterface* route = nullptr;
    uint32_t deadline = 0;
    uint8_t peer[32] = {};
    char password[16] = {};
    uint8_t retry_history = NO_HISTORY;
  } deferred_login;

private:
  struct RequestGuard {
    uint8_t peer[32] = {};
    uint32_t tag = 0, until = 0;
    Phase phase = Empty;
    bool login = false, completed_login = false;
  };
  struct TraceGuard {
    uint32_t tag = 0, auth = 0, until = 0;
    Phase phase = Empty;
  };
  RequestGuard requests[HISTORY_SIZE] = {};
  TraceGuard traces[HISTORY_SIZE] = {};
  // The effective wire password stays in volatile RAM only. A delivered
  // success permits reopening the same login on the same live host route
  // during its bounded history window. Never persist these credentials.
  char login_password[16] = {};
  bool login_password_valid = false;
  char completed_login_password[16] = {};
  BaseSerialInterface* completed_login_route = nullptr;
  uint8_t completed_login_history = NO_HISTORY;
  uint32_t last_login_send_at = 0;
  bool login_interval_active = false;

  static bool due(uint32_t now, uint32_t deadline);
  static uint32_t retirementDeadline(uint32_t now, uint32_t radio_deadline,
                                     uint32_t floor);
  static uint32_t radioBudget(const Reply& reply);
  static bool expired(const Reply& reply, uint32_t now);
  static void reset(Reply& reply);
  static void arm(Reply& reply, uint32_t timeout, bool flood, uint32_t now);
  static bool store(Reply& reply, const uint8_t* frame, size_t len, uint32_t now);
  static bool service(Reply& reply, BaseSerialInterface* serial, uint32_t now,
                       bool* delivered = nullptr);
  void clearCompletedLogin();
  bool canRepeatCompletedLogin(const uint8_t peer[32], const char* password,
                               BaseSerialInterface* route, uint32_t now) const;
  bool matchesActiveLogin(const uint8_t peer[32], const char* password,
                           BaseSerialInterface* route, uint32_t now) const;
  void prune(uint32_t now);

public:

  bool reserveRequest(Kind kind, const uint8_t peer[32],
                      BaseSerialInterface* route, bool terminal, uint32_t now,
                      const char* password = nullptr);
  void forgetLoginRoute(BaseSerialInterface* route);
  bool loginReady(uint32_t now) const;
  void noteLoginSent(uint32_t now);
  bool queueLogin(const uint8_t peer[32], const char* password,
                   BaseSerialInterface* route, uint32_t now);
  void clearDeferredLogin();
  bool hasDeferredLogin() const { return deferred_login.route != nullptr; }
  void rememberLoginPassword(const char* password);
  bool canRetryLogin(const uint8_t peer[32], const char* password,
                     BaseSerialInterface* route, uint32_t now) const;
  bool allowRequestTag(uint32_t tag);
  void rememberLoginTag(uint32_t tag);
  void armRequest(uint32_t tag, uint32_t timeout, bool flood, uint32_t now);
  void abandonRequest();
  void retireRequest(uint32_t now);
  const uint8_t* requestPeer() const;
  bool requestMatches(const uint8_t peer[32], uint32_t tag) const;
  bool blocksResponse(const uint8_t peer[32], uint32_t tag, uint32_t now) const;
  bool storeRequest(const uint8_t* frame, size_t len, uint32_t now);
  void serviceRequest(BaseSerialInterface* serial, uint32_t now);

  // Terminal TRACE reserves the same tuple history without taking the Binary
  // slot. A live reservation is never evicted to make room for another host.
  uint8_t reserveTrace(uint32_t tag, uint32_t auth, uint32_t now);
  void abandonTrace(uint8_t history);
  void retireTrace(uint8_t history, uint32_t now, uint32_t radio_deadline);
  // A fresh tuple on the same route replaces its current binary trace.
  // Refused reservations leave the current operation intact.
  bool reserveBinaryTrace(uint32_t tag, uint32_t auth,
                          BaseSerialInterface* route, uint32_t now);
  void armBinaryTrace(uint32_t timeout, uint32_t now);
  void abandonBinaryTrace();
  void retireBinaryTrace(uint32_t now);
  bool blocksTrace(uint32_t tag, uint32_t auth, uint32_t now) const;
  bool storeBinaryTrace(const uint8_t* frame, size_t len, uint32_t now);
  void serviceBinaryTrace(BaseSerialInterface* serial, uint32_t now);

  bool hasRequest() const { return request.phase != Empty; }
  bool hasBinaryTrace() const { return trace.phase != Empty; }
  bool hasReplyForRoute(BaseSerialInterface* route, uint32_t now) const;
};

}  // namespace mesh
