#include "SensorMesh.h"
#include <helpers/CLICommandUtils.h>
#include <helpers/UsbLoggingWatchdog.h>
#include <math.h>

#if MESH_ENABLE_SENSOR_TRACKER

static uint32_t trackerFutureSeconds(uint32_t now, uint32_t interval) {
  return interval > UINT32_MAX - now ? UINT32_MAX : now + interval;
}

bool SensorMesh::saveTrackerRecord(mesh::tracker::TrackerRecord& candidate) {
  if (!tracker_storage_available || tracker_storage_fault
      || mesh::tracker::saveTracker(_fs, candidate) != mesh::tracker::StoreResult::Success) {
    tracker_storage_fault = true;
    return false;
  }
  tracker = candidate;
  return true;
}

void SensorMesh::configureTrackerRuntime() {
  sensors.setTrackerGpsModeEnabled(tracker.enabled);
  sensors.setPowerSavingEnabled(tracker.enabled || _prefs.powersaving_enabled != 0);
  if (tracker.enabled) {
    if (tracker_clock_millis == 0) {
      tracker_clock_rtc = getRTCClock()->getCurrentTime();
      tracker_clock_millis = futureMillis(0);
    }
    self_id.calcSharedSecret(tracker_secret, tracker.owner);
    if (tracker_next_check == 0) tracker_next_check = getRTCClock()->getCurrentTime();
    next_local_advert = next_flood_advert = 0;
    for (int i = 0; i < num_alert_tasks; ++i) alert_tasks[i]->text[0] = 0;
    num_alert_tasks = 0;
  } else {
    cancelTrackerPending();
    tracker_acquiring = false;
    tracker_awake_until = 0;
    tracker_next_check = 0;
    memset(tracker_secret, 0, sizeof(tracker_secret));
    wakeTrackerRadio();
    updateAdvertTimer();
    updateFloodAdvertTimer();
  }
}

void SensorMesh::wakeTrackerRadio() {
  if (!tracker_radio_asleep) return;
  // A failed warm wake must not leave the dispatcher's liveness recovery
  // disabled forever. Try a peripheral reset once, then return ownership to
  // the normal watchdog even when the hardware still reports failure.
  if (!_radio->setTrackerSleep(false)) _radio->recoverRadio(true);
  setRadioAvailable(true); // Also resets the dispatcher's RX watchdog anchors.
  tracker_radio_asleep = false;
}

void SensorMesh::cancelTrackerPending(bool delivered) {
  if (!tracker_pending) return;
  cancelActiveRetries(tracker_packet_hash);
  for (int i = _mgr->getOutboundTotal() - 1; i >= 0; --i) {
    const mesh::Packet* queued = _mgr->getOutboundByIdx(i);
    uint8_t hash[MAX_HASH_SIZE];
    queued->calculatePacketHash(hash);
    if (memcmp(hash, tracker_packet_hash, sizeof(hash)) != 0) continue;
    mesh::Packet* removed = _mgr->removeOutboundByIdx(i);
    cancelOutboundRadioRetry(removed, delivered);
    releasePacket(removed);
  }
  const mesh::Packet* in_flight = getOutboundInFlight();
  if (in_flight) {
    uint8_t hash[MAX_HASH_SIZE];
    in_flight->calculatePacketHash(hash);
    if (memcmp(hash, tracker_packet_hash, sizeof(hash)) == 0)
      cancelOutboundRadioRetry(in_flight, delivered);
  }
  tracker_pending = tracker_sent = false;
  tracker_pending_tag = 0;
  tracker_reply_until = 0;
}

bool SensorMesh::trackerNeedsRadio() const {
  if (!tracker.enabled || board.isUsbDataConnected() || board.isOTAUpdateRunning()
      || board.isRadioTestActive() || mesh::isUsbLoggingWatchdogArmed()
      || isAnyTempRadioActive() || hasPendingOtaApply() || isDualRadioActive()
      || set_radio_at || revert_radio_at || saved_radio_apply_pending
      || _cli.hasActiveUserGpioTimer() || hasOutbound() || _mgr->getOutboundTotal() != 0)
    return true;
  uint32_t delay;
  if (getNextQueueWakeDelay(delay) || getNextRetryWakeDelay(delay)) return true;
  if (tracker_pending || tracker_acquiring
      || (tracker_awake_until && !millisHasNowPassed(tracker_awake_until))) return true;
  return !tracker_storage_fault && getRTCClock()->getCurrentTime() >= tracker_next_check;
}

