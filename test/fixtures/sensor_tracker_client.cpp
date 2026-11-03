#include "SensorMesh.h"
#include "Tracker.cpp"
@PEER_METHODS@
using namespace mesh::tracker;
using mesh::Packet;

static unsigned scenarios = 0;
static std::array<uint8_t, 32> ownerKey(uint8_t suffix = 1) {
  std::array<uint8_t, 32> key{};
  for (size_t i = 0; i < key.size(); ++i) key[i] = uint8_t(i + 11);
  key[31] = suffix; return key;
}
static std::string hexKey(const std::array<uint8_t, 32>& key) {
  static const char hex[] = "0123456789abcdef"; std::string value;
  for (uint8_t byte : key) { value += hex[byte >> 4]; value += hex[byte & 15]; }
  return value;
}
static std::string command(SensorMesh& mesh, const std::string& value, uint32_t timestamp = 0) {
  char answer[160]; memset(answer, 0xa5, sizeof(answer));
  assert(mesh.handleTrackerCommand(timestamp, value.c_str(), answer + 1));
  assert(answer[0] == char(0xa5) && answer[158] == char(0xa5) && answer[159] == char(0xa5));
  return answer + 1;
}
static void configure(SensorMesh& mesh) {
  assert(command(mesh, "set tracker.owner " + hexKey(ownerKey())) == "OK");
  assert(command(mesh, "set tracker.mode dog") == "OK");
  assert(mesh.tracker.enabled && mesh.tracker.generation == 2);
  assert(sensors.mode && sensors.power_save && mesh.num_alert_tasks == 0);
  assert(mesh.alert1.text[0] == 0 && mesh.alert2.text[0] == 0);
  assert(!mesh.next_local_advert && !mesh.next_flood_advert);
}
static Packet* queueCheck(SensorMesh& mesh, bool gps = true, bool foreground = false) {
  const auto service = [&]() { if (foreground) mesh.loop(); else mesh.serviceTracker(); };
  service();
  assert(mesh.tracker_acquiring && !mesh.tracker_pending && sensors.pending);
  if (gps) sensors.fix(); else { sensors.pending = false; sensors.fresh = false; }
  service();
  assert(!mesh.tracker_acquiring && mesh.tracker_pending && !mesh.tracker_sent);
  assert(mesh.pool.queued.size() == 1);
  assert(mesh.last_payload.size() == 4 + RequestLength);
  assert(get32(mesh.last_payload.data()) == mesh.tracker_pending_tag);
  StatusRequest request;
  assert(parseStatusRequest(mesh.last_payload.data() + 4, RequestLength, request));
  assert(request.flags == (gps ? FreshGps : GpsAttemptFailed));
  assert(request.battery_mv == 3765);
  if (gps) assert(request.latitude == 471234567 && request.longitude == -1222345678);
  else assert(!request.latitude && !request.longitude);
  assert(memcmp(mesh.last_dest, mesh.tracker.owner, 32) == 0);
  assert(memcmp(mesh.last_secret, mesh.tracker_secret, 32) == 0);
  return mesh.pool.queued.front();
}
static void transmitted(SensorMesh& mesh) {
  assert(mesh.pool.queued.size() == 1 && mesh.tracker_pending);
  Packet* packet = mesh.pool.removeOutboundByIdx(0);
  mesh.pool.in_flight = packet;
  const auto tx_services = mesh._cli.tx_services, rx_services = mesh._cli.rx_services;
  const auto tuning_services = mesh._cli.profiles.tuning_services;
  const auto primary_services = mesh.primary_radio_services;
  mesh.onSendComplete(packet);
  assert(mesh._cli.tx_services == tx_services + 1 && mesh._cli.rx_services == rx_services + 1);
  assert(mesh._cli.profiles.tuning_services == tuning_services + 1);
  assert(mesh.primary_radio_services == primary_services + 1);
  assert(mesh.tracker_sent && mesh.tracker_reply_until == uint32_t(mesh.now_ms + 20000));
  // The dispatcher owns and frees the original packet after this callback.
  mesh.pool.in_flight = nullptr; mesh.releasePacket(packet);
}
static void savedClient(SensorMesh& mesh, const std::array<uint8_t, 32>& key) {
  ClientInfo client; client.id = mesh::Identity(key.data());
  mesh.self_id.calcSharedSecret(client.shared_secret, key.data()); mesh.acl.clients.push_back(client);
}
static bool authenticated(SensorMesh& mesh, const std::array<uint8_t, 32>& key,
                          Packet& packet, uint8_t* response, size_t length,
                          uint8_t* path = nullptr, uint8_t path_length = 0) {
  uint8_t authenticated_secret[32]; mesh.self_id.calcSharedSecret(authenticated_secret, key.data());
  const int count = mesh.searchPeersByHash(key.data());
  for (int slot = 0; slot < count; ++slot) {
    uint8_t candidate_secret[32]; memset(candidate_secret, 0xa5, sizeof(candidate_secret));
    mesh.getPeerSharedSecret(candidate_secret, slot);
    // Radio adapter calls the actual peer callback only for the full secret
    // which authenticated the packet, as Mesh's encrypted receive loop does.
    if (memcmp(candidate_secret, authenticated_secret, 32) != 0) continue;
    if (path) mesh.onPeerPathRecv(&packet, slot, candidate_secret, path, path_length,
                                 PAYLOAD_TYPE_RESPONSE, response, uint8_t(length));
    else mesh.onPeerDataRecv(&packet, PAYLOAD_TYPE_RESPONSE, slot, candidate_secret, response, length);
    return true;
  }
  return false;
}
static bool response(SensorMesh& mesh, uint8_t state, uint16_t awake = 0,
                     uint32_t tag = 0, bool padded = true, uint8_t* path = nullptr,
                     uint8_t path_length = 0, bool direct = true) {
  uint8_t bytes[16] = {};
  assert(makeStatusResponse(tag ? tag : mesh.tracker_pending_tag, state, awake, bytes, sizeof(bytes)) == 9);
  Packet packet;
  packet.header = (PAYLOAD_TYPE_RESPONSE << PH_TYPE_SHIFT) | (direct ? ROUTE_TYPE_DIRECT : ROUTE_TYPE_FLOOD);
  assert(authenticated(mesh, ownerKey(), packet, bytes, padded ? 16 : 9, path, path_length));
  return packet.isMarkedDoNotRetransmit();
}
static TrackerRecord restored(SensorMesh& mesh) {
  TrackerRecord record; assert(loadTracker(&mesh.fs, record) == StoreResult::Success); return record;
}

static void commands() {
  SensorMesh mesh;
  assert(command(mesh, "get tracker").find("mode=off status=unknown interval=1800 lost.interval=60") == 0);
  assert(command(mesh, "tracker check") == "ERR: tracker unavailable");
  assert(command(mesh, "set tracker.mode dog").find("configure tracker.owner") != std::string::npos);
  assert(command(mesh, "set tracker.owner " + std::string(64, '0')) == "ERR: invalid owner key");
  auto self = ownerKey(); memcpy(self.data(), mesh.self_id.pub_key, 32);
  assert(command(mesh, "set tracker.owner " + hexKey(self)) == "ERR: invalid owner key");
  for (const std::string& invalid : {std::string(63, 'a'), std::string(65, 'a'), std::string(64, 'g')})
    assert(command(mesh, "set tracker.owner " + invalid).find("ERR:") == 0);
  assert(!mesh.tracker.generation && !mesh.fs.write_opens);
  configure(mesh);
  assert(command(mesh, "get tracker.owner") == hexKey(ownerKey()));
  assert(command(mesh, "set tracker.mode other") == "ERR: unknown tracker setting");
  assert(command(mesh, "set tracker.interval") == "ERR: unknown tracker setting");
  const auto before = mesh.tracker; const auto writes = mesh.fs.write_opens;
  for (const char* value : {"get tracker", "get tracker.owner", "tracker check", "set tracker.mode off", "set tracker.interval 60"})
    assert(command(mesh, value, mesh.rtc.now) == "ERR: tracker is local-only");
  assert(mesh.tracker.generation == before.generation && mesh.fs.write_opens == writes && mesh.tracker.enabled);
  for (const std::string& setting : {std::string("interval"), std::string("lost.interval")}) {
    for (const char* invalid : {"59", "86401", "-1", "60x", "2147483648", "", "60 61"})
      assert(command(mesh, "set tracker." + setting + " " + invalid).find("ERR:") == 0);
    assert(command(mesh, "set tracker." + setting + " 86400") == "OK");
    assert(command(mesh, "set tracker." + setting + " 60") == "OK");
  }
  assert(mesh.tracker.normal_interval == 60 && mesh.tracker.lost_interval == 60);
  assert(command(mesh, "set tracker.path aabb,ccdd") == "OK");
  assert(mesh.tracker.path_len == 66 && mesh.tracker.path[0] == 0xaa && mesh.tracker.path[3] == 0xdd);
  auto routed = restored(mesh); assert(routed.path_len == 66);
  for (const char* invalid : {"aaaa,bb", "a", "aabbccdd", "", "clear x"})
    assert(command(mesh, std::string("set tracker.path ") + invalid) == "ERR: invalid tracker path");
  assert(command(mesh, "set tracker.path direct") == "OK" && mesh.tracker.path_len == 0);
  assert(command(mesh, "set tracker.path clear") == "OK" && mesh.tracker.path_len == kUnknownPath);
  assert(command(mesh, "set tracker.owner " + hexKey(ownerKey(2))) == "OK");
  assert(mesh.tracker.path_len == kUnknownPath && !mesh.tracker.last_lost);
  sensors.detected = false;
  assert(command(mesh, "set tracker.mode dog") == "ERR: GPS required");
  assert(command(mesh, "set tracker.mode off") == "OK" && !mesh.tracker.enabled && !sensors.mode);
  assert(!mesh.tracker_next_check && mesh.advert_timer_updates && mesh.flood_timer_updates);
  char unused[160] = {}; assert(!mesh.handleTrackerCommand(0, "other", unused));
  ++scenarios;
}

