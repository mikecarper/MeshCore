#include "CompanionDelayedReplies.h"
#include <string.h>

namespace mesh {

// The stock app adds three seconds to SENT's estimate for legacy telemetry.
static constexpr uint32_t TELEMETRY_APP_EXTRA_MS = 3000;
static constexpr uint32_t TELEMETRY_CACHE_MS = 10000;

// Keep complete member initialization shared, not expanded into a large owner.
#if defined(__GNUC__) && !defined(__clang__)
__attribute__((noinline, noclone))
#elif defined(__clang__)
__attribute__((noinline))
#endif
CompanionDelayedReplies::CompanionDelayedReplies() noexcept {}

bool CompanionDelayedReplies::due(uint32_t now, uint32_t deadline) {
  return (int32_t)(now - deadline) >= 0;
}

uint32_t CompanionDelayedReplies::retirementDeadline(
    uint32_t now, uint32_t radio_deadline, uint32_t floor) {
  uint32_t remaining = due(now, radio_deadline) ? 0 : radio_deadline - now;
  if (remaining + DELIVERY_GRACE_MS > floor) floor = remaining + DELIVERY_GRACE_MS;
  return now + floor;
}

void CompanionDelayedReplies::reset(Reply& reply) {
  // The frame bytes need not be erased: Empty is the sole ownership authority.
  reply.route = nullptr;
  reply.kind = None;
  reply.phase = Empty;
  reply.history = NO_HISTORY;
  reply.frame_len = 0;
  reply.sent_pending = false;
  reply.repeat_login = false;
  reply.repeat_sent = false;
}

uint32_t CompanionDelayedReplies::radioBudget(const Reply& reply) {
  // Leave room for retirement grace within the signed half of millis range.
  const uint32_t maximum = 0x7FFFFFFFU - DELIVERY_GRACE_MS;
  const uint32_t extra = reply.timeout / 5;
  uint32_t budget = reply.timeout > maximum - extra
      ? maximum : reply.timeout + extra;
  if (!reply.terminal) {
    // Request clients add five seconds to SENT's estimate. Keep this longer
    // radio window for traces too; their owner may replace one sooner.
    const uint32_t host_budget = reply.timeout > maximum - HOST_EXTRA_TIMEOUT_MS
        ? maximum : reply.timeout + HOST_EXTRA_TIMEOUT_MS;
    if (host_budget > budget) budget = host_budget;
  }
  return budget;
}

void CompanionDelayedReplies::arm(Reply& reply, uint32_t timeout,
                                  bool flood, uint32_t now) {
  reply.timeout = timeout;
  reply.flood = flood;
  reply.sent_pending = !reply.terminal;
  reply.sent_deadline = now + DELIVERY_GRACE_MS;
  // Before SENT admission only the bounded pre-SENT grace applies. Terminal
  // output has no SENT frame and retains its printed arm-time radio budget.
  reply.radio_deadline = reply.terminal
      ? now + radioBudget(reply) : reply.sent_deadline;
  reply.frame_len = 0;
  reply.phase = AwaitRadio;
}

bool CompanionDelayedReplies::store(Reply& reply, const uint8_t* frame,
                                   size_t len, uint32_t now) {
  if (reply.phase != AwaitRadio || frame == nullptr || len == 0
      || len > sizeof(reply.frame) || due(now, reply.radio_deadline)
      || (reply.sent_pending && due(now, reply.sent_deadline))) return false;
  memcpy(reply.frame, frame, len);
  reply.frame_len = len;
  reply.delivery_deadline = now + DELIVERY_GRACE_MS;
  if (reply.sent_pending
      && (int32_t)(reply.sent_deadline - reply.delivery_deadline) < 0) {
    reply.delivery_deadline = reply.sent_deadline;
  }
  reply.phase = AwaitAdmission;
  return true;
}

bool CompanionDelayedReplies::expired(const Reply& reply, uint32_t now) {
  return due(now, reply.phase == AwaitAdmission
      ? reply.delivery_deadline : reply.radio_deadline)
      || (reply.sent_pending && due(now, reply.sent_deadline));
}

bool CompanionDelayedReplies::service(Reply& reply,
    BaseSerialInterface* serial, uint32_t now, bool* delivered) {
  if (delivered != nullptr) *delivered = false;
  if (reply.phase == Empty || reply.phase == Reserved) return false;
  if (reply.terminal) return due(now, reply.radio_deadline);
  if (serial == nullptr || !serial->isReplyRouteAvailable(reply.route)
      || expired(reply, now)) return true;
  if (reply.sent_pending || reply.repeat_sent) {
    // Re-acknowledge a retry with the existing tag and remaining wait. Only
    // the first SENT admission starts the radio window.
    uint32_t timeout = reply.timeout;
    if (!reply.sent_pending) {
      const uint32_t deadline = reply.phase == AwaitRadio ? reply.radio_deadline : reply.delivery_deadline;
      timeout = deadline - now;
    }
    uint8_t sent[10];
    sent[0] = 6;  // RESP_CODE_SENT, unchanged Companion wire format.
    sent[1] = reply.flood ? 1 : 0;
    memcpy(sent + 2, &reply.tag, 4);
    memcpy(sent + 6, &timeout, 4);
    if (serial->writeFrameToRoute(reply.route, sent, sizeof(sent)) != sizeof(sent)) {
      return false;
    }
    if (reply.sent_pending) {
      reply.sent_pending = false;
      reply.radio_deadline = now + radioBudget(reply);
    }
    if (reply.kind == Telemetry) {
      // Bound the estimate the same way as the radio deadline at wraparound.
      const uint32_t maximum = 0x7FFFFFFFU - TELEMETRY_APP_EXTRA_MS;
      reply.client_deadline = now + (timeout > maximum ? maximum : timeout)
          + TELEMETRY_APP_EXTRA_MS;
    }
    reply.repeat_sent = false;
  }
  const bool admitted = reply.phase == AwaitAdmission
      && serial->writeFrameToRoute(reply.route, reply.frame, reply.frame_len)
          == reply.frame_len;
  if (delivered != nullptr) *delivered = admitted;
  return admitted;
}

void CompanionDelayedReplies::clearCompletedLogin() {
  memset(completed_login_password, 0, sizeof(completed_login_password));
  completed_login_route = nullptr;
  completed_login_history = NO_HISTORY;
}

void CompanionDelayedReplies::forgetRequestRoute(BaseSerialInterface* route) {
  if (completed_login_route == route) clearCompletedLogin();
  if (deferred_login.route == route) clearDeferredLogin();
  if (telemetry_cache.route == route) telemetry_cache.route = nullptr;
}

bool CompanionDelayedReplies::loginReady(uint32_t now) const {
  return !login_interval_active
      || (uint32_t)(now - last_login_send_at) >= LOGIN_INTERVAL_MS;
}

void CompanionDelayedReplies::noteLoginSent(uint32_t now) {
  last_login_send_at = now;
  login_interval_active = true;
}

bool CompanionDelayedReplies::queueLogin(const uint8_t peer[32],
    const char* password, BaseSerialInterface* route, uint32_t now) {
  if (peer == nullptr || password == nullptr || route == nullptr) return false;
  if (hasDeferredLogin()) {
    // Duplicate clicks share the original deadline, rather than holding the
    // login indefinitely or replacing another host's queued operation.
    return deferred_login.route == route
        && memcmp(deferred_login.peer, peer, sizeof(deferred_login.peer)) == 0
        && strncmp(deferred_login.password, password,
                   sizeof(deferred_login.password) - 1) == 0;
  }
  deferred_login.route = route;
  deferred_login.deadline = now + LOGIN_HOLD_MS;
  memcpy(deferred_login.peer, peer, sizeof(deferred_login.peer));
  strncpy(deferred_login.password, password, sizeof(deferred_login.password) - 1);
  deferred_login.retry_history = matchesActiveLogin(peer, password, route, now)
      ? request.history : NO_HISTORY;
  return true;
}

void CompanionDelayedReplies::clearDeferredLogin() {
  memset(deferred_login.peer, 0, sizeof(deferred_login.peer));
  memset(deferred_login.password, 0, sizeof(deferred_login.password));
  deferred_login.route = nullptr;
  deferred_login.deadline = 0;
  deferred_login.retry_history = NO_HISTORY;
}

bool CompanionDelayedReplies::canRepeatCompletedLogin(const uint8_t peer[32],
    const char* password, BaseSerialInterface* route, uint32_t now) const {
  if (completed_login_history >= HISTORY_SIZE || peer == nullptr
      || password == nullptr || route == nullptr
      || route != completed_login_route) return false;
  const RequestGuard& held = requests[completed_login_history];
  return held.phase == AwaitAdmission && held.completed_login
      && !due(now, held.until) && memcmp(held.peer, peer, 32) == 0
      && strncmp(completed_login_password, password,
                 sizeof(completed_login_password) - 1) == 0;
}

void CompanionDelayedReplies::prune(uint32_t now) {
  for (unsigned i = 0; i < HISTORY_SIZE; ++i) {
    if (requests[i].phase == AwaitAdmission && due(now, requests[i].until)) {
      if (completed_login_history == i) clearCompletedLogin();
      requests[i].phase = Empty;
    }
    if (traces[i].phase == AwaitAdmission && due(now, traces[i].until)) {
      traces[i].phase = Empty;
    }
  }
}

bool CompanionDelayedReplies::reserveRequest(Kind kind, const uint8_t peer[32],
    BaseSerialInterface* route, bool terminal, uint32_t now, const char* password) {
  prune(now);
  if (hasRequest() || kind == None || kind == Trace || peer == nullptr
      || (!terminal && route == nullptr)) return false;
  const bool repeat_login = kind == Login && !terminal
      && canRepeatCompletedLogin(peer, password, route, now);
  uint8_t free = NO_HISTORY;
  for (unsigned i = 0; i < HISTORY_SIZE; ++i) {
    const RequestGuard& held = requests[i];
    if (held.phase == Empty) {
      free = i;
    } else if (kind == Login && held.login && memcmp(held.peer, peer, 32) == 0) {
      // A timed-out/cancelled login still has ambiguous replies in flight.
      // Completed logins may repeat only on their original authenticated
      // route with identical credentials; known old tags remain blocked.
      if (!held.completed_login || !repeat_login) return false;
    }
  }
  if (free == NO_HISTORY) return false;
  if (kind == Login) forgetTelemetry(peer, now);
  RequestGuard& held = requests[free];
  memcpy(held.peer, peer, 32);
  held.tag = 0;
  held.login = kind == Login;
  held.completed_login = false;
  held.phase = Reserved;
  request.route = route;
  request.kind = kind;
  request.history = free;
  request.terminal = terminal;
  request.repeat_login = repeat_login;
  request.radio_deadline = now;
  request.phase = Reserved;
  return true;
}

bool CompanionDelayedReplies::allowRequestTag(uint32_t tag) {
  if (request.phase != Reserved || request.history >= HISTORY_SIZE) return false;
  RequestGuard& held = requests[request.history];
  for (unsigned i = 0; i < HISTORY_SIZE; ++i) {
    if (i != request.history && requests[i].phase != Empty
        && requests[i].tag == tag && memcmp(requests[i].peer, held.peer, 32) == 0) {
      return false;
    }
  }
  held.tag = tag;
  return true;
}

void CompanionDelayedReplies::rememberLoginPassword(const char* password) {
  memset(login_password, 0, sizeof(login_password));
  login_password_valid = request.phase == Reserved && request.kind == Login
      && !request.terminal && password != nullptr;
  if (login_password_valid) {
    // BaseChatMesh sends at most 15 password bytes, including for rooms.
    strncpy(login_password, password, sizeof(login_password) - 1);
  }
}

bool CompanionDelayedReplies::matchesActiveLogin(const uint8_t peer[32],
    const char* password, BaseSerialInterface* route, uint32_t now) const {
  return login_password_valid && password != nullptr && peer != nullptr
      && route != nullptr && request.route == route && !request.terminal
      && request.kind == Login && request.phase >= AwaitRadio
      && !expired(request, now)
      && requestMatches(peer, 0)
      && strncmp(login_password, password, sizeof(login_password) - 1) == 0;
}

bool CompanionDelayedReplies::canRetryLogin(const uint8_t peer[32],
    const char* password, BaseSerialInterface* route, uint32_t now) const {
  return request.phase == AwaitRadio && !request.sent_pending
      && matchesActiveLogin(peer, password, route, now);
}

void CompanionDelayedReplies::rememberLoginTag(uint32_t tag) {
  if (request.kind == Login && request.history < HISTORY_SIZE) {
    requests[request.history].tag = tag;
  }
}

void CompanionDelayedReplies::armRequest(uint32_t tag, uint32_t timeout,
                                         bool flood, uint32_t now) {
  request.tag = tag;
  if (request.kind != Login && request.history < HISTORY_SIZE) {
    requests[request.history].tag = tag;
  }
  arm(request, timeout, flood, now);
}

void CompanionDelayedReplies::abandonRequest() {
  memset(login_password, 0, sizeof(login_password));
  login_password_valid = false;
  if (request.history < HISTORY_SIZE) {
    if (completed_login_history == request.history) clearCompletedLogin();
    requests[request.history].phase = Empty;
  }
  reset(request);
}

void CompanionDelayedReplies::retireRequest(uint32_t now) {
  memset(login_password, 0, sizeof(login_password));
  login_password_valid = false;
  if (request.phase == Reserved) {
    abandonRequest();
    return;
  }
  if (request.history < HISTORY_SIZE && requests[request.history].phase == Reserved) {
    RequestGuard& held = requests[request.history];
    held.until = retirementDeadline(now, request.radio_deadline,
        held.login ? LOGIN_QUARANTINE_MS : DELIVERY_GRACE_MS);
    held.phase = AwaitAdmission;
  }
  reset(request);
}

const uint8_t* CompanionDelayedReplies::requestPeer() const {
  return request.history < HISTORY_SIZE ? requests[request.history].peer : nullptr;
}

bool CompanionDelayedReplies::requestMatches(const uint8_t peer[32], uint32_t tag) const {
  const uint8_t* expected = requestPeer();
  return request.phase >= AwaitRadio && expected != nullptr
      && memcmp(expected, peer, 32) == 0
      && (request.kind == Login || request.tag == tag);
}

bool CompanionDelayedReplies::blocksResponse(const uint8_t peer[32],
                                             uint32_t tag, uint32_t now) const {
  // A tag-only match from another identity must not become a generic host reply.
  if (request.phase >= AwaitRadio && request.kind != Login && request.tag == tag
      && (!requestMatches(peer, tag) || request.phase == AwaitAdmission)) return true;
  for (unsigned i = 0; i < HISTORY_SIZE; ++i) {
    const RequestGuard& held = requests[i];
    if (held.phase == Empty || memcmp(held.peer, peer, 32) != 0) continue;
    if (held.phase == AwaitAdmission && due(now, held.until)) continue;
    if (i == request.history && request.phase == AwaitRadio) continue;
    if (held.tag == tag) return true;
    // A completed same-session re-login can receive a fresh server tag.
    // Outside that exact case, login's nonce-free envelope stays quarantined.
    if (held.login && !(held.completed_login && request.repeat_login
        && requestMatches(peer, tag))) return true;
  }
  return false;
}

bool CompanionDelayedReplies::storeRequest(const uint8_t* frame, size_t len, uint32_t now) {
  if (!store(request, frame, len, now)) return false;
  if (request.kind == Telemetry && request.history < HISTORY_SIZE && !request.terminal) {
    telemetry_cache.route = request.route;
    telemetry_cache.tag = request.tag;
    telemetry_cache.until = now + TELEMETRY_CACHE_MS;
    telemetry_cache.len = len;
    telemetry_cache.flood = request.flood;
    telemetry_cache.retryable = false;
    memcpy(telemetry_cache.peer, requestPeer(), sizeof(telemetry_cache.peer));
    memcpy(telemetry_cache.frame, frame, len);
  }
  // Keep the active login credential until final admission so queue pressure,
  // expiry or disconnect cannot turn a buffered reply into a completed login.
  return true;
}

void CompanionDelayedReplies::serviceRequest(BaseSerialInterface* serial, uint32_t now) {
  prune(now);
  if (completed_login_route != nullptr
      && (serial == nullptr || !serial->isReplyRouteAvailable(completed_login_route))) {
    clearCompletedLogin();
  }
  if (telemetry_cache.route != nullptr && (due(now, telemetry_cache.until)
      || serial == nullptr || !serial->isReplyRouteAvailable(telemetry_cache.route))) {
    telemetry_cache.route = nullptr;
  }
  bool delivered = false;
  if (service(request, serial, now, &delivered)) {
    if (request.kind == Telemetry && request.history < HISTORY_SIZE
        && !request.sent_pending && matchesTelemetryCache(requestPeer(), request.route, now)) {
      telemetry_cache.retryable = !delivered || due(now, request.client_deadline);
    }
    // 0x85 is PUSH_CODE_LOGIN_SUCCESS in the unchanged Companion protocol.
    // SENT admission and terminal output do not establish this host context.
    if (delivered && request.kind == Login && !request.terminal
        && request.frame_len >= 8 && request.frame[0] == 0x85
        && login_password_valid && request.history < HISTORY_SIZE) {
      clearCompletedLogin();
      completed_login_route = request.route;
      completed_login_history = request.history;
      memcpy(completed_login_password, login_password, sizeof(login_password));
      requests[request.history].completed_login = true;
    }
    retireRequest(now);
  }
}

bool CompanionDelayedReplies::matchesTelemetryCache(const uint8_t peer[32],
    BaseSerialInterface* route, uint32_t now) const {
  return route != nullptr && telemetry_cache.route == route
      && !due(now, telemetry_cache.until)
      && memcmp(telemetry_cache.peer, peer, sizeof(telemetry_cache.peer)) == 0;
}

bool CompanionDelayedReplies::retryTelemetry(const uint8_t peer[32], BaseSerialInterface* route,
    BaseSerialInterface* serial, uint32_t now) {
  if (route == nullptr || serial == nullptr || !serial->isReplyRouteAvailable(route)) return false;
  const bool cached = matchesTelemetryCache(peer, route, now);
  if (request.phase >= AwaitRadio && request.kind == Telemetry && request.route == route
      && !expired(request, now)
      && (request.history == NO_HISTORY ? cached : requestMatches(peer, request.tag))) {
    if (cached && !hasDeferredLogin() && request.phase == AwaitRadio && !request.sent_pending
        && due(now, request.client_deadline)) {
      // The latest over-the-air request missed the app's deadline. A recent
      // sample can satisfy the retry while the old radio tag is retired.
      retireRequest(now);
      telemetry_cache.retryable = true;
    } else {
      request.repeat_sent = !request.sent_pending;
      serviceRequest(serial, now);
      return true;
    }
  } else {
    serviceRequest(serial, now);
  }
  if (hasRequest() || hasDeferredLogin() || !telemetry_cache.retryable
      || !matchesTelemetryCache(peer, route, now)) return false;

  // Admit SENT and the legacy telemetry push in order, using the same bounded
  // transport queue as a fresh reply. A replay does not renew the sample age.
  request.kind = Telemetry;
  request.route = route;
  request.tag = telemetry_cache.tag;
  request.terminal = false;
  arm(request, telemetry_cache.until - now, telemetry_cache.flood, now);
  request.frame_len = telemetry_cache.len;
  memcpy(request.frame, telemetry_cache.frame, request.frame_len);
  request.sent_deadline = request.delivery_deadline = telemetry_cache.until;
  request.phase = AwaitAdmission;
  telemetry_cache.retryable = false;
  serviceRequest(serial, now);
  return true;
}

void CompanionDelayedReplies::forgetTelemetry(const uint8_t peer[32], uint32_t now) {
  const bool cached_peer = telemetry_cache.route != nullptr
      && memcmp(telemetry_cache.peer, peer, sizeof(telemetry_cache.peer)) == 0;
  if (request.kind == Telemetry
      && (request.history == NO_HISTORY ? cached_peer : requestMatches(peer, request.tag))) {
    retireRequest(now);
  }
  if (cached_peer) telemetry_cache.route = nullptr;
}

uint8_t CompanionDelayedReplies::reserveTrace(uint32_t tag, uint32_t auth, uint32_t now) {
  prune(now);
  uint8_t free = NO_HISTORY;
  for (unsigned i = 0; i < HISTORY_SIZE; ++i) {
    const TraceGuard& held = traces[i];
    if (held.phase == Empty) {
      free = i;
    } else if (held.tag == tag && held.auth == auth) {
      return NO_HISTORY;
    }
  }
  if (free != NO_HISTORY) {
    traces[free].tag = tag;
    traces[free].auth = auth;
    traces[free].phase = Reserved;
  }
  return free;
}

void CompanionDelayedReplies::abandonTrace(uint8_t history) {
  if (history < HISTORY_SIZE) traces[history].phase = Empty;
}

void CompanionDelayedReplies::retireTrace(uint8_t history, uint32_t now,
                                         uint32_t radio_deadline) {
  if (history < HISTORY_SIZE && traces[history].phase == Reserved) {
    traces[history].until = retirementDeadline(now, radio_deadline, DELIVERY_GRACE_MS);
    traces[history].phase = AwaitAdmission;
  }
}

bool CompanionDelayedReplies::reserveBinaryTrace(uint32_t tag, uint32_t auth,
    BaseSerialInterface* route, uint32_t now) {
  if (route == nullptr) return false;
  if (trace.phase >= AwaitRadio && expired(trace, now)) retireBinaryTrace(now);
  if (hasBinaryTrace() && trace.route != route) return false;
  // Reserve the replacement first. A duplicate/quarantined tuple or full
  // history must not cancel the current trace. Retired replies stay isolated.
  uint8_t history = reserveTrace(tag, auth, now);
  if (history == NO_HISTORY) return false;
  if (hasBinaryTrace()) retireBinaryTrace(now);
  trace.route = route;
  trace.tag = tag;
  trace.auth = auth;
  trace.kind = Trace;
  trace.history = history;
  trace.terminal = false;
  trace.phase = Reserved;
  return true;
}

void CompanionDelayedReplies::armBinaryTrace(uint32_t timeout, uint32_t now) {
  arm(trace, timeout, false, now);
}

void CompanionDelayedReplies::abandonBinaryTrace() {
  abandonTrace(trace.history);
  reset(trace);
}

void CompanionDelayedReplies::retireBinaryTrace(uint32_t now) {
  if (trace.phase == Reserved) {
    abandonBinaryTrace();
    return;
  }
  retireTrace(trace.history, now, trace.radio_deadline);
  reset(trace);
}

bool CompanionDelayedReplies::blocksTrace(uint32_t tag, uint32_t auth, uint32_t now) const {
  for (unsigned i = 0; i < HISTORY_SIZE; ++i) {
    if (traces[i].phase == AwaitAdmission && !due(now, traces[i].until)
        && traces[i].tag == tag && traces[i].auth == auth) return true;
  }
  return false;
}

bool CompanionDelayedReplies::storeBinaryTrace(const uint8_t* frame, size_t len, uint32_t now) {
  return store(trace, frame, len, now);
}

void CompanionDelayedReplies::serviceBinaryTrace(BaseSerialInterface* serial, uint32_t now) {
  prune(now);
  if (service(trace, serial, now)) retireBinaryTrace(now);
}

bool CompanionDelayedReplies::hasReplyForRoute(BaseSerialInterface* route, uint32_t now) const {
  const Reply* slots[] = { &request, &trace };
  for (const Reply* reply : slots) {
    if (reply->phase >= AwaitRadio && reply->route == route
        && !expired(*reply, now)) return true;
  }
  return false;
}

}  // namespace mesh
