// Execute tracker RF ownership against the real Dispatcher and packet manager.
// Only the radio/clock and CLI beacon schedule are hardware-boundary adapters.
#include <Dispatcher.h>
#include <Arduino.h>
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/PowerManagementUtils.h>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/MorseLoopPolicy.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>

constexpr uint32_t SENSOR_READ_INTERVAL_SECS = 60;
namespace mesh {
bool isUsbLoggingWatchdogArmed() { return false; }
class Mesh : public Dispatcher {
public:
  Mesh(Radio& radio, MillisecondClock& clock, PacketManager& manager)
      : Dispatcher(radio, clock, manager) {}
  void loop() { Dispatcher::loop(); }
};
}
struct Clock : mesh::MillisecondClock {
  uint32_t now;
  explicit Clock(uint32_t initial = 1000) : now(initial) {}
  unsigned long getMillis() override { return now; }
};
struct RTC { uint32_t now = 100; uint32_t getCurrentTime() const { return now; } };
struct Board {
  bool isUsbDataConnected() const { return false; }
  bool isOTAUpdateRunning() const { return false; }
  bool isRadioTestActive() const { return false; }
};
struct Radio : mesh::Radio {
  mesh::RadioProfiles profiles_state;
  bool asleep = false, sending = false, complete = false, beacon_owned = false;
  bool receive_retry = false, carrier = false, morse_tx = false, morse_rx = false;
  unsigned starts = 0, wakes = 0, sleeps = 0, agc = 0, beacon_sends = 0;
  mesh::RadioParamApplyResult restore_cr = mesh::RadioParamApplyResult::APPLIED;
  Radio() {
    auto& p = profiles_state.primary;
    p.freq = 906.5; p.bw = 62.5; p.sf = 7; p.cr = 5; p.preamble = 32;
  }
  mesh::RadioProfiles* profiles() override { return &profiles_state; }
  const mesh::RadioProfiles* profiles() const override { return &profiles_state; }
  void begin() override { assert(!asleep); ++starts; }
  int recvRaw(uint8_t*, int) override { return 0; }
  float packetScore(float, int) override { return 1; }
  uint32_t getEstAirtimeFor(int) override { return 100; }
  bool startSendRaw(const uint8_t*, int) override {
    assert(!asleep); sending = true; complete = false;
    if (beacon_owned) ++beacon_sends;
    return true;
  }
  bool isSendComplete() override { return complete; }
  void onSendFinished() override { sending = false; }
  bool isInRecvMode() const override { return !asleep && !sending; }
  bool isReceiving() override { return false; }
  bool isCarrierWaveActive() const override { return carrier; }
  bool isMorseTxActive() const override { return morse_tx; }
  bool isMorseRxActive() const override { return morse_rx; }
  bool setTrackerSleep(bool sleeping) override {
    assert(!sending && !beacon_owned);
    asleep = sleeping; sleeping ? ++sleeps : ++wakes; return true;
  }
  bool recoverRadio(bool) override { return true; }
  bool isWatchdogObserving() const { return false; }
  bool getReceiveRetryDelay(uint32_t, uint32_t& delay) const override {
    delay = 2000; return receive_retry;
  }
  bool setCodingRate(uint8_t) override { return true; }
  mesh::RadioParamApplyResult tryRestoreCodingRate(uint8_t) override { return restore_cr; }
  void resetAGC() override { assert(!asleep && !beacon_owned); ++agc; }
  bool tryResetAGC() override { resetAGC(); return true; }
  bool validateProfile(const mesh::RadioProfileParams& params) const override {
    return mesh::RadioProfiles::valid(params);
  }
  bool supportsMeshtext() const override { return true; }
#if MESH_ENABLE_TX_BEACONS
  uint32_t getBeaconAirtime(const mesh::RadioProfileParams&, int) override { return 100; }
  mesh::RadioParamApplyResult prepareBeaconTransmit(const mesh::RadioProfileParams&, int8_t) override {
    beacon_owned = true; return mesh::RadioParamApplyResult::APPLIED;
  }
  bool isBeaconRadioOwned() const override { return beacon_owned; }
  bool isBeaconChannelBusy() override { return false; }
  mesh::RadioParamApplyResult restoreBeaconReceive() override {
    beacon_owned = false; return mesh::RadioParamApplyResult::APPLIED;
  }
#endif
};
class SensorMesh;
struct CLI {
  SensorMesh* owner = nullptr;
  bool scheduled = false;
  unsigned attempts = 0;
  bool hasActiveUserGpioTimer() const { return false; }
  void loop();
};
class SensorMesh : public mesh::Mesh {
public:
  Clock& clock;
  Radio& radio_driver;
  Board board;
  RTC rtc;
  CLI _cli{this};
  struct { bool enabled = true; } tracker;
  struct { uint8_t powersaving_enabled = 1; } prefs;
  bool tracker_pending = false, tracker_acquiring = false, tracker_storage_fault = false;
  bool tracker_radio_asleep = false, saved_radio_apply_pending = false, temp_radio_applied = false;
  unsigned long tracker_awake_until = 0, set_radio_at = 0, revert_radio_at = 0;
  unsigned long next_flood_advert = 0, next_local_advert = 0, dirty_contacts_expiry = 0;
  unsigned long radio_apply_retry_at = 0;
  uint32_t tracker_next_check = 1900, last_read_time = 100;
  struct Alert { unsigned long send_expiry = 0; } alert;
  Alert* alert_tasks[1] = {&alert};
  int num_alert_tasks = 0, interval = 4000;
  bool retry = false;
  SensorMesh(Radio& radio, Clock& time, StaticPoolPacketManager& manager)
      : mesh::Mesh(radio, time, manager), clock(time), radio_driver(radio) { begin(); }
  RTC* getRTCClock() const { return const_cast<RTC*>(&rtc); }
  bool millisHasNowPassed(unsigned long timestamp) const {
    return int32_t(clock.now - uint32_t(timestamp)) > 0;
  }
  bool isAnyTempRadioActive() const { return false; }
  bool hasPendingOtaApply() const { return false; }
  bool getNextRetryWakeDelay(uint32_t& delay) const { delay = 1500; return retry; }
  bool hasRetryWorkDue() const { return false; }
  int getAGCResetInterval() const override { return interval; }
  bool useAdaptiveAGC() const override { return prefs.powersaving_enabled != 0; }
  mesh::DispatcherAction onRecvPacket(mesh::Packet*) override { return ACTION_RELEASE; }
  using mesh::Dispatcher::getNextQueueWakeDelay;
  using mesh::Dispatcher::hasPendingRadioWork;
  using mesh::Dispatcher::setRadioAvailable;
  using mesh::Dispatcher::isRadioAvailable;
#if MESH_ENABLE_TX_BEACONS
  using mesh::Dispatcher::getNextBeaconWakeDelay;
#endif
#if MESH_ENABLE_FOREIGN_RADIO
  using mesh::Dispatcher::queueForeignRadioFrame;
#endif
  bool trackerNeedsRadio() const;
  void wakeTrackerRadio();
  bool isMillisTimerDue(unsigned long) const;
  uint32_t limitSleepToMillisTimer(unsigned long, uint32_t) const;
  bool hasPendingWork() const;
  mesh::powerdiag::Mask pendingWorkReason() const;
  uint32_t getPowerSaveSleepSeconds(uint32_t) const;
  void loopBeforeService();
  void sleepIfIdle() { @SLEEP_BLOCK@ }
};
@METHODS@

