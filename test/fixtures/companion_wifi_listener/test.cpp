#include <helpers/wifi/SerialWifiInterface.h>
#include <helpers/MultiSerialInterface.h>
#include <lwip/sockets.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <limits>
#include <vector>

uint32_t mock_millis = 100;
std::deque<WiFiClient> WiFiServer::incoming;

using Socket = std::shared_ptr<MockSocket>;
using Bytes = std::vector<uint8_t>;
static const IPAddress A(192, 168, 1, 20);
static const IPAddress B(192, 168, 1, 30);

static Bytes framed(std::initializer_list<Bytes> frames) {
  Bytes result;
  for (const auto& frame : frames) {
    result.insert(result.end(), {'>', uint8_t(frame.size()), uint8_t(frame.size() >> 8)});
    result.insert(result.end(), frame.begin(), frame.end());
  }
  return result;
}

struct RoutingPeer : BaseSerialInterface {
  bool enabled = false, connected = true;
  unsigned polls = 0;
  std::deque<Bytes> input;
  std::vector<Bytes> output;
  void enable() override { enabled = true; }
  void disable() override { enabled = false; }
  bool isEnabled() const override { return enabled; }
  bool isConnected() const override { return connected; }
  bool isReadBusy() const override { return !input.empty(); }
  bool isWriteBusy() const override { return false; }
  size_t writeFrame(const uint8_t* src, size_t length) override {
    if (!enabled || !connected) return 0;
    output.emplace_back(src, src + length);
    return length;
  }
  size_t checkRecvFrame(uint8_t* dest) override {
    ++polls;
    if (!connected || input.empty()) return 0;
    const Bytes frame = input.front(); input.pop_front();
    memcpy(dest, frame.data(), frame.size());
    return frame.size();
  }
};