static void cadence() {
  SensorMesh mesh; configure(mesh); queueCheck(mesh); transmitted(mesh);
  assert(response(mesh, Safe));
  assert(!mesh.tracker_pending && mesh.tracker.last_lost == Safe && !mesh.tracker_awake_until);
  assert(mesh.tracker_next_check == mesh.rtc.now + 1800 && restored(mesh).last_lost == Safe);
  assert(mesh.tracker.route.last_target_reply == mesh.rtc.now && !mesh.tracker.route.flags);
  mesh.serviceTracker(); assert(mesh.tracker_radio_asleep && !mesh.available && !mesh.trackerNeedsRadio());
  mesh.rtc.now += 1799; mesh.now_ms += 1799000; mesh.serviceTracker();
  assert(mesh.tracker_radio_asleep && mesh.created == 1);
  ++mesh.rtc.now; mesh.now_ms += 1000;
  queueCheck(mesh); assert(!mesh.tracker_radio_asleep && mesh.radio.wakes == 1);
  transmitted(mesh); assert(response(mesh, Lost, 120, 0, false));
  assert(mesh.tracker_next_check == mesh.rtc.now + 60 && mesh.tracker_awake_until == mesh.now_ms + 120000);
  assert(restored(mesh).last_lost == Lost && mesh.trackerNeedsRadio());
  mesh.rtc.now += 59; mesh.now_ms += 59000; mesh.serviceTracker(); assert(mesh.created == 2);
  ++mesh.rtc.now; mesh.now_ms += 1000; queueCheck(mesh); transmitted(mesh);
  assert(response(mesh, Unknown));
  assert(mesh.tracker.last_lost == Lost && mesh.tracker_next_check == mesh.rtc.now + 60);
  assert(mesh.tracker_awake_until != 0); // Unknown never silently declares the dog safe.
  mesh.rtc.now += 60; mesh.now_ms += 60000; queueCheck(mesh); transmitted(mesh);
  assert(response(mesh, Lost, 65535));
  assert(mesh.tracker_awake_until == mesh.now_ms + 300000);
  mesh.rtc.now += 60; mesh.now_ms += 60000; queueCheck(mesh); transmitted(mesh);
  assert(response(mesh, Safe, 65535));
  assert(!mesh.tracker_awake_until && mesh.tracker_next_check == mesh.rtc.now + 1800);
  ++scenarios;
}

static void awakeExpiry() {
  for (uint32_t started : {100U, UINT32_MAX - 60000U}) {
    for (unsigned path = 0; path < 4; ++path) {
      SensorMesh mesh; mesh.now_ms = started; configure(mesh);
      assert(command(mesh, "set tracker.lost.interval 86400") == "OK");
      queueCheck(mesh, true, true); transmitted(mesh); assert(response(mesh, Lost, 120));
      const auto before = mesh.tracker;
      const auto writes = mesh.fs.write_opens;
      const uint32_t deadline = uint32_t(started + 120000U);
      mesh.now_ms = deadline; mesh.rtc.now += 120;
      mesh.loop();
      assert(mesh.tracker_awake_until == deadline && mesh.trackerNeedsRadio());
      assert(!mesh.tracker_radio_asleep); // Production expiry is strictly > 0.
      if (path == 1) {
        mesh.tracker_radio_asleep = mesh.radio.sleeping = true; mesh.available = false;
      } else if (path == 2) mesh.radio.morse_tx = true;
      else if (path == 3) mesh.radio.morse_rx = true;
      const auto wakes = mesh.radio.wakes, loops = mesh._clock_sync.loops;
      const auto cli_loops = mesh._cli.loops, base_loops = mesh.base_loops;
      ++mesh.now_ms; mesh.loop();
      assert(mesh._cli.loops == cli_loops + 1 && mesh.base_loops == base_loops + 1);
      assert(!mesh.tracker_awake_until && "observed expiry must retire the tracker awake lease");
      assert(mesh.trackerNeedsRadio() == (path >= 2) && mesh.radio.wakes == wakes);
      assert(mesh.tracker.last_lost == Lost && mesh.tracker.generation == before.generation);
      assert(mesh.tracker_next_check == mesh.rtc.now - 120 + 86400);
      assert(mesh.fs.write_opens == writes && !mesh.tracker_pending && !mesh.tracker_acquiring);
      if (path < 2) assert(mesh.tracker_radio_asleep && !mesh.available);
      else assert(mesh._clock_sync.loops == loops); // Still defers optional Morse work.
      // Observation of expiry, not a half-cycle without servicing, is the
      // supported guarantee. A retained deadline used to become future again.
      mesh.now_ms += 0x80000000U; mesh.loop();
      assert(mesh.trackerNeedsRadio() == (path >= 2) && !mesh.tracker_awake_until);
      assert(mesh.radio.wakes == wakes && mesh.tracker.last_lost == Lost);
      mesh.radio.morse_tx = mesh.radio.morse_rx = false; mesh.loop();
      assert(!mesh.trackerNeedsRadio() && mesh.tracker_radio_asleep);
      ++scenarios;
    }
  }
}

static void awakeOwners() {
  SensorMesh mesh; configure(mesh);
  assert(command(mesh, "set tracker.lost.interval 86400") == "OK");
  queueCheck(mesh, true, true); transmitted(mesh); assert(response(mesh, Lost, 120));
  mesh.now_ms += 120001; mesh.rtc.now += 120; mesh.loop();
  assert(!mesh.tracker_awake_until && mesh.tracker_radio_asleep);
  const auto next = mesh.tracker_next_check;
  // The actual local-command lease runs before the following foreground pass.
  mesh.localConsoleLease(1); assert(!mesh.tracker_awake_until && mesh.tracker_radio_asleep);
  mesh.localConsoleLease(0); mesh.loop();
  assert(mesh.tracker_awake_until == uint32_t(mesh.now_ms + 120000));
  assert(mesh.trackerNeedsRadio() && !mesh.tracker_radio_asleep && mesh.tracker_next_check == next);
  // Renewal during CLI service must survive retirement of the preceding lease.
  mesh.now_ms += 120001; mesh.rtc.now += 120; mesh._cli.local_command = true; mesh.loop();
  assert(mesh.tracker_awake_until == uint32_t(mesh.now_ms + 120000) && mesh.trackerNeedsRadio());
  bool* owners[] = {&mesh.board.usb, &mesh.board.ota, &mesh.board.test, &mesh.temporary,
    &mesh.pending_ota, &mesh.dual, &mesh.saved_radio_apply_pending, &mesh._cli.active,
    &mesh.queue_wake, &mesh.retry_wake, &mesh::test_logging_watchdog};
  for (bool* owner : owners) {
    *owner = true; mesh.tracker_awake_until = mesh.now_ms - 1; mesh.loop();
    assert(!mesh.tracker_awake_until && mesh.trackerNeedsRadio() && !mesh.tracker_radio_asleep);
    assert(mesh.tracker.last_lost == Lost && mesh.tracker_next_check == next);
    *owner = false;
  }
  for (bool pending : {false, true}) {
    mesh.tracker_next_check = mesh.rtc.now; mesh.loop();
    assert(mesh.tracker_acquiring && sensors.pending);
    if (pending) { sensors.fix(); mesh.loop(); transmitted(mesh); }
    const auto tag = mesh.tracker_pending_tag;
    mesh.tracker_awake_until = mesh.now_ms - 1; mesh.loop();
    assert(!mesh.tracker_awake_until && mesh.trackerNeedsRadio() && !mesh.tracker_radio_asleep);
    assert(mesh.tracker_pending == pending && mesh.tracker_acquiring == !pending);
    assert(mesh.tracker_pending_tag == tag && mesh.tracker.last_lost == Lost);
    if (pending) assert(response(mesh, Lost, 300));
    else { mesh.tracker_acquiring = false; sensors.pending = false; }
  }
  mesh.loop(); assert(mesh.tracker_awake_until == uint32_t(mesh.now_ms + 300000));
  ++scenarios;
}

