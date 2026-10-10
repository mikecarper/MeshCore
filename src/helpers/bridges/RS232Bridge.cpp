#include "RS232Bridge.h"
#include "RS232UartUtils.h"

#include <HardwareSerial.h>

#ifdef WITH_RS232_BRIDGE

#ifdef ESP32
// check_firmware_ram.py reserves this object plus the UART driver's runtime
// allocation even when a combined Full image starts with the UART disabled.
static_assert(sizeof(RS232Bridge) <= 2304, "Update the ESP32 UART heap budget");
#endif
#if defined(NRF52_PLATFORM) && defined(RAK4631_COMBINED_ETHERNET)
// The combined RAK RAM policy reserves this object plus allocator metadata
// and the UART driver's TX semaphore. Its RX/TX arrays are linked globals.
static_assert(sizeof(RS232Bridge) <= 2304, "Update the combined RAK UART heap budget");
#endif

RS232Bridge::RS232Bridge(NodePrefs *prefs, Stream &serial, int16_t rx_pin,
                         int16_t tx_pin, mesh::PacketManager *mgr,
                         mesh::RTCClock *rtc)
    : BridgeBase(prefs, mgr, rtc), _serial(&serial), _rx_pin(rx_pin),
      _tx_pin(tx_pin) {}

void RS232Bridge::begin() {
  _initialized = false;
  _rx_buffer_pos = 0;
  _rx_packet_ready = false;
  BRIDGE_DEBUG_PRINTLN("Initializing at %d baud...\n", _prefs->bridge_baud);
#if defined(ESP32)
  if (!((HardwareSerial *)_serial)->setPins(_rx_pin, _tx_pin)) return;
#elif defined(NRF52_PLATFORM)
  // Tested with RAK_4631 and T114
  // The Adafruit Uart object may already be active on its variant defaults.
  // Stop it before changing pins or the EasyDMA instance can retain the old
  // pin selection and silently receive nothing.
  mesh::bridge::prepareNrfUart(*((Uart *)_serial), _rx_pin, _tx_pin);
#elif defined(RP2040_PLATFORM)
  ((SerialUART *)_serial)->setRX(_rx_pin);
  ((SerialUART *)_serial)->setTX(_tx_pin);
#elif defined(STM32_PLATFORM)
  ((HardwareSerial *)_serial)->setRx(_rx_pin);
  ((HardwareSerial *)_serial)->setTx(_tx_pin);
#else
#error RS232Bridge was not tested on the current platform
#endif
  ((HardwareSerial *)_serial)->begin(_prefs->bridge_baud);

  // Update bridge state
#if defined(ESP32)
  _initialized = static_cast<bool>(*static_cast<HardwareSerial *>(_serial));
#else
  _initialized = true;
#endif
}

void RS232Bridge::end() {
  BRIDGE_DEBUG_PRINTLN("Stopping...\n");
  ((HardwareSerial *)_serial)->end();

  // Update bridge state
  _initialized = false;
  _rx_buffer_pos = 0;
  _rx_packet_ready = false;
}

void RS232Bridge::drainReceivedPackets() {
  if (!_rx_packet_ready) return;
  mesh::Packet* pkt = _mgr->allocNew();
  if (!pkt) return;
  const uint16_t len = (_rx_buffer[2] << 8) | _rx_buffer[3];
  const bool valid = pkt->readFrom(_rx_buffer + 4, len);
  _rx_buffer_pos = 0;
  _rx_packet_ready = false;
  if (valid) onPacketReceived(pkt);
  else _mgr->free(pkt);
}

