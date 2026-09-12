#include <gtest/gtest.h>

#include <helpers/RadioLivenessTracker.h>
#include <helpers/radiolib/LR2021Band.h>
#include <helpers/radiolib/RxBoostedGainDefaults.h>
#include <Dispatcher.h>
#include <helpers/StaticPoolPacketManager.h>

using mesh::RadioLivenessTracker;
using mesh::RadioRecoveryAction;

TEST(RxBoostedGainDefaults, UsesTargetCompileTimeSetting) {
  using mesh::radio::selectRxBoostedGainDefault;

  EXPECT_EQ(selectRxBoostedGainDefault(true, 0, false, 0), 0);
  EXPECT_EQ(selectRxBoostedGainDefault(true, 1, false, 0), 1);
  EXPECT_EQ(selectRxBoostedGainDefault(false, 0, true, 0), 0);
  EXPECT_EQ(selectRxBoostedGainDefault(false, 0, true, 1), 1);
  EXPECT_EQ(selectRxBoostedGainDefault(false, 0, false, 0), 1);
}

TEST(RxBoostedGainDefaults, Sx126xSettingTakesPrecedence) {
  using mesh::radio::selectRxBoostedGainDefault;

  EXPECT_EQ(selectRxBoostedGainDefault(true, 0, true, 1), 0);
  EXPECT_EQ(selectRxBoostedGainDefault(true, 1, true, 0), 1);
}