static void awakeLongCadence() {
  for (unsigned stayawake : {0U, 120U}) for (bool unknown : {false, true}) {
    SensorMesh mesh; configure(mesh);
    assert(command(mesh, "set tracker.lost.interval 86400") == "OK");
    const uint32_t origin = mesh.rtc.now; uint64_t elapsed_ms = 0;
    const auto advance = [&](uint64_t delta) {
      elapsed_ms += delta; mesh.now_ms = uint32_t(100 + elapsed_ms);
      mesh.rtc.now = origin + uint32_t(elapsed_ms / 1000);
    };
    queueCheck(mesh, true, true); transmitted(mesh); assert(response(mesh, Lost, stayawake));
    advance(120001); mesh.loop();
    assert(!mesh.trackerNeedsRadio() && mesh.tracker_radio_asleep);
    for (unsigned day = 1; day <= 26; ++day) {
      const uint64_t due_ms = uint64_t(mesh.tracker_next_check - origin) * 1000;
      advance(due_ms - elapsed_ms); queueCheck(mesh, true, true); transmitted(mesh);
      if (unknown && day == 1) assert(response(mesh, Unknown));
      else { advance(20001); mesh.loop(); }
      assert(!mesh.tracker_pending && !mesh.tracker_acquiring && mesh.tracker.last_lost == Lost);
      mesh.loop();
      assert(!mesh.trackerNeedsRadio() && mesh.tracker_radio_asleep
          && "expired tracker awake lease must not revive after signed half-wrap");
    }
    ++scenarios;
  }
}

static void timing() {
  { SensorMesh mesh; configure(mesh); queueCheck(mesh);
    assert(!response(mesh, Lost, 120)); // A reply cannot start before the send callback.
    mesh.now_ms += 100000; mesh.serviceTracker();
    assert(mesh.tracker_pending && mesh.created == 1 && !mesh.tracker.route.outage_since);
    transmitted(mesh); mesh.now_ms += 19999; mesh.serviceTracker(); assert(mesh.tracker_pending);
    mesh.now_ms += 2; mesh.rtc.now += 120;
    assert(!response(mesh, Safe)); mesh.serviceTracker();
    assert(!mesh.tracker_pending && mesh.tracker.route.outage_since == mesh.rtc.now);
    assert(mesh.tracker_next_check == mesh.rtc.now + 1800 && mesh.flood_sends == 0); }
  { SensorMesh mesh; configure(mesh); queueCheck(mesh); mesh.now_ms += 120001;
    mesh.serviceTracker(); assert(!mesh.tracker_pending && mesh.pool.queued.empty() && mesh.pool.frees == 1);
    assert(mesh.tracker.route.flags & kOutageActive); }
  { SensorMesh mesh; configure(mesh); mesh.now_ms = UINT32_MAX - 10000;
    queueCheck(mesh); transmitted(mesh); mesh.now_ms += 19000;
    assert(response(mesh, Safe)); }
  ++scenarios;
}

static void identityAndNonce() {
  SensorMesh mesh; configure(mesh); queueCheck(mesh); transmitted(mesh);
  auto collision = ownerKey(2); savedClient(mesh, collision);
  uint8_t bytes[16] = {}; assert(makeStatusResponse(mesh.tracker_pending_tag, Lost, 120, bytes, sizeof(bytes)) == 9);
  Packet wrong_peer; assert(authenticated(mesh, collision, wrong_peer, bytes, sizeof(bytes)));
  assert(mesh.tracker_pending && mesh.tracker.last_lost == Unknown && mesh.ordinary_data == 1);
  auto unknown = ownerKey(3); Packet unknown_peer;
  assert(!authenticated(mesh, unknown, unknown_peer, bytes, sizeof(bytes)) && mesh.tracker_pending);
  assert(!response(mesh, Safe, 0, mesh.tracker_pending_tag + 1));
  bytes[15] = 1; Packet wrong_tail;
  assert(authenticated(mesh, ownerKey(), wrong_tail, bytes, sizeof(bytes)) && mesh.tracker_pending);
  bytes[15] = 0; bytes[5] = 2; Packet wrong_version;
  assert(authenticated(mesh, ownerKey(), wrong_version, bytes, sizeof(bytes)) && mesh.tracker_pending);
  Packet unrelated; unrelated.header = PAYLOAD_TYPE_REQ << PH_TYPE_SHIFT;
  unrelated.payload[0] = 1; unrelated.payload_len = 1;
  const auto deadline = mesh.tracker_reply_until; mesh.now_ms += 1000;
  mesh.onSendComplete(&unrelated); mesh.onSendFail(&unrelated);
  assert(mesh.tracker_reply_until == deadline);
  assert(response(mesh, Safe));
  assert(!response(mesh, Lost, 120, 1000000)); // Repeated and unsolicited replies have no authority.
  savedClient(mesh, ownerKey());
  mesh.rtc.now += 1800; mesh.now_ms += 1800000; queueCheck(mesh); transmitted(mesh);
  makeStatusResponse(mesh.tracker_pending_tag + 1, Safe, 0, bytes, sizeof(bytes));
  Packet ordinary; assert(authenticated(mesh, ownerKey(), ordinary, bytes, sizeof(bytes)));
  assert(mesh.ordinary_data == 2 && mesh.tracker_pending); // Saved owner ordinary traffic keeps ACL routing.
  assert(response(mesh, Safe));
  uint8_t secret[32]; memset(secret, 0x55, sizeof(secret)); mesh.getPeerSharedSecret(secret, -1);
  for (auto value : secret) assert(value == 0);
  ++scenarios;
}

static void paths() {
  SensorMesh mesh; configure(mesh); queueCheck(mesh); transmitted(mesh);
  uint8_t route[] = {0xaa, 0xbb, 0xcc, 0xdd};
  const auto generation = mesh.tracker.generation;
  assert(!response(mesh, Safe, 0, mesh.tracker_pending_tag + 1, true, route, 66));
  assert(mesh.tracker.path_len == kUnknownPath && mesh.tracker.generation == generation);
  assert(!response(mesh, Safe, 0, 0, true, route, 192));
  assert(mesh.tracker.path_len == kUnknownPath);
  auto collision = ownerKey(2); savedClient(mesh, collision);
  uint8_t bytes[16] = {}; makeStatusResponse(mesh.tracker_pending_tag, Safe, 0, bytes, sizeof(bytes));
  Packet wrong_peer; assert(authenticated(mesh, collision, wrong_peer, bytes, sizeof(bytes), route, 66));
  assert(mesh.tracker.path_len == kUnknownPath && mesh.ordinary_paths == 1);
  assert(response(mesh, Safe, 0, 0, true, route, 66));
  auto durable = restored(mesh); assert(durable.path_len == 66 && memcmp(durable.path, route, 4) == 0);
  mesh.rtc.now += 1800; mesh.now_ms += 1800000; auto* next = queueCheck(mesh);
  assert(next->path_len == 66 && memcmp(next->path, route, 4) == 0); transmitted(mesh);
  const auto prior = restored(mesh); --mesh.rtc.now;
  uint8_t different[] = {1, 2};
  assert(!response(mesh, Lost, 120, 0, true, different, 2));
  const auto after = restored(mesh);
  assert(after.path_len == prior.path_len && memcmp(after.path, prior.path, 64) == 0);
  assert(mesh.tracker_pending && !mesh.tracker_awake_until);
  ++scenarios;
}

static void suppressedNormalTraffic() {
  SensorMesh mesh; Packet packet; packet.header = ROUTE_TYPE_FLOOD;
  assert(mesh.allowPacketForward(&packet)); mesh.sendSelfAdvertisement(0, true);
  assert(mesh.adverts == 1);
  configure(mesh);
  assert(!mesh.allowPacketForward(&packet));
  mesh.sendSelfAdvertisement(0, true); mesh.sendSelfAdvertisement(0, false);
  assert(mesh.adverts == 1 && mesh.zero_hops == 0 && mesh._clock_sync.observed == 1);
  queueCheck(mesh); assert(mesh.direct_sends == 1 && mesh.flood_sends == 0 && mesh.created == 1);
  assert(mesh.num_alert_tasks == 0); transmitted(mesh); assert(response(mesh, Safe));
  mesh.rtc.now += 1800; mesh.now_ms += 1800000; queueCheck(mesh);
  assert(mesh.direct_sends == 2 && mesh.flood_sends == 0);
  assert(command(mesh, "set tracker.mode off") == "OK");
  assert(mesh.pool.queued.empty() && !mesh.tracker_pending && mesh.allowPacketForward(&packet));
  mesh.sendSelfAdvertisement(0, false); assert(mesh.adverts == 2 && mesh.zero_hops == 1);
  ++scenarios;
}

