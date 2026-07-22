#if defined(ESP_PLATFORM) && defined(PORTABLE_MQTT_OBSERVER)

#include <cstring>
#include <esp_err.h>

extern "C" void mbedtls_strerror(int, char* buffer, size_t buffer_size) {
  if (buffer_size == 0) return;
  const char message[] = "mbedTLS error";
  std::strncpy(buffer, message, buffer_size - 1);
  buffer[buffer_size - 1] = '\0';
}

extern "C" const char* esp_err_to_name(esp_err_t) {
  return "ESP error";
}

extern "C" const char* esp_err_to_name_r(esp_err_t, char* buffer, size_t buffer_size) {
  if (buffer_size == 0) return buffer;
  const char message[] = "ESP error";
  std::strncpy(buffer, message, buffer_size - 1);
  buffer[buffer_size - 1] = '\0';
  return buffer;
}

#endif