void SensorMesh::serviceTracker() {
  if (!tracker.enabled) { wakeTrackerRadio(); return; }
  const uint32_t now = getRTCClock()->getCurrentTime();
  const uint32_t now_ms = uint32_t(futureMillis(0));
  const uint32_t elapsed = uint32_t(now_ms - uint32_t(tracker_clock_millis)) / 1000;
  if (now > tracker_clock_rtc && uint64_t(now - tracker_clock_rtc) > uint64_t(elapsed) + 2) {
    // GPS/manual UTC correction cannot manufacture six hours of RF silence.
    // Refresh the conservative quiet anchor without forgiving an outage.
    tracker_boot_anchor = now;
  }
  tracker_clock_rtc = now;
  tracker_clock_millis = now_ms;
  // A transient clock rollback must remain visible even if the next automatic
  // check is hours away, or the radio is already asleep. Persist only this
  // exceptional transition; observing each idle second would wear the flash.
  if (!tracker_storage_fault && !(tracker.route.flags & mesh::tracker::kClockFault)
      && (now == 0 || now < tracker.route.latest_observed)) {
    mesh::tracker::TrackerRecord candidate = tracker;
    mesh::tracker::planRoute(tracker.route, now, true, candidate.route);
    saveTrackerRecord(candidate);
  }
  if (trackerNeedsRadio()) wakeTrackerRadio();
  if (tracker_radio_asleep) return;
  if (tracker_storage_fault) {
    cancelTrackerPending();
    tracker_acquiring = false;
  } else if (tracker_pending) {
    if (!millisHasNowPassed(tracker_reply_until)) return;
    cancelTrackerPending();
    mesh::tracker::TrackerRecord candidate = tracker;
    mesh::tracker::noteFailure(tracker.route, now, candidate.route);
    saveTrackerRecord(candidate);
    tracker_next_check = trackerFutureSeconds(now,
        tracker.last_lost == mesh::tracker::Lost ? tracker.lost_interval : tracker.normal_interval);
  } else if (!tracker_acquiring && now >= tracker_next_check) {
    if (board.isOTAUpdateRunning() || board.isRadioTestActive()
        || isAnyTempRadioActive() || hasPendingOtaApply() || isDualRadioActive()
        || set_radio_at || revert_radio_at || saved_radio_apply_pending
        || _mgr->getFreeCount() <= 2 || hasOutbound()) return;
    tracker_acquiring = true;
    sensors.beginTrackerGpsAcquisition();
  }
  if (tracker_acquiring) {
    // SensorManager owns the 120-second GPS/time-sync bound. It runs after
    // mesh in main, so finishing at our earlier deadline could miss its fix.
    if (sensors.isTrackerGpsAcquisitionPending()) return;
    tracker_acquiring = false;
    mesh::tracker::StatusRequest request;
    double lat, lon;
    if (sensors.takeTrackerGpsPosition(lat, lon) && isfinite(lat) && isfinite(lon)
        && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
      request.flags = mesh::tracker::FreshGps;
      request.latitude = (int32_t)lround(lat * 1e7);
      request.longitude = (int32_t)lround(lon * 1e7);
    } else request.flags = mesh::tracker::GpsAttemptFailed;
    request.battery_mv = board.getBattMilliVolts();

    // GPS gets its bounded chance to establish UTC, but an unset RTC cannot
    // allocate a durable request tag. This is a clock wait, not a storage fault.
    if (now == 0) {
      tracker_next_check = 1;
      return;
    }

    mesh::tracker::TrackerRecord candidate = tracker;
    // A boot conservatively opens a fresh six-hour RF wait. This covers an
    // advert heard immediately before power loss without per-packet writes.
    const uint32_t radio_anchor = tracker_boot_anchor > tracker.route.last_repeater
        ? tracker_boot_anchor : tracker.route.last_repeater;
    candidate.route.last_repeater = radio_anchor;
    if (candidate.route.latest_observed < radio_anchor)
      candidate.route.latest_observed = radio_anchor;
    const auto route = mesh::tracker::planRoute(candidate.route, now, true, candidate.route);
    const bool flood = route == mesh::tracker::RouteDecision::Flood;
    if (flood && !mesh::tracker::reserveFlood(candidate.route, now, candidate.route)) return;
    if (candidate.last_request_tag == UINT32_MAX) {
      tracker_storage_fault = true;
      return;
    }
    const uint32_t minimum_tag = candidate.last_request_tag + 1;
    candidate.last_request_tag = now > minimum_tag ? now : minimum_tag;
    if ((uint64_t)candidate.last_request_tag > (uint64_t)now + 300) {
      tracker_next_check = trackerFutureSeconds(now, 60);
      return;
    }
    if (!saveTrackerRecord(candidate)) return; // Reserve before any RF admission.

    uint8_t payload[4 + mesh::tracker::RequestLength] = {};
    tracker_pending_tag = tracker.last_request_tag;
    mesh::tracker::put32(payload, tracker_pending_tag);
    if (!mesh::tracker::makeStatusRequest(request, payload + 4, sizeof(payload) - 4)) return;
    mesh::Packet* packet = createDatagram(PAYLOAD_TYPE_REQ,
        mesh::Identity(tracker.owner), tracker_secret, payload, sizeof(payload));
    if (!packet) {
      tracker_next_check = trackerFutureSeconds(now, 60);
      return;
    }
    packet->tx_radio = mesh::RADIO_TX_PRIMARY; // One physical/profile copy.
    packet->flood_retry_policy = mesh::FLOOD_RETRY_POLICY_DENY;
    packet->calculatePacketHash(tracker_packet_hash);
    tracker_pending = true;
    tracker_sent = false;
    tracker_reply_until = futureMillis(120000); // Bound queued admission too.
    const bool queued = flood
        ? sendFloodScoped(default_scope, packet, 0, _prefs.path_hash_mode + 1)
        : sendDirect(packet, tracker.path,
            mesh::Packet::isValidPathLen(tracker.path_len) ? tracker.path_len : 0);
    if (!queued) {
      cancelTrackerPending();
      tracker_next_check = trackerFutureSeconds(now, 60);
    }
    return;
  }
  if (!trackerNeedsRadio() && !radio_driver.isWatchdogObserving()
      && !radio_driver.isCalibratingNoiseFloor() && _radio->setTrackerSleep(true)) {
    setRadioAvailable(false);
    tracker_radio_asleep = true;
  }
}