static void outage(SensorMesh& mesh, uint32_t now) {
  auto candidate = mesh.tracker;
  assert(noteFailure(candidate.route, mesh.rtc.now, candidate.route));
  assert(mesh.saveTrackerRecord(candidate));
  mesh.now_ms += uint32_t((uint64_t(now) - mesh.rtc.now) * 1000);
  mesh.rtc.now = now; mesh.tracker_next_check = now;
}
static void floods() {
  SensorMesh mesh; configure(mesh); const auto start = mesh.rtc.now;
  outage(mesh, start + kFloodIntervalSeconds - 1); queueCheck(mesh); transmitted(mesh);
  assert(mesh.flood_sends == 0 && mesh.direct_sends == 1);
  mesh.now_ms += 20001; mesh.serviceTracker();
  mesh.rtc.now = start + kFloodIntervalSeconds; mesh.tracker_next_check = mesh.rtc.now;
  queueCheck(mesh); assert(mesh.flood_sends == 1 && restored(mesh).route.last_flood == mesh.rtc.now);
  transmitted(mesh); mesh.now_ms += 20001; mesh.serviceTracker();
  mesh.rtc.now += 60; mesh.now_ms += 60000; mesh.tracker_next_check = mesh.rtc.now; queueCheck(mesh);
  assert(mesh.flood_sends == 1 && mesh.direct_sends == 2); transmitted(mesh);
  mesh.now_ms += 20001; mesh.serviceTracker();
  mesh.now_ms += uint32_t((uint64_t(start + 2 * kFloodIntervalSeconds) - mesh.rtc.now) * 1000);
  mesh.rtc.now = start + 2 * kFloodIntervalSeconds;
  assert(noteRepeater(mesh.tracker.route, mesh.rtc.now, mesh.tracker.route));
  mesh.tracker_next_check = mesh.rtc.now; queueCheck(mesh); assert(mesh.flood_sends == 1);
  transmitted(mesh); mesh.now_ms += 20001; mesh.serviceTracker();
  mesh.rtc.now += kFloodIntervalSeconds; mesh.now_ms += kFloodIntervalSeconds * 1000;
  mesh.tracker_next_check = mesh.rtc.now;
  queueCheck(mesh); assert(mesh.flood_sends == 2); transmitted(mesh);
  mesh.now_ms += 20001; mesh.serviceTracker();
  mesh.now_ms += uint32_t((uint64_t(start + kOutageCutoffSeconds) - mesh.rtc.now) * 1000);
  mesh.rtc.now = start + kOutageCutoffSeconds; mesh.tracker_next_check = mesh.rtc.now;
  queueCheck(mesh); assert(mesh.flood_sends == 2 && mesh.tracker.route.flags & kFloodLocked);
  assert(restored(mesh).route.flags & kFloodLocked); transmitted(mesh);
  assert(response(mesh, Lost, 120, 0, true, nullptr, 0, false));
  assert(mesh.tracker.route.flags & kFloodLocked && restored(mesh).route.flags & kFloodLocked);
  assert(mesh.tracker_next_check == mesh.rtc.now + 60 && mesh.tracker.last_lost == Lost);
  mesh.rtc.now += 60; mesh.now_ms += 60000; queueCheck(mesh);
  assert(mesh.flood_sends == 2); transmitted(mesh);
  assert(response(mesh, Safe) && !mesh.tracker.route.flags && !restored(mesh).route.flags);
  ++scenarios;
}

static void durability() {
  { SensorMesh mesh; configure(mesh); const auto start = mesh.rtc.now;
    outage(mesh, start + kFloodIntervalSeconds);
    const auto before = restored(mesh); mesh.fs.faults.insert("rename:/tracker.tmp:/tracker.1");
    mesh.serviceTracker(); sensors.fix(); mesh.serviceTracker();
    assert(mesh.tracker_storage_fault && !mesh.tracker_pending && mesh.pool.allocated.empty());
    assert(!mesh.created && !mesh.flood_sends && !mesh.direct_sends);
    mesh.fs.reset(); const auto after = restored(mesh);
    assert(after.generation == before.generation && after.route.last_flood == before.route.last_flood);
    mesh.rtc.now += kFloodIntervalSeconds; mesh.serviceTracker(); assert(!mesh.created); }
  { SensorMesh mesh; configure(mesh); outage(mesh, mesh.rtc.now + kFloodIntervalSeconds);
    mesh.allocation_failure = true; mesh.serviceTracker(); sensors.fix(); mesh.serviceTracker();
    assert(!mesh.tracker_pending && mesh.pool.allocated.empty() && !mesh.flood_sends);
    auto durable = restored(mesh); assert(durable.route.last_flood == mesh.rtc.now);
    mesh.tracker = durable; mesh.allocation_failure = false;
    mesh.rtc.now += 60; mesh.now_ms += 60000; mesh.tracker_next_check = mesh.rtc.now; queueCheck(mesh);
    assert(!mesh.flood_sends && mesh.direct_sends == 1); }
  { SensorMesh mesh; configure(mesh); queueCheck(mesh); transmitted(mesh);
    const auto before = restored(mesh); mesh.fs.faults.insert("open:w:/tracker.tmp");
    assert(!response(mesh, Lost, 120));
    assert(mesh.tracker_pending && mesh.tracker_storage_fault && !mesh.tracker_awake_until);
    assert(mesh.tracker.last_lost == Unknown);
    mesh.serviceTracker(); assert(!mesh.tracker_pending && mesh.created == 1);
    mesh.fs.reset(); assert(restored(mesh).generation == before.generation); }
  { SensorMesh mesh; configure(mesh); auto candidate = mesh.tracker;
    assert(noteFailure(candidate.route, mesh.rtc.now, candidate.route)); assert(mesh.saveTrackerRecord(candidate));
    mesh.rtc.now -= 1; mesh.tracker_next_check = mesh.rtc.now; queueCheck(mesh);
    // Fail closed on flood; a retained direct probe is permitted.
    assert(mesh.flood_sends == 0 && mesh.tracker.route.flags & kClockFault); }
  ++scenarios;
}

static void ownership() {
  { SensorMesh mesh; configure(mesh); auto* original = queueCheck(mesh);
    auto* duplicate = mesh.pool.alloc(); *duplicate = *original; mesh.pool.queued.push_back(duplicate);
    auto* unrelated = mesh.pool.alloc(); unrelated->payload[0] = 9; unrelated->payload_len = 1;
    mesh.pool.queued.push_back(unrelated);
    mesh.cancelTrackerPending();
    assert(mesh.pool.queued.size() == 1 && mesh.pool.queued.front() == unrelated);
    assert(mesh.pool.frees == 2 && mesh.cancel_queued == 2 && mesh.cancel_active == 1);
    assert(!mesh.cancel_delivered && !mesh.tracker_pending && !mesh.tracker_pending_tag);
    mesh.cancelTrackerPending(); assert(mesh.pool.frees == 2);
    mesh.pool.removeOutboundByIdx(0); mesh.releasePacket(unrelated); }
  { SensorMesh mesh; configure(mesh); auto* packet = queueCheck(mesh);
    mesh.pool.removeOutboundByIdx(0); mesh.pool.in_flight = packet;
    mesh.cancelTrackerPending(true);
    assert(mesh.cancel_inflight == 1 && mesh.cancel_delivered && !mesh.pool.frees);
    mesh.onSendComplete(packet); assert(!mesh.tracker_pending && !mesh.tracker_sent);
    mesh.pool.in_flight = nullptr; mesh.releasePacket(packet); assert(mesh.pool.frees == 1); }
  { SensorMesh mesh; configure(mesh); auto* packet = queueCheck(mesh);
    mesh.pool.removeOutboundByIdx(0); mesh.pool.in_flight = packet; mesh.onSendComplete(packet);
    assert(response(mesh, Safe));
    assert(mesh.cancel_inflight == 1 && mesh.cancel_delivered && !mesh.pool.frees);
    assert(mesh.pool.allocated.count(packet) && !mesh.tracker_pending);
    mesh.onSendFail(packet); assert(!mesh.tracker_pending && !mesh.tracker_reply_until);
    mesh.pool.in_flight = nullptr; mesh.releasePacket(packet); assert(mesh.pool.frees == 1); }
  { SensorMesh mesh; configure(mesh); mesh.admission_failure = true;
    mesh.serviceTracker(); sensors.fix(); mesh.serviceTracker();
    assert(!mesh.tracker_pending && mesh.pool.allocated.empty() && mesh.pool.frees == 1);
    assert(mesh.base_send_fail == 1 && mesh.tracker_next_check == mesh.rtc.now + 60); }
  { SensorMesh mesh; configure(mesh); queueCheck(mesh); transmitted(mesh);
    assert(command(mesh, "set tracker.mode off") == "OK");
    assert(!mesh.tracker_pending && !mesh.tracker_sent && !mesh.tracker_pending_tag && !mesh.tracker_awake_until); }
  ++scenarios;
}