void RS232Bridge::loop() {
  // Guard against uninitialized state
  if (_initialized == false) {
    return;
  }

  drainReceivedPackets();
  // Snapshot bounded work; a noisy UART must not starve mesh/radio service.
  int remaining = _serial->available();
  if (remaining > 512) remaining = 512;
  while (remaining-- > 0 && !_rx_packet_ready) {
    const int value = _serial->read();
    if (value < 0) break;
    const uint8_t b = static_cast<uint8_t>(value);

    if (_rx_buffer_pos < 2) {
      // Waiting for magic word
      if ((_rx_buffer_pos == 0 && b == ((BRIDGE_PACKET_MAGIC >> 8) & 0xFF)) ||
          (_rx_buffer_pos == 1 && b == (BRIDGE_PACKET_MAGIC & 0xFF))) {
        _rx_buffer[_rx_buffer_pos++] = b;
      } else {
        // Invalid magic byte, reset and start over
        _rx_buffer_pos = 0;
        // Check if this byte could be the start of a new magic word
        if (b == ((BRIDGE_PACKET_MAGIC >> 8) & 0xFF)) {
          _rx_buffer[_rx_buffer_pos++] = b;
        }
      }
    } else {
      // Reading length, payload, and checksum
      _rx_buffer[_rx_buffer_pos++] = b;

      if (_rx_buffer_pos >= 4) {
        uint16_t len = (_rx_buffer[2] << 8) | _rx_buffer[3];

        // Validate length field
        if (len == 0 || len > (MAX_TRANS_UNIT + 1)) {
          BRIDGE_DEBUG_PRINTLN("RX invalid length %d, resetting\n", len);
          _rx_buffer_pos = 0; // Invalid length, reset
          continue;
        }

        if (_rx_buffer_pos == len + SERIAL_OVERHEAD) { // Full packet received
          uint16_t received_checksum = (_rx_buffer[4 + len] << 8) | _rx_buffer[5 + len];

          if (validateChecksum(_rx_buffer + 4, len, received_checksum)) {
            BRIDGE_DEBUG_PRINTLN("RX, len=%d crc=0x%04x\n", len, received_checksum);
            _rx_packet_ready = true;
            drainReceivedPackets(); // continue this bounded batch when the pool is available
          } else {
            BRIDGE_DEBUG_PRINTLN("RX checksum mismatch, rcv=0x%04x\n", received_checksum);
          }
          if (!_rx_packet_ready) _rx_buffer_pos = 0; // Keep a complete valid frame
        }
      }
    }
  }
  drainReceivedPackets();
}

void RS232Bridge::sendPacket(mesh::Packet *packet) {
  // Guard against uninitialized state
  if (_initialized == false) {
    return;
  }

  // First validate the packet pointer
  if (!packet) {
    BRIDGE_DEBUG_PRINTLN("TX invalid packet pointer\n");
    return;
  }

  if (!allowsPacket(packet)) return;

  if (!_seen_packets.wasSeen(packet)) {
    const int expected = packet->getRawLength();
    if (expected <= 0 || expected > MAX_TRANS_UNIT + 1) return;
    uint8_t buffer[MAX_SERIAL_PACKET_SIZE];
    uint16_t len = packet->writeTo(buffer + 4);

    // Check if packet fits within our maximum payload size
    if (len > (MAX_TRANS_UNIT + 1)) {
      BRIDGE_DEBUG_PRINTLN("TX packet too large (payload=%d, max=%d)\n", len, MAX_TRANS_UNIT + 1);
      return;
    }

    // Build packet header
    buffer[0] = (BRIDGE_PACKET_MAGIC >> 8) & 0xFF; // Magic high byte
    buffer[1] = BRIDGE_PACKET_MAGIC & 0xFF;        // Magic low byte
    buffer[2] = (len >> 8) & 0xFF;                 // Length high byte
    buffer[3] = len & 0xFF;                        // Length low byte

    // Calculate checksum over the payload
    uint16_t checksum = fletcher16(buffer + 4, len);
    buffer[4 + len] = (checksum >> 8) & 0xFF; // Checksum high byte
    buffer[5 + len] = checksum & 0xFF;        // Checksum low byte

    // Send complete packet
    if (_serial->write(buffer, len + SERIAL_OVERHEAD) == static_cast<size_t>(len + SERIAL_OVERHEAD)) {
      _seen_packets.markSeen(packet);
    }

    BRIDGE_DEBUG_PRINTLN("TX, len=%d crc=0x%04x\n", len, checksum);
  }
}

void RS232Bridge::onPacketReceived(mesh::Packet *packet) {
  handleReceivedPacket(packet);
}

#endif
