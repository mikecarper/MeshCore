#include <helpers/esp32/SerialWifiInterface.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <initializer_list>
#include <limits>
#include <vector>

uint32_t mock_millis = 0;
std::deque<WiFiClient> WiFiServer::incoming;

using Bytes = std::vector<uint8_t>;
using Socket = std::shared_ptr<MockSocket>;

static const IPAddress A(192, 168, 1, 20);
static const IPAddress B(192, 168, 1, 30);
static const IPAddress C(192, 168, 1, 40);

static Bytes framed(std::initializer_list<Bytes> frames) {
  Bytes result;
  for (const auto& frame : frames) {
    result.push_back('>');
    result.push_back(uint8_t(frame.size()));
    result.push_back(uint8_t(frame.size() >> 8));
    result.insert(result.end(), frame.begin(), frame.end());
  }
  return result;
}

struct Fixture {
  SerialWifiInterface transport;
  uint8_t input[MAX_FRAME_SIZE] = {};
  int callbacks = 0;
  bool pending_operation = false;
  Socket next_client;

  Fixture() {
    mock_millis = 100;
    WiFiServer::incoming.clear();
    transport.begin(5000);
    transport.enable();
    transport.setSessionChangedCallback([](void* context) {
      Fixture& fixture = *static_cast<Fixture*>(context);
      ++fixture.callbacks;
      assert(!fixture.transport.isConnected());
      fixture.pending_operation = false;
      // Cancellation must happen before a new socket sees an old response or
      // has any of its own command bytes consumed.
      if (fixture.next_client) {
        assert(fixture.next_client->sent.empty());
        assert(fixture.next_client->read_count == 0);
      }
    }, this);
  }

  ~Fixture() { transport.end(); }

  size_t tick() { return transport.checkRecvFrame(input); }

  Socket connect(IPAddress address, const Bytes& command = {}, bool valid = true) {
    Socket socket = std::make_shared<MockSocket>(address);
    socket->received.insert(socket->received.end(), command.begin(), command.end());
    next_client = socket;
    WiFiServer::incoming.push_back(WiFiClient(socket));
    tick();
    next_client.reset();
    assert(transport.isConnected() == valid);
    return socket;
  }

  void enqueue(const Bytes& frame) {
    assert(transport.writeFrame(frame.data(), frame.size()) == frame.size());
  }

  void drain(const Socket& socket) {
    socket->write_limit = std::numeric_limits<size_t>::max();
    for (int i = 0; i < 12; ++i) tick();
  }
};

