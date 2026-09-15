#include "SerialWifiInterface.h"
#include "../CompanionFrameQueue.h"
#include <WiFi.h>

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
  expireHeldQueue((uint32_t)millis());
}

void SerialWifiInterface::selectClient(const IPAddress& ip, uint32_t now) {
  expireHeldQueue(now);
  // TCP framing always starts afresh on a new connection, even for the same
  // IP. An incomplete outgoing frame is retained whole, not just its suffix.
  send_offset = 0;
  if (queue_has_ip && queue_ip != ip) {
    if (session_changed != nullptr) session_changed(session_context);

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
  deviceConnected = false;
  if (client) client.stop();
  if (queue_has_ip && session_changed != nullptr) session_changed(session_context);
  resetReceivedFrameHeader();
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
}

size_t SerialWifiInterface::checkRecvFrame(uint8_t dest[]) {
  expireHeldQueue((uint32_t)millis());
  if (!_isEnabled) return 0;
  // check if new client connected
  auto newClient = server.available();
  if (newClient) {
    const IPAddress new_ip = newClient.remoteIP();

    // disconnect existing client
    deviceConnected = false;
    client.stop();

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
      deviceConnected = false;
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

      // check if we are waiting for a frame header
      if(!hasReceivedFrameHeader()){

        // make sure we have received enough bytes for a frame header
        // 3 bytes frame header = (1 byte frame type) + (2 bytes frame length as unsigned 16-bit little endian)
        int frame_header_length = 3;
        if(client.available() >= frame_header_length){

          // read frame header
          client.readBytes(&received_frame_header.type, 1);
          client.readBytes((uint8_t*)&received_frame_header.length, 2);

        }

      }

      // check if we have received a frame header
      if(hasReceivedFrameHeader()){

        // make sure we have received enough bytes for the required frame length
        int available = client.available();
        int frame_type = received_frame_header.type;
        int frame_length = received_frame_header.length;
        if(frame_length > available){
          WIFI_DEBUG_PRINTLN("Waiting for %d more bytes", frame_length - available);
          return 0;
        }

        // skip frames that are larger than MAX_FRAME_SIZE
        if(frame_length > MAX_FRAME_SIZE){
          WIFI_DEBUG_PRINTLN("Skipping frame: length=%d is larger than MAX_FRAME_SIZE=%d", frame_length, MAX_FRAME_SIZE);
          while(frame_length > 0){
            uint8_t skip[1];
            int skipped = client.read(skip, 1);
            frame_length -= skipped;
          }
          resetReceivedFrameHeader();
          return 0;
        }

        // skip frames that are not expected type
        // '<' is 0x3c which indicates a frame sent from app to radio
        if(frame_type != '<'){
          WIFI_DEBUG_PRINTLN("Skipping frame: type=0x%x is unexpected", frame_type);
          while(frame_length > 0){
            uint8_t skip[1];
            int skipped = client.read(skip, 1);
            frame_length -= skipped;
          }
          resetReceivedFrameHeader();
          return 0;
        }

        // read frame data to provided buffer
        client.readBytes(dest, frame_length);

        // ready for next frame
        resetReceivedFrameHeader();
        return frame_length;

      }
      
    }
  }

  return 0;
}

bool SerialWifiInterface::isConnected() const {
  return deviceConnected;  //pServer != NULL && pServer->getConnectedCount() > 0;
}
