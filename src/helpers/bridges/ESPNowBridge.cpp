#include "ESPNowBridge.h"

#include <esp_wifi.h>

#ifdef WITH_ESPNOW_BRIDGE

// Static member to handle callbacks
ESPNowBridge *ESPNowBridge::_instance = nullptr;

// Static callback wrappers
#if ESP_IDF_VERSION_MAJOR >= 5
void ESPNowBridge::recv_cb(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  const uint8_t *mac = info != nullptr ? info->src_addr : nullptr;
  if (_instance) {
    _instance->queueReceivedFrame(mac, data, len);
  }
}
#else
void ESPNowBridge::recv_cb(const uint8_t *mac, const uint8_t *data, int len) {
  if (_instance) {
    _instance->queueReceivedFrame(mac, data, len);
  }
}
#endif

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
void ESPNowBridge::send_cb(const esp_now_send_info_t *info,
                           esp_now_send_status_t status) {
  const uint8_t *mac = info != nullptr ? info->des_addr : nullptr;
#else
void ESPNowBridge::send_cb(const uint8_t *mac, esp_now_send_status_t status) {
#endif
  if (_instance) {
    _instance->onDataSent(mac, status);
  }
}

ESPNowBridge::ESPNowBridge(NodePrefs *prefs, mesh::PacketManager *mgr, mesh::RTCClock *rtc)
    : BridgeBase(prefs, mgr, rtc), _rx_head(0), _rx_tail(0), _rx_count(0),
      _rx_dropped(0), _rx_dropped_reported(0),
      _tx_head(0), _tx_tail(0), _tx_count(0), _tx_waiting(false),
      _tx_callback_done(false), _tx_callback_status(ESP_NOW_SEND_SUCCESS),
      _tx_dropped(0), _tx_dropped_reported(0),
      _active_format(mesh::bridge::ESPNOW_FORMAT_WRAPPED) {
  _instance = this;
}

void ESPNowBridge::begin() {
  BRIDGE_DEBUG_PRINTLN("Initializing...\n");

  if (_initialized) return;

  _active_format = mesh::bridge::isValidEspNowFormat(_prefs->bridge_format)
      ? _prefs->bridge_format : mesh::bridge::ESPNOW_FORMAT_WRAPPED;
  portENTER_CRITICAL(&_rx_mux);
  _rx_head = 0;
  _rx_tail = 0;
  _rx_count = 0;
  _rx_dropped = 0;
  portEXIT_CRITICAL(&_rx_mux);
  _rx_dropped_reported = 0;
  _raw_reassembler.reset();
  portENTER_CRITICAL(&_tx_mux);
  _tx_head = 0;
  _tx_tail = 0;
  _tx_count = 0;
  _tx_waiting = false;
  _tx_callback_done = false;
  _tx_callback_status = ESP_NOW_SEND_SUCCESS;
  _tx_dropped = 0;
  portEXIT_CRITICAL(&_tx_mux);
  _tx_dropped_reported = 0;

  if (!mesh::bridge::isValidEspNowBridgeChannel(
          _prefs->bridge_channel, _active_format)) {
    BRIDGE_DEBUG_PRINTLN("ESP-NOW channel must be 1-13\n");
    return;
  }

  // ESP-NOW only needs the ESP-IDF station interface. Avoid Arduino's WiFi
  // facade here: it pulls DHCP, DNS, scanning, AP, and event plumbing that an
  // ESP-NOW-only bridge never uses.
  wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
  if (esp_wifi_init(&wifi_config) != ESP_OK ||
      esp_wifi_set_storage(WIFI_STORAGE_RAM) != ESP_OK ||
      esp_wifi_set_mode(WIFI_MODE_STA) != ESP_OK ||
      esp_wifi_start() != ESP_OK) {
    esp_wifi_stop();
    esp_wifi_deinit();
    return;
  }

  const bool raw_format =
      _active_format == mesh::bridge::ESPNOW_FORMAT_RAW;
  if (raw_format) {
    // Primary ESPNOWRadio nodes use Espressif's LR PHY. Retain B/G/N receive
    // support as well, but force this peer's outgoing frames to LR below.
    const uint8_t protocols = WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G
        | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR;
    if (esp_wifi_set_protocol(WIFI_IF_STA, protocols) != ESP_OK) {
      BRIDGE_DEBUG_PRINTLN("Error enabling ESP-NOW LR protocol\n");
      esp_wifi_stop();
      esp_wifi_deinit();
      return;
    }
  }
  
  // Set Wi-Fi channel
  if (esp_wifi_set_channel(_prefs->bridge_channel, WIFI_SECOND_CHAN_NONE) != ESP_OK) {
    BRIDGE_DEBUG_PRINTLN("Error setting WIFI channel to %d\n", _prefs->bridge_channel);
    esp_wifi_stop();
    esp_wifi_deinit();
    return;
  }

  // Initialize ESP-NOW
  if (esp_now_init() != ESP_OK) {
    BRIDGE_DEBUG_PRINTLN("Error initializing ESP-NOW\n");
    esp_wifi_stop();
    esp_wifi_deinit();
    return;
  }

  // Register callbacks
  if (esp_now_register_recv_cb(recv_cb) != ESP_OK || esp_now_register_send_cb(send_cb) != ESP_OK) {
    BRIDGE_DEBUG_PRINTLN("Error registering ESP-NOW callbacks\n");
    esp_now_register_recv_cb(nullptr);
    esp_now_register_send_cb(nullptr);
    esp_now_deinit();
    esp_wifi_stop();
    esp_wifi_deinit();
    return;
  }

  // Add broadcast peer
  esp_now_peer_info_t peerInfo = {};
  memset(&peerInfo, 0, sizeof(peerInfo));
  memset(peerInfo.peer_addr, 0xFF, ESP_NOW_ETH_ALEN); // Broadcast address
  peerInfo.channel = _prefs->bridge_channel;
  peerInfo.ifidx = WIFI_IF_STA;
  peerInfo.encrypt = false;

  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    BRIDGE_DEBUG_PRINTLN("Failed to add broadcast peer\n");
    esp_now_register_recv_cb(nullptr);
    esp_now_register_send_cb(nullptr);
    esp_now_deinit();
    esp_wifi_stop();
    esp_wifi_deinit();
    return;
  }

  if (raw_format) {
    esp_err_t rate_result;
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 2, 0)
    esp_now_rate_config_t rate_config = {};
    rate_config.phymode = WIFI_PHY_MODE_LR;
    rate_config.rate = WIFI_PHY_RATE_LORA_250K;
    rate_config.ersu = false;
    rate_config.dcm = false;
    rate_result = esp_now_set_peer_rate_config(peerInfo.peer_addr, &rate_config);
#else
    rate_result = esp_wifi_config_espnow_rate(
        WIFI_IF_STA, WIFI_PHY_RATE_LORA_250K);
#endif
    if (rate_result != ESP_OK) {
      BRIDGE_DEBUG_PRINTLN("Error configuring ESP-NOW LR peer rate\n");
      esp_now_del_peer(peerInfo.peer_addr);
      esp_now_register_recv_cb(nullptr);
      esp_now_register_send_cb(nullptr);
      esp_now_deinit();
      esp_wifi_stop();
      esp_wifi_deinit();
      return;
    }
  }

  // Update bridge state
  _initialized = true;
}

