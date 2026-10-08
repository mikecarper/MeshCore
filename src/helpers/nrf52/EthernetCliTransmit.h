#pragma once

#include <RAK13800_W5100S.h>
#include <w5100.h>
#include <stddef.h>
#include <stdint.h>

namespace mesh {
namespace nrf52 {

#ifndef RAK4631_ETHERNET_SEND_TIMEOUT_MS
  #define RAK4631_ETHERNET_SEND_TIMEOUT_MS 250u
#endif
static_assert(RAK4631_ETHERNET_SEND_TIMEOUT_MS > 0u &&
              RAK4631_ETHERNET_SEND_TIMEOUT_MS < 0x80000000u,
              "Ethernet send deadline must support wrap-safe elapsed time");

// The vendor socketSend()/execCmdSn() wait for ACK/command completion without
// a software deadline. Queue one SEND directly in the controller, then inspect
// its completion on later worker turns. Every method has a finite number of
// register transfers and never owns heap storage or waits for network traffic.
class EthernetCliTransmit {
  static constexpr uint8_t NO_SOCKET = 0xFFu;
  static constexpr uint8_t SOCKET_COUNT = 4u; // RAK13800's exact W5100S
  static constexpr uint8_t FREE_SIZE_READS = 4u;
  uint8_t _socket = NO_SOCKET;
  bool _pending = false;
  bool _failed = false;
  bool _waiting_command = false;
  uint32_t _started = 0;

  class Transaction {
    SPIClass* _spi;
  public:
    Transaction() : _spi(W5100.getSPI()) {
      if (_spi) _spi->beginTransaction(SPI_ETHERNET_SETTINGS);
    }
    ~Transaction() { if (_spi) _spi->endTransaction(); }
    explicit operator bool() const { return _spi != nullptr; }
  };

  static bool usableStatus(uint8_t status) {
    return status == SnSR::ESTABLISHED || status == SnSR::CLOSE_WAIT;
  }

  static bool socketValid(uint8_t socket) {
    return socket < SOCKET_COUNT && W5100.getChip() == 51u;
  }

  static bool receiveSize(uint8_t socket, uint16_t& size) {
    uint16_t previous = W5100.readSnRX_RSR(socket);
    for (uint8_t count = 1u; count < FREE_SIZE_READS; ++count) {
      const uint16_t current = W5100.readSnRX_RSR(socket);
      if (current == previous) {
        size = current;
        return current <= W5100.SSIZE;
      }
      previous = current;
    }
    return false;
  }

  static int receiveByte(uint8_t socket, bool consume) {
    if (!socketValid(socket)) return -1;
    Transaction transaction;
    if (!transaction || !usableStatus(W5100.readSnSR(socket)) ||
        W5100.readSnCR(socket) != 0u) return -1;
    uint16_t size = 0u;
    if (!receiveSize(socket, size) || !size) return -1;
    const uint16_t pointer = W5100.readSnRX_RD(socket);
    uint8_t value = 0u;
    if (W5100.read(static_cast<uint16_t>(W5100.RBASE(socket) + (pointer & W5100.SMASK)),
                   &value, 1u) != 1u) return -1;
    if (consume) {
      W5100.writeSnRX_RD(socket, static_cast<uint16_t>(pointer + 1u));
      W5100.writeSnCR(socket, Sock_RECV);
    }
    return value;
  }

  bool expired(uint32_t now) const {
    return static_cast<uint32_t>(now - _started) >= RAK4631_ETHERNET_SEND_TIMEOUT_MS;
  }

  void fail() {
    _failed = true;
    _pending = false;
    _waiting_command = false;
  }

public:
  bool pending() const { return _pending || _waiting_command; }
  bool failed() const { return _failed; }

  void reset() {
    _socket = NO_SOCKET;
    _pending = _failed = _waiting_command = false;
    _started = 0;
  }

  void poll(uint32_t now) {
    if (_failed || (!_pending && !_waiting_command)) return;
    Transaction transaction;
    if (!transaction) { fail(); return; }
    if (!usableStatus(W5100.readSnSR(_socket))) { fail(); return; }
    const uint8_t interrupt = W5100.readSnIR(_socket);
    if (interrupt & SnIR::TIMEOUT) {
      W5100.writeSnIR(_socket, SnIR::TIMEOUT | SnIR::SEND_OK);
      fail();
      return;
    }
    const uint8_t command = W5100.readSnCR(_socket);
    if (_pending && (interrupt & SnIR::SEND_OK) && command == 0u) {
      W5100.writeSnIR(_socket, SnIR::SEND_OK);
      _pending = false;
      return;
    }
    if (_waiting_command && command == 0u) {
      _waiting_command = false;
      return;
    }
    if (expired(now)) fail();
  }

