#include <helpers/ethernet/SerialEthernetInterface.h>
#include <helpers/MultiSerialInterface.h>
#include <WiFi.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>

uint32_t mock_millis = 100;
using Bytes = std::vector<uint8_t>;
using Socket = std::shared_ptr<MockSocket>;
static constexpr uint32_t A = 0xc0a80114;
static constexpr uint32_t B = 0xc0a8011e;

static Bytes framed(std::initializer_list<Bytes> frames) {
  Bytes result;
  for (const auto& frame : frames) {
#if !ETHERNET_RAW_LINE
    result.insert(result.end(), {'>', uint8_t(frame.size()), uint8_t(frame.size() >> 8)});
#endif
    result.insert(result.end(), frame.begin(), frame.end());
#if ETHERNET_RAW_LINE
    result.insert(result.end(), {'\r', '\n'});
#endif
  }
  return result;
}

static Bytes command(const Bytes& payload) {
#if ETHERNET_RAW_LINE
  Bytes data = payload;
  data.push_back('\n');
#else
  Bytes data{'<', uint8_t(payload.size()), uint8_t(payload.size() >> 8)};
  data.insert(data.end(), payload.begin(), payload.end());
#endif
  return data;
}

struct Transport : SerialEthernetInterface {
  bool connected = false;
  bool io_unavailable = false;
  bool pause_after_write = false;
  bool pause_after_read = false;
  bool pause_after_available = false;
  bool replenish_read = false;
  size_t read_guard = 0;
  Bytes append_after_read;
  int available_calls = 0;
  int write_calls = 0;
  int disconnect_calls = 0;
  Socket client;
  Socket next_client;
  bool isIOUnavailable() const override { return io_unavailable; }
  void loop() override { serviceIOAvailability(); }
  bool isConnected() const override { return isEnabled() && connected; }
  void disconnectClient() override {
    ++disconnect_calls;
    connected = false;
    if (client) client->connected = false;
  }
  int available() override {
    assert(!io_unavailable);
    ++available_calls;
    if (pause_after_available) {
      io_unavailable = true;
      pause_after_available = false;
    }
    return client ? int(client->received.size()) : 0;
  }
  int read() override {
    assert(!io_unavailable);
    if (!client || client->received.empty()) return -1;
    assert((read_guard == 0 || client->read_count < read_guard)
        && "Ethernet RX invocation exceeded bounded read guard");
    const int value = client->received.front();
    client->received.pop_front();
    ++client->read_count;
    if (replenish_read) client->received.push_back(uint8_t(value));
    if (!append_after_read.empty()) {
      client->received.insert(client->received.end(), append_after_read.begin(), append_after_read.end());
      append_after_read.clear();
    }
    if (pause_after_read) {
      io_unavailable = true;
      pause_after_read = false;
    }
    return value;
  }
  size_t write(const uint8_t* src, size_t size) override {
    assert(!io_unavailable);
    ++write_calls;
    if (!isConnected() || !client) return 0;
    size = std::min(size, client->write_limit);
    client->sent.insert(client->sent.end(), src, src + size);
    if (pause_after_write) {
      io_unavailable = true;
      pause_after_write = false;
    }
    return size;
  }
  Socket connect(uint32_t ip, const Bytes& input = {}) {
    next_client = std::make_shared<MockSocket>(IPAddress(ip));
    next_client->received.insert(next_client->received.end(), input.begin(), input.end());
    disconnectClient();
    onClientConnected(ip); // Same ordering as both hardware driver loops.
    client = next_client;
    next_client.reset();
    connected = true;
    return client;
  }
};

