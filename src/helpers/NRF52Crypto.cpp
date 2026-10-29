#include "NRF52CryptoScratch.h"

#if defined(USE_CC310_HW_CRYPTO)

#include <Adafruit_nRFCrypto.h>
#include <Arduino.h>
#include <new>
#include <string.h>
#include <type_traits>

namespace {

enum CC310State : uint8_t {
  CC310_UNINITIALIZED,
  CC310_AVAILABLE,
  CC310_UNAVAILABLE
};

StaticSemaphore_t cc310_mutex_storage;
SemaphoreHandle_t cc310_mutex = NULL;
CC310State cc310_state = CC310_UNINITIALIZED;

struct HmacScratch {
  CRYS_HASH_Result_t result;
};

// The session mutex already excludes simultaneous SDK operations. Share their
// scratch storage without adding to the loop stack or allocating from the heap.
union CryptoScratch {
  CRYS_ECEDW_TempBuff_t verification;
  SaSiAesUserContext_t aes;
  HmacScratch hmac;
};
static_assert(std::is_trivially_destructible<CryptoScratch>::value,
              "CC310 scratch members must not require destruction");
static_assert(sizeof(CryptoScratch) == sizeof(CRYS_ECEDW_TempBuff_t),
              "Review CC310 scratch layout when SDK workspace sizes change");
CryptoScratch crypto_scratch;

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

CRYS_ECEDW_TempBuff_t* cc310VerificationScratch(const CC310CryptoSession& session) {
  if (!session) return nullptr;
  return ::new (static_cast<void*>(&crypto_scratch.verification)) CRYS_ECEDW_TempBuff_t{};
}

SaSiAesUserContext_t* cc310AesScratch(const CC310CryptoSession& session) {
  if (!session) return nullptr;
  return ::new (static_cast<void*>(&crypto_scratch.aes)) SaSiAesUserContext_t{};
}

CRYS_HASH_Result_t* cc310HmacScratch(const CC310CryptoSession& session) {
  if (!session) return nullptr;
  // Construct a single wrapper object, avoiding placement array-new cookies.
  return &(::new (static_cast<void*>(&crypto_scratch.hmac)) HmacScratch{})->result;
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
