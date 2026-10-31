"""The repeater's radio2 (MediaMesh) neighbour cache: noteNeighbourRadio2 and
neighbourRadio2 from examples/simple_repeater/MyMesh.cpp, compiled as they are.

Full ESP32 builds keep up to 254 neighbours, so radio2 is not a field per
neighbour but a small cache keyed by public-key prefix. These pin its rules:
unknown (no trailer) changes nothing, a node's newer report replaces its
older one, and a full cache keeps On (MediaMesh) reports over Off ones.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class Radio2NeighbourCacheTest(unittest.TestCase):
    def build_and_run(self):
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        decl = header[header.index("  struct Radio2CacheEntry {"):
                      header.index("  uint8_t radio2_cache_next;")]
        size = int(re.search(r"RADIO2_CACHE_SIZE = (\d+);", decl).group(1))
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "void MyMesh::noteNeighbourRadio2(",
            "Radio2DiscoverTrailer MyMesh::neighbourRadio2("))
        code = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <helpers/Radio2DiscoverTrailer.h>
namespace mesh { struct Identity { uint8_t pub_key[32]; }; }
struct MyMesh {
''' + decl + r'''
  uint8_t radio2_cache_next = 0;
  MyMesh() { memset(radio2_cache, 0, sizeof(radio2_cache)); }
  void noteNeighbourRadio2(const mesh::Identity& id, const Radio2DiscoverTrailer& radio2);
  Radio2DiscoverTrailer neighbourRadio2(const mesh::Identity& id) const;
};
''' + methods + r'''
static mesh::Identity key(int n) { mesh::Identity id; memset(id.pub_key, 0, 32);
  id.pub_key[0] = uint8_t(n); id.pub_key[1] = uint8_t(n >> 8); id.pub_key[3] = 0xA5; return id; }
static const uint8_t ON[] = {1, 0x05, 0x2E, 0xE7, 0x0D, 0x00, 0x90, 0xD0, 0x03, 0x00, 7, 5};
static const uint8_t OFF[] = {1, 0};
int main() {
  const Radio2DiscoverTrailer on = Radio2DiscoverTrailer::parse(ON, sizeof(ON));
  const Radio2DiscoverTrailer off = Radio2DiscoverTrailer::parse(OFF, sizeof(OFF));
  const Radio2DiscoverTrailer unknown = Radio2DiscoverTrailer::parse(nullptr, 0);
  MyMesh m;
  // Nothing known yet.
  assert(m.neighbourRadio2(key(1)).state == Radio2DiscoverTrailer::Unknown);
  // A report sticks; a later reply without a trailer does not erase it.
  m.noteNeighbourRadio2(key(1), on);
  assert(m.neighbourRadio2(key(1)).state == Radio2DiscoverTrailer::On);
  assert(m.neighbourRadio2(key(1)).freq_khz == 911150);
  m.noteNeighbourRadio2(key(1), unknown);
  assert(m.neighbourRadio2(key(1)).state == Radio2DiscoverTrailer::On);
  // A newer report from the same node replaces it, in the same slot.
  m.noteNeighbourRadio2(key(1), off);
  assert(m.neighbourRadio2(key(1)).state == Radio2DiscoverTrailer::Off);
  int used = 0;
  for (const auto& e : m.radio2_cache) used += e.radio2.state != Radio2DiscoverTrailer::Unknown;
  assert(used == 1);
  // Fill the cache with Off reports; one more Off is dropped.
  for (int n = 2; n <= @SIZE@; n++) m.noteNeighbourRadio2(key(n), off);
  m.noteNeighbourRadio2(key(500), off);
  assert(m.neighbourRadio2(key(500)).state == Radio2DiscoverTrailer::Unknown);
  // An On report takes an Off slot: MediaMesh nodes win a full cache.
  m.noteNeighbourRadio2(key(501), on);
  assert(m.neighbourRadio2(key(501)).state == Radio2DiscoverTrailer::On);
  // All On: further On reports rotate through, newest kept.
  for (int n = 600; n < 600 + 3 * @SIZE@; n++) m.noteNeighbourRadio2(key(n), on);
  assert(m.neighbourRadio2(key(600 + 3 * @SIZE@ - 1)).state == Radio2DiscoverTrailer::On);
  int on_count = 0;
  for (const auto& e : m.radio2_cache) on_count += e.radio2.state == Radio2DiscoverTrailer::On;
  assert(on_count == @SIZE@);
  // Small: one cache entry is a 4-byte prefix and an 8-byte report.
  static_assert(sizeof(MyMesh::Radio2CacheEntry) == 12, "compact cache entry");
  printf("radio2 neighbour cache ok\n");
  return 0;
}
'''.replace("@SIZE@", str(size))
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="meshcore-radio2-cache-") as temporary:
            work = Path(temporary)
            generated = work / "cache.cpp"
            generated.write_text(code)
            binary = work / "cache"
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
                          if sys.platform.startswith("linux") else [])
            compiled = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", *sanitizers,
                                       "-I" + str(ROOT / "src"), str(generated), "-o", str(binary)],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            return subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)

    def test_cache_rules(self):
        checked = self.build_and_run()
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn("radio2 neighbour cache ok", checked.stdout)


if __name__ == "__main__":
    unittest.main()
