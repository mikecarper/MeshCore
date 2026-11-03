// This fixture runs extracted production scheduling methods. Hardware and
// packet-path hashing are deliberately small substitutes, not clock policies.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <initializer_list>
#include <string>
#include <helpers/ClockSyncUtils.h>
#include <helpers/EmergencyPowerPolicy.h>
#define private public
#include <helpers/MeshClockSync.h>
#undef private

#define MESH_DEBUG_PRINTLN(...) do {} while (false)

static constexpr uint64_t Day = 86400000ULL;
static constexpr uint64_t Startup = 1800000ULL;
static constexpr uint64_t Retry = 1800000ULL;
static constexpr uint64_t Freshness = 7200000ULL;
static constexpr uint32_t Epoch = 1800000000UL;
static constexpr uint32_t MaximumEpoch = Epoch + 315360000UL;

class FakeCLI {
public:
  mesh::emergencypower::State state;
  FakeCLI() { state.begin(mesh::emergencypower::Settings{}, 0); }
  bool emergencyActive() const { return state.active(); }
};

struct Mqtt {
  bool fresh = false, running = false, requested = false;
  bool finished = false, success = false;
  uint32_t epoch = Epoch;
  unsigned requests = 0;
  bool hasFreshNtpThisBoot() const { return fresh; }
  bool isRunning() const { return running; }
  bool requestNtpTimeEstimate() { ++requests; requested = true; return true; }
  bool takeNtpTimeEstimate(uint32_t& value, bool& done) {
    value = epoch; done = finished; return success;
  }
};

static const char* skipLocalSpaces(const char* text) {
  while (text && *text == ' ') ++text;
  return text;
}
static bool parsePositiveSelector(const char*, int&) { return false; }

namespace repeater {
@REPEATER_DEFINES@
@REPEATER_ENUMS@

static bool clockSyncEpochIsValid(uint32_t epoch) {
  return epoch >= Epoch && epoch <= MaximumEpoch;
}
static uint32_t clockSyncMaximumValidEpoch() { return MaximumEpoch; }
static void deriveClockSyncPathId(const mesh::Packet* packet, uint8_t* path) {
  memcpy(path, packet->path_id, 8);
}
struct Limiter { unsigned resets = 0; void reset() { ++resets; } };
struct Nonce { void clear() {} };

class MyMesh {
public:
  @REPEATER_SAMPLE@
  @REPEATER_FIELDS@
  mesh::MillisecondClock ms;
  mesh::MillisecondClock* _ms = &ms;
  mesh::Radio radio;
  mesh::Radio* _radio = &radio;
  mesh::RTCClock rtc;
  ClientACL acl;
  SensorManager sensors;
  FakeCLI _cli;
  Mqtt mqtt;
  Mqtt* mqtt_bridge = &mqtt;
  Limiter discover_limiter, anon_limiter;
  Nonce replay_reset_nonce;
  bool replay_clock_set = false;
  uint64_t uptime_millis = 0;
  unsigned radio_refreshes = 0;

  MyMesh() {
    memset(clock_sync_samples, 0, sizeof(clock_sync_samples));
    clock_sync_required_samples = CLOCK_SYNC_REQUIRED_SAMPLES_DEFAULT;
    clock_sync_mesh_enabled = true;
    clock_sync_mesh_edge_enabled = true;
    clock_sync_internet_enabled = false;
    clock_sync_mesh_suppressed_by = CLOCK_SYNC_MESH_SUPPRESS_NONE;
    clock_sync_drift_seconds = mesh::CLOCK_SYNC_DRIFT_DEFAULT_SECONDS;
    resetClockSyncAttempt();
  }
  mesh::RTCClock* getRTCClock() { return &rtc; }
  void refreshScheduledRadioState() { ++radio_refreshes; }
  uint32_t estimateClockTransitMillis(const mesh::Packet*) const { return 0; }
  void formatClockSyncTable(char*, size_t) const { assert(false); }
  void formatClockSyncSampleDetail(int, char*, size_t) const { assert(false); }
  void resetClockSyncAttempt();
  void suppressMeshClockSyncForBoot(uint8_t);
  void onManualClockSet();
  void checkGpsClockSyncOverride();
  uint64_t nextClockSyncAttemptUptime() const;
  bool isClockSyncCollectionActive() const;
  void recordClockSyncSample(uint8_t, const uint8_t[4], uint32_t, const mesh::Packet*);
  bool estimateMeshClock(uint32_t&, uint8_t&, uint8_t&, uint8_t&) const;
  bool applyClockEstimate(uint32_t, uint8_t, uint8_t);
  void checkClockSync();
  void formatClockSyncStatus(const char*, char*, size_t) const;
};
@REPEATER_METHODS@
}  // namespace repeater

