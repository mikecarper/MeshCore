// RAK collar mode. Companion keeps its normal Bluetooth and LoRa receive service.
#ifdef RAK_WISMESH_TAG
#include "MyMesh.h"

#if COMPANION_RAK_TRACKER
#include <helpers/TrackerSettingsCommand.h>
#include <math.h>

using namespace mesh::tracker;

static uint32_t nextCheck(uint32_t now, uint32_t interval) {
  return interval > UINT32_MAX - now ? UINT32_MAX : now + interval;
}

void MyMesh::beginDogTracker() {
  auto loaded = _store->isVolatilePrimaryFS() ? StoreResult::Unavailable
      : loadTracker(_store->getPrimaryFS(), dog);
  dog_storage_fault = loaded != StoreResult::Success && loaded != StoreResult::NotFound;
  if (dog_storage_fault) dog.enabled = false;
  dog_due = dog_radio_anchor = dog_rtc = getRTCClock()->getCurrentTime();
  dog_ms = millis();
  sensors.setTrackerGpsModeEnabled(dog.enabled);
}

bool MyMesh::saveDogTracker(TrackerRecord& candidate) {
  if (dog_storage_fault || _store->isVolatilePrimaryFS()
      || saveTracker(_store->getPrimaryFS(), candidate) != StoreResult::Success) {
    dog_storage_fault = true;
    cancelDogCheck();
    sensors.setTrackerGpsModeEnabled(false);
    return false;
  }
  dog = candidate;
  return true;
}

void MyMesh::cancelDogCheck(bool delivered) {
  if (dog_phase == DogPhase::Queued || dog_phase == DogPhase::Sent) {
    cancelActiveRetries(dog_packet_hash);
    uint8_t hash[MAX_HASH_SIZE];
    for (int i = _mgr->getOutboundTotal() - 1; i >= 0; --i) {
      _mgr->getOutboundByIdx(i)->calculatePacketHash(hash);
      if (memcmp(hash, dog_packet_hash, sizeof(hash)) != 0) continue;
      auto* item = _mgr->removeOutboundByIdx(i);
      cancelOutboundRadioRetry(item, delivered);
      releasePacket(item);
    }
    const auto* item = getOutboundInFlight();
    if (item) {
      item->calculatePacketHash(hash);
      if (memcmp(hash, dog_packet_hash, sizeof(hash)) == 0) cancelOutboundRadioRetry(item, delivered);
    }
  }
  dog_phase = DogPhase::Idle;
}

void MyMesh::dogHeardRepeater() {
  if (dog_storage_fault) return;
  TrackerRecord next = dog;
  noteRepeater(dog.route, getRTCClock()->getCurrentTime(), next.route);
  if (next.route.flags != dog.route.flags) saveDogTracker(next);
  else dog.route = next.route;
}

void MyMesh::dogSendResult(mesh::Packet* packet, bool sent) {
  if (!packet || dog_phase != DogPhase::Queued || packet->getPayloadType() != PAYLOAD_TYPE_REQ) return;
  uint8_t hash[MAX_HASH_SIZE];
  packet->calculatePacketHash(hash);
  if (memcmp(hash, dog_packet_hash, sizeof(hash)) != 0) return;
  if (sent) dog_phase = DogPhase::Sent;
  dog_deadline = futureMillis(sent ? kReplyWindowSeconds * 1000UL : 1);
}

bool MyMesh::dogResponse(mesh::Packet* packet, const uint8_t* secret, const uint8_t* body,
                         size_t size, const uint8_t* path, uint8_t path_len) {
  if (!packet || !secret || dog_phase != DogPhase::Sent || millisHasNowPassed(dog_deadline)) return false;
  uint8_t state;
  uint16_t lease;
  if (!parseStatusResponse(body, size, dog.last_request_tag, state, lease)) return false;
  auto* owner = lookupPersistentContactByPubKey(dog.owner, sizeof(dog.owner));
  if (!owner || owner->type != ADV_TYPE_CHAT
      || memcmp(owner->getSharedSecret(self_id), secret, PUB_KEY_SIZE) != 0) return false;
  if (path && (!mesh::Packet::isValidPathLen(path_len) || trackerPathByteLength(path_len) > sizeof(dog.path))) return false;
  const uint32_t now = getRTCClock()->getCurrentTime();
  TrackerRecord next = dog;
  auto route = planRoute(dog.route, now, true, next.route);
  if (route == RouteDecision::InvalidClock || route == RouteDecision::InvalidState) {
    if (next.route.flags != dog.route.flags) saveDogTracker(next);
    return false;
  }
  if (!(next.route.flags & kFloodLocked) || packet->isRouteDirect())
    noteAuthenticatedReply(next.route, now, next.route);
  if (state != Unknown) next.last_lost = state;
  if (path) {
    next.path_len = path_len;
    memset(next.path, 0, sizeof(next.path));
    memcpy(next.path, path, trackerPathByteLength(path_len));
  }
  if (!saveDogTracker(next)) return false;
  packet->markDoNotRetransmit();
  cancelDogCheck(true);
  dog_due = nextCheck(now, dog.last_lost == Lost ? dog.lost_interval : dog.normal_interval);
  return true;
}