int main(int argc, char** argv) {
  assert(argc == 2);
  const int scenario = std::atoi(argv[1]);
  Fixture f;
  const Bytes reply_a{0x01, 0x21, 0x22, 0x23};
  const Bytes reply_b{0x02, 0x31, 0x32};
  const Bytes reply_c{0x03, 0x41};

  if (scenario == 0) {
    auto old = f.connect(A);
    f.enqueue(reply_a);
    auto replacement = f.connect(A);
    assert(!old->connected && f.callbacks == 0);
    f.drain(replacement);
    assert(replacement->sent == framed({reply_a}));
  } else if (scenario == 1) {
    auto old = f.connect(A);
    f.enqueue(reply_a);
    f.pending_operation = true;
    old->connected = false;
    f.tick();
    assert(!f.transport.isConnected());
    assert(f.callbacks == 1 && !f.pending_operation);
    mock_millis += 60000; // Same-IP retention is not the different-IP grace timer.
    auto replacement = f.connect(A);
    f.drain(replacement);
    assert(replacement->sent == framed({reply_a}) && f.callbacks == 1);
  } else if (scenario == 2) {
    f.connect(A);
    f.enqueue(reply_a);
    auto other = f.connect(B);
    f.enqueue(reply_b);
    f.drain(other);
    assert(other->sent == framed({reply_b}) && f.callbacks == 1);
  } else if (scenario == 3) {
    f.connect(A);
    f.enqueue(reply_a);
    auto other = f.connect(B);
    f.enqueue(reply_b);
    mock_millis += 29999;
    auto returned = f.connect(A);
    f.drain(returned);
    assert(returned->sent == framed({reply_a}));
    assert(other->sent.empty());
    auto other_returned = f.connect(B);
    f.drain(other_returned);
    assert(other_returned->sent == framed({reply_b}));
    assert(f.callbacks == 3);
  } else if (scenario == 4) {
    f.connect(A);
    f.enqueue(reply_a);
    f.connect(B);
    mock_millis += 30000;
    auto expired = f.connect(A);
    f.drain(expired);
    assert(expired->sent.empty());
  } else if (scenario == 5) {
    f.connect(A);
    f.enqueue(reply_a);
    f.connect(B);
    mock_millis += 20000;
    f.connect(B); // This must not extend A's held deadline.
    assert(f.callbacks == 1);
    mock_millis += 10000;
    auto expired = f.connect(A);
    f.drain(expired);
    assert(expired->sent.empty());
  } else if (scenario == 6 || scenario == 7) {
    mock_millis = UINT32_MAX - 10000;
    f.connect(A);
    f.enqueue(reply_a);
    f.connect(B);
    mock_millis += scenario == 6 ? 29999 : 30000;
    auto returned = f.connect(A);
    f.drain(returned);
    assert(returned->sent == (scenario == 6 ? framed({reply_a}) : Bytes{}));
  } else if (scenario == 8) {
    f.connect(A);
    f.enqueue(reply_a);
    f.connect(B);
    f.enqueue(reply_b);
    f.connect(C); // Park B and evict A: only one spare fixed-size queue exists.
    f.enqueue(reply_c);
    auto evicted = f.connect(A);
    f.drain(evicted);
    assert(evicted->sent.empty());
    auto newest = f.connect(C);
    f.drain(newest);
    assert(newest->sent == framed({reply_c}));
  } else if (scenario == 9) {
    auto socket = f.connect(A);
    f.enqueue(reply_a);
    socket->write_limit = 2;
    for (int i = 0; i < 8; ++i) f.tick();
    assert(socket->sent == framed({reply_a}));
    assert(!f.transport.hasPendingIO());
  } else if (scenario == 10) {
    auto socket = f.connect(A);
    const Bytes push{0x80, 0xA1, 0xA2, 0xA3};
    f.enqueue(push);
    socket->write_limit = 2;
    f.tick();
    assert(socket->sent.size() == 2);
    f.enqueue(reply_a); // Responses have higher queue priority than this push.
    f.drain(socket);
    assert(socket->sent == framed({push, reply_a}));
  } else if (scenario == 11) {
    auto old = f.connect(A);
    f.enqueue(reply_a);
    old->write_limit = 5; // Header and part of payload already sent.
    f.tick();
    assert(old->sent.size() == 5);
    auto replacement = f.connect(A);
    f.drain(replacement);
    assert(replacement->sent == framed({reply_a}));
  } else if (scenario == 12) {
    auto old = f.connect(A);
    f.enqueue(reply_a);
    old->write_limit = 2; // Partial header must not be a suffix on reconnection.
    f.tick();
    auto other = f.connect(B);
    f.drain(other);
    assert(other->sent.empty());
    auto returned = f.connect(A);
    f.drain(returned);
    assert(returned->sent == framed({reply_a}));
  } else if (scenario == 13 || scenario == 14) {
    f.connect(A);
    f.enqueue(reply_a);
    f.connect(B);
    f.enqueue(reply_b);
    if (scenario == 13) f.transport.disable();
    else f.transport.end();
    assert(!f.transport.isConnected() && !f.transport.hasPendingIO());
    f.transport.begin(5000);
    f.transport.enable();
    auto previous = f.connect(A);
    f.drain(previous);
    assert(previous->sent.empty());
    auto other = f.connect(B);
    f.drain(other);
    assert(other->sent.empty());
  } else if (scenario == 15) {
    auto old = f.connect(A, Bytes{'<', 4, 0, 0x11}); // Incomplete old command.
    assert(old->read_count == 3);
    f.pending_operation = true;
    f.enqueue(reply_a);
    auto other = f.connect(B, Bytes{'<', 2, 0, 0x71, 0x72});
    assert(f.callbacks == 1 && !f.pending_operation);
    // The new header is parsed from its start, not attached to the old header.
    if (other->read_count == 0) assert(f.tick() == 2);
    assert(other->read_count == 5);
    assert(f.input[0] == 0x71 && f.input[1] == 0x72);
    assert(other->sent.empty());
  } else if (scenario == 16) {
    auto old = f.connect(A, Bytes{'<', 4, 0, 0x11});
    assert(old->read_count == 3);
    f.pending_operation = true;
    auto replacement = f.connect(A, Bytes{'<', 2, 0, 0x61, 0x62});
    assert(f.callbacks == 0 && f.pending_operation);
    assert(replacement->read_count == 5);
    assert(f.input[0] == 0x61 && f.input[1] == 0x62);
  } else if (scenario == 17) {
    auto socket = f.connect(A);
    f.enqueue(reply_a);
    f.tick(); // A zero-byte socket write is backpressure, not frame completion.
    assert(socket->sent.empty() && f.transport.hasPendingIO());
    f.drain(socket);
    assert(socket->sent == framed({reply_a}));
  } else if (scenario == 18) {
    f.connect(A);
    for (int i = 0; i < 4; ++i) f.enqueue(Bytes{0x01, uint8_t(i)});
    assert(f.transport.writeFrame(reply_a.data(), reply_a.size()) == 0);
    f.connect(B);
    for (int i = 0; i < 4; ++i) f.enqueue(Bytes{0x02, uint8_t(i)});
    auto returned = f.connect(A);
    f.drain(returned);
    assert(returned->sent == framed({{0x01, 0}, {0x01, 1}, {0x01, 2}, {0x01, 3}}));
    auto other = f.connect(B);
    f.drain(other);
    assert(other->sent == framed({{0x02, 0}, {0x02, 1}, {0x02, 2}, {0x02, 3}}));
  } else if (scenario == 19) {
    f.connect(A);
    f.enqueue(reply_a);
    f.connect(B);
    mock_millis += 20000;
    f.connect(C); // B has no backlog: preserve A without extending its deadline.
    mock_millis += 9999;
    auto returned = f.connect(A);
    f.drain(returned);
    assert(returned->sent == framed({reply_a}));
  } else if (scenario == 20) {
    f.connect(A);
    f.enqueue(reply_a);
    f.connect(B);
    mock_millis += 20000;
    f.connect(C);
    assert(!f.transport.hasPendingIO()); // Parked-only data must not busy-loop.
    mock_millis += 10000;
    f.transport.loop();
    auto expired = f.connect(A);
    f.drain(expired);
    assert(expired->sent.empty());
  } else if (scenario == 21) {
    auto socket = f.connect(A);
    const Bytes push{0x80, 0xA1, 0xA2, 0xA3};
    const Bytes keep{0x81, 0xB1};
    const Bytes evict{0x88, 0xC1};
    f.enqueue(push);
    socket->write_limit = 2;
    f.tick();
    f.enqueue(keep);
    f.enqueue(evict);
    f.enqueue(reply_a);
    f.enqueue(reply_b); // Full queue can evict a waiting push, never its sent head.
    f.drain(socket);
    assert(socket->sent == framed({push, reply_a, reply_b, keep}));
  } else if (scenario == 22) {
    f.connect(A, Bytes{'<', 4, 0, 0x11});
    f.pending_operation = true;
    f.transport.disable();
    assert(f.callbacks == 1 && !f.pending_operation);
    f.transport.end(); // Repeated teardown must not cancel the same owner twice.
    assert(f.callbacks == 1);
    f.transport.begin(5000);
    f.transport.enable();
    auto replacement = f.connect(A, Bytes{'<', 2, 0, 0x61, 0x62});
    assert(replacement->read_count == 5);
    assert(f.input[0] == 0x61 && f.input[1] == 0x62);
  } else if (scenario == 23) {
    auto socket = f.connect(A);
    Bytes maximum(MAX_FRAME_SIZE);
    for (size_t i = 0; i < maximum.size(); ++i) maximum[i] = uint8_t(i);
    maximum[0] = 0x80; // Low-priority push, pinned once any bytes are sent.
    f.enqueue(maximum);
    socket->write_limit = MAX_FRAME_SIZE + 2; // All but the final payload byte.
    f.tick();
    assert(socket->sent.size() == MAX_FRAME_SIZE + 2);
    assert(f.transport.hasPendingIO());
    f.enqueue(reply_a);
    f.drain(socket);
    assert(socket->sent == framed({maximum, reply_a}));
    assert(!f.transport.hasPendingIO());
  } else if (scenario == 24) {
    auto old = f.connect(A);
    Bytes maximum(MAX_FRAME_SIZE);
    for (size_t i = 0; i < maximum.size(); ++i) maximum[i] = uint8_t(i ^ 0x55);
    maximum[0] = 0x01;
    f.enqueue(maximum);
    old->write_limit = MAX_FRAME_SIZE + 2;
    f.tick();
    assert(old->sent.size() == MAX_FRAME_SIZE + 2);
    f.connect(B);
    Bytes other_maximum(MAX_FRAME_SIZE, 0xFE);
    other_maximum[0] = 0x02;
    f.enqueue(other_maximum);
    auto returned = f.connect(A);
    f.drain(returned);
    assert(returned->sent == framed({maximum}));
    auto other_returned = f.connect(B);
    f.drain(other_returned);
    assert(other_returned->sent == framed({other_maximum}));
  } else if (scenario >= 25 && scenario <= 27) {
    auto socket = f.connect(A);
    f.pending_operation = true;
    const Bytes invalid = scenario == 25 ? Bytes{'<', 0xff, 0xff}
        : scenario == 26 ? Bytes{'?', 1, 0} : Bytes{'<', 0, 0};
    socket->received.insert(socket->received.end(), invalid.begin(), invalid.end());
    socket->received.insert(socket->received.end(), {'<', 1, 0, 0x71});
    assert(f.tick() == 0);
    assert(!socket->connected && !f.transport.hasReceivedFrameHeader());
    assert(f.callbacks == 1 && !f.pending_operation);
    assert(socket->read_count == 3); // Do not wait for or scan the illegal body.
    for (int i = 0; i < 10; ++i) assert(f.tick() == 0);
    f.connect(A, Bytes{'<', 1, 0, 0x72});
    assert(f.input[0] == 0x72);
  } else if (scenario == 28 || scenario == 29 || scenario == 34) {
    if (scenario == 34) mock_millis = UINT32_MAX - 2000;
    auto socket = f.connect(A, scenario == 29 ? Bytes{'<', 2} : Bytes{'<', 2, 0, 0x71});
    f.pending_operation = true;
    mock_millis += 4999;
    assert(f.tick() == 0 && f.transport.isConnected());
    mock_millis += 1;
    f.transport.loop();
    assert(!socket->connected && !f.transport.hasReceivedFrameHeader());
    assert(f.callbacks == 1 && !f.pending_operation);
    assert(f.tick() == 0);
    f.connect(A, Bytes{'<', 1, 0, 0x72});
    assert(f.input[0] == 0x72);
  } else if (scenario == 30 || scenario == 31) {
    auto socket = f.connect(A, scenario == 30 ? Bytes{'<', 2, 0} : Bytes{});
    if (scenario == 30) socket->received = {0x71, 0x72};
    else socket->received = {'<', 2, 0, 0x71, 0x72};
    socket->read_limit = 1;
    assert(f.tick() == 0);
    assert(!socket->connected && !f.transport.hasReceivedFrameHeader());
    assert(f.callbacks == 1);
    f.connect(A, Bytes{'<', 1, 0, 0x73});
    assert(f.input[0] == 0x73);
  } else if (scenario == 32) {
    auto socket = f.connect(A, Bytes{'<'});
    socket->received.push_back(2);
    assert(f.tick() == 0);
    socket->received.insert(socket->received.end(), {0, 0x71});
    assert(f.tick() == 0);
    mock_millis += 4999;
    socket->received.push_back(0x72);
    assert(f.tick() == 2);
    assert(f.input[0] == 0x71 && f.input[1] == 0x72);
    assert(f.callbacks == 0 && socket->connected);
  } else if (scenario == 33) {
    auto socket = f.connect(A);
    socket->received = {'<', MAX_FRAME_SIZE, 0};
    for (int i = 0; i < MAX_FRAME_SIZE; ++i) socket->received.push_back(uint8_t(i));
    socket->received.insert(socket->received.end(), {'<', 1, 0, 0x72});
    assert(f.tick() == MAX_FRAME_SIZE);
    for (int i = 0; i < MAX_FRAME_SIZE; ++i) assert(f.input[i] == uint8_t(i));
    assert(f.tick() == 1 && f.input[0] == 0x72);
    assert(f.callbacks == 0 && socket->connected);
  } else if (scenario == 35) {
    auto socket = f.connect(A, Bytes{'<', 2, 0, 0x71});
    f.pending_operation = true;
    f.enqueue(reply_a);
    socket->connected = false;
    f.transport.loop();
    assert(f.callbacks == 1 && !f.pending_operation);
    f.transport.disable();
    assert(f.callbacks == 1); // No second cancellation for an ended session.
  } else {
    assert(false && "unknown scenario");
  }
  std::printf("PASS: Wi-Fi session scenario %d\n", scenario);
}
