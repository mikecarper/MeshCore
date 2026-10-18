#include <gtest/gtest.h>
#include <Arduino.h>
#include <deque>
#include <string>
#include <utility>
#include <vector>

void yield() { ++g_mock_millis; }

// This includes the actual provider, LocationProvider and pinned native parser.
#include <helpers/sensors/MicroNMEALocationProvider.h>

void LocationProvider::sendSentence(const char*) {}

namespace {

constexpr uint32_t NOON_2026_10_17 = 1792238400UL;
constexpr uint32_t MIDNIGHT_2026_10_18 = 1792281600UL;

class GpsInput : public Stream {
public:
  std::deque<uint8_t> incoming;

  void receive(const std::string& text) {
    for (unsigned char c : text) incoming.push_back(c);
  }
  int available() override { return static_cast<int>(incoming.size()); }
  int read() override {
    if (incoming.empty()) return -1;
    const uint8_t c = incoming.front();
    incoming.pop_front();
    return c;
  }
};

class AdvancingRtc : public mesh::RTCClock {
  uint32_t _epoch = NOON_2026_10_17 - 400;
  uint32_t _at = 0;

public:
  struct Write {
    uint32_t at;
    uint32_t before;
    uint32_t after;
  };
  std::vector<Write> writes;

  uint32_t getCurrentTime() override {
    return _epoch + static_cast<uint32_t>(millis() - _at) / 1000;
  }
  void setCurrentTime(uint32_t epoch) override {
    writes.push_back({millis(), getCurrentTime(), epoch});
    _epoch = epoch;
    _at = millis();
  }
};

std::string nmea(const std::string& body) {
  const std::string line = "$" + body;
  char checksum[3] = {};
  MicroNMEA::generateChecksum(line.c_str(), checksum);
  const std::string result = line + "*" + checksum + "\r\n";
  EXPECT_TRUE(MicroNMEA::testChecksum(result.c_str()));
  return result;
}

std::string gga(const std::string& time) {
  return nmea("GPGGA," + time +
              ",4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,");
}

std::string rmc(const std::string& time,
                const std::string& date = "171026") {
  return nmea("GPRMC," + time +
              ",A,4807.038,N,01131.000,E,0.0,0.0," + date + ",,,A");
}

std::string pair(const std::string& time,
                 const std::string& date = "171026") {
  return gga(time) + rmc(time, date);
}

std::string noonPlus(unsigned seconds) {
  char time[16] = {};
  std::snprintf(time, sizeof(time), "%02u%02u%02u.00",
                12 + seconds / 3600, seconds / 60 % 60, seconds % 60);
  return time;
}

class GpsRig {
  bool _first = true;
  uint32_t _last_check = 0;

public:
  GpsInput uart;
  AdvancingRtc rtc;
  MicroNMEALocationProvider gps;

  explicit GpsRig(bool clock = true)
      : gps(uart, clock ? &rtc : nullptr, -1, -1) {
    resetArduinoMock();
    gps.begin();
  }

  uint32_t nextDeadline() const {
    return _first ? 0 : _last_check + 1000;
  }

  // Feed on (not after) the strict deadline so even a two-loop byte-budget
  // drain completes before the validation tick. No private provider access.
  void receiveWithoutCheck(const std::string& data) {
    g_mock_millis = nextDeadline();
    uart.receive(data);
    while (uart.available()) gps.loop();
  }

  void checkAt(uint32_t at) {
    g_mock_millis = at;
    gps.loop();
    _first = false;
    _last_check = at;
  }

  void window(const std::string& data = {}) {
    const uint32_t deadline = nextDeadline();
    receiveWithoutCheck(data);
    checkAt(deadline + 1);
  }

  void acquire() {
    window(pair("120000.00"));
    window(pair("120001.00"));
    window(pair("120002.00"));
  }

  void idleUntilPeriodicDue() {
    const uint32_t applied_at = rtc.writes.back().at;
    while (static_cast<uint32_t>(millis() - applied_at) < 1800000UL)
      window();
  }
};

}  // namespace

