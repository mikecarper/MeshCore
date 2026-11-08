#include "CompanionDelayedReplies.h"
#include <string.h>

namespace mesh {

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
                                     BaseSerialInterface* serial, uint32_t now) {
  if (reply.phase == Empty || reply.phase == Reserved) return false;
  if (reply.terminal) return due(now, reply.radio_deadline);
  if (serial == nullptr || !serial->isReplyRouteAvailable(reply.route)
      || expired(reply, now)) return true;
  if (reply.sent_pending) {
    uint8_t sent[10];
    sent[0] = 6;  // RESP_CODE_SENT, unchanged Companion wire format.
    sent[1] = reply.flood ? 1 : 0;
    memcpy(sent + 2, &reply.tag, 4);
    memcpy(sent + 6, &reply.timeout, 4);
    if (serial->writeFrameToRoute(reply.route, sent, sizeof(sent)) != sizeof(sent)) {
      return false;
    }
    reply.sent_pending = false;
    reply.radio_deadline = now + radioBudget(reply);
  }
  return reply.phase == AwaitAdmission
      && serial->writeFrameToRoute(reply.route, reply.frame, reply.frame_len)
          == reply.frame_len;
}

void CompanionDelayedReplies::prune(uint32_t now) {
  for (unsigned i = 0; i < HISTORY_SIZE; ++i) {
    if (requests[i].phase == AwaitAdmission && due(now, requests[i].until)) {
      requests[i].phase = Empty;
    }
    if (traces[i].phase == AwaitAdmission && due(now, traces[i].until)) {
      traces[i].phase = Empty;
    }
  }
}

bool CompanionDelayedReplies::reserveRequest(Kind kind, const uint8_t peer[32],
    BaseSerialInterface* route, bool terminal, uint32_t now) {
  prune(now);
  if (hasRequest() || kind == None || kind == Trace || peer == nullptr
      || (!terminal && route == nullptr)) return false;
  uint8_t free = NO_HISTORY;
  for (unsigned i = 0; i < HISTORY_SIZE; ++i) {
    const RequestGuard& held = requests[i];
    if (held.phase == Empty) {
      free = i;
    } else if (kind == Login && held.login && memcmp(held.peer, peer, 32) == 0) {
      return false;
    }
  }
  if (free == NO_HISTORY) return false;
  RequestGuard& held = requests[free];
  memcpy(held.peer, peer, 32);
  held.tag = 0;
  held.login = kind == Login;
  held.phase = Reserved;
  request.route = route;
  request.kind = kind;
  request.history = free;
  request.terminal = terminal;
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

bool CompanionDelayedReplies::canRetryLogin(const uint8_t peer[32],
    const char* password, BaseSerialInterface* route, uint32_t now) const {
  return login_password_valid && password != nullptr && peer != nullptr
      && route != nullptr && request.route == route && !request.terminal
      && request.kind == Login && request.phase == AwaitRadio
      && !request.sent_pending && !expired(request, now)
      && requestMatches(peer, 0)
      && strncmp(login_password, password, sizeof(login_password) - 1) == 0;
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
  if (request.history < HISTORY_SIZE) requests[request.history].phase = Empty;
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
    if (held.login || held.tag == tag) return true;
  }
  return false;
}

bool CompanionDelayedReplies::storeRequest(const uint8_t* frame, size_t len, uint32_t now) {
  if (!store(request, frame, len, now)) return false;
  memset(login_password, 0, sizeof(login_password));
  login_password_valid = false;
  return true;
}

void CompanionDelayedReplies::serviceRequest(BaseSerialInterface* serial, uint32_t now) {
  prune(now);
  if (service(request, serial, now)) retireRequest(now);
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
