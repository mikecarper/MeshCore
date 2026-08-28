#include "ESPNOWRadio.h"
#include <esp_now.h>
#include <esp_idf_version.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <helpers/ESPNowRawFragmentation.h>
#include <helpers/esp32/WiFiRadioPolicy.h>
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  #include <esp_mac.h>
#endif

static uint8_t broadcastAddress[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
static esp_now_peer_info_t peerInfo;
static volatile bool is_send_complete = false;
static esp_err_t last_send_result;
static volatile esp_now_send_status_t last_send_status =
    ESP_NOW_SEND_SUCCESS;
static bool tx_second_pending = false;
static uint16_t tx_second_length = 0;
static uint8_t tx_second_frame[mesh::espnow::ESPNOW_RAW_MAX_FRAME_SIZE];
static constexpr uint16_t RX_FRAME_MAX =
    mesh::espnow::ESPNOW_RAW_MAX_FRAME_SIZE;
static constexpr uint8_t RX_QUEUE_DEPTH = 4;
static uint8_t rx_frames[RX_QUEUE_DEPTH][RX_FRAME_MAX];
static uint16_t rx_lengths[RX_QUEUE_DEPTH];
static uint8_t rx_sources[RX_QUEUE_DEPTH]
                         [mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE];
static volatile uint8_t rx_head = 0;
static volatile uint8_t rx_tail = 0;
static volatile uint8_t rx_count = 0;
static volatile uint32_t rx_dropped = 0;
static uint32_t rx_dropped_reported = 0;
static portMUX_TYPE rx_mux = portMUX_INITIALIZER_UNLOCKED;
static mesh::espnow::ESPNowRawReassembler rx_reassembler;

// callback when data is sent
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
static void OnDataSent(const esp_now_send_info_t *info,
                       esp_now_send_status_t status) {
  (void)info;
#else
static void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  (void)mac_addr;
#endif
  last_send_status = status;
  is_send_complete = true;
  ESPNOW_DEBUG_PRINTLN("Send Status: %d", (int)status);
}

#if ESP_ARDUINO_VERSION_MAJOR >= 3
static void OnDataRecv(const esp_now_recv_info_t *info, const uint8_t *data,
                       int len) {
  const uint8_t *mac = info != nullptr ? info->src_addr : nullptr;
#else
static void OnDataRecv(const uint8_t *mac, const uint8_t *data, int len) {
#endif
  ESPNOW_DEBUG_PRINTLN("Recv: len = %d", len);
  portENTER_CRITICAL(&rx_mux);
  if (data != nullptr && len > 0 && len <= RX_FRAME_MAX
      && rx_count < RX_QUEUE_DEPTH) {
    memcpy(rx_frames[rx_head], data, static_cast<size_t>(len));
    rx_lengths[rx_head] = static_cast<uint16_t>(len);
    if (mac != nullptr) {
      memcpy(rx_sources[rx_head], mac,
             mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE);
    } else {
      memset(rx_sources[rx_head], 0,
             mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE);
    }
    rx_head = static_cast<uint8_t>((rx_head + 1) % RX_QUEUE_DEPTH);
    rx_count++;
  } else {
    rx_dropped++;
  }
  portEXIT_CRITICAL(&rx_mux);
}

void ESPNOWRadio::init() {
  portENTER_CRITICAL(&rx_mux);
  rx_head = 0;
  rx_tail = 0;
  rx_count = 0;
  rx_dropped = 0;
  portEXIT_CRITICAL(&rx_mux);
  rx_dropped_reported = 0;
  rx_reassembler.reset();
  tx_second_pending = false;
  tx_second_length = 0;
  last_send_status = ESP_NOW_SEND_SUCCESS;

  // Set device as a Wi-Fi Station
  // ESP-NOW's LR protocol is local to this radio transport. Keeping WiFi
  // driver writes in RAM prevents a later conventional WiFi image from
  // inheriting the proprietary protocol bit through NVS.
  WiFi.persistent(false);
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  // Arduino defaults station auto-reconnect to enabled. Disable it before the
  // driver starts so credentials left by another image cannot pull the radio
  // away from the configured mesh channel behind this policy's back.
  WiFi.setAutoReconnect(false);
#endif
  WiFi.mode(WIFI_STA);
#if defined(MESH_ESPNOW_RADIO) && MESH_ESPNOW_RADIO
  // Full Companion also exposes ordinary WiFi. Preserve B/G/N for normal
  // access points while retaining LR for ESP-NOW, and pin both transports to
  // one channel because an ESP32 cannot associate and send ESP-NOW on two
  // different channels simultaneously.
  if (mesh::wifi::applyProtocolMask(WIFI_IF_STA) != ESP_OK) {
    ESPNOW_DEBUG_PRINTLN("Error configuring ESP-NOW/WiFi coexistence");
    return;
  }
#else
  // Dedicated ESP-NOW targets retain the original LR-only behavior.
  if (esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_LR) != ESP_OK) {
    ESPNOW_DEBUG_PRINTLN("Error configuring ESP-NOW LR protocol");
    return;
  }
#endif
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  if (mesh::wifi::restoreEspNowChannel() != ESP_OK) {
    ESPNOW_DEBUG_PRINTLN("Error setting primary ESP-NOW channel");
    return;
  }
#endif

  // Init ESP-NOW
  if (esp_now_init() != ESP_OK) {
    ESPNOW_DEBUG_PRINTLN("Error initializing ESP-NOW");
    return;
  }

  esp_wifi_set_max_tx_power(80);  // should be 20dBm

  esp_now_register_send_cb(OnDataSent);
  esp_now_register_recv_cb(OnDataRecv);

  // Register peer
  memset(&peerInfo, 0, sizeof(peerInfo));
  memcpy(peerInfo.peer_addr, broadcastAddress, 6);
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  // Zero follows the station/AP's current channel. The policy pins that radio
  // channel and rejects off-channel infrastructure associations, avoiding a
  // stale fixed peer entry after a configured-channel reboot.
#endif
  peerInfo.channel = 0;
  peerInfo.ifidx = WIFI_IF_STA;
  peerInfo.encrypt = false;

  is_send_complete = true;

  // Add peer        
  if (esp_now_add_peer(&peerInfo) == ESP_OK) {
#if defined(MESH_ESPNOW_RADIO) && MESH_ESPNOW_RADIO
    // Enabling interoperable B/G/N alongside LR changes the interface's
    // default rate selection. Force ESP-NOW packets back to the legacy LR rate
    // so Full images remain compatible with existing LR-only mesh nodes.
  #if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 2, 0)
    esp_now_rate_config_t rate_config = {};
    rate_config.phymode = WIFI_PHY_MODE_LR;
    rate_config.rate = WIFI_PHY_RATE_LORA_250K;
    rate_config.ersu = false;
    rate_config.dcm = false;
    if (esp_now_set_peer_rate_config(broadcastAddress, &rate_config) != ESP_OK) {
      ESPNOW_DEBUG_PRINTLN("Error configuring ESP-NOW LR peer rate");
    }
  #else
    if (esp_wifi_config_espnow_rate(WIFI_IF_STA, WIFI_PHY_RATE_LORA_250K)
        != ESP_OK) {
      ESPNOW_DEBUG_PRINTLN("Error configuring ESP-NOW LR interface rate");
    }
  #endif
#endif
    ESPNOW_DEBUG_PRINTLN("init success");
  } else {
   // ESPNOW_DEBUG_PRINTLN("Failed to add peer");
  }
}

uint32_t ESPNOWRadio::getRngSeed() {
  return millis() + intID();  // TODO: where to get some entropy?
}

bool ESPNOWRadio::setTxPower(int8_t dbm) {
  return esp_wifi_set_max_tx_power(dbm * 4) == ESP_OK;
}

uint32_t ESPNOWRadio::intID() {
  uint8_t mac[8];
  memset(mac, 0, sizeof(mac));
  esp_efuse_mac_get_default(mac);
  uint32_t n, m;
  memcpy(&n, &mac[0], 4);
  memcpy(&m, &mac[4], 4);
  
  return n + m;
}

bool ESPNOWRadio::startSendRaw(const uint8_t* bytes, int len) {
  mesh::espnow::ESPNowRawFrames frames;
  if (len < 0
      || !mesh::espnow::encodeEspNowRawFrames(
          bytes, static_cast<size_t>(len), frames)) {
    ESPNOW_DEBUG_PRINTLN("Invalid TX length: %d", len);
    return false;
  }

  tx_second_pending = frames.count == 2;
  tx_second_length = 0;
  if (tx_second_pending) {
    tx_second_length = frames.lengths[1];
    memcpy(tx_second_frame, frames.data[1], tx_second_length);
  }

  // Send the first (or only) message via ESP-NOW. isSendComplete() submits a
  // second fragment only after this frame's callback, preserving Espressif's
  // required callback ordering without blocking the mesh dispatcher.
  last_send_status = ESP_NOW_SEND_SUCCESS;
  is_send_complete = false;
  esp_err_t result = esp_now_send(
      broadcastAddress, frames.data[0], frames.lengths[0]);
  if (result == ESP_OK) {
    n_sent++;
    ESPNOW_DEBUG_PRINTLN("Send started: frames=%u raw_len=%d",
                         (unsigned)frames.count, len);
    return true;
  }
  last_send_result = result;
  tx_second_pending = false;
  tx_second_length = 0;
  is_send_complete = true;
  ESPNOW_DEBUG_PRINTLN("Send failed: %d", result);
  return false;
}

bool ESPNOWRadio::isSendComplete() {
  if (!is_send_complete) return false;
  if (last_send_status != ESP_NOW_SEND_SUCCESS) {
    // A failed callback means the logical packet was not transmitted. Do not
    // send an orphan second fragment; remaining incomplete lets Dispatcher use
    // its bounded timeout/retry path.
    tx_second_pending = false;
    tx_second_length = 0;
    is_send_complete = false;
    ESPNOW_DEBUG_PRINTLN("Send callback failed: %d", (int)last_send_status);
    return false;
  }
  if (tx_second_pending) {
    tx_second_pending = false;
    last_send_status = ESP_NOW_SEND_SUCCESS;
    is_send_complete = false;
    const esp_err_t result = esp_now_send(
        broadcastAddress, tx_second_frame, tx_second_length);
    if (result != ESP_OK) {
      // Leave the operation incomplete. Dispatcher will use its bounded radio
      // timeout/retry path instead of reporting a partially sent packet as a
      // success.
      last_send_result = result;
      tx_second_length = 0;
      ESPNOW_DEBUG_PRINTLN("Second fragment failed to start: %d", result);
      return false;
    }
    tx_second_length = 0;
    ESPNOW_DEBUG_PRINTLN("Second fragment started");
    return false;
  }
  return is_send_complete;
}
void ESPNOWRadio::onSendFinished() {
  tx_second_pending = false;
  tx_second_length = 0;
  last_send_status = ESP_NOW_SEND_SUCCESS;
  is_send_complete = true;
}

bool ESPNOWRadio::isInRecvMode() const {
  return is_send_complete && !tx_second_pending;
}

float ESPNOWRadio::getLastRSSI() const { return 0; }
float ESPNOWRadio::getLastSNR() const { return 0; }

int ESPNOWRadio::recvRaw(uint8_t* bytes, int sz) {
  if (bytes == nullptr || sz <= 0) return 0;

  uint32_t dropped = 0;
  portENTER_CRITICAL(&rx_mux);
  dropped = rx_dropped;
  portEXIT_CRITICAL(&rx_mux);

  if (dropped != rx_dropped_reported) {
    n_recv_errors += dropped - rx_dropped_reported;
    ESPNOW_DEBUG_PRINTLN("RX queue/length drops: %lu",
                         (unsigned long)dropped);
    rx_dropped_reported = dropped;
  }

  // Drain fragment-only and malformed frames in one dispatcher poll. This
  // lets a queued two-frame packet become one ordinary MeshCore receive while
  // retaining the callback's fixed, bounded handoff queue.
  for (uint8_t processed = 0; processed < RX_QUEUE_DEPTH; ++processed) {
    uint8_t frame[RX_FRAME_MAX];
    uint8_t source[mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE];
    uint16_t frame_len = 0;

    portENTER_CRITICAL(&rx_mux);
    if (rx_count > 0) {
      frame_len = rx_lengths[rx_tail];
      if (frame_len > 0 && frame_len <= sizeof(frame)) {
        memcpy(frame, rx_frames[rx_tail], frame_len);
        memcpy(source, rx_sources[rx_tail], sizeof(source));
      } else {
        frame_len = 0;
      }
      rx_tail = static_cast<uint8_t>((rx_tail + 1) % RX_QUEUE_DEPTH);
      rx_count--;
    }
    portEXIT_CRITICAL(&rx_mux);

    if (frame_len == 0) break;
    size_t packet_len = 0;
    const mesh::espnow::ESPNowRawReassemblyResult result =
        rx_reassembler.acceptFrame(
            source, frame, frame_len, millis(), bytes,
            static_cast<size_t>(sz), packet_len);
    if (result == mesh::espnow::ESPNowRawReassemblyResult::PASSTHROUGH
        || result ==
            mesh::espnow::ESPNowRawReassemblyResult::PACKET_COMPLETE) {
      n_recv++;
      return static_cast<int>(packet_len);
    }
    if (result == mesh::espnow::ESPNowRawReassemblyResult::REJECTED
        || result ==
            mesh::espnow::ESPNowRawReassemblyResult::OUTPUT_TOO_SMALL) {
      n_recv_errors++;
      ESPNOW_DEBUG_PRINTLN("RX fragment rejected: result=%u len=%u size=%d",
                           (unsigned)result, (unsigned)frame_len, sz);
    }
  }
  return 0;
}

uint32_t ESPNOWRadio::getEstAirtimeFor(int len_bytes) {
  if (len_bytes > static_cast<int>(
          mesh::espnow::ESPNOW_RAW_MAX_FRAME_SIZE)) {
    // A maximum raw packet becomes one full ESP-NOW LR frame plus one short
    // frame. Include callback scheduling margin for both transmissions.
    return 40;
  }
  // At the forced 250-kbit/s LR rate, a 250-byte payload alone takes 8 ms.
  // Dispatcher applies a 1.5x deadline multiplier, so 20 ms leaves room for
  // ESP-NOW/MAC overhead and callback scheduling without false timeouts.
  return 20;
}
