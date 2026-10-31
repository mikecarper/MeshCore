"""Reject incomplete real ArduinoJson documents before MQTT publication."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/MQTTPayloadBuilder.cpp"
HARNESS = r'''
#include <ArduinoJson.h>
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <string>
#include "helpers/MQTTPayloadBuilder.h"

struct FailingAllocator : ArduinoJson::Allocator {
  enum Mode { Healthy, RejectAll, RejectSecondPool } mode;
  unsigned pools = 0, denied = 0;
  explicit FailingAllocator(Mode value) : mode(value) {}
  void* allocate(size_t bytes) override {
    const size_t pool_bytes = ArduinoJson::detail::ResourceManager::slotSize * ARDUINOJSON_POOL_CAPACITY;
    if (mode == RejectAll || (mode == RejectSecondPool && bytes == pool_bytes && pools++)) {
      ++denied;
      return nullptr;
    }
    return std::malloc(bytes);
  }
  void* reallocate(void* pointer, size_t bytes) override {
    if (mode == RejectAll) { ++denied; return nullptr; }
    return std::realloc(pointer, bytes);
  }
  void deallocate(void* pointer) override { std::free(pointer); }
};

static int build(unsigned kind, JsonDocument& doc, char* buffer, size_t capacity) {
  if (kind == 0) return MQTTPayloadBuilder::buildStatusMessage(doc,
      "node", "identity", "model", "firmware", "radio", "client", "online", "timestamp",
      buffer, capacity, 4200, 12, 0, 1, -112, 2, 3, 0, 20000, 4, 5, "on");
  if (kind == 1) {
    const uint8_t path[] = {0xAA, 0xBB};
    return MQTTPayloadBuilder::buildPacketMessage(doc, "node", "identity", "timestamp",
        "rx", "12:34:56", "27/10/2026", 12, 4, "F", 2, "AABB", 2.5f, -85,
        0.125f, "hash", path, 2, 1, sizeof(path), buffer, capacity);
  }
  if (kind == 2) return MQTTPayloadBuilder::buildRawMessage(doc,
      "node", "identity", "timestamp", "AABB", buffer, capacity);
  static const MQTTRadio2Info neighbor_radio2 = {"911.150000,250.0,7,5", "rxtx", false};
  const MQTTPayloadBuilder::NeighborsMessageEntry neighbor = {
      "ABCDEF", 2.5f, 10, "region", "active", -85, false, &neighbor_radio2};
  return MQTTPayloadBuilder::buildNeighborsMessage(doc, "node", "identity", "timestamp",
      "region", "*", &neighbor, 1, buffer, capacity, 1, 1, false);
}

int main() {
  static_assert(ARDUINOJSON_POOL_CAPACITY == 4, "Force real pool growth even for the five-field RAW message");
  for (unsigned kind = 0; kind < 4; ++kind) {
    JsonDocument healthy;
    char expected[2048];
    const int complete = build(kind, healthy, expected, sizeof(expected));
    assert(complete > 0 && !healthy.overflowed());
    assert(healthy.size() >= 5);

    for (auto mode : {FailingAllocator::RejectAll, FailingAllocator::RejectSecondPool}) {
      FailingAllocator allocator(mode);
      JsonDocument doc(&allocator);
      char buffer[2048]; memset(buffer, 'x', sizeof(buffer));
      const int written = build(kind, doc, buffer, sizeof(buffer));
      assert(allocator.denied && doc.overflowed());
      if (mode == FailingAllocator::RejectSecondPool) {
        assert(allocator.pools > 1 && doc.size() > 0);
        // The corrupt partial object itself still serializes to positive JSON.
        // Only the production completeness guard prevents its publication.
        char incomplete[2048];
        assert(serializeJson(doc, incomplete, sizeof(incomplete)) > 0);
        assert(strcmp(incomplete, expected));
      }
      assert(written == 0 && buffer[0] == '\0');
      // Recovering the same document clears its overflow state and restores
      // every original byte, including packet metrics/path and neighbor data.
      allocator.mode = FailingAllocator::Healthy;
      assert(build(kind, doc, buffer, sizeof(buffer)) == complete);
      assert(!doc.overflowed() && !strcmp(buffer, expected));
      assert(build(kind, doc, nullptr, sizeof(buffer)) == 0);
      assert(build(kind, doc, buffer, 0) == 0);
    }
  }
}
'''


class MqttPayloadOomTests(unittest.TestCase):
    def compile_run(self, source=None, reject=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a native C++ compiler is required")
        header = next((ROOT / ".pio/libdeps").glob("*/ArduinoJson/src/ArduinoJson.h"), None)
        self.assertIsNotNone(header, "actual ArduinoJson headers are required")
        with tempfile.TemporaryDirectory(prefix="mqtt-payload-oom-") as directory:
            folder = Path(directory)
            harness, binary = folder / "oom.cpp", folder / "oom"
            harness.write_text(HARNESS)
            json_include = folder / "pinned_arduinojson.h"
            json_include.write_text('''\
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmaybe-uninitialized"
#endif
#include <ArduinoJson.h>
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC diagnostic pop
#endif
''')
            production = SOURCE
            if source is not None:
                production = folder / "MQTTPayloadBuilder.cpp"
                production.write_text(source)
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            # GCC's sanitizer/inlining pass flags ArduinoJson 7.4.3's empty
            # iterator. Scope that diagnostic to the dependency definitions;
            # the harness and firmware keep optimized -Werror + sanitizers.
            built = subprocess.run([compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-DARDUINOJSON_POOL_CAPACITY=4", *flags, "-isystem", str(header.parent),
                "-include", str(json_include),
                "-I" + str(ROOT / "src"), "-I" + str(ROOT / "src/helpers"),
                "-isystem", str(ROOT / "test/mocks"),
                str(harness), str(production), str(ROOT / "src/helpers/TxtDataHelpers.cpp"), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            if reject:
                self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn("Assertion", checked.stderr)
            else:
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_all_builders_reject_total_and_partial_pool_oom_and_recover(self):
        self.compile_run()

    def test_previous_length_only_serialization_is_rejected(self):
        source = SOURCE.read_text()
        old = source.replace("  if (doc.overflowed() || root.isNull()) return 0;", "")
        self.assertNotEqual(source, old)
        self.compile_run(old, reject=True)


if __name__ == "__main__":
    unittest.main()
