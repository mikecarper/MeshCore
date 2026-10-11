#pragma once
// Hardware adapter for the complete production Tracker.cpp. The wire codec,
// route policy, durable store, Packet layout and packet SHA256 stay production.
#include "tracker_storage_fs.h"
#include <Packet.h>
#include <helpers/SensorTrackerStore.h>
#include <helpers/TrackerProtocol.h>
#include <array>
#include <cmath>
#include <limits>

#define MESH_ENABLE_SENSOR_TRACKER 1
#define MAX_SEARCH_RESULTS 24

namespace mesh {
static bool test_logging_watchdog = false;
bool isUsbLoggingWatchdogArmed() { return test_logging_watchdog; }
struct Identity {
  uint8_t pub_key[32] = {};
  Identity() = default;
  explicit Identity(const uint8_t* key) { memcpy(pub_key, key, sizeof(pub_key)); }
  bool matches(const uint8_t* key) const { return memcmp(pub_key, key, sizeof(pub_key)) == 0; }
  bool isHashMatch(const uint8_t* hash) const { return memcmp(pub_key, hash, PATH_HASH_SIZE) == 0; }
};
struct LocalIdentity : Identity {
  // Deterministic transport adapter preserves all 32 key bytes. Production
  // encrypted transport authenticates one of the selected full-key secrets.
  void calcSharedSecret(uint8_t* out, const uint8_t* owner) const {
    for (size_t i = 0; i < 32; ++i) out[i] = owner[i] ^ pub_key[i] ^ 0xa7;
  }
};
class Mesh {
public:
  unsigned base_send_complete = 0, base_send_fail = 0;
  void onSendComplete(Packet*) { ++base_send_complete; }
  void onSendFail(Packet*) { ++base_send_fail; }
};
}

struct FakeRTC : mesh::RTCClock {
  uint32_t now = 1000000;
  uint32_t getCurrentTime() override { return now; }
  void setCurrentTime(uint32_t value) override { now = value; }
};
struct FakeBoard {
  bool usb = false, ota = false, test = false;
  bool isUsbDataConnected() const { return usb; }
  bool isOTAUpdateRunning() const { return ota; }
  bool isRadioTestActive() const { return test; }
  uint16_t getBattMilliVolts() const { return 3765; }
};
struct FakeRadio {
  bool permit_sleep = true, permit_wake = true, permit_recovery = true, sleeping = false;
  unsigned sleeps = 0, wakes = 0, wake_attempts = 0, hard_recoveries = 0;
  bool setTrackerSleep(bool value) {
    if (!value) ++wake_attempts;
    if ((value && !permit_sleep) || (!value && !permit_wake)) return false;
    sleeping = value;
    if (value) ++sleeps; else ++wakes;
    return true;
  }
  bool recoverRadio(bool hard) {
    assert(hard); ++hard_recoveries;
    if (!permit_recovery) return false;
    sleeping = false; return true;
  }
};
struct FakeDriver {
  bool watchdog = false, calibrating = false;
  bool isWatchdogObserving() const { return watchdog; }
  bool isCalibratingNoiseFloor() const { return calibrating; }
};
struct FakeSensors {
  bool detected = true, mode = false, power_save = false;
  bool pending = false, fresh = false;
  double latitude = 0, longitude = 0;
  uint32_t* clock = nullptr;
  uint32_t acquisition_until = 0;
  bool hold_window = false;
  unsigned starts = 0;
  void setTrackerGpsModeEnabled(bool value) { mode = value; if (!value) pending = fresh = false; }
  void setPowerSavingEnabled(bool value) { power_save = value; }
  bool isGPSDetected() const { return detected; }
  bool beginTrackerGpsAcquisition() {
    assert(clock); ++starts; pending = true; fresh = false;
    acquisition_until = *clock + 120000; return detected;
  }
  bool isTrackerGpsAcquisitionPending() {
    // SensorManager owns finalizing its acquisition, including receiver stop.
    if (pending && !hold_window && int32_t(*clock - acquisition_until) >= 0) pending = fresh = false;
    return pending;
  }
  bool takeTrackerGpsPosition(double& lat, double& lon) {
    if (!fresh) return false;
    lat = latitude; lon = longitude; fresh = false; return true;
  }
  void fix(double lat = 47.1234567, double lon = -122.2345678) {
    latitude = lat; longitude = lon; pending = false; fresh = true;
  }
};
static FakeSensors sensors;

