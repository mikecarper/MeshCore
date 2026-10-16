#include <helpers/rp2040/SerialBLEInterface.h>
#include <helpers/MultiSerialInterface.h>
#include <cassert>
#include <cstdio>
#include <string>

using fake_pico::Bytes;
@QUERY_PRODUCER@

struct Fixture {
  SerialBLEInterface node;
  BLECharacteristic *rx = nullptr, *tx = nullptr;
  Fixture() {
    fake_pico::now = 100;
    assert(node.begin("MeshCore-", "PicoW", 123456)); node.enable();
    for (auto* characteristic : BLE.server()->services[0]->characteristics) {
      if (characteristic->uuid.bytes[3] == 2) rx = characteristic;
      if (characteristic->uuid.bytes[3] == 3) tx = characteristic;
    }
    assert(rx && tx);
    fake_pico::disconnect_event = []() { BLE.server()->disconnectEvent(); };
    BLE.server()->connect(); tx->setNotify(true); fake_pico::mtu = 247;
    assert(node.isConnected());
  }
  void write(const Bytes& frame) { assert(node.writeFrame(frame.data(), frame.size()) == frame.size()); }
  void query() { rx->hostWrite(Bytes{CMD_DEVICE_QUERY, 3}); }
  size_t receive(uint8_t* input) { return node.checkRecvFrame(input); }
  void dispatch() {
    uint8_t input[MAX_FRAME_SIZE]{};
    const size_t length = receive(input); assert(length == 2);
    ActualDeviceQuery mesh; mesh._serial = &node; mesh.dispatch(input, length);
  }
  void tick(uint32_t delay = 10) { fake_pico::now += delay; node.loop(); }
  void drain() {
    for (unsigned i = 0; i < 20; ++i) {
      if (!fake_pico::pending.empty()) fake_pico::fireOne();
      tick();
    }
  }
};

struct HoldingUsb : BaseSerialInterface {
  bool pending = true;
  void enable() override {}
  void disable() override {}
  bool isEnabled() const override { return true; }
  bool isConnected() const override { return true; }
  bool isReadBusy() const override { return pending; }
  bool isWriteBusy() const override { return false; }
  size_t writeFrame(const uint8_t*, size_t length) override { return length; }
  size_t checkRecvFrame(uint8_t* out) override {
    if (!pending) return 0;
    pending = false; out[0] = 4; return 1;
  }
};