struct Fixture {
  Transport transport;
  MultiSerialInterface routes;
  uint8_t input[MAX_FRAME_SIZE] = {};
  int callbacks = 0;
  bool pending_operation = false;
  Fixture() {
    assert(routes.addInterface(InterfaceType::Ethernet, &transport));
    routes.enable();
    transport.setSessionChangedCallback([](void* context) {
      auto& f = *static_cast<Fixture*>(context);
      assert(!f.transport.isConnected());
      if (f.transport.next_client) {
        assert(f.transport.next_client->sent.empty());
        assert(f.transport.next_client->read_count == 0);
      }
      ++f.callbacks;
      f.pending_operation = false;
      f.routes.forgetReplyRouteForDisconnected(&f.transport);
    }, this);
  }
  size_t tick() { return routes.checkRecvFrame(input); }
  void enqueue(const Bytes& data) {
    assert(transport.writeFrame(data.data(), data.size()) == data.size());
  }
  void drain(const Socket& socket) {
    socket->write_limit = std::numeric_limits<size_t>::max();
    for (int i = 0; i < 20; ++i) tick();
  }
};

int main(int argc, char** argv) {
  assert(argc == 2);
  const int scenario = std::atoi(argv[1]);
  Fixture f;
  const Bytes reply{0x01, 0x21, 0x22, 0x23};
  const Bytes other{0x02, 0x31};
  const Bytes push{0x80, 0x41, 0x42};
  auto socket = f.transport.connect(A);
  if (scenario <= 2) {
    f.enqueue(reply);
    socket->write_limit = scenario == 0 ? 2 : scenario == 1 ? 4 : 0;
    f.tick();
    assert(socket->sent.size() == socket->write_limit);
    f.drain(socket);
    assert(socket->sent == framed({reply}));
  } else if (scenario == 3 || scenario == 4) {
    f.enqueue(push);
    socket->write_limit = 2;
    f.tick();
    if (scenario == 4) {
      f.enqueue(Bytes{0x81, 0x51});
      f.enqueue(Bytes{0x88, 0x61});
    }
    f.enqueue(reply);
    if (scenario == 4) f.enqueue(other);
    f.drain(socket);
    assert(socket->sent == (scenario == 4 ? framed({push, reply, other, {0x81, 0x51}})
                                         : framed({push, reply})));
  } else if (scenario == 5) {
    Bytes maximum(MAX_FRAME_SIZE, 0x45);
    maximum[0] = 0x80;
    f.enqueue(maximum);
    socket->write_limit = framed({maximum}).size() - 1;
    f.tick();
    assert(socket->sent.size() == socket->write_limit);
    f.enqueue(reply);
    f.drain(socket);
    assert(socket->sent == framed({maximum, reply}));
  } else if (scenario == 6 || scenario == 7) {
    f.pending_operation = true;
    f.enqueue(reply);
    socket->write_limit = 2;
    f.tick();
    auto replacement = f.transport.connect(scenario == 6 ? A : B);
    assert(!socket->connected);
    assert(f.callbacks == (scenario == 6 ? 0 : 1));
    assert(f.pending_operation == (scenario == 6));
    f.drain(replacement);
    assert(replacement->sent == (scenario == 6 ? framed({reply}) : Bytes{}));
    if (scenario == 7) {
      auto returned = f.transport.connect(A);
      f.drain(returned);
      assert(returned->sent.empty()); // No extra held queue on Ethernet.
    }
  } else if (scenario == 8) {
    f.pending_operation = true;
    f.enqueue(reply);
    f.transport.disconnectClient();
    f.transport.onClientDisconnected();
    assert(f.callbacks == 1 && !f.pending_operation);
    assert(!f.transport.hasPendingIO());
    mock_millis += 60000;
    auto returned = f.transport.connect(A);
    f.drain(returned);
    assert(returned->sent == framed({reply}) && f.callbacks == 1);
  } else if (scenario == 9) {
    const Bytes request = command(Bytes{0x04});
    socket->received.insert(socket->received.end(), request.begin(), request.end());
    assert(f.tick() == 1);
    assert(f.routes.captureReplyRoute() == &f.transport);
    f.routes.lockReplyRoute();
    f.pending_operation = true;
    auto replacement = f.transport.connect(B, command(Bytes{0x05}));
    assert(!f.pending_operation && f.callbacks == 1);
    assert(f.routes.captureReplyRoute() == nullptr);
    assert(replacement->sent.empty() && replacement->read_count == 0);
    assert(f.tick() == 1 && f.input[0] == 0x05);
    assert(f.routes.captureReplyRoute() == &f.transport);
  } else if (scenario == 10) {
    f.enqueue(reply);
    f.pending_operation = true;
    f.transport.disable();
    f.transport.disable();
    assert(!socket->connected && !f.pending_operation && f.callbacks == 1);
    assert(!f.transport.hasPendingIO());
    f.transport.enable();
    auto replacement = f.transport.connect(A);
    f.drain(replacement);
    assert(replacement->sent.empty());
  } else if (scenario == 11) {
#if ETHERNET_RAW_LINE
    socket->received = std::deque<uint8_t>(MAX_FRAME_SIZE + 1, 'a');
    socket->received.push_back('\n');
#else
    socket->received = {'<', 0xff, 0xff};
#endif
    const Bytes tail = command(Bytes{0x71});
    socket->received.insert(socket->received.end(), tail.begin(), tail.end());
    assert(f.tick() == 0);
    assert(!socket->connected && f.callbacks == 1);
    assert(!f.transport.isReadBusy());
    for (int i = 0; i < 5; ++i) assert(f.tick() == 0);
    f.transport.connect(A, command(Bytes{0x72}));
    assert(f.tick() == 1 && f.input[0] == 0x72);
  } else if (scenario == 12 || scenario == 14) {
    if (scenario == 14) mock_millis = UINT32_MAX - 2000;
    Bytes partial = command(Bytes{'a', 'b'});
    partial.pop_back();
    socket->received.insert(socket->received.end(), partial.begin(), partial.end());
    assert(f.tick() == 0);
    assert(f.transport.isReadBusy());
    mock_millis += 4999;
    assert(f.tick() == 0 && socket->connected);
    mock_millis += 1;
#if ETHERNET_RAW_LINE
    // Human terminal input is not subject to the binary frame deadline.
    assert(f.tick() == 0 && socket->connected);
    mock_millis += 60000;
    socket->received.push_back('\n');
    assert(f.tick() == 2 && f.input[0] == 'a' && f.input[1] == 'b');
    assert(f.callbacks == 0 && !f.transport.isReadBusy());
#else
    assert(f.tick() == 0 && !socket->connected);
    assert(f.callbacks == 1 && !f.transport.isReadBusy());
#endif
  } else if (scenario == 13) {
    Bytes maximum(MAX_FRAME_SIZE, 'a');
    const Bytes bytes = command(maximum);
    for (size_t i = 0; i < bytes.size(); ++i) {
      socket->received.push_back(bytes[i]);
      assert(f.tick() == (i + 1 == bytes.size() ? MAX_FRAME_SIZE : 0));
    }
    for (int i = 0; i < MAX_FRAME_SIZE; ++i) assert(f.input[i] == 'a');
    assert(!f.transport.isReadBusy() && socket->connected);
    const Bytes next = command(Bytes{'b'});
    socket->received.insert(socket->received.end(), next.begin(), next.end());
    assert(f.tick() == 1 && f.input[0] == 'b');
  } else if (scenario == 15) {
    const Bytes data = command(reply);
    socket->received.insert(socket->received.end(), data.begin(), data.end());
    assert(f.transport.checkRecvFrame(nullptr) == 0);
    assert(!socket->connected && f.callbacks == 1);
  } else if (scenario == 16) {
    f.enqueue(reply);
    socket->write_limit = 2;
    assert(f.tick() == 0);
    const Bytes prefix = socket->sent;
    assert(prefix.size() == 2);
    const Bytes request = command(other);
    socket->received.insert(socket->received.end(), request.begin(), request.end());
    const int available_calls = f.transport.available_calls;
    const int write_calls = f.transport.write_calls;
    const int disconnect_calls = f.transport.disconnect_calls;
    f.transport.io_unavailable = true;
    assert(f.transport.isConnected());
    assert(f.transport.hasPendingIO());
    assert(f.transport.isWriteBusy());
    for (int i = 0; i < 10; ++i) {
      mock_millis += 10000;
      assert(f.tick() == 0);
    }
    assert(f.transport.available_calls == available_calls);
    assert(f.transport.write_calls == write_calls);
    assert(f.transport.disconnect_calls == disconnect_calls);
    assert(socket->sent == prefix && socket->read_count == 0);
    assert(f.callbacks == 0 && f.transport.isConnected());
    f.transport.io_unavailable = false;
    assert(!f.transport.isWriteBusy());
    socket->write_limit = 100;
    assert(f.tick() == 0);
    assert(socket->sent == framed({reply}));
    assert(f.tick() == other.size());
    assert(f.input[0] == other[0] && f.callbacks == 0);
  } else if (scenario == 17 || scenario == 18 || scenario == 19 || scenario == 24) {
    if (scenario == 19) mock_millis = UINT32_MAX - 3000;
    if (scenario == 24) mock_millis = UINT32_MAX - 1999;
    Bytes partial = command(Bytes{'a', 'b'});
    partial.pop_back();
    socket->received.insert(socket->received.end(), partial.begin(), partial.end());
    assert(f.tick() == 0 && f.transport.isReadBusy());
    mock_millis += scenario == 18 ? 5000 : 2000;
    f.transport.io_unavailable = true;
    const int disconnect_calls = f.transport.disconnect_calls;
    assert(f.tick() == 0 && socket->connected);
    // Repeated polls must preserve the first pause boundary, including a
    // boundary at millis()==0 and a pause that crosses the 32-bit rollover.
    for (int i = 0; i < 3; ++i) {
      mock_millis += 4000;
      assert(f.tick() == 0 && socket->connected);
    }
    assert(f.transport.disconnect_calls == disconnect_calls);
    f.transport.io_unavailable = false;
#if !ETHERNET_RAW_LINE
    if (scenario == 18) {
      assert(f.tick() == 0 && !socket->connected);
      assert(f.callbacks == 1 && !f.transport.isReadBusy());
    } else {
      assert(f.tick() == 0 && socket->connected);
      mock_millis += 1499;
      assert(f.tick() == 0 && socket->connected);
      // A second long pause must preserve the 1501ms remaining, not grant a
      // new five-second budget on each DHCP/renewal worker operation.
      f.transport.io_unavailable = true;
      assert(f.tick() == 0 && socket->connected);
      mock_millis += 20000;
      assert(f.tick() == 0 && socket->connected);
      f.transport.io_unavailable = false;
      assert(f.tick() == 0 && socket->connected);
      mock_millis += 1500;
      assert(f.tick() == 0 && socket->connected);
      mock_millis += 1;
      assert(f.tick() == 0 && !socket->connected);
      assert(f.callbacks == 1 && !f.transport.isReadBusy());
    }
#else
    // Human terminal input has no binary framing deadline, even when already
    // older than five seconds at the start of worker ownership.
    mock_millis += 60000;
    socket->received.push_back('\n');
    assert(f.tick() == 2 && f.input[0] == 'a' && f.input[1] == 'b');
    assert(f.callbacks == 0 && !f.transport.isReadBusy());
#endif
  } else if (scenario >= 20 && scenario <= 23) {
    Bytes partial = command(Bytes{'a', 'b'});
    partial.pop_back();
    socket->received.insert(socket->received.end(), partial.begin(), partial.end());
    assert(f.tick() == 0 && f.transport.isReadBusy());
    f.enqueue(reply);
    socket->write_limit = 2;
    assert(f.tick() == 0 && socket->sent.size() == 2);
    mock_millis += 2000;
    f.transport.io_unavailable = true;
    assert(f.tick() == 0);
    mock_millis += 20000;
    Socket replacement;
    if (scenario == 20) {
      f.transport.disconnectClient();
      f.transport.onClientDisconnected();
      replacement = f.transport.connect(A);
      assert(f.callbacks == 1);
    } else if (scenario == 21) {
      f.transport.disable();
      f.transport.enable();
      replacement = f.transport.connect(A);
      assert(f.callbacks == 1);
    } else {
      replacement = f.transport.connect(scenario == 22 ? A : B);
      assert(f.callbacks == (scenario == 22 ? 0 : 1));
    }
    assert(!f.transport.isReadBusy());
    assert(f.tick() == 0 && replacement->connected);
    mock_millis += 20000;
    assert(f.tick() == 0 && replacement->connected);
    f.transport.io_unavailable = false;
    f.drain(replacement);
    assert(replacement->sent == ((scenario == 20 || scenario == 22)
                                 ? framed({reply}) : Bytes{}));
    // The replacement's frame must receive its own full timeout and must not
    // inherit any bytes or suspended time from the displaced stream.
    replacement->received.insert(replacement->received.end(), partial.begin(), partial.end());
    assert(f.tick() == 0 && f.transport.isReadBusy());
    mock_millis += 4999;
    assert(f.tick() == 0 && replacement->connected);
    replacement->received.push_back(
#if ETHERNET_RAW_LINE
        '\n'
#else
        'b'
#endif
    );
    assert(f.tick() == 2 && f.input[0] == 'a' && f.input[1] == 'b');
  } else if (scenario == 25) {
    f.transport.io_unavailable = true;
    assert(f.tick() == 0);
    mock_millis += 20000;
    assert(f.tick() == 0);
    f.transport.io_unavailable = false;
    Bytes partial = command(Bytes{'a', 'b'});
    partial.pop_back();
    socket->received.insert(socket->received.end(), partial.begin(), partial.end());
    assert(f.tick() == 0 && f.transport.isReadBusy());
    mock_millis += 4999;
    assert(f.tick() == 0 && socket->connected);
    mock_millis += 1;
#if ETHERNET_RAW_LINE
    socket->received.push_back('\n');
    assert(f.tick() == 2 && f.callbacks == 0);
#else
    assert(f.tick() == 0 && !socket->connected && f.callbacks == 1);
#endif
  } else if (scenario == 26) {
    Bytes partial = command(Bytes{'a', 'b'});
    partial.pop_back();
    socket->received.insert(socket->received.end(), partial.begin(), partial.end());
    assert(f.tick() == 0 && f.transport.isReadBusy());
    f.enqueue(reply);
    socket->write_limit = 2;
    mock_millis += 2000;
    f.transport.pause_after_write = true;
    assert(f.tick() == 0 && socket->sent.size() == 2);
    assert(f.transport.io_unavailable);
    mock_millis += 12000;
    f.transport.io_unavailable = false;
    socket->write_limit = 100;
    assert(f.tick() == 0 && socket->connected);
    assert(socket->sent == framed({reply}));
    mock_millis += 2999;
    socket->received.push_back(
#if ETHERNET_RAW_LINE
        '\n'
#else
        'b'
#endif
    );
    assert(f.tick() == 2 && f.input[0] == 'a' && f.input[1] == 'b');
    assert(f.callbacks == 0);
  } else if (scenario == 27 || scenario == 28) {
    const Bytes request = command(Bytes{'a', 'b'});
    socket->received.insert(socket->received.end(), request.begin(), request.end());
    if (scenario == 27) f.transport.pause_after_read = true;
    else f.transport.pause_after_available = true;
    assert(f.tick() == 0 && socket->connected);
    assert(f.transport.io_unavailable);
    assert(socket->read_count == (scenario == 27 ? 1U : 0U));
    assert(f.transport.isReadBusy() == (scenario == 27));
    mock_millis += 12000;
    f.transport.io_unavailable = false;
    assert(f.tick() == 2 && f.input[0] == 'a' && f.input[1] == 'b');
    assert(f.callbacks == 0 && !f.transport.isReadBusy());
  } else if (scenario == 29) {
    Bytes partial = command(Bytes{'a', 'b'});
    partial.pop_back();
    socket->received.insert(socket->received.end(), partial.begin(), partial.end());
    assert(f.tick() == 0 && f.transport.isReadBusy());
    // Exercise the actual MultiSerial routing path: another interface owns a
    // multi-frame response, so Ethernet receives loop() service but no frame
    // polls until that producer releases its route.
    Transport other_transport;
    assert(f.routes.addInterface(InterfaceType::USB, &other_transport));
    other_transport.enable();
    auto other_socket = other_transport.connect(B, command(Bytes{0x07}));
    assert(f.tick() == 1 && f.input[0] == 0x07);
    assert(f.routes.captureReplyRoute() == &other_transport);
    f.routes.lockReplyRoute();
    mock_millis += 2000;
    f.transport.io_unavailable = true;
    f.routes.loop(); // Main-side ownership handoff, not a frame poll.
    const int available_calls = f.transport.available_calls;
    for (int i = 0; i < 3; ++i) {
      mock_millis += 4000;
      f.routes.loop();
      assert(f.tick() == 0 && socket->connected);
    }
    assert(f.transport.available_calls == available_calls);
    assert(f.transport.isReadBusy() && f.callbacks == 0);
    f.transport.io_unavailable = false;
    f.routes.loop(); // Main-side completion releases the preserved budget.
    f.routes.unlockReplyRoute();
    mock_millis += 2999;
    assert(f.tick() == 0 && socket->connected);
    mock_millis += 1;
#if ETHERNET_RAW_LINE
    socket->received.push_back('\n');
    assert(f.tick() == 2 && f.input[0] == 'a' && f.input[1] == 'b');
    assert(f.callbacks == 0);
#else
    assert(f.tick() == 0 && !socket->connected && f.callbacks == 1);
#endif
    assert(other_socket->connected);
  } else if (scenario >= 30 && scenario <= 35) {
    const bool replenish = scenario <= 32;
    static constexpr size_t budgets[] = {1, 7, 257};
    const size_t budget = budgets[(scenario - 30) % 3];
    const Bytes request = command(Bytes{'a', 'b'});
#if ETHERNET_RAW_LINE
    const uint8_t junk = '\n';
#else
    const uint8_t junk = 'x';
#endif
    socket->received.assign(budget, junk);
    f.transport.read_guard = 4096; // The live-availability negative control must terminate.
    f.transport.replenish_read = replenish;
    if (!replenish) f.transport.append_after_read = request;
    assert(f.tick() == 0 && "new arrivals must wait for the next mesh loop");
    assert(socket->read_count == budget && f.transport.available_calls == 1);
    assert(socket->received.size() == (replenish ? budget : request.size()));
    assert(socket->connected && !f.transport.isReadBusy() && f.callbacks == 0);
    f.transport.replenish_read = false;
    if (replenish) socket->received.insert(socket->received.end(), request.begin(), request.end());
    // Nothing was dropped: existing idle bytes and a newly arrived complete
    // command remain in wire order and parse on the following mesh loop.
    assert(f.tick() == 2 && f.input[0] == 'a' && f.input[1] == 'b');
    assert(socket->received.empty() && f.transport.available_calls == 2);
  } else if (scenario >= 36 && scenario <= 39) {
    const bool zero_available = scenario >= 38;
    mock_millis = (scenario & 1) ? UINT32_MAX - 1000U : 0;
    const Bytes request = command(Bytes{'a', 'b'});
    socket->received.push_back(request[0]);
    if (!zero_available) f.transport.pause_after_read = true;
    assert(f.tick() == 0 && f.transport.isReadBusy());
    if (zero_available) {
      mock_millis += 2000;
      f.transport.pause_after_available = true;
      assert(f.tick() == 0);
    }
    assert(f.transport.io_unavailable && socket->read_count == 1);
    const int available_calls = f.transport.available_calls;
    mock_millis += 12000;
    assert(f.tick() == 0 && f.transport.available_calls == available_calls);
    f.transport.io_unavailable = false;
    f.transport.loop();
    mock_millis += zero_available ? 2999 : 4999;
    socket->received.insert(socket->received.end(), request.begin() + 1, request.end());
    assert((f.tick() == 2 && socket->connected && f.input[0] == 'a' && f.input[1] == 'b'
        && f.callbacks == 0) && "worker handoff must preserve remaining frame deadline");
  } else {
    assert(false && "unknown scenario");
  }
  std::printf("PASS: Ethernet session scenario %d\n", scenario);
}
