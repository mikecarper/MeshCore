#!/usr/bin/env python3
"""Test MQTT memory admission and its production allocator/connection wiring."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_mqtt_transport_results import method

ROOT = Path(__file__).resolve().parents[1]

POLICY_PREAMBLE = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include "helpers/MQTTConnectionAdmission.h"
namespace Admission = MQTTConnectionAdmission;
'''

HEAP_PREAMBLE = POLICY_PREAMBLE + r'''
#define ESP_PLATFORM 1
#define BOARD_HAS_PSRAM 1
constexpr uint32_t MALLOC_CAP_INTERNAL = 1, MALLOC_CAP_DMA = 2, MALLOC_CAP_SPIRAM = 4;
static size_t dma_free = 100000, dma_largest = 20000;
static size_t heap_queries = 0, psram_allocations = 0, internal_allocations = 0;
static bool psram_fails = false, internal_fails = false;
static unsigned char psram_storage[4096], internal_storage[4096];
size_t heap_caps_get_free_size(uint32_t caps) {
  assert(caps == (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
  ++heap_queries;
  return dma_free;
}
size_t heap_caps_get_largest_free_block(uint32_t caps) {
  assert(caps == (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
  ++heap_queries;
  return dma_largest;
}
void* heap_caps_calloc(size_t count, size_t size, uint32_t caps) {
  assert(count && size && count <= SIZE_MAX / size);
  if (caps == MALLOC_CAP_SPIRAM) {
    ++psram_allocations;
    return psram_fails ? nullptr : psram_storage;
  }
  assert(caps == (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
  ++internal_allocations;
  return internal_fails ? nullptr : internal_storage;
}
'''

CONNECTION_PREAMBLE = HEAP_PREAMBLE + r'''
#include <atomic>
constexpr int RUNTIME_MQTT_SLOTS = 6;
struct { size_t size = 0; size_t getPsramSize() const { return size; } } ESP;
struct Preset { const char* server_url; };
struct MQTTBridge {
  struct MQTTSlot {
    const Preset* preset = nullptr;
    char broker_uri[128] = {}, host[64] = "broker";
    uint16_t port = 8883;
  } _slots[RUNTIME_MQTT_SLOTS];
  std::atomic<bool> _slot_attempt_pending[RUNTIME_MQTT_SLOTS] = {};
  bool hasPendingSlotConnection() const;
  bool canStartSlotConnection(int index) const;
};
'''


class MqttConnectionAdmissionTest(unittest.TestCase):
    def compile_run(self, source):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-admission-") as temporary:
            work = Path(temporary)
            path, binary = work / "admission.cpp", work / "admission"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src"), str(path), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], cwd=work, capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_connection_admission_checks_both_total_and_contiguous_dma_heap(self):
        self.compile_run(POLICY_PREAMBLE + r'''
int main() {
  // A large aggregate heap cannot compensate for a fragmented DMA region.
  assert(!Admission::canStart(true, true, 100000, 4095));
  assert(!Admission::canStart(true, true, 16383, 20000));
  assert(Admission::canStart(true, true, 16384, 4096));
  assert(Admission::canStart(false, false, 16384, 4096));
  assert(!Admission::canStart(false, false, 16383, 4096));
  assert(!Admission::canStart(false, false, 16384, 4095));

  // Without PSRAM, TLS also needs a full record allocation and session budget.
  assert(!Admission::canStart(false, true, 61439, 20000));
  assert(!Admission::canStart(false, true, 100000, 17407));
  assert(Admission::canStart(false, true, 61440, 17408));
  assert(Admission::canStart(true, false, 16384, 4096));
}
''')

    def test_internal_fallback_preserves_dma_reserve_and_rejects_fragmentation(self):
        self.compile_run(POLICY_PREAMBLE + r'''
int main() {
  assert(Admission::canFallback(1600, 16384 + 1600, 1600));
  assert(!Admission::canFallback(1600, 16384 + 1599, 1600));
  assert(!Admission::canFallback(1600, 100000, 1599));
  assert(!Admission::canFallback(1600, 16383, 100000));
  assert(!Admission::canFallback(1, 0, 100000));
  assert(!Admission::canFallback(0, 100000, 100000));
  assert(!Admission::canFallback(SIZE_MAX, SIZE_MAX, SIZE_MAX));
  assert(Admission::canFallback(SIZE_MAX - 16384, SIZE_MAX, SIZE_MAX));
}
''')

    def test_calloc_size_rejects_zero_and_overflow_without_allocator_calls(self):
        self.compile_run(POLICY_PREAMBLE + r'''
int main() {
  size_t total = 123;
  assert(!Admission::checkedCallocSize(0, 16, total) && total == 0);
  total = 123;
  assert(!Admission::checkedCallocSize(16, 0, total) && total == 0);
  total = 123;
  assert(!Admission::checkedCallocSize(SIZE_MAX, 2, total) && total == 0);
  assert(Admission::checkedCallocSize(SIZE_MAX, 1, total) && total == SIZE_MAX);
  assert(Admission::checkedCallocSize(1, SIZE_MAX, total) && total == SIZE_MAX);
  assert(Admission::checkedCallocSize(2, SIZE_MAX / 2, total));
  assert(total == SIZE_MAX - 1);
  assert(Admission::checkedCallocSize(4, 896, total) && total == 3584);
}
''')

    def test_explicit_uri_scheme_takes_precedence_over_port(self):
        self.compile_run(POLICY_PREAMBLE + r'''
int main() {
  assert(Admission::requiresTls("wss://broker:1883/mqtt", 1883));
  assert(Admission::requiresTls("mqtts://broker:1883", 1883));
  assert(!Admission::requiresTls("ws://broker:443/mqtt", 443));
  assert(!Admission::requiresTls("mqtt://broker:8883", 8883));
  assert(Admission::requiresTls("broker", 443));
  assert(Admission::requiresTls("broker", 8883));
  assert(!Admission::requiresTls("broker", 1883));
  assert(!Admission::requiresTls("broker", 9443));
  assert(Admission::requiresTls(nullptr, 443));
  assert(!Admission::requiresTls(nullptr, 0));
  assert(Admission::requiresTls("", 8883));
}
''')

    def test_actual_calloc_fallback_preserves_dma_headroom_and_handles_failure(self):
        source = HEAP_PREAMBLE + method("src/helpers/bridges/MQTTBridge.cpp",
                                       "static void* psram_calloc(size_t n, size_t size)")
        self.compile_run(source + r'''
int main() {
  assert(psram_calloc(0, 16) == nullptr);
  assert(psram_calloc(16, 0) == nullptr);
  assert(psram_calloc(SIZE_MAX, 2) == nullptr);
  assert(psram_allocations == 0 && internal_allocations == 0 && heap_queries == 0);

  // A successful external allocation needs no internal fallback or reserve.
  dma_free = dma_largest = 0;
  assert(psram_calloc(4, 400) == psram_storage);
  assert(psram_allocations == 1 && internal_allocations == 0 && heap_queries == 0);
  psram_fails = true;
  dma_free = 16384 + 1599; dma_largest = 20000;
  assert(psram_calloc(4, 400) == nullptr && internal_allocations == 0);
  dma_free = 100000; dma_largest = 1599;
  assert(psram_calloc(4, 400) == nullptr && internal_allocations == 0);
  dma_free = 16383; dma_largest = 20000;
  assert(psram_calloc(1, 1) == nullptr && internal_allocations == 0);

  dma_free = 16384 + 1600; dma_largest = 1600;
  assert(psram_calloc(4, 400) == internal_storage && internal_allocations == 1);
  internal_fails = true;
  assert(psram_calloc(4, 400) == nullptr && internal_allocations == 2);
}
''')

    def test_actual_byte_allocators_use_byte_capable_fallback_and_preserve_failed_resize(self):
        source = POLICY_PREAMBLE + r'''
#include <cstring>
#define ESP_PLATFORM 1
#define BOARD_HAS_PSRAM 1
constexpr uint32_t MALLOC_CAP_INTERNAL = 1, MALLOC_CAP_SPIRAM = 4, MALLOC_CAP_8BIT = 8;
static bool psram_fails = false, internal_fails = false;
static size_t malloc_calls = 0, realloc_calls = 0, free_calls = 0;
static unsigned char original[32], psram_storage[64], internal_storage[64];
static void* freed = nullptr;
void* allocation(uint32_t caps) {
  if (caps == MALLOC_CAP_SPIRAM) return psram_fails ? nullptr : psram_storage;
  // INTERNAL alone can select 32-bit-only IRAM on ESP32-S3.
  assert(caps == (MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
  return internal_fails ? nullptr : internal_storage;
}
void* heap_caps_malloc(size_t size, uint32_t caps) {
  assert(size == 32);
  ++malloc_calls;
  return allocation(caps);
}
void* heap_caps_realloc(void* ptr, size_t size, uint32_t caps) {
  assert(ptr == original && size == 48);
  ++realloc_calls;
  return allocation(caps);
}
void heap_caps_free(void* ptr) { ++free_calls; freed = ptr; }
'''
        for signature in ("static void* psram_malloc(size_t size)",
                          "static void psram_free(void* ptr)",
                          "static void* psram_realloc(void* ptr, size_t new_size)"):
            source += method("src/helpers/bridges/MQTTBridge.cpp", signature)
        self.compile_run(source + r'''
int main() {
  assert(psram_malloc(0) == nullptr && malloc_calls == 0);
  assert(psram_malloc(32) == psram_storage && malloc_calls == 1);
  psram_fails = true;
  assert(psram_malloc(32) == internal_storage && malloc_calls == 3);
  internal_fails = true;
  assert(psram_malloc(32) == nullptr && malloc_calls == 5 && free_calls == 0);

  memset(original, 0xa5, sizeof(original));
  psram_fails = internal_fails = false;
  assert(psram_realloc(original, 48) == psram_storage && realloc_calls == 1);
  psram_fails = true;
  assert(psram_realloc(original, 48) == internal_storage && realloc_calls == 3);
  internal_fails = true;
  assert(psram_realloc(original, 48) == nullptr && realloc_calls == 5);
  assert(free_calls == 0);
  for (unsigned char byte : original) assert(byte == 0xa5);

  assert(psram_realloc(original, 0) == nullptr);
  assert(free_calls == 1 && freed == original && realloc_calls == 5);
  assert(psram_realloc(nullptr, 0) == nullptr && free_calls == 1);
}
''')

    def test_actual_preflight_uses_detected_psram_transport_and_dma_caps(self):
        source = CONNECTION_PREAMBLE + method("src/helpers/bridges/MQTTBridge.cpp",
            "bool MQTTBridge::hasPendingSlotConnection() const")
        source += method("src/helpers/bridges/MQTTBridge.cpp",
                         "bool MQTTBridge::canStartSlotConnection(int index) const")
        self.compile_run(source + r'''
int main() {
  MQTTBridge bridge;
  assert(!bridge.canStartSlotConnection(-1));
  assert(!bridge.canStartSlotConnection(RUNTIME_MQTT_SLOTS));
  assert(heap_queries == 0);

  // BOARD_HAS_PSRAM does not establish that physical PSRAM initialized.
  ESP.size = 0; dma_free = 61440; dma_largest = 17408;
  assert(bridge.canStartSlotConnection(0));
  dma_free = 61439;
  assert(!bridge.canStartSlotConnection(0));
  dma_free = 100000; dma_largest = 17407;
  assert(!bridge.canStartSlotConnection(0));
  ESP.size = 2097152; dma_free = 16384; dma_largest = 4096;
  assert(bridge.canStartSlotConnection(0));
  dma_largest = 4095;
  assert(!bridge.canStartSlotConnection(0));

  // An explicit plaintext URI overrides a TLS port. The effective URI wins
  // over the raw host; a preset's URI wins over both custom fields.
  ESP.size = 0; dma_free = 16384; dma_largest = 4096;
  strcpy(bridge._slots[0].host, "mqtt://broker:8883");
  assert(bridge.canStartSlotConnection(0));
  strcpy(bridge._slots[0].broker_uri, "wss://broker/mqtt");
  assert(!bridge.canStartSlotConnection(0));
  const Preset plaintext{"ws://broker:443/mqtt"};
  bridge._slots[0].preset = &plaintext;
  assert(bridge.canStartSlotConnection(0));
  const Preset tls{"mqtts://broker:1883"};
  bridge._slots[0].preset = &tls;
  assert(!bridge.canStartSlotConnection(0));
  ESP.size = 2097152;
  assert(bridge.canStartSlotConnection(0));

  // Every pending TLS attempt, including the target's own attempt, blocks all
  // other starts until an event callback clears it. No heap walk is needed.
  for (int pending = 0; pending < RUNTIME_MQTT_SLOTS; ++pending) {
    bridge._slot_attempt_pending[pending].store(true, std::memory_order_release);
    const size_t before = heap_queries;
    for (int index = 0; index < RUNTIME_MQTT_SLOTS; ++index) {
      assert(!bridge.canStartSlotConnection(index));
    }
    assert(heap_queries == before);
    bridge._slot_attempt_pending[pending].store(false, std::memory_order_release);
    assert(bridge.canStartSlotConnection(0));
  }
}
''')


if __name__ == "__main__":
    unittest.main()