namespace mesh {
@SHARED_CONSTANTS@
static bool validEpoch(uint32_t epoch) { return epoch >= Epoch && epoch <= MaximumEpoch; }
static uint32_t maximumValidEpoch() { return MaximumEpoch; }
static const char* skipSpaces(const char* text) { return skipLocalSpaces(text); }
static void derivePathId(const Packet* packet, uint8_t* path) {
  memcpy(path, packet->path_id, 8);
}
uint32_t MeshClockSync::estimateTransitMillis(const Packet*) const { return 0; }
void MeshClockSync::formatTable(char*, size_t) const { assert(false); }
void MeshClockSync::formatSample(int, char*, size_t) const { assert(false); }
@SHARED_METHODS@
}  // namespace mesh

class TestCallbacks : public mesh::MeshClockSyncCallbacks {
public:
  bool authoritative = false;
  unsigned adjustments = 0;
  bool hasAuthoritativeClock() const override { return authoritative; }
  void onMeshClockAdjusted(uint32_t, uint32_t) override { ++adjustments; }
};

class RoomCallbacks : public TestCallbacks {
public:
  FakeCLI& _cli;
  explicit RoomCallbacks(FakeCLI& cli) : _cli(cli) {}
  @ROOM_CALLBACK@
};

class SensorCallbacks : public TestCallbacks {
public:
  FakeCLI& _cli;
  explicit SensorCallbacks(FakeCLI& cli) : _cli(cli) {}
  @SENSOR_CALLBACK@
};

struct RepeaterFixture {
  repeater::MyMesh impl;
  FakeCLI& cli() { return impl._cli; }
  mesh::RTCClock& rtc() { return impl.rtc; }
  ClientACL& acl() { return impl.acl; }
  void now(uint64_t time) { impl.uptime_millis = time; impl.ms.now = uint32_t(time); }
  uint64_t uptime() const { return impl.uptime_millis; }
  uint64_t deadline() const { return impl.nextClockSyncAttemptUptime(); }
  uint64_t canonical() const { return impl.clock_sync_next_attempt_uptime; }
  bool complete() const { return impl.clock_sync_complete; }
  bool collection() const { return impl.isClockSyncCollectionActive(); }
  uint8_t result() const { return impl.clock_sync_last_result; }
  uint8_t suppression() const { return impl.clock_sync_mesh_suppressed_by; }
  void check() { impl.checkGpsClockSyncOverride(); impl.checkClockSync(); }
  void reset() { impl.resetClockSyncAttempt(); }
  bool success(uint32_t value) {
    return impl.applyClockEstimate(value, repeater::CLOCK_SYNC_SOURCE_MESH, 9);
  }
  void clearSamples() { memset(impl.clock_sync_samples, 0, sizeof(impl.clock_sync_samples)); }
  void record(unsigned id, uint32_t value) {
    uint8_t source[4] = {}; memcpy(source, &id, sizeof(id));
    mesh::Packet packet; memcpy(packet.path_id, &id, sizeof(id));
    impl.recordClockSyncSample(mesh::CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT, source, value, &packet);
  }
  void manualClock() { impl.onManualClockSet(); }
  void gpsClock() { impl.sensors.location.applied = true; check(); }
  void authoritative(bool active) { impl.mqtt.fresh = active; }
  void meshEnabled(bool enabled) { impl.clock_sync_mesh_enabled = enabled; }
  std::string status() const {
    char reply[256]; impl.formatClockSyncStatus("", reply, sizeof(reply)); return reply;
  }
  bool collectingResult() const { return result() == repeater::CLOCK_SYNC_RESULT_COLLECTING; }
  bool noConsensusResult() const { return result() == repeater::CLOCK_SYNC_RESULT_NO_CONSENSUS; }
};

