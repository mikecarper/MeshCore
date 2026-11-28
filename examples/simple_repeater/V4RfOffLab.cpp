#if defined(MC_V4_RF24_HARD_OFF)

#include "V4RfOffLab.h"
#include <atomic>

namespace {

constexpr bool disabled = MC_V4_RF24_HARD_OFF != 0;
std::atomic<unsigned> wifiInitCalls{0};
std::atomic<unsigned> wifiStartCalls{0};
std::atomic<unsigned> espNowInitCalls{0};
std::atomic<bool> espNowInitialized{false};
std::atomic<unsigned> btInitCalls{0};
std::atomic<unsigned> btEnableCalls{0};

}  // namespace

extern "C" {
esp_err_t __real_esp_wifi_init(const wifi_init_config_t*);
esp_err_t __real_esp_wifi_start();
esp_err_t __real_esp_now_init();
esp_err_t __real_esp_now_deinit();
esp_err_t __real_esp_bt_controller_init(esp_bt_controller_config_t*);
esp_err_t __real_esp_bt_controller_enable(esp_bt_mode_t);

esp_err_t __wrap_esp_wifi_init(const wifi_init_config_t* config) {
  wifiInitCalls.fetch_add(1, std::memory_order_relaxed);
  return disabled ? ESP_ERR_WIFI_NOT_INIT : __real_esp_wifi_init(config);
}

esp_err_t __wrap_esp_wifi_start() {
  wifiStartCalls.fetch_add(1, std::memory_order_relaxed);
  return disabled ? ESP_ERR_WIFI_NOT_INIT : __real_esp_wifi_start();
}

esp_err_t __wrap_esp_now_init() {
  espNowInitCalls.fetch_add(1, std::memory_order_relaxed);
  if (disabled) return ESP_ERR_ESPNOW_NOT_INIT;
  const esp_err_t status = __real_esp_now_init();
  if (status == ESP_OK) espNowInitialized.store(true, std::memory_order_relaxed);
  return status;
}

esp_err_t __wrap_esp_now_deinit() {
  const esp_err_t status = __real_esp_now_deinit();
  if (status == ESP_OK) espNowInitialized.store(false, std::memory_order_relaxed);
  return status;
}

esp_err_t __wrap_esp_bt_controller_init(esp_bt_controller_config_t* config) {
  btInitCalls.fetch_add(1, std::memory_order_relaxed);
  return disabled ? ESP_ERR_NOT_SUPPORTED : __real_esp_bt_controller_init(config);
}

esp_err_t __wrap_esp_bt_controller_enable(esp_bt_mode_t mode) {
  btEnableCalls.fetch_add(1, std::memory_order_relaxed);
  return disabled ? ESP_ERR_NOT_SUPPORTED : __real_esp_bt_controller_enable(mode);
}
}

void v4rf24::shutDown() {
  if (!disabled) return;
  if (espNowInitialized.load(std::memory_order_relaxed)) esp_now_deinit();
  wifi_mode_t mode = WIFI_MODE_NULL;
  if (esp_wifi_get_mode(&mode) == ESP_OK) {
    esp_wifi_stop();
    esp_wifi_deinit();
  }
  if (esp_bt_controller_get_status() == ESP_BT_CONTROLLER_STATUS_ENABLED)
    esp_bt_controller_disable();
  if (esp_bt_controller_get_status() == ESP_BT_CONTROLLER_STATUS_INITED)
    esp_bt_controller_deinit();
}

void v4rf24::printStatus(char* reply, size_t size) {
  wifi_mode_t wifiMode = WIFI_MODE_NULL;
  const esp_err_t wifiResult = esp_wifi_get_mode(&wifiMode);
  const bool espNow = espNowInitialized.load(std::memory_order_relaxed);
  snprintf(reply, size, "> hard=%u wifi=%d mode=%d now=%d bt=%d wi=%u ws=%u ni=%u bi=%u be=%u",
           static_cast<unsigned>(disabled), static_cast<int>(wifiResult),
           static_cast<int>(wifiMode), static_cast<int>(espNow),
           static_cast<int>(esp_bt_controller_get_status()),
           wifiInitCalls.load(std::memory_order_relaxed),
           wifiStartCalls.load(std::memory_order_relaxed),
           espNowInitCalls.load(std::memory_order_relaxed),
           btInitCalls.load(std::memory_order_relaxed),
           btEnableCalls.load(std::memory_order_relaxed));
}

#endif