static void test_stall(const std::string& scenario) {
  Fixture f;
  fake_pico::now = scenario == "stall-wrap" ? UINT32_MAX - 5000 : 0;
  fake_pico::synchronous = scenario == "stall-register" || scenario == "stall-notify" || scenario == "stall-mtu";
  if (scenario == "stall-register") fake_pico::registration_error = ERROR_CODE_UNKNOWN_CONNECTION_IDENTIFIER;
  if (scenario == "stall-notify") fake_pico::notify_errors = {BTSTACK_ACL_BUFFERS_FULL, BTSTACK_ACL_BUFFERS_FULL};
  if (scenario == "stall-mtu") fake_pico::mtu = 23;
  fake_pico::disconnect_error = ERROR_CODE_COMMAND_DISALLOWED;
  Bytes required(scenario == "stall-mtu" ? 82 : 2, 0x41); required[0] = 0x09;
  f.write(required); f.node.loop();
  if (scenario == "stall-cccd") f.tx->setNotify(false);
  f.tick(9999); assert(fake_pico::disconnects == 0);
  f.tick(1);
  assert(fake_pico::disconnects == 1 && fake_pico::connection_live);
  assert(!f.node.isConnected() && f.node.isWriteBusy() && f.node.hasPendingIO());
  uint8_t input[MAX_FRAME_SIZE]{};
  f.query(); assert(f.receive(input) == 0);
  const uint8_t reply[] = {0x09, 0x42};
  assert(f.node.writeFrame(reply, sizeof(reply)) == 0);
  f.tx->setNotify(false); f.tick(999); assert(fake_pico::disconnects == 1);
  f.tick(1); assert(fake_pico::disconnects == 2 && !f.node.isConnected());
  fake_pico::disconnect_error = 0;
  f.tick(1000); assert(fake_pico::disconnects == 3 && fake_pico::connection_live);
  // Request acceptance and missing CCCD cannot end recovery before closure.
  assert(!f.node.isConnected() && f.node.hasPendingIO());
  BLE.server()->disconnectEvent(); assert(!f.node.hasPendingIO());
  assert(fake_pico::advertising_active);
  fake_pico::synchronous = true;
  fake_pico::registration_error = 0; fake_pico::notify_errors.clear(); fake_pico::mtu = 247;
  BLE.server()->connect(2); f.tx->setNotify(true);
  assert(f.node.isConnected()); f.query(); f.dispatch(); f.tick();
  assert(fake_pico::notified.size() == 1 && fake_pico::notified[0].size() == 82);
  assert(fake_pico::notified[0][0] == 13);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  if (scenario.compare(0, 6, "stall-") == 0) {
    test_stall(scenario);
  } else {
    Fixture f;
    if (scenario == "normal") {
      Bytes maximum(MAX_FRAME_SIZE);
      for (unsigned i = 0; i < maximum.size(); ++i) maximum[i] = uint8_t(i);
      f.write(maximum); f.write(Bytes{0x09, 0x41});
      assert(fake_pico::notified == (std::vector<Bytes>{maximum, Bytes{0x09, 0x41}}));
      assert(!f.node.hasPendingIO() && fake_pico::max_callback_depth == 1);
      assert(f.node.writeFrame(nullptr, 1) == 0);
      assert(f.node.writeFrame(maximum.data(), 0) == 0);
      assert(f.node.writeFrame(maximum.data(), MAX_FRAME_SIZE + 1) == 0);
      assert(f.node.checkRecvFrame(nullptr) == 0);
      f.rx->hostWrite({}); f.rx->hostWrite(Bytes(MAX_FRAME_SIZE + 1, 1));
      uint8_t input[MAX_FRAME_SIZE + 2]{}; input[0] = input[MAX_FRAME_SIZE + 1] = 0xa5;
      assert(f.receive(input + 1) == 0);
      f.rx->hostWrite(maximum); assert(f.receive(input + 1) == MAX_FRAME_SIZE);
      assert(input[0] == 0xa5 && input[MAX_FRAME_SIZE + 1] == 0xa5);
      assert(std::equal(maximum.begin(), maximum.end(), input + 1));
    } else if (scenario == "admission") {
      fake_pico::synchronous = false;
      for (unsigned i = 0; i < 8; ++i) f.write(Bytes{0x09, uint8_t(i)});
      f.query(); uint8_t input[MAX_FRAME_SIZE]{};
      assert(f.receive(input) == 0
          && "a full required TX queue must retain the command until its reply fits");
      fake_pico::fireOne(); assert(f.receive(input) == 2);
      ActualDeviceQuery mesh; mesh._serial = &f.node; mesh.dispatch(input, 2);
      f.drain();
      assert(fake_pico::notified.size() == 9 && fake_pico::notified.back().size() == 82);
      assert(fake_pico::notified.back()[0] == 13 && !f.node.hasPendingIO());
    } else if (scenario == "priority") {
      fake_pico::synchronous = false;
      const Bytes log{0x88, 0x41};
      for (unsigned i = 0; i < 7; ++i) f.write(log);
      assert(f.node.writeFrame(log.data(), log.size()) == 0);
      f.query(); f.dispatch(); f.drain();
      assert(fake_pico::notified.size() == 8 && fake_pico::notified[0].size() == 82);
      assert(fake_pico::notified[0][0] == 13);
      for (unsigned i = 1; i < 8; ++i) assert(fake_pico::notified[i] == log);
      fake_pico::notified.clear();
      for (unsigned i = 0; i < 30; ++i) f.write(Bytes{0x83});
      f.drain(); assert(fake_pico::notified == std::vector<Bytes>{Bytes{0x83}});
      fake_pico::notified.clear();
      for (unsigned i = 0; i < 7; ++i) f.write(log);
      f.write(Bytes{0x85, 1}); f.write(Bytes{0x09, 2}); f.drain();
      assert(fake_pico::notified.size() == 8);
      assert(fake_pico::notified[0] == (Bytes{0x09, 2}));
      assert(fake_pico::notified[1] == (Bytes{0x85, 1}));
    } else if (scenario == "mtu") {
      fake_pico::mtu = 23; f.query(); f.dispatch();
      assert(f.node.hasPendingIO() && fake_pico::notified.empty()
          && "required DEVICE_QUERY response must survive an initial MTU of 23");
      const unsigned attempts = fake_pico::registrations;
      f.tick(9); assert(fake_pico::registrations == attempts);
      f.tick(1); assert(fake_pico::notified.empty());
      fake_pico::mtu = 247; f.tick(); f.tick();
      assert(fake_pico::notified.size() == 1 && fake_pico::notified[0].size() == 82);
      assert(fake_pico::notified[0][0] == 13 && !f.node.hasPendingIO());
    } else if (scenario == "notify") {
      Bytes maximum(MAX_FRAME_SIZE, 0x31); maximum[0] = 13;
      fake_pico::notify_errors = {BTSTACK_ACL_BUFFERS_FULL, ERROR_CODE_COMMAND_DISALLOWED, 0};
      f.write(maximum); assert(fake_pico::notified.empty() && f.node.hasPendingIO());
      assert(fake_pico::registrations == 1 && fake_pico::max_callback_depth == 1);
      f.tick(9); assert(fake_pico::registrations == 1);
      f.tick(1); assert(fake_pico::registrations == 2 && fake_pico::notified.empty());
      f.tick(); f.tick();
      assert(fake_pico::notified == std::vector<Bytes>{maximum});
      assert(fake_pico::notification_attempts == 3 && !f.node.hasPendingIO());
    } else if (scenario == "registration" || scenario == "registration-sync-duplicate") {
      fake_pico::synchronous = scenario == "registration-sync-duplicate";
      fake_pico::duplicate_registration = true;
      f.write(Bytes{0x09, 1});
      if (!fake_pico::synchronous) {
        for (unsigned i = 0; i < 10; ++i) f.tick();
        assert(fake_pico::registrations == 1 && fake_pico::pending.size() == 1);
        fake_pico::fireOne();
      }
      assert(fake_pico::notified == (std::vector<Bytes>{Bytes{0x09, 1}}));
      assert(!f.node.hasPendingIO());
      f.write(Bytes{0x09, 2});
      if (!fake_pico::synchronous) fake_pico::fireOne();
      assert(fake_pico::notified.size() == 2 && fake_pico::registrations == 2);
    } else if (scenario == "registration-error" || scenario == "callback-clock") {
      fake_pico::registration_error = ERROR_CODE_UNKNOWN_CONNECTION_IDENTIFIER;
      f.write(Bytes{0x09, 1}); assert(fake_pico::registrations == 1);
      fake_pico::registration_error = 0;
      if (scenario == "callback-clock") fake_pico::now += 9;
      else f.tick(9);
      assert(fake_pico::registrations == 1);
      if (scenario == "callback-clock") {
        fake_pico::callback_advance_ms = 1;
        fake_pico::notify_errors.push_back(BTSTACK_ACL_BUFFERS_FULL);
      }
      f.tick(1);
      assert(fake_pico::disconnects == 0 && f.node.isConnected()
          && "a synchronous callback must not backdate the stall watchdog");
      f.tick();
      assert(fake_pico::notified == (std::vector<Bytes>{Bytes{0x09, 1}}));
    } else if (scenario == "deadline-progress") {
      fake_pico::mtu = 23; f.query(); f.dispatch();
      fake_pico::mtu = 247; f.tick(10000);
      assert(fake_pico::notified.size() == 1 && fake_pico::disconnects == 0);
    } else if (scenario == "best-effort") {
      fake_pico::mtu = 23; Bytes log(64, 1); log[0] = 0x88; f.write(log);
      f.tick(100000); assert(fake_pico::disconnects == 0 && !f.node.hasPendingIO());
      fake_pico::mtu = 247;
      fake_pico::notify_errors.push_back(BTSTACK_ACL_BUFFERS_FULL);
      f.write(Bytes{0x88, 1}); f.tick(100000);
      assert(fake_pico::disconnects == 0 && f.node.isConnected() && !f.node.hasPendingIO());
    } else if (scenario == "locked") {
      HoldingUsb usb; MultiSerialInterface manager;
      assert(manager.addInterface(InterfaceType::USB, &usb));
      assert(manager.addInterface(InterfaceType::Bluetooth, &f.node)); manager.enable();
      uint8_t input[MAX_FRAME_SIZE]{};
      assert(manager.checkRecvFrame(input) == 1); manager.lockReplyRoute();
      fake_pico::mtu = 23; Bytes required(82, 1); required[0] = 13; f.write(required);
      f.query(); assert(manager.checkRecvFrame(input) == 0);
      fake_pico::mtu = 247; fake_pico::now += 10; manager.loop();
      assert(fake_pico::notified == std::vector<Bytes>{required});
      assert(manager.captureReplyRoute() == &usb && manager.checkRecvFrame(input) == 0);
      fake_pico::mtu = 23; f.write(required);
      fake_pico::now += 10000; manager.loop();
      assert(fake_pico::disconnects == 1 && !manager.isReplyRouteAvailable(&f.node));
      assert(manager.captureReplyRoute() == &usb && manager.isConnected());
    } else if (scenario == "disabled-reenable" || scenario == "disabled-closed") {
      fake_pico::disconnect_error = ERROR_CODE_COMMAND_DISALLOWED;
      f.node.disable(); assert(!f.node.isConnected() && !fake_pico::advertising_requested);
      if (scenario == "disabled-reenable") f.node.enable();
      assert(fake_pico::advertising_starts == 1 && !fake_pico::advertising_active);
      f.tick(1000); assert(fake_pico::disconnects == 2 && !f.node.isConnected());
      BLE.server()->disconnectEvent();
      assert(!fake_pico::advertising_active); f.tick();
      const bool enabled = scenario == "disabled-reenable";
      assert(fake_pico::advertising_active == enabled);
      assert(fake_pico::advertising_starts == unsigned(enabled ? 2 : 1));
      assert(!f.node.hasPendingIO());
    } else if (scenario == "late-connect") {
      BLE.server()->disconnectEvent(); f.node.disable();
      BLE.server()->connect(2); // Controller completed connection after stopAdvertising.
      assert(!f.node.isConnected() && f.node.hasPendingIO());
      f.node.enable(); assert(fake_pico::advertising_starts == 1);
      f.tick(); assert(fake_pico::disconnects == 1 && !f.node.isConnected());
      BLE.server()->disconnectEvent(); f.tick();
      assert(fake_pico::advertising_active && fake_pico::advertising_starts == 2);
    } else if (scenario == "synchronous-disconnect") {
      fake_pico::synchronous_disconnect = true;
      f.node.disconnect();
      assert(!fake_pico::connection_live && !f.node.hasPendingIO() && !f.node.isConnected());
      assert(fake_pico::disconnects == 1);
      f.tick(1000); assert(fake_pico::disconnects == 1);
    } else if (scenario == "pending-cccd") {
      fake_pico::synchronous = false; f.query(); f.dispatch();
      f.tx->setNotify(false); fake_pico::fireOne();
      assert(f.node.hasPendingIO() && fake_pico::notified.empty());
      f.tx->setNotify(true); f.tick(); fake_pico::fireOne();
      assert(fake_pico::notified.size() == 1 && fake_pico::notified[0].size() == 82);
      assert(!f.node.hasPendingIO());
    } else if (scenario == "cccd") {
      f.tx->setNotify(false); f.query(); uint8_t input[MAX_FRAME_SIZE]{};
      assert(f.receive(input) == 0);
      f.tx->setNotify(true); f.dispatch();
      assert(fake_pico::notified.size() == 1 && fake_pico::notified[0].size() == 82);
    } else assert(false && "unknown scenario");
  }
  assert(fake_pico::lock_depth == 0 && fake_pico::callback_depth == 0);
  std::printf("PASS: Pico BLE delivery %s\n", scenario.c_str());
}