template <class Callbacks>
struct SharedFixture {
  mesh::Radio radio;
  mesh::MillisecondClock ms;
  mesh::RTCClock clock;
  ClientACL clients;
  SensorManager sensors;
  float delay = 0;
  FakeCLI local_cli;
  Callbacks callbacks;
  mesh::MeshClockSync impl;
  SharedFixture() : callbacks(local_cli), impl(radio, ms, clock, clients, sensors, delay, &callbacks) {}
  FakeCLI& cli() { return local_cli; }
  mesh::RTCClock& rtc() { return clock; }
  ClientACL& acl() { return clients; }
  void now(uint64_t time) {
    impl._uptime_millis = time; ms.now = uint32_t(time); impl._last_millis = ms.now;
  }
  uint64_t uptime() const { return impl._uptime_millis; }
  uint64_t deadline() const { return impl.nextAttemptUptime(); }
  uint64_t canonical() const { return impl._next_attempt_uptime; }
  bool complete() const { return impl._complete; }
  bool collection() const { return impl.collectionActive(); }
  uint8_t suppression() const { return impl._suppressed_by; }
  void check() { impl.loop(); }
  void reset() { impl.resetAttempt(); }
  bool success(uint32_t value) { return impl.applyEstimate(value, 9); }
  void clearSamples() { memset(impl._samples, 0, sizeof(impl._samples)); }
  void record(unsigned id, uint32_t value) {
    uint8_t source[4] = {}; memcpy(source, &id, sizeof(id));
    mesh::Packet packet; memcpy(packet.path_id, &id, sizeof(id));
    impl.recordSample(mesh::CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT, source, value, &packet);
  }
  void manualClock() { impl.onManualClockSet(); }
  void gpsClock() { sensors.location.applied = true; check(); }
  void authoritative(bool active) { callbacks.authoritative = active; }
  void meshEnabled(bool enabled) { impl._mesh_enabled = enabled; }
  std::string status() const {
    char reply[256]; impl.formatStatus("", reply, sizeof(reply)); return reply;
  }
  bool collectingResult() const { return impl._last_result == mesh::MeshClockSync::RESULT_COLLECTING; }
  bool noConsensusResult() const { return impl._last_result == mesh::MeshClockSync::RESULT_NO_CONSENSUS; }
};

template <class Fixture>
static void emergency(Fixture& node, bool active) {
  node.cli().state.setManual(active, uint32_t(node.uptime()));
}

template <class Fixture>
static void quorum(Fixture& node, uint32_t epoch = Epoch, unsigned count = 9) {
  node.clearSamples();
  for (unsigned id = 1; id <= count; ++id) node.record(id, epoch);
}

template <class Fixture>
static void next(Fixture& node, uint64_t expected) {
  const auto status = node.status();
  const std::string field = "next=" + std::to_string(expected) + "s";
  if (status.find(field) == std::string::npos)
    fprintf(stderr, "expected %s in %s\n", field.c_str(), status.c_str());
  assert(status.find(field) != std::string::npos);
}

template <class Fixture>
static void transitions() {
  Fixture node;
  const uint64_t anchor = 52 * Day + 12345; // Extended uptime, after millis() wraps.
  node.now(anchor);
  assert(node.success(Epoch));
  assert(node.complete() && node.rtc().writes == 0);
  assert(node.canonical() == anchor + 7 * Day && node.deadline() == anchor + 7 * Day);
  node.now(anchor + Day);
  emergency(node, true);
  assert(node.deadline() == anchor + 3 * Day);
  assert(node.canonical() == anchor + 7 * Day);
  emergency(node, false);
  assert(node.deadline() == anchor + 7 * Day);
  // Automatic low battery selects the same active mode, with its existing hysteresis.
  node.cli().state.sample(uint32_t(node.uptime()), 3060, 5, true);
  assert(node.cli().emergencyActive() && node.deadline() == anchor + 3 * Day);
  node.cli().state.sample(uint32_t(node.uptime()), 3084, 7, true);
  assert(!node.cli().emergencyActive() && node.deadline() == anchor + 7 * Day);

  node.now(anchor + 4 * Day); // Entering after three days makes the check due now.
  emergency(node, true);
  assert(node.deadline() < node.uptime());
  next(node, 0);
  emergency(node, false); // Recovery before the next normal loop restores the normal deadline.
  node.check();
  assert(node.complete() && node.canonical() == anchor + 7 * Day);
  emergency(node, true);
  quorum(node);
  node.check();
  assert(node.complete());
  assert(node.deadline() == node.uptime() + 3 * Day);
  assert(node.canonical() == node.uptime() + 7 * Day);
  emergency(node, false);
  assert(node.deadline() == node.uptime() + 7 * Day);

  Fixture boot_in_emergency;
  emergency(boot_in_emergency, true);
  boot_in_emergency.now(Startup);
  quorum(boot_in_emergency);
  boot_in_emergency.check();
  assert(boot_in_emergency.complete());
  assert(boot_in_emergency.deadline() == Startup + 3 * Day);
  emergency(boot_in_emergency, false);
  assert(boot_in_emergency.deadline() == Startup + 7 * Day);
}

