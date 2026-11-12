#include <helpers/ArduinoSerialInterface.h>
#include <helpers/MultiSerialInterface.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <limits>
#include <vector>

#define REQUIRE(condition) do { \
  if (!(condition)) { \
    std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #condition); \
    std::exit(1); \
  } \
} while (0)

using Bytes = std::vector<uint8_t>;
static const char START_TOKEN[] = "+++MESHCORE-TERM-START";
static const char MOTA_TOKEN[] = "ota folder on";

class BufferStream : public Stream {
public:
  std::deque<uint8_t> input;
  Bytes output;
  int capacity = 0;
  bool reports_capacity = true;
  size_t max_write = std::numeric_limits<size_t>::max();
  unsigned reads = 0;

  void push(const Bytes& bytes) { input.insert(input.end(), bytes.begin(), bytes.end()); }
  void push(const char* text) { while (*text) input.push_back(uint8_t(*text++)); }
  int available() override { return static_cast<int>(input.size()); }
  int availableForWrite() override { return reports_capacity ? capacity : 0; }
  int peek() override { return input.empty() ? -1 : input.front(); }
  int read() override {
    if (input.empty()) return -1;
    const uint8_t value = input.front();
    input.pop_front();
    ++reads;
    return value;
  }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* data, size_t len) override {
    const size_t written = std::min(std::min(len, max_write), size_t(std::max(capacity, 0)));
    output.insert(output.end(), data, data + written);
    capacity -= int(written);
    return written;
  }
};

#define RESP_CODE_OK 0
#define RESP_CODE_ERR 1
struct MyMesh {
  BaseSerialInterface* _serial;
  void writeOKFrame(BaseSerialInterface* route = nullptr);
  void writeErrFrame(uint8_t code, BaseSerialInterface* route = nullptr);
};
// The runner extracts these immediate reply methods from production MyMesh.cpp.
#include "immediate_reply_methods.inc"

static Bytes framed(const std::vector<Bytes>& frames) {
  Bytes result;
  for (const Bytes& frame : frames) {
    result.push_back('>');
    result.push_back(uint8_t(frame.size()));
    result.push_back(uint8_t(frame.size() >> 8));
    result.insert(result.end(), frame.begin(), frame.end());
  }
  return result;
}

struct Fixture {
  BufferStream stream;
  ArduinoSerialInterface usb;
  MultiSerialInterface manager;
  MyMesh mesh{&manager};
  uint8_t command[MAX_FRAME_SIZE] = {};

  Fixture() {
    g_mock_millis = 100;
    usb.begin(stream, START_TOKEN, MOTA_TOKEN);
    usb.enableFlowControl(true);
    REQUIRE(manager.addInterface(InterfaceType::USB, &usb));
    manager.enable();
  }
  size_t tick() { return manager.checkRecvFrame(command); }
  void issue(uint8_t tag) { stream.push(Bytes{'<', 1, 0, tag}); }
  void reply() { mesh.writeErrFrame(command[0]); }
  void enqueue(const Bytes& frame) {
    REQUIRE(usb.writeFrame(frame.data(), frame.size()) == frame.size());
  }
  std::vector<Bytes> fillRequired() {
    std::vector<Bytes> result;
    for (uint8_t i = 0; i < 4; ++i) {
      result.push_back(Bytes{1, uint8_t(0xA0 + i)});
      enqueue(result.back());
    }
    return result;
  }
  void flush() {
    stream.capacity = 65536;
    stream.max_write = std::numeric_limits<size_t>::max();
    manager.loop();
    REQUIRE(!usb.isWriteBusy());
  }
};

