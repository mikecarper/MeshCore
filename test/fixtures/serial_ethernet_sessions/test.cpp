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
  Socket client;
  Socket next_client;
  bool isConnected() const override { return isEnabled() && connected; }
  void disconnectClient() override {
    connected = false;
    if (client) client->connected = false;
  }
  int available() override { return client ? int(client->received.size()) : 0; }
  int read() override {
    if (!client || client->received.empty()) return -1;
    const int value = client->received.front();
    client->received.pop_front();
    ++client->read_count;
    return value;
  }
  size_t write(const uint8_t* src, size_t size) override {
    if (!isConnected() || !client) return 0;
    size = std::min(size, client->write_limit);
    client->sent.insert(client->sent.end(), src, src + size);
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
  } else {
    assert(false && "unknown scenario");
  }
  std::printf("PASS: Ethernet session scenario %d\n", scenario);
}
