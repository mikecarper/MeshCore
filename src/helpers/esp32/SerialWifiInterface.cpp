#include "SerialWifiInterface.h"
#include "../CompanionFrameQueue.h"
#include <WiFi.h>

static constexpr uint32_t WIFI_FRAME_TIMEOUT_MS = 5000;

void SerialWifiInterface::clearBuffers() {
  held_queue_len = send_queue_len = 0;
  send_offset = 0;
  queue_has_ip = false;
  held_since = 0;
  memset(held_queue, 0, sizeof(held_queue));
  memset(send_queue, 0, sizeof(send_queue));
}

void SerialWifiInterface::expireHeldQueue(uint32_t now) {
  if (held_queue_len > 0 && (uint32_t)(now - held_since) >= 30000U) {
    held_queue_len = 0;
    memset(held_queue, 0, sizeof(held_queue));
  }
}

void SerialWifiInterface::loop() {
  const uint32_t now = (uint32_t)millis();
  expireHeldQueue(now);
  if (deviceConnected && (!client.connected()
      || (receive_pending && (uint32_t)(now - receive_started) >= WIFI_FRAME_TIMEOUT_MS))) {
    disconnectClient(true);
  }
}

void SerialWifiInterface::disconnectClient(bool cancel_session) {
  const bool was_connected = deviceConnected;
  deviceConnected = false;
  if (client) client.stop();
  resetReceivedFrameHeader();
  send_offset = 0; // A reconnect must replay the whole frame, never its suffix.
  if (cancel_session && was_connected && session_changed != nullptr) {
    session_changed(session_context);
  }
}

void SerialWifiInterface::selectClient(const IPAddress& ip, uint32_t now) {
  expireHeldQueue(now);
  // TCP framing always starts afresh on a new connection, even for the same
  // IP. An incomplete outgoing frame is retained whole, not just its suffix.
  send_offset = 0;
  if (queue_has_ip && queue_ip != ip) {
    if (held_queue_len > 0 && held_ip == ip) {
      // The previous owner returned: restore its queue and park the displaced
      // active queue in the same fixed storage. Only one old IP is retained.
      for (int i = 0; i < FRAME_QUEUE_SIZE; ++i) {
        Frame swap = send_queue[i];
        send_queue[i] = held_queue[i];
        held_queue[i] = swap;
      }
      const int restored_len = held_queue_len;
      held_queue_len = send_queue_len;
      send_queue_len = restored_len;
      held_ip = queue_ip;
      held_since = now;
    } else if (send_queue_len > 0) {
      // Bounded fallback: a third owner's backlog replaces the older parked
      // queue instead of allocating RAM or delaying the new connection.
      memcpy(held_queue, send_queue, sizeof(held_queue));
      held_queue_len = send_queue_len;
      held_ip = queue_ip;
      held_since = now;
      send_queue_len = 0;
      memset(send_queue, 0, sizeof(send_queue));
    }
  }
  queue_ip = ip;
  queue_has_ip = true;
}

void SerialWifiInterface::begin(int port) {
  // wifi setup is handled outside of this class, only starts the server
  server.begin(port);
}

void SerialWifiInterface::end() {
  disable();
  if (client) client.stop();
  server.end();
  deviceConnected = false;
  resetReceivedFrameHeader();
  clearBuffers();
}

// ---------- public methods
void SerialWifiInterface::enable() { 
  if (_isEnabled) return;

  _isEnabled = true;
  clearBuffers();
  resetReceivedFrameHeader();
}

void SerialWifiInterface::disable() {
  _isEnabled = false;
  disconnectClient(true);
  clearBuffers();
}

size_t SerialWifiInterface::writeFrame(const uint8_t src[], size_t len) {
  if (len > MAX_FRAME_SIZE) {
    WIFI_DEBUG_PRINTLN("writeFrame(), frame too big, len=%d\n", len);
    return 0;
  }

  if (_isEnabled && deviceConnected && src != nullptr && len > 0) {
    // Once any bytes of the front frame have reached TCP, priority insertion
    // must not move or evict it. New frames may reorder only the unsent tail.
    const int pinned = send_offset != 0 ? 1 : 0;
    int tail_len = send_queue_len - pinned;
    if (!mesh::enqueueCompanionFrame(send_queue + pinned, tail_len,
                                     FRAME_QUEUE_SIZE - pinned, src, len)) {
      WIFI_DEBUG_PRINTLN("writeFrame(), send_queue is full!");
      return 0;
    }
    send_queue_len = tail_len + pinned;
    return len;
  }
  return 0;
}