template <class Fixture>
static void consumers() {
  Fixture node;
  const uint64_t anchor = Startup + 4000;
  node.now(anchor);
  assert(node.success(Epoch));
  emergency(node, true);
  node.now(anchor + 3 * Day - Freshness - 1);
  assert(!node.collection());
  next(node, 7201);
  node.now(anchor + 3 * Day - Freshness);
  assert(node.collection());
  next(node, 7200);
  emergency(node, false);
  assert(!node.collection());
  next(node, 4 * 86400 + 7200);
  emergency(node, true);
  node.now(anchor + 3 * Day - 1);
  quorum(node);
  assert(node.canonical() == anchor + 7 * Day); // Collection cannot accelerate a completed wait.
  node.check();
  assert(node.complete() && node.canonical() == anchor + 7 * Day);
  next(node, 1);
  node.now(anchor + 3 * Day);
  next(node, 0);
  node.check();
  assert(node.complete() && node.canonical() == node.uptime() + 7 * Day);

  Fixture normal;
  normal.now(anchor);
  assert(normal.success(Epoch));
  normal.now(anchor + 7 * Day - Freshness - 1);
  assert(!normal.collection());
  normal.now(anchor + 7 * Day - Freshness);
  assert(normal.collection());
  normal.now(anchor + 7 * Day - 1);
  quorum(normal);
  normal.check();
  assert(normal.canonical() == anchor + 7 * Day);
  normal.now(anchor + 7 * Day);
  normal.check();
  assert(normal.canonical() == normal.uptime() + 7 * Day);
}

template <class Fixture>
static void retries() {
  Fixture node;
  emergency(node, true);
  assert(node.deadline() == Startup && node.collection());
  node.now(Startup - 1);
  node.check();
  assert(node.deadline() == Startup && !node.complete());
  node.now(Startup);
  node.check();
  assert(node.collectingResult() && node.deadline() == Startup + Retry);
  assert(!node.complete() && node.collection());
  emergency(node, false);
  assert(node.deadline() == Startup + Retry);
  emergency(node, true);
  assert(node.deadline() == Startup + Retry);
  node.now(Startup + Retry - 1);
  for (unsigned id = 1; id <= 8; ++id) node.record(id, Epoch);
  assert(node.deadline() == Startup + Retry);
  node.record(9, Epoch);
  assert(node.deadline() == node.uptime()); // A fresh quorum still bypasses a retry.
  node.check();
  assert(node.complete() && node.deadline() == node.uptime() + 3 * Day);

  Fixture early_quorum;
  emergency(early_quorum, true);
  early_quorum.now(10000);
  quorum(early_quorum);
  assert(early_quorum.deadline() == 10000);
  early_quorum.check();
  assert(early_quorum.complete());
  early_quorum.now(20000);
  early_quorum.reset();
  assert(!early_quorum.complete() && early_quorum.deadline() == Startup);
  early_quorum.now(Startup + 1);
  early_quorum.reset();
  assert(early_quorum.deadline() == early_quorum.uptime());
}

