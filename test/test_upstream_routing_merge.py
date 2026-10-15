"""Check upstream zero-scope semantics without dropping fork safety rules."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class UpstreamRoutingMergeTests(unittest.TestCase):
    def test_zero_transport_code_matches_wildcard_without_key_lookup(self):
        method = extract_braced((ROOT / "src/helpers/RegionMap.cpp").read_text(),
                                "RegionEntry* RegionMap::findMatch(")
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("native C++ compiler required")
        source = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
namespace mesh { struct Packet { uint16_t transport_codes[2] = {}; }; }
struct RegionEntry { uint8_t flags; };
struct TransportKey {
  uint16_t code = 0x1234;
  uint16_t calcTransportCode(const mesh::Packet*) const { return code; }
};
class RegionMap {
public:
  RegionEntry wildcard{0}, regions[1]{{0}};
  int num_regions = 1, lookups = 0;
  int getTransportKeysFor(const RegionEntry&, TransportKey keys[], int) {
    ++lookups; keys[0].code = 0x1234; return 1;
  }
  RegionEntry* findMatch(const mesh::Packet*, uint8_t);
};
@METHOD@
int main() {
  RegionMap regions;
  mesh::Packet packet;
  const mesh::Packet* input = &packet;
  assert(regions.findMatch(input, 1) == &regions.wildcard);
  assert(regions.lookups == 0);
  packet.transport_codes[1] = 0x9999;
  assert(regions.findMatch(input, 1) == &regions.wildcard);
  packet.transport_codes[0] = 0x1234;
  assert(regions.findMatch(input, 1) == &regions.regions[0]);
  regions.regions[0].flags = 1;
  assert(regions.findMatch(input, 1) == nullptr);
  regions.regions[0].flags = 0;
  packet.transport_codes[0] = 0x9999;
  assert(regions.findMatch(input, 1) == nullptr);
}
'''.replace("@METHOD@", method)
        with tempfile.TemporaryDirectory(prefix="mesh-upstream-routing-") as directory:
            cpp = Path(directory) / "routing.cpp"
            exe = Path(directory) / "routing"
            cpp.write_text(source)
            subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            str(cpp), "-o", str(exe)], check=True, timeout=30,
                           capture_output=True)
            subprocess.run([str(exe)], check=True, timeout=10, capture_output=True)

    def test_room_repeat_deprecation_keeps_full_rules_and_clock_sync(self):
        source = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        handler = extract_braced(source, "void MyMesh::handleCommand(")
        self.assertIn('strcmp(command, "set repeat on") == 0', handler)
        self.assertIn('"ERR - repeat now deprecated"', handler)
        self.assertIn("flood_rules.handleCommand(command, reply)", handler)
        self.assertIn("_clock_sync.handleCommand(command, reply)", handler)
        self.assertIn("_local_cli_output.owns", handler)


if __name__ == "__main__":
    unittest.main()