bool SensorMesh::handleTrackerResponse(mesh::Packet* packet, uint8_t type,
                                       const uint8_t* data, size_t length,
                                       const uint8_t* learned_path, uint8_t learned_path_len) {
  uint8_t status;
  uint16_t stayawake;
  if (!tracker.enabled || !tracker_pending || !tracker_sent
      || millisHasNowPassed(tracker_reply_until) || type != PAYLOAD_TYPE_RESPONSE
      || !mesh::tracker::parseStatusResponse(data, length, tracker_pending_tag, status, stayawake))
    return false;
  mesh::tracker::TrackerRecord candidate = tracker;
  const uint32_t now = getRTCClock()->getCurrentTime();
  const auto observed = mesh::tracker::planRoute(tracker.route, now, true, candidate.route);
  if (observed == mesh::tracker::RouteDecision::InvalidClock
      || observed == mesh::tracker::RouteDecision::InvalidState) {
    if (candidate.route.flags != tracker.route.flags) saveTrackerRecord(candidate);
    return false;
  }
  // The cutoff can become due while a recovery flood waits for its reply.
  // Evaluate it at arrival, then require a direct reply to clear that lock.
  const bool keep_cutoff = (candidate.route.flags & mesh::tracker::kFloodLocked)
      && !packet->isRouteDirect();
  if (!keep_cutoff && !mesh::tracker::noteAuthenticatedReply(candidate.route,
        now, candidate.route)) {
    if (candidate.route.flags != tracker.route.flags) saveTrackerRecord(candidate);
    return false;
  }
  if (learned_path != nullptr) {
    if (!mesh::Packet::isValidPathLen(learned_path_len)) return false;
    candidate.path_len = learned_path_len;
    memset(candidate.path, 0, sizeof(candidate.path));
    memcpy(candidate.path, learned_path, mesh::tracker::trackerPathByteLength(learned_path_len));
  }
  if (status != mesh::tracker::Unknown) candidate.last_lost = status;
  if (!saveTrackerRecord(candidate)) return false;
  packet->markDoNotRetransmit();
  cancelTrackerPending(true);
  if (status == mesh::tracker::Safe) tracker_awake_until = 0;
  else if (status == mesh::tracker::Lost) {
    if (stayawake > 300) stayawake = 300;
    tracker_awake_until = stayawake ? futureMillis((uint32_t)stayawake * 1000UL) : 0;
  }
  tracker_next_check = trackerFutureSeconds(getRTCClock()->getCurrentTime(),
      tracker.last_lost == mesh::tracker::Lost ? tracker.lost_interval : tracker.normal_interval);
  return true;
}

