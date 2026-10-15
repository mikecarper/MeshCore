#pragma once
#include <Arduino.h>
#include <algorithm>
#include <deque>
#include <map>
#include <memory>
#include <vector>

static constexpr int WIFI_OFF = 0, WIFI_STA = 1, WIFI_AP = 2, WIFI_AP_STA = 3;
static constexpr int WL_DISCONNECTED = 6, WL_CONNECTED = 3;

class IPAddress {
  uint32_t _value;
public:
  explicit IPAddress(uint32_t value = 0) : _value(value) {}
  operator uint32_t() const { return _value; }
};

struct FakeSocket {
  bool connected = true;
  bool eof = false;
  int readError = 0, writeError = 0;
  size_t writeQuota = 128;
  size_t reads = 0, writes = 0;
  std::deque<uint8_t> input;
  std::vector<uint8_t> output;
};

namespace fake {
inline std::map<int, std::shared_ptr<FakeSocket>> sockets;
inline std::deque<int> pending;
inline int nextFd = 3;
inline uint32_t boundAddress = 0;
inline uint16_t boundPort = 0;
inline bool listenFails = false;
inline unsigned begins = 0, ends = 0;
inline int newSocket() {
  const int fd = nextFd++;
  sockets[fd] = std::make_shared<FakeSocket>();
  pending.push_back(fd);
  return fd;
}
}

class WiFiClient {
  std::shared_ptr<FakeSocket> _socket;
  int _fd = -1;
public:
  WiFiClient() = default;
  explicit WiFiClient(int fd) : _socket(fake::sockets.at(fd)), _fd(fd) {}
  explicit operator bool() { return connected(); }
  bool connected() { return _socket && _socket->connected; }
  void stop() { if (_socket) _socket->connected = false; _socket.reset(); _fd = -1; }
  void setNoDelay(bool) {}
  int fd() const { return _fd; }
};

class WiFiServer {
  uint32_t _address;
  uint16_t _port;
  bool _listening = false;
public:
  explicit WiFiServer(uint16_t port = 80, uint8_t = 4) : _address(0), _port(port) {}
  WiFiServer(const IPAddress& address, uint16_t port, uint8_t = 4)
    : _address(address), _port(port) {}
  void begin() {
    ++fake::begins;
    _listening = !fake::listenFails;
    if (_listening) { fake::boundAddress = _address; fake::boundPort = _port; }
  }
  void end() {
    ++fake::ends;
    _listening = false;
    while (!fake::pending.empty()) {
      fake::sockets.at(fake::pending.front())->connected = false;
      fake::pending.pop_front();
    }
  }
  explicit operator bool() { return _listening; }
  WiFiClient available() {
    if (!_listening || fake::pending.empty()) return WiFiClient();
    const int fd = fake::pending.front();
    fake::pending.pop_front();
    return WiFiClient(fd);
  }
};

class WiFiClass {
public:
  int mode = WIFI_STA;
  int state = WL_CONNECTED;
  uint32_t address = 0x01020304;
  int getMode() const { return mode; }
  int status() const { return state; }
  IPAddress localIP() const { return IPAddress(address); }
};
inline WiFiClass WiFi;
