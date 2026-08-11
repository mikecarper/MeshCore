#include "NRF52Crypto.h"

#if defined(USE_CC310_HW_CRYPTO)

#include <Adafruit_nRFCrypto.h>
#include <Arduino.h>
#include <string.h>

namespace {

enum CC310State : uint8_t {
  CC310_UNINITIALIZED,
  CC310_AVAILABLE,
  CC310_UNAVAILABLE
};

StaticSemaphore_t cc310_mutex_storage;
SemaphoreHandle_t cc310_mutex = NULL;
CC310State cc310_state = CC310_UNINITIALIZED;

SemaphoreHandle_t getCC310Mutex() {
  if (cc310_mutex == NULL) {
    // nRF52840 is single-core. The critical section makes the lazy static
    // mutex construction safe if two tasks make their first crypto call at
    // the same time.
    taskENTER_CRITICAL();
    if (cc310_mutex == NULL) {
      cc310_mutex = xSemaphoreCreateMutexStatic(&cc310_mutex_storage);
    }
    taskEXIT_CRITICAL();
  }
  return cc310_mutex;
}

} // namespace

namespace mesh {

CC310CryptoSession::CC310CryptoSession()
    : _locked(false), _available(false) {
  // FreeRTOS mutex APIs and the CC310 driver are not ISR-safe.
  if (isInISR()) return;

  SemaphoreHandle_t mutex = getCC310Mutex();
  if (mutex == NULL || xSemaphoreTake(mutex, portMAX_DELAY) != pdTRUE) return;
  _locked = true;

  if (cc310_state == CC310_UNINITIALIZED) {
    // Adafruit_nRFCrypto::begin() also initializes its DRBG. Keep it alive for
    // the process lifetime: end()/begin() would repeatedly reset that state.
    cc310_state = nRFCrypto.begin() ? CC310_AVAILABLE : CC310_UNAVAILABLE;
  }
  _available = cc310_state == CC310_AVAILABLE;
}

CC310CryptoSession::~CC310CryptoSession() {
  if (_locked) xSemaphoreGive(cc310_mutex);
}

bool initializeCC310Crypto() {
  CC310CryptoSession session;
  return static_cast<bool>(session);
}

void mixCC310Random(uint8_t* dest, size_t size) {
  if (dest == NULL || size == 0) return;

  CC310CryptoSession session;
  if (!session) return;

  uint8_t hardware_random[32];
  size_t offset = 0;
  while (offset < size) {
    const size_t remaining = size - offset;
    const uint16_t chunk = static_cast<uint16_t>(
        remaining < sizeof(hardware_random) ? remaining : sizeof(hardware_random));
    if (!nRFCrypto.Random.generate(hardware_random, chunk)) break;
    for (uint16_t i = 0; i < chunk; ++i) {
      dest[offset + i] ^= hardware_random[i];
    }
    offset += chunk;
  }
  memset(hardware_random, 0, sizeof(hardware_random));
}

} // namespace mesh

#endif