bool SensorMesh::handleTrackerPath(mesh::Packet* packet, uint8_t* path, uint8_t path_len,
                                   uint8_t extra_type, uint8_t* extra, size_t extra_len) {
  if (!path || !mesh::Packet::isValidPathLen(path_len)) return false;
  return handleTrackerResponse(packet, extra_type, extra, extra_len, path, path_len);
}

void SensorMesh::onSendComplete(mesh::Packet* packet) {
  mesh::Mesh::onSendComplete(packet);
  if (!tracker_pending || !packet || packet->getPayloadType() != PAYLOAD_TYPE_REQ) return;
  uint8_t hash[MAX_HASH_SIZE];
  packet->calculatePacketHash(hash);
  if (memcmp(hash, tracker_packet_hash, sizeof(hash)) == 0) {
    tracker_sent = true;
    tracker_reply_until = futureMillis(mesh::tracker::kReplyWindowSeconds * 1000UL);
  }
}

void SensorMesh::onSendFail(mesh::Packet* packet) {
  mesh::Mesh::onSendFail(packet);
  if (!tracker_pending || !packet) return;
  uint8_t hash[MAX_HASH_SIZE];
  packet->calculatePacketHash(hash);
  if (memcmp(hash, tracker_packet_hash, sizeof(hash)) == 0)
    tracker_reply_until = futureMillis(1);
}

