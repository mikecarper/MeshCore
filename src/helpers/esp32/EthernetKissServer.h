#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <errno.h>
#include <lwip/sockets.h>
#include <helpers/EthernetKiss.h>

namespace mesh { namespace ethernet {

// Opt-in, unauthenticated KISS-over-TCP on a trusted station LAN only. This
// service never joins WiFi, opens an AP, selects an RF profile, or adds bytes
// to the Ethernet frame. The owner must explicitly enable and service RF.
class KissServer {
public:
  // Full Companion already uses 5000/5001/5002 for binary, OTA and text.
  static constexpr uint16_t Port = 5003;
  static constexpr size_t ReadBudget = 128;
  static constexpr size_t WriteBudget = 128;
  struct Status {
    bool enabled, listening, connected;
    uint32_t accepted, rejected, disconnects, rxFrames, txFrames;
    uint32_t malformed, dropped, listenFailures;
    uint8_t pendingTransmit, pendingReceive;
  };

private:
  WiFiServer _server;
  WiFiClient _client;
  KissCodec _codec;
  uint32_t _address = 0, _lastListenAttempt = 0;
  bool _enabled = false, _listening = false, _connected = false;
  bool _listenAttempted = false;
  uint32_t _accepted = 0, _rejected = 0, _disconnects = 0, _listenFailures = 0;

  void disconnect() {
    if (_connected) ++_disconnects;
    _connected = false;
    _client.stop();
    _codec.resetPending();
  }

  void stop() {
    disconnect();
    _server.end();
    _listening = _listenAttempted = false;
    _address = 0;
  }

public:
  KissServer() : _server(Port, 2) {}

  // Closing the stream AND pending accepts discards unread SDK/socket bytes.
  // Merely clearing the decoder would let pre-change work survive in a socket.
  // The next enabled poll immediately rebinds to the same station address.
  void resetPending() {
    disconnect();
    _server.end();
    _listening = _listenAttempted = false;
  }

  void loop(bool enabled) {
    _enabled = enabled;
    const bool station = (WiFi.getMode() & WIFI_STA)
        && WiFi.status() == WL_CONNECTED;
    const uint32_t address = station ? static_cast<uint32_t>(WiFi.localIP()) : 0;
    if (!enabled || !address) {
      if (_address || _listening || _connected) stop();
      return;
    }
    if (_address != address) {
      stop();
      _address = address;
      // Binding the station address prevents this unauthenticated service from
      // appearing on a simultaneously enabled setup AP.
      _server = WiFiServer(IPAddress(address), Port, 2);
    }
    const uint32_t now = static_cast<uint32_t>(millis());
    if (_listening && !static_cast<bool>(_server)) {
      _server.end();
      _listening = _listenAttempted = false;
    }
    if (!_listening && (!_listenAttempted
        || static_cast<uint32_t>(now - _lastListenAttempt) >= 1000U)) {
      _lastListenAttempt = now;
      _listenAttempted = true;
      _server.begin();
      _listening = static_cast<bool>(_server);
      if (!_listening) { ++_listenFailures; _server.end(); }
    }
    if (_connected && !_client.connected()) disconnect();
    if (_listening) {
      // Accept at most one queued connection per poll. An idle valid owner is
      // never displaced by another host or by that host reconnecting early.
      WiFiClient incoming = _server.available();
      if (incoming) {
        if (_connected) { incoming.stop(); ++_rejected; }
        else {
          _codec.resetPending();
          _client = incoming;
          _client.setNoDelay(true);
          _connected = true;
          ++_accepted;
        }
      }
    }
    if (!_connected) return;
    // WiFiClient::write may retry blocking select calls for seconds. One
    // MSG_DONTWAIT send, with a fixed byte budget, leaves LoRa scanning runnable.
    const uint8_t* bytes;
    size_t length;
    if (_codec.peekOutput(bytes, length)) {
      if (length > WriteBudget) length = WriteBudget;
      const int written = ::send(_client.fd(), bytes, length, MSG_DONTWAIT);
      if (written > 0) _codec.consumeOutput(static_cast<size_t>(written));
      else if (written < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
        disconnect();
        return;
      }
    }
    for (size_t consumed = 0; consumed < ReadBudget && _codec.canAcceptByte(); ++consumed) {
      uint8_t byte;
      const int received = ::recv(_client.fd(), &byte, 1, MSG_DONTWAIT);
      if (received == 1) _codec.feed(byte);
      else {
        if (received == 0 || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR))
          disconnect();
        break;
      }
    }
  }

  bool peekTransmit(const uint8_t*& bytes, size_t& length) const {
    return _codec.peekTransmit(bytes, length);
  }
  void consumeTransmit() { _codec.consumeTransmit(); }
  bool queueReceive(const uint8_t* bytes, size_t length) {
    return _connected && _codec.queueReceive(bytes, length);
  }
  Status status() const {
    return {_enabled, _listening, _connected, _accepted, _rejected, _disconnects,
        _codec.receivedFrames(), _codec.sentFrames(), _codec.malformedFrames(),
        _codec.droppedFrames(), _listenFailures,
        _codec.pendingTransmit(), _codec.pendingReceive()};
  }
};

} }  // namespace mesh::ethernet