static void rebootAndNonces() {
  { SensorMesh prior; configure(prior); queueCheck(prior); transmitted(prior);
    const auto old_tag = prior.tracker_pending_tag;
    assert(old_tag == prior.rtc.now && restored(prior).last_request_tag == old_tag);
    SensorMesh reboot; reboot.fs.files = prior.fs.files;
    assert(loadTracker(&reboot.fs, reboot.tracker) == StoreResult::Success);
    reboot.rtc.now = prior.rtc.now; reboot.tracker_boot_anchor = reboot.rtc.now;
    reboot.configureTrackerRuntime(); queueCheck(reboot); transmitted(reboot);
    assert(reboot.tracker_pending_tag == old_tag + 1 && restored(reboot).last_request_tag == old_tag + 1);
    assert(!response(reboot, Lost, 120, old_tag) && reboot.tracker_pending);
    assert(response(reboot, Safe) && reboot.tracker.last_lost == Safe); }
  { SensorMesh prior; configure(prior); outage(prior, prior.rtc.now + kFloodIntervalSeconds);
    queueCheck(prior); transmitted(prior); assert(prior.flood_sends == 1);
    SensorMesh reboot; reboot.fs.files = prior.fs.files;
    assert(loadTracker(&reboot.fs, reboot.tracker) == StoreResult::Success);
    reboot.rtc.now = prior.rtc.now + kFloodIntervalSeconds;
    reboot.tracker_boot_anchor = reboot.rtc.now; reboot.configureTrackerRuntime();
    queueCheck(reboot); assert(reboot.flood_sends == 0 && reboot.direct_sends == 1);
    assert(reboot.tracker.route.last_flood == prior.rtc.now);
    assert(reboot.tracker.route.last_repeater == reboot.rtc.now); }
  { SensorMesh mesh; configure(mesh); auto candidate = mesh.tracker;
    candidate.route.latest_observed = mesh.rtc.now;
    candidate.last_request_tag = mesh.rtc.now + 300; assert(mesh.saveTrackerRecord(candidate));
    mesh.serviceTracker(); sensors.fix(); mesh.serviceTracker();
    assert(!mesh.tracker_pending && !mesh.created && !mesh.tracker_storage_fault);
    assert(mesh.tracker_next_check == mesh.rtc.now + 60);
    mesh.rtc.now += 60; mesh.now_ms += 60000;
    queueCheck(mesh); assert(mesh.tracker_pending_tag <= mesh.rtc.now + 300); }
  { SensorMesh mesh; configure(mesh); mesh.rtc.now = UINT32_MAX;
    mesh.tracker_boot_anchor = UINT32_MAX; auto candidate = mesh.tracker;
    candidate.route.latest_observed = UINT32_MAX; candidate.last_request_tag = UINT32_MAX;
    assert(mesh.saveTrackerRecord(candidate)); mesh.tracker_next_check = UINT32_MAX;
    mesh.serviceTracker(); sensors.fix(); mesh.serviceTracker();
    assert(mesh.tracker_storage_fault && !mesh.created && !mesh.tracker_pending); }
  ++scenarios;
}

static void idleClockAndCutoffCrossing() {
  { SensorMesh mesh; configure(mesh); queueCheck(mesh); transmitted(mesh); assert(response(mesh, Safe));
    const uint32_t accepted = mesh.rtc.now;
    mesh.serviceTracker(); assert(mesh.tracker_radio_asleep);
    mesh.rtc.now -= 60; ++mesh.now_ms; mesh.serviceTracker();
    assert(mesh.tracker.route.flags & kClockFault && restored(mesh).route.flags & kClockFault);
    assert(mesh.tracker_next_check == mesh.rtc.now && mesh.tracker_acquiring && mesh.created == 1);
    sensors.fix(); mesh.serviceTracker();
    assert(mesh.tracker.route.flags & kClockFault && !mesh.flood_sends && mesh.direct_sends == 2);
    transmitted(mesh);
    mesh.rtc.now = accepted + 60;
    assert(response(mesh, Safe) && !mesh.tracker.route.flags && !restored(mesh).route.flags); }
  for (bool direct : {false, true}) {
    SensorMesh mesh; configure(mesh); const uint32_t start = mesh.rtc.now;
    outage(mesh, start + kOutageCutoffSeconds - 5); queueCheck(mesh);
    assert(mesh.flood_sends == 1 && !(mesh.tracker.route.flags & kFloodLocked)); transmitted(mesh);
    mesh.rtc.now += 5; mesh.now_ms += 5000;
    assert(response(mesh, Safe, 0, 0, true, nullptr, 0, direct));
    if (direct) assert(!mesh.tracker.route.flags && !restored(mesh).route.flags);
    else {
      assert(mesh.tracker.route.flags & kFloodLocked && restored(mesh).route.flags & kFloodLocked);
      assert(mesh.tracker.route.outage_since == start);
      mesh.rtc.now += 1800; mesh.now_ms += 1800000; queueCheck(mesh);
      assert(mesh.flood_sends == 1); transmitted(mesh);
      assert(response(mesh, Safe) && !mesh.tracker.route.flags && !restored(mesh).route.flags);
    }
  }
  ++scenarios;
}

static void wakeRecovery() {
  for (bool recovery : {false, true}) {
    SensorMesh mesh; configure(mesh); queueCheck(mesh); transmitted(mesh); assert(response(mesh, Safe));
    mesh.serviceTracker(); assert(mesh.tracker_radio_asleep && !mesh.available && mesh.radio.sleeping);
    mesh.radio.permit_wake = false; mesh.radio.permit_recovery = recovery;
    mesh.board.usb = true; // Keeps the dispatcher live for its ordinary hardware watchdog.
    mesh.serviceTracker();
    assert(mesh.radio.wake_attempts == 1 && mesh.radio.hard_recoveries == 1);
    assert(!mesh.tracker_radio_asleep && mesh.available && mesh.radio.sleeping == !recovery);
    for (unsigned repeat = 0; repeat < 5; ++repeat) {
      mesh.wakeTrackerRadio(); mesh.serviceTracker();
      assert(!mesh.tracker_radio_asleep && mesh.available);
      assert(mesh.radio.wake_attempts == 1 && mesh.radio.hard_recoveries == 1);
    }
    assert(mesh.created == 1 && !mesh.tracker_pending);
  }
  { SensorMesh mesh; configure(mesh); queueCheck(mesh); transmitted(mesh); assert(response(mesh, Safe));
    mesh.serviceTracker(); mesh.board.usb = true; mesh.serviceTracker();
    assert(mesh.radio.wake_attempts == 1 && mesh.radio.wakes == 1 && !mesh.radio.hard_recoveries);
    assert(!mesh.tracker_radio_asleep && mesh.available); }
  ++scenarios;
}

static void zeroClockAcquisition() {
  SensorMesh mesh; mesh.rtc.now = 0; mesh.tracker_boot_anchor = 0; configure(mesh);
  mesh.serviceTracker(); assert(mesh.tracker_acquiring && sensors.pending && sensors.starts == 1);
  assert(!mesh.created && !mesh.tracker_pending && !mesh.tracker_storage_fault);
  sensors.fix(); mesh.serviceTracker();
  assert(!mesh.created && !mesh.tracker_pending && !mesh.tracker_storage_fault);
  assert(!mesh.tracker.last_request_tag && !restored(mesh).last_request_tag);
  for (unsigned repeat = 0; repeat < 5; ++repeat) {
    mesh.now_ms += 1000; mesh.serviceTracker();
    assert(!mesh.created && !mesh.tracker_pending && !mesh.tracker_storage_fault);
  }
  // A GPS/RTC sync makes the next due acquisition eligible for a direct probe.
  mesh.rtc.now = 1000000; mesh.serviceTracker();
  assert(mesh.tracker_acquiring && !mesh.tracker_pending);
  sensors.fix(); mesh.serviceTracker();
  assert(mesh.tracker_pending && mesh.created == 1 && mesh.direct_sends == 1 && !mesh.flood_sends);
  assert(!mesh.tracker_storage_fault && mesh.tracker_pending_tag == mesh.rtc.now);
  transmitted(mesh); assert(response(mesh, Safe));
  assert(!mesh.tracker.route.flags && mesh.tracker_next_check == mesh.rtc.now + 1800);
  ++scenarios;
}

static void forwardClockCorrection() {
  SensorMesh mesh; configure(mesh); const uint32_t start = mesh.rtc.now;
  auto candidate = mesh.tracker;
  assert(noteFailure(candidate.route, start, candidate.route));
  candidate.route.last_flood = start - kFloodIntervalSeconds;
  assert(mesh.saveTrackerRecord(candidate));
  const auto prior_flood = candidate.route.last_flood;
  mesh.serviceTracker(); assert(mesh.tracker_acquiring && sensors.pending && !mesh.created);
  mesh.now_ms += 1000; mesh.rtc.now += kFloodIntervalSeconds;
  const uint32_t corrected = mesh.rtc.now;
  sensors.fix(); mesh.serviceTracker();
  assert(mesh.tracker_pending && mesh.direct_sends == 1 && !mesh.flood_sends);
  assert(mesh.tracker_boot_anchor == corrected && mesh.tracker.route.last_repeater == corrected);
  assert(mesh.tracker.route.outage_since == start && mesh.tracker.route.last_flood == prior_flood);
  assert(restored(mesh).route.outage_since == start && restored(mesh).route.last_flood == prior_flood);
  transmitted(mesh); mesh.now_ms += 20001; mesh.serviceTracker();
  mesh.rtc.now += kFloodIntervalSeconds;
  mesh.now_ms += kFloodIntervalSeconds * 1000;
  queueCheck(mesh); assert(mesh.flood_sends == 1 && mesh.direct_sends == 1);
  assert(restored(mesh).route.last_flood == corrected + kFloodIntervalSeconds);
  assert(mesh.tracker.route.outage_since == start);
  transmitted(mesh); mesh.now_ms += 20001; mesh.serviceTracker();
  mesh.rtc.now += 60; mesh.now_ms += 60000; mesh.tracker_next_check = mesh.rtc.now;
  queueCheck(mesh); assert(mesh.flood_sends == 1 && mesh.direct_sends == 2);
  ++scenarios;
}

