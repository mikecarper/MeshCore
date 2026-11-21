#include <Dispatcher.h>
#include <helpers/StaticPoolPacketManager.h>
#include <cassert>
#include <initializer_list>

class TestClock : public mesh::MillisecondClock {
 public:
  unsigned long now = 1000;
  unsigned long getMillis() override { return now; }
};

// The frame reaches RF, but its TxDone interrupt never reaches the driver.
class TestRadio : public mesh::Radio {
 public:
  bool in_rx = true, complete = false, fail_first_start = false;
  unsigned starts = 0, transmitted = 0, finished = 0, recoveries = 0;
  int recvRaw(uint8_t*, int) override { return 0; }
  uint32_t packet_airtime = 100, maximum_airtime = 100;
  bool use_profile_rate = false;
  uint8_t coding_rate = 5;
  uint32_t getEstAirtimeFor(int len) override { return len == MAX_TRANS_UNIT ? maximum_airtime : packet_airtime; }
  uint32_t getProfileAirtime(uint8_t, int len, uint8_t cr=0) override {
    if (use_profile_rate && cr == 8) return 200;
    return getEstAirtimeFor(len);
  }
  bool setCodingRate(uint8_t cr) override { coding_rate = cr; return true; }
  float packetScore(float, int) override { return 0; }
  bool startSendRaw(const uint8_t*, int) override {
    ++starts;
    if (fail_first_start && starts == 1) return false;
    ++transmitted;
    in_rx = false;
    return true;
  }
  bool isSendComplete() override { return complete; }
  void onSendFinished() override { ++finished; in_rx = true; }
  bool isInRecvMode() const override { return in_rx; }
  bool recoverRadio(bool hard) override {
    assert(hard);
    ++recoveries;
    in_rx = true;
    return true;
  }
};

class TestDispatcher : public mesh::Dispatcher {
 public:
  unsigned failures = 0, completions = 0;
  uint8_t getDefaultTxCodingRate() const override { return 5; }
  TestDispatcher(TestRadio& radio, TestClock& clock, StaticPoolPacketManager& manager)
      : Dispatcher(radio, clock, manager) {}
  mesh::DispatcherAction onRecvPacket(mesh::Packet*) override { return ACTION_RELEASE; }
  void onSendFail(mesh::Packet*) override { ++failures; }
  void onSendComplete(mesh::Packet*) override { ++completions; }
};

struct Fixture {
  TestClock clock;
  TestRadio radio;
  StaticPoolPacketManager manager{4};
  TestDispatcher dispatcher{radio, clock, manager};
  Fixture() { dispatcher.begin(); }
  void loopAt(unsigned long now) { clock.now = now; dispatcher.loop(); }
  void queue(uint8_t route, uint8_t policy, uint8_t cr = 0) {
    mesh::Packet* packet = dispatcher.obtainNewPacket();
    assert(packet);
    packet->header = route | (PAYLOAD_TYPE_REQ << PH_TYPE_SHIFT);
    packet->payload_len = 4;
    for (unsigned i = 0; i < packet->payload_len; ++i) packet->payload[i] = i;
    packet->transport_codes[0] = packet->transport_codes[1] = 0;
    packet->flood_retry_policy = policy;
    packet->tx_cr = cr;
    assert(dispatcher.sendPacket(packet, 0));
  }
  void expectRetired(unsigned failures, unsigned completions) {
    assert(!dispatcher.hasOutbound());
    assert(manager.getOutboundTotal() == 0 && manager.getFreeCount() == 4);
    assert(dispatcher.failures == failures && dispatcher.completions == completions);
  }
};

static bool retryAllowed(uint8_t route, uint8_t policy) {
  const bool flood = route == ROUTE_TYPE_FLOOD || route == ROUTE_TYPE_TRANSPORT_FLOOD;
  return !flood || policy != mesh::FLOOD_RETRY_POLICY_DENY;
}