void ESPNowBridge::end() {
  BRIDGE_DEBUG_PRINTLN("Stopping...\n");

  // Remove broadcast peer
  uint8_t broadcastAddress[] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };
  if (esp_now_del_peer(broadcastAddress) != ESP_OK) {
    BRIDGE_DEBUG_PRINTLN("Error removing broadcast peer\n");
  }

  // Unregister callbacks
  esp_now_register_recv_cb(nullptr);
  esp_now_register_send_cb(nullptr);

  // Deinitialize ESP-NOW
  if (esp_now_deinit() != ESP_OK) {
    BRIDGE_DEBUG_PRINTLN("Error deinitializing ESP-NOW\n");
  }

  // Turn off the ESP-IDF WiFi interface.
  esp_wifi_stop();
  esp_wifi_deinit();

  portENTER_CRITICAL(&_rx_mux);
  _rx_head = 0;
  _rx_tail = 0;
  _rx_count = 0;
  portEXIT_CRITICAL(&_rx_mux);
  _raw_reassembler.reset();

  // queueTransmitFrames() marks packets seen at admission so the same bridge
  // loop cannot enqueue duplicates. Undo that mark for entries which never
  // reached esp_now_send() before an explicit bridge stop/restart.
  uint8_t tx_index = _tx_tail;
  for (uint8_t i = 0; i < _tx_count; ++i) {
    const QueuedTransmit& queued_tx = _tx_queue[tx_index];
    if (!queued_tx.started) _seen_packets.clear(&queued_tx.packet);
    tx_index = static_cast<uint8_t>((tx_index + 1) % TX_QUEUE_DEPTH);
  }
  portENTER_CRITICAL(&_tx_mux);
  _tx_head = 0;
  _tx_tail = 0;
  _tx_count = 0;
  _tx_waiting = false;
  _tx_callback_done = false;
  portEXIT_CRITICAL(&_tx_mux);

  // Update bridge state
  _initialized = false;
}