  size_t write(uint8_t socket, const uint8_t* data, size_t length, uint32_t now) {
    if (!data || !length || _failed) return 0u;
    if (socket >= SOCKET_COUNT || W5100.getChip() != 51u) { fail(); return 0u; }
    if (_socket != NO_SOCKET && _socket != socket) { fail(); return 0u; }
    _socket = socket;
    poll(now);
    if (_failed || _pending || _waiting_command) return 0u;
    Transaction transaction;
    if (!transaction) { fail(); return 0u; }
    if (!usableStatus(W5100.readSnSR(socket))) { fail(); return 0u; }
    if (W5100.readSnCR(socket) != 0u) {
      _waiting_command = true;
      _started = now;
      return 0u;
    }
    if (!W5100.SSIZE || W5100.SMASK != W5100.SSIZE - 1u) { fail(); return 0u; }

    // A 16-bit free-size register can change between byte reads. The vendor
    // retries forever for a matching pair; retain backpressure after at most
    // four reads and try again on a later worker turn instead.
    uint16_t previous = W5100.readSnTX_FSR(socket);
    uint16_t available = 0u;
    bool stable = false;
    for (uint8_t count = 1u; count < FREE_SIZE_READS; ++count) {
      const uint16_t current = W5100.readSnTX_FSR(socket);
      if (current == previous) { available = current; stable = true; break; }
      previous = current;
    }
    if (!stable || !available) return 0u;
    if (available > W5100.SSIZE) { fail(); return 0u; }
    if (length > available) length = available;
    const uint16_t count = static_cast<uint16_t>(length);
    const uint16_t pointer = W5100.readSnTX_WR(socket);
    const uint16_t offset = pointer & W5100.SMASK;
    const uint16_t first = count <= W5100.SSIZE - offset
        ? count : static_cast<uint16_t>(W5100.SSIZE - offset);
    if (W5100.write(static_cast<uint16_t>(W5100.SBASE(socket) + offset), data, first) != first) {
      fail();
      return 0u;
    }
    if (first < count && W5100.write(W5100.SBASE(socket), data + first,
                                     static_cast<uint16_t>(count - first)) != count - first) {
      fail();
      return 0u;
    }
    W5100.writeSnTX_WR(socket, static_cast<uint16_t>(pointer + count));
    W5100.writeSnIR(socket, SnIR::SEND_OK | SnIR::TIMEOUT); // revoke stale completion
    W5100.writeSnCR(socket, Sock_SEND); // deliberately do not call execCmdSn()
    _started = now;
    _pending = true;
    return count;
  }

  static bool close(uint8_t socket) {
    if (socket >= SOCKET_COUNT || W5100.getChip() != 51u) return false;
    Transaction transaction;
    if (!transaction || W5100.readSnCR(socket) != 0u) return false;
    W5100.writeSnCR(socket, Sock_CLOSE);
    return true;
  }

  static bool commandReady(uint8_t socket) {
    if (!socketValid(socket)) return false;
    Transaction transaction;
    return transaction && W5100.readSnCR(socket) == 0u;
  }

  static int available(uint8_t socket) {
    if (!socketValid(socket)) return 0;
    Transaction transaction;
    if (!transaction || !usableStatus(W5100.readSnSR(socket)) ||
        W5100.readSnCR(socket) != 0u) return 0;
    uint16_t size = 0u;
    return receiveSize(socket, size) ? size : 0;
  }

  static int peek(uint8_t socket) { return receiveByte(socket, false); }
  static int read(uint8_t socket) { return receiveByte(socket, true); }

  static bool connected(uint8_t socket) {
    if (!socketValid(socket)) return false;
    Transaction transaction;
    if (!transaction) return false;
    const uint8_t status = W5100.readSnSR(socket);
    if (status == SnSR::ESTABLISHED) return true;
    if (status != SnSR::CLOSE_WAIT) return false;
    uint16_t size = 0u;
    // An unstable size snapshot is backpressure, not proof of EOF. Retain
    // the client until a stable zero proves its final bytes were consumed.
    return !receiveSize(socket, size) || size != 0u;
  }
};

} // namespace nrf52
} // namespace mesh