static void burst() {
  Fixture f;
  std::vector<Bytes> expected;
  for (uint8_t i = 0; i < 5; ++i) f.issue(uint8_t(0x40 + i));
  for (uint8_t i = 0; i < 4; ++i) {
    REQUIRE(f.tick() == 1);
    REQUIRE(f.command[0] == 0x40 + i);
    f.reply();
    expected.push_back(Bytes{1, uint8_t(0x40 + i)});
  }
  const unsigned reads = f.stream.reads;
  for (unsigned i = 0; i < 8; ++i) {
    REQUIRE(f.tick() == 0);
    REQUIRE(f.stream.reads == reads && f.stream.input.size() == 4);
    REQUIRE(f.usb.getCompletedFrameCount() == 4);
    g_mock_millis += 1000;
  }
  // Release exactly one complete reply, then accept the waiting command into
  // that slot even though every later write is still backpressured.
  f.stream.capacity = 5;
  REQUIRE(f.tick() == 1 && f.command[0] == 0x44);
  f.reply();
  expected.push_back(Bytes{1, 0x44});
  f.flush();
  REQUIRE(f.stream.output == framed(expected));
  REQUIRE(f.stream.input.empty() && !f.manager.hasPendingIO());
}

static void partialRxBudget() {
  for (uint32_t started : {100U, UINT32_MAX - 500U}) {
    for (const Bytes& prefix : {Bytes{'<'}, Bytes{'<', 2}, Bytes{'<', 2, 0, 0x40}}) {
      Fixture f;
      g_mock_millis = started;
      f.stream.push(prefix);
      REQUIRE(f.tick() == 0 && f.usb.isReadBusy());
      g_mock_millis += 250;
      f.fillRequired();
      g_mock_millis += 5000;
      f.manager.loop();
      REQUIRE(f.usb.isReadBusy());
      f.flush();
      g_mock_millis += 749;
      f.manager.loop();
      REQUIRE(f.usb.isReadBusy());
      ++g_mock_millis;
      f.manager.loop();
      REQUIRE(!f.usb.isReadBusy() && !f.manager.hasPendingIO());
    }
  }
}

static void partialRxResume() {
  const Bytes wire{'<', 6, 0, 0x70, '<', '>', '\r', 0, 0xEE};
  for (size_t split = 1; split < wire.size(); ++split) {
    Fixture f;
    f.stream.push(Bytes(wire.begin(), wire.begin() + split));
    REQUIRE(f.tick() == 0 && f.usb.isReadBusy());
    std::vector<Bytes> expected = f.fillRequired();
    f.stream.push(Bytes(wire.begin() + split, wire.end()));
    f.issue(0x72);
    const unsigned reads = f.stream.reads;
    g_mock_millis += 5000;
    REQUIRE(f.tick() == 0 && f.stream.reads == reads);
    f.stream.capacity = 5;
    REQUIRE(f.tick() == 6);
    REQUIRE(Bytes(f.command, f.command + 6) == Bytes(wire.begin() + 3, wire.end()));
    f.reply();
    expected.push_back(Bytes{1, 0x70});
    REQUIRE(f.tick() == 0 && f.stream.input.size() == 4);
    f.flush();
    REQUIRE(f.tick() == 1 && f.command[0] == 0x72);
    f.reply();
    expected.push_back(Bytes{1, 0x72});
    f.flush();
    REQUIRE(f.stream.output == framed(expected));
  }
}

static void evictableCapacity() {
  for (bool pinned : {false, true}) {
    Fixture f;
    Bytes push(MAX_FRAME_SIZE, 0xA5);
    push[0] = 0x88;
    f.enqueue(push);
    if (pinned) {
      f.stream.capacity = 2;
      f.manager.loop();
      REQUIRE(f.stream.output.size() == 2);
    }
    const Bytes required{1, 0xA0};
    for (unsigned i = 0; i < 3; ++i) f.enqueue(required);
    f.issue(0x40);
    const unsigned reads = f.stream.reads;
    if (pinned) {
      REQUIRE(f.tick() == 0 && f.stream.reads == reads);
      // The partly transmitted best-effort head cannot create reply capacity
      // until every byte has left the stream; its suffix must remain intact.
      f.stream.capacity = MAX_FRAME_SIZE + 1;
    }
    REQUIRE(f.tick() == 1 && f.command[0] == 0x40);
    f.reply();
    f.flush();
    std::vector<Bytes> expected;
    if (pinned) expected.push_back(push);
    for (unsigned i = 0; i < 3; ++i) expected.push_back(required);
    expected.push_back(Bytes{1, 0x40});
    REQUIRE(f.stream.output == framed(expected));
  }
  // An unsent best-effort tail remains evictable behind a partly sent required
  // head; admission must not stop merely because all four slots are occupied.
  Fixture f;
  const Bytes required{1, 0xA0};
  f.enqueue(required);
  f.stream.capacity = 2;
  f.manager.loop();
  f.enqueue(Bytes{0x88, 0xFF});
  f.enqueue(required);
  f.enqueue(required);
  f.issue(0x40);
  REQUIRE(f.tick() == 1);
  f.reply();
  f.flush();
  REQUIRE(f.stream.output == framed({required, required, required, Bytes{1, 0x40}}));
}