void ESPNowBridge::loop() {
  pumpTransmitQueue();

  uint8_t frame[MAX_ESPNOW_PACKET_SIZE];
  uint8_t source[mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE];
  for (uint8_t processed = 0; processed < RX_QUEUE_DEPTH; ++processed) {
    size_t frame_len = 0;
    uint32_t dropped = 0;

    portENTER_CRITICAL(&_rx_mux);
    dropped = _rx_dropped;
    if (_rx_count > 0) {
      frame_len = _rx_lengths[_rx_tail];
      if (frame_len > 0 && frame_len <= sizeof(frame)) {
        memcpy(frame, _rx_buffers[_rx_tail], frame_len);
        memcpy(source, _rx_sources[_rx_tail], sizeof(source));
      } else {
        frame_len = 0;
      }
      _rx_tail = static_cast<uint8_t>((_rx_tail + 1) % RX_QUEUE_DEPTH);
      _rx_count--;
    }
    portEXIT_CRITICAL(&_rx_mux);

    if (dropped != _rx_dropped_reported) {
      BRIDGE_DEBUG_PRINTLN("RX queue full, dropped=%lu\n",
                           (unsigned long)dropped);
      _rx_dropped_reported = dropped;
    }
    if (frame_len == 0) break;
    processReceivedFrame(source, frame, frame_len);
  }
}

bool ESPNowBridge::queueTransmitFrames(
    const mesh::espnow::ESPNowRawFrames& frames,
    const mesh::Packet& packet) {
  if (frames.count == 0 || frames.count > mesh::espnow::ESPNOW_RAW_FRAGMENT_COUNT) {
    return false;
  }
  for (uint8_t i = 0; i < frames.count; ++i) {
    if (frames.lengths[i] == 0
        || frames.lengths[i] > MAX_ESPNOW_PACKET_SIZE) {
      return false;
    }
  }

  bool queued = false;
  portENTER_CRITICAL(&_tx_mux);
  if (_tx_count < TX_QUEUE_DEPTH) {
    QueuedTransmit& queued_tx = _tx_queue[_tx_head];
    queued_tx.frames = frames;
    queued_tx.packet = packet;
    queued_tx.next_frame = 0;
    queued_tx.started = false;
    _tx_head = static_cast<uint8_t>((_tx_head + 1) % TX_QUEUE_DEPTH);
    _tx_count++;
    queued = true;
  } else {
    _tx_dropped++;
  }
  portEXIT_CRITICAL(&_tx_mux);
  return queued;
}