bool SensorMesh::handleTrackerCommand(uint32_t sender_timestamp, const char* command, char* reply) {
  const bool getter = strcmp(command, "get tracker") == 0
      || strcmp(command, "get tracker status") == 0 || strncmp(command, "get tracker.", 12) == 0;
  const bool setter = strncmp(command, "set tracker.", 12) == 0;
  if (!getter && !setter && strcmp(command, "tracker check") != 0) return false;
  if (sender_timestamp != 0) { strcpy(reply, "ERR: tracker is local-only"); return true; }
  if (strcmp(command, "get tracker.owner") == 0) {
    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < 32; ++i) {
      reply[i * 2] = hex[tracker.owner[i] >> 4]; reply[i * 2 + 1] = hex[tracker.owner[i] & 15];
    }
    reply[64] = 0;
    return true;
  }
  if (getter) {
    if (strcmp(command, "get tracker.mode") == 0) {
      strcpy(reply, tracker.enabled ? "dog" : "off"); return true;
    }
    if (strcmp(command, "get tracker.interval") == 0
        || strcmp(command, "get tracker.lost.interval") == 0) {
      snprintf(reply, 157, "%lu", (unsigned long)(command[12] == 'l'
          ? tracker.lost_interval : tracker.normal_interval)); return true;
    }
    if (strcmp(command, "get tracker.path") == 0) {
      snprintf(reply, 157, "path=%s hops=%u hash.bytes=%u",
          tracker.path_len == mesh::tracker::kUnknownPath ? "unknown"
              : (tracker.path_len & 63) == 0 ? "direct" : "saved",
          tracker.path_len == mesh::tracker::kUnknownPath ? 0 : tracker.path_len & 63,
          tracker.path_len == mesh::tracker::kUnknownPath ? 0 : (tracker.path_len >> 6) + 1);
      return true;
    }
    if (strcmp(command, "get tracker") != 0 && strcmp(command, "get tracker status") != 0) {
      strcpy(reply, "ERR: unknown tracker setting"); return true;
    }
    snprintf(reply, 157, "mode=%s status=%s interval=%lu lost.interval=%lu flood=%s clock=%s storage=%s",
        tracker.enabled ? "dog" : "off", tracker.last_lost == 2 ? "yes" : tracker.last_lost == 1 ? "no" : "unknown",
        (unsigned long)tracker.normal_interval, (unsigned long)tracker.lost_interval,
        tracker.route.flags & mesh::tracker::kFloodLocked ? "locked" : "6h",
        tracker.route.flags & mesh::tracker::kClockFault ? "fault" : "ok",
        tracker_storage_fault ? "fault" : "ok");
    return true;
  }
  if (strcmp(command, "tracker check") == 0) {
    if (!tracker.enabled || tracker_storage_fault) strcpy(reply, "ERR: tracker unavailable");
    else if (tracker_pending || tracker_acquiring) strcpy(reply, "ERR: check already pending");
    else { tracker_next_check = getRTCClock()->getCurrentTime(); strcpy(reply, "OK scheduled"); }
    return true;
  }
  mesh::tracker::TrackerRecord candidate = tracker;
  bool changed_owner = false;
  if (strncmp(command, "set tracker.owner ", 18) == 0) {
    const char* key = command + 18;
    if (strlen(key) != 64) { strcpy(reply, "ERR: full owner key required"); return true; }
    uint8_t nonzero = 0;
    for (size_t i = 0; i < 64; ++i) {
      const char c = key[i];
      const int digit = c >= '0' && c <= '9' ? c - '0'
          : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
      if (digit < 0) { strcpy(reply, "ERR: invalid owner key"); return true; }
      if ((i & 1) == 0) candidate.owner[i / 2] = uint8_t(digit << 4);
      else { candidate.owner[i / 2] |= uint8_t(digit); nonzero |= candidate.owner[i / 2]; }
    }
    if (!nonzero || memcmp(candidate.owner, self_id.pub_key, PUB_KEY_SIZE) == 0) {
      strcpy(reply, "ERR: invalid owner key"); return true;
    }
    changed_owner = memcmp(candidate.owner, tracker.owner, PUB_KEY_SIZE) != 0;
    if (changed_owner) {
      candidate.path_len = mesh::tracker::kUnknownPath;
      memset(candidate.path, 0, sizeof(candidate.path));
      candidate.last_lost = mesh::tracker::Unknown;
    }
  } else if (strcmp(command, "set tracker.mode dog") == 0) {
    if (!sensors.isGPSDetected()) { strcpy(reply, "ERR: GPS required"); return true; }
    candidate.enabled = true;
  } else if (strcmp(command, "set tracker.mode off") == 0) candidate.enabled = false;
  else if (strncmp(command, "set tracker.interval ", 21) == 0
      || strncmp(command, "set tracker.lost.interval ", 26) == 0) {
    const bool lost = command[12] == 'l';
    int32_t seconds;
    if (!mesh::cli::parseIntegerStrict(command + (lost ? 26 : 21), seconds)
        || seconds < 60 || seconds > 86400) {
      strcpy(reply, "ERR: interval must be 60..86400 seconds"); return true;
    }
    if (lost) candidate.lost_interval = uint32_t(seconds);
    else candidate.normal_interval = uint32_t(seconds);
  } else if (strncmp(command, "set tracker.path ", 17) == 0) {
    mesh::cli::TerminalPath parsed;
    memset(candidate.path, 0, sizeof(candidate.path));
    if (mesh::cli::parseTerminalPath(command + 17, candidate.path, sizeof(candidate.path), 63, parsed)
        != mesh::cli::TerminalPathParseResult::Valid) {
      strcpy(reply, "ERR: invalid tracker path"); return true;
    }
    candidate.path_len = parsed.mode == mesh::cli::TerminalPathMode::Clear
        ? mesh::tracker::kUnknownPath : parsed.encoded_len;
  } else { strcpy(reply, "ERR: unknown tracker setting"); return true; }
  if (!mesh::tracker::isValidTrackerRecord(candidate)) {
    strcpy(reply, "ERR: configure tracker.owner first"); return true;
  }
  uint8_t secret[PUB_KEY_SIZE];
  self_id.calcSharedSecret(secret, candidate.owner);
  uint8_t secret_bits = 0;
  for (size_t i = 0; i < sizeof(secret); ++i) secret_bits |= secret[i];
  if (!secret_bits) { strcpy(reply, "ERR: invalid owner key"); return true; }
  if (!saveTrackerRecord(candidate)) { strcpy(reply, "ERR: tracker save failed"); return true; }
  cancelTrackerPending();
  tracker_acquiring = false;
  tracker_next_check = getRTCClock()->getCurrentTime();
  configureTrackerRuntime();
  strcpy(reply, "OK");
  return true;
}

#endif
