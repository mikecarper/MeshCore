#pragma once
#include <Arduino.h>
#include <SPI.h>
#include <array>

#define SPI_ETHERNET_SETTINGS SPISettings{}
enum SockCMD { Sock_CLOSE = 0x10, Sock_SEND = 0x20, Sock_RECV = 0x40 };
struct SnSR { enum { CLOSED = 0, ESTABLISHED = 0x17, CLOSE_WAIT = 0x1c }; };
struct SnIR { enum { SEND_OK = 0x10, TIMEOUT = 8 }; };
extern SPIClass* mock_w5100_spi;
extern std::atomic<bool> mock_ack_send;
extern std::atomic<bool> mock_stuck_command;
extern std::atomic<bool> mock_hold_terminal_recv;
extern std::atomic<bool> mock_replace_during_parser;
void mock_replace_while_parser_active();
extern std::mutex mock_output_mutex;
class MockW5100 {
public:
  static constexpr uint16_t SSIZE = 2048;
  static constexpr uint16_t SMASK = SSIZE - 1;
  std::array<std::array<uint8_t, SSIZE>, 4> memory{};
  uint16_t tx_wr[4] = {}, committed[4] = {};
  uint16_t rx_rd[4] = {};
  uint8_t interrupt[4] = {};
  mutable uint8_t command[4] = {};
  uint8_t getChip() const { return 51; }
  SPIClass* getSPI() const { return mock_w5100_spi; }
  uint16_t SBASE(uint8_t socket) const { return 0x4000 + socket * SSIZE; }
  uint16_t RBASE(uint8_t socket) const { return 0x6000 + socket * SSIZE; }
  uint8_t readSnSR(uint8_t socket) const {
    mock_bus_touch();
    return mock_socket_connected[socket].load() ? SnSR::ESTABLISHED : SnSR::CLOSED;
  }
  uint8_t readSnIR(uint8_t socket) const { mock_bus_touch(); return interrupt[socket]; }
  uint8_t readSnCR(uint8_t socket) const {
    mock_bus_touch();
    if (command[socket] == Sock_RECV && !mock_stuck_command.load() && !mock_hold_terminal_recv.load()) {
      command[socket] = 0;
    }
    return command[socket];
  }
  uint16_t readSnTX_FSR(uint8_t) const { mock_bus_touch(); return mock_write_capacity.load(); }
  uint16_t readSnTX_WR(uint8_t socket) const { mock_bus_touch(); return tx_wr[socket]; }
  uint16_t readSnRX_RSR(uint8_t) const { mock_bus_touch(); return mock_ethernet_input.size(); }
  uint16_t readSnRX_RD(uint8_t socket) const { mock_bus_touch(); return rx_rd[socket]; }
  void writeSnRX_RD(uint8_t socket, uint16_t value) { mock_bus_touch(); rx_rd[socket] = value; }
  void writeSnTX_WR(uint8_t socket, uint16_t value) { mock_bus_touch(); tx_wr[socket] = value; }
  void writeSnIR(uint8_t socket, uint8_t bits) { mock_bus_touch(); interrupt[socket] &= ~bits; }
  uint16_t write(uint16_t address, const uint8_t* data, uint16_t count) {
    mock_bus_touch();
    const uint16_t relative = address - 0x4000;
    const uint8_t socket = relative / SSIZE;
    const uint16_t offset = relative % SSIZE;
    assert(socket < 4 && offset + count <= SSIZE);
    memcpy(memory[socket].data() + offset, data, count);
    return count;
  }
  uint16_t read(uint16_t address, uint8_t* data, uint16_t count) {
    mock_bus_touch();
    assert(address >= 0x6000 && count == 1 && !mock_ethernet_input.empty());
    data[0] = static_cast<uint8_t>(mock_ethernet_input.front());
    return 1;
  }
  void writeSnCR(uint8_t socket, uint8_t value) {
    mock_bus_touch();
    assert(command[socket] == 0 && "commands must never overwrite a stalled controller command");
    if (value == Sock_RECV) {
      assert(!mock_ethernet_input.empty());
      if (mock_replace_during_parser.exchange(false)) mock_replace_while_parser_active();
      const char consumed = mock_ethernet_input.front();
      mock_ethernet_input.erase(0, 1);
      command[socket] = mock_stuck_command.load() || (consumed == '\r' && mock_hold_terminal_recv.load()) ? Sock_RECV : 0;
      return;
    }
    if (value == Sock_CLOSE) {
      ++mock_stop_count[socket];
      mock_socket_connected[socket].store(false);
      return;
    }
    assert(value == Sock_SEND);
    {
      std::lock_guard<std::mutex> lock(mock_output_mutex);
      for (uint16_t pointer = committed[socket]; pointer != tx_wr[socket]; ++pointer) {
        mock_ethernet_output.push_back(memory[socket][pointer & SMASK]);
      }
    }
    committed[socket] = tx_wr[socket];
    command[socket] = mock_stuck_command.load() ? Sock_SEND : 0;
    interrupt[socket] = mock_ack_send.load() ? SnIR::SEND_OK : 0;
  }
};
extern MockW5100 W5100;
