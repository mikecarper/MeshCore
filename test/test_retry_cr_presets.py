#!/usr/bin/env python3
"""Execute preset application, role retry budgets, and the flood-count setter."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class RetryCrPresetsTest(unittest.TestCase):
    def test_presets_and_disabling_flood_leave_direct_settings_unchanged(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        header = (ROOT / "src/helpers/CommonCLI.h").read_text()
        core = (ROOT / "src/Mesh.cpp").read_text()
        repeater = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        definitions = "\n".join(re.findall(
            r"^#define (?:RETRY_PRESET_|DIRECT_RETRY_|FLOOD_RETRY_)[A-Z0-9_]+[^\n]*",
            header, re.M))
        definitions += "\n" + re.search(
            r"#define FLOOD_RETRY_PATH_GATE_DISABLED[^\n]*",
            (ROOT / "src/Mesh.h").read_text()).group()
        generated = definitions + r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#define constrain(x, low, high) ((x) < (low) ? (low) : (x) > (high) ? (high) : (x))
struct NodePrefs {
  uint8_t retry_preset, direct_retry_attempts, direct_retry_enabled;
  uint8_t direct_retry_cr_enabled, direct_retry_recent_enabled;
  uint8_t direct_retry_prefs_magic[2], flood_retry_attempts;
  uint8_t flood_retry_max_path, flood_retry_group_max_path, disable_fwd;
  uint16_t direct_retry_base_ms, direct_retry_step_ms, direct_retry_snr_margin_x4;
  int8_t direct_retry_cr4_snr_x4, direct_retry_cr5_snr_x4;
  int8_t direct_retry_cr7_snr_x4, direct_retry_cr8_snr_x4;
};
namespace mesh {
struct Packet { uint8_t hops = 0; uint8_t getPathHashCount() const { return hops; } };
class Mesh { public:
  static uint8_t getDirectRetryCodingRateForAttempt(uint8_t, uint8_t);
};
}
class MyMesh : public mesh::Mesh {
public:
  NodePrefs _prefs{};
  unsigned direct_cancellations = 0, flood_cancellations = 0;
  uint8_t getFloodRetryMaxAttempts(const mesh::Packet*) const;
  uint8_t roomFloodAttempts(const mesh::Packet*) const;
  uint8_t sensorFloodAttempts(const mesh::Packet*) const;
  uint8_t getDirectRetryCodingRateForSNR(int8_t) const;
  void onRetryConfigChanged();
  void cancelAllDirectRetries() { ++direct_cancellations; }
  void cancelAllFloodRetries() { ++flood_cancellations; }
};
struct CLI {
  NodePrefs* _prefs;
  MyMesh* _callbacks;
  unsigned saves = 0;
  void savePrefs() { ++saves; }
  int _atoi(const char* value) { return atoi(value); }
  void set(const char* config, char* reply);
};
'''
        for signature in ("static void markDirectRetryPrefsValid(",
                          "static void applyFloodRetryPreset(",
                          "static void applyDirectRetryPreset(",
                          "static void setDefaultDirectRetryPrefs(",
                          "static bool looksUnsignedInteger("):
            generated += extract_braced(common, signature) + "\n"
        generated += "namespace mesh {\n" + extract_braced(
            core, "uint8_t Mesh::getDirectRetryCodingRateForAttempt(") + "\n}\n"
        for signature in ("uint8_t MyMesh::getFloodRetryMaxAttempts(",
                          "uint8_t MyMesh::getDirectRetryCodingRateForSNR(",
                          "void MyMesh::onRetryConfigChanged("):
            generated += extract_braced(repeater, signature) + "\n"
        for role, owner, method in (("simple_room_server", "MyMesh", "roomFloodAttempts"),
                                    ("simple_sensor", "SensorMesh", "sensorFloodAttempts")):
            source = (ROOT / f"examples/{role}/{owner}.cpp").read_text()
            budget = extract_braced(source, f"uint8_t {owner}::getFloodRetryMaxAttempts(")
            generated += budget.replace(f"{owner}::getFloodRetryMaxAttempts", f"MyMesh::{method}") + "\n"
        setter = extract_braced(common, 'if (memcmp(config, "flood.retry.count ", 18) == 0)')
        generated += "void CLI::set(const char* config, char* reply) { " + setter + " }\n"
        generated += r'''
static std::vector<int> directSettings(const NodePrefs& p) {
  return {p.direct_retry_attempts, p.direct_retry_enabled, p.direct_retry_cr_enabled,
    p.direct_retry_recent_enabled, p.direct_retry_base_ms, p.direct_retry_step_ms,
    p.direct_retry_snr_margin_x4, p.direct_retry_cr4_snr_x4, p.direct_retry_cr5_snr_x4,
    p.direct_retry_cr7_snr_x4, p.direct_retry_cr8_snr_x4};
}
static std::vector<uint8_t> directSchedule(const MyMesh& node, int8_t snr) {
  std::vector<uint8_t> rates;
  for (uint8_t attempt = 1; attempt <= node._prefs.direct_retry_attempts; ++attempt) {
    rates.push_back(node.getDirectRetryCodingRateForAttempt(
        node.getDirectRetryCodingRateForSNR(snr), attempt));
  }
  return rates;
}
int main() {
  const uint8_t presets[] = {RETRY_PRESET_INFRA, RETRY_PRESET_ROOFTOP, RETRY_PRESET_MOBILE};
  const uint8_t direct_counts[] = {4, 15, 15};
  const uint8_t origin_counts[] = {2, 6, 15};
  const uint8_t expected_cr5[] = {5, 7, 7, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8};
  for (unsigned preset = 0; preset < 3; ++preset) {
    for (int8_t snr : {-40, 10, 12, 30, 40}) {
      MyMesh node;
      setDefaultDirectRetryPrefs(&node._prefs);
      applyDirectRetryPreset(&node._prefs, presets[preset]);
      assert(node._prefs.direct_retry_attempts == direct_counts[preset]);
      mesh::Packet packet;
      assert(node.getFloodRetryMaxAttempts(&packet) == origin_counts[preset]);
      assert(node.roomFloodAttempts(&packet) == origin_counts[preset]);
      assert(node.sensorFloodAttempts(&packet) == origin_counts[preset]);
      for (uint8_t i = 0; i < origin_counts[preset]; ++i) {
        assert(node.getDirectRetryCodingRateForAttempt(5, i + 1) == expected_cr5[i]);
      }
      const auto settings = directSettings(node._prefs);
      const auto schedule = directSchedule(node, snr);
      CLI cli{&node._prefs, &node};
      char reply[160] = {};
      cli.set("flood.retry.count 0", reply);
      assert(strcmp(reply, "OK") == 0 && cli.saves == 1);
      assert(node._prefs.retry_preset == RETRY_PRESET_CUSTOM);
      assert(node.getFloodRetryMaxAttempts(&packet) == 0);
      assert(node.roomFloodAttempts(&packet) == 0);
      assert(node.sensorFloodAttempts(&packet) == 0);
      assert(node.flood_cancellations == 1 && node.direct_cancellations == 0);
      assert(directSettings(node._prefs) == settings);
      assert(directSchedule(node, snr) == schedule);
    }
  }
  puts("15 preset/disabled-flood schedule checks passed");
}
'''
        with tempfile.TemporaryDirectory(prefix=".tmp-retry-cr-", dir=ROOT) as directory:
            work = Path(directory)
            source = work / "retry-cr.cpp"
            source.write_text(generated, encoding="ascii")
            binary = work / "retry-cr"
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                                     str(source), "-o", str(binary)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("15 preset/disabled-flood schedule checks passed", checked.stdout)


if __name__ == "__main__":
    unittest.main()