static void managerOwnsAcquisitionHandoff() {
  SensorMesh mesh; configure(mesh); mesh.serviceTracker();
  assert(mesh.tracker_acquiring && sensors.pending);
  sensors.hold_window = true;
  mesh.now_ms += 120001; mesh.rtc.now += 120; mesh.serviceTracker();
  assert(mesh.tracker_acquiring && sensors.pending && !mesh.created && !mesh.tracker_pending);
  // The manager can hand off its final fresh fix on the following main-loop
  // turn; Tracker may not have consumed an earlier empty result.
  sensors.fix(); mesh.serviceTracker();
  assert(!mesh.tracker_acquiring && mesh.tracker_pending && mesh.created == 1);
  StatusRequest request; assert(parseStatusRequest(mesh.last_payload.data() + 4, RequestLength, request));
  assert(request.flags == FreshGps && request.latitude == 471234567 && request.longitude == -1222345678);
  ++scenarios;
}

static void gpsAndMaintenance() {
  for (const auto& coordinates : {std::pair<double,double>{NAN, 0}, {0, INFINITY}, {91, 0}, {0, -181}}) {
    SensorMesh mesh; configure(mesh); mesh.serviceTracker();
    sensors.fix(coordinates.first, coordinates.second); mesh.serviceTracker();
    StatusRequest request;
    assert(parseStatusRequest(mesh.last_payload.data() + 4, RequestLength, request));
    assert(request.flags == GpsAttemptFailed && !request.latitude && !request.longitude);
  }
  { SensorMesh mesh; configure(mesh); mesh.serviceTracker(); mesh.now_ms += 119999;
    mesh.serviceTracker(); assert(!mesh.created && mesh.tracker_acquiring);
    mesh.now_ms += 2; mesh.serviceTracker(); assert(mesh.created == 1 && mesh.tracker_pending);
    StatusRequest request; assert(parseStatusRequest(mesh.last_payload.data() + 4, RequestLength, request));
    assert(request.flags == GpsAttemptFailed); }
  { SensorMesh mesh; configure(mesh); queueCheck(mesh); transmitted(mesh); assert(response(mesh, Safe));
    bool* inhibitors[] = {&mesh.board.usb, &mesh.board.ota, &mesh.board.test, &mesh.temporary,
      &mesh.pending_ota, &mesh.dual, &mesh.saved_radio_apply_pending, &mesh._cli.active,
      &mesh._cli.pending_radio_settings,
      &mesh.queue_wake, &mesh.retry_wake, &mesh::test_logging_watchdog};
    for (bool* condition : inhibitors) {
      *condition = true; assert(mesh.trackerNeedsRadio()); mesh.serviceTracker();
      assert(!mesh.tracker_radio_asleep); *condition = false;
    }
    mesh.radio_driver.watchdog = true; mesh.serviceTracker(); assert(!mesh.tracker_radio_asleep);
    mesh.radio_driver.watchdog = false; mesh.radio_driver.calibrating = true;
    mesh.serviceTracker(); assert(!mesh.tracker_radio_asleep); mesh.radio_driver.calibrating = false;
    mesh.serviceTracker(); assert(mesh.tracker_radio_asleep);
    mesh.board.usb = true; mesh.serviceTracker(); assert(!mesh.tracker_radio_asleep && mesh.available);
    assert(mesh.created == 1); }
  ++scenarios;
}

static void geofenceScenario() {
  using mesh::tracker::geofence::Result;
  SensorMesh mesh;
  assert(command(mesh, "get tracker.box") == "off");
  assert(command(mesh, "get tracker.fence").find("box=off last-known=unknown last-acquisition=none") == 0);
  assert(!mesh.isTrackerFindMyEscalated());
  for (const char* invalid : {"", "47,-123,46,-122", "-91,-123,48,-122", "47,-181,48,-122",
                              "47.12345678,-123,48,-122", "47,-123,48,-122junk"}) {
    assert(command(mesh, std::string("set tracker.box ") + invalid).find("ERR:") == 0);
  }
  assert(command(mesh, "set tracker.box 47,-123,48,-122") == "OK - box set; location unknown");
  assert(command(mesh, "get tracker.box") == "47.0000000,-123.0000000,48.0000000,-122.0000000");
  assert(command(mesh, "set tracker.box clear", mesh.rtc.now) == "ERR: tracker is local-only");
  configure(mesh);

  queueCheck(mesh); // Fresh inside fix.
  assert(mesh.trackerGeofenceState() == Result::Inside && !mesh.isTrackerFindMyEscalated());
  assert(command(mesh, "get tracker.fence").find("last-known=inside last-acquisition=valid") != std::string::npos);
  transmitted(mesh); assert(response(mesh, Safe));
  assert(mesh.tracker_next_check == mesh.rtc.now + mesh.tracker.normal_interval);

  // A fresh exit is persisted independently of a safe owner reply; LoRa
  // cadence remains the existing owner-controlled normal interval.
  mesh.tracker_next_check = mesh.rtc.now; mesh.serviceTracker();
  assert(mesh.tracker_acquiring); sensors.fix(47.5, -121.5); mesh.serviceTracker();
  assert(mesh.trackerGeofenceState() == Result::Outside && mesh.isTrackerFindMyEscalated());
  transmitted(mesh); assert(response(mesh, Safe));
  assert(mesh.tracker.last_lost == Safe && mesh.isTrackerFindMyEscalated());
  assert(mesh.tracker_next_check == mesh.rtc.now + mesh.tracker.normal_interval);
  mesh::tracker::geofence::Record saved;
  assert(mesh::tracker::geofence::load(&mesh.fs, saved) == mesh::tracker::geofence::StoreResult::Success);
  assert(saved.last_known == Result::Outside);
  {
    const FakeSensors original_sensors = sensors;
    SensorMesh reboot;
    reboot.fs.files = mesh.fs.files;
    metadata_filesystem = &reboot.fs;
    reboot.tracker = mesh.tracker;
    assert(mesh::tracker::geofence::load(&reboot.fs, reboot.tracker_geofence)
           == mesh::tracker::geofence::StoreResult::Success);
    assert(reboot.trackerGeofenceState() == Result::Outside && reboot.isTrackerFindMyEscalated());
    assert(command(reboot, "get tracker.fence").find("last-known=outside last-acquisition=none") != std::string::npos);
    metadata_filesystem = &mesh.fs;
    sensors = original_sensors;
  }
  assert(command(mesh, "set tracker.box 47,-123,48,-122") == "OK - box set; outside retained pending fix");
  assert(mesh.isTrackerFindMyEscalated());
  assert(command(mesh, "get tracker.fence").find("last-known=outside(previous-box) last-acquisition=none") != std::string::npos);

  // No fix and an invalid fix cannot clear a confirmed exit.
  mesh.tracker_next_check = mesh.rtc.now; mesh.serviceTracker();
  sensors.pending = sensors.fresh = false; mesh.serviceTracker();
  assert(mesh.trackerGeofenceState() == Result::Outside && mesh.isTrackerFindMyEscalated());
  assert(command(mesh, "get tracker.fence").find("last-known=outside(previous-box) last-acquisition=no-fix") != std::string::npos);
  transmitted(mesh); assert(response(mesh, Safe));
  mesh.tracker_next_check = mesh.rtc.now; mesh.serviceTracker();
  sensors.fix(47.5, -121.5); mesh.serviceTracker();
  assert(command(mesh, "get tracker.fence").find("last-known=outside last-acquisition=valid") != std::string::npos);
  assert(mesh::tracker::geofence::load(&mesh.fs, saved) == mesh::tracker::geofence::StoreResult::Success);
  assert(!saved.previous_box && saved.last_known == Result::Outside);
  transmitted(mesh); assert(response(mesh, Safe));
  mesh.tracker_next_check = mesh.rtc.now; mesh.serviceTracker();
  sensors.fix(NAN, -122); mesh.serviceTracker();
  assert(mesh.trackerGeofenceState() == Result::Outside && mesh.isTrackerFindMyEscalated());
  transmitted(mesh); assert(response(mesh, Safe));

  // A new valid inside fix clears the fence cause, but an owner Lost answer
  // still independently escalates, using the existing lost interval.
  mesh.tracker_next_check = mesh.rtc.now; mesh.serviceTracker();
  sensors.fix(47.5, -122.5); mesh.serviceTracker();
  assert(mesh.trackerGeofenceState() == Result::Inside && !mesh.isTrackerFindMyEscalated());
  transmitted(mesh); assert(response(mesh, Lost, 120));
  assert(mesh.tracker.last_lost == Lost && mesh.isTrackerFindMyEscalated());
  assert(mesh.tracker_next_check == mesh.rtc.now + mesh.tracker.lost_interval);

  // Clearing the box persists the disabled state and does not change owner loss.
  assert(command(mesh, "set tracker.box clear") == "OK - box cleared");
  assert(mesh.isTrackerFindMyEscalated() && mesh.tracker.last_lost == Lost);
  assert(command(mesh, "get tracker.box") == "off");
  assert(mesh::tracker::geofence::load(&mesh.fs, saved) == mesh::tracker::geofence::StoreResult::Success);
  assert(!saved.box.configured && saved.last_known == Result::Unknown);
  ++scenarios;
}