struct Fixture {
  SerialWifiInterface transport;
  uint8_t input[MAX_FRAME_SIZE] = {};
  int cancellations = 0;
  MultiSerialInterface* manager = nullptr;
  bool pending_operation = false;
  Fixture() {
    transport.setSessionChangedCallback([](void* context) {
      auto& f = *static_cast<Fixture*>(context);
      ++f.cancellations;
      f.pending_operation = false;
      if (f.manager) f.manager->forgetReplyRouteForDisconnected(&f.transport);
    }, this);
  }
  ~Fixture() { transport.end(); }
  bool start() { const bool result = transport.begin(5000); transport.enable(); return result; }
  size_t tick() { return transport.checkRecvFrame(input); }
  Socket connect(IPAddress ip, const Bytes& data = {}) {
    auto socket = std::make_shared<MockSocket>(ip);
    socket->received.insert(socket->received.end(), data.begin(), data.end());
    WiFiServer::incoming.push_back(WiFiClient(socket));
    tick();
    return socket;
  }
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
  WiFiServer::resetDiagnostics();
  mock_millis = 100;
  Fixture f;
  if (scenario == 0) {
    WiFi.link_status = WL_DISCONNECTED;
    WiFi.station_ip = IPAddress();
    assert(!f.start());
    for (int i = 0; i < 100; ++i) { mock_millis += 1000; f.tick(); }
    auto status = f.transport.getStatus();
    assert(status.enabled && status.requested && !status.addressReady);
    assert(!status.listening && status.beginAttempts == 0 && status.beginFailures == 0);
    assert(WiFiServer::begin_calls == 0);
    WiFi.link_status = WL_CONNECTED;
    WiFi.station_ip = IPAddress(192, 168, 1, 10);
    f.tick();
    assert(f.transport.getStatus().listening && WiFiServer::begin_calls == 1);
  } else if (scenario == 1) {
    assert(f.start());
    const int ends = WiFiServer::end_calls;
    for (int i = 0; i < 100; ++i) { mock_millis += 1000; f.tick(); }
    assert(f.transport.begin(5000));
    assert(WiFiServer::begin_calls == 1 && WiFiServer::end_calls == ends);
    assert(WiFiServer::open_sockets == 1);
  } else if (scenario >= 2 && scenario <= 4) {
    WiFiServer::begin_fault = scenario == 2 ? WiFiServer::BeginFault::Socket
        : scenario == 3 ? WiFiServer::BeginFault::Bind : WiFiServer::BeginFault::Listen;
    assert(!f.start());
    auto failed = f.transport.getStatus();
    assert(!failed.listening && failed.beginAttempts == 1 && failed.beginFailures == 1);
    assert(!f.transport.isEnabled());
    assert(failed.retryInMs == 1000 && static_cast<int>(failed.lastStartError) != 0);
    assert(failed.lastErrno == (scenario == 2 ? ENOMEM : scenario == 3 ? EADDRINUSE : EIO));
    assert(WiFiServer::open_sockets == 0);
    for (int i = 0; i < 50; ++i) f.tick();
    assert(WiFiServer::begin_calls == 1 && WiFiServer::open_sockets == 0);
    mock_millis += 999; f.tick();
    assert(WiFiServer::begin_calls == 1);
    WiFiServer::begin_fault = WiFiServer::BeginFault::None;
    ++mock_millis; f.tick();
    auto healed = f.transport.getStatus();
    assert(healed.listening && healed.beginAttempts == 2 && healed.beginFailures == 1);
    assert(healed.retryInMs == 0 && WiFiServer::open_sockets == 1);
    assert(healed.lastStartError == SerialWifiInterface::StartError::None && healed.lastErrno == 0);
    assert(f.transport.isEnabled());
  } else if (scenario == 5) {
    WiFiServer::begin_fault = WiFiServer::BeginFault::Listen;
    assert(!f.start());
    for (uint32_t delay : {1000U, 2000U, 4000U, 8000U, 16000U, 30000U, 30000U}) {
      auto before = f.transport.getStatus();
      assert(before.retryInMs == delay && WiFiServer::open_sockets == 0);
      mock_millis += delay - 1;
      for (int i = 0; i < 20; ++i) f.tick();
      assert(f.transport.getStatus().beginAttempts == before.beginAttempts);
      ++mock_millis; f.tick();
      assert(f.transport.getStatus().beginAttempts == before.beginAttempts + 1);
      assert(WiFiServer::open_sockets == 0);
    }
    WiFiServer::begin_fault = WiFiServer::BeginFault::None;
    mock_millis += 30000; f.tick();
    assert(f.transport.getStatus().listening);
  } else if (scenario == 6) {
    mock_millis = UINT32_MAX - 500;
    WiFiServer::begin_fault = WiFiServer::BeginFault::Bind;
    assert(!f.start());
    mock_millis += 999; f.tick();
    assert(WiFiServer::begin_calls == 1 && f.transport.getStatus().retryInMs == 1);
    WiFiServer::begin_fault = WiFiServer::BeginFault::None;
    ++mock_millis; f.tick();
    assert(f.transport.getStatus().listening && WiFiServer::begin_calls == 2);
  } else if (scenario == 7 || scenario == 8) {
    WiFiServer::begin_fault = WiFiServer::BeginFault::Bind;
    assert(!f.start());
    if (scenario == 7) f.transport.disable();
    else f.transport.end(); // The main loop uses end() for network suspension.
    assert(!f.transport.getStatus().requested && !f.transport.getStatus().listening);
    WiFiServer::begin_fault = WiFiServer::BeginFault::None;
    mock_millis += 30000;
    f.transport.enable();
    for (int i = 0; i < 20; ++i) f.tick();
    assert(WiFiServer::begin_calls == 1 && WiFiServer::open_sockets == 0);
    assert(!f.transport.getStatus().requested);
    assert(f.transport.begin(5000));
    assert(f.transport.getStatus().listening);
  } else if (scenario == 9) {
    assert(f.start());
    auto socket = f.connect(A);
    const Bytes reply{0x01, 0x23, 0x45};
    assert(f.transport.writeFrame(reply.data(), reply.size()) == reply.size());
    WiFiServer::listener_lost = true;
    f.transport.loop();
    assert(f.transport.getStatus().listening);
    assert(f.transport.isConnected() && socket->connected && f.cancellations == 0);
    socket->write_limit = std::numeric_limits<size_t>::max();
    for (int i = 0; i < 5; ++i) f.tick();
    assert((socket->sent == Bytes{'>', 3, 0, 0x01, 0x23, 0x45}));
  } else if (scenario == 10) {
    assert(f.start());
    WiFi.link_status = WL_DISCONNECTED;
    WiFi.station_ip = IPAddress();
    f.tick();
    const int begins = WiFiServer::begin_calls;
    assert(f.transport.getStatus().requested && !f.transport.getStatus().addressReady);
    for (int i = 0; i < 20; ++i) { mock_millis += 1000; f.tick(); }
    assert(WiFiServer::begin_calls == begins);
    WiFi.link_status = WL_CONNECTED;
    WiFi.station_ip = IPAddress(192, 168, 1, 11);
    f.tick();
    assert(f.transport.getStatus().listening);
    auto socket = f.connect(A, Bytes{'<', 1, 0, 0x16});
    assert(socket->connected && f.input[0] == 0x16);
  } else if (scenario == 11) {
    WiFi.station_ip = IPAddress();
    assert(!f.start());
    f.tick();
    assert(!f.transport.getStatus().addressReady && WiFiServer::begin_calls == 0);
    WiFi.station_ip = IPAddress(192, 168, 1, 11);
    f.tick();
    assert(f.transport.getStatus().listening);
  } else if (scenario == 12 || scenario == 13) {
    WiFi.link_status = WL_DISCONNECTED;
    WiFi.station_ip = IPAddress();
    WiFi.mode_value = WIFI_AP;
    if (scenario == 12) WiFi.access_point_ip = IPAddress(192, 168, 4, 1);
    assert(f.start() == (scenario == 12));
    f.tick();
    assert(f.transport.getStatus().addressReady == (scenario == 12));
    assert(f.transport.getStatus().listening == (scenario == 12));
    assert(WiFiServer::begin_calls == (scenario == 12 ? 1 : 0));
  } else if (scenario == 14) {
    assert(f.start());
    auto old = f.connect(A, Bytes{'<', 1, 0, 0x11});
    auto next = f.connect(B, Bytes{'<', 1, 0, 0x12});
    assert(!old->connected && next->connected && f.cancellations == 1);
    auto status = f.transport.getStatus();
    assert(status.acceptedClients == 2 && status.validFrames == 2);
    assert(status.lastOpcode == 0x12 && status.clientConnected && status.listening);
    assert(status.disconnects == 1);
    assert(status.lastDisconnectReason == SerialWifiInterface::DisconnectReason::Replaced);
  } else if (scenario == 15) {
    assert(f.start());
    auto socket = f.connect(A);
    auto accepted = f.transport.getStatus();
    assert(accepted.acceptedClients == 1 && accepted.validFrames == 0);
    socket->received = {'<', 1, 0, 0x16};
    assert(f.tick() == 1);
    auto parsed = f.transport.getStatus();
    assert(parsed.validFrames == 1 && parsed.lastOpcode == 0x16);
    socket->received = {'<', 2, 0, 0x42, 0x17};
    assert(f.tick() == 2);
    assert(f.transport.getStatus().validFrames == 2);
    assert(f.transport.getStatus().lastOpcode == 0x42);
    socket->connected = false; f.tick(); f.tick();
    assert(f.transport.getStatus().disconnects == 1);
    assert(f.transport.getStatus().lastDisconnectReason == SerialWifiInterface::DisconnectReason::PeerClosed);
    assert(f.cancellations == 1 && !f.transport.getStatus().clientConnected);
  } else if (scenario == 16) {
    assert(f.start());
    auto socket = f.connect(A, Bytes{'?', 1, 0, '<', 1, 0, 0x16});
    auto status = f.transport.getStatus();
    assert(!socket->connected && !status.clientConnected);
    assert(status.acceptedClients == 1 && status.validFrames == 0 && status.disconnects == 1);
    assert(status.lastDisconnectReason == SerialWifiInterface::DisconnectReason::InvalidHeader);
    assert(socket->read_count == 3 && status.listening);
    for (int i = 0; i < 20; ++i) assert(f.tick() == 0);
    assert(f.transport.getStatus().disconnects == 1);
    f.connect(A, Bytes{'<', 1, 0, 0x16});
    assert(f.transport.getStatus().validFrames == 1);
  } else if (scenario == 17) {
    assert(f.start());
    auto socket = f.connect(A, Bytes{'<', 2, 0, 0x16});
    mock_millis += 4999; f.tick();
    assert(socket->connected && f.transport.getStatus().disconnects == 0);
    ++mock_millis; f.tick();
    auto status = f.transport.getStatus();
    assert(!socket->connected && status.disconnects == 1 && status.validFrames == 0);
    assert(status.lastDisconnectReason == SerialWifiInterface::DisconnectReason::FrameTimeout);
  } else if (scenario == 18) {
    assert(f.start());
    auto socket = f.connect(A);
    socket->received = {'<', 1, 0, 0x16};
    const int begins = WiFiServer::begin_calls, ends = WiFiServer::end_calls;
    for (int i = 0; i < 100; ++i) {
      auto status = f.transport.getStatus();
      assert(status.listening && status.validFrames == 0);
    }
    assert(socket->read_count == 0 && WiFiServer::begin_calls == begins && WiFiServer::end_calls == ends);
    assert(f.tick() == 1 && f.transport.getStatus().validFrames == 1);
  } else if (scenario == 19) {
    WiFiServer::begin_fault = WiFiServer::BeginFault::Listen;
    assert(!f.transport.begin(5000));
    assert(!f.transport.getStatus().enabled);
    WiFiServer::begin_fault = WiFiServer::BeginFault::None;
    mock_millis += 30000;
    for (int i = 0; i < 20; ++i) f.tick();
    assert(WiFiServer::begin_calls == 1 && WiFiServer::open_sockets == 0);
    f.transport.enable(); f.tick();
    assert(f.transport.getStatus().listening && WiFiServer::begin_calls == 2);
  } else if (scenario == 20) {
    assert(f.start());
    auto socket = f.connect(A);
    const Bytes reply{0x05, 0x23, 0x45};
    assert(f.transport.writeFrame(reply.data(), reply.size()) == reply.size());
    assert(f.transport.getStatus().txFrames == 0);
    f.tick(); // A zero-byte write is neither a sent packet nor a failure.
    assert(f.transport.getStatus().txFrames == 0 && socket->sent.empty());
    socket->write_limit = 2;
    f.tick(); f.tick();
    assert(socket->sent.size() == 4 && f.transport.getStatus().txFrames == 0);
    f.tick();
    auto complete = f.transport.getStatus();
    assert(complete.txFrames == 1 && complete.lastTxOpcode == 0x05);
    for (int i = 0; i < 10; ++i) f.tick();
    assert(f.transport.getStatus().txFrames == 1);
  } else if (scenario == 21) {
    assert(!f.transport.begin(0));
    f.transport.enable();
    for (int i = 0; i < 20; ++i) { mock_millis += 1000; f.tick(); }
    auto status = f.transport.getStatus();
    assert(!status.requested && !status.listening && WiFiServer::begin_calls == 0);
    assert(status.beginAttempts == 0 && status.beginFailures == 0);
    assert(status.lastStartError == SerialWifiInterface::StartError::InvalidPort);
    assert(!f.transport.begin(65536));
    assert(f.transport.begin(5000));
    assert(f.transport.getStatus().listening);
  } else if (scenario == 22 || scenario == 23) {
    assert(f.start());
    auto socket = f.connect(A, scenario == 23 ? Bytes{'<', 2, 0} : Bytes{});
    socket->read_limit = 1;
    const Bytes remaining = scenario == 23 ? Bytes{0x16, 0x17} : Bytes{'<', 2, 0, 0x16, 0x17};
    socket->received.assign(remaining.begin(), remaining.end());
    assert(f.tick() == 0);
    auto status = f.transport.getStatus();
    assert(!socket->connected && status.validFrames == 0 && status.disconnects == 1);
    const auto expected = scenario == 22 ? SerialWifiInterface::DisconnectReason::ShortHeader
        : SerialWifiInterface::DisconnectReason::ShortPayload;
    assert(status.lastDisconnectReason == expected);
    assert(status.listening);
  } else if (scenario == 24) {
    assert(f.start());
    auto socket = f.connect(A, Bytes{'<', 1, 0, 0x16});
    const int begins = WiFiServer::begin_calls, ends = WiFiServer::end_calls;
    WiFi.station_ip = IPAddress(192, 168, 1, 99);
    f.tick();
    assert(f.transport.getStatus().listening && socket->connected && f.cancellations == 0);
    assert(WiFiServer::begin_calls == begins && WiFiServer::end_calls == ends);
  } else if (scenario == 25) {
    WiFiServer::begin_fault = WiFiServer::BeginFault::Socket;
    assert(!f.transport.begin(5000));
    MultiSerialInterface manager;
    assert(manager.addInterface(InterfaceType::WiFi, &f.transport));
    manager.enable();
    assert(!f.transport.isEnabled());
    assert(manager.checkRecvFrame(f.input) == 0);
    mock_millis += 1000;
    WiFiServer::begin_fault = WiFiServer::BeginFault::None;
    manager.loop(); // Must service failed transports despite unavailable dispatch.
    assert(f.transport.isEnabled() && f.transport.getStatus().listening);
    auto socket = std::make_shared<MockSocket>(A);
    socket->received = {'<', 1, 0, 0x16};
    WiFiServer::incoming.push_back(WiFiClient(socket));
    assert(manager.checkRecvFrame(f.input) == 1 && f.input[0] == 0x16);
  } else if (scenario == 26) {
    assert(f.start());
    auto socket = f.connect(A);
    MultiSerialInterface manager;
    assert(manager.addInterface(InterfaceType::WiFi, &f.transport));
    manager.enable();
    socket->received = {'<', 1, 0, 0x04};
    assert(manager.checkRecvFrame(f.input) == 1 && f.input[0] == 0x04);
    manager.lockReplyRoute();
    assert(manager.isReplyRouteAvailable(&f.transport));
    const Bytes reply{0x05, 0x23, 0x45};
    assert(manager.writeFrame(reply.data(), reply.size()) == reply.size());
    WiFiServer::listener_lost = true;
    WiFiServer::begin_fault = WiFiServer::BeginFault::Bind;
    manager.loop();
    assert(!f.transport.getStatus().listening && socket->connected && f.cancellations == 0);
    assert(f.transport.hasPendingIO() && WiFiServer::open_sockets == 0);
    assert(f.transport.isEnabled() && manager.isConnected());
    assert(manager.isReplyRouteAvailable(&f.transport));
    // A separately accepted socket must continue this locked response stream
    // before listener repair succeeds; a listener-only readiness test breaks it.
    socket->write_limit = std::numeric_limits<size_t>::max();
    assert(manager.checkRecvFrame(f.input) == 0);
    assert((socket->sent == Bytes{'>', 3, 0, 0x05, 0x23, 0x45}));
    assert(f.transport.getStatus().txFrames == 1);
    socket->received = {'<', 1, 0, 0x16};
    assert(manager.checkRecvFrame(f.input) == 1 && f.input[0] == 0x16);
    assert(f.transport.getStatus().validFrames == 2);
    assert(!f.transport.getStatus().listening && WiFiServer::begin_calls == 2);
    mock_millis += 1000;
    WiFiServer::begin_fault = WiFiServer::BeginFault::None;
    manager.loop();
    assert(f.transport.getStatus().listening && socket->connected && f.cancellations == 0);
    assert(manager.isReplyRouteAvailable(&f.transport));
  } else if (scenario == 27) {
    WiFi.mode_value = WIFI_STA;
    WiFi.link_status = WL_DISCONNECTED;
    WiFi.station_ip = IPAddress();
    WiFi.access_point_ip = IPAddress(192, 168, 4, 1);
    assert(!f.start());
    f.tick();
    assert(!f.transport.getStatus().addressReady && WiFiServer::begin_calls == 0);
  } else if (scenario == 28 || scenario == 31 || scenario == 32 || scenario == 33) {
    assert(f.start());
    auto socket = f.connect(A);
    f.enqueue(Bytes{0x80, 0xa1});
    if (scenario == 28) socket->write_limit = std::numeric_limits<size_t>::max();
    if (scenario == 32) socket->send_error = EAGAIN;
    if (scenario == 33) socket->send_error = EINTR;
    if (scenario == 31) {
      socket->sdk_write_block_ms = 10000;
      MockTcp::sdk_write_hook = [](uint32_t delay) { mock_millis += delay; };
    }
    socket->received = {'<', 2, 0, 0x16, 3};
    const auto before = mock_millis;
    assert(f.tick() == 2 && f.input[0] == 0x16 && f.input[1] == 3);
    assert(mock_millis == before && socket->sdk_write_calls == 0 && socket->send_calls == 1);
    assert(socket->last_send_flags & MSG_DONTWAIT);
    assert(socket->connected && f.cancellations == 0);
    if (scenario != 28) {
      assert(socket->sent.empty() && f.transport.hasPendingIO());
      socket->send_error = 0;
      f.drain(socket);
      assert((socket->sent == framed({Bytes{0x80, 0xa1}})));
    }
  } else if (scenario == 29) {
    assert(f.start());
    auto socket = f.connect(A);
    for (int poll = 0; poll < 100; ++poll) {
      const Bytes push{0x88, uint8_t(poll)};
      // Production permits shedding best-effort radio logs when saturated.
      f.transport.writeFrame(push.data(), push.size());
      socket->received = {'<', 2, 0, 0x16, 3};
      assert(f.tick() == 2 && f.input[0] == 0x16 && f.input[1] == 3);
      ++mock_millis;
    }
    auto status = f.transport.getStatus();
    assert(status.validFrames == 100 && status.acceptedClients == 1);
    assert(socket->sdk_write_calls == 0 && socket->send_calls == 100);
    assert(status.clientConnected && f.cancellations == 0 && f.transport.hasPendingIO());
  } else if (scenario == 30) {
    assert(f.start());
    auto socket = f.connect(A);
    const Bytes first{0x80, 0xa1}, second{0x88, 0xb1}, info{0x0d, 0x31, 0x32};
    f.enqueue(first); f.enqueue(second);
    socket->write_limit = 1;
    socket->received = {'<', 2, 0, 0x16, 3};
    assert(f.tick() == 2 && socket->sent == Bytes{'>'});
    assert(f.transport.getStatus().txFrames == 0);
    f.enqueue(info);
    f.drain(socket);
    assert(socket->sent == framed({first, info, second}));
    assert(f.transport.getStatus().txFrames == 3 && f.transport.getStatus().validFrames == 1);
  } else if (scenario == 34 || scenario == 35 || scenario == 42) {
    assert(f.start());
    auto socket = f.connect(A);
    MultiSerialInterface manager;
    f.manager = &manager;
    assert(manager.addInterface(InterfaceType::WiFi, &f.transport)); manager.enable();
    socket->received = {'<', 2, 0, 0x16, 3};
    assert(manager.checkRecvFrame(f.input) == 2); manager.lockReplyRoute();
    f.pending_operation = true;
    const Bytes reply{0x0d, 0x31, 0x32};
    assert(manager.writeFrame(reply.data(), reply.size()) == reply.size());
    socket->write_limit = 2; f.tick();
    assert(socket->sent.size() == 2);
    if (scenario == 34 || scenario == 42) socket->send_error = EPIPE;
    else socket->descriptor_override = -1;
    socket->received = {'<', 2, 0, 0x16, 3};
    assert(f.tick() == 0);
    assert(!socket->connected && f.cancellations == 1 && !f.pending_operation);
    assert(!manager.isReplyRouteAvailable(&f.transport));
    assert(f.transport.getStatus().lastDisconnectReason == SerialWifiInterface::DisconnectReason::TxError);
    assert(socket->read_count == 5); // Fatal TX must not execute the next command.
    f.transport.loop(); assert(f.cancellations == 1);
    f.manager = nullptr;
    if (scenario == 42) {
      auto other = f.connect(B, Bytes{'<', 2, 0, 0x16, 3});
      const Bytes other_reply{0x0d, 0x41}; f.enqueue(other_reply); f.drain(other);
      assert(other->sent == framed({other_reply}));
    }
    auto returned = f.connect(A); f.drain(returned);
    assert(returned->sent == framed({reply}));
  } else if (scenario == 36 || scenario == 37) {
    assert(f.start());
    auto socket = f.connect(A);
    if (scenario == 37) mock_millis = UINT32_MAX - 5000;
    const Bytes reply{0x0d, 0x51}; f.enqueue(reply); f.tick();
    mock_millis += 9999; f.tick();
    assert(socket->connected && f.cancellations == 0);
    ++mock_millis; f.tick();
    assert(!socket->connected && f.cancellations == 1);
    assert(f.transport.getStatus().lastDisconnectReason == SerialWifiInterface::DisconnectReason::TxTimeout);
    auto returned = f.connect(A); f.drain(returned);
    assert(returned->sent == framed({reply}));
  } else if (scenario == 38) {
    assert(f.start());
    auto socket = f.connect(A); f.enqueue(Bytes{0x0d, 0x51}); f.tick();
    mock_millis += 9999; socket->write_limit = 1; f.tick();
    assert(socket->connected && socket->sent.size() == 1);
    socket->write_limit = 0;
    mock_millis += 9999; f.tick();
    assert(socket->connected && f.cancellations == 0);
    ++mock_millis; f.tick();
    assert(!socket->connected && f.cancellations == 1);
    assert(f.transport.getStatus().lastDisconnectReason == SerialWifiInterface::DisconnectReason::TxTimeout);
  } else if (scenario == 39) {
    assert(f.start());
    auto socket = f.connect(A); f.enqueue(Bytes{0x0d, 0x51}); f.drain(socket);
    mock_millis += 60000; f.transport.loop();
    assert(socket->connected && f.cancellations == 0);
    socket->write_limit = 0; f.enqueue(Bytes{0x01, 0x61}); f.tick();
    mock_millis += 9999; f.tick();
    assert(socket->connected);
    ++mock_millis; f.tick();
    assert(!socket->connected && f.cancellations == 1);
  } else if (scenario == 40) {
    assert(f.start());
    auto socket = f.connect(A); f.enqueue(Bytes{0x88, 0x51});
    socket->received = {'<', 2}; f.tick();
    mock_millis += 4999; f.tick();
    assert(socket->connected);
    ++mock_millis; f.transport.loop();
    assert(!socket->connected && f.cancellations == 1);
    assert(f.transport.getStatus().lastDisconnectReason == SerialWifiInterface::DisconnectReason::FrameTimeout);
  } else if (scenario == 41) {
    assert(f.start());
    auto old = f.connect(A); const Bytes reply{0x0d, 0x51}; f.enqueue(reply); f.tick();
    mock_millis += 9999;
    auto next = f.connect(A);
    assert(!old->connected && next->connected && f.cancellations == 0);
    mock_millis += 9999; f.transport.loop();
    assert(next->connected && f.cancellations == 0);
    f.drain(next); assert(next->sent == framed({reply}));
  } else if (scenario == 43) {
    assert(f.start());
    auto socket = f.connect(A); f.enqueue(Bytes{0x0d, 0x51});
    socket->send_error = EWOULDBLOCK; socket->received = {'<', 2, 0, 0x16, 3};
    assert(f.tick() == 2 && socket->connected && f.cancellations == 0);
    socket->send_error = ECONNRESET; assert(f.tick() == 0);
    assert(!socket->connected && f.cancellations == 1);
    assert(f.transport.getStatus().lastDisconnectReason == SerialWifiInterface::DisconnectReason::TxError);
  } else if (scenario == 44) {
    assert(f.start());
    auto socket = f.connect(A);
    for (int i = 0; i < 4; ++i) f.enqueue(Bytes{0x01, uint8_t(i)});
    socket->received = {'<', 2, 0, 0x16, 3};
    assert(f.tick() == 0 && socket->read_count == 0 && f.transport.getStatus().validFrames == 0);
    socket->write_limit = 2; assert(f.tick() == 0 && socket->read_count == 0);
    socket->write_limit = std::numeric_limits<size_t>::max();
    assert(f.tick() == 2 && f.input[0] == 0x16 && f.input[1] == 3);
    f.enqueue(Bytes{0x0d, 0x51});
    f.drain(socket);
    assert((socket->sent == framed({Bytes{0x01, 0}, Bytes{0x01, 1}, Bytes{0x01, 2}, Bytes{0x01, 3}, Bytes{0x0d, 0x51}})));
  } else if (scenario == 45) {
    assert(f.start());
    auto socket = f.connect(A);
    const Bytes best{0x88, 0x41}; f.enqueue(best);
    for (int i = 0; i < 3; ++i) f.enqueue(Bytes{0x01, uint8_t(i)});
    socket->received = {'<', 2, 0, 0x16, 3};
    assert(f.tick() == 2);
    f.enqueue(Bytes{0x0d, 0x51}); f.drain(socket);
    assert((socket->sent == framed({Bytes{0x01, 0}, Bytes{0x01, 1}, Bytes{0x01, 2}, Bytes{0x0d, 0x51}})));
  } else if (scenario == 46) {
    assert(f.start());
    auto socket = f.connect(A); const Bytes best{0x88, 0x41}; f.enqueue(best);
    socket->write_limit = 1; f.tick();
    assert(socket->sent == Bytes{'>'});
    for (int i = 0; i < 3; ++i) f.enqueue(Bytes{0x01, uint8_t(i)});
    socket->write_limit = 0; socket->received = {'<', 2, 0, 0x16, 3};
    assert(f.tick() == 0 && socket->read_count == 0);
    socket->write_limit = std::numeric_limits<size_t>::max();
    assert(f.tick() == 2); f.enqueue(Bytes{0x0d, 0x51}); f.drain(socket);
    assert((socket->sent == framed({best, Bytes{0x01, 0}, Bytes{0x01, 1}, Bytes{0x01, 2}, Bytes{0x0d, 0x51}})));
  } else if (scenario == 47) {
    assert(f.start());
    auto socket = f.connect(A, Bytes{'<', 2, 0, 0x16});
    assert(socket->read_count == 3 && f.transport.hasReceivedFrameHeader());
    for (int i = 0; i < 4; ++i) f.enqueue(Bytes{0x01, uint8_t(i)});
    socket->received.push_back(3); assert(f.tick() == 0);
    mock_millis += 6000; f.transport.loop();
    assert(socket->connected && f.cancellations == 0 && f.transport.hasReceivedFrameHeader());
    socket->write_limit = std::numeric_limits<size_t>::max();
    assert(f.tick() == 2 && f.input[0] == 0x16 && f.input[1] == 3);
    assert(socket->connected && f.cancellations == 0);
  } else if (scenario == 48) {
    assert(f.start());
    auto socket = f.connect(A); const Bytes reply{0x0d, 0x51, 0x52};
    f.enqueue(reply); socket->write_limit = 1; f.tick();
    struct HoldingInterface : BaseSerialInterface {
      bool input = true;
      void enable() override {}
      void disable() override {}
      bool isEnabled() const override { return true; }
      bool isConnected() const override { return true; }
      bool isReadBusy() const override { return false; }
      bool isWriteBusy() const override { return false; }
      size_t writeFrame(const uint8_t[], size_t size) override { return size; }
      size_t checkRecvFrame(uint8_t dest[]) override {
        if (!input) return 0;
        input = false; dest[0] = 0x04; return 1;
      }
    } other;
    MultiSerialInterface manager;
    assert(manager.addInterface(InterfaceType::WiFi, &f.transport));
    assert(manager.addInterface(InterfaceType::USB, &other)); manager.enable();
    assert(manager.checkRecvFrame(f.input) == 1 && f.input[0] == 0x04);
    manager.lockReplyRoute();
    const size_t sent = socket->sent.size(), sends = socket->send_calls;
    for (int i = 0; i < 20; ++i) {
      mock_millis += 1000; manager.loop();
      assert(manager.checkRecvFrame(f.input) == 0);
    }
    assert(socket->connected && f.cancellations == 0);
    assert(socket->sent.size() == sent && socket->send_calls == sends);
    manager.unlockReplyRoute(); socket->write_limit = std::numeric_limits<size_t>::max();
    manager.checkRecvFrame(f.input);
    assert(socket->connected && f.cancellations == 0 && socket->sent == framed({reply}));
  } else if (scenario == 49 || scenario == 51) {
    assert(f.start());
    if (scenario == 51) mock_millis = UINT32_MAX - 3000;
    auto socket = f.connect(A, Bytes{'<', 2, 0, 0x16});
    mock_millis += 4000;
    for (int i = 0; i < 4; ++i) f.enqueue(Bytes{0x01, uint8_t(i)});
    f.tick(); mock_millis += 6000; f.transport.loop();
    assert(socket->connected && f.cancellations == 0 && f.transport.hasReceivedFrameHeader());
    socket->write_limit = std::numeric_limits<size_t>::max(); f.tick(); // Frees a reply slot, but body is incomplete.
    mock_millis += 999; f.tick();
    assert(socket->connected && f.cancellations == 0);
    ++mock_millis; f.transport.loop();
    assert(!socket->connected && f.cancellations == 1);
    assert(f.transport.getStatus().lastDisconnectReason == SerialWifiInterface::DisconnectReason::FrameTimeout);
  } else if (scenario == 50) {
    assert(f.start());
    auto socket = f.connect(A, Bytes{'<', 2, 0, 0x16});
    mock_millis += 5000;
    for (int i = 0; i < 4; ++i) f.enqueue(Bytes{0x01, uint8_t(i)});
    f.transport.loop();
    assert(!socket->connected && f.cancellations == 1);
    assert(f.transport.getStatus().lastDisconnectReason == SerialWifiInterface::DisconnectReason::FrameTimeout);
  } else if (scenario == 52) {
    assert(f.start());
    auto socket = f.connect(A);
    socket->write_limit = std::numeric_limits<size_t>::max();
    RoutingPeer usb;
    MultiSerialInterface manager;
    assert(manager.addInterface(InterfaceType::WiFi, &f.transport));
    assert(manager.addInterface(InterfaceType::USB, &usb)); manager.enable();
    const Bytes wifi_reply{0x0d, 0xa1}, usb_reply{0x0d, 0xb1};
    unsigned wifi_commands = 0;
    for (unsigned poll = 0; poll < 10000; ++poll) {
      const uint8_t query[] = {'<', 2, 0, 0x16, 3};
      for (uint8_t byte : query) socket->received.push_back(byte);
      if (poll % 100 == 0) usb.input.push_back(Bytes{0x16, 4});
      ++mock_millis; manager.loop();
      assert(manager.checkRecvFrame(f.input) == 2 && f.input[0] == 0x16);
      auto* route = manager.captureReplyRoute();
      assert(route == (f.input[1] == 3 ? static_cast<BaseSerialInterface*>(&f.transport) : &usb));
      const Bytes& reply = route == &usb ? usb_reply : wifi_reply;
      if (route == &f.transport) ++wifi_commands;
      assert(manager.writeFrame(reply.data(), reply.size()) == reply.size());
      assert((poll % 100 < 1 || usb.input.empty())
          && "an unlocked pipelined WiFi client must not starve a ready USB command");
    }
    // Finish the finite WiFi pipeline and the last accepted response.
    for (unsigned poll = 0; poll < 102; ++poll) {
      const size_t length = manager.checkRecvFrame(f.input);
      if (length == 0) break;
      assert(length == 2 && manager.captureReplyRoute() == &f.transport);
      ++wifi_commands;
      assert(manager.writeFrame(wifi_reply.data(), wifi_reply.size()) == wifi_reply.size());
    }
    assert(socket->received.empty() && wifi_commands == 10000);
    assert(f.transport.getStatus().txFrames == wifi_commands);
    Bytes expected;
    const Bytes wire_reply = framed({wifi_reply});
    for (unsigned i = 0; i < wifi_commands; ++i)
      expected.insert(expected.end(), wire_reply.begin(), wire_reply.end());
    assert(socket->sent == expected && usb.output == std::vector<Bytes>(100, usb_reply));
  } else if (scenario == 53) {
    assert(f.start());
    auto socket = f.connect(A);
    socket->write_limit = std::numeric_limits<size_t>::max();
    RoutingPeer usb, ble, ethernet;
    MultiSerialInterface manager;
    BaseSerialInterface* routes[] = {&f.transport, &usb, &ble, &ethernet};
    RoutingPeer* peers[] = {&usb, &ble, &ethernet};
    const InterfaceType types[] = {InterfaceType::WiFi, InterfaceType::USB,
                                  InterfaceType::Bluetooth, InterfaceType::Ethernet};
    for (unsigned i = 0; i < 4; ++i) assert(manager.addInterface(types[i], routes[i]));
    manager.enable();
    socket->received = {'<', 2, 0, 0x16, 0};
    for (unsigned i = 0; i < 3; ++i) peers[i]->input.push_back(Bytes{0x16, uint8_t(i + 1)});
    unsigned counts[4] = {};
    BaseSerialInterface* captured_wifi = nullptr;
    for (unsigned poll = 0; poll < 16000; ++poll) {
      ++mock_millis; manager.loop();
      assert(manager.checkRecvFrame(f.input) == 2);
      const unsigned index = poll % 4;
      assert(manager.captureReplyRoute() == routes[index]
          && "each unlocked ready transport must be served within one registration cycle");
      assert(f.input[0] == 0x16 && f.input[1] == index);
      ++counts[index];
      const uint8_t reply[] = {0x0d, uint8_t(index)};
      assert(manager.writeFrame(reply, sizeof(reply)) == sizeof(reply));
      if (index == 0) {
        captured_wifi = manager.captureReplyRoute();
        if (counts[index] < 4000) socket->received = {'<', 2, 0, 0x16, 0};
      } else if (counts[index] < 4000) {
        peers[index - 1]->input.push_back(Bytes{0x16, uint8_t(index)});
      }
    }
    assert(manager.checkRecvFrame(f.input) == 0); // Drain WiFi's final response.
    for (unsigned count : counts) assert(count == 4000);
    for (unsigned i = 0; i < 3; ++i)
      assert(peers[i]->output == std::vector<Bytes>(4000, Bytes{0x0d, uint8_t(i + 1)}));
    Bytes expected;
    for (unsigned i = 0; i < 4000; ++i) {
      const Bytes frame = framed({Bytes{0x0d, 0}});
      expected.insert(expected.end(), frame.begin(), frame.end());
    }
    assert(socket->sent == expected && f.transport.getStatus().txFrames == 4000);
    // A captured delayed route is independent of the most recent requester.
    assert(manager.captureReplyRoute() == &ethernet && captured_wifi == &f.transport);
    const uint8_t delayed[] = {0x85, 0x41};
    assert(manager.writeFrameToRoute(captured_wifi, delayed, sizeof(delayed)) == sizeof(delayed));
    assert(manager.checkRecvFrame(f.input) == 0);
    const Bytes last = framed({Bytes{0x85, 0x41}});
    expected.insert(expected.end(), last.begin(), last.end());
    assert(socket->sent == expected && ethernet.output.size() == 4000);
  } else if (scenario == 54) {
    assert(f.start());
    auto socket = f.connect(A, Bytes{'<', 2, 0, 0x16, 3});
    RoutingPeer usb, ble;
    MultiSerialInterface manager;
    assert(manager.addInterface(InterfaceType::WiFi, &f.transport));
    assert(manager.addInterface(InterfaceType::USB, &usb));
    assert(manager.addInterface(InterfaceType::Bluetooth, &ble)); manager.enable();
    socket->received = {'<', 2, 0, 0x16, 3};
    assert(manager.checkRecvFrame(f.input) == 2); manager.lockReplyRoute();
    usb.input.push_back(Bytes{0x16, 4}); ble.input.push_back(Bytes{0x16, 5});
    const uint8_t reply[] = {0x03, 0x41};
    for (unsigned poll = 0; poll < 8; ++poll) {
      socket->received = {'<', 2, 0, 0x16, 3};
      assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &f.transport);
      assert(manager.writeFrame(reply, sizeof(reply)) == sizeof(reply));
      socket->write_limit = std::numeric_limits<size_t>::max();
    }
    assert(manager.checkRecvFrame(f.input) == 0 && manager.captureReplyRoute() == &f.transport);
    assert(usb.polls == 0 && ble.polls == 0 && usb.output.empty() && ble.output.empty());
    socket->connected = false; manager.loop();
    assert(!manager.isConnected() && manager.checkRecvFrame(f.input) == 0);
    assert(manager.writeFrame(reply, sizeof(reply)) == 0);
    assert(usb.polls == 0 && ble.polls == 0);
    // The response producer owns cancellation; forgetting its disconnected
    // route releases the lock without leaking the abandoned response.
    manager.forgetReplyRouteForDisconnected(&f.transport);
    assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &usb);
    assert(manager.writeFrame(reply, sizeof(reply)) == sizeof(reply));
    assert((usb.output == std::vector<Bytes>{Bytes{0x03, 0x41}} && ble.output.empty()));
    assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &ble);
  } else if (scenario == 55) {
    RoutingPeer first, removed, third, fourth, replacement;
    RoutingPeer* peers[] = {&first, &removed, &third, &fourth};
    MultiSerialInterface manager;
    for (auto* peer : peers) assert(manager.addInterface(InterfaceType::USB, peer));
    manager.enable();
    for (auto* peer : peers) peer->input.push_back(Bytes{0x16, 3});
    assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &first);
    assert(manager.removeInterface(&removed));
    assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &third);
    assert(manager.addInterface(InterfaceType::Ethernet, &replacement)); replacement.enable();
    replacement.input.push_back(Bytes{0x16, 3}); first.input.push_back(Bytes{0x16, 3});
    assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &fourth);
    assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &first);
    assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &replacement);
    third.input.push_back(Bytes{0x16, 3}); third.disable();
    fourth.input.push_back(Bytes{0x16, 3});
    assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &fourth);
    assert(third.input.size() == 1 && removed.input.size() == 1);
    first.input.push_back(Bytes{0x16, 3}); manager.disable();
    assert(manager.checkRecvFrame(f.input) == 0 && manager.captureReplyRoute() == nullptr);
    assert(first.input.size() == 1);
    manager.enable();
    assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &first);
    assert(manager.checkRecvFrame(f.input) == 2 && manager.captureReplyRoute() == &third);
  } else {
    assert(false && "unknown scenario");
  }
  std::printf("PASS: Wi-Fi listener scenario %d\n", scenario);
}
