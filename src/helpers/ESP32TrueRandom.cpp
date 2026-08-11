#include "ESP32TrueRandom.h"

#if defined(ESP32_PLATFORM)

#include <bootloader_random.h>
#include <esp_random.h>
#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <mbedtls/platform_util.h>

#include "IdentityGeneration.h"

namespace {

constexpr size_t TRUE_RANDOM_POOL_SIZE =
    SEED_SIZE * mesh::MAX_LOCAL_IDENTITY_GENERATION_ATTEMPTS;

enum TrueRandomState : uint8_t {
  TRUE_RANDOM_UNINITIALIZED,
  TRUE_RANDOM_READY,
  TRUE_RANDOM_DISCARDED
};

uint8_t true_random_pool[TRUE_RANDOM_POOL_SIZE];
size_t true_random_offset = 0;
TrueRandomState true_random_state = TRUE_RANDOM_UNINITIALIZED;

StaticSemaphore_t true_random_mutex_storage;
SemaphoreHandle_t true_random_mutex = NULL;
portMUX_TYPE true_random_mutex_init_mux = portMUX_INITIALIZER_UNLOCKED;

SemaphoreHandle_t getTrueRandomMutex() {
  // Always enter the cross-core critical section: an unlocked preliminary
  // pointer read would itself race a first caller creating the mutex.
  portENTER_CRITICAL(&true_random_mutex_init_mux);
  if (true_random_mutex == NULL) {
    true_random_mutex = xSemaphoreCreateMutexStatic(&true_random_mutex_storage);
  }
  SemaphoreHandle_t mutex = true_random_mutex;
  portEXIT_CRITICAL(&true_random_mutex_init_mux);
  return mutex;
}

bool tryMixESP32TrueRandom(uint8_t* dest, size_t size) {
  if (size == 0) return true;
  if (dest == NULL) return false;

  SemaphoreHandle_t mutex = getTrueRandomMutex();
  if (mutex == NULL || xSemaphoreTake(mutex, portMAX_DELAY) != pdTRUE) return false;

  const bool valid_offset = true_random_offset <= sizeof(true_random_pool);
  const size_t available = valid_offset
      ? sizeof(true_random_pool) - true_random_offset
      : 0;
  if (true_random_state != TRUE_RANDOM_READY || size > available) {
    xSemaphoreGive(mutex);
    return false;
  }

  for (size_t i = 0; i < size; ++i) {
    dest[i] ^= true_random_pool[true_random_offset + i];
  }
  mbedtls_platform_zeroize(&true_random_pool[true_random_offset], size);
  true_random_offset += size;

  xSemaphoreGive(mutex);
  return true;
}

} // namespace

namespace mesh {

void initializeESP32TrueRandom() {
  SemaphoreHandle_t mutex = getTrueRandomMutex();
  if (mutex == NULL || xSemaphoreTake(mutex, portMAX_DELAY) != pdTRUE) return;

  if (true_random_state != TRUE_RANDOM_UNINITIALIZED) {
    xSemaphoreGive(mutex);
    return;
  }

  // ESP-IDF guarantees true RNG output while this SAR ADC entropy source is
  // enabled. Arduino's init hook below runs before variant and board setup.
  bootloader_random_enable();
  esp_fill_random(true_random_pool, sizeof(true_random_pool));
  bootloader_random_disable();

  true_random_offset = 0;
  true_random_state = TRUE_RANDOM_READY;
  xSemaphoreGive(mutex);
}

void mixESP32TrueRandom(uint8_t* dest, size_t size) {
  if (tryMixESP32TrueRandom(dest, size)) return;

  // A software or radio source may already have populated dest. Never let
  // those bytes escape as pseudo-only identity material.
  if (dest != NULL && size > 0) mbedtls_platform_zeroize(dest, size);
  discardESP32TrueRandom();
  esp_restart();
}

void discardESP32TrueRandom() {
  SemaphoreHandle_t mutex = getTrueRandomMutex();
  if (mutex == NULL || xSemaphoreTake(mutex, portMAX_DELAY) != pdTRUE) {
    mbedtls_platform_zeroize(true_random_pool, sizeof(true_random_pool));
    true_random_offset = sizeof(true_random_pool);
    true_random_state = TRUE_RANDOM_DISCARDED;
    return;
  }

  mbedtls_platform_zeroize(true_random_pool, sizeof(true_random_pool));
  true_random_offset = sizeof(true_random_pool);
  true_random_state = TRUE_RANDOM_DISCARDED;
  xSemaphoreGive(mutex);
}

} // namespace mesh

// Arduino-ESP32 calls this weak hook before initVariant() and setup(). Defining
// it here makes entropy capture independent of every board subclass's begin()
// ordering, including future variants which initialize ADC or RF early.
extern "C" void init() {
  mesh::initializeESP32TrueRandom();
}

#endif