static void geofenceStorageFault() {
  using mesh::tracker::geofence::Result;
  {
    SensorMesh mesh; configure(mesh);
    assert(command(mesh, "set tracker.box 47,-123,48,-122") == "OK - box set; location unknown");
    mesh.observeTrackerGeofence(true, 475000000, -1215000000);
    assert(mesh.isTrackerFindMyEscalated() && mesh.trackerGeofenceState() == Result::Outside);
    const unsigned writes = mesh.fs.write_opens;
    mesh.observeTrackerGeofence(false, 0, 0);
    mesh.observeTrackerGeofence(true, 475000000, -1215000000);
    assert(mesh.fs.write_opens == writes); // no wear for unchanged/no fix
    mesh.fs.short_write = true;
    mesh.observeTrackerGeofence(true, 475000000, -1225000000);
    assert(mesh.tracker_geofence_storage_fault && !mesh.tracker_storage_fault);
    assert(mesh.trackerGeofenceState() == Result::Outside && mesh.isTrackerFindMyEscalated());
    assert(command(mesh, "get tracker.fence").find("storage=fault") != std::string::npos);
    assert(command(mesh, "set tracker.box clear") == "ERR: box save failed");
    mesh.fs.short_write = false;
    mesh::tracker::geofence::Record saved;
    assert(mesh::tracker::geofence::load(&mesh.fs, saved) == mesh::tracker::geofence::StoreResult::Success);
    assert(saved.last_known == Result::Outside); // reboot stays conservative
  }
  {
    SensorMesh mesh; configure(mesh);
    assert(command(mesh, "set tracker.box 47,-123,48,-122") == "OK - box set; location unknown");
    mesh.observeTrackerGeofence(true, 475000000, -1225000000);
    assert(mesh.trackerGeofenceState() == Result::Inside);
    mesh.fs.short_write = true;
    mesh.observeTrackerGeofence(true, 475000000, -1215000000);
    assert(mesh.tracker_geofence_storage_fault && !mesh.tracker_storage_fault);
    assert(mesh.isTrackerFindMyEscalated()); // do not hide a newly observed exit
  }
  ++scenarios;
}

