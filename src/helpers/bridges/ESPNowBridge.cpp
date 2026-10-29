#include "ESPNowBridge.h"
#include <helpers/WirelessControl.h>

#include <esp_wifi.h>
#include <helpers/esp32/WiFiRadioPolicy.h>
#include <helpers/ESPNowPeerSettings.h>
#include <stdio.h>

#ifdef WITH_ESPNOW_BRIDGE
#include <WiFi.h>

static bool stopBridgeWiFiIfUnused() {
  if (!(mesh::wireless::control().enabled() & mesh::wireless::WiFi)) {
    // WebConfig/OTA may have adopted an initially IDF-only bridge driver.
    // Shut down through Arduino first so its private initialized/started
    // caches cannot outlive the SDK driver and break the next WiFi startup.
    // In the pinned Arduino 2/3 cores, channel() is gated by initialized but
    // getMode() is also gated by started. A failed facade start can therefore
    // report OFF despite retaining initialized state. Complete that start
    // before stopping it, without allocating a facade for a pure IDF owner.
    if (WiFi.getMode() == WIFI_OFF && WiFi.channel() > 0) {
      if (!WiFi.mode(WIFI_STA)) return false;
    }
    if (!WiFi.mode(WIFI_OFF)) return false;

    // Arduino OFF is a no-op when the bridge alone initialized the SDK.
    // Probe the SDK separately rather than trusting the facade's mode/cache.
    wifi_mode_t mode = WIFI_MODE_NULL;
    const esp_err_t state = esp_wifi_get_mode(&mode);
    if (state == ESP_OK) {
      if (esp_wifi_stop() != ESP_OK || esp_wifi_deinit() != ESP_OK) return false;
    } else if (state != ESP_ERR_WIFI_NOT_INIT) return false;
  }
  return true;
}

