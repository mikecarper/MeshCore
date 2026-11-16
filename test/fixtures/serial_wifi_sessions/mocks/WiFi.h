#pragma once

#include <algorithm>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <limits>
#include <map>
#include <memory>
#include <string>
#include <vector>

class IPAddress {
  uint32_t _address = 0;
public:
  IPAddress() = default;
  IPAddress(uint32_t address) : _address(address) {}
  IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d)
      : _address(uint32_t(a) << 24 | uint32_t(b) << 16 | uint32_t(c) << 8 | d) {}
  operator uint32_t() const { return _address; }
  bool operator==(const IPAddress& other) const { return _address == other._address; }
  bool operator!=(const IPAddress& other) const { return !(*this == other); }
  std::string toString() const {
    return std::to_string((_address >> 24) & 255) + "."
        + std::to_string((_address >> 16) & 255) + "."
        + std::to_string((_address >> 8) & 255) + "."
        + std::to_string(_address & 255);
  }
};

constexpr int WL_CONNECTED = 3;
constexpr int WL_DISCONNECTED = 6;
constexpr int WIFI_OFF = 0;
constexpr int WIFI_STA = 1;
constexpr int WIFI_AP = 2;
constexpr int WIFI_AP_STA = 3;

struct WiFiClass {
  int link_status = WL_CONNECTED;
  int mode_value = WIFI_STA;
  IPAddress station_ip{192, 168, 1, 10};
  IPAddress access_point_ip;
  int status() const { return link_status; }
  int getMode() const { return mode_value; }
  IPAddress localIP() const { return station_ip; }
  IPAddress softAPIP() const { return access_point_ip; }
};

inline WiFiClass WiFi;

struct MockSocket;
namespace MockTcp {
inline int next_descriptor = 3;
inline std::map<int, std::weak_ptr<MockSocket>> sockets;
inline void (*sdk_write_hook)(uint32_t) = nullptr;
}

struct MockSocket {
  IPAddress address;
  bool connected = true;
  int descriptor = MockTcp::next_descriptor++;
  int descriptor_override = -2;
  size_t write_limit = 0;
  size_t sdk_write_calls = 0;
  uint32_t sdk_write_block_ms = 0;
  size_t send_calls = 0;
  int last_send_flags = 0;
  int send_error = 0;
  std::deque<int> send_results;
  size_t read_limit = std::numeric_limits<size_t>::max();
  size_t read_count = 0;
  std::vector<uint8_t> sent;
  std::deque<uint8_t> received;
  explicit MockSocket(IPAddress value) : address(value) {}
};

class WiFiClient {
  std::shared_ptr<MockSocket> _socket;
public:
  WiFiClient() = default;
  explicit WiFiClient(std::shared_ptr<MockSocket> socket) : _socket(socket) {
    MockTcp::sockets[_socket->descriptor] = _socket;
  }
  explicit operator bool() { return _socket && _socket->connected; }
  // Match the pinned ESP32 SDK's non-const connected() signature. This catches
  // const transport accessors that accidentally rely on a const-only fake.
  bool connected() { return _socket && _socket->connected; }
  // Public SDK 2.x/3.x API; no test-only access to a protected socket handle.
  virtual int fd() const {
    if (!_socket) return -1;
    return _socket->descriptor_override == -2 ? _socket->descriptor : _socket->descriptor_override;
  }
  void stop() { if (_socket) _socket->connected = false; }
  IPAddress remoteIP() const { return _socket ? _socket->address : IPAddress(); }
  int available() { return connected() ? int(_socket->received.size()) : 0; }
  int read() { uint8_t b = 0; return read(&b, 1) == 1 ? b : -1; }
  int connect(IPAddress address, uint16_t, int) {
    _socket = std::make_shared<MockSocket>(address);
    MockTcp::sockets[_socket->descriptor] = _socket;
    return 1;
  }
  size_t read(uint8_t* dest, size_t size) {
    if (_socket) size = std::min(size, _socket->read_limit);
    size_t read = 0;
    while (connected() && read < size && !_socket->received.empty()) {
      dest[read++] = _socket->received.front();
      _socket->received.pop_front();
      ++_socket->read_count;
    }
    return read;
  }
  size_t readBytes(uint8_t* dest, size_t size) { return read(dest, size); }
  size_t write(const uint8_t* src, size_t size) {
    if (!connected()) return 0;
    ++_socket->sdk_write_calls;
    if (MockTcp::sdk_write_hook) MockTcp::sdk_write_hook(_socket->sdk_write_block_ms);
    const size_t written = std::min(size, _socket->write_limit);
    _socket->sent.insert(_socket->sent.end(), src, src + written);
    return written;
  }
};

class WiFiServer {
  bool _listening = false;
  bool _owns_socket = false;
public:
  explicit WiFiServer(int = 0) {}
  enum class BeginFault { None, Socket, Bind, Listen };
  static std::deque<WiFiClient> incoming;
  inline static BeginFault begin_fault = BeginFault::None;
  inline static int begin_calls = 0;
  inline static int end_calls = 0;
  inline static int open_sockets = 0;
  inline static int last_port = 0;
  inline static bool listener_lost = false;
  static void resetDiagnostics() {
    begin_fault = BeginFault::None;
    begin_calls = end_calls = open_sockets = last_port = 0;
    listener_lost = false;
    incoming.clear();
    WiFi = WiFiClass{};
    MockTcp::sockets.clear();
    MockTcp::next_descriptor = 3;
    MockTcp::sdk_write_hook = nullptr;
  }
  void begin(int port) {
    ++begin_calls;
    last_port = port;
    if (_listening) return;
    if (begin_fault == BeginFault::Socket) { errno = ENOMEM; return; }
    // The pinned Arduino ESP32 WiFiServer leaves its descriptor open when
    // bind/listen fails. A wrapper must call end() before a bounded retry.
    _owns_socket = true;
    ++open_sockets;
    if (begin_fault == BeginFault::Bind) { errno = EADDRINUSE; return; }
    if (begin_fault == BeginFault::Listen) { errno = EIO; return; }
    _listening = true;
    listener_lost = false;
    errno = 0;
  }
  void begin() { begin(5005); }
  void end() {
    ++end_calls;
    if (_owns_socket) --open_sockets;
    _owns_socket = _listening = false;
    listener_lost = false;
    incoming.clear();
    // Cleanup may overwrite errno; diagnostics must preserve the start error.
    errno = 0;
  }
  operator bool() { return _listening && !listener_lost; }
  bool hasClient() { return !incoming.empty(); }
  WiFiClient available() {
    if (!static_cast<bool>(*this) || incoming.empty()) return WiFiClient();
    WiFiClient client = incoming.front();
    incoming.pop_front();
    return client;
  }
};