void ESPNowBridge::pumpTransmitQueue() {
  uint8_t frame[MAX_ESPNOW_PACKET_SIZE];
  uint16_t frame_len = 0;
  bool completed = false;
  int completed_status = ESP_NOW_SEND_SUCCESS;
  bool clear_unsent_seen = false;
  mesh::Packet unsent_packet;
  uint32_t dropped = 0;

  portENTER_CRITICAL(&_tx_mux);
  dropped = _tx_dropped;
  if (_tx_waiting && _tx_callback_done) {
    completed = true;
    completed_status = _tx_callback_status;
    _tx_waiting = false;
    _tx_callback_done = false;

    if (_tx_count > 0) {
      QueuedTransmit& queued_tx = _tx_queue[_tx_tail];
      if (completed_status == ESP_NOW_SEND_SUCCESS) {
        queued_tx.next_frame++;
        if (queued_tx.next_frame >= queued_tx.frames.count) {
          _tx_tail = static_cast<uint8_t>(
              (_tx_tail + 1) % TX_QUEUE_DEPTH);
          _tx_count--;
        }
      } else {
        // esp_now_send() accepted at least one frame, matching the old bridge
        // definition of "seen", but never submit any remaining fragments from
        // a logical packet whose preceding frame failed.
        _tx_tail = static_cast<uint8_t>((_tx_tail + 1) % TX_QUEUE_DEPTH);
        _tx_count--;
      }
    }
  }
  if (!_tx_waiting && _tx_count > 0) {
    QueuedTransmit& queued_tx = _tx_queue[_tx_tail];
    if (queued_tx.next_frame < queued_tx.frames.count) {
      frame_len = queued_tx.frames.lengths[queued_tx.next_frame];
      if (frame_len > 0 && frame_len <= sizeof(frame)) {
        memcpy(frame, queued_tx.frames.data[queued_tx.next_frame], frame_len);
      } else {
        frame_len = 0;
      }
    }
    if (frame_len > 0) {
      _tx_waiting = true;
      _tx_callback_done = false;
    }
  }
  portEXIT_CRITICAL(&_tx_mux);

  if (dropped != _tx_dropped_reported) {
    BRIDGE_DEBUG_PRINTLN("TX queue full, dropped=%lu\n",
                         (unsigned long)dropped);
    _tx_dropped_reported = dropped;
  }
  if (completed && completed_status != ESP_NOW_SEND_SUCCESS) {
    BRIDGE_DEBUG_PRINTLN("TX callback failed, status=%d\n",
                         completed_status);
  }
  if (frame_len == 0) return;

  static const uint8_t broadcast_address[] = {
      0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
  const esp_err_t result = esp_now_send(
      broadcast_address, frame, frame_len);
  if (result != ESP_OK) {
    portENTER_CRITICAL(&_tx_mux);
    _tx_waiting = false;
    _tx_callback_done = false;
    if (_tx_count > 0) {
      QueuedTransmit& queued_tx = _tx_queue[_tx_tail];
      if (!queued_tx.started) {
        unsent_packet = queued_tx.packet;
        clear_unsent_seen = true;
      }
      _tx_tail = static_cast<uint8_t>((_tx_tail + 1) % TX_QUEUE_DEPTH);
      _tx_count--;
    }
    portEXIT_CRITICAL(&_tx_mux);
    // Admission marks the packet to suppress duplicate queue entries. Restore
    // the prior behavior when even its first esp_now_send() cannot start.
    if (clear_unsent_seen) _seen_packets.clear(&unsent_packet);
    BRIDGE_DEBUG_PRINTLN("TX failed to start, error=%d\n", (int)result);
  } else {
    portENTER_CRITICAL(&_tx_mux);
    if (_tx_count > 0) _tx_queue[_tx_tail].started = true;
    portEXIT_CRITICAL(&_tx_mux);
  }
}

bool ESPNowBridge::xorCrypt(uint8_t *data, size_t len) {
  size_t keyLen = 0;
  while (keyLen < sizeof(_prefs->bridge_secret) && _prefs->bridge_secret[keyLen] != 0) keyLen++;
  if (keyLen == 0 || keyLen == sizeof(_prefs->bridge_secret)) {
    BRIDGE_DEBUG_PRINTLN("Invalid empty or unterminated bridge secret\n");
    return false;
  }
  for (size_t i = 0; i < len; i++) {
    data[i] ^= _prefs->bridge_secret[i % keyLen];
  }
  return true;
}

void ESPNowBridge::receiveMeshPacket(const uint8_t *data, size_t len) {
  mesh::Packet *pkt = _mgr->allocNew();
  if (!pkt) return;

  if (pkt->readFrom(data, len)) {
    onPacketReceived(pkt);
  } else {
    _mgr->free(pkt);
  }
}

void ESPNowBridge::queueReceivedFrame(const uint8_t *mac, const uint8_t *data,
                                      int len) {
  if (data == nullptr || len <= 0 || len > MAX_ESPNOW_PACKET_SIZE) {
    return;
  }

  // Wrapped deployments share the WiFi channel with unrelated ESP-NOW users.
  // Reject obvious foreign traffic before it can occupy a queue slot.
  if (_active_format == mesh::bridge::ESPNOW_FORMAT_WRAPPED
      && (len < BRIDGE_MAGIC_SIZE
          || data[0] != static_cast<uint8_t>(BRIDGE_PACKET_MAGIC >> 8)
          || data[1] != static_cast<uint8_t>(BRIDGE_PACKET_MAGIC & 0xFF))) {
    return;
  }

  portENTER_CRITICAL(&_rx_mux);
  if (_rx_count < RX_QUEUE_DEPTH) {
    memcpy(_rx_buffers[_rx_head], data, static_cast<size_t>(len));
    _rx_lengths[_rx_head] = static_cast<uint16_t>(len);
    if (mac != nullptr) {
      memcpy(_rx_sources[_rx_head], mac,
             mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE);
    } else {
      memset(_rx_sources[_rx_head], 0,
             mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE);
    }
    _rx_head = static_cast<uint8_t>((_rx_head + 1) % RX_QUEUE_DEPTH);
    _rx_count++;
  } else {
    _rx_dropped++;
  }
  portEXIT_CRITICAL(&_rx_mux);
}

void ESPNowBridge::processReceivedFrame(const uint8_t *mac,
                                        const uint8_t *data, size_t len) {
  if (data == nullptr || len == 0 || len > MAX_ESPNOW_PACKET_SIZE) {
    return;
  }

  if (_active_format == mesh::bridge::ESPNOW_FORMAT_RAW) {
    uint8_t packet[MAX_TRANS_UNIT];
    size_t packet_len = 0;
    const mesh::espnow::ESPNowRawReassemblyResult result =
        _raw_reassembler.acceptFrame(
            mac, data, len, millis(), packet, sizeof(packet), packet_len);
    if (result == mesh::espnow::ESPNowRawReassemblyResult::PASSTHROUGH
        || result ==
            mesh::espnow::ESPNowRawReassemblyResult::PACKET_COMPLETE) {
      BRIDGE_DEBUG_PRINTLN("RX raw, payload_len=%u%s\n",
                           (unsigned)packet_len,
                           result == mesh::espnow::ESPNowRawReassemblyResult::PACKET_COMPLETE
                               ? " (reassembled)" : "");
      receiveMeshPacket(packet, packet_len);
    } else if (result == mesh::espnow::ESPNowRawReassemblyResult::REJECTED
               || result ==
                   mesh::espnow::ESPNowRawReassemblyResult::OUTPUT_TOO_SMALL) {
      BRIDGE_DEBUG_PRINTLN("RX raw fragment rejected, result=%u len=%u\n",
                           (unsigned)result, (unsigned)len);
    }
    return;
  }

  // Wrapped mode is strict: ignore packets too small to contain its header
  // and checksum rather than trying to auto-detect raw MeshCore traffic.
  if (len < (BRIDGE_MAGIC_SIZE + BRIDGE_CHECKSUM_SIZE)) {
    BRIDGE_DEBUG_PRINTLN("RX packet too small, len=%u\n", (unsigned)len);
    return;
  }

  // Check packet header magic
  uint16_t received_magic = (data[0] << 8) | data[1];
  if (received_magic != BRIDGE_PACKET_MAGIC) {
    BRIDGE_DEBUG_PRINTLN("RX invalid magic 0x%04X\n", received_magic);
    return;
  }

  // Make a copy we can decrypt
  uint8_t decrypted[MAX_ESPNOW_PACKET_SIZE];
  const size_t encryptedDataLen = len - BRIDGE_MAGIC_SIZE;
  memcpy(decrypted, data + BRIDGE_MAGIC_SIZE, encryptedDataLen);

  // Try to decrypt (checksum + payload)
  if (!xorCrypt(decrypted, encryptedDataLen)) return;

  // Validate checksum
  uint16_t received_checksum = (decrypted[0] << 8) | decrypted[1];
  const size_t payloadLen = encryptedDataLen - BRIDGE_CHECKSUM_SIZE;

  if (!validateChecksum(decrypted + BRIDGE_CHECKSUM_SIZE, payloadLen, received_checksum)) {
    // Failed to decrypt - likely from a different network
    BRIDGE_DEBUG_PRINTLN("RX checksum mismatch, rcv=0x%04X\n", received_checksum);
    return;
  }

  BRIDGE_DEBUG_PRINTLN("RX, payload_len=%u\n", (unsigned)payloadLen);
  receiveMeshPacket(decrypted + BRIDGE_CHECKSUM_SIZE, payloadLen);
}

void ESPNowBridge::onDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  (void)mac_addr;
  portENTER_CRITICAL(&_tx_mux);
  if (_tx_waiting) {
    _tx_callback_status = static_cast<int>(status);
    _tx_callback_done = true;
  }
  portEXIT_CRITICAL(&_tx_mux);
}