static void stopBridgeWiFiAndReleaseSleepPreference(bool& owned, uint8_t previous,
                                                    bool teardown_uncertain) {
  // Keep both driver and cache intact while an old ESP-NOW session or a failed
  // WiFi teardown may still be alive. A later stop retries before restoration.
  if (teardown_uncertain || !stopBridgeWiFiIfUnused() || !owned) return;
  owned = false;
  // A new owner may use its own cached preference or a direct SDK policy.
  // Never overwrite either while it owns WiFi, or a newer cached preference.
  if (!(mesh::wireless::control().enabled() & mesh::wireless::WiFi)
      && WiFi.getSleep() == WIFI_PS_NONE) {
    (void)WiFi.setSleep(static_cast<wifi_ps_type_t>(previous));
  }
}

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
  using mesh::bridge::diag::Stage;
  if (mesh::wireless::control().blocked(mesh::wireless::EspNow)) {
    recordError(Stage::Policy, ESP_ERR_INVALID_STATE);
    emitDebug();
    return;
  }
  BRIDGE_DEBUG_PRINTLN("Initializing...\n");

  if (_initialized) return;
  if (_sdk_teardown_failed || _wifi_sleep_preference_owned) {
    end();
    if (_sdk_teardown_failed || _wifi_sleep_preference_owned) return;
  }
  if (!mesh::bridge::copyEspNowPeer(_active_peer)) {
    recordError(Stage::PeerConfig, ESP_ERR_INVALID_STATE);
    emitDebug();
    return;
  }

  _active_format = mesh::bridge::isValidEspNowFormat(_prefs->bridge_format)
      ? _prefs->bridge_format : mesh::bridge::ESPNOW_FORMAT_WRAPPED;
  const bool raw_format = _active_format == mesh::bridge::ESPNOW_FORMAT_RAW;
  if (raw_format && mesh::wifi::checkLongRangeRadioStart() != ESP_OK) {
    recordError(Stage::WiFiProtocol, ESP_ERR_INVALID_STATE);
    emitDebug();
    BRIDGE_DEBUG_PRINTLN("ESP-NOW LR start deferred while a WiFi AP is active\n");
    return;
  }
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
  _tx_started_at = _tx_retry_at = 0;

  if (!mesh::bridge::isValidEspNowBridgeChannel(
          _prefs->bridge_channel, _active_format)) {
    recordError(Stage::Channel, ESP_ERR_INVALID_ARG);
    emitDebug();
    BRIDGE_DEBUG_PRINTLN("ESP-NOW channel must be 1-13\n");
    return;
  }

  // ESP-NOW only needs the ESP-IDF station interface. Avoid Arduino's WiFi
  // facade here: it pulls DHCP, DNS, scanning, AP, and event plumbing that an
  // ESP-NOW-only bridge never uses.
  const bool shared_wifi = (mesh::wireless::control().enabled() & mesh::wireless::WiFi) != 0;
  if (shared_wifi) {
    uint8_t channel = 0;
    wifi_second_chan_t secondary = WIFI_SECOND_CHAN_NONE;
    const esp_err_t result = esp_wifi_get_channel(&channel, &secondary);
    if (result != ESP_OK || channel != _prefs->bridge_channel) {
      recordError(Stage::ChannelConflict,
                  result != ESP_OK ? result : ESP_ERR_INVALID_STATE);
      emitDebug();
      BRIDGE_DEBUG_PRINTLN("ESP-NOW bridge channel must match the active WiFi channel\n");
      return;
    }
  }
  wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
  auto failed = [this](esp_err_t result, Stage stage) {
    if (result == ESP_OK) return false;
    recordError(stage, result);
    stopBridgeWiFiAndReleaseSleepPreference(_wifi_sleep_preference_owned,
        _previous_wifi_sleep, _sdk_teardown_failed);
    emitDebug();
    return true;
  };
  if (!shared_wifi) {
    _previous_wifi_sleep = static_cast<uint8_t>(WiFi.getSleep());
    _wifi_sleep_preference_owned = true;
    // Arduino applies this cache asynchronously on STA_START, including after
    // raw IDF startup. Prepare it first so a late event cannot undo SDK NONE.
    // setSleep returns false when the cache is already correct.
    (void)WiFi.setSleep(WIFI_PS_NONE);
    if (failed(esp_wifi_init(&wifi_config), Stage::WiFiInit)
        || failed(esp_wifi_set_storage(WIFI_STORAGE_RAM), Stage::WiFiStorage)
        || failed(esp_wifi_set_mode(WIFI_MODE_STA), Stage::WiFiMode)
        || failed(esp_wifi_start(), Stage::WiFiStart)
        // A dedicated ESP-NOW receiver must listen continuously. Associated
        // infrastructure WiFi retains its own saved modem-sleep policy.
        || failed(esp_wifi_set_ps(WIFI_PS_NONE), Stage::WiFiPowerSave)) return;
  }

  if (raw_format) {
    // Primary ESPNOWRadio nodes use Espressif's LR PHY. Retain B/G/N receive
    // support as well, but force this peer's outgoing frames to LR below.
    const uint8_t protocols = WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G
        | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR;
    const esp_err_t result = mesh::wifi::applyStationProtocolMask(protocols, true);
    if (result != ESP_OK) {
      recordError(Stage::WiFiProtocol, result);
      emitDebug();
      BRIDGE_DEBUG_PRINTLN("Error enabling ESP-NOW LR protocol\n");
      stopBridgeWiFiAndReleaseSleepPreference(_wifi_sleep_preference_owned,
          _previous_wifi_sleep, _sdk_teardown_failed);
      return;
    }
  }
  
  // Set Wi-Fi channel
  const esp_err_t channel_result = shared_wifi ? ESP_OK
      : esp_wifi_set_channel(_prefs->bridge_channel, WIFI_SECOND_CHAN_NONE);
  if (channel_result != ESP_OK) {
    recordError(Stage::WiFiChannel, channel_result);
    emitDebug();
    BRIDGE_DEBUG_PRINTLN("Error setting WIFI channel to %d\n", _prefs->bridge_channel);
    stopBridgeWiFiAndReleaseSleepPreference(_wifi_sleep_preference_owned,
        _previous_wifi_sleep, _sdk_teardown_failed);
    return;
  }

  // Initialize ESP-NOW
  const esp_err_t init_result = esp_now_init();
  if (init_result != ESP_OK) {
    recordError(Stage::EspNowInit, init_result);
    emitDebug();
    BRIDGE_DEBUG_PRINTLN("Error initializing ESP-NOW\n");
    stopBridgeWiFiAndReleaseSleepPreference(_wifi_sleep_preference_owned,
        _previous_wifi_sleep, _sdk_teardown_failed);
    return;
  }

  // Register callbacks
  const esp_err_t recv_result = esp_now_register_recv_cb(recv_cb);
  const esp_err_t send_result = recv_result == ESP_OK
      ? esp_now_register_send_cb(send_cb) : ESP_OK;
  if (recv_result != ESP_OK || send_result != ESP_OK) {
    recordError(recv_result != ESP_OK ? Stage::RecvCallback : Stage::SendCallback,
                recv_result != ESP_OK ? recv_result : send_result);
    emitDebug();
    BRIDGE_DEBUG_PRINTLN("Error registering ESP-NOW callbacks\n");
    esp_now_register_recv_cb(nullptr);
    esp_now_register_send_cb(nullptr);
    _sdk_teardown_failed = esp_now_deinit() != ESP_OK;
    stopBridgeWiFiAndReleaseSleepPreference(_wifi_sleep_preference_owned,
        _previous_wifi_sleep, _sdk_teardown_failed);
    return;
  }

  // A missing peer setting keeps legacy broadcast. Configured unicast uses
  // the same bytes and gains MAC acknowledgement plus bounded callback retry.
  esp_now_peer_info_t peerInfo = {};
  memset(&peerInfo, 0, sizeof(peerInfo));
  memcpy(peerInfo.peer_addr, _active_peer, sizeof(_active_peer));
  peerInfo.channel = _prefs->bridge_channel;
  peerInfo.ifidx = WIFI_IF_STA;
  peerInfo.encrypt = false;

  const esp_err_t peer_result = esp_now_add_peer(&peerInfo);
  if (peer_result != ESP_OK) {
    recordError(Stage::PeerAdd, peer_result);
    emitDebug();
    BRIDGE_DEBUG_PRINTLN("Failed to add broadcast peer\n");
    esp_now_register_recv_cb(nullptr);
    esp_now_register_send_cb(nullptr);
    _sdk_teardown_failed = esp_now_deinit() != ESP_OK;
    stopBridgeWiFiAndReleaseSleepPreference(_wifi_sleep_preference_owned,
        _previous_wifi_sleep, _sdk_teardown_failed);
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
      recordError(Stage::PeerRate, rate_result);
      emitDebug();
      BRIDGE_DEBUG_PRINTLN("Error configuring ESP-NOW LR peer rate\n");
      esp_now_del_peer(peerInfo.peer_addr);
      esp_now_register_recv_cb(nullptr);
      esp_now_register_send_cb(nullptr);
      _sdk_teardown_failed = esp_now_deinit() != ESP_OK;
      stopBridgeWiFiAndReleaseSleepPreference(_wifi_sleep_preference_owned,
          _previous_wifi_sleep, _sdk_teardown_failed);
      return;
    }
  }

  // Update bridge state
  _initialized = true;
  mesh::wifi::setLongRangeOwner(mesh::wifi::kLongRangeBridgeOwner, raw_format);
  mesh::wifi::bridgeEspNowChannel().store(_prefs->bridge_channel);
  _debug_dirty.store(true, std::memory_order_relaxed);
  emitDebug();
}