TEST(RadioLivenessTracker, StagesSoftThenHardRecovery) {
  RadioLivenessTracker tracker;
  tracker.begin(1000);
  EXPECT_EQ(tracker.poll(1999, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(2000, 1000, 5000), RadioRecoveryAction::SOFT);
  EXPECT_EQ(tracker.poll(3000, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(6000, 1000, 5000), RadioRecoveryAction::HARD);
  EXPECT_EQ(tracker.poll(6999, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(35999, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(36000, 1000, 5000), RadioRecoveryAction::HARD);
  tracker.noteHardRecoveryResult(36000, true);
  EXPECT_EQ(tracker.poll(36999, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(37000, 1000, 5000), RadioRecoveryAction::SOFT);
}

TEST(RadioLivenessTracker, SuccessfulReceiveCancelsEscalation) {
  RadioLivenessTracker tracker;
  tracker.begin(0);
  EXPECT_EQ(tracker.poll(1000, 1000, 5000), RadioRecoveryAction::SOFT);
  tracker.noteReceive(1200);
  EXPECT_EQ(tracker.stage(), 0);
  EXPECT_EQ(tracker.poll(2199, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(2200, 1000, 5000), RadioRecoveryAction::SOFT);
}

TEST(RadioLivenessTracker, ElapsedTimeIsRolloverSafe) {
  RadioLivenessTracker tracker;
  tracker.begin(0xFFFFFF00UL);
  EXPECT_EQ(tracker.poll(0x000000FFUL, 512, 4096), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(0x00000100UL, 512, 4096), RadioRecoveryAction::SOFT);
}

TEST(LR2021Band, SelectsTheCorrectFrontEnd) {
  EXPECT_FALSE(mesh::lr2021::isHighBand(1090.0f));
  EXPECT_FALSE(mesh::lr2021::isHighBand(1500.0f));
  EXPECT_TRUE(mesh::lr2021::isHighBand(1900.0f));
  EXPECT_TRUE(mesh::lr2021::isHighBand(2400.0f));
}

namespace {
class AdvancingClock : public mesh::MillisecondClock {
public:
  unsigned long now = 1;
  unsigned long getMillis() override { return now++; }
};

class BurstRadio : public mesh::Radio {
public:
  bool receiving = true;
  bool cad_busy = false;
  bool broken_rx = false;
  bool pending_rx = false;
  unsigned long last_irq = 0;
  int completed = 0;
  int recoveries = 0;
  int soft_recoveries = 0;
  int hard_recoveries = 0;
  int recvRaw(uint8_t* raw, int) override {
    if (!broken_rx) receiving = true;
    if (pending_rx) {
      pending_rx = false;
      raw[0] = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
      raw[1] = 0;
      raw[2] = 0x42;
      return 3;
    }
    return 0;
  }
  uint32_t getEstAirtimeFor(int) override { return 1000; }
  float packetScore(float, int) override { return 1; }
  bool startSendRaw(const uint8_t*, int) override {
    receiving = false;
    return true;
  }
  bool isSendComplete() override { ++completed; return true; }
  void onSendFinished() override { receiving = false; }
  unsigned long getLastRadioInterruptMillis() const override { return last_irq; }
  bool isInRecvMode() const override { return receiving; }
  bool isReceiving() override {
    // Active CAD may leave the radio outside RX until the next recvRaw().
    if (cad_busy) receiving = false;
    return cad_busy;
  }
  bool recoverRadio(bool hard) override {
    ++recoveries;
    if (hard) ++hard_recoveries;
    else ++soft_recoveries;
    receiving = !broken_rx;
    return !broken_rx;
  }
};

class WatchdogDispatcher : public mesh::Dispatcher {
public:
  WatchdogDispatcher(BurstRadio& radio, AdvancingClock& clock,
                     StaticPoolPacketManager& packets)
      : Dispatcher(radio, clock, packets) {}
  mesh::DispatcherAction onRecvPacket(mesh::Packet*) override { return ACTION_RELEASE; }
  uint16_t errors() const { return _err_flags; }
};

constexpr unsigned long SOFT_RX_TIMEOUT = 30UL * 60UL * 1000UL;
constexpr unsigned long HARD_RX_TIMEOUT = 12UL * 60UL * 60UL * 1000UL;

void queueTransmission(WatchdogDispatcher& dispatcher, StaticPoolPacketManager& packets) {
  auto* packet = packets.allocNew();
  ASSERT_NE(packet, nullptr);
  packet->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT);
  packet->path_len = 0;
  packet->payload_len = 1;
  packet->payload[0] = 0;
  ASSERT_TRUE(dispatcher.sendPacket(packet, 0));
}
}

TEST(DispatcherRadioWatchdog, TransmitSuccessDoesNotPostponeRxRecovery) {
  AdvancingClock clock;
  BurstRadio radio;
  StaticPoolPacketManager packets(2);
  WatchdogDispatcher dispatcher(radio, clock, packets);
  dispatcher.begin();

  clock.now = SOFT_RX_TIMEOUT - 100;
  queueTransmission(dispatcher, packets);
  dispatcher.loop();
  clock.now = SOFT_RX_TIMEOUT - 50;
  dispatcher.loop();
  ASSERT_EQ(radio.completed, 1);

  clock.now = SOFT_RX_TIMEOUT + 100;
  dispatcher.loop();
  EXPECT_EQ(radio.soft_recoveries, 1);
  EXPECT_EQ(radio.hard_recoveries, 0);
}

TEST(DispatcherRadioWatchdog, InterruptsWithoutReceivedPacketsDoNotPostponeRecovery) {
  AdvancingClock clock;
  BurstRadio radio;
  StaticPoolPacketManager packets(2);
  WatchdogDispatcher dispatcher(radio, clock, packets);
  dispatcher.begin();

  clock.now = 1000;
  radio.last_irq = clock.now;
  dispatcher.loop();
  clock.now = SOFT_RX_TIMEOUT + 100;
  radio.last_irq = clock.now;
  dispatcher.loop();
  EXPECT_EQ(radio.soft_recoveries, 1);
}

TEST(DispatcherRadioWatchdog, ReceivedPacketAtDeadlinePreventsUnnecessaryRecovery) {
  AdvancingClock clock;
  BurstRadio radio;
  StaticPoolPacketManager packets(2);
  WatchdogDispatcher dispatcher(radio, clock, packets);
  dispatcher.begin();

  clock.now = SOFT_RX_TIMEOUT + 100;
  radio.pending_rx = true;
  dispatcher.loop();
  EXPECT_EQ(radio.recoveries, 0);

  clock.now += SOFT_RX_TIMEOUT - 100;
  dispatcher.loop();
  EXPECT_EQ(radio.recoveries, 0);
  clock.now += 200;
  dispatcher.loop();
  EXPECT_EQ(radio.soft_recoveries, 1);
}

TEST(DispatcherRadioWatchdog, ReceivedPacketCancelsRecoveryEscalation) {
  AdvancingClock clock;
  BurstRadio radio;
  StaticPoolPacketManager packets(2);
  WatchdogDispatcher dispatcher(radio, clock, packets);
  dispatcher.begin();

  clock.now = SOFT_RX_TIMEOUT + 100;
  dispatcher.loop();
  ASSERT_EQ(radio.soft_recoveries, 1);
  clock.now += 100;
  radio.pending_rx = true;
  dispatcher.loop();
  clock.now += SOFT_RX_TIMEOUT - 100;
  dispatcher.loop();
  EXPECT_EQ(radio.soft_recoveries, 1);
  clock.now += 200;
  dispatcher.loop();
  EXPECT_EQ(radio.soft_recoveries, 2);
  EXPECT_EQ(radio.hard_recoveries, 0);
}

TEST(DispatcherRadioWatchdog, ContinuousTransmitQueueCannotStarveRxRecovery) {
  AdvancingClock clock;
  BurstRadio radio;
  StaticPoolPacketManager packets(4);
  WatchdogDispatcher dispatcher(radio, clock, packets);
  dispatcher.begin();

  clock.now = SOFT_RX_TIMEOUT - 100;
  for (int i = 0; i < 3; ++i) queueTransmission(dispatcher, packets);
  dispatcher.loop();
  clock.now = SOFT_RX_TIMEOUT + 100;
  dispatcher.loop();
  ASSERT_EQ(radio.completed, 1);
  EXPECT_EQ(radio.soft_recoveries, 1);
  clock.now += 100;
  dispatcher.loop();
  EXPECT_EQ(radio.completed, 2);
  EXPECT_EQ(radio.soft_recoveries, 1);
}

#ifndef RADIO_LIVENESS_SOFT_ONLY
TEST(DispatcherRadioWatchdog, TransmitSuccessDoesNotPreventHardRxRecovery) {
  AdvancingClock clock;
  BurstRadio radio;
  StaticPoolPacketManager packets(2);
  WatchdogDispatcher dispatcher(radio, clock, packets);
  dispatcher.begin();

  clock.now = SOFT_RX_TIMEOUT + 100;
  dispatcher.loop();
  ASSERT_EQ(radio.soft_recoveries, 1);
  clock.now = HARD_RX_TIMEOUT - 100;
  queueTransmission(dispatcher, packets);
  dispatcher.loop();
  clock.now = HARD_RX_TIMEOUT + 100;
  dispatcher.loop();
  ASSERT_EQ(radio.completed, 1);
  EXPECT_EQ(radio.hard_recoveries, 1);
}
#endif

TEST(DispatcherRadioWatchdog, SuccessfulTxBurstDoesNotTriggerStuckRxRecovery) {
  AdvancingClock clock;
  BurstRadio radio;
  StaticPoolPacketManager packets(16);
  WatchdogDispatcher dispatcher(radio, clock, packets);
  dispatcher.begin();
  for (int i = 0; i < 12; ++i) {
    auto* packet = packets.allocNew();
    ASSERT_NE(packet, nullptr);
    packet->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TXT_MSG << 2);
    packet->path_len = 0;
    packet->payload_len = 1;
    packet->payload[0] = 0;
    ASSERT_TRUE(dispatcher.sendPacket(packet, 0));
  }
  dispatcher.loop();
  // RX happens inside each loop between completed TX and the next TX. The
  // watchdog at the top of the loop sees only TX, for longer than 8 seconds.
  for (int i = 0; i < 10; ++i) {
    clock.now += 1000;
    if (i == 9) radio.cad_busy = true;
    dispatcher.loop();
  }
  ASSERT_EQ(radio.completed, 10);
  clock.now += 100;
  dispatcher.loop();
  EXPECT_EQ(radio.recoveries, 0);
  EXPECT_EQ(dispatcher.errors() & ERR_EVENT_STARTRX_TIMEOUT, 0);

  // A subsequent genuine RX failure still gets its own recovery deadline.
  radio.broken_rx = true;
  clock.now += 8100;
  dispatcher.loop();
  EXPECT_EQ(radio.recoveries, 1);
  EXPECT_NE(dispatcher.errors() & ERR_EVENT_STARTRX_TIMEOUT, 0);
}

TEST(DispatcherRadioWatchdog, StuckReceiverStillRecoversWithoutTransmitActivity) {
  AdvancingClock clock;
  BurstRadio radio;
  StaticPoolPacketManager packets(2);
  WatchdogDispatcher dispatcher(radio, clock, packets);
  dispatcher.begin();
  radio.broken_rx = true;
  radio.receiving = false;
  dispatcher.loop();
  clock.now += 8100;
  dispatcher.loop();
  EXPECT_EQ(radio.recoveries, 1);
  EXPECT_NE(dispatcher.errors() & ERR_EVENT_STARTRX_TIMEOUT, 0);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
