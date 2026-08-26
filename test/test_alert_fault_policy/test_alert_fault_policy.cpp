#include <gtest/gtest.h>

#include <atomic>
#include <limits>

#include "helpers/AlertFaultPolicy.h"

namespace Alert = AlertFaultPolicy;

namespace {

Alert::Fault okFault() {
  Alert::Fault fault{};
  fault.state = Alert::State::OK;
  return fault;
}

Alert::OutageSnapshot down(uint32_t start, uint8_t reason = 0) {
  Alert::OutageSnapshot snapshot{};
  snapshot.down = true;
  snapshot.started_ms = start;
  snapshot.reason = reason;
  return snapshot;
}

}  // namespace

TEST(AlertFaultPolicy, PreservesFirstOutageReasonAcrossReconnectAttempts) {
  Alert::OutageSnapshot snapshot{};
  snapshot = Alert::applyWifiDisconnectEvent(1000, 200, snapshot);
  snapshot = Alert::applyWifiDisconnectEvent(16000, 8, snapshot);
  EXPECT_TRUE(snapshot.down);
  EXPECT_EQ(1000U, snapshot.started_ms);
  EXPECT_EQ(200U, snapshot.reason);
}

TEST(AlertFaultPolicy, GotIpClosesAnOutageBetweenStatusPolls) {
  Alert::OutageSnapshot snapshot = down(1000, 200);
  snapshot = Alert::applyWifiGotIp(snapshot);
  EXPECT_FALSE(snapshot.down);
  EXPECT_EQ(0U, snapshot.started_ms);
  EXPECT_EQ(0U, snapshot.reason);
}

TEST(AlertFaultPolicy, PackedSnapshotRepresentsOutageAtMillisZero) {
  std::atomic<uint64_t> value{0};
  value.store(Alert::packOutageSnapshot(down(0, 200)),
              std::memory_order_release);
  Alert::OutageSnapshot snapshot = Alert::unpackOutageSnapshot(
      value.load(std::memory_order_acquire));
  EXPECT_TRUE(snapshot.down);
  EXPECT_EQ(0U, snapshot.started_ms);
  EXPECT_EQ(200U, snapshot.reason);
}

TEST(AlertFaultPolicy, FiresAtThresholdAndFormatsInitiatingReason) {
  Alert::Fault fault = okFault();
  const uint32_t threshold = Alert::thresholdMs(30);
  Alert::OutageSnapshot snapshot = down(1000, 200);
  Alert::TickResult result = Alert::tick(
      fault, 1000 + threshold, snapshot, threshold,
      Alert::minIntervalMs(60));
  EXPECT_EQ(Alert::Action::FireDown, result.action);

  char text[80];
  ASSERT_TRUE(Alert::formatWifiAlert(text, sizeof(text), result, snapshot));
  EXPECT_STREQ("WiFi down 30m (reason 200)", text);
}

TEST(AlertFaultPolicy, RecoveryUsesRememberedOutageStart) {
  Alert::Fault fault = okFault();
  Alert::commitDown(fault, 1801000U, 1000U);
  Alert::OutageSnapshot up{};
  Alert::TickResult result = Alert::tick(
      fault, 7501000U, up, Alert::thresholdMs(30),
      Alert::minIntervalMs(60));
  EXPECT_EQ(Alert::Action::FireRecovered, result.action);
  EXPECT_EQ(7500000U, result.duration_ms);
}

TEST(AlertFaultPolicy, FirstAlertIsNotSuppressedByUptime) {
  Alert::Fault fault = okFault();
  const uint32_t threshold = Alert::thresholdMs(1);
  Alert::TickResult result = Alert::tick(
      fault, threshold, down(0), threshold, Alert::minIntervalMs(60));
  EXPECT_EQ(Alert::Action::FireDown, result.action);
}

TEST(AlertFaultPolicy, MinimumIntervalHasOneHourFloor) {
  EXPECT_EQ(3600000U, Alert::minIntervalMs(0));
  EXPECT_EQ(3600000U, Alert::minIntervalMs(59));
  EXPECT_EQ(7200000U, Alert::minIntervalMs(120));
}

TEST(AlertFaultPolicy, DurationAndCadenceSurviveMillisRollover) {
  const uint32_t start = std::numeric_limits<uint32_t>::max() - 999U;
  EXPECT_EQ(1500U, Alert::elapsedMs(500U, start));
  EXPECT_TRUE(Alert::checkDue(500U, 400U));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