void ESPNowBridge::end() {
  if (!_initialized && !_sdk_teardown_failed) {
    if (_wifi_sleep_preference_owned)
      stopBridgeWiFiAndReleaseSleepPreference(_wifi_sleep_preference_owned,
          _previous_wifi_sleep, false);
    return;
  }
  BRIDGE_DEBUG_PRINTLN("Stopping...\n");

  const esp_err_t remove_result = esp_now_del_peer(_active_peer);
  if (remove_result != ESP_OK) {
    recordError(mesh::bridge::diag::Stage::PeerRemove, remove_result);
    BRIDGE_DEBUG_PRINTLN("Error removing ESP-NOW peer\n");
  }

  // Unregister callbacks
  esp_now_register_recv_cb(nullptr);
  esp_now_register_send_cb(nullptr);

  // Deinitialize ESP-NOW
  const esp_err_t deinit_result = esp_now_deinit();
  _sdk_teardown_failed = deinit_result != ESP_OK;
  if (_sdk_teardown_failed) {
    recordError(mesh::bridge::diag::Stage::EspNowDeinit, deinit_result);
    BRIDGE_DEBUG_PRINTLN("Error deinitializing ESP-NOW\n");
  }

  // Infrastructure WiFi may still own this shared radio.
  mesh::wifi::bridgeEspNowChannel().store(0);
  mesh::wifi::setLongRangeOwner(mesh::wifi::kLongRangeBridgeOwner, false);
  stopBridgeWiFiAndReleaseSleepPreference(_wifi_sleep_preference_owned,
      _previous_wifi_sleep, _sdk_teardown_failed);

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
    if (!queued_tx.started && !queued_tx.diagnostic)
      _seen_packets.clearSeenKey(queued_tx.seen_key);
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
  _debug_dirty.store(true, std::memory_order_relaxed);
  emitDebug();
}

void ESPNowBridge::loop() {
  if (!_initialized) { emitDebug(); return; }
  pumpTransmitQueue();
  if (!_initialized) { emitDebug(); return; }

  uint8_t frame[MAX_ESPNOW_PACKET_SIZE];
  uint8_t source[mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE];
  for (uint8_t processed = 0; processed < RX_QUEUE_DEPTH; ++processed) {
    size_t frame_len = 0;
    portENTER_CRITICAL(&_rx_mux);
    if (_rx_count > 0) {
      frame_len = _rx_lengths[_rx_tail];
      if (frame_len > 0 && frame_len <= sizeof(frame)) {
        memcpy(frame, _rx_buffers[_rx_tail], frame_len);
        memcpy(source, _rx_sources[_rx_tail], sizeof(source));
      } else {
        frame_len = 0;
      }
    }
    portEXIT_CRITICAL(&_rx_mux);
    if (frame_len == 0) break;

    const bool diagnostic = mesh::bridge::diag::isProbeCandidate(frame, frame_len);
    // Diagnostic frames never consume a mesh slot, even under pool pressure.
    // Ordinary frames remain queued until an allocation succeeds.
    mesh::Packet* packet = diagnostic ? nullptr : _mgr->allocNew();
    if (!diagnostic && packet == nullptr) break;
    portENTER_CRITICAL(&_rx_mux);
    _rx_tail = static_cast<uint8_t>((_rx_tail + 1) % RX_QUEUE_DEPTH);
    _rx_count--;
    portEXIT_CRITICAL(&_rx_mux);

    if (diagnostic) {
      mesh::bridge::diag::Probe probe;
      const bool valid = mesh::bridge::diag::decodeProbe(frame, frame_len, probe);
      portENTER_CRITICAL(&_rx_mux);
      if (valid) {
        mesh::bridge::diag::increment(_stats.probe.rx_ok);
        _stats.probe.last_rx_nonce = probe.nonce;
      } else {
        mesh::bridge::diag::increment(_stats.probe.rx_bad);
      }
      portEXIT_CRITICAL(&_rx_mux);
      _debug_dirty.store(true, std::memory_order_relaxed);
      continue;  // Never decode, echo, deduplicate, or relay a probe as MeshCore.
    }
    if (!processReceivedFrame(packet, source, frame, frame_len)) _mgr->free(packet);
  }
  emitDebug();
}

