// Compile the actual provider/base policy. Only GNSS, clock and Arduino I/O
// are adapters. This is not a full EnvironmentSensorManager or I2C test.
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <helpers/sensors/LocationProvider.h>

void LocationProvider::sendSentence(const char*) {}

struct Pvt {
  bool fix = true, date = true, time = true;
  int sats = 8;
  long lat = 476100000, lng = -1223300000, alt = 125000, epoch = 1792195200;
};

// SparkFun 2.2.x flushPVT clears unread bits, not the PVT payload. Each scalar
// getter implicitly polls if its bit is clear, ignores failure, then returns
// the retained payload. Only the eight fields consumed by this provider are
// modeled (UnixEpoch's calendar conversion/parser are outside this boundary).
struct Gnss {
  enum Field { FIX, LAT, LNG, ALT, SATS, DATE, TIME, EPOCH, FIELDS };
  Pvt incoming, cached{false, false, false, 0, 0, 0, 0, 0};
  bool available = true;
  uint8_t unread = 0;
  unsigned polls = 0, received = 0, flushes = 0;
  std::array<unsigned, FIELDS> reads{};
  std::vector<uint16_t> waits;
  void flushPVT() { ++flushes; unread = 0; }
  bool getPVT(uint16_t max_wait) {
    ++polls;
    waits.push_back(max_wait);
    if (!available) return false;
    ++received;
    cached = incoming;
    unread = 0xff;
    return true;
  }
  void access(Field field, uint16_t max_wait) {
    ++reads[field];
    const uint8_t mask = static_cast<uint8_t>(1U << field);
    if (!(unread & mask)) (void)getPVT(max_wait);
    unread &= static_cast<uint8_t>(~mask);
  }
  bool getGnssFixOk(uint16_t wait) { access(FIX, wait); return cached.fix; }
  long getLatitude(uint16_t wait) { access(LAT, wait); return cached.lat; }
  long getLongitude(uint16_t wait) { access(LNG, wait); return cached.lng; }
  long getAltitude(uint16_t wait) { access(ALT, wait); return cached.alt; }
  int getSIV(uint16_t wait) { access(SATS, wait); return cached.sats; }
  bool getDateValid(uint16_t wait) { access(DATE, wait); return cached.date; }
  bool getTimeValid(uint16_t wait) { access(TIME, wait); return cached.time; }
  long getUnixEpoch(uint16_t wait) { access(EPOCH, wait); return cached.epoch; }
} ublox_GNSS;

@PROVIDER@

static const char* scenario = "";
static void check(bool value, const char* diagnostic, int line) {
  if (value) return;
  std::fprintf(stderr, "FAIL %s: %s (line %d)\n", scenario, diagnostic, line);
  std::exit(1);
}
#define CHECK(value, diagnostic) check((value), (diagnostic), __LINE__)

// A due loop must perform exactly one bounded PVT poll. Successful cache
// getters must not cause hidden additional polls; failed polls read no fields.
static void poll(RAK12500LocationProvider& gps, uint32_t at) {
  const auto polls = ublox_GNSS.polls, flushes = ublox_GNSS.flushes;
  const auto reads = ublox_GNSS.reads;
  now_ms = at;
  gps.loop();
  CHECK(ublox_GNSS.polls == polls + 1 && ublox_GNSS.waits.back() == 8,
        "due loop must use exactly one eight-millisecond PVT poll");
  CHECK(ublox_GNSS.flushes == flushes + 1, "due loop must discard unread status before polling");
  for (unsigned field = 0; field < Gnss::FIELDS; ++field) {
    const bool position = field >= Gnss::LAT && field <= Gnss::SATS;
    const unsigned count = ublox_GNSS.available && (!position || ublox_GNSS.incoming.fix);
    CHECK(ublox_GNSS.reads[field] == reads[field] + count,
          "PVT cache getter access budget changed");
  }
}

static void good(RAK12500LocationProvider& gps, uint32_t at) {
  ublox_GNSS.available = true;
  ublox_GNSS.incoming.epoch = 1792195200 + at / 1000;
  poll(gps, at);
}

