#include "SerialWifiInterface.h"
#include "../CompanionFrameQueue.h"
#include <WiFi.h>
#include <errno.h>
#if defined(ESP32)
#include <lwip/sockets.h>
#endif

static constexpr uint32_t WIFI_FRAME_TIMEOUT_MS = 5000;
static constexpr uint32_t WIFI_TX_STALL_TIMEOUT_MS = 10000;

void SerialWifiInterface::clearBuffers() {
  held_queue_len = send_queue_len = 0;
  send_offset = 0;
  tx_waiting = false;
  tx_last_progress = 0;
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
  // Use enable intent, not isEnabled(): an unavailable listener must recover
  // even though MultiSerialInterface correctly declines to dispatch into it.
  if (_isEnabled && listener_requested) serviceListener(now);
  updateReceiveCapacityPause(now);
  if (deviceConnected && !client.connected()) {
    disconnectClient(true, DisconnectReason::PeerClosed);
  } else if (deviceConnected && receive_pending && !receive_capacity_paused
      && (uint32_t)(now - receive_started) >= WIFI_FRAME_TIMEOUT_MS) {
    disconnectClient(true, DisconnectReason::FrameTimeout);
  }
}

void SerialWifiInterface::disconnectClient(bool cancel_session,
                                           DisconnectReason reason) {
  const bool was_connected = deviceConnected;
  deviceConnected = false;
  if (client) client.stop();
  resetReceivedFrameHeader();
  send_offset = 0; // A reconnect must replay the whole frame, never its suffix.
  tx_waiting = false;
  tx_last_progress = 0;
  if (was_connected) {
    ++disconnect_count;
    last_disconnect_reason = reason;
  }
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

bool SerialWifiInterface::addressReady() {
  if (WiFi.status() == WL_CONNECTED
      && static_cast<uint32_t>(WiFi.localIP()) != 0) return true;
#if defined(ESP32)
  // A setup/OTA AP is usable even when STA has no association. Do not start a
  // listener for an AP's default address when the AP interface is disabled.
  if ((WiFi.getMode() & WIFI_AP)
      && static_cast<uint32_t>(WiFi.softAPIP()) != 0) return true;
#endif
  return false;
}

bool SerialWifiInterface::serviceListener(uint32_t now) {
  if (!listener_requested) return false;
  if (static_cast<bool>(server)) {
    listener_was_healthy = true;
    return true;  // Pico W begin() would otherwise replace a healthy listener.
  }
  if (listener_was_healthy) {
    // Release a stale listening descriptor, not the separately accepted
    // client or its response queue. Link changes alone do not take this path.
    server.end();
    listener_was_healthy = false;
    listener_attempted = false;
    listener_retry_ms = 0;
    consecutive_start_failures = 0;
  }
  if (!addressReady()) return false;
  if (listener_attempted
      && (uint32_t)(now - listener_last_attempt) < listener_retry_ms) return false;

  listener_last_attempt = now;
  listener_attempted = true;
  ++listener_begin_attempts;
  // The ESP32 SDK silently returns from begin() after socket/bind/listen
  // errors. Capture errno before end() can replace it during descriptor cleanup.
  errno = 0;
  server.begin(listener_port);
  if (!static_cast<bool>(server)) {
    listener_last_errno = errno;
    listener_last_error = StartError::StartFailed;
    ++listener_begin_failures;
    server.end();  // Also releases a socket left behind by a failed bind/listen.
    if (consecutive_start_failures < 6) ++consecutive_start_failures;
    listener_retry_ms = consecutive_start_failures < 6
        ? (1000U << (consecutive_start_failures - 1)) : 30000U;
    return false;
  }
  listener_was_healthy = true;
  consecutive_start_failures = 0;
  listener_retry_ms = 0;
  listener_last_error = StartError::None;
  listener_last_errno = 0;
  return true;
}

bool SerialWifiInterface::begin(int port) {
  if (port <= 0 || port > 65535) {
    listener_last_error = StartError::InvalidPort;
    listener_last_errno = 0;
    return false;
  }
  const bool new_request = !listener_requested || listener_port != port;
  if (listener_port != 0 && listener_port != port) {
    server.end();  // Explicit listener reconfiguration leaves an accepted client intact.
    listener_was_healthy = false;
  }
  listener_port = static_cast<uint16_t>(port);
  listener_requested = true;
  if (new_request) {
    listener_attempted = false;
    listener_retry_ms = 0;
    consecutive_start_failures = 0;
  }
  // Existing callers begin before enable. This explicit request may start
  // immediately, but background retries never implicitly re-enable it.
  return serviceListener((uint32_t)millis());
}

void SerialWifiInterface::stopListener(DisconnectReason reason) {
  _isEnabled = false;
  listener_requested = false;
  server.end();
  listener_was_healthy = false;
  listener_attempted = false;
  listener_retry_ms = 0;
  consecutive_start_failures = 0;
  disconnectClient(true, reason);
  clearBuffers();
}

void SerialWifiInterface::end() {
  stopListener(DisconnectReason::Ended);
}

// ---------- public methods
void SerialWifiInterface::enable() { 
  if (_isEnabled) return;

  _isEnabled = true;
  clearBuffers();
  resetReceivedFrameHeader();
}

void SerialWifiInterface::disable() {
  stopListener(DisconnectReason::Disabled);
}

SerialWifiInterface::Status SerialWifiInterface::getStatus() {
  const bool ready = addressReady();
  const bool listening = static_cast<bool>(server);
  State state = State::Off;
  if (_isEnabled && listener_requested) {
    state = !ready ? State::WaitingIp
        : listening ? State::Listening : State::Retrying;
  }
  const uint32_t elapsed = (uint32_t)((uint32_t)millis() - listener_last_attempt);
  const uint32_t retry = listener_requested && listener_attempted
      && !listening && elapsed < listener_retry_ms
      ? listener_retry_ms - elapsed : 0;
  return {state, _isEnabled, listener_requested, ready, listening,
          deviceConnected && client.connected(), listener_port,
          listener_begin_attempts, listener_begin_failures, retry,
          listener_last_error, listener_last_errno, accepted_clients,
          valid_frames, last_opcode, tx_frames, last_tx_opcode,
          disconnect_count, last_disconnect_reason};
}

const char* SerialWifiInterface::stateName(State state) {
  switch (state) {
    case State::Off: return "off";
    case State::WaitingIp: return "waiting-ip";
    case State::Retrying: return "retrying";
    case State::Listening: return "listening";
  }
  return "unknown";
}

const char* SerialWifiInterface::startErrorName(StartError error) {
  switch (error) {
    case StartError::None: return "none";
    case StartError::InvalidPort: return "invalid-port";
    case StartError::StartFailed: return "start-failed";
  }
  return "unknown";
}

const char* SerialWifiInterface::disconnectReasonName(DisconnectReason reason) {
  switch (reason) {
    case DisconnectReason::None: return "none";
    case DisconnectReason::PeerClosed: return "peer-closed";
    case DisconnectReason::FrameTimeout: return "frame-timeout";
    case DisconnectReason::InvalidHeader: return "invalid-header";
    case DisconnectReason::ShortHeader: return "short-header";
    case DisconnectReason::ShortPayload: return "short-payload";
    case DisconnectReason::Replaced: return "replaced";
    case DisconnectReason::Disabled: return "disabled";
    case DisconnectReason::Ended: return "ended";
    case DisconnectReason::TxError: return "tx-error";
    case DisconnectReason::TxTimeout: return "tx-timeout";
  }
  return "unknown";
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
    updateReceiveCapacityPause((uint32_t)millis());
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

bool SerialWifiInterface::responseCapacityAvailable() const {
  if (send_queue_len < FRAME_QUEUE_SIZE) return true;
  // A command's reply can displace an unsent best-effort push, never a
  // delivery-required reply or the partly transmitted front frame.
  for (int i = send_offset != 0 ? 1 : 0; i < send_queue_len; ++i) {
    if (!mesh::companionFrameRequiresDelivery(send_queue[i].buf,
                                              send_queue[i].len)) return true;
  }
  return false;
}

void SerialWifiInterface::updateReceiveCapacityPause(uint32_t now) {
  if (!receive_pending) {
    receive_capacity_paused = false;
    receive_capacity_paused_at = 0;
    return;
  }
  const bool pause = deviceConnected && !responseCapacityAvailable();
  if (pause && !receive_capacity_paused) {
    // A reply-capacity change cannot retroactively forgive a deadline which
    // already expired while RX was runnable.
    if ((uint32_t)(now - receive_started) >= WIFI_FRAME_TIMEOUT_MS) return;
    receive_capacity_paused = true;
    receive_capacity_paused_at = now;
  } else if (!pause && receive_capacity_paused) {
    // Preserve the framing budget already spent, excluding time for which
    // this transport intentionally withheld RX to make room for its reply.
    receive_started += (uint32_t)(now - receive_capacity_paused_at);
    receive_capacity_paused = false;
    receive_capacity_paused_at = 0;
  }
}

void SerialWifiInterface::resetReceivedFrameHeader() {
  received_frame_header.type = 0;
  received_frame_header.length = 0;
  receive_pending = false;
  receive_started = 0;
  receive_capacity_paused = false;
  receive_capacity_paused_at = 0;
}

size_t SerialWifiInterface::checkRecvFrame(uint8_t dest[]) {
  loop();
  if (!isEnabled()) return 0;
  // check if new client connected
  // Continue an accepted client's RX/TX during listener repair, but never
  // call the SDK accept path while its listening descriptor is unavailable.
  auto newClient = static_cast<bool>(server) ? server.available() : WiFiClient();
  if (newClient) {
    const IPAddress new_ip = newClient.remoteIP();

    // disconnect existing client
    disconnectClient(!queue_has_ip || queue_ip != new_ip,
                     DisconnectReason::Replaced);

    // Partition queues and cancel the displaced session before exposing the
    // new connection to response producers or consuming its first command.
    selectClient(new_ip, (uint32_t)millis());

    // switch active connection to new client
    client = newClient;
    ++accepted_clients;

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
      disconnectClient(true, DisconnectReason::PeerClosed);
      WIFI_DEBUG_PRINTLN("Disconnected");
    }
  }

  if (deviceConnected && send_queue_len > 0) {
    _last_write = millis();
    const uint32_t tx_now = (uint32_t)_last_write;
    if (!tx_waiting) {
      tx_waiting = true;
      tx_last_progress = tx_now;
    }
    int len = send_queue[0].len;

    // One queued packet expands to two wire frames. Keep one byte offset
    // across the entire group so responses/eviction cannot split the marker
    // from its legacy raw log, even when the socket makes partial progress.
    const bool radio2_log = mesh::isCompanionRadio2Log(send_queue[0].buf, len);
    uint8_t pkt[MAX_FRAME_SIZE + 8];
    const size_t total = mesh::buildCompanionWireFrame(pkt, send_queue[0].buf, len);
    const size_t remaining = total - send_offset;
    size_t written = 0;
#if defined(ESP32)
    // WiFiClient::write retries ten one-second select waits and restarts that
    // budget after partial progress. One nonblocking send bounds this poll
    // and leaves both incoming commands and other transports runnable.
    const int result = ::send(client.fd(), pkt + send_offset, remaining,
                              MSG_DONTWAIT);
    if (result > 0) {
      written = static_cast<size_t>(result);
    } else if (result < 0 && errno != EAGAIN && errno != EWOULDBLOCK
               && errno != EINTR) {
      disconnectClient(true, DisconnectReason::TxError);
      return 0;
    }
#else
    written = client.write(pkt + send_offset, remaining);
#endif
    if (written > 0) {
      tx_last_progress = (uint32_t)millis();
    } else if ((uint32_t)((uint32_t)millis() - tx_last_progress)
               >= WIFI_TX_STALL_TIMEOUT_MS) {
      // A locked stream on another transport may omit WiFi's polling for a
      // while. Try its socket before declaring a stall: a writable client
      // can resume after that scheduler gap without an artificial timeout.
      disconnectClient(true, DisconnectReason::TxTimeout);
      return 0;
    }
    send_offset += written < remaining ? written : remaining;
    if (send_offset == total) {
      send_offset = 0;
      tx_waiting = false;
      tx_last_progress = 0;
      tx_frames += radio2_log ? 2U : 1U;
      last_tx_opcode = radio2_log ? mesh::COMPANION_LEGACY_RX_LOG : send_queue[0].buf[0];
      send_queue_len--;
      for (int i = 0; i < send_queue_len; i++) {   // delete top item from queue
        send_queue[i] = send_queue[i + 1];
      }
      memset(&send_queue[send_queue_len], 0, sizeof(send_queue[send_queue_len]));
    }
  }
  // Sending a queued push or just part of a frame must not postpone the
  // client's device-query/app-start indefinitely. Poll at most one incoming
  // frame independently of the single outgoing attempt above.
  updateReceiveCapacityPause((uint32_t)millis());
  if (deviceConnected) {
    if (!responseCapacityAvailable()) return 0;
    if (!receive_pending && client.available() > 0) {
      receive_pending = true;
      receive_started = (uint32_t)millis();
    }
    if (!hasReceivedFrameHeader()) {
      if (client.available() < 3) return 0;
      uint8_t header[3];
      if (client.readBytes(header, sizeof(header)) != sizeof(header)) {
        disconnectClient(true, DisconnectReason::ShortHeader);
        return 0;
      }
      received_frame_header.type = header[0];
      received_frame_header.length = uint16_t(header[1]) | (uint16_t(header[2]) << 8);
      // Validate before waiting for any payload. Closing an invalid stream
      // avoids unbounded discard loops and interpreting its tail as commands.
      if (received_frame_header.type != '<' || received_frame_header.length == 0
          || received_frame_header.length > MAX_FRAME_SIZE) {
        disconnectClient(true, DisconnectReason::InvalidHeader);
        return 0;
      }
    }
    const size_t frame_length = received_frame_header.length;
    if (client.available() < (int)frame_length) return 0;
    if (dest == nullptr || client.readBytes(dest, frame_length) != frame_length) {
      disconnectClient(true, DisconnectReason::ShortPayload);
      return 0; // Never dispatch a truncated command, even after a short read.
    }
    resetReceivedFrameHeader();
    ++valid_frames;
    last_opcode = dest[0];
    return frame_length;
  }

  return 0;
}

bool SerialWifiInterface::isConnected() const {
  return deviceConnected;  //pServer != NULL && pServer->getConnectedCount() > 0;
}