bool ESPNowBridge::queueTransmitFrames(
    const mesh::espnow::ESPNowRawFrames& frames,
    const mesh::Packet& packet, bool diagnostic) {
  if (frames.count == 0 || frames.count > mesh::espnow::ESPNOW_RAW_FRAGMENT_COUNT) {
    return false;
  }
  for (uint8_t i = 0; i < frames.count; ++i) {
    if (frames.lengths[i] == 0
        || frames.lengths[i] > frames.data.capacity(i)) {
      return false;
    }
  }

  // Hash the caller-owned snapshot before entering the callback-shared queue
  // lock. SHA work never extends an ESP-NOW critical section.
  SimpleMeshTables::SeenKey seen_key;
  if (!diagnostic) seen_key = _seen_packets.seenKey(packet);
  bool queued = false;
  portENTER_CRITICAL(&_tx_mux);
  if (_tx_count < TX_QUEUE_DEPTH) {
    QueuedTransmit& queued_tx = _tx_queue[_tx_head];
    queued_tx.frames = frames;
    queued_tx.seen_key = seen_key;
    queued_tx.next_frame = 0;
    queued_tx.started = false;
    queued_tx.start_retries = 0;
    queued_tx.diagnostic = diagnostic;
    if (diagnostic) mesh::bridge::diag::increment(_stats.probe.tx_queued);
    else mesh::bridge::diag::increment(_stats.tx.queued);
    _debug_dirty.store(true, std::memory_order_relaxed);
    _tx_head = static_cast<uint8_t>((_tx_head + 1) % TX_QUEUE_DEPTH);
    _tx_count++;
    queued = true;
  } else {
    _tx_dropped++;
    if (diagnostic) mesh::bridge::diag::increment(_stats.probe.tx_fail);
    else mesh::bridge::diag::increment(_stats.tx.drops);
    _debug_dirty.store(true, std::memory_order_relaxed);
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
  SimpleMeshTables::SeenKey unsent_key;
  uint32_t dropped = 0;
  const uint32_t now = millis();
  bool timed_out = false;

  portENTER_CRITICAL(&_tx_mux);
  timed_out = _tx_waiting && !_tx_callback_done
      && static_cast<uint32_t>(now - _tx_started_at) >= TX_CALLBACK_TIMEOUT_MS;
  if (timed_out) {
    if (_tx_count > 0 && _tx_queue[_tx_tail].diagnostic)
      mesh::bridge::diag::increment(_stats.probe.tx_timeout);
    else mesh::bridge::diag::increment(_stats.tx.callback_timeout);
  }
  portEXIT_CRITICAL(&_tx_mux);
  if (timed_out) {
    recordError(mesh::bridge::diag::Stage::TxTimeout, ESP_ERR_TIMEOUT);
    // A completion carries no frame identity. Continuing after timeout could
    // mistake a late callback for the next fragment. Tear down that SDK session
    // before accepting another frame; never transmit an orphan sibling.
    BRIDGE_DEBUG_PRINTLN("TX callback timeout; restarting ESP-NOW\n");
    end();
    // Fail closed if the old session could still own an accepted frame. A
    // later explicit begin retries teardown before accepting new traffic.
    if (!_sdk_teardown_failed) begin();
    return;
  }

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
        queued_tx.start_retries = 0;
        if (queued_tx.next_frame >= queued_tx.frames.count) {
          _tx_tail = static_cast<uint8_t>(
              (_tx_tail + 1) % TX_QUEUE_DEPTH);
          _tx_count--;
        }
      } else if (!queued_tx.diagnostic && queued_tx.start_retries < TX_MAX_START_RETRIES) {
        // This completion is definite, unlike a missing callback. Retry the
        // same frame within the shared start/callback budget; its sibling
        // cannot be submitted until this frame succeeds.
        ++queued_tx.start_retries;
        _tx_retry_at = now + TX_RETRY_DELAY_MS;
      } else {
        if (!queued_tx.diagnostic) mesh::bridge::diag::increment(_stats.tx.drops);
        _tx_tail = static_cast<uint8_t>((_tx_tail + 1) % TX_QUEUE_DEPTH);
        _tx_count--;
      }
    }
  }
  if (!_tx_waiting && _tx_count > 0
      && (_tx_queue[_tx_tail].start_retries == 0
          || static_cast<int32_t>(now - _tx_retry_at) >= 0)) {
    QueuedTransmit& queued_tx = _tx_queue[_tx_tail];
    if (queued_tx.next_frame < queued_tx.frames.count) {
      frame_len = queued_tx.frames.lengths[queued_tx.next_frame];
      if (frame_len > 0 && frame_len <= sizeof(frame)
          && frame_len <= queued_tx.frames.data.capacity(queued_tx.next_frame)) {
        memcpy(frame, queued_tx.frames.data[queued_tx.next_frame], frame_len);
      } else {
        frame_len = 0;
      }
    }
    if (frame_len > 0) {
      _tx_waiting = true;
      _tx_callback_done = false;
      _tx_started_at = now;
      _tx_retry_at = 0;
    }
  }
  portEXIT_CRITICAL(&_tx_mux);

  if (dropped != _tx_dropped_reported) {
    BRIDGE_DEBUG_PRINTLN("TX queue full, dropped=%lu\n",
                         (unsigned long)dropped);
    _tx_dropped_reported = dropped;
  }
  if (completed && completed_status != ESP_NOW_SEND_SUCCESS) {
    recordError(mesh::bridge::diag::Stage::TxCallback, completed_status);
    BRIDGE_DEBUG_PRINTLN("TX callback failed, status=%d\n",
                         completed_status);
  }
  if (frame_len == 0) return;

  const esp_err_t result = esp_now_send(
      _active_peer, frame, frame_len);
  if (result != ESP_OK) {
    recordError(mesh::bridge::diag::Stage::TxStart, result);
    portENTER_CRITICAL(&_tx_mux);
    _tx_waiting = false;
    _tx_callback_done = false;
    if (_tx_count > 0) {
      QueuedTransmit& queued_tx = _tx_queue[_tx_tail];
      if (queued_tx.diagnostic) mesh::bridge::diag::increment(_stats.probe.tx_fail);
      else mesh::bridge::diag::increment(_stats.tx.start_fail);
      if (!queued_tx.diagnostic && result == ESP_ERR_ESPNOW_NO_MEM
          && queued_tx.start_retries < TX_MAX_START_RETRIES) {
        ++queued_tx.start_retries;
        _tx_retry_at = now + TX_RETRY_DELAY_MS;
      } else {
        if (!queued_tx.started && !queued_tx.diagnostic) {
          unsent_key = queued_tx.seen_key;
          clear_unsent_seen = true;
        }
        _tx_tail = static_cast<uint8_t>((_tx_tail + 1) % TX_QUEUE_DEPTH);
        _tx_count--;
        _tx_retry_at = 0;
        if (!queued_tx.diagnostic) mesh::bridge::diag::increment(_stats.tx.drops);
      }
    }
    portEXIT_CRITICAL(&_tx_mux);
    // Admission marks the packet to suppress duplicate queue entries. Restore
    // the prior behavior when even its first esp_now_send() cannot start.
    if (clear_unsent_seen) _seen_packets.clearSeenKey(unsent_key);
    BRIDGE_DEBUG_PRINTLN("TX failed to start, error=%d\n", (int)result);
  } else {
    portENTER_CRITICAL(&_tx_mux);
    if (_tx_count > 0) {
      _tx_queue[_tx_tail].started = true;
      if (_tx_queue[_tx_tail].diagnostic) mesh::bridge::diag::increment(_stats.probe.tx_start);
      else mesh::bridge::diag::increment(_stats.tx.start_ok);
    }
    portEXIT_CRITICAL(&_tx_mux);
  }
  _debug_dirty.store(true, std::memory_order_relaxed);
}