static void controlsAndPassthrough() {
  for (bool secondary : {false, true}) {
    for (bool partial : {false, true}) {
      Fixture f;
      const char* token = secondary ? MOTA_TOKEN : START_TOKEN;
      if (partial) {
        f.stream.push(Bytes(token, token + 5));
        REQUIRE(f.tick() == 0);
        token += 5;
      }
      f.fillRequired();
      f.stream.push(token);
      f.stream.push("\rver\r");
      const unsigned reads = f.stream.reads;
      REQUIRE(f.tick() == 0 && f.stream.reads == reads);
      REQUIRE(!f.usb.takeControlSequence() && !f.usb.takeSecondaryControlSequence());
      f.flush();
      REQUIRE(f.tick() == 0);
      REQUIRE(f.usb.takeControlSequence() == !secondary);
      REQUIRE(f.usb.takeSecondaryControlSequence() == secondary);
      REQUIRE(f.stream.input == std::deque<uint8_t>({'v', 'e', 'r', '\r'}));
      f.usb.setPassthroughMode(true);
      const Bytes before = f.stream.output;
      const uint8_t response[] = {0};
      REQUIRE(f.usb.writeFrame(response, sizeof(response)) == sizeof(response));
      REQUIRE(f.tick() == 0 && f.stream.input.size() == 4);
      REQUIRE(f.stream.output == before);
    }
  }
}

static void resetsAndUncontrolledStreams() {
  for (unsigned mode = 0; mode < 4; ++mode) {
    Fixture f;
    f.stream.push(Bytes{'<', 2, 0, 0x40});
    REQUIRE(f.tick() == 0 && f.usb.isReadBusy());
    f.fillRequired();
    g_mock_millis += 5000;
    if (mode == 0) {
      f.usb.enableFlowControl(false);
      f.stream.push(Bytes{0x41});
      REQUIRE(f.tick() == 2 && f.command[0] == 0x40 && f.command[1] == 0x41);
    } else {
      if (mode == 1) f.usb.resetSessionState();
      if (mode == 2) { f.usb.setPassthroughMode(true); f.usb.setPassthroughMode(false); }
      if (mode == 3) { f.usb.disable(); f.usb.enable(); }
      f.issue(0x42);
      REQUIRE(f.tick() == 1 && f.command[0] == 0x42);
    }
    REQUIRE(!f.usb.isReadBusy() && !f.manager.hasPendingIO());
  }
  Fixture f;
  f.usb.enableFlowControl(false);
  f.stream.capacity = 4096;
  for (unsigned i = 0; i < 5; ++i) {
    f.issue(0x40);
    REQUIRE(f.tick() == 1);
    f.mesh.writeOKFrame();
  }
  REQUIRE(f.stream.output == framed({Bytes{0}, Bytes{0}, Bytes{0}, Bytes{0}, Bytes{0}}));
}

static void expiredBeforeBackpressure() {
  Fixture f;
  f.stream.push(Bytes{'<', 2, 0, 0x40});
  REQUIRE(f.tick() == 0 && f.usb.isReadBusy());
  g_mock_millis += 1000;
  f.fillRequired();
  f.manager.loop();
  REQUIRE(!f.usb.isReadBusy());
  // The normal incomplete-frame timeout still works when there is no TX
  // backpressure and must remain exact across the millis wrap boundary.
  Fixture normal;
  g_mock_millis = UINT32_MAX - 500U;
  normal.stream.push(Bytes{'<', 2});
  REQUIRE(normal.tick() == 0);
  g_mock_millis += 999;
  normal.manager.loop();
  REQUIRE(normal.usb.isReadBusy());
  ++g_mock_millis;
  normal.manager.loop();
  REQUIRE(!normal.usb.isReadBusy());
}