TEST(GpsClockFreshness, ScopedUtcAdapterAndPinnedParserHaveKnownReferenceValues) {
  EXPECT_STREQ("2.0.6", MICRONMEA_VERSION);
  EXPECT_EQ(NOON_2026_10_17, DateTime(2026, 10, 17, 12, 0, 0).unixtime());
  EXPECT_EQ(MIDNIGHT_2026_10_18, DateTime(2026, 10, 18, 0, 0, 0).unixtime());
  EXPECT_EQ(1709251200UL, DateTime(2024, 3, 1, 0, 0, 0).unixtime());
}

TEST(GpsClockFreshness, StartupSilenceCannotReuseOneObservationThreeTimes) {
  GpsRig rig;
  rig.window(pair("120000.00"));
  for (unsigned i = 0; i < 5; ++i) rig.window();
  EXPECT_TRUE(rig.rtc.writes.empty());
  EXPECT_TRUE(rig.gps.waitingTimeSync());
  EXPECT_EQ(0U, rig.gps.getTimeSyncGeneration());
  rig.window(pair("120006.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120007.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(NOON_2026_10_17 + 7, rig.rtc.writes.back().after);
}

TEST(GpsClockFreshness, ThreeOneHzObservationsApplyExactlyOnce) {
  GpsRig rig;
  rig.window(pair("120000.00"));
  rig.window(pair("120001.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120002.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(NOON_2026_10_17 + 2, rig.rtc.writes.back().after);
  EXPECT_EQ(1U, rig.gps.getTimeSyncGeneration());
  EXPECT_FALSE(rig.gps.waitingTimeSync());
  EXPECT_TRUE(rig.gps.consumeTimeSyncApplied());
  EXPECT_FALSE(rig.gps.consumeTimeSyncApplied());
  rig.window(pair("120003.00"));
  EXPECT_EQ(1U, rig.rtc.writes.size());
}

TEST(GpsClockFreshness, FiveSecondReceiverRetainsProgressThroughEmptyWindows) {
  GpsRig rig;
  rig.window(pair("120000.00"));
  for (unsigned i = 0; i < 4; ++i) rig.window();
  rig.window(pair("120005.00"));
  for (unsigned i = 0; i < 4; ++i) rig.window();
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120010.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(NOON_2026_10_17 + 10, rig.rtc.writes.back().after);
}

TEST(GpsClockFreshness, RepeatedUtcSecondsCannotValidateAStoppedReceiver) {
  GpsRig rig;
  for (unsigned i = 0; i < 6; ++i) rig.window(pair("120000.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  EXPECT_TRUE(rig.gps.waitingTimeSync());
  EXPECT_EQ(0U, rig.gps.getTimeSyncGeneration());
}

TEST(GpsClockFreshness, RmcOnlyClockDoesNotRequireGgaSatelliteCount) {
  GpsRig rig;
  for (unsigned second = 0; second < 3; ++second) {
    rig.window(rmc(noonPlus(second)));
    EXPECT_EQ(0, rig.gps.satellitesCount());
    if (second < 2) {
      EXPECT_TRUE(rig.rtc.writes.empty());
    }
  }
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(NOON_2026_10_17 + 2, rig.rtc.writes.back().after);
  EXPECT_EQ(1U, rig.gps.getTimeSyncGeneration());
}

TEST(GpsClockFreshness, StartupOutlierRestartsAgreementBeforeApplyingClock) {
  for (unsigned outlier : {0U, 1U, 2U}) {
    SCOPED_TRACE(outlier);
    GpsRig rig;
    for (unsigned second = 0; second < 3; ++second) {
      rig.window(pair(noonPlus(second), second == outlier ? "171027" : "171026"));
      EXPECT_TRUE(rig.rtc.writes.empty());
      EXPECT_EQ(0U, rig.gps.getTimeSyncGeneration());
    }
    // Every outlier position leaves fewer than three agreeing samples. A
    // complete new run must recover without ever writing the outlier's year.
    for (unsigned second = 3; second <= 5; ++second) {
      rig.window(pair(noonPlus(second)));
      EXPECT_EQ(second >= outlier + 3 ? 1U : 0U, rig.rtc.writes.size());
    }
    ASSERT_EQ(1U, rig.rtc.writes.size());
    EXPECT_EQ(NOON_2026_10_17 + outlier + 3, rig.rtc.writes.back().after);
  }
}

TEST(GpsClockFreshness, MultipleQueuedRmcSentencesCountOnlyOneValidation) {
  GpsRig rig;
  rig.window(gga("120000.00") + rmc("120000.00") +
             rmc("120001.00") + rmc("120002.00"));
  rig.window();
  rig.window();
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120005.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120006.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(NOON_2026_10_17 + 6, rig.rtc.writes.back().after);
}

TEST(GpsClockFreshness, PeriodicSilenceCannotRewindAdvancingRtc) {
  GpsRig rig;
  rig.acquire();
  ASSERT_EQ(1U, rig.rtc.writes.size());
  rig.idleUntilPeriodicDue();
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_GE(rig.rtc.getCurrentTime(), NOON_2026_10_17 + 1802);
  EXPECT_TRUE(rig.gps.waitingTimeSync());
  // A warm receiver remains validated; one new observation is sufficient.
  rig.window(pair("123003.00"));
  ASSERT_EQ(2U, rig.rtc.writes.size());
  EXPECT_EQ(NOON_2026_10_17 + 1803, rig.rtc.writes.back().after);
  EXPECT_EQ(2U, rig.gps.getTimeSyncGeneration());
}

TEST(GpsClockFreshness, MidIntervalObservationIsConsumedBeforePeriodicDeadline) {
  GpsRig rig;
  rig.acquire();
  ASSERT_EQ(1U, rig.rtc.writes.size());
  rig.window(pair("120003.00"));
  rig.idleUntilPeriodicDue();
  EXPECT_EQ(1U, rig.rtc.writes.size());
}

TEST(GpsClockFreshness, CandidateCannotSurviveThirtyMinuteLoopPause) {
  GpsRig rig;
  rig.acquire();
  ASSERT_EQ(1U, rig.rtc.writes.size());
  rig.receiveWithoutCheck(pair("120003.00"));
  const uint32_t observed_at = millis();
  rig.checkAt(observed_at + 1800001UL);
  EXPECT_EQ(1U, rig.rtc.writes.size());
  EXPECT_TRUE(rig.gps.waitingTimeSync());
  rig.window(pair("123004.00"));
  ASSERT_EQ(2U, rig.rtc.writes.size());
  EXPECT_EQ(NOON_2026_10_17 + 1804, rig.rtc.writes.back().after);
}

TEST(GpsClockFreshness, ExpiredCandidateCannotReappearAfterAFullMillisCycle) {
  GpsRig rig;
  rig.acquire();
  ASSERT_EQ(1U, rig.rtc.writes.size());
  rig.receiveWithoutCheck(pair("120003.00"));
  const uint32_t observed_at = millis();
  // Three forward steps below INT32_MAX model one complete uint32 millis
  // cycle, without billions of polling calls. The first poll expires and
  // consumes the old candidate while making periodic synchronization due.
  rig.checkAt(observed_at + 0x70000000UL);
  rig.checkAt(observed_at + 0xe0000000UL);
  rig.checkAt(observed_at + 1001UL);
  EXPECT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(1U, rig.gps.getTimeSyncGeneration());
  EXPECT_TRUE(rig.gps.waitingTimeSync());
}

TEST(GpsClockFreshness, CandidateAgeAllowsTwoSecondsButNotAnOlderObservation) {
  for (uint32_t age : {2000U, 2001U}) {
    SCOPED_TRACE(age);
    GpsRig rig;
    rig.window(pair("120000.00"));
    rig.window(pair("120001.00"));
    rig.receiveWithoutCheck(pair("120002.00"));
    const uint32_t observed_at = millis();
    rig.checkAt(observed_at + age);
    EXPECT_EQ(age == 2000 ? 1U : 0U, rig.rtc.writes.size());
    if (age == 2001) {
      rig.window(pair("120005.00"));
      ASSERT_EQ(1U, rig.rtc.writes.size());
      EXPECT_EQ(NOON_2026_10_17 + 5, rig.rtc.writes.back().after);
    }
  }
}

TEST(GpsClockFreshness, FreshGgaAfterMidnightCannotAuthorizeYesterdayDate) {
  GpsRig rig;
  rig.window(pair("235956.00"));
  rig.window(pair("235957.00"));
  rig.window(pair("235958.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(MIDNIGHT_2026_10_18 - 2, rig.rtc.writes.back().after);
  rig.idleUntilPeriodicDue();
  rig.window(gga("002958.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_GE(rig.rtc.getCurrentTime(), MIDNIGHT_2026_10_18 + 1798);
  rig.window(pair("003000.00", "181026"));
  ASSERT_EQ(2U, rig.rtc.writes.size());
  EXPECT_EQ(MIDNIGHT_2026_10_18 + 1800, rig.rtc.writes.back().after);
}

TEST(GpsClockFreshness, RmcCandidateIsNotChangedByLaterGgaInSameWindow) {
  GpsRig rig;
  rig.window(pair("235956.00"));
  rig.window(pair("235957.00"));
  // The complete RMC carries the 17th, followed by GGA from the 18th. The
  // parser's live timestamp is now hybrid, but the captured candidate is not.
  rig.window(rmc("235958.00") + gga("000000.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(MIDNIGHT_2026_10_18 - 2, rig.rtc.writes.back().after);
}

TEST(GpsClockFreshness, BadChecksumAndInvalidCalendarDatesCannotCount) {
  for (bool bad_checksum : {false, true}) {
    SCOPED_TRACE(bad_checksum);
    GpsRig rig;
    for (unsigned i = 0; i < 3; ++i) {
      std::string observation = rmc("120000.00", bad_checksum ? "171026" : "290226");
      if (bad_checksum) {
        const size_t checksum = observation.find('*') + 1;
        observation[checksum] = observation[checksum] == '0' ? '1' : '0';
        ASSERT_FALSE(MicroNMEA::testChecksum(observation.c_str()));
      }
      rig.window(gga("120000.00") + observation);
    }
    EXPECT_TRUE(rig.rtc.writes.empty());
    rig.acquire();
    ASSERT_EQ(1U, rig.rtc.writes.size());
    EXPECT_EQ(NOON_2026_10_17 + 2, rig.rtc.writes.back().after);
  }
}

TEST(GpsClockFreshness, TrackerRequiresFreshObservationsInConsecutiveWindows) {
  GpsRig rig;
  rig.gps.beginFreshTimeSync();
  rig.window(pair("120000.00"));
  rig.window(pair("120001.00"));
  rig.window();
  rig.window(pair("120003.00"));
  rig.window(pair("120004.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120005.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(NOON_2026_10_17 + 5, rig.rtc.writes.back().after);
}

TEST(GpsClockFreshness, TrackerRepeatedEpochBreaksConsecutiveValidation) {
  GpsRig rig;
  rig.gps.beginFreshTimeSync();
  rig.window(pair("120000.00"));
  rig.window(pair("120001.00"));
  // A checksum-valid repeat is not an advancing clock observation, even when
  // its UART arrival is recent. Tracker acquisition must restart its run.
  rig.window(pair("120001.00"));
  rig.window(pair("120003.00"));
  rig.window(pair("120004.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120005.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(NOON_2026_10_17 + 5, rig.rtc.writes.back().after);
}

TEST(GpsClockFreshness, ManualSyncResetsCountAndUnconsumedCandidate) {
  GpsRig rig;
  rig.window(pair("120000.00"));
  rig.window(pair("120001.00"));
  rig.receiveWithoutCheck(pair("120002.00"));
  rig.gps.syncTime();
  rig.window();
  rig.window(pair("120004.00"));
  rig.window(pair("120005.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120006.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_EQ(NOON_2026_10_17 + 6, rig.rtc.writes.back().after);
}

TEST(GpsClockFreshness, ClearPositionResetsCountCandidateAndQueuedInput) {
  GpsRig rig;
  rig.window(pair("120000.00"));
  rig.window(pair("120001.00"));
  rig.receiveWithoutCheck(pair("120002.00"));
  rig.uart.receive(pair("120003.00"));
  ASSERT_TRUE(rig.gps.clearPositionFix());
  EXPECT_EQ(0, rig.uart.available());
  rig.window();
  rig.window(pair("120004.00"));
  rig.window(pair("120005.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120006.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
}

TEST(GpsClockFreshness, FreshRequestDiscardsEarlierNormalValidationProgress) {
  GpsRig rig;
  rig.window(pair("120000.00"));
  rig.window(pair("120001.00"));
  rig.gps.beginFreshTimeSync();
  // beginFreshTimeSync reanchors next_check to the current millisecond. Check
  // that newly pending request once, then resume the regular strict cadence.
  rig.checkAt(millis() + 1);
  rig.window(pair("120003.00"));
  rig.window(pair("120004.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120005.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
}

TEST(GpsClockFreshness, NullClockNeverReportsAnAppliedSync) {
  GpsRig rig(false);
  rig.acquire();
  for (unsigned i = 0; i < 10; ++i) rig.window(pair("120003.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  EXPECT_EQ(0U, rig.gps.getTimeSyncGeneration());
  EXPECT_FALSE(rig.gps.consumeTimeSyncApplied());
  EXPECT_TRUE(rig.gps.waitingTimeSync());
}

TEST(GpsClockFreshness, WarmValidationHistorySaturatesInsteadOfWrapping) {
  GpsRig rig;
  rig.acquire();
  ASSERT_EQ(1U, rig.rtc.writes.size());
  // Three initial observations plus 253 more would wrap an unsaturated byte.
  for (unsigned i = 0; i < 253; ++i) rig.window(pair(noonPlus(3 + i)));
  rig.idleUntilPeriodicDue();
  EXPECT_EQ(1U, rig.rtc.writes.size());
  rig.window(pair("123003.00"));
  ASSERT_EQ(2U, rig.rtc.writes.size());
}

TEST(GpsClockFreshness, TrackerValidationCadenceSurvivesMillisRollover) {
  GpsRig rig;
  g_mock_millis = UINT32_MAX - 3000;
  rig.gps.beginFreshTimeSync();
  rig.checkAt(millis() + 1);
  rig.window(pair("120000.00"));
  rig.window(pair("120001.00"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  rig.window(pair("120002.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  EXPECT_LT(millis(), 1000U);
  EXPECT_EQ(NOON_2026_10_17 + 2, rig.rtc.writes.back().after);
}

TEST(GpsClockFreshness, CandidateAgeLimitIsMeasuredAcrossMillisRollover) {
  for (uint32_t age : {2000U, 2001U}) {
    SCOPED_TRACE(age);
    GpsRig rig;
    g_mock_millis = UINT32_MAX - 4000;
    rig.gps.beginFreshTimeSync();
    rig.checkAt(millis() + 1);
    rig.window(pair("120000.00"));
    rig.window(pair("120001.00"));
    rig.receiveWithoutCheck(pair("120002.00"));
    const uint32_t observed_at = millis();
    ASSERT_GT(observed_at, UINT32_MAX - 1000);
    rig.checkAt(observed_at + age);
    EXPECT_LT(millis(), 2000U);
    EXPECT_EQ(age == 2000 ? 1U : 0U, rig.rtc.writes.size());
  }
}

TEST(GpsClockFreshness, MissingRmcDateCannotAuthorizeRetainedPreviousDay) {
  GpsRig rig;
  rig.window(pair("235956.00"));
  rig.window(pair("235957.00"));
  rig.window(pair("235958.00"));
  ASSERT_EQ(1U, rig.rtc.writes.size());
  rig.idleUntilPeriodicDue();
  // Checksum and new time are valid, but MicroNMEA retains the 17th's date.
  rig.window(rmc("002958.00", ""));
  EXPECT_EQ(1U, rig.rtc.writes.size());
  EXPECT_GE(rig.rtc.getCurrentTime(), MIDNIGHT_2026_10_18 + 1798);
}

TEST(GpsClockFreshness, MissingRmcTimeCannotCountRetainedTimeWithNewDate) {
  GpsRig rig;
  rig.window(pair("120000.00"));
  rig.window(pair("120001.00"));
  // A new date alone must not reclassify the previous time as an observation.
  rig.window(rmc("", "181026"));
  EXPECT_TRUE(rig.rtc.writes.empty());
  EXPECT_EQ(0U, rig.gps.getTimeSyncGeneration());
}

TEST(GpsClockFreshness, PartialOrNonNumericRmcClockFieldsCannotCount) {
  const std::vector<std::pair<std::string, std::string>> malformed = {
      {"12000", "171026"}, {"12", "171026"}, {"1", "171026"},
      {"120002.00", "17102"}, {"120002.00", "17"},
      {"120002.00", "1"}, {"120002.", "171026"},
      {"120002.0x", "171026"}, {"120002.00", "171026x"}};
  for (const auto& fields : malformed) {
    SCOPED_TRACE(fields.first + "/" + fields.second);
    GpsRig rig;
    rig.window(pair("120000.00"));
    rig.window(pair("120001.00"));
    rig.window(rmc(fields.first, fields.second));
    EXPECT_TRUE(rig.rtc.writes.empty());
    EXPECT_EQ(0U, rig.gps.getTimeSyncGeneration());
  }
}

TEST(GpsClockFreshness, CompleteRmcTimeAllowsIntegerAndDecimalSeconds) {
  for (const char* time : {"120002", "120002.0", "120002.00",
                           "120002.000", "120002.123456"}) {
    SCOPED_TRACE(time);
    GpsRig rig;
    rig.window(pair("120000.00"));
    rig.window(pair("120001.00"));
    rig.window(gga("120002.00") + rmc(time));
    EXPECT_EQ(1U, rig.rtc.writes.size());
    if (rig.rtc.writes.size() != 1) continue;
    EXPECT_EQ(NOON_2026_10_17 + 2, rig.rtc.writes.back().after);
  }
}

TEST(GpsClockFreshness, GgaAndRmcTimePrecisionPreservesFixAndClockAcquisition) {
  for (const char* fraction : {"", ".0", ".00", ".000", ".123456"}) {
    SCOPED_TRACE(fraction);
    GpsRig rig;
    rig.window(pair(std::string("120000") + fraction));
    ASSERT_TRUE(rig.gps.isValid());
    EXPECT_EQ(8, rig.gps.satellitesCount());
    EXPECT_EQ(48117300L, rig.gps.getLatitude());
    EXPECT_EQ(11516666L, rig.gps.getLongitude());
    EXPECT_EQ(545400L, rig.gps.getAltitude());
    rig.window(pair(std::string("120001") + fraction));
    EXPECT_TRUE(rig.rtc.writes.empty());
    rig.window(pair(std::string("120002") + fraction));
    ASSERT_EQ(1U, rig.rtc.writes.size());
    EXPECT_EQ(NOON_2026_10_17 + 2, rig.rtc.writes.back().after);
    EXPECT_EQ(1U, rig.gps.getTimeSyncGeneration());
  }
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
