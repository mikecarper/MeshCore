#pragma once

#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
#include <sdkconfig.h>
#endif

#ifndef MESH_REPEATER_PSRAM_TABLES
  #if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM) \
      && defined(CONFIG_IDF_TARGET_ESP32S3) && CONFIG_IDF_TARGET_ESP32S3 \
      && defined(MESH_CLIENT_REPEATER_ONLY) && MESH_CLIENT_REPEATER_ONLY
    #define MESH_REPEATER_PSRAM_TABLES 1
  #else
    #define MESH_REPEATER_PSRAM_TABLES 0
  #endif
#endif

#if MESH_REPEATER_PSRAM_TABLES != 0 && MESH_REPEATER_PSRAM_TABLES != 1
#error "MESH_REPEATER_PSRAM_TABLES must be 0 or 1"
#endif

#ifndef MESH_REPEATER_REQUIRE_PSRAM
#define MESH_REPEATER_REQUIRE_PSRAM 0
#endif
#if MESH_REPEATER_REQUIRE_PSRAM != 0 && MESH_REPEATER_REQUIRE_PSRAM != 1
#error "MESH_REPEATER_REQUIRE_PSRAM must be 0 or 1"
#endif
#if MESH_REPEATER_REQUIRE_PSRAM && (!defined(ESP32_PLATFORM) \
    || !defined(BOARD_HAS_PSRAM) || !MESH_REPEATER_PSRAM_TABLES)
#error "Required repeater PSRAM needs an ESP32 PSRAM table build"
#endif

#if MESH_REPEATER_PSRAM_TABLES
#if !defined(ESP32_PLATFORM) || !defined(BOARD_HAS_PSRAM)
#error "Repeater PSRAM tables require an ESP32 target with PSRAM support"
#endif
#if !defined(MESH_CLIENT_REPEATER_ONLY) || !MESH_CLIENT_REPEATER_ONLY
#error "Repeater PSRAM tables require the repeater startup path"
#endif

#include <sdkconfig.h>
#if !defined(CONFIG_IDF_TARGET_ESP32S3) || !CONFIG_IDF_TARGET_ESP32S3 \
    || !defined(CONFIG_SPIRAM_USE_MALLOC) || !CONFIG_SPIRAM_USE_MALLOC
#error "Repeater PSRAM tables require ESP32-S3 with PSRAM heaps"
#endif

#include <esp_heap_caps.h>
#include <helpers/MQTTConnectionAdmission.h>
#include <stdio.h>
#include <stdlib.h>
#include <type_traits>
#if MESH_REPEATER_REQUIRE_PSRAM
// IDF 5 (Arduino 3) exports this predicate here; IDF 4 (Arduino 2)
// defines the same predicate in the SoC memory-types header instead.
#if __has_include(<esp_memory_utils.h>)
#include <esp_memory_utils.h>
#else
#include <soc/soc_memory_types.h>
#endif
#endif

namespace mesh {

// Own only CPU data here. ISR state, locks, task stacks and DMA buffers retain
// their existing internal storage. Allocate from begin(), after initArduino()
// initializes both QSPI and OPI PSRAM. Global constructors own only a pointer.
template<typename T> class RepeaterPsramStorage {
  static_assert(std::is_trivial<T>::value,
                "Repeater PSRAM tables require zero-initialized trivial data");
  T* value_ = nullptr;
public:
  RepeaterPsramStorage() = default;
  ~RepeaterPsramStorage() { if (value_) heap_caps_free(value_); }
  RepeaterPsramStorage(const RepeaterPsramStorage&) = delete;
  RepeaterPsramStorage& operator=(const RepeaterPsramStorage&) = delete;

  bool begin(const char* label = "repeater table") {
    if (value_) return true;
    value_ = static_cast<T*>(heap_caps_calloc(
        1, sizeof(T), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
#if MESH_REPEATER_REQUIRE_PSRAM
    // This build budgets these complete tables in external memory. Never
    // silently spend the internal reserve needed by radio/network owners.
    if (value_ && !esp_ptr_external_ram(value_)) {
      heap_caps_free(value_);
      value_ = nullptr;
    }
#else
    if (!value_) {
      const uint32_t caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA;
      if (MQTTConnectionAdmission::canFallback(sizeof(T),
              heap_caps_get_free_size(caps), heap_caps_get_largest_free_block(caps))) {
        value_ = static_cast<T*>(heap_caps_calloc(
            1, sizeof(T), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
      }
    }
#endif
    if (!value_) {
      fprintf(stderr, "Cannot allocate %s (%u bytes)%s; full capacity is required\n",
              label, static_cast<unsigned>(sizeof(T)),
              MESH_REPEATER_REQUIRE_PSRAM ? " in required PSRAM" : "");
    }
    return value_ != nullptr;
  }

  T* data() { return value_; }
  const T* data() const { return value_; }
  T& require() {
    // Never allocate through a getter: initialization must precede routing
    // and persistence, and allocation never moves after pointers escape.
    if (!value_) abort();
    return *value_;
  }
  const T& require() const {
    if (!value_) abort();
    return *value_;
  }
};

} // namespace mesh
#endif