class OtherInterface : public BaseSerialInterface {
public:
  bool pending = true;
  void enable() override {}
  void disable() override {}
  bool isEnabled() const override { return true; }
  bool isConnected() const override { return true; }
  bool isReadBusy() const override { return false; }
  bool isWriteBusy() const override { return false; }
  size_t writeFrame(const uint8_t*, size_t len) override { return len; }
  size_t checkRecvFrame(uint8_t* dest) override {
    if (!pending) return 0;
    pending = false;
    dest[0] = 0x79;
    return 1;
  }
};

static void independentRoute() {
  Fixture f;
  OtherInterface other;
  REQUIRE(f.manager.addInterface(InterfaceType::Bluetooth, &other));
  f.fillRequired();
  f.issue(0x40);
  REQUIRE(f.tick() == 1 && f.command[0] == 0x79);
  REQUIRE(f.manager.captureReplyRoute() == &other);
  REQUIRE(f.stream.reads == 0 && f.stream.input.size() == 4);
  f.flush();
  REQUIRE(f.tick() == 1 && f.command[0] == 0x40);
  REQUIRE(f.manager.captureReplyRoute() == &f.usb);
}

static Bytes radio2Log() {
  Bytes log(MAX_FRAME_SIZE);
  log[0] = 0x92;
  log[1] = 10;
  log[2] = uint8_t(-92);
  for (size_t i = 3; i < log.size(); ++i) log[i] = uint8_t(i);
  return log;
}

static Bytes legacyLog(const Bytes& log) {
  Bytes legacy = log;
  legacy[0] = 0x88;
  return legacy;
}

static void radio2WireSplits() {
  const Bytes log = radio2Log(), reply{0};
  const Bytes expected = framed({Bytes{0x92, 0xA1}, legacyLog(log), reply});
  const size_t group_size = expected.size() - framed({reply}).size();
  // Cover every stop point in the marker, both raw-frame header boundaries,
  // SNR/RSSI and the complete maximum-size payload. A queued response may
  // never appear between the marker and the legacy raw frame.
  for (size_t split = 0; split <= group_size; ++split) {
    Fixture f;
    f.stream.capacity = int(split);
    f.enqueue(log);
    REQUIRE(f.stream.output == Bytes(expected.begin(), expected.begin() + split));
    f.enqueue(reply);
    f.flush();
    REQUIRE(f.stream.output == expected);
  }
  // Short writes must obey their actual return count even when the stream
  // reports enough capacity for the entire entry.
  for (size_t limit : {1U, 2U, 3U, 5U, 8U, 31U}) {
    Fixture f;
    f.stream.capacity = 65536;
    f.stream.max_write = limit;
    f.enqueue(log);
    f.enqueue(reply);
    unsigned attempts = 0;
    while (f.usb.hasPendingIO()) {
      f.manager.loop();
      REQUIRE(++attempts <= expected.size());
    }
    REQUIRE(f.stream.output == expected);
  }
}