bool ESPNowBridge::xorCrypt(uint8_t *data, size_t len) {
  size_t keyLen = 0;
  while (keyLen < sizeof(_prefs->bridge_secret) && _prefs->bridge_secret[keyLen] != 0) keyLen++;
  if (keyLen == 0 || keyLen == sizeof(_prefs->bridge_secret)) {
    recordError(mesh::bridge::diag::Stage::Secret, ESP_ERR_INVALID_ARG);
    BRIDGE_DEBUG_PRINTLN("Invalid empty or unterminated bridge secret\n");
    return false;
  }
  for (size_t i = 0; i < len; i++) {
    data[i] ^= _prefs->bridge_secret[i % keyLen];
  }
  return true;
}

void ESPNowBridge::receiveMeshPacket(mesh::Packet* pkt, const uint8_t *data, size_t len) {

  if (pkt->readFrom(data, len)) {
    noteRx(&mesh::bridge::diag::RxStats::packet_ok);
    onPacketReceived(pkt);
  } else {
    noteRx(&mesh::bridge::diag::RxStats::packet_bad);
    _mgr->free(pkt);
  }
}

void ESPNowBridge::queueReceivedFrame(const uint8_t *mac, const uint8_t *data,
                                      int len) {
  portENTER_CRITICAL(&_rx_mux);
  mesh::bridge::diag::increment(_stats.rx.frames);
  _debug_dirty.store(true, std::memory_order_relaxed);
  if (data == nullptr || len <= 0 || len > MAX_ESPNOW_PACKET_SIZE) {
    mesh::bridge::diag::increment(_stats.rx.badframe);
    portEXIT_CRITICAL(&_rx_mux);
    return;
  }

  // Wrapped deployments share the WiFi channel with unrelated ESP-NOW users.
  // Reject obvious foreign traffic before it can occupy a queue slot.
  if (!mesh::bridge::diag::isProbeCandidate(data, static_cast<size_t>(len))
      && _active_format == mesh::bridge::ESPNOW_FORMAT_WRAPPED
      && (len < BRIDGE_MAGIC_SIZE
          || data[0] != static_cast<uint8_t>(BRIDGE_PACKET_MAGIC >> 8)
          || data[1] != static_cast<uint8_t>(BRIDGE_PACKET_MAGIC & 0xFF))) {
    mesh::bridge::diag::increment(_stats.rx.foreign);
    portEXIT_CRITICAL(&_rx_mux);
    return;
  }

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
    mesh::bridge::diag::increment(_stats.rx.queue_drop);
  }
  portEXIT_CRITICAL(&_rx_mux);
}