template <class Fixture>
static void consensus() {
  Fixture node;
  emergency(node, true);
  node.now(Startup);
  quorum(node, Epoch, 8);
  node.check();
  assert(!node.complete() && node.collectingResult() && node.rtc().writes == 0);
  assert(node.deadline() == node.uptime() + Retry);
  node.clearSamples();
  for (unsigned id = 1; id <= 16; ++id) node.record(id, id <= 8 ? Epoch : Epoch + 1800);
  node.check();
  assert(!node.complete() && node.noConsensusResult() && node.rtc().writes == 0);
  assert(node.deadline() == node.uptime() + Retry);

  quorum(node, Epoch);
  node.now(Startup + Freshness + 1);
  node.check();
  assert(!node.complete() && node.collectingResult()); // Stale votes cannot satisfy an emergency check.
  assert(!node.success(Epoch - 1));
  assert(!node.complete());
  quorum(node, Epoch + 600);
  node.check();
  assert(node.complete() && node.rtc().writes == 0 && node.rtc().resets == 0);
  assert(node.status().find("reason=within-drift") != std::string::npos);
  assert(node.deadline() == node.uptime() + 3 * Day);

  node.now(node.deadline());
  node.acl().client.last_activity = Epoch - 100;
  quorum(node, Epoch + 601);
  node.check();
  assert(node.complete() && node.rtc().writes == 1 && node.rtc().resets == 1);
  assert(node.rtc().epoch == Epoch + 601 && node.acl().client.last_activity == Epoch + 501);
  assert(node.status().find("reason=corrected-forward") != std::string::npos);
  node.now(node.deadline());
  quorum(node, Epoch);
  node.check();
  assert(node.complete() && node.rtc().writes == 2 && node.rtc().epoch == Epoch);
  assert(node.status().find("reason=corrected-backward") != std::string::npos);
}

template <class Fixture>
static void authority() {
  for (unsigned source : {1U, 2U, 3U}) {
    Fixture node;
    emergency(node, true);
    node.now(Startup);
    quorum(node, Epoch + 1000);
    if (source == 1) node.manualClock();
    else if (source == 2) node.gpsClock();
    else { node.authoritative(true); node.check(); }
    assert(node.suppression() == source && !node.collection());
    assert(node.rtc().writes == 0);
    node.reset();
    node.meshEnabled(false);
    node.meshEnabled(true);
    emergency(node, false);
    emergency(node, true);
    quorum(node, Epoch + 1000);
    node.check();
    assert(node.suppression() == source && node.rtc().writes == 0);
    assert(node.status().find("suppressed-") != std::string::npos);
  }

  Fixture racing_authority;
  emergency(racing_authority, true);
  racing_authority.authoritative(true);
  assert(racing_authority.success(Epoch + 1000));
  assert(racing_authority.suppression() == 3 && racing_authority.rtc().writes == 0);

  Fixture disabled;
  disabled.now(Startup);
  assert(disabled.success(Epoch));
  emergency(disabled, true);
  disabled.meshEnabled(false);
  assert(disabled.deadline() == Startup + 7 * Day && !disabled.collection());
  disabled.now(Startup + 3 * Day);
  disabled.check();
  assert(disabled.complete() && disabled.canonical() == Startup + 7 * Day);
  assert(disabled.status().find("mesh-off") != std::string::npos);
}

static void internet() {
  RepeaterFixture node;
  node.now(Startup);
  emergency(node, true);
  assert(node.impl.applyClockEstimate(Epoch, repeater::CLOCK_SYNC_SOURCE_INTERNET, 1));
  assert(node.deadline() == Startup + 7 * Day);
  node.impl.clock_sync_internet_enabled = true;
  node.impl.mqtt.running = true;
  node.now(Startup + 3 * Day);
  node.check();
  assert(node.impl.mqtt.requests == 0 && node.complete());

  RepeaterFixture mesh;
  mesh.now(Startup);
  emergency(mesh, true);
  assert(mesh.success(Epoch));
  mesh.impl.clock_sync_internet_enabled = true;
  mesh.impl.mqtt.running = true;
  mesh.now(mesh.deadline() - 1);
  quorum(mesh, Epoch + 1000);
  mesh.check();
  assert(mesh.impl.mqtt.requests == 0 && mesh.rtc().writes == 0);
  mesh.now(mesh.deadline());
  mesh.check();
  assert(mesh.impl.mqtt.requests == 1 && mesh.impl.clock_sync_internet_pending);
  assert(mesh.rtc().writes == 0); // The existing internet-first probe still wins at a mesh deadline.
  const uint64_t requested_at = mesh.uptime();
  assert(mesh.deadline() == requested_at);
  mesh.now(requested_at + 1000);
  mesh.check();
  assert(mesh.impl.clock_sync_internet_pending && mesh.impl.mqtt.requests == 1);
  mesh.impl.mqtt.finished = true;
  mesh.impl.mqtt.success = true;
  mesh.impl.mqtt.epoch = Epoch + 1001;
  mesh.check();
  assert(mesh.complete() && mesh.rtc().epoch == Epoch + 1001);
  assert(mesh.deadline() == mesh.uptime() + 7 * Day);

  RepeaterFixture timeout;
  timeout.now(Startup);
  emergency(timeout, true);
  assert(timeout.success(Epoch));
  timeout.impl.clock_sync_internet_enabled = true;
  timeout.impl.mqtt.running = true;
  timeout.now(timeout.deadline());
  quorum(timeout, Epoch + 1000);
  timeout.check();
  const uint64_t began = timeout.uptime();
  assert(timeout.impl.clock_sync_internet_pending && timeout.rtc().writes == 0);
  timeout.now(began + 29999);
  timeout.check();
  assert(timeout.impl.clock_sync_internet_pending && timeout.rtc().writes == 0);
  timeout.now(began + 30000);
  timeout.check();
  assert(!timeout.impl.clock_sync_internet_pending && timeout.complete());
  assert(timeout.rtc().epoch == Epoch + 1030);
  assert(timeout.deadline() == timeout.uptime() + 3 * Day);
}

