"""Execute the production TX-only beacon owner and Dispatcher lifecycle.

The test uses the actual Dispatcher, packet serializer, and static pool. Radio
and clock boundaries are counted adapters. No PlatformIO or hardware is used.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


HARNESS = r'''
#include <Dispatcher.h>
#include <helpers/StaticPoolPacketManager.h>
#if MESH_ENABLE_OTA_KISS_DIRECT
#include <helpers/ota/OtaFormat.h>
#endif
#include <cassert>
#include <cstring>
#include <cstdio>
#include <vector>
using namespace mesh;
class Clock : public MillisecondClock {
public:
  uint32_t now;
  explicit Clock(uint32_t initial = 1000) : now(initial) {}
  unsigned long getMillis() override { return now; }
};
class TestRadio : public Radio {
public:
  RadioProfiles p;
  RadioProfileParams physical, saved;
  int8_t power = 22, saved_power = 22;
  uint8_t active = 0;
  bool owned = false, receiving = false, complete = false, sending = false;
  bool start_ok = true, carrier = false, morse_tx = false, morse_rx = false;
  bool calibrating = false, incoming = false, channel_busy = false;
  bool valid = true, busy_owns = false, fail_owns = false;
  unsigned sends = 0, beacon_sends = 0, normal_sends = 0, finishes = 0, recovery = 0;
  unsigned processed = 0, prepare_calls = 0, restore_calls = 0, loops = 0;
  unsigned agc = 0, noise = 0, begins = 0, begin_while_owned = 0;
  uint32_t airtime = 100;
  Clock* clock = nullptr;
  uint32_t cad_delay = 0;
  RadioParamApplyResult prepare = RadioParamApplyResult::APPLIED;
  RadioParamApplyResult restore = RadioParamApplyResult::APPLIED;
  std::vector<uint8_t> sent;
  std::vector<uint32_t> beacon_starts;
  TestRadio() {
    p.primary.freq = 909.5f; p.primary.bw = 125; p.primary.sf = 7; p.primary.cr = 5;
    p.primary.preamble = 32;
    p.secondary.params = p.primary; p.secondary.params.freq = 910;
    physical = saved = p.primary;
  }
  RadioProfiles* profiles() override { return &p; }
  const RadioProfiles* profiles() const override { return &p; }
  bool supportsMeshtext() const override { return true; }
  bool supportsEthernetTunnel() const override { return true; }
  bool validateProfile(const RadioProfileParams& params) const override {
    return valid && RadioProfiles::valid(params);
  }
  uint8_t receiveProfile() const override { return active; }
  int recvRaw(uint8_t* bytes, int) override {
    assert(!owned);
    if (!incoming) return 0;
    incoming = false; bytes[0] = 0x15; bytes[1] = 0; bytes[2] = 0x23; return 3;
  }
  void onReceiveProcessed() override { ++processed; }
  uint32_t getEstAirtimeFor(int) override { return 100; }
  uint32_t getProfileAirtime(uint8_t, int, uint8_t = 0) override { return 100; }
  uint32_t getBeaconAirtime(const RadioProfileParams&, int) override { return airtime; }
  float packetScore(float, int) override { return 1; }
  RadioParamApplyResult prepareTransmitProfile(uint8_t profile) override {
    assert(!owned); active = profile; physical = p.params(profile); return RadioParamApplyResult::APPLIED;
  }
  RadioParamApplyResult prepareBeaconTransmit(const RadioProfileParams& params, int8_t requested_power) override {
    ++prepare_calls;
    if (prepare == RadioParamApplyResult::APPLIED || busy_owns || fail_owns) {
      if (!owned) { saved = physical; saved_power = power; }
      owned = true; physical = params; power = requested_power;
    }
    return prepare;
  }
  RadioParamApplyResult restoreBeaconReceive() override {
    ++restore_calls;
    if (restore == RadioParamApplyResult::APPLIED) {
      if (owned) { physical = saved; power = saved_power; }
      owned = false;
    }
    return restore;
  }
  bool isBeaconRadioOwned() const override { return owned; }
  bool isBeaconChannelBusy() override {
    assert(owned);
    if (clock) clock->now += cad_delay;
    return channel_busy;
  }
  bool startSendRaw(const uint8_t* bytes, int length) override {
    ++sends;
    if (owned) {
      ++beacon_sends;
      if (clock) beacon_starts.push_back(clock->now);
    } else ++normal_sends;
    sent.assign(bytes, bytes + length); sending = start_ok; complete = false; return start_ok;
  }
  bool isSendComplete() override { return complete; }
  void onSendFinished() override { ++finishes; sending = false; }
  bool isInRecvMode() const override { return !sending; }
  bool isReceiving() override { return receiving; }
  bool isCarrierWaveActive() const override { return carrier; }
  bool isMorseTxActive() const override { return morse_tx; }
  bool isMorseRxActive() const override { return morse_rx; }
  bool isCalibratingNoiseFloor() const override { return calibrating; }
  bool recoverRadio(bool) override { ++recovery; return true; }
  uint32_t getRecoveryCount() const override { return recovery; }
  void loop() override { assert(!owned); ++loops; }
  void begin() override { ++begins; if (owned) ++begin_while_owned; }
  void resetAGC() override { assert(!owned); ++agc; }
  bool tryResetAGC() override { resetAGC(); return true; }
  void triggerNoiseFloorCalibrate(int) override { assert(!owned); ++noise; }
};
class TestDispatcher : public Dispatcher {
public:
  unsigned received = 0, mc_sent = 0, failed = 0;
  unsigned long duty_window = 3600000;
  int interval = 0;
  bool adaptive = false;
  TestDispatcher(TestRadio& radio, Clock& clock, StaticPoolPacketManager& manager)
      : Dispatcher(radio, clock, manager) {}
  using Dispatcher::setRadioAvailable;
  using Dispatcher::getNextQueueWakeDelay;
#if MESH_ENABLE_FOREIGN_RADIO
  using Dispatcher::queueForeignRadioFrame;
  using Dispatcher::hasForeignRadioFrame;
  bool queueOtherOwner(const uint8_t* bytes, size_t length) {
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL
    const auto protocol = MESH_ENABLE_MESHTEXT_CHAT
        ? RadioProfileProtocol::Meshtext : RadioProfileProtocol::Ethernet;
    _radio->profiles()->secondary.params.protocol = protocol;
    _radio->profiles()->secondary.mode = RadioProfileMode::RxTx;
    return queueForeignRadioFrame(bytes, length, 1, _radio->profiles()->generation[1], protocol);
#elif MESH_ENABLE_OTA_KISS_DIRECT
    const uint8_t message[] = {ota::OTA_DATA, 0x23};
    return queueDirectOtaRadioFrame(message, sizeof(message), 0, _radio->profiles()->generation[0]);
#endif
  }
#if MESH_ENABLE_OTA_KISS_DIRECT
  bool allowDirectOtaTransmit(uint8_t, uint32_t) const override { return true; }
  uint32_t directOtaEpoch() const override { return 1; }
#endif
#endif
  DispatcherAction onRecvPacket(Packet*) override { ++received; return ACTION_RELEASE; }
  void onSendComplete(Packet*) override { ++mc_sent; }
  void onSendFail(Packet*) override { ++failed; }
  unsigned long getDutyCycleWindowMs() const override { return duty_window; }
  int getAGCResetInterval() const override { return interval; }
  bool useAdaptiveAGC() const override { return adaptive; }
};
struct Fixture {
  Clock clock;
  TestRadio radio;
  StaticPoolPacketManager manager{8};
  TestDispatcher d{radio, clock, manager};
  explicit Fixture(uint32_t initial = 1000) : clock(initial) { radio.clock = &clock; d.begin(); }
  void tick(uint32_t delta = 1) { clock.now += delta; d.loop(); }
};
static const uint8_t bytes[] = {0x15, 0, 0x23};
static RadioProfileParams beacon_params() {
  RadioProfileParams p;
  p.freq = 906.875f; p.bw = 250; p.sf = 11; p.cr = 5; p.preamble = 16;
  p.protocol = RadioProfileProtocol::Meshtext;
  return p;
}
static bool queue(Fixture& f) {
  return f.d.queueBeaconRadioFrame(bytes, sizeof(bytes), beacon_params(), 17);
}
static Packet* normal(Fixture& f, uint32_t delay = 0) {
  Packet* packet = f.d.obtainNewPacket(); assert(packet);
  packet->header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
  packet->tx_radio = RADIO_TX_PRIMARY; packet->payload[0] = 0x12; packet->payload_len = 1;
  assert(f.d.sendPacket(packet, 0, delay));
  return packet;
}
static void basic() {
  Fixture f;
  f.radio.p.secondary.mode = RadioProfileMode::RxTx;
  f.radio.p.secondary_temporary = true;
  const auto saved = f.radio.p;
  assert(queue(f)); assert(f.d.hasOutbound() && !queue(f));
  uint32_t delay = 99;
  assert(f.d.getNextQueueWakeDelay(delay) && delay == 0);
  f.tick();
  assert(f.radio.beacon_sends == 1 && f.radio.owned && f.radio.physical == beacon_params());
  assert(f.radio.sent == std::vector<uint8_t>(bytes, bytes + sizeof(bytes)));
  assert(f.radio.power == 17);
#if MESH_ENABLE_FOREIGN_RADIO
  assert(!f.d.queueForeignRadioFrame(bytes, sizeof(bytes), 1, 1));
#endif
  f.tick(20); assert(f.d.hasOutbound() && f.radio.finishes == 0);
  f.radio.complete = true; f.tick();
  assert(!f.d.hasOutbound() && !f.radio.owned && f.radio.finishes == 1);
  assert(f.radio.physical == saved.primary && f.radio.power == 22);
  assert(f.radio.p.primary == saved.primary && f.radio.p.secondary.params == saved.secondary.params);
  assert(f.radio.p.secondary.mode == saved.secondary.mode && f.radio.p.secondary_temporary);
  assert(f.radio.p.generation[0] == saved.generation[0] && f.radio.p.generation[1] == saved.generation[1]);
  assert(!f.d.getNumSentFlood() && !f.d.getNumSentDirect() && !f.d.mc_sent);
  assert(f.d.getTotalAirTime() == 21);
}
static void preparation() {
  { Fixture f; f.radio.prepare = RadioParamApplyResult::BUSY;
    assert(queue(f)); f.tick(); assert(f.radio.prepare_calls == 1 && !f.radio.owned && !f.radio.sends);
    f.tick(10); assert(f.radio.prepare_calls == 1);
    f.tick(1); assert(f.radio.prepare_calls == 2);
    f.tick(29987); assert(f.d.hasBeaconRadioFrame());
    f.tick(1); assert(!f.d.hasBeaconRadioFrame() && !f.radio.sends && !f.radio.owned);
    assert(queue(f)); // No RF attempt means the one-hour guard was not consumed.
    f.d.cancelBeaconRadioFrame(); }
  { Fixture f; f.radio.prepare = RadioParamApplyResult::BUSY; f.radio.busy_owns = true;
    f.radio.restore = RadioParamApplyResult::BUSY; assert(queue(f)); f.tick();
    assert(f.d.hasBeaconRadioFrame() && f.radio.owned && f.radio.restore_calls == 1);
    const unsigned loops = f.radio.loops; f.tick(500);
    assert(f.radio.loops == loops && !f.radio.sends);
    f.radio.restore = RadioParamApplyResult::APPLIED; f.tick();
    assert(!f.d.hasBeaconRadioFrame() && !f.radio.owned); }
  { Fixture f; f.radio.prepare = RadioParamApplyResult::FAILED; f.radio.fail_owns = true;
    assert(queue(f)); f.tick(); assert(!f.radio.owned && !f.d.hasBeaconRadioFrame() && !f.radio.sends); }
}
static void failures() {
  { Fixture f; f.radio.channel_busy = true; assert(queue(f)); f.tick();
    assert(!f.radio.sends && !f.d.hasBeaconRadioFrame() && !f.radio.owned);
    f.radio.channel_busy = false; assert(queue(f)); f.d.cancelBeaconRadioFrame(); }
  { Fixture f; f.radio.start_ok = false; assert(queue(f)); f.tick();
    assert(f.radio.sends == 1 && !f.d.hasBeaconRadioFrame() && !f.radio.owned);
    assert(queue(f)); f.tick(); assert(f.radio.sends == 1);
    f.tick(30000); assert(!f.d.hasBeaconRadioFrame() && f.radio.sends == 1);
    f.tick(3600000); assert(queue(f)); f.d.cancelBeaconRadioFrame(); }
  { Fixture f; f.radio.airtime = 0; assert(queue(f)); f.tick();
    assert(!f.d.hasBeaconRadioFrame() && !f.radio.sends); }
  { Fixture f; f.radio.airtime = UINT32_MAX; assert(queue(f)); f.tick();
    assert(!f.d.hasBeaconRadioFrame() && !f.radio.sends); }
  { Fixture f; assert(queue(f)); f.tick(); f.tick(1150);
    assert(!f.d.hasBeaconRadioFrame() && !f.radio.owned && f.radio.finishes == 1 && f.radio.recovery == 1);
    assert(f.d.getErrFlags() & ERR_EVENT_RADIO_WATCHDOG);
    f.tick(10000); assert(f.radio.sends == 1); }
}
static void restoration() {
  Fixture f; f.d.interval = 8; f.d.adaptive = true;
  assert(queue(f)); f.tick();
  const unsigned loops = f.radio.loops, agc = f.radio.agc, noise = f.radio.noise;
  f.radio.restore = RadioParamApplyResult::BUSY;
  f.radio.complete = true; f.radio.incoming = true;
  normal(f); f.tick(20);
  assert(f.radio.owned && f.d.hasBeaconRadioFrame() && f.radio.finishes == 1);
  f.tick(30000);
  assert(f.radio.loops == loops && f.radio.agc == agc && f.radio.noise == noise);
  assert(f.radio.normal_sends == 0 && f.radio.incoming && f.manager.getOutboundTotal() == 1);
  f.radio.restore = RadioParamApplyResult::FAILED; f.tick();
  assert(f.radio.owned && f.radio.loops == loops && !f.radio.normal_sends);
  f.radio.restore = RadioParamApplyResult::APPLIED; f.tick();
  assert(!f.radio.owned && !f.d.hasBeaconRadioFrame() && !f.radio.incoming);
  assert(f.radio.normal_sends == 1 && f.radio.agc > agc);
  f.radio.complete = true; f.tick();
}
static void availability() {
  { Fixture f; assert(queue(f)); f.d.setRadioAvailable(false);
    assert(!f.d.hasBeaconRadioFrame() && !queue(f));
    const unsigned restores = f.radio.restore_calls, loops = f.radio.loops;
    f.tick(10000); assert(f.radio.restore_calls == restores && f.radio.loops == loops);
    f.d.setRadioAvailable(true); assert(queue(f)); f.d.cancelBeaconRadioFrame(); }
  { Fixture f; assert(queue(f)); f.tick(); f.radio.restore = RadioParamApplyResult::BUSY;
    f.d.setRadioAvailable(false); assert(f.radio.owned && f.d.hasBeaconRadioFrame());
    const unsigned restores = f.radio.restore_calls, finishes = f.radio.finishes, loops = f.radio.loops;
    f.tick(10000);
    assert(f.radio.restore_calls == restores && f.radio.finishes == finishes && f.radio.loops == loops);
    f.d.setRadioAvailable(true);
    assert(!f.radio.begin_while_owned); // Never initialize through an outstanding beacon lease.
    f.radio.restore = RadioParamApplyResult::APPLIED; f.tick();
    assert(!f.radio.owned && !f.d.hasBeaconRadioFrame()); }
}
static void priority() {
  { Fixture f; assert(queue(f)); normal(f); f.tick();
    assert(f.radio.normal_sends == 1 && !f.radio.beacon_sends && !f.radio.owned);
    assert(f.d.hasBeaconRadioFrame()); f.radio.complete = true; f.tick(); f.tick(101);
    assert(f.radio.beacon_sends == 1 && f.radio.owned);
    f.radio.complete = true; f.tick(); }
  { Fixture f; normal(f, 5000); assert(queue(f)); f.tick();
    assert(f.radio.beacon_sends == 1 && !f.radio.normal_sends);
    f.radio.complete = true; f.tick(); f.tick(5000);
    assert(f.radio.normal_sends == 1); f.radio.complete = true; f.tick(); }
  { Fixture f; normal(f); f.tick(); assert(!queue(f)); f.radio.complete = true; f.tick(); }
#if MESH_ENABLE_MESHTEXT_CHAT
  { Fixture f; f.radio.p.secondary.params.protocol = RadioProfileProtocol::Meshtext;
    f.radio.p.secondary.mode = RadioProfileMode::RxTx;
    assert(f.d.queueForeignRadioFrame(bytes, sizeof(bytes), 1, f.radio.p.generation[1]));
    assert(!queue(f)); f.tick(); f.radio.complete = true; f.tick(); }
#endif
}
static void owners() {
  Fixture f;
#if MESH_ENABLE_FOREIGN_RADIO
  assert(f.d.queueOtherOwner(bytes, sizeof(bytes)) && f.d.hasForeignRadioFrame());
  assert(!queue(f)); // A queued foreign frame already owns the shared raw slot.
  f.tick();
  assert(f.radio.normal_sends == 1 && !f.radio.beacon_sends);
  assert(!queue(f)); // Ownership also survives until its TX completes.
  f.radio.complete = true; f.tick();
  assert(!f.d.hasForeignRadioFrame());
#endif
  assert(queue(f));
#if MESH_ENABLE_FOREIGN_RADIO
  assert(!f.d.queueOtherOwner(bytes, sizeof(bytes)));
#endif
  f.tick();
  assert(f.radio.beacon_sends == 1 && f.radio.owned);
  f.radio.complete = true; f.tick();
  assert(!f.radio.owned && !f.d.hasBeaconRadioFrame());
}
static void budget_and_hour() {
  { Fixture f; f.d.duty_window = 200; f.d.begin();
    assert(queue(f)); f.tick(); f.radio.complete = true; f.tick(100);
    assert(f.d.getRemainingTxBudget() == 0 && f.d.getTotalAirTime() == 100);
    assert(queue(f)); f.tick(); assert(f.radio.beacon_sends == 1);
    f.d.cancelBeaconRadioFrame();
    f.clock.now = 3601000; assert(queue(f)); // Admission just before the actual RF deadline is valid.
    f.d.loop(); assert(f.radio.beacon_sends == 1);
    f.clock.now = 3601001; f.d.loop(); assert(f.radio.beacon_sends == 1);
    f.clock.now = 3601101; f.d.loop(); assert(f.radio.beacon_sends == 2);
    f.radio.complete = true; f.tick(100); }
  { Fixture f; f.d.duty_window = 20; f.d.begin(); assert(queue(f)); f.tick();
    assert(!f.radio.sends && f.d.hasBeaconRadioFrame());
    f.tick(30000); assert(!f.d.hasBeaconRadioFrame() && !f.radio.sends); }
  { Fixture f(UINT32_MAX - 500); assert(queue(f)); f.tick();
    f.radio.complete = true; f.tick(50); assert(queue(f));
    f.tick(); assert(f.radio.beacon_sends == 1); f.d.cancelBeaconRadioFrame();
    f.clock.now = uint32_t(UINT32_MAX - 499 + uint64_t(3600000));
    assert(queue(f)); f.tick(); assert(f.radio.beacon_sends == 2);
    f.radio.complete = true; f.tick(); }
}
static void hourly_cad_delay() {
  Fixture f;
  f.radio.cad_delay = 30;
  const uint32_t first_admission = f.clock.now;
  assert(queue(f)); f.tick();
  assert(f.radio.beacon_sends == 1 && f.radio.beacon_starts[0] == first_admission + 31);
  f.radio.complete = true; f.tick();
  // The manager staggers admission, not the end of synchronous CAD. A new
  // hourly slot must wait for the RF guard instead of being dropped here.
  f.clock.now = first_admission + 3600000;
  assert(queue(f));
  f.tick(); assert(f.radio.beacon_sends == 1 && !f.radio.owned);
  f.tick(30); assert(f.radio.beacon_sends == 1 && !f.radio.owned);
  f.tick(71); assert(f.radio.beacon_sends == 2 && f.radio.owned);
  assert(uint32_t(f.radio.beacon_starts[1] - f.radio.beacon_starts[0]) >= 3600000);
  f.radio.complete = true; f.tick();
}
static void sleep_deadlines() {
  Fixture f; f.d.interval = 8000; f.d.adaptive = true; f.tick();
  uint32_t delay = 0;
  f.d.setBeaconWakeDelay(f.clock.now, 3600000, true);
  assert(f.d.getNextQueueWakeDelay(delay) && delay == 8000);
  f.d.setBeaconWakeDelay(f.clock.now, 1000, true);
  assert(f.d.getNextQueueWakeDelay(delay) && delay == 1000);
  f.d.setBeaconWakeDelay(f.clock.now, 1000, false);
  assert(f.d.getNextQueueWakeDelay(delay) && delay == 8000);
  assert(queue(f)); assert(f.d.getNextQueueWakeDelay(delay) && delay == 0);
  f.d.cancelBeaconRadioFrame();
  f.d.setBeaconWakeDelay(f.clock.now, 0, true);
  assert(f.d.getNextQueueWakeDelay(delay) && delay == 0);
  f.d.setRadioAvailable(false);
  assert(!f.d.getNextQueueWakeDelay(delay));
}
static void begin_lifecycle() {
  Fixture f; assert(queue(f)); f.tick();
  assert(f.radio.owned);
  f.d.begin();
  assert(!f.radio.begin_while_owned && !f.radio.owned && !f.d.hasBeaconRadioFrame());
  assert(f.radio.finishes == 1 && f.radio.restore_calls == 1);
}
static void configure_agc(Fixture& f, bool adaptive) {
  f.d.interval = 4000;
  f.d.adaptive = adaptive;
  f.tick(0);
  assert(f.radio.agc == 0);
  f.clock.now += 3990; // TX begins just before the original maintenance deadline.
}
static void agc_retirement() {
  for (bool adaptive : {false, true}) for (bool wrap : {false, true}) {
    const uint32_t initial = wrap ? UINT32_MAX - 2000U : 1000U;
    Fixture ordinary(initial);
    configure_agc(ordinary, adaptive);
    normal(ordinary); ordinary.d.loop();
    assert(ordinary.radio.normal_sends == 1 && ordinary.radio.agc == 0);
    ordinary.clock.now += 100; ordinary.radio.complete = true; ordinary.d.loop();
    assert(ordinary.radio.agc == unsigned(adaptive)); // Existing normal TX policy.

    for (bool abort : {false, true}) {
      Fixture f(initial);
      configure_agc(f, adaptive);
      assert(queue(f)); f.d.loop();
      assert(f.radio.beacon_sends == 1 && f.radio.agc == 0);
      f.clock.now += 100;
      if (abort) f.d.cancelBeaconRadioFrame();
      else f.radio.complete = true;
      f.d.loop();
      assert(!f.radio.owned && !f.d.hasBeaconRadioFrame() && f.radio.finishes == 1);
      assert(f.d.getTotalAirTime() == 100);
      assert(f.radio.agc == unsigned(adaptive)
          && "beacon TX must follow normal fixed/adaptive AGC retirement policy");
    }

    // Count retirement once, not again for each failed restore/cancel attempt.
    Fixture delayed(initial);
    configure_agc(delayed, adaptive);
    assert(queue(delayed)); delayed.d.loop();
    delayed.radio.restore = RadioParamApplyResult::BUSY;
    delayed.clock.now += 100;
    delayed.radio.complete = true; delayed.d.loop();
    assert(delayed.radio.owned && delayed.radio.agc == 0);
    delayed.clock.now += 1000; delayed.d.cancelBeaconRadioFrame();
    assert(delayed.d.getTotalAirTime() == 100 && delayed.radio.finishes == 1);
    delayed.clock.now = initial + 8091U;
    delayed.radio.restore = RadioParamApplyResult::APPLIED; delayed.d.loop();
    assert(delayed.radio.agc == 1 && "restore retries must not postpone AGC again");

    // No accepted Sending phase means no TX retirement. In particular, a
    // queued frame, failed preparation/CAD/start, or rejected admission cannot
    // use periodic beacon attempts to starve fixed maintenance.
    for (unsigned failure = 0; failure < 6; ++failure) {
      Fixture f(initial);
      configure_agc(f, adaptive);
      if (failure == 0) {
        f.radio.valid = false;
        assert(!queue(f));
      } else {
        if (failure == 2) f.radio.prepare = RadioParamApplyResult::FAILED;
        if (failure == 3) f.radio.channel_busy = true;
        if (failure == 4) f.radio.start_ok = false;
        if (failure == 5) f.radio.prepare = RadioParamApplyResult::BUSY;
        assert(queue(f));
        if (failure == 1) f.d.cancelBeaconRadioFrame();
        else f.d.loop();
      }
      f.clock.now += 100;
      f.d.cancelBeaconRadioFrame(); f.d.loop();
      assert(f.radio.agc == 1 && "unsent beacon must not postpone AGC");
      assert(f.d.getTotalAirTime() == 0);
    }
  }
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "basic")) basic();
  else if (!strcmp(argv[1], "preparation")) preparation();
  else if (!strcmp(argv[1], "failures")) failures();
  else if (!strcmp(argv[1], "restoration")) restoration();
  else if (!strcmp(argv[1], "availability")) availability();
  else if (!strcmp(argv[1], "priority")) priority();
  else if (!strcmp(argv[1], "owners")) owners();
  else if (!strcmp(argv[1], "budget")) budget_and_hour();
  else if (!strcmp(argv[1], "hourly")) hourly_cad_delay();
  else if (!strcmp(argv[1], "sleep")) sleep_deadlines();
  else if (!strcmp(argv[1], "begin")) begin_lifecycle();
  else if (!strcmp(argv[1], "agc")) agc_retirement();
  else assert(false);
  puts("PASS");
}
'''


class BeaconDispatcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise unittest.SkipTest("a host C++ compiler is required")
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-beacon-dispatcher-")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        cpp = work / "dispatcher.cpp"
        cpp.write_text(HARNESS, encoding="ascii")
        cls.binary = work / ("dispatcher.exe" if sys.platform == "win32" else "dispatcher")
        flags = ["-std=c++17", "-Wall", "-Wextra", "-Wno-unused-parameter",
                 "-DMESH_ENABLE_TX_BEACONS=1"]
        if sys.platform.startswith("linux"):
            flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        cls.compiler, cls.flags, cls.cpp, cls.work = compiler, flags, cpp, work
        result = cls.compile_variant(ROOT / "src/DispatcherBeacon.cpp", cls.binary)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def compile_variant(cls, beacon_source, binary, feature_flags=None):
        if feature_flags is None:
            feature_flags = ["-DMESH_ENABLE_MESHTEXT_CHAT=1"]
        return subprocess.run(
            [cls.compiler, *cls.flags, *feature_flags,
             "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
             str(ROOT / "src/Dispatcher.cpp"), str(beacon_source),
             str(ROOT / "src/Packet.cpp"), str(ROOT / "src/helpers/StaticPoolPacketManager.cpp"),
             str(cls.cpp), "-o", str(binary)], capture_output=True, text=True, timeout=90)

    def run_case(self, name):
        env = os.environ.copy()
        # The legacy static pool intentionally lives for the entire firmware
        # lifetime and has no teardown. Keep ASan/UBSan fatal, exclude only LSAN.
        env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
        result = subprocess.run(
            [str(self.binary), name], capture_output=True, text=True,
            env=env, cwd=self.work, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_one_tx_uses_private_tuple_preserves_radio2_and_accounts_airtime(self):
        self.run_case("basic")

    def test_prepare_busy_is_bounded_and_owned_failures_restore_exclusively(self):
        self.run_case("preparation")

    def test_cad_start_failure_invalid_airtime_and_tx_timeout_do_not_retry(self):
        self.run_case("failures")

    def test_restore_busy_blocks_normal_tx_rx_agc_and_noise_until_repaired(self):
        self.run_case("restoration")

    def test_suspension_and_reactivation_do_not_initialize_through_a_beacon_lease(self):
        self.run_case("availability")

    def test_due_normal_traffic_wins_and_meshtext_ownership_is_exclusive(self):
        self.run_case("priority")

    OWNER_FEATURES = {
        "chat": ["-DMESH_ENABLE_MESHTEXT_CHAT=1"],
        "ethernet": ["-DMESH_ENABLE_MESHTEXT_CHAT=0", "-DMESH_ENABLE_ETHERNET_TUNNEL=1"],
        "direct_ota": ["-DMESH_ENABLE_MESHTEXT_CHAT=0", "-DENABLE_OTA=1"],
        "none": ["-DMESH_ENABLE_MESHTEXT_CHAT=0", "-DMESH_ENABLE_FOREIGN_RADIO=0"],
    }

    def test_foreign_owners_are_exclusive_across_feature_matrix(self):
        for name, flags in self.OWNER_FEATURES.items():
            with self.subTest(features=name):
                binary = self.work / ("owner-" + name)
                compiled = self.compile_variant(ROOT / "src/DispatcherBeacon.cpp", binary, flags)
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0")
                checked = subprocess.run([str(binary), "owners"], capture_output=True,
                                         text=True, env=env, cwd=self.work, timeout=30)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn("PASS", checked.stdout)

    def test_old_chat_only_owner_guard_is_rejected(self):
        source = (ROOT / "src/DispatcherBeacon.cpp").read_text(encoding="ascii")
        guard = "#if MESH_ENABLE_FOREIGN_RADIO\n  if (hasForeignRadioFrame()) return false;"
        self.assertEqual(source.count(guard), 1)
        broken = self.work / "old-owner-guard.cpp"
        broken.write_text(source.replace(guard, guard.replace("FOREIGN_RADIO", "MESHTEXT_CHAT")),
                          encoding="ascii")
        for name in ("ethernet", "direct_ota"):
            with self.subTest(features=name):
                binary = self.work / ("old-owner-" + name)
                compiled = self.compile_variant(broken, binary, self.OWNER_FEATURES[name])
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0")
                checked = subprocess.run([str(binary), "owners"], capture_output=True,
                                         text=True, env=env, cwd=self.work, timeout=30)
                self.assertNotEqual(checked.returncode, 0, "old owner guard unexpectedly passed")
                self.assertIn("!queue(f)", checked.stderr)

    def test_budget_hour_guard_and_millisecond_rollover(self):
        self.run_case("budget")

    def test_hourly_admission_waits_for_actual_rf_guard_after_cad_delay(self):
        self.run_case("hourly")

    def test_old_hour_guard_at_admission_is_rejected_by_regression(self):
        source = (ROOT / "src/DispatcherBeacon.cpp").read_text(encoding="utf-8")
        marker = "  const uint32_t now = _ms->getMillis();\n"
        old_guard = "  if (beacon_has_started && uint32_t(now - beacon_last_start) < 3600000UL) return false;\n"
        self.assertIn(marker, source)
        broken = self.work / "old-admission.cpp"
        broken.write_text(source.replace(marker, marker + old_guard, 1), encoding="ascii")
        binary = self.work / ("old-admission.exe" if sys.platform == "win32" else "old-admission")
        compile_result = self.compile_variant(broken, binary)
        self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
        env = os.environ.copy()
        env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
        result = subprocess.run([str(binary), "hourly"], capture_output=True, text=True,
                                env=env, cwd=self.work, timeout=30)
        self.assertNotEqual(result.returncode, 0, "old admission guard unexpectedly passed")
        self.assertIn("queue(f)", result.stderr)

    def test_beacon_sleep_deadline_is_merged_with_adaptive_agc(self):
        self.run_case("sleep")

    def test_begin_retires_an_in_flight_beacon_before_radio_initialization(self):
        self.run_case("begin")

    def test_sent_beacon_retires_agc_once_like_normal_tx_but_unsent_work_does_not(self):
        self.run_case("agc")

    def test_missing_beacon_agc_retirement_negative_control(self):
        source = (ROOT / "src/DispatcherBeacon.cpp").read_text(encoding="ascii")
        notify = "  agc_reset.noteTransmit(_ms->getMillis());\n"
        self.assertEqual(source.count(notify), 1)
        broken = self.work / "old-beacon-agc.cpp"
        broken.write_text(source.replace(notify, ""), encoding="ascii")
        binary = self.work / ("old-beacon-agc.exe" if sys.platform == "win32" else "old-beacon-agc")
        compiled = self.compile_variant(broken, binary)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        checked = subprocess.run([str(binary), "agc"], capture_output=True, text=True,
                                 env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0"),
                                 cwd=self.work, timeout=30)
        self.assertNotEqual(checked.returncode, 0, "missing AGC retirement escaped regression")
        self.assertIn("beacon TX must follow normal fixed/adaptive AGC retirement policy", checked.stderr)


if __name__ == "__main__":
    unittest.main()