bool SerialWifiInterface::isReadBusy() const {
  return false;
}

bool SerialWifiInterface::isWriteBusy() const {
  return send_queue_len >= FRAME_QUEUE_SIZE;
}

bool SerialWifiInterface::hasReceivedFrameHeader() {
  return received_frame_header.type != 0 && received_frame_header.length != 0;
}

void SerialWifiInterface::resetReceivedFrameHeader() {
  received_frame_header.type = 0;
  received_frame_header.length = 0;
  receive_pending = false;
  receive_started = 0;
}

size_t SerialWifiInterface::checkRecvFrame(uint8_t dest[]) {
  loop();
  if (!_isEnabled) return 0;
  // check if new client connected
  auto newClient = server.available();
  if (newClient) {
    const IPAddress new_ip = newClient.remoteIP();

    // disconnect existing client
    disconnectClient(!queue_has_ip || queue_ip != new_ip);

    // Partition queues and cancel the displaced session before exposing the
    // new connection to response producers or consuming its first command.
    selectClient(new_ip, (uint32_t)millis());

    // switch active connection to new client
    client = newClient;

    // forget received frame header
    resetReceivedFrameHeader();
    
  }

  if (client.connected()) {
    if (!deviceConnected) {
      WIFI_DEBUG_PRINTLN("Got connection");
      deviceConnected = true;
    }
  } else {
    if (deviceConnected) {
      disconnectClient(true);
      WIFI_DEBUG_PRINTLN("Disconnected");
    }
  }

  if (deviceConnected) {
    if (send_queue_len > 0) {   // first, check send queue
      
      _last_write = millis();
      int len = send_queue[0].len;

      uint8_t pkt[3 + MAX_FRAME_SIZE]; // serial-compatible framing
      pkt[0] = '>';
      pkt[1] = (len & 0xFF);  // LSB
      pkt[2] = (len >> 8);    // MSB
      memcpy(&pkt[3], send_queue[0].buf, send_queue[0].len);
      const size_t total = 3U + len;
      const size_t remaining = total - send_offset;
      const size_t written = client.write(pkt + send_offset, remaining);
      send_offset += written < remaining ? written : remaining;
      if (send_offset < total) return 0;
      send_offset = 0;
      send_queue_len--;
      for (int i = 0; i < send_queue_len; i++) {   // delete top item from queue
        send_queue[i] = send_queue[i + 1];
      }
      memset(&send_queue[send_queue_len], 0, sizeof(send_queue[send_queue_len]));
    } else {

      if (!receive_pending && client.available() > 0) {
        receive_pending = true;
        receive_started = (uint32_t)millis();
      }
      if (!hasReceivedFrameHeader()) {
        if (client.available() < 3) return 0;
        uint8_t header[3];
        if (client.readBytes(header, sizeof(header)) != sizeof(header)) {
          disconnectClient(true);
          return 0;
        }
        received_frame_header.type = header[0];
        received_frame_header.length = uint16_t(header[1]) | (uint16_t(header[2]) << 8);
        // Validate before waiting for any payload. Closing an invalid stream
        // avoids unbounded discard loops and interpreting its tail as commands.
        if (received_frame_header.type != '<' || received_frame_header.length == 0
            || received_frame_header.length > MAX_FRAME_SIZE) {
          disconnectClient(true);
          return 0;
        }
      }
      const size_t frame_length = received_frame_header.length;
      if (client.available() < (int)frame_length) return 0;
      if (dest == nullptr || client.readBytes(dest, frame_length) != frame_length) {
        disconnectClient(true);
        return 0; // Never dispatch a truncated command, even after a short read.
      }
      resetReceivedFrameHeader();
      return frame_length;
      
    }
  }

  return 0;
}

bool SerialWifiInterface::isConnected() const {
  return deviceConnected;  //pServer != NULL && pServer->getConnectedCount() > 0;
}