void MyMesh::loopDogTracker() {
  if (!dog.enabled || dog_storage_fault) return;
  uint32_t now = getRTCClock()->getCurrentTime();
  uint32_t ms = millis();
  if (now > dog_rtc && now - dog_rtc > (ms - dog_ms) / 1000U + 2) dog_radio_anchor = now;
  dog_rtc = now;
  dog_ms = ms;
  if (!(dog.route.flags & kClockFault) && (!now || now < dog.route.latest_observed)) {
    TrackerRecord next = dog;
    planRoute(dog.route, now, true, next.route);
    if (!saveDogTracker(next)) return;
  }
  if (dog_phase == DogPhase::Queued || dog_phase == DogPhase::Sent) {
    if (!millisHasNowPassed(dog_deadline)) return;
    cancelDogCheck();
    TrackerRecord next = dog;
    noteFailure(dog.route, now, next.route);
    if (saveDogTracker(next))
      dog_due = nextCheck(now, dog.last_lost == Lost ? dog.lost_interval : dog.normal_interval);
    return;
  }
  if (dog_phase == DogPhase::Idle && now < dog_due) return;
  if (!_radio_available || board.isOTAUpdateRunning() || board.isRadioTestActive()
      || isAnyTempRadioActive() || hasPendingOtaApply() || isDualRadioActive()
#if COMPANION_FEATURE_TEMP_RADIO
      || _temp_radio_set_at || _temp_radio_revert_at
#endif
      || hasOutbound() || _mgr->getFreeCount() <= 2) return;
  auto* owner = lookupPersistentContactByPubKey(dog.owner, sizeof(dog.owner));
  if (!owner || owner->type != ADV_TYPE_CHAT) {
    dog_phase = DogPhase::Idle;
    dog_due = nextCheck(now, 60);
    return;
  }
  if (dog_phase == DogPhase::Idle) {
    sensors.beginTrackerGpsAcquisition();
    dog_phase = DogPhase::Gps;
  }
  if (sensors.isTrackerGpsAcquisitionPending()) return;
  dog_phase = DogPhase::Idle;
  dog_due = nextCheck(now, 60);
  StatusRequest request = {};
  double lat, lon;
  if (sensors.takeTrackerGpsPosition(lat, lon) && isfinite(lat) && isfinite(lon)
      && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
    request.flags = FreshGps;
    request.latitude = lround(lat * 1e7);
    request.longitude = lround(lon * 1e7);
  } else request.flags = GpsAttemptFailed;
  request.battery_mv = board.getBattMilliVolts();
  if (!now) { dog_due = 1; return; }

  TrackerRecord next = dog;
  if (dog_radio_anchor > next.route.last_repeater) next.route.last_repeater = dog_radio_anchor;
  if (next.route.last_repeater > next.route.latest_observed) next.route.latest_observed = next.route.last_repeater;
  auto route = planRoute(next.route, now, true, next.route);
  bool flood = route == RouteDecision::Flood;
  if (flood && !reserveFlood(next.route, now, next.route)) return;
  if (dog.last_request_tag == UINT32_MAX) return;
  uint32_t tag = getRTCClock()->getCurrentTimeUnique();
  if (tag <= dog.last_request_tag) tag = dog.last_request_tag + 1;
  if (uint64_t(tag) > uint64_t(now) + 300) return;
  next.last_request_tag = tag;
  if (!saveDogTracker(next)) return;

  uint8_t body[TagLength + RequestLength] = {};
  put32(body, tag);
  if (!makeStatusRequest(request, body + TagLength, RequestLength)) return;
  auto* packet = createDatagram(PAYLOAD_TYPE_REQ, owner->id, owner->getSharedSecret(self_id), body, sizeof(body));
  if (!packet) return;
  packet->tx_radio = mesh::RADIO_TX_PRIMARY;
  packet->flood_retry_policy = mesh::FLOOD_RETRY_POLICY_DENY;
  packet->calculatePacketHash(dog_packet_hash);
  dog_phase = DogPhase::Queued;
  dog_deadline = futureMillis(120000);
  bool queued = flood ? sendFloodScoped(*owner, packet)
                      : sendDirect(packet, dog.path, mesh::Packet::isValidPathLen(dog.path_len) ? dog.path_len : 0);
  if (!queued) cancelDogCheck();
}

bool MyMesh::dogCommand(const char* command, char* reply, size_t capacity) {
  if (!strcmp(command, "get tracker") || !strcmp(command, "get tracker status") || !strncmp(command, "get tracker.", 12))
    return formatQuery(command, dog, dog_storage_fault, reply, capacity);
  if (!strcmp(command, "tracker check")) {
    if (!dog.enabled || dog_storage_fault) snprintf(reply, capacity, "ERR: tracker unavailable");
    else if (dog_phase != DogPhase::Idle) snprintf(reply, capacity, "ERR: check already pending");
    else { dog_due = getRTCClock()->getCurrentTime(); snprintf(reply, capacity, "OK scheduled"); }
    return true;
  }
  if (strncmp(command, "set tracker.", 12)) return false;
  TrackerRecord next = dog;
  const char* problem = editSetting(command, dog, next, self_id.pub_key, sensors.isGPSDetected());
  if (!problem && !isValidTrackerRecord(next)) problem = "ERR: configure tracker.owner first";
  if (!problem && next.enabled) {
    auto* owner = lookupPersistentContactByPubKey(next.owner, sizeof(next.owner));
    if (!owner || owner->type != ADV_TYPE_CHAT) problem = "ERR: save owner Companion contact first";
    else {
      const uint8_t zero[PUB_KEY_SIZE] = {};
      if (!memcmp(owner->getSharedSecret(self_id), zero, sizeof(zero))) problem = "ERR: invalid owner key";
    }
  }
  if (!problem && !saveDogTracker(next)) problem = "ERR: tracker save failed";
  if (!problem) {
    cancelDogCheck();
    sensors.setTrackerGpsModeEnabled(false);
    sensors.setTrackerGpsModeEnabled(dog.enabled);
    dog_due = getRTCClock()->getCurrentTime();
  }
  snprintf(reply, capacity, "%s", problem ? problem : "OK");
  return true;
}
#endif
#endif