static void radio2QueuePressure() {
  const Bytes log = radio2Log(), required{1, 0x70};
  // A rejected group emits neither half, retaining the reserved reply slot.
  {
    Fixture f;
    for (unsigned i = 0; i < 3; ++i) f.enqueue(Bytes{0x88, uint8_t(i)});
    REQUIRE(f.usb.writeFrame(log.data(), log.size()) == 0);
    f.enqueue(required);
    f.flush();
    REQUIRE(f.stream.output == framed({Bytes{0x88, 0}, Bytes{0x88, 1}, Bytes{0x88, 2}, required}));
  }
  // Before the marker starts, eviction removes the entire group atomically.
  {
    Fixture f;
    f.enqueue(log);
    for (unsigned i = 0; i < 4; ++i) f.enqueue(required);
    f.flush();
    REQUIRE(f.stream.output == framed({required, required, required, required}));
  }
  const Bytes group = framed({Bytes{0x92, 0xA1}, legacyLog(log)});
  for (size_t split = 1; split < group.size(); ++split) {
    Fixture f;
    f.stream.capacity = int(split);
    f.enqueue(log);
    REQUIRE(f.stream.output.size() == split);
    for (unsigned i = 0; i < 3; ++i) f.enqueue(required);
    // Once any marker byte escaped, even a command reply cannot evict its
    // raw packet. Keep that command unread until the group has drained.
    REQUIRE(f.usb.writeFrame(required.data(), required.size()) == 0);
    f.issue(0x42);
    REQUIRE(f.tick() == 0 && f.stream.reads == 0);
    f.flush();
    REQUIRE(f.stream.output == framed({Bytes{0x92, 0xA1}, legacyLog(log), required, required, required}));
    REQUIRE(f.tick() == 1 && f.command[0] == 0x42);
    f.reply();
    f.flush();
    REQUIRE(f.stream.output == framed({Bytes{0x92, 0xA1}, legacyLog(log), required, required, required, Bytes{1, 0x42}}));
  }
  // Back-to-back primary/secondary/primary receptions retain the legacy wire
  // format and never attach the radio2 marker to a primary packet.
  {
    Fixture f;
    f.enqueue(Bytes{0x88, 1, 2, 3});
    f.enqueue(log);
    f.enqueue(Bytes{0x88, 4, 5, 6});
    f.flush();
    REQUIRE(f.stream.output == framed({Bytes{0x88, 1, 2, 3}, Bytes{0x92, 0xA1}, legacyLog(log), Bytes{0x88, 4, 5, 6}}));
  }
}

static bool radio2Connected = true;
static bool checkRadio2Connection() { return radio2Connected; }

static void radio2Reconnect() {
  const Bytes log = radio2Log();
  const Bytes group = framed({Bytes{0x92, 0xA1}, legacyLog(log)});
  for (size_t split : {1U, 4U, 5U, 6U, 8U, 9U, 31U}) {
    Fixture f;
    radio2Connected = true;
    f.usb.setConnectedCheck(checkRadio2Connection);
    f.stream.capacity = int(split);
    f.enqueue(log);
    REQUIRE(f.stream.output == Bytes(group.begin(), group.begin() + split));
    radio2Connected = false;
    f.manager.loop();
    REQUIRE(!f.usb.hasPendingIO());
    // The old host session's partial data is discarded by USB. A new session
    // receives neither an old suffix nor a raw packet without its marker.
    f.stream.output.clear();
    radio2Connected = true;
    f.enqueue(Bytes{0x88, 0, 0, 7});
    f.enqueue(log);
    f.flush();
    REQUIRE(f.stream.output == framed({Bytes{0x88, 0, 0, 7}, Bytes{0x92, 0xA1}, legacyLog(log)}));
  }
}

static void radio2UncontrolledStreams() {
  const Bytes log = radio2Log(), reply{0};
  const Bytes expected = framed({Bytes{0x92, 0xA1}, legacyLog(log), reply});
  for (size_t split : {0U, 1U, 4U, 5U, 6U, 8U, 9U, 31U}) {
    Fixture f;
    f.usb.enableFlowControl(false);
    f.stream.reports_capacity = false; // Print's default API returns zero.
    f.stream.capacity = int(split);
    f.enqueue(log);
    REQUIRE(f.stream.output == Bytes(expected.begin(), expected.begin() + split));
    f.enqueue(reply);
    REQUIRE(f.usb.hasPendingIO() && f.usb.isWriteBusy());
    f.flush();
    REQUIRE(f.stream.output == expected);
  }
}

int main(int argc, char** argv) {
  REQUIRE(argc == 2);
  const int scenario = std::atoi(argv[1]);
  switch (scenario) {
    case 0: burst(); break;
    case 1: partialRxBudget(); break;
    case 2: partialRxResume(); break;
    case 3: evictableCapacity(); break;
    case 4: controlsAndPassthrough(); break;
    case 5: resetsAndUncontrolledStreams(); break;
    case 6: expiredBeforeBackpressure(); break;
    case 7: independentRoute(); break;
    case 8: radio2WireSplits(); break;
    case 9: radio2QueuePressure(); break;
    case 10: radio2Reconnect(); break;
    case 11: radio2UncontrolledStreams(); break;
    default: REQUIRE(false);
  }
  std::printf("PASS: Arduino serial backpressure scenario %d\n", scenario);
}