void CLI::loop() {
#if MESH_ENABLE_TX_BEACONS
  uint32_t delay;
  if (scheduled && owner->getNextBeaconWakeDelay(delay) && delay == 0) {
    // BeaconManager consumes one admission attempt, even if the radio is
    // unavailable. Exercise that same boundary with the real queue API.
    ++attempts;
    const uint8_t bytes[] = {0x15, 0, 0x23};
    const auto params = owner->radio_driver.profiles_state.primary;
    assert(owner->queueBeaconRadioFrame(bytes, sizeof(bytes), params, 14)
        && "due beacon must wake RF before the CLI admission attempt");
    owner->setBeaconWakeDelay(owner->clock.now, 86400000, true);
  }
#endif
}
struct Fixture {
  Clock clock;
  Radio radio;
  StaticPoolPacketManager manager{8};
  SensorMesh sensor{radio, clock, manager};
  explicit Fixture(uint32_t initial = 1000) : clock(initial) { g_mock_millis = clock.now; }
  void advance(uint32_t elapsed) { clock.now += elapsed; g_mock_millis = clock.now; }
};
static mesh::Packet* packet(Fixture& f) {
  auto* p = f.sensor.obtainNewPacket(); assert(p);
  p->header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
  p->tx_radio = mesh::RADIO_TX_PRIMARY; p->payload_len = 1; p->payload[0] = 0x12;
  return p;
}
static void agc_deadlines() {
  for (bool powersaving : {false, true}) {
    Fixture f; f.sensor.prefs.powersaving_enabled = powersaving;
    f.sensor.mesh::Mesh::loop();
    uint32_t delay = 0;
    assert(f.sensor.getNextQueueWakeDelay(delay) == powersaving);
    if (powersaving) assert(delay == 4000);
    assert(!f.sensor.trackerNeedsRadio() && "a future AGC wake is not an RF lease");
    f.advance(4001);
    assert(!f.sensor.trackerNeedsRadio() && "due AGC is not an RF lease either");
    f.sensor.mesh::Mesh::loop(); assert(f.radio.agc == 1);
    f.sensor.sleepIfIdle();
    assert(f.sensor.tracker_radio_asleep && !f.sensor.isRadioAvailable());
    const auto starts = f.radio.starts;
    for (unsigned pass = 0; pass < 3; ++pass) {
      f.advance(60000);
      assert(!f.sensor.trackerNeedsRadio());
      assert(!f.sensor.getNextQueueWakeDelay(delay));
      assert(f.sensor.getPowerSaveSleepSeconds(30) == 30);
      f.sensor.loopBeforeService();
      assert(f.sensor.tracker_radio_asleep && f.radio.starts == starts);
    }
    f.sensor.tracker_next_check = f.sensor.rtc.now;
    f.sensor.loopBeforeService();
    assert(!f.sensor.tracker_radio_asleep && f.sensor.isRadioAvailable());
    assert(f.radio.starts == starts + 1 && f.radio.agc == 1);
    if (powersaving) assert(f.sensor.getNextQueueWakeDelay(delay) && delay == 4000);
  }
}
static void radio_leases() {
  {
    Fixture f; auto* p = packet(f);
    assert(f.sensor.sendPacket(p, 0, 5000));
    assert(f.sensor.trackerNeedsRadio());
    f.sensor.sleepIfIdle(); assert(!f.sensor.tracker_radio_asleep);
  }
  {
    Fixture f; auto* p = packet(f);
    f.manager.queueInbound(p, f.clock.now + 5000);
    assert(!f.manager.getOutboundTotal() && f.sensor.trackerNeedsRadio());
    f.sensor.sleepIfIdle(); assert(!f.sensor.tracker_radio_asleep);
  }
  {
    Fixture f; f.radio.receive_retry = true;
    assert(f.sensor.trackerNeedsRadio());
    f.sensor.sleepIfIdle(); assert(!f.sensor.tracker_radio_asleep);
    f.sensor.setRadioAvailable(false); f.sensor.tracker_radio_asleep = true;
    assert(!f.sensor.trackerNeedsRadio()); // Stale maintenance cannot wake dormant RF.
  }
  {
    Fixture f; f.sensor.retry = true;
    assert(f.sensor.trackerNeedsRadio());
    f.sensor.sleepIfIdle(); assert(!f.sensor.tracker_radio_asleep);
  }
  for (unsigned owner = 0; owner < 3; ++owner) {
    Fixture f;
    f.radio.carrier = owner == 0; f.radio.morse_tx = owner == 1; f.radio.morse_rx = owner == 2;
    assert(f.sensor.trackerNeedsRadio());
    f.sensor.sleepIfIdle(); assert(!f.sensor.tracker_radio_asleep);
  }
  {
    Fixture f; auto* p = packet(f); p->tx_cr = 6;
    assert(f.sensor.sendPacket(p, 0));
    f.advance(1); f.sensor.mesh::Mesh::loop();
    assert(f.radio.sending && f.sensor.trackerNeedsRadio());
    f.radio.complete = true; f.radio.restore_cr = mesh::RadioParamApplyResult::BUSY;
    f.advance(100); f.sensor.mesh::Mesh::loop();
    assert(!f.sensor.hasOutbound() && !f.manager.getOutboundTotal());
    assert(f.sensor.trackerNeedsRadio() && "pending CR restoration retains the radio lease");
    f.sensor.sleepIfIdle(); assert(!f.sensor.tracker_radio_asleep);
    f.radio.restore_cr = mesh::RadioParamApplyResult::APPLIED;
    f.advance(251); f.sensor.mesh::Mesh::loop();
    assert(!f.sensor.trackerNeedsRadio());
  }
#if MESH_ENABLE_FOREIGN_RADIO
  {
    Fixture f; const uint8_t bytes[] = {1, 2, 3};
    auto& p = f.radio.profiles_state;
    p.secondary.params = p.primary; p.secondary.params.protocol = mesh::RadioProfileProtocol::Meshtext;
    p.secondary.mode = mesh::RadioProfileMode::RxTx;
    assert(f.sensor.queueForeignRadioFrame(bytes, sizeof(bytes), 1, p.generation[1]));
    assert(f.sensor.hasPendingRadioWork());
  }
#endif
}
static void beacon_deadlines() {
#if MESH_ENABLE_TX_BEACONS
  for (bool wrap : {false, true}) {
    Fixture f(wrap ? UINT32_MAX - 1500U : 1000U);
    f.sensor._cli.scheduled = true;
    f.sensor.setBeaconWakeDelay(f.clock.now, 2500, true);
    assert(!f.sensor.trackerNeedsRadio());
    f.sensor.sleepIfIdle();
    assert(f.sensor.tracker_radio_asleep && !f.sensor.isRadioAvailable());
    uint32_t delay = 0;
    assert(!f.sensor.getNextQueueWakeDelay(delay)); // Normal unavailable-radio policy is unchanged.
    assert(f.sensor.getNextBeaconWakeDelay(delay) && delay == 2500);
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM) || defined(STM32_PLATFORM)
    constexpr unsigned sleep = 3;
#else
    constexpr unsigned sleep = 2;
#endif
    assert(f.sensor.getPowerSaveSleepSeconds(30) == sleep
        && "RF suspension must not hide the next beacon MCU wake");
    f.advance(2499); f.sensor.loopBeforeService();
    assert(f.sensor.tracker_radio_asleep && f.sensor._cli.attempts == 0);
    f.advance(1);
    assert(f.sensor.getPowerSaveSleepSeconds(30) == 0);
    f.sensor.loopBeforeService();
    assert(!f.sensor.tracker_radio_asleep && f.sensor.isRadioAvailable());
    assert(f.sensor._cli.attempts == 1 && f.radio.beacon_sends == 1);
    assert(f.sensor.trackerNeedsRadio()); f.sensor.sleepIfIdle();
    assert(!f.sensor.tracker_radio_asleep);
    f.radio.complete = true; f.advance(100); f.sensor.loopBeforeService();
    assert(!f.sensor.hasOutbound() && !f.sensor.trackerNeedsRadio());
    f.sensor.sleepIfIdle(); assert(f.sensor.tracker_radio_asleep);
    f.sensor.setBeaconWakeDelay(f.clock.now, 0, false);
    assert(!f.sensor.getNextBeaconWakeDelay(delay));
    assert(f.sensor.getPowerSaveSleepSeconds(30) == 30);
  }
#endif
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "agc")) agc_deadlines();
  else if (!strcmp(argv[1], "leases")) radio_leases();
  else if (!strcmp(argv[1], "beacons")) beacon_deadlines();
  else assert(false);
  puts("PASS: tracker radio lease");
}