struct FakePool {
  std::set<mesh::Packet*> allocated;
  std::vector<mesh::Packet*> queued;
  mesh::Packet* in_flight = nullptr;
  unsigned frees = 0;
  int free_limit = 32;
  ~FakePool() { for (auto* packet : allocated) delete packet; }
  int getOutboundTotal() const { return static_cast<int>(queued.size()); }
  const mesh::Packet* getOutboundByIdx(int i) const { return queued.at(i); }
  mesh::Packet* removeOutboundByIdx(int i) {
    auto* packet = queued.at(i); queued.erase(queued.begin() + i); return packet;
  }
  int getFreeCount() const { return free_limit - static_cast<int>(allocated.size()); }
  mesh::Packet* alloc() { auto* p = new mesh::Packet; assert(allocated.insert(p).second); return p; }
  void free(mesh::Packet* p) {
    assert(p && p != in_flight);
    assert(std::find(queued.begin(), queued.end(), p) == queued.end());
    assert(allocated.erase(p) == 1); ++frees; delete p;
  }
};
struct ClientInfo {
  mesh::Identity id;
  uint8_t shared_secret[32] = {};
};
struct FakeACL {
  std::vector<ClientInfo> clients;
  int getNumClients() const { return static_cast<int>(clients.size()); }
  ClientInfo* getClientByIdx(int index) { return &clients.at(index); }
};
struct FakeTrigger { char text[8] = "alert"; };