static void callbacks() {
  mesh::MeshClockSyncCallbacks defaults;
  assert(!defaults.clockSyncEmergencyActive());
  SharedFixture<RoomCallbacks> node;
  emergency(node, true);
  node.now(Startup);
  node.impl._callbacks = &defaults;
  assert(node.success(Epoch));
  assert(node.deadline() == Startup + 7 * Day);
  node.impl._callbacks = nullptr;
  assert(node.deadline() == Startup + 7 * Day);
  node.impl._callbacks = &node.callbacks;
  assert(node.deadline() == Startup + 3 * Day);
}

template <class Fixture>
static void sleep() {
  using namespace mesh::emergencypower;
  static_assert(AwakeWindowMs == 900000UL, "Emergency awake window remains 15 minutes");
  static_assert(HourMs - AwakeWindowMs == 2700000UL, "Emergency sleep window remains 45 minutes");
  Fixture node;
  node.now(Startup);
  emergency(node, true);
  assert(node.success(Epoch));
  const uint32_t start = PlausibleEpoch;
  for (uint32_t hour : {0UL, 24UL, 72UL, 168UL}) {
    const uint32_t now = uint32_t(Startup + 5000 + hour * HourMs);
    assert(node.cli().state.sleepMilliseconds(now, start + hour * 3600 + 899) == 0);
    assert(node.cli().state.sleepMilliseconds(now, start + hour * 3600 + 900) == 2700000UL);
    assert(node.cli().state.sleepMilliseconds(now, start + hour * 3600 + 3599) == 1000UL);
    assert(node.cli().state.sleepMilliseconds(now, start + (hour + 1) * 3600) == 0);
  }
  assert(node.canonical() == Startup + 7 * Day && node.deadline() == Startup + 3 * Day);
}

template <class Fixture>
static void run(const std::string& scenario) {
  if (scenario == "transitions") transitions<Fixture>();
  else if (scenario == "consumers") consumers<Fixture>();
  else if (scenario == "retry") retries<Fixture>();
  else if (scenario == "consensus") consensus<Fixture>();
  else if (scenario == "authority") authority<Fixture>();
  else if (scenario == "sleep") sleep<Fixture>();
  else assert(false);
}

int main(int argc, char** argv) {
  assert(argc == 3);
  const std::string role = argv[1], scenario = argv[2];
  static_assert(mesh::CLOCK_SYNC_NORMAL_RESYNC_MILLIS == 7 * Day, "Normal LoRa cadence");
  static_assert(mesh::CLOCK_SYNC_EMERGENCY_RESYNC_MILLIS == 3 * Day, "Emergency LoRa cadence");
  assert(mesh::clockSyncEffectiveDeadline(0, true, true) == 0); // No unsigned underflow.
  if (scenario == "internet") internet();
  else if (scenario == "callbacks") callbacks();
  else if (role == "repeater") run<RepeaterFixture>(scenario);
  else if (role == "room") run<SharedFixture<RoomCallbacks>>(scenario);
  else if (role == "sensor") run<SharedFixture<SensorCallbacks>>(scenario);
  else assert(false);
}