static void qualifiedThree(RAK12500LocationProvider& gps, uint32_t first) {
  for (unsigned i = 0; i < 3; ++i) good(gps, first + i * 1000U);
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  scenario = argv[1];
  mesh::RTCClock clock;
  RAK12500LocationProvider gps;
  gps.setRTCClock(&clock);
  if (!std::strcmp(scenario, "startup-silence")) {
    // Put the clock oracle before budget/fix assertions so the old normal
    // path fails for counting retained timestamps, not an incidental metric.
    now_ms = 1; gps.loop();
    CHECK(gps.isValid() && !clock.writes && ublox_GNSS.received == 1,
          "one new startup PVT must update position without setting clock");
    ublox_GNSS.available = false;
    now_ms = 1001; gps.loop(); now_ms = 2001; gps.loop();
    CHECK(clock.writes == 0, "stale PVT must not update the clock");
    CHECK(!gps.isValid() && gps.waitingTimeSync() && !gps.getTimeSyncGeneration(),
          "failed startup polls must invalidate fix without claiming time sync");
    CHECK(ublox_GNSS.received == 1 && ublox_GNSS.polls == 3
          && ublox_GNSS.flushes == 3, "startup failure poll budget changed");
    qualifiedThree(gps, 3001);
    CHECK(clock.writes == 1 && gps.getTimeSyncGeneration() == 1,
          "three new PVTs must recover startup time acquisition");
  } else if (!std::strcmp(scenario, "periodic-silence")) {
    for (unsigned i = 0; i < 3; ++i) {
      now_ms = 1 + i * 1000; ublox_GNSS.incoming.epoch += i; gps.loop();
    }
    CHECK(clock.writes == 1 && !gps.waitingTimeSync(), "initial three-PVT sync failed");
    CHECK(gps.consumeTimeSyncApplied() && !gps.consumeTimeSyncApplied(),
          "applied notification must be an edge");
    const auto synced = clock.getCurrentTime();
    const uint32_t due = now_ms + gps.periodicTimeSyncIntervalMillis();
    ublox_GNSS.available = false;
    now_ms = due; gps.loop();
    CHECK(clock.writes == 1, "stale PVT must not update the clock");
    CHECK(clock.getCurrentTime() == synced + 1800 && gps.getTimeSyncGeneration() == 1
          && !gps.consumeTimeSyncApplied(), "failed periodic poll must not rewind RTC or emit sync");
    CHECK(!gps.isValid() && ublox_GNSS.received == 3 && ublox_GNSS.polls == 4,
          "failed periodic acquisition must retain only old cache, not a fix");
    good(gps, due + 1000); good(gps, due + 2000);
    CHECK(clock.writes == 1, "periodic recovery must require three new PVTs after failure");
    good(gps, due + 3000);
    CHECK(clock.writes == 2 && gps.getTimeSyncGeneration() == 2,
          "fresh periodic acquisition must eventually update RTC");
    CHECK(clock.getCurrentTime() == static_cast<uint32_t>(gps.getTimestamp()),
          "periodic recovery wrote a timestamp other than the new PVT");
  } else if (!std::strcmp(scenario, "consecutive-failure")) {
    good(gps, 1); good(gps, 1001);
    CHECK(!clock.writes, "two samples must not set the clock");
    ublox_GNSS.available = false; poll(gps, 2001);
    good(gps, 3001); good(gps, 4001);
    CHECK(!clock.writes, "poll failure must reset consecutive fresh-sample count");
    good(gps, 5001);
    CHECK(clock.writes == 1 && gps.isValid(), "third post-failure fresh PVT must restore sync");
  } else if (!std::strcmp(scenario, "poll-budget")) {
    good(gps, 1);
    const auto reads = ublox_GNSS.reads;
    for (uint32_t at : {1U, 2U, 500U, 1000U}) { now_ms = at; gps.loop(); }
    CHECK(ublox_GNSS.polls == 1 && ublox_GNSS.flushes == 1 && ublox_GNSS.reads == reads,
          "subsecond service must not poll or reread receiver cache");
    good(gps, 1001);
    const long lat = gps.getLatitude(), lng = gps.getLongitude(), alt = gps.getAltitude();
    const long sats = gps.satellitesCount(), epoch = gps.getTimestamp();
    ublox_GNSS.available = false; poll(gps, 2001);
    CHECK(!gps.isValid() && !clock.writes, "failed poll must not count retained navigation data");
    const auto polls = ublox_GNSS.polls;
    CHECK(gps.getLatitude() == lat && gps.getLongitude() == lng && gps.getAltitude() == alt
          && gps.satellitesCount() == sats && gps.getTimestamp() == epoch,
          "failure must preserve scalar cache for explicitly invalid position");
    CHECK(ublox_GNSS.polls == polls, "provider cache getters must not perform I2C polls");
  } else if (!std::strcmp(scenario, "position-request")) {
    good(gps, 1);
    CHECK(gps.clearPositionFix() && !gps.isValid(), "position request must invalidate old fix");
    ublox_GNSS.available = false; poll(gps, 2);
    CHECK(!gps.isValid() && !clock.writes, "position-only request cannot reuse retained fix");
    qualifiedThree(gps, 1002);
    CHECK(gps.isValid() && clock.writes == 1, "position-only request must recover with fresh PVTs");
  } else if (!std::strcmp(scenario, "fresh-tracker")) {
    gps.beginFreshTimeSync();
    ublox_GNSS.available = false; poll(gps, 1); poll(gps, 1001);
    CHECK(!gps.isValid() && !clock.writes && gps.waitingTimeSync(),
          "fresh tracker request cannot use failed polls");
    good(gps, 2001); good(gps, 3001);
    CHECK(!clock.writes, "tracker clock must wait for three fresh samples");
    good(gps, 4001);
    CHECK(clock.writes == 1 && gps.getTimeSyncGeneration() == 1 && gps.consumeTimeSyncApplied(),
          "fresh tracker request must finish with one applied edge");
    gps.beginFreshTimeSync();
    ublox_GNSS.available = false; poll(gps, 5001);
    CHECK(!gps.isValid() && clock.writes == 1 && gps.getTimeSyncGeneration() == 1,
          "a second tracker request must not inherit its previous valid cache");
    qualifiedThree(gps, 6001);
    CHECK(clock.writes == 2 && gps.getTimeSyncGeneration() == 2
          && gps.consumeTimeSyncApplied() && !gps.consumeTimeSyncApplied(),
          "second tracker request must require three new samples and emit one edge");
    gps.endFreshTimeSync();
  } else if (!std::strcmp(scenario, "reset")) {
    qualifiedThree(gps, 1);
    CHECK(clock.writes == 1, "reset control needs a previous applied fix");
    gps.beginFreshTimeSync(); gps.clearPositionFix(); gps.reset();
    CHECK(!gps.isValid() && !gps.getLatitude() && !gps.getLongitude() && !gps.getAltitude()
          && !gps.satellitesCount() && !gps.getTimestamp() && !gps.getLastValidTimeSync()
          && gps.waitingTimeSync() && !gps.consumeTimeSyncApplied(),
          "reset must clear public fix/time state before reselecting the receiver");
    ublox_GNSS.available = false; poll(gps, 2002);
    CHECK(clock.writes == 1 && !gps.isValid() && !gps.getTimestamp(),
          "reset provider must not republish retained receiver cache after failed poll");
    good(gps, 3002); good(gps, 4002);
    CHECK(clock.writes == 1, "reset must discard the previous consecutive sample count");
    good(gps, 5002);
    CHECK(clock.writes == 2 && gps.getTimeSyncGeneration() == 2,
          "reset must allow a newly qualified sync without reusing an applied edge");
  } else if (!std::strcmp(scenario, "null-clock")) {
    gps.setRTCClock(nullptr);
    qualifiedThree(gps, 1);
    CHECK(gps.isValid() && gps.getTimestamp() > 0 && !clock.writes
          && gps.waitingTimeSync() && !gps.getTimeSyncGeneration() && !gps.consumeTimeSyncApplied(),
          "clockless location acquisition must not fabricate a time-sync result");
    ublox_GNSS.available = false; poll(gps, 3001);
    CHECK(!gps.isValid(), "clockless failed poll must also invalidate fix");
  } else if (!std::strncmp(scenario, "invalid-", 8)) {
    good(gps, 1); good(gps, 1001);
    const char* field = scenario + 8;
    if (!std::strcmp(field, "fix")) ublox_GNSS.incoming.fix = false;
    else if (!std::strcmp(field, "date")) ublox_GNSS.incoming.date = false;
    else if (!std::strcmp(field, "time")) ublox_GNSS.incoming.time = false;
    else if (!std::strcmp(field, "sats")) ublox_GNSS.incoming.sats = 4;
    else if (!std::strcmp(field, "epoch")) ublox_GNSS.incoming.epoch = 0;
    else return 2;
    poll(gps, 2001);
    CHECK(!clock.writes, "unqualified fresh PVT must not finish time acquisition");
    if (!std::strcmp(field, "fix")) CHECK(!gps.isValid(), "no-nav PVT must invalidate position");
    ublox_GNSS.incoming = Pvt{};
    good(gps, 3001); good(gps, 4001);
    CHECK(!clock.writes, "unqualified PVT must reset consecutive time samples");
    good(gps, 5001);
    CHECK(clock.writes == 1 && gps.isValid(), "three newly qualified PVTs must restore time sync");
  } else if (!std::strcmp(scenario, "boundary-contract")) {
    CHECK(ublox_GNSS.getPVT(8), "boundary needs a received PVT");
    const auto lat = ublox_GNSS.cached.lat, epoch = ublox_GNSS.cached.epoch;
    ublox_GNSS.flushPVT(); ublox_GNSS.available = false;
    CHECK(!ublox_GNSS.getPVT(8) && ublox_GNSS.cached.lat == lat,
          "failed explicit poll must retain receiver payload");
    CHECK(ublox_GNSS.getLatitude(2) == lat && ublox_GNSS.getUnixEpoch(2) == epoch
          && ublox_GNSS.polls == 4 && ublox_GNSS.received == 1,
          "failed implicit getters must still return retained payload");
  } else return 2;
  std::printf("PASS %s\n", scenario);
}
