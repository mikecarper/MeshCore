#include "ESPNOWRadio.h"
#include <esp_now.h>
#include <esp_idf_version.h>
#include <WiFi.h>
#include <esp_wifi.h>
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  #include <esp_mac.h>
#endif

static uint8_t broadcastAddress[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
static esp_now_peer_info_t peerInfo;
static volatile bool is_send_complete = false;
static esp_err_t last_send_result;
static uint8_t rx_buf[256];
static uint8_t last_rx_len = 0;

// callback when data is sent
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
static void OnDataSent(const esp_now_send_info_t *info,
                       esp_now_send_status_t status) {
  (void)info;
#else
static void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  (void)mac_addr;
#endif
  is_send_complete = true;
  ESPNOW_DEBUG_PRINTLN("Send Status: %d", (int)status);
}

#if ESP_ARDUINO_VERSION_MAJOR >= 3
static void OnDataRecv(const esp_now_recv_info_t *info, const uint8_t *data,
                       int len) {
  (void)info;
#else
static void OnDataRecv(const uint8_t *mac, const uint8_t *data, int len) {
  (void)mac;
#endif
  ESPNOW_DEBUG_PRINTLN("Recv: len = %d", len);
  memcpy(rx_buf, data, len);
  last_rx_len = len;
}

void ESPNOWRadio::init() {
  // Set device as a Wi-Fi Station
  // ESP-NOW's LR protocol is local to this radio transport. Keeping WiFi
  // driver writes in RAM prevents a later conventional WiFi image from
  // inheriting the proprietary protocol bit through NVS.
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  // Long Range mode
  esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_LR);

  // Init ESP-NOW
  if (esp_now_init() != ESP_OK) {
    ESPNOW_DEBUG_PRINTLN("Error initializing ESP-NOW");
    return;
  }

  esp_wifi_set_max_tx_power(80);  // should be 20dBm

  esp_now_register_send_cb(OnDataSent);
  esp_now_register_recv_cb(OnDataRecv);

  // Register peer
  memcpy(peerInfo.peer_addr, broadcastAddress, 6);
  peerInfo.channel = 0;
  peerInfo.encrypt = false;

  is_send_complete = true;

  // Add peer        
  if (esp_now_add_peer(&peerInfo) == ESP_OK) {
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
  // Send message via ESP-NOW
  is_send_complete = false;
  esp_err_t result = esp_now_send(broadcastAddress, bytes, len);
  if (result == ESP_OK) {
    n_sent++;
    ESPNOW_DEBUG_PRINTLN("Send success");
    return true;
  }
  last_send_result = result;
  is_send_complete = true;
  ESPNOW_DEBUG_PRINTLN("Send failed: %d", result);
  return false;
}

bool ESPNOWRadio::isSendComplete() {
  return is_send_complete;
}
void ESPNOWRadio::onSendFinished() {
  is_send_complete = true;
}

bool ESPNOWRadio::isInRecvMode() const {
  return is_send_complete;    // if NO send in progress, then we're in Rx mode
}

float ESPNOWRadio::getLastRSSI() const { return 0; }
float ESPNOWRadio::getLastSNR() const { return 0; }

int ESPNOWRadio::recvRaw(uint8_t* bytes, int sz) {
  int len = last_rx_len;
  if (last_rx_len > 0) {
    memcpy(bytes, rx_buf, last_rx_len);
    last_rx_len = 0;
    n_recv++;
  }
  return len;
}

uint32_t ESPNOWRadio::getEstAirtimeFor(int len_bytes) {
  return 4;  // Fast AF
}
