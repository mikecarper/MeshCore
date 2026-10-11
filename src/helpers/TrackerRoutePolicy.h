#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {
namespace tracker {

static constexpr uint32_t kFloodIntervalSeconds = UINT32_C(21600);
static constexpr uint32_t kOutageCutoffSeconds = UINT32_C(345600);
static constexpr size_t kRouteStateImageSize = 32;
static constexpr uint8_t kOutageActive = 1;
static constexpr uint8_t kFloodLocked = 2;
static constexpr uint8_t kClockFault = 4;

// RTC seconds, never millis(). Zero denotes an unknown timestamp. Persist with
// encodeRouteState, rather than copying this compiler-dependent struct layout.
struct RouteState {
  uint32_t outage_since = 0;
  uint32_t last_target_reply = 0;
  uint32_t last_repeater = 0;
  uint32_t last_flood = 0;
  uint32_t latest_observed = 0;
  uint8_t flags = 0;
};

enum class RouteDecision : uint8_t {
  Direct, Wait, Flood, Locked, InvalidClock, InvalidState
};

namespace route_detail {
inline uint32_t crcByte(uint32_t crc, uint8_t value) {
  crc ^= value;
  for (uint8_t bit = 0; bit < 8; ++bit) {
    crc = (crc >> 1) ^ ((crc & 1) ? UINT32_C(0xedb88320) : 0);
  }
  return crc;
}
inline uint32_t crc(const uint8_t* data, size_t length) {
  uint32_t value = UINT32_C(0xffffffff);
  for (size_t index = 0; index < length; ++index) value = crcByte(value, data[index]);
  return value ^ UINT32_C(0xffffffff);
}
inline void put32(uint8_t* data, uint32_t value) {
  for (uint8_t index = 0; index < 4; ++index) data[index] = static_cast<uint8_t>(value >> (index * 8));
}
inline uint32_t get32(const uint8_t* data) {
  uint32_t value = 0;
  for (uint8_t index = 0; index < 4; ++index) value |= static_cast<uint32_t>(data[index]) << (index * 8);
  return value;
}
inline RouteState failClosed() {
  RouteState state;
  state.flags = kClockFault;
  return state;
}
} // namespace route_detail

inline bool isValidRouteState(const RouteState& state) {
  if (state.flags & ~(kOutageActive | kFloodLocked | kClockFault)) return false;
  const bool active = (state.flags & kOutageActive) != 0;
  if (active != (state.outage_since != 0) || ((state.flags & kFloodLocked) && !active)) return false;
  if (state.outage_since > state.latest_observed || state.last_target_reply > state.latest_observed
      || state.last_repeater > state.latest_observed || state.last_flood > state.latest_observed) return false;
  if (active && state.last_target_reply > state.outage_since) return false;
  return true;
}

namespace route_detail {
// Invalid input never wraps a timestamp or silently resets an outage. A clock
// fault is sticky until an authenticated target reply at a valid later time.
inline bool observe(const RouteState& current, uint64_t now, RouteState& candidate) {
  candidate = current;
  if (!isValidRouteState(current)) {
    candidate = failClosed();
    return false;
  }
  if (now == 0 || now > UINT32_MAX || now < current.latest_observed) {
    candidate.flags |= kClockFault;
    return false;
  }
  candidate.latest_observed = static_cast<uint32_t>(now);
  if ((candidate.flags & kOutageActive)
      && candidate.latest_observed - candidate.outage_since >= kOutageCutoffSeconds) {
    candidate.flags |= kFloodLocked;
  }
  return true;
}
inline void startOutage(RouteState& state) {
  if (!(state.flags & kOutageActive)) {
    state.flags |= kOutageActive;
    state.outage_since = state.latest_observed;
  }
}
inline bool elapsed(uint32_t now, uint32_t anchor, uint32_t interval) {
  return anchor == 0 || (now >= anchor && now - anchor >= interval);
}
} // namespace route_detail

// Planning does not authorize transmission. Persist every changed candidate,
// and reserve/persist a flood before transmitting it. During an outage, eligible
// recovery floods take precedence over a retained path that has stopped working.
inline RouteDecision planRoute(const RouteState& current, uint64_t now,
                               bool has_direct_path, RouteState& candidate) {
  if (!isValidRouteState(current)) {
    candidate = route_detail::failClosed();
    return RouteDecision::InvalidState;
  }
  if (!route_detail::observe(current, now, candidate)) return RouteDecision::InvalidClock;
  if (!has_direct_path) route_detail::startOutage(candidate);
  if (candidate.flags & kClockFault) return has_direct_path ? RouteDecision::Direct : RouteDecision::InvalidClock;
  if (candidate.flags & kFloodLocked) return has_direct_path ? RouteDecision::Direct : RouteDecision::Locked;
  if (candidate.flags & kOutageActive) {
    const uint32_t time = candidate.latest_observed;
    if (route_detail::elapsed(time, candidate.outage_since, kFloodIntervalSeconds)
        && route_detail::elapsed(time, candidate.last_repeater, kFloodIntervalSeconds)
        && route_detail::elapsed(time, candidate.last_flood, kFloodIntervalSeconds)) return RouteDecision::Flood;
  }
  return has_direct_path ? RouteDecision::Direct : RouteDecision::Wait;
}

inline bool noteFailure(const RouteState& current, uint64_t now, RouteState& candidate) {
  if (!route_detail::observe(current, now, candidate)) return false;
  route_detail::startOutage(candidate);
  return true;
}

inline bool noteRepeater(const RouteState& current, uint64_t now, RouteState& candidate) {
  if (!route_detail::observe(current, now, candidate)) return false;
  candidate.last_repeater = candidate.latest_observed;
  return true;
}

// The caller must first authenticate the configured target and establish that
// this is its direct DM response or matching direct ACK. Repeater adverts, room
// messages and unrelated ACKs cannot call this function.
inline bool noteAuthenticatedReply(const RouteState& current, uint64_t now, RouteState& candidate) {
  if (!route_detail::observe(current, now, candidate)) return false;
  candidate.last_target_reply = candidate.latest_observed;
  candidate.outage_since = 0;
  candidate.flags = 0;
  return true;
}

// Save this reservation successfully BEFORE sending. A failed save means no
// transmission; a reboot after saving conservatively spends this flood slot.
inline bool reserveFlood(const RouteState& current, uint64_t now, RouteState& reserved) {
  if (planRoute(current, now, false, reserved) != RouteDecision::Flood) return false;
  reserved.last_flood = reserved.latest_observed;
  return true;
}

inline bool encodeRouteState(const RouteState& state, uint8_t* image, size_t size) {
  if (!image || size != kRouteStateImageSize || !isValidRouteState(state)) return false;
  memset(image, 0, size);
  image[0] = 'T'; image[1] = 'R'; image[2] = 'P'; image[3] = 1;
  image[4] = state.flags;
  route_detail::put32(image + 8, state.outage_since);
  route_detail::put32(image + 12, state.last_target_reply);
  route_detail::put32(image + 16, state.last_repeater);
  route_detail::put32(image + 20, state.last_flood);
  route_detail::put32(image + 24, state.latest_observed);
  route_detail::put32(image + 28, route_detail::crc(image, 28));
  return true;
}

inline bool decodeRouteState(const uint8_t* image, size_t size, RouteState& state) {
  state = route_detail::failClosed();
  if (!image || size != kRouteStateImageSize || image[0] != 'T' || image[1] != 'R'
      || image[2] != 'P' || image[3] != 1 || image[5] || image[6] || image[7]
      || route_detail::get32(image + 28) != route_detail::crc(image, 28)) return false;
  RouteState decoded;
  decoded.flags = image[4];
  decoded.outage_since = route_detail::get32(image + 8);
  decoded.last_target_reply = route_detail::get32(image + 12);
  decoded.last_repeater = route_detail::get32(image + 16);
  decoded.last_flood = route_detail::get32(image + 20);
  decoded.latest_observed = route_detail::get32(image + 24);
  if (!isValidRouteState(decoded)) return false;
  state = decoded;
  return true;
}

} // namespace tracker
} // namespace mesh