void ESPNowBridge::sendPacket(mesh::Packet *packet) {
  // Guard against uninitialized state
  if (_initialized == false) {
    return;
  }

  // First validate the packet pointer
  if (!packet) {
    BRIDGE_DEBUG_PRINTLN("TX invalid packet pointer\n");
    return;
  }

  if (!_seen_packets.wasSeen(packet)) {
    // Check the serialized size before writing into the ESP-NOW-sized buffer.
    const int expectedMeshPacketLen = packet->getRawLength();
    const size_t maxPayload =
        mesh::bridge::espNowMaxMeshPacketSize(_active_format);
    if (expectedMeshPacketLen < 0
        || (size_t)expectedMeshPacketLen > maxPayload) {
      BRIDGE_DEBUG_PRINTLN("TX packet too large (payload=%d, max=%u)\n",
                           expectedMeshPacketLen, (unsigned)maxPayload);
      return;
    }

    uint8_t buffer[MAX_TRANS_UNIT];
    uint16_t meshPacketLen = packet->writeTo(buffer);
    if (meshPacketLen != (uint16_t)expectedMeshPacketLen) {
      BRIDGE_DEBUG_PRINTLN("TX packet length mismatch (actual=%d, expected=%d)\n", meshPacketLen,
                           expectedMeshPacketLen);
      return;
    }

    size_t totalPacketSize = meshPacketLen;
    if (_active_format != mesh::bridge::ESPNOW_FORMAT_RAW) {
      const size_t packetOffset = BRIDGE_MAGIC_SIZE + BRIDGE_CHECKSUM_SIZE;
      memmove(buffer + packetOffset, buffer, meshPacketLen);

      // Write the legacy magic and encrypted checksum/payload wrapper.
      buffer[0] = (BRIDGE_PACKET_MAGIC >> 8) & 0xFF;
      buffer[1] = BRIDGE_PACKET_MAGIC & 0xFF;
      const uint16_t checksum =
          fletcher16(buffer + packetOffset, meshPacketLen);
      buffer[2] = (checksum >> 8) & 0xFF;
      buffer[3] = checksum & 0xFF;
      if (!xorCrypt(buffer + BRIDGE_MAGIC_SIZE,
                    meshPacketLen + BRIDGE_CHECKSUM_SIZE)) {
        return;
      }
      totalPacketSize = packetOffset + meshPacketLen;
    }

    mesh::espnow::ESPNowRawFrames frames;
    bool encoded = false;
    if (_active_format == mesh::bridge::ESPNOW_FORMAT_RAW) {
      encoded = mesh::espnow::encodeEspNowRawFrames(
          buffer, totalPacketSize, frames);
    } else if (totalPacketSize <= MAX_ESPNOW_PACKET_SIZE) {
      frames.count = 1;
      frames.lengths[0] = static_cast<uint16_t>(totalPacketSize);
      memcpy(frames.data[0], buffer, totalPacketSize);
      encoded = true;
    }

    if (encoded && queueTransmitFrames(frames, *packet)) {
      _seen_packets.markSeen(packet);
      BRIDGE_DEBUG_PRINTLN("TX %s, len=%d, frames=%u\n",
                           mesh::bridge::espNowFormatName(_active_format),
                           meshPacketLen, (unsigned)frames.count);
    } else {
      BRIDGE_DEBUG_PRINTLN("TX queue/encode failed!\n");
    }
  }
}

void ESPNowBridge::onPacketReceived(mesh::Packet *packet) {
  handleReceivedPacket(packet);
}

#endif
