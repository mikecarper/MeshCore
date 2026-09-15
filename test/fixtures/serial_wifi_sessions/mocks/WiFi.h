#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <limits>
#include <memory>
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
};

struct MockSocket {
  IPAddress address;
  bool connected = true;
  size_t write_limit = 0;
  size_t read_count = 0;
  std::vector<uint8_t> sent;
  std::deque<uint8_t> received;
  explicit MockSocket(IPAddress value) : address(value) {}
};

class WiFiClient {
  std::shared_ptr<MockSocket> _socket;
public:
  WiFiClient() = default;
  explicit WiFiClient(std::shared_ptr<MockSocket> socket) : _socket(socket) {}
  explicit operator bool() const { return _socket && _socket->connected; }
  bool connected() const { return _socket && _socket->connected; }
  void stop() { if (_socket) _socket->connected = false; }
  IPAddress remoteIP() const { return _socket ? _socket->address : IPAddress(); }
  int available() const { return connected() ? int(_socket->received.size()) : 0; }
  size_t read(uint8_t* dest, size_t size) {
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
    const size_t written = std::min(size, _socket->write_limit);
    _socket->sent.insert(_socket->sent.end(), src, src + written);
    return written;
  }
};

class WiFiServer {
public:
  static std::deque<WiFiClient> incoming;
  void begin(int) {}
  void end() { incoming.clear(); }
  WiFiClient available() {
    if (incoming.empty()) return WiFiClient();
    WiFiClient client = incoming.front();
    incoming.pop_front();
    return client;
  }
};