bool ESPNowBridge::processReceivedFrame(mesh::Packet* packet_slot, const uint8_t *mac,
                                        const uint8_t *data, size_t len) {
  const auto ingress_source = mesh::bridge::IngressSource::mac(mac);
  IngressSourceScope ingress_scope(*this, ingress_source);
  if (data == nullptr || len == 0 || len > MAX_ESPNOW_PACKET_SIZE) {
    noteRx(&mesh::bridge::diag::RxStats::badframe);
    return false;
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
      receiveMeshPacket(packet_slot, packet, packet_len);
      return true;
    } else if (result == mesh::espnow::ESPNowRawReassemblyResult::REJECTED
               || result ==
                   mesh::espnow::ESPNowRawReassemblyResult::OUTPUT_TOO_SMALL) {
      noteRx(&mesh::bridge::diag::RxStats::badframe);
      BRIDGE_DEBUG_PRINTLN("RX raw fragment rejected, result=%u len=%u\n",
                           (unsigned)result, (unsigned)len);
    }
    return false;
  }

  // Wrapped mode is strict: ignore packets too small to contain its header
  // and checksum rather than trying to auto-detect raw MeshCore traffic.
  if (len < (BRIDGE_MAGIC_SIZE + BRIDGE_CHECKSUM_SIZE)) {
    noteRx(&mesh::bridge::diag::RxStats::badframe);
    BRIDGE_DEBUG_PRINTLN("RX packet too small, len=%u\n", (unsigned)len);
    return false;
  }

  // Check packet header magic
  uint16_t received_magic = (data[0] << 8) | data[1];
  if (received_magic != BRIDGE_PACKET_MAGIC) {
    noteRx(&mesh::bridge::diag::RxStats::foreign);
    BRIDGE_DEBUG_PRINTLN("RX invalid magic 0x%04X\n", received_magic);
    return false;
  }

  // Make a copy we can decrypt
  uint8_t decrypted[MAX_ESPNOW_PACKET_SIZE];
  const size_t encryptedDataLen = len - BRIDGE_MAGIC_SIZE;
  memcpy(decrypted, data + BRIDGE_MAGIC_SIZE, encryptedDataLen);

  // Try to decrypt (checksum + payload)
  if (!xorCrypt(decrypted, encryptedDataLen)) return false;

  // Validate checksum
  uint16_t received_checksum = (decrypted[0] << 8) | decrypted[1];
  const size_t payloadLen = encryptedDataLen - BRIDGE_CHECKSUM_SIZE;

  if (!validateChecksum(decrypted + BRIDGE_CHECKSUM_SIZE, payloadLen, received_checksum)) {
    noteRx(&mesh::bridge::diag::RxStats::checksum_bad);
    // Failed to decrypt - likely from a different network
    BRIDGE_DEBUG_PRINTLN("RX checksum mismatch, rcv=0x%04X\n", received_checksum);
    return false;
  }

  BRIDGE_DEBUG_PRINTLN("RX, payload_len=%u\n", (unsigned)payloadLen);
  receiveMeshPacket(packet_slot, decrypted + BRIDGE_CHECKSUM_SIZE, payloadLen);
  return true;
}

void ESPNowBridge::onDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  (void)mac_addr;
  portENTER_CRITICAL(&_tx_mux);
  if (_tx_waiting && !_tx_callback_done) {
    _tx_callback_status = static_cast<int>(status);
    _tx_callback_done = true;
    if (_tx_count > 0 && _tx_queue[_tx_tail].diagnostic) {
      if (status == ESP_NOW_SEND_SUCCESS) mesh::bridge::diag::increment(_stats.probe.tx_ok);
      else mesh::bridge::diag::increment(_stats.probe.tx_fail);
    } else {
      if (status == ESP_NOW_SEND_SUCCESS) mesh::bridge::diag::increment(_stats.tx.callback_ok);
      else mesh::bridge::diag::increment(_stats.tx.callback_fail);
    }
    _debug_dirty.store(true, std::memory_order_relaxed);
  }
  portEXIT_CRITICAL(&_tx_mux);
}

void ESPNowBridge::sendPacket(mesh::Packet *packet) {
  using mesh::bridge::diag::TxStats;
  noteTx(&TxStats::requested);
  // Guard against uninitialized state
  if (_initialized == false) {
    noteTx(&TxStats::drops);
    return;
  }

  // First validate the packet pointer
  if (!packet) {
    noteTx(&TxStats::drops);
    BRIDGE_DEBUG_PRINTLN("TX invalid packet pointer\n");
    return;
  }

  if (!allowsPacket(packet)) { noteTx(&TxStats::filtered); return; }

  // Core OTA forwarding already bounds repeated requests and rejects longer
  // downstream echoes. Do not permanently suppress its byte-identical retry.
  const bool ota = packet->getPayloadType() == PAYLOAD_TYPE_OTA;
  if (ota || !_seen_packets.wasSeen(packet)) {
    // Check the serialized size before writing into the ESP-NOW-sized buffer.
    const int expectedMeshPacketLen = packet->getRawLength();
    const size_t maxPayload =
        mesh::bridge::espNowMaxMeshPacketSize(_active_format);
    if (expectedMeshPacketLen < 0
        || (size_t)expectedMeshPacketLen > maxPayload) {
      noteTx(&TxStats::oversize);
      BRIDGE_DEBUG_PRINTLN("TX packet too large (payload=%d, max=%u)\n",
                           expectedMeshPacketLen, (unsigned)maxPayload);
      return;
    }

    uint8_t buffer[MAX_TRANS_UNIT];
    uint16_t meshPacketLen = packet->writeTo(buffer);
    if (meshPacketLen != (uint16_t)expectedMeshPacketLen) {
      noteTx(&TxStats::drops);
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
        noteTx(&TxStats::drops);
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
      if (!ota) _seen_packets.markSeen(packet);
      BRIDGE_DEBUG_PRINTLN("TX %s, len=%d, frames=%u\n",
                           mesh::bridge::espNowFormatName(_active_format),
                           meshPacketLen, (unsigned)frames.count);
    } else {
      if (!encoded) noteTx(&TxStats::drops);
      BRIDGE_DEBUG_PRINTLN("TX queue/encode failed!\n");
    }
  } else {
    noteTx(&TxStats::duplicate);
  }
}

void ESPNowBridge::onPacketReceived(mesh::Packet *packet) {
  handleReceivedPacket(packet);
}