static void lostTxDone(uint8_t route, uint8_t policy, bool second_done) {
  Fixture f;
  f.queue(route, policy);
  f.loopAt(1001);
  assert(f.radio.starts == 1 && f.radio.transmitted == 1 && f.dispatcher.hasOutbound());
  f.loopAt(1151);  // Exact expiry has not passed yet.
  assert(f.radio.recoveries == 0 && f.radio.finished == 0);
  f.loopAt(1152);  // Actual Dispatcher TX timeout and hard peripheral recovery.
  assert(f.radio.recoveries == 1 && f.radio.finished == 1);
  assert(f.dispatcher.getErrFlags() & ERR_EVENT_RADIO_WATCHDOG);
  f.loopAt(1352);  // Retry backoff has not passed yet either.
  assert(f.radio.starts == 1);
  f.loopAt(1353);
  if (!retryAllowed(route, policy)) {
    assert(f.radio.starts == 1 && "a denied flood must never be retransmitted");
    assert(f.radio.transmitted == 1);
    f.expectRetired(1, 0);
    f.loopAt(5000);
    assert(f.radio.starts == 1 && f.radio.recoveries == 1);
  } else {
    assert(f.radio.starts == 2 && f.radio.transmitted == 2);
    f.radio.complete = second_done;
    f.loopAt(1504);
    f.expectRetired(second_done ? 0 : 1, second_done ? 1 : 0);
    assert(f.radio.recoveries == (second_done ? 1U : 2U));
    f.loopAt(5000);
    assert(f.radio.starts == 2);  // Never a third hardware attempt.
  }
}

static void failedStart(uint8_t route, uint8_t policy) {
  Fixture f;
  f.radio.fail_first_start = true;
  f.queue(route, policy);
  f.loopAt(1001);
  assert(f.radio.starts == 1 && f.radio.transmitted == 0);
  f.loopAt(1202);
  if (!retryAllowed(route, policy)) {
    assert(f.radio.starts == 1);
    f.expectRetired(1, 0);
  } else {
    assert(f.radio.starts == 2 && f.radio.transmitted == 1);
    f.radio.complete = true;
    f.loopAt(1203);
    f.expectRetired(0, 1);
  }
  assert(f.radio.recoveries == 0);
}

int main() {
  // Run the strict policy first so removing its guard fails through the real
  // missing-TxDone timeout path, rather than a textual contract assertion.
  lostTxDone(ROUTE_TYPE_FLOOD, mesh::FLOOD_RETRY_POLICY_DENY, false);
  for (uint8_t route : {ROUTE_TYPE_FLOOD, ROUTE_TYPE_TRANSPORT_FLOOD,
                        ROUTE_TYPE_DIRECT, ROUTE_TYPE_TRANSPORT_DIRECT}) {
    for (uint8_t policy : {mesh::FLOOD_RETRY_POLICY_DEFAULT,
                          mesh::FLOOD_RETRY_POLICY_DENY,
                          mesh::FLOOD_RETRY_POLICY_ALLOW}) {
      lostTxDone(route, policy, true);
      lostTxDone(route, policy, false);
      failedStart(route, policy);
    }
  }
  for (uint32_t invalid : {0U, UINT32_MAX}) {
    for (bool repaired : {false, true}) {
      Fixture f;
      f.radio.packet_airtime = invalid;
      f.queue(ROUTE_TYPE_DIRECT, mesh::FLOOD_RETRY_POLICY_DEFAULT, 8);
      f.loopAt(1001);
      assert(f.radio.starts == 0 && f.radio.coding_rate == 5);
      if (repaired) f.radio.packet_airtime = 100;
      f.loopAt(1202);
      assert(f.radio.starts == unsigned(repaired));
      if (repaired) {
        assert(f.radio.coding_rate == 8);
        f.radio.complete = true;
        f.loopAt(1250);
      }
      f.expectRetired(!repaired, repaired);
      assert(f.radio.recoveries == 0 && f.radio.coding_rate == 5);
    }
  }
  // A cached default-CR estimate can be shorter than the on-air override.
  {
    Fixture f;
    f.radio.use_profile_rate = true;
    f.queue(ROUTE_TYPE_DIRECT, mesh::FLOOD_RETRY_POLICY_DEFAULT, 8);
    f.loopAt(1001);
    f.loopAt(1190);
    assert(f.radio.coding_rate == 8 && !f.radio.recoveries);
    f.radio.complete = true;
    f.loopAt(1201);
    f.expectRetired(0, 1);
  }
  // An impossible maximum must not put a healthy shorter packet to sleep.
  {
    Fixture f;
    f.radio.maximum_airtime = UINT32_MAX;
    f.queue(ROUTE_TYPE_DIRECT, mesh::FLOOD_RETRY_POLICY_DEFAULT);
    f.loopAt(1001);
    assert(f.radio.starts == 1);
    f.radio.complete = true;
    f.loopAt(1050);
    f.expectRetired(0, 1);
  }
  // Denied floods still get the ordinary successful-send completion hook.
  Fixture f;
  f.queue(ROUTE_TYPE_FLOOD, mesh::FLOOD_RETRY_POLICY_DENY);
  f.loopAt(1001);
  f.radio.complete = true;
  f.loopAt(1002);
  f.expectRetired(0, 1);
  assert(f.radio.starts == 1 && f.radio.transmitted == 1 && f.radio.recoveries == 0);
}