#if defined(MESH_SENSOR_MOTION) && MESH_SENSOR_MOTION
static void motionScheduling() {
  using mesh::tracker::motion::Observation;
  {
    SensorMesh mesh; configure(mesh);
    assert(command(mesh, "set tracker.box 47,-123,48,-122") == "OK - box set; location unknown");
    queueCheck(mesh); transmitted(mesh); assert(response(mesh, Safe));
    assert(mesh.trackerGeofenceState() == mesh::tracker::geofence::Result::Inside);
    mesh.serviceTracker();
    assert(mesh.tracker_radio_asleep);
    // A working sensor must report quiet repeatedly for five minutes.
    for (int i = 0; i < 12; ++i) {
      mesh.now_ms += 30000; mesh.rtc.now += 30; mesh.serviceTracker();
    }
    assert(mesh.motion_quiet_policy.confirmed());
    assert(mesh.trackerCanExtendOwnerCheck(mesh.now_ms));
    assert(mesh.trackerOwnerInterval(mesh.rtc.now, mesh.now_ms, false) == 1800);
    // A fresh Unknown reply must not reuse an older Safe response to extend.
    assert(command(mesh, "tracker check") == "OK scheduled");
    queueCheck(mesh); transmitted(mesh); assert(response(mesh, Unknown));
    assert(mesh.tracker.last_lost == Safe && mesh.tracker_next_check == mesh.rtc.now + 1800);
    mesh.tracker.last_lost = Unknown;
    assert(!mesh.trackerCanExtendOwnerCheck(mesh.now_ms));
    mesh.tracker.last_lost = Safe;
    assert(mesh.trackerOwnerInterval(mesh.rtc.now, mesh.now_ms, true) == 7200);
    mesh.tracker_next_check = mesh.rtc.now + 7200;
    assert(command(mesh, "get tracker.profile") == "profile=backyard");
    assert(command(mesh, "set tracker.profile hiking", 1) == "ERR: tracker is local-only");
    assert(mesh.getTrackerProfile() == mesh::tracker::profile::Mode::Backyard);
    assert(command(mesh, "set tracker.profile hiking") == "OK - profile hiking");
    assert(mesh.getTrackerProfile() == mesh::tracker::profile::Mode::Hiking);
    assert(mesh.isTrackerProfileAvailable() && !mesh.trackerCanExtendOwnerCheck(mesh.now_ms));
    assert(!mesh.tracker_quiet_extended && mesh.tracker_next_check == mesh.rtc.now + mesh.tracker.normal_interval);
    mesh::tracker::profile::Record stored_profile;
    assert(mesh::tracker::profile::load(&mesh.fs, stored_profile)
           == mesh::tracker::profile::StoreResult::Success);
    assert(stored_profile.mode == mesh::tracker::profile::Mode::Hiking);
    const auto writes_after_hiking = mesh.fs.write_opens;
    assert(command(mesh, "set tracker.profile hiking") == "OK - profile hiking");
    assert(mesh.fs.write_opens == writes_after_hiking); // no extra flash wear
    assert(command(mesh, "set tracker.profile backyard") == "OK - profile backyard");
    assert(mesh.tracker_next_check == mesh.rtc.now + mesh.tracker.normal_interval);
    assert(mesh.toggleTrackerProfile() && mesh.getTrackerProfile() == mesh::tracker::profile::Mode::Hiking);
    assert(mesh.toggleTrackerProfile() && mesh.getTrackerProfile() == mesh::tracker::profile::Mode::Backyard);
    assert(mesh.trackerOwnerInterval(mesh.rtc.now, mesh.now_ms, true) == 7200);
    mesh.tracker_next_check = mesh.rtc.now + 7200;
    assert(command(mesh, "get tracker.motion").find("quiet-window") != std::string::npos);
    mesh.tracker_geofence_last_acquisition = 2;
    assert(!mesh.trackerCanExtendOwnerCheck(mesh.now_ms)); // a failed GPS fix is unknown
    mesh.tracker_geofence_last_acquisition = 1;

    // A failed accelerometer must immediately retire the quiet extension.
    const uint32_t normal_due = mesh.rtc.now + mesh.tracker.normal_interval;
    mesh.motion_sensor.available = false;
    mesh.now_ms += 30000; mesh.rtc.now += 30; mesh.serviceTracker();
    assert(mesh.motion_observation == Observation::Unknown);
    assert(mesh.tracker_next_check == normal_due && !mesh.tracker_quiet_extended);
    assert(command(mesh, "get tracker.motion").find("unknown") != std::string::npos);
    mesh.motion_sensor.available = true;
    for (int i = 0; i < 13; ++i) {
      mesh.now_ms += 30000; mesh.rtc.now += 30; mesh.serviceTracker();
    }
    assert(mesh.motion_quiet_policy.confirmed() && mesh.trackerCanExtendOwnerCheck(mesh.now_ms));
    assert(mesh.trackerOwnerInterval(mesh.rtc.now, mesh.now_ms, true) == 7200);
    mesh.tracker_next_check = mesh.rtc.now + 7200;

    // A genuine quiet-to-motion transition triggers exactly one earlier owner
    // check and coalesces its GPS work instead of sending on every poll.
    const unsigned first_created = mesh.created;
    mesh.now_ms += 30000; mesh.rtc.now += 30;
    mesh.motion_sensor.next = Observation::Motion;
    mesh.serviceTracker();
    assert(mesh.tracker_acquiring && sensors.pending && !mesh.tracker_radio_asleep);
    assert(mesh.created == first_created);
    sensors.fix(); mesh.serviceTracker(); assert(mesh.created == first_created + 1);
    transmitted(mesh); assert(response(mesh, Safe));
    mesh.serviceTracker(); assert(mesh.tracker_radio_asleep);

    // Motion without a verified quiet edge only starts a GPS/geofence fix.
    // LoRa stays asleep and the owner deadline is not advanced.
    mesh.motion_quiet_policy.reset();
    mesh.tracker_next_check = mesh.rtc.now + 7200;
    const uint32_t owner_due = mesh.tracker_next_check;
    const unsigned before_gps = sensors.starts, before_packets = mesh.created, before_wakes = mesh.radio.wakes;
    mesh.now_ms += 16 * 60000; mesh.rtc.now += 16 * 60;
    mesh.motion_sensor.next = Observation::Motion;
    mesh.serviceTracker();
    assert(mesh.motion_gps_acquiring && sensors.pending);
    assert(mesh.tracker_radio_asleep && mesh.radio.wakes == before_wakes);
    assert(mesh.created == before_packets && mesh.tracker_next_check == owner_due);
    sensors.fix(47.5, -121.5); mesh.serviceTracker();
    assert(!mesh.motion_gps_acquiring && mesh.isTrackerFindMyEscalated());
    assert(mesh.created == before_packets && sensors.starts == before_gps + 1);

    // A failed motion fix backs off at least one hour, while an explicit
    // owner check bypasses that throttle and is not hidden.
    mesh.motion_quiet_policy.reset();
    mesh.tracker_next_check = mesh.rtc.now + 7200;
    mesh.now_ms += 16 * 60000; mesh.rtc.now += 16 * 60;
    mesh.motion_sensor.next = Observation::Motion; mesh.serviceTracker();
    assert(mesh.motion_gps_acquiring && sensors.pending);
    sensors.pending = false; sensors.fresh = false; mesh.serviceTracker();
    assert(!mesh.motion_gps_acquiring);
    const unsigned after_failed = sensors.starts;
    mesh.now_ms += 16 * 60000; mesh.rtc.now += 16 * 60;
    mesh.motion_sensor.next = Observation::Motion; mesh.serviceTracker();
    assert(!mesh.motion_gps_acquiring && sensors.starts == after_failed);
    assert(command(mesh, "tracker check") == "OK scheduled");
    mesh.serviceTracker(); assert(mesh.tracker_acquiring && sensors.starts == after_failed + 1);
  }
  {
    // An ambiguous profile save prevents further changes and removes a
    // backyard-only extended owner check without delaying normal work.
    SensorMesh mesh; configure(mesh);
    mesh.tracker_quiet_extended = true;
    mesh.tracker_owner_check_anchor = mesh.rtc.now;
    mesh.tracker_next_check = mesh.rtc.now + 7200;
    mesh.fs.short_write = true;
    assert(command(mesh, "set tracker.profile hiking") == "ERR: profile save failed");
    assert(!mesh.isTrackerProfileAvailable());
    assert(command(mesh, "get tracker.profile") == "ERR: profile storage unavailable");
    assert(!mesh.toggleTrackerProfile());
    assert(mesh.tracker_next_check == mesh.rtc.now + mesh.tracker.normal_interval);
    assert(!mesh.tracker_quiet_extended);
  }
  {
    SensorMesh mesh; configure(mesh);
    assert(command(mesh, "set tracker.box 47,-123,48,-122") == "OK - box set; location unknown");
    queueCheck(mesh); transmitted(mesh); assert(response(mesh, Safe));
    mesh.serviceTracker(); assert(mesh.tracker_radio_asleep);
    mesh.tracker_next_check = mesh.rtc.now + 7200;
    mesh.now_ms += 16 * 60000; mesh.rtc.now += 16 * 60;
    mesh.motion_sensor.next = Observation::Motion; mesh.serviceTracker();
    assert(mesh.motion_gps_acquiring && sensors.pending && mesh.tracker_radio_asleep);
    const unsigned before_packets = mesh.created, before_wakes = mesh.radio.wakes;
    mesh._cli.emergency_sleep = true;
    mesh.tracker_next_check = mesh.rtc.now; // due while emergency is requested
    mesh.loop();
    assert(mesh.motion_gps_acquiring && !mesh.tracker_acquiring && mesh.radio.wakes == before_wakes);
    sensors.fix(); mesh.loop();
    assert(!mesh.motion_gps_acquiring && mesh.created == before_packets && mesh.radio.wakes == before_wakes);
    mesh.serviceTracker(); assert(mesh.created == before_packets && mesh.radio.wakes == before_wakes);
  }
  {
    SensorMesh mesh; configure(mesh);
    mesh.serviceTracker(); assert(mesh.tracker_acquiring && sensors.pending);
    const unsigned before_packets = mesh.created;
    mesh._cli.emergency_sleep = true;
    sensors.fix(); mesh.serviceTracker();
    assert(!mesh.tracker_acquiring && !mesh.tracker_pending && mesh.created == before_packets);
  }
  {
    SensorMesh mesh; configure(mesh);
    mesh.serviceTracker(); assert(mesh.tracker_acquiring && sensors.pending);
    mesh.tracker_storage_fault = true;
    mesh.serviceTracker();
    assert(!mesh.tracker_acquiring && sensors.pending);
    mesh._cli.emergency_sleep = true;
    assert(mesh.emergencyPowerBusy());
    sensors.fix();
    assert(!mesh.emergencyPowerBusy());
  }
  {
    // Boards which cannot enter emergency sleep must not silently suppress
    // tracking because the saved emergency policy asks them to sleep.
    SensorMesh mesh; configure(mesh);
    mesh.board.emergency_supported = false;
    mesh._cli.emergency_sleep = true;
    mesh.serviceTracker();
    assert(mesh.tracker_acquiring && sensors.pending);
  }
  {
    SensorMesh mesh; configure(mesh);
    assert(command(mesh, "set tracker.box 47,-123,48,-122") == "OK - box set; location unknown");
    assert(command(mesh, "set tracker.profile hiking") == "OK - profile hiking");
    mesh.serviceTracker(); assert(sensors.pending && mesh.tracker_acquiring);
    SPIFFS.fs = &mesh.fs; SPIFFS.success = true; SPIFFS.calls = 0;
    assert(mesh.formatFileSystem() && SPIFFS.calls == 1);
    assert(!mesh.tracker.enabled && !sensors.pending && !sensors.mode && !mesh.tracker_acquiring);
    assert(!mesh.tracker_geofence.box.configured && mesh.tracker_profile.mode == mesh::tracker::profile::Mode::Backyard);
    assert(!mesh.tracker_storage_fault && !mesh.tracker_geofence_storage_fault && !mesh.tracker_profile_storage_fault);
    assert(command(mesh, "set tracker.box 47,-123,48,-122") == "OK - box set; location unknown");
  }
  {
    SensorMesh mesh; configure(mesh);
    mesh.motion_sensor.fail_stop = true;
    SPIFFS.fs = &mesh.fs; SPIFFS.success = true; SPIFFS.calls = 0;
    assert(!mesh.formatFileSystem() && SPIFFS.calls == 0);
    assert(!mesh.tracker.enabled && !sensors.mode && mesh.tracker_storage_fault && mesh.tracker_profile_storage_fault);
    assert(command(mesh, "set tracker.profile hiking") == "ERR: profile storage unavailable");
  }
  {
    SensorMesh mesh; configure(mesh);
    SPIFFS.fs = &mesh.fs; SPIFFS.success = false; SPIFFS.calls = 0;
    assert(!mesh.formatFileSystem() && SPIFFS.calls == 1);
    assert(!mesh.tracker.enabled && mesh.tracker_storage_fault && mesh.tracker_geofence_storage_fault && mesh.tracker_profile_storage_fault);
  }
  ++scenarios;
}
#endif

int main(int argc, char** argv) {
  assert(argc == 2); const std::string name = argv[1];
  if (name == "commands") commands();
  else if (name == "cadence") cadence();
  else if (name == "awake-expiry") awakeExpiry();
  else if (name == "awake-owners") awakeOwners();
  else if (name == "awake-long") awakeLongCadence();
  else if (name == "timing") timing();
  else if (name == "identity") identityAndNonce();
  else if (name == "paths") paths();
  else if (name == "suppression") suppressedNormalTraffic();
  else if (name == "floods") floods();
  else if (name == "durability") durability();
  else if (name == "ownership") ownership();
  else if (name == "reboot") rebootAndNonces();
  else if (name == "clock-cutoff") idleClockAndCutoffCrossing();
  else if (name == "wake") wakeRecovery();
  else if (name == "zero-clock") zeroClockAcquisition();
  else if (name == "forward-clock") forwardClockCorrection();
  else if (name == "gps-handoff") managerOwnsAcquisitionHandoff();
  else if (name == "gps") gpsAndMaintenance();
  else if (name == "geofence") geofenceScenario();
  else if (name == "geofence-fault") geofenceStorageFault();
#if defined(MESH_SENSOR_MOTION) && MESH_SENSOR_MOTION
  else if (name == "motion") motionScheduling();
#endif
  else assert(false);
  printf("PASS: sensor tracker client %s (%u scenario groups)\n", name.c_str(), scenarios);
}