void ESPNowBridge::noteRx(uint32_t mesh::bridge::diag::RxStats::* field) {
  portENTER_CRITICAL(&_rx_mux);
  mesh::bridge::diag::increment(_stats.rx.*field);
  portEXIT_CRITICAL(&_rx_mux);
  _debug_dirty.store(true, std::memory_order_relaxed);
}

void ESPNowBridge::noteTx(uint32_t mesh::bridge::diag::TxStats::* field) {
  portENTER_CRITICAL(&_tx_mux);
  mesh::bridge::diag::increment(_stats.tx.*field);
  portEXIT_CRITICAL(&_tx_mux);
  _debug_dirty.store(true, std::memory_order_relaxed);
}

void ESPNowBridge::onIngressResult(IngressResult result) {
  using mesh::bridge::diag::RxStats;
  if (result == IngressResult::Queued) noteRx(&RxStats::delivered);
  else if (result == IngressResult::Duplicate) noteRx(&RxStats::duplicate);
  else noteRx(&RxStats::filtered);
}

mesh::bridge::diag::Stats ESPNowBridge::snapshotStats() {
  portENTER_CRITICAL(&_rx_mux);
  portENTER_CRITICAL(&_tx_mux);
  const mesh::bridge::diag::Stats snapshot = _stats;
  portEXIT_CRITICAL(&_tx_mux);
  portEXIT_CRITICAL(&_rx_mux);
  return snapshot;
}

void ESPNowBridge::recordError(mesh::bridge::diag::Stage stage, int32_t code) {
  portENTER_CRITICAL(&_tx_mux);
  _stats.error.stage = stage;
  _stats.error.code = code;
  _stats.error.at_ms = millis();
  portEXIT_CRITICAL(&_tx_mux);
  _debug_dirty.store(true, std::memory_order_relaxed);
}

void ESPNowBridge::resetStats() {
  portENTER_CRITICAL(&_rx_mux);
  portENTER_CRITICAL(&_tx_mux);
  _stats = mesh::bridge::diag::Stats{};
  _rx_dropped = _rx_dropped_reported = 0;
  _tx_dropped = _tx_dropped_reported = 0;
  portEXIT_CRITICAL(&_tx_mux);
  portEXIT_CRITICAL(&_rx_mux);
  _debug_dirty.store(true, std::memory_order_relaxed);
}

bool ESPNowBridge::sendTest(uint32_t nonce) {
  using namespace mesh::bridge::diag;
  if (!_initialized) {
    recordError(Stage::TestQueue, ESP_ERR_INVALID_STATE);
    return false;
  }
  mesh::espnow::ESPNowRawFrames frames;
  Probe probe;
  probe.nonce = nonce;
  probe.format = _active_format;
  probe.channel = _prefs->bridge_channel;
  if (!encodeProbe(frames.data[0], frames.data.capacity(0), probe)) {
    recordError(Stage::TestQueue, ESP_ERR_INVALID_ARG);
    return false;
  }
  frames.count = 1;
  frames.lengths[0] = ProbeSize;
  const mesh::Packet unused_packet;
  if (!queueTransmitFrames(frames, unused_packet, true)) {
    recordError(Stage::TestQueue, ESP_ERR_ESPNOW_NO_MEM);
    return false;
  }
  portENTER_CRITICAL(&_tx_mux);
  _stats.probe.last_tx_nonce = nonce;
  portEXIT_CRITICAL(&_tx_mux);
  return true;
}

void ESPNowBridge::formatStatus(char* reply, size_t reply_size) {
  uint8_t channel = 0, protocols = 0;
  wifi_second_chan_t secondary = WIFI_SECOND_CHAN_NONE;
  wifi_mode_t mode = WIFI_MODE_NULL;
  wifi_ps_type_t power_save = WIFI_PS_NONE;
  const bool channel_ok = esp_wifi_get_channel(&channel, &secondary) == ESP_OK;
  const bool mode_ok = esp_wifi_get_mode(&mode) == ESP_OK;
  const bool ps_ok = esp_wifi_get_ps(&power_save) == ESP_OK;
  (void)esp_wifi_get_protocol(WIFI_IF_STA, &protocols);
  uint8_t rx_depth, tx_depth;
  portENTER_CRITICAL(&_rx_mux); rx_depth = _rx_count; portEXIT_CRITICAL(&_rx_mux);
  portENTER_CRITICAL(&_tx_mux); tx_depth = _tx_count; portEXIT_CRITICAL(&_tx_mux);
  const auto stats = snapshotStats();
  const char* state = !_initialized ? (_sdk_teardown_failed ? "teardown-error" : "off")
      : (!channel_ok || !mode_ok || !(mode & WIFI_MODE_STA)) ? "driver-error"
      : (channel != _prefs->bridge_channel) ? "channel-mismatch" : "ready";
  snprintf(reply, reply_size,
      "> %s ch=%u/%u fmt=%s src=%s wifi=%s mode=%d ps=%d proto=%02X q=%u/%u last=%s/%ld",
      state, (unsigned)_prefs->bridge_channel, (unsigned)(channel_ok ? channel : 0),
      mesh::bridge::espNowFormatName(_initialized ? _active_format : _prefs->bridge_format),
      _prefs->bridge_pkt_src ? "rx" : "tx",
      (mesh::wireless::control().enabled() & mesh::wireless::WiFi) ? "on" : "off",
      mode_ok ? static_cast<int>(mode) : -1, ps_ok ? static_cast<int>(power_save) : -1,
      (unsigned)protocols, (unsigned)rx_depth, (unsigned)tx_depth,
      mesh::bridge::diag::stageName(stats.error.stage), (long)stats.error.code);
}