class SensorMesh : public mesh::Mesh {
public:
  MemoryFS fs;
  MemoryFS* _fs = &fs;
  FakeRTC rtc;
  uint32_t now_ms = 100;
  struct Milliseconds { uint32_t* value; uint32_t getMillis() const { return *value; } } clock_ms{&now_ms};
  Milliseconds* _ms = &clock_ms;
  FakeBoard board;
  FakeRadio radio;
  FakeRadio* _radio = &radio;
  FakeDriver radio_driver;
  FakePool pool;
  FakePool* _mgr = &pool;
  mesh::LocalIdentity self_id;
  mesh::tracker::TrackerRecord tracker;
  uint8_t tracker_secret[32] = {};
  bool tracker_storage_available = true, tracker_storage_fault = false;
  bool tracker_radio_asleep = false, tracker_acquiring = false;
  bool tracker_pending = false, tracker_sent = false;
  uint8_t tracker_packet_hash[MAX_HASH_SIZE] = {};
  uint32_t tracker_next_check = 0, tracker_pending_tag = 0;
  unsigned long tracker_reply_until = 0, tracker_awake_until = 0;
  uint32_t tracker_boot_anchor = 1000000;
  uint32_t tracker_clock_rtc = 0;
  unsigned long tracker_clock_millis = 0;
  unsigned long next_local_advert = 123, next_flood_advert = 456;
  struct Prefs { uint8_t powersaving_enabled = 0, path_hash_mode = 0;
    bool disable_fwd = false; unsigned flood_max = 64; } _prefs;
  FakeTrigger alert1, alert2;
  FakeTrigger* alert_tasks[4] = {&alert1, &alert2};
  int num_alert_tasks = 2;
  struct CLI { bool active = false; bool hasActiveUserGpioTimer() const { return active; } } _cli;
  struct ClockSync { unsigned observed = 0;
    void observeAcceptedFlood(const mesh::Packet*) { ++observed; } } _clock_sync;
  uint32_t set_radio_at = 0, revert_radio_at = 0;
  bool saved_radio_apply_pending = false, temporary = false, pending_ota = false, dual = false;
  bool queue_wake = false, retry_wake = false, available = true;
  bool allocation_failure = false, admission_failure = false;
  unsigned created = 0, direct_sends = 0, flood_sends = 0, adverts = 0, zero_hops = 0;
  unsigned cancel_active = 0, cancel_queued = 0, cancel_inflight = 0;
  bool cancel_delivered = false;
  unsigned advert_timer_updates = 0, flood_timer_updates = 0;
  std::vector<uint8_t> last_payload;
  uint8_t last_dest[32] = {}, last_secret[32] = {};
  int default_scope = 0;
  FakeACL acl;
  int matching_peer_indexes[MAX_SEARCH_RESULTS] = {};
  unsigned ordinary_data = 0, ordinary_paths = 0;
  SensorMesh() {
    sensors = FakeSensors(); mesh::test_logging_watchdog = false;
    sensors.clock = &now_ms;
    metadata_filesystem = &fs;
    self_id.pub_key[0] = 0xf1;
  }
  FakeRTC* getRTCClock() const { return const_cast<FakeRTC*>(&rtc); }
  unsigned long futureMillis(int delta) const { return uint32_t(now_ms + uint32_t(delta)); }
  bool millisHasNowPassed(unsigned long value) const { return int32_t(now_ms - uint32_t(value)) > 0; }
  bool hasOutbound() const { return pool.in_flight != nullptr; }
  bool isAnyTempRadioActive() const { return temporary; }
  bool hasPendingOtaApply() const { return pending_ota; }
  bool isDualRadioActive() const { return dual; }
  bool getNextQueueWakeDelay(uint32_t& value) const { value = 1; return queue_wake; }
  bool getNextRetryWakeDelay(uint32_t& value) const { value = 1; return retry_wake; }
  void setRadioAvailable(bool value) { available = value; }
  void updateAdvertTimer() { ++advert_timer_updates; }
  void updateFloodAdvertTimer() { ++flood_timer_updates; }
  void releasePacket(mesh::Packet* p) { pool.free(p); }
  const mesh::Packet* getOutboundInFlight() const { return pool.in_flight; }
  void cancelActiveRetries(const uint8_t* hash) {
    assert(memcmp(hash, tracker_packet_hash, MAX_HASH_SIZE) == 0); ++cancel_active;
  }
  void cancelOutboundRadioRetry(const mesh::Packet* p, bool delivered) {
    assert(pool.allocated.count(const_cast<mesh::Packet*>(p)));
    if (p == pool.in_flight) ++cancel_inflight; else ++cancel_queued;
    cancel_delivered = delivered;
  }
  mesh::Packet* createDatagram(uint8_t type, const mesh::Identity& dest,
                              const uint8_t* secret, const uint8_t* data, size_t length) {
    ++created;
    assert(length <= MAX_PACKET_PAYLOAD);
    memcpy(last_dest, dest.pub_key, 32); memcpy(last_secret, secret, 32);
    last_payload.assign(data, data + length);
    if (allocation_failure) return nullptr;
    auto* packet = pool.alloc(); packet->header = type << PH_TYPE_SHIFT;
    packet->payload_len = length; memcpy(packet->payload, data, length); return packet;
  }
  bool admit(mesh::Packet* packet) {
    assert(packet->tx_radio == mesh::RADIO_TX_PRIMARY);
    assert(packet->flood_retry_policy == mesh::FLOOD_RETRY_POLICY_DENY);
    if (admission_failure) { onSendFail(packet); releasePacket(packet); return false; }
    pool.queued.push_back(packet); return true;
  }
  bool sendDirect(mesh::Packet* packet, const uint8_t* path, uint8_t length) {
    ++direct_sends; assert(mesh::Packet::isValidPathLen(length));
    packet->header = (packet->header & ~PH_ROUTE_MASK) | ROUTE_TYPE_DIRECT;
    packet->path_len = length;
    if (length) memcpy(packet->path, path, mesh::tracker::trackerPathByteLength(length));
    return admit(packet);
  }
  bool sendFloodScoped(int, mesh::Packet* packet, unsigned = 0, uint8_t width = 1) {
    if (packet->getPayloadType() == PAYLOAD_TYPE_ADVERT) { ++adverts; releasePacket(packet); return true; }
    ++flood_sends; assert(width >= 1 && width <= 3);
    packet->header = (packet->header & ~PH_ROUTE_MASK) | ROUTE_TYPE_FLOOD;
    packet->setPathHashSizeAndCount(width, 0); return admit(packet);
  }
  mesh::Packet* createSelfAdvert() { auto* p = pool.alloc(); p->header = PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT; return p; }
  void sendZeroHop(mesh::Packet* p, int) { ++adverts; ++zero_hops; releasePacket(p); }
  bool isTrackerModeEnabled() const { return tracker.enabled; }
  bool handleTrackerCommand(uint32_t, const char*, char*);
  void configureTrackerRuntime();
  bool saveTrackerRecord(mesh::tracker::TrackerRecord&);
  bool trackerNeedsRadio() const;
  void wakeTrackerRadio();
  void cancelTrackerPending(bool delivered = false);
  void serviceTracker();
  bool handleTrackerResponse(mesh::Packet*, uint8_t, const uint8_t*, size_t,
                             const uint8_t* path = nullptr, uint8_t path_len = 0xff);
  bool handleTrackerPath(mesh::Packet*, uint8_t*, uint8_t, uint8_t, uint8_t*, size_t);
  void onSendComplete(mesh::Packet*);
  void onSendFail(mesh::Packet*);
  int searchPeersByHash(const uint8_t*);
  void getPeerSharedSecret(uint8_t*, int);
  void onPeerDataRecv(mesh::Packet*, uint8_t, int, const uint8_t*, uint8_t*, size_t);
  bool onPeerPathRecv(mesh::Packet*, int, const uint8_t*, uint8_t*, uint8_t, uint8_t, uint8_t*, uint8_t);
  bool allowPacketForward(const mesh::Packet*);
  void sendSelfAdvertisement(int, bool);
};
