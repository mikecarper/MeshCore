#!/usr/bin/env python3
"""Exercise production MQTT diagnostics with distinct internal and DMA heaps."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(signature):
    source = (ROOT / "src/helpers/bridges/MQTTBridge.cpp").read_text(encoding="utf-8")
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    pos = opening + 1
    while depth:
        depth += (source[pos] == "{") - (source[pos] == "}")
        pos += 1
    return source[start:pos] + "\n"


PREAMBLE = r'''
#include <cassert>
#include <cstdint>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include "helpers/MQTTReplyFormat.h"
#include "helpers/MQTTConnectionHealth.h"
#include "helpers/bridges/MQTTErrorLabels.h"
constexpr int RUNTIME_MQTT_SLOTS = 5;
constexpr unsigned MALLOC_CAP_DMA = 1U << 3;
constexpr unsigned MALLOC_CAP_INTERNAL = 1U << 11;
static unsigned dma_queries = 0;
static size_t dma_free = 8128, dma_largest = 1536;
size_t heap_caps_get_free_size(unsigned caps) {
  assert(caps == MALLOC_CAP_DMA);
  ++dma_queries;
  return dma_free;
}
size_t heap_caps_get_largest_free_block(unsigned caps) {
  assert(caps == MALLOC_CAP_DMA);
  ++dma_queries;
  return dma_largest;
}
struct {
  int getFreeHeap() const { return 64000; }
  int getMaxAllocHeap() const { return 30000; }
} ESP;
unsigned uxQueueMessagesWaiting(void*) { return 3; }
static char debug_line[512];
void capture_debug(const char* format, ...) {
  va_list args;
  va_start(args, format);
  vsnprintf(debug_line, sizeof(debug_line), format, args);
  va_end(args);
}
#define MQTT_DEBUG_PRINTLN(...) capture_debug(__VA_ARGS__)
struct MQTTPrefs {};
struct Client {
  unsigned long getOutboxSize() const { return 0; }
  unsigned long getOutboxDrops() const { return 0; }
  unsigned long getPublishOk() const { return 123; }
  unsigned long getPublishErr() const { return 4; }
};
struct MQTTBridge {
  struct Slot {
    Client* client = nullptr;
    bool enabled = true, connected = true;
  };
  Slot _slots[RUNTIME_MQTT_SLOTS];
  bool _initialized = true;
  void* _packet_queue_handle = reinterpret_cast<void*>(1);
  int _queue_count = 3, _skipped_publishes = 0;
  unsigned long _filtered_packets = 0;
  static constexpr int MAX_QUEUE_SIZE = 50;
  bool isSlotReady(int) const { return true; }
  static void formatMqttStatsReply(char*, size_t);
  static const char* tlsErrorStr(int32_t);
  void logMemoryStatus();
};
static MQTTBridge* s_mqtt_bridge_instance = nullptr;
'''

MAIN = r'''
int main() {
  // Use the public production method, not the pure table alone. Unknown SDK
  // codes retain raw-number formatting, and write failures stay distinct.
  assert(strcmp(MQTTBridge::tlsErrorStr(0x8006), "connection timeout") == 0);
  assert(strcmp(MQTTBridge::tlsErrorStr(0x8008), "server closed connection") == 0);
  assert(strcmp(MQTTBridge::tlsErrorStr(0x8018), "TLS write failed") == 0);
  assert(MQTTBridge::tlsErrorStr(0x800B) == nullptr);

  char reply[160];
  MQTTBridge::formatMqttStatsReply(reply, sizeof(reply));
  assert(strcmp(reply, "> (bridge not running)") == 0);
  assert(dma_queries == 0);

  MQTTBridge bridge;
  Client clients[RUNTIME_MQTT_SLOTS];
  for (int i = 0; i < RUNTIME_MQTT_SLOTS; ++i) bridge._slots[i].client = &clients[i];
  s_mqtt_bridge_instance = &bridge;
  MQTTBridge::formatMqttStatsReply(reply, sizeof(reply));
  assert(strstr(reply, "Free=64000 Max=30000") != nullptr);
#ifdef ESP_PLATFORM
  // A large general internal block must not mask the smaller AES DMA heap.
  assert(strstr(reply, "DMA=8128/1536") != nullptr);
  assert(dma_queries == 2);
#else
  assert(strstr(reply, "DMA=") == nullptr);
  assert(dma_queries == 0);
#endif
  // Normal five-slot stats still fit the fixed LoRa reply budget.
  assert(strstr(reply, "s5=123/4") != nullptr);

  bridge.logMemoryStatus();
#ifdef ESP_PLATFORM
  assert(strstr(debug_line, "DMA=8128/1536") != nullptr);
#else
  assert(strstr(debug_line, "DMA=") == nullptr);
#endif

  // Large counters and new heap fields never write beyond a short reply.
  struct { char reply[32]; unsigned char guard[8]; } bounded;
  memset(&bounded, 0xA5, sizeof(bounded));
  bridge._filtered_packets = 4294967295UL;
  dma_free = 4294967295UL;
  dma_largest = 4294967295UL;
  MQTTBridge::formatMqttStatsReply(bounded.reply, sizeof(bounded.reply));
  assert(bounded.reply[sizeof(bounded.reply) - 1] == '\0');
  for (unsigned char byte : bounded.guard) assert(byte == 0xA5);
  MQTTBridge::formatMqttStatsReply(nullptr, 32);
  MQTTBridge::formatMqttStatsReply(bounded.reply, 0);
}
'''


class MqttDiagnosticsTests(unittest.TestCase):
    def test_production_stats_and_labels(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is unavailable")
        source = PREAMBLE + "\n".join(method(signature) for signature in (
            "void MQTTBridge::formatMqttStatsReply(",
            "const char* MQTTBridge::tlsErrorStr(",
            "void MQTTBridge::logMemoryStatus(",
        )) + MAIN
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-diagnostics-") as directory:
            test_source = Path(directory) / "diagnostics.cpp"
            test_source.write_text(source, encoding="ascii")
            for esp in (False, True):
                with self.subTest(esp=esp):
                    executable = Path(directory) / ("esp" if esp else "other")
                    command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                               "-I", str(ROOT / "src"), str(test_source), "-o", str(executable)]
                    if esp:
                        command.insert(1, "-DESP_PLATFORM=1")
                    subprocess.run(command, check=True, capture_output=True, text=True)
                    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