void ESPNowBridge::formatStats(const char* page, char* reply, size_t reply_size) {
  const auto s = snapshotStats();
  if (page == nullptr || page[0] == 0) {
    snprintf(reply, reply_size, "> rx=%lu mesh=%lu tx=%lu sent=%lu; pages: mac rx tx drops test",
        (unsigned long)s.rx.frames, (unsigned long)s.rx.delivered,
        (unsigned long)s.tx.queued, (unsigned long)s.tx.callback_ok);
  } else if (strcmp(page, "mac") == 0) {
    uint8_t mac[6];
    if (esp_wifi_get_mac(WIFI_IF_STA, mac) != ESP_OK)
      snprintf(reply, reply_size, "Error: ESP-NOW station MAC unavailable; start the bridge first");
    else snprintf(reply, reply_size, "> mac:%02X:%02X:%02X:%02X:%02X:%02X",
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  } else if (strcmp(page, "rx") == 0) {
    snprintf(reply, reply_size, "> frames=%lu decoded=%lu mesh=%lu crc=%lu bad=%lu parse=%lu",
        (unsigned long)s.rx.frames, (unsigned long)s.rx.packet_ok,
        (unsigned long)s.rx.delivered, (unsigned long)s.rx.checksum_bad,
        (unsigned long)s.rx.badframe, (unsigned long)s.rx.packet_bad);
  } else if (strcmp(page, "tx") == 0) {
    snprintf(reply, reply_size, "> req=%lu queued=%lu start=%lu ok=%lu err=%lu cbfail=%lu timeout=%lu",
        (unsigned long)s.tx.requested, (unsigned long)s.tx.queued,
        (unsigned long)s.tx.start_ok, (unsigned long)s.tx.callback_ok,
        (unsigned long)s.tx.start_fail, (unsigned long)s.tx.callback_fail,
        (unsigned long)s.tx.callback_timeout);
  } else if (strcmp(page, "drops") == 0) {
    snprintf(reply, reply_size, "> rxq=%lu foreign=%lu filter=%lu dupe=%lu txq=%lu txfilter=%lu txdupe=%lu big=%lu",
        (unsigned long)s.rx.queue_drop, (unsigned long)s.rx.foreign,
        (unsigned long)s.rx.filtered, (unsigned long)s.rx.duplicate,
        (unsigned long)s.tx.drops, (unsigned long)s.tx.filtered,
        (unsigned long)s.tx.duplicate, (unsigned long)s.tx.oversize);
  } else if (strcmp(page, "test") == 0) {
    snprintf(reply, reply_size, "> queued=%lu start=%lu ok=%lu fail=%lu timeout=%lu rx=%lu bad=%lu txid=%08lX rxid=%08lX",
        (unsigned long)s.probe.tx_queued, (unsigned long)s.probe.tx_start,
        (unsigned long)s.probe.tx_ok, (unsigned long)s.probe.tx_fail,
        (unsigned long)s.probe.tx_timeout, (unsigned long)s.probe.rx_ok,
        (unsigned long)s.probe.rx_bad, (unsigned long)s.probe.last_tx_nonce,
        (unsigned long)s.probe.last_rx_nonce);
  } else {
    snprintf(reply, reply_size, "Error: use get espnow.stats [mac|rx|tx|drops|test]");
  }
}

void ESPNowBridge::emitDebug() {
#if defined(ARDUINO)
  if (!mesh::isUsbDebugLoggingEnabled()) { _debug_was_enabled = false; return; }
  if (!_debug_was_enabled) _debug_dirty.store(true, std::memory_order_relaxed);
  _debug_was_enabled = true;
  const uint32_t now = millis();
  if (static_cast<uint32_t>(now - _debug_last_at) < 1000
      || !_debug_dirty.exchange(false, std::memory_order_relaxed)) return;
  _debug_last_at = now;
  char status[157];
  formatStatus(status, sizeof(status));
  const auto s = snapshotStats();
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
  mesh::nrf52DebugPrintf("ESPNOW: %s rx=%lu mesh=%lu crc=%lu dup=%lu tx=%lu ok=%lu test.rx=%08lX\n",
      status + 2, (unsigned long)s.rx.frames, (unsigned long)s.rx.delivered,
      (unsigned long)s.rx.checksum_bad, (unsigned long)s.rx.duplicate,
      (unsigned long)s.tx.queued, (unsigned long)s.tx.callback_ok,
      (unsigned long)s.probe.last_rx_nonce);
#else
  char line[256];
  const int size = snprintf(line, sizeof(line),
      "ESPNOW: %s rx=%lu mesh=%lu crc=%lu dup=%lu tx=%lu ok=%lu test.rx=%08lX\n",
      status + 2, (unsigned long)s.rx.frames, (unsigned long)s.rx.delivered,
      (unsigned long)s.rx.checksum_bad, (unsigned long)s.rx.duplicate,
      (unsigned long)s.tx.queued, (unsigned long)s.tx.callback_ok,
      (unsigned long)s.probe.last_rx_nonce);
  if (size > 0 && static_cast<size_t>(size) < sizeof(line)
      && mesh::usbLoggingPort().availableForWrite() >= size)
    mesh::usbLoggingPort().write(reinterpret_cast<const uint8_t*>(line), size);
#endif
#endif
}

#endif
