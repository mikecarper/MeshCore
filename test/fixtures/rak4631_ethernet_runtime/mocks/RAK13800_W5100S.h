#pragma once
#include <Arduino.h>
#include <SPI.h>

class IPAddress {
  uint32_t address;
public:
  explicit IPAddress(uint32_t value = 0x2a01a8c0) : address(value) {}
  uint8_t operator[](size_t index) const { return (address >> (8 * index)) & 255; }
};
extern std::atomic<bool> mock_socket_connected[4];
extern std::atomic<int> mock_stop_count[4];
extern std::atomic<bool> mock_accept_client;
extern std::string mock_ethernet_input;
extern std::string mock_ethernet_output;
extern std::atomic<int> mock_write_capacity;
class EthernetClient : public Stream {
  int socket_index = -1;
public:
  EthernetClient() = default;
  explicit EthernetClient(uint8_t index) : socket_index(index) {}
  explicit operator bool() const { return socket_index >= 0 && socket_index < 4; }
  uint8_t getSocketNumber() const { return socket_index < 0 ? 255 : socket_index; }
  bool connected() const {
    mock_bus_touch();
    return socket_index >= 0 && mock_socket_connected[socket_index].load();
  }
  void setConnectionTimeout(uint16_t milliseconds) { assert(milliseconds == 50); }
  void stop() {
    assert(false && "vendor blocking socket stop must not run");
  }
  int available() override { mock_bus_touch(); return mock_ethernet_input.size(); }
  int read() override {
    mock_bus_touch();
    if (mock_ethernet_input.empty()) return -1;
    const uint8_t next = mock_ethernet_input.front();
    mock_ethernet_input.erase(0, 1);
    return next;
  }
  int peek() override {
    mock_bus_touch();
    return mock_ethernet_input.empty() ? -1 : static_cast<uint8_t>(mock_ethernet_input.front());
  }
  int availableForWrite() override { assert(false && "vendor free-size spin must not run"); return 0; }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* data, size_t size) override {
    (void)data; (void)size;
    assert(false && "vendor blocking socketSend must not run");
    return 0;
  }
  void flush() override { assert(false && "raw blocking flush must not be called"); }
  using Print::write;
};
class EthernetServer {
public:
  explicit EthernetServer(int port) { assert(port == 23); }
  void begin() { mock_worker_bus_touch(); }
  EthernetClient accept() {
    mock_bus_touch();
    if (!mock_accept_client.exchange(false)) return {};
    mock_socket_connected[0].store(true);
    return EthernetClient(uint8_t{0});
  }
};
enum { EthernetNoHardware = 0, EthernetPresent = 1, LinkOFF = 0, LinkON = 1 };
class MockEthernet {
public:
  std::atomic<int> begin_result{1};
  std::atomic<int> hardware{EthernetPresent};
  std::atomic<int> maintain_result{0};
  std::atomic<uint32_t> ip{0x2a01a8c0};
  std::atomic<int> begin_count{0};
  std::atomic<int> maintain_count{0};
  std::atomic<bool> begin_blocked{false};
  std::atomic<bool> maintain_blocked{false};
  void init(SPIClass&, int chip_select);
  int begin(uint8_t* mac, int timeout, int response_timeout);
  int hardwareStatus() const { mock_worker_bus_touch(); return hardware.load(); }
  IPAddress localIP() const { mock_worker_bus_touch(); return IPAddress(ip.load()); }
  int maintain();
};
extern MockEthernet Ethernet;
