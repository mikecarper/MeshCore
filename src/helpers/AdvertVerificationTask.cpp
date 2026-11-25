#include "AdvertVerificationTask.h"

#if defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM)
#include "Dispatcher.h"
#include "Identity.h"
#include <Arduino.h>
#include <atomic>
#if defined(ESP32_PLATFORM)
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#else
#include <FreeRTOS.h>
#include <queue.h>
#include <task.h>
#endif

namespace mesh {
namespace {
struct Verification {
  const Identity& identity;
  const uint8_t* signature;
  const uint8_t* message;
  int length;
  bool valid = false;
  std::atomic<bool> finished{false};

  Verification(const Identity& id, const uint8_t* sig, const uint8_t* data, int len)
      : identity(id), signature(sig), message(data), length(len) { }
};

void runVerifier(void* queue) {
  for (;;) {
    Verification* work;
    if (xQueueReceive(static_cast<QueueHandle_t>(queue), &work, portMAX_DELAY) != pdTRUE) continue;
    work->valid = work->identity.verify(work->signature, work->message, work->length);
    work->finished.store(true, std::memory_order_release);
  }
}

#if defined(ESP32_PLATFORM) && !CONFIG_FREERTOS_UNICORE
constexpr bool shares_core = false;
#else
constexpr bool shares_core = true;
#endif

// RF and the packet pool stay on the caller. The single worker borrows only
// immutable inputs, which the caller keeps alive until the result is published.
QueueHandle_t startVerifier() {
  auto queue = xQueueCreate(1, sizeof(Verification*));
  if (!queue) return nullptr;
  const UBaseType_t priority = uxTaskPriorityGet(nullptr);
#if defined(ESP32_PLATFORM) && !CONFIG_FREERTOS_UNICORE
  // ESP-IDF task stacks are sized in bytes.
  const auto started = xTaskCreatePinnedToCore(runVerifier, "advert-verify", 5120,
      queue, priority, nullptr, 1 - xPortGetCoreID());
#elif defined(ESP32_PLATFORM)
  const auto started = xTaskCreate(runVerifier, "advert-verify", 5120, queue, priority, nullptr);
#else
  // Adafruit nRF52 uses words. Leave space for the software fallback as well.
  const auto started = xTaskCreate(runVerifier, "advert-verify", 1280, queue, priority, nullptr);
#endif
  if (started == pdPASS) return queue;
  vQueueDelete(queue);
  return nullptr;
}
}  // namespace

bool verifyAdvertWhileScanning(Radio& radio, const Identity& identity,
                               const uint8_t* signature, const uint8_t* message, int length) {
  const auto* profiles = radio.profiles();
  if (!profiles || !profiles->enabled() || profiles->secondaryOnly()) {
    return identity.verify(signature, message, length);
  }

  static const QueueHandle_t queue = startVerifier();
  Verification work(identity, signature, message, length);
  Verification* request = &work;
  if (!queue || xQueueSend(queue, &request, 0) != pdTRUE) {
    return identity.verify(signature, message, length);
  }

  while (!work.finished.load(std::memory_order_acquire)) {
    // Service RF only. Calling Dispatcher here would re-enter packet parsing
    // and could overwrite the original packet's receive context. A new RX
    // completion stays pending for the normal receive path to drain.
    radio.loop();
    if (shares_core) {
      // Equal-priority tasks share the RTOS tick. Give the verifier an early
      // turn only when the scanner has an idle window it can afford to yield.
      uint32_t idle_ms = 0;
      if (!configUSE_PREEMPTION || !configUSE_TIME_SLICING
          || (radio.getProfileIdleDelay(idle_ms) && idle_ms)) taskYIELD();
    }
  }
  return work.valid;
}
}  // namespace mesh
#endif
