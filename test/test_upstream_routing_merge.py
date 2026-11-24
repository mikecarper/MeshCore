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
        region = (ROOT / "src/helpers/RegionMap.cpp").read_text()
        method = extract_braced(region, "bool RegionMap::matchesScope(") + "\n" + extract_braced(
            region, "RegionEntry* RegionMap::findMatch(")
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
  bool matchesScope(const RegionEntry&, const mesh::Packet*);
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
  regions.wildcard.flags = 1;
  assert(regions.findMatch(input, 1) == nullptr);
  assert(regions.findMatch(input, 2) == &regions.wildcard);
  assert(regions.findMatch(input, 0) == &regions.wildcard);
  assert(regions.lookups == 0);  // A denied wildcard must not fall into a region.
  regions.wildcard.flags = 3;
  assert(regions.findMatch(input, 1) == nullptr);
  assert(regions.findMatch(input, 2) == nullptr);
  regions.wildcard.flags = 0;
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

    def test_zero_transport_code_obeys_ordinary_unscoped_hop_limits(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("native C++ compiler required")
        # Include the production packet representation and routing policy;
        # only the constructor's platform-independent reset is supplied here.
        packet_constructor = extract_braced((ROOT / "src/Packet.cpp").read_text(),
                                           "Packet::Packet()")
        source = r'''
#include <cassert>
#include <initializer_list>
#include <helpers/RoutingPolicy.h>
namespace mesh {
@CONSTRUCTOR@
}
int main() {
  for (uint8_t hash_size : {1, 2, 3}) {
    for (uint8_t payload : {PAYLOAD_TYPE_RESPONSE, PAYLOAD_TYPE_ADVERT}) {
      mesh::Packet packet;
      packet.header = ROUTE_TYPE_TRANSPORT_FLOOD | (payload << PH_TYPE_SHIFT);
      packet.transport_codes[0] = 0;
      packet.transport_codes[1] = 0x5678;
      packet.setPathHashSizeAndCount(hash_size, 0);
      assert(mesh::isFloodHopLimitExceeded(&packet, 64, 0, 8));
      packet.setPathHashCount(2);
      assert(!mesh::isFloodHopLimitExceeded(&packet, 64, 3, 8));
      packet.setPathHashCount(3);
      assert(mesh::isFloodHopLimitExceeded(&packet, 64, 3, 8));
      packet.transport_codes[0] = 0x1234;
      assert(!mesh::isFloodHopLimitExceeded(&packet, 64, 0, 8));
      assert(mesh::isFloodHopLimitExceeded(&packet, 3, 64, 8));
      if (payload == PAYLOAD_TYPE_ADVERT)
        assert(mesh::isFloodHopLimitExceeded(&packet, 64, 64, 3));
    }
  }
}
'''.replace("@CONSTRUCTOR@", packet_constructor)
        with tempfile.TemporaryDirectory(prefix="mesh-upstream-hop-limits-") as directory:
            cpp, exe = Path(directory) / "routing.cpp", Path(directory) / "routing"
            cpp.write_text(source)
            built = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter",
                "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                "-I", str(ROOT / "src"), str(cpp), "-o", str(exe),
            ], text=True, capture_output=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(exe)], text=True, capture_output=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_room_repeat_is_opt_in_through_shared_cli(self):
        source = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        constructor = extract_braced(source, "MyMesh::MyMesh(")
        self.assertIn("_prefs.disable_fwd = 1;", constructor)
        begin = extract_braced(source, "void MyMesh::begin(")
        self.assertIn("_cli.loadPrefs(_fs)", begin)
        self.assertNotRegex(begin, r"_prefs\.disable_fwd\s*=(?!=)")
        handler = extract_braced(source, "void MyMesh::handleCommand(")
        self.assertNotIn('"set repeat on"', handler)
        self.assertIn("_cli.handleCommand(sender_timestamp, command, reply)", handler)
        self.assertIn("flood_rules.handleCommand(command, reply)", handler)
        self.assertIn("_clock_sync.handleCommand(command, reply)", handler)
        self.assertIn("_local_cli_output.owns", handler)

    def test_shared_repeat_command_can_enable_an_opted_out_room(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("native C++ compiler required")
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        repeat = extract_braced(cli, 'else if (memcmp(config, "repeat ", 7) == 0)')
        source = r'''
#include <cassert>
#include <cstring>
#define MESH_CLI_REPLY_TEXT(normal, compact) normal
struct Cli {
  struct Prefs { bool disable_fwd = true; } prefs, *_prefs = &prefs;
  struct Callbacks {
    int changes = 0;
    void onRetryConfigChanged() { ++changes; }
  } callbacks, *_callbacks = &callbacks;
  int saves = 0;
  void savePrefs() { ++saves; }
  void set(const char* config, char* reply) {
    if (false) {}
    @REPEAT@
  }
};
int main() {
  Cli cli;
  char reply[160]{};
  cli.set("repeat on", reply);
  assert(!cli.prefs.disable_fwd && cli.saves == 1 && cli.callbacks.changes == 1);
  assert(strcmp(reply, "OK - repeat is now ON") == 0);
  cli.set("repeat maybe", reply);
  assert(!cli.prefs.disable_fwd && cli.saves == 1 && cli.callbacks.changes == 1);
  cli.set("repeat off", reply);
  assert(cli.prefs.disable_fwd && cli.saves == 2 && cli.callbacks.changes == 2);
  assert(strcmp(reply, "OK - repeat is now OFF") == 0);
}
'''.replace("@REPEAT@", repeat)
        with tempfile.TemporaryDirectory(prefix="mesh-room-repeat-") as directory:
            cpp, exe = Path(directory) / "repeat.cpp", Path(directory) / "repeat"
            cpp.write_text(source)
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            str(cpp), "-o", str(exe)], check=True, timeout=30)
            subprocess.run([str(exe)], check=True, timeout=10)

    def test_room_forwarding_and_repeater_controls(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("native C++ compiler required")
        room = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        self.assertIn("_cli(board, rtc, sensors, region_map, acl, &_prefs, this)", room)
        self.assertIn("region_map.load(_fs)", extract_braced(room, "void MyMesh::begin("))
        self.assertIn("_prefs.disable_fwd = 1;", room)
        constructor = extract_braced((ROOT / "src/Packet.cpp").read_text(), "Packet::Packet()")
        source = r'''
#include <cassert>
#include <initializer_list>
#include <new>
#include <helpers/RoutingPolicy.h>
#include "RoomServerFeatures.h"
static_assert(MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE == EXPECT_ROOM_RULES);
namespace mesh { @PACKET@ }
#if defined(ESP32_PLATFORM)
#include <helpers/FloodProfilePolicy.h>
#define FLOOD_CHANNEL_HOPS_ALL 0xff
static unsigned long millis() { return 0; }
void mesh::Packet::calculatePacketHash(uint8_t* hash) const {
  // These tests reuse the same payload across radio profiles.
  memset(hash, getPayloadType(), MAX_HASH_SIZE);
}
namespace mesh {
struct Mesh { bool allowPacketTransmit(const Packet*) const { return true; } };
}
#endif
#if MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE
enum { LOOP_DETECT_OFF, LOOP_DETECT_MINIMAL, LOOP_DETECT_MODERATE };
static const uint8_t ROOM_MAX_LOOP_MINIMAL[] = {0};
static const uint8_t ROOM_MAX_LOOP_MODERATE[] = {0};
static const uint8_t ROOM_MAX_LOOP_STRICT[] = {0};
#endif
struct MyMesh
#if defined(ESP32_PLATFORM)
    : mesh::Mesh
#endif
{
  struct { uint8_t disable_fwd = 0, flood_max = 64, flood_max_unscoped = 2,
                   flood_max_advert = 8, loop_detect = 0, trace_when_repeat_off = 0,
                   flood_channel_data_enabled = 1, flood_channel_data_max_hops = 0xff; } _prefs;
  struct {
    struct Policy {
      uint8_t enabled = 3;
      bool floodProfileAllowed(uint8_t profile) const { return (enabled >> profile) & 1; }
    } policy;
    const Policy& radioProfiles() const { return policy; }
    bool batteryAllowsForward(bool) const { return true; }
    bool allow_unknown = false;
    bool allowsUnmatchedFlood(const mesh::Packet*) const { return allow_unknown; }
    bool batteryAllowsTransmit(const mesh::Packet*) const { return true; }
  } _cli;
#if defined(ESP32_PLATFORM)
  mesh::flood_profiles::DeferredFloods* deferred_floods = nullptr;
  ~MyMesh() { delete deferred_floods; }
  bool deferredFloodCandidate(const mesh::Packet*, uint8_t, bool);
  bool allowPacketTransmit(const mesh::Packet*) const;
#endif
  struct { void observeAcceptedFlood(const mesh::Packet*) {} } _clock_sync;
  const void* recv_pkt_region = nullptr;
#if MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE
  bool recv_pkt_regionless_scope_set = false;
  uint32_t recv_pkt_rule_match_mask = 0;
  struct Time { uint32_t getMillis() const { return 0; } } time, *_ms = &time;
  struct Rules {
    bool blocked = false;
    bool shouldBlock(const mesh::Packet*, uint32_t, uint32_t) const { return blocked; }
    void commitRates(const mesh::Packet*, uint32_t, uint32_t) {}
  } flood_rules;
  bool isLooped(const mesh::Packet*, const uint8_t*) { return false; }
#endif
  bool allowPacketForward(const mesh::Packet*);
};
@ALLOW@
#if defined(ESP32_PLATFORM)
@TRANSMIT@
@DEFERRED@
#endif
int main() {
  MyMesh room;
  mesh::Packet packet;
  packet.setPathHashSizeAndCount(1, 0);
  for (uint8_t route : {ROUTE_TYPE_FLOOD, ROUTE_TYPE_TRANSPORT_FLOOD}) {
    packet.header = route | (PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT);
    packet.transport_codes[0] = route == ROUTE_TYPE_FLOOD ? 0 : 0x1234;
    assert(!room.allowPacketForward(&packet));
    if (route == ROUTE_TYPE_TRANSPORT_FLOOD) {
      room._cli.allow_unknown = true;
      assert(room.allowPacketForward(&packet));
      room._cli.allow_unknown = false;
    }
    room.recv_pkt_region = &room;
    assert(room.allowPacketForward(&packet));
    room._prefs.disable_fwd = 1;
    assert(!room.allowPacketForward(&packet));
    room._prefs.disable_fwd = 0;
    room.recv_pkt_region = nullptr;
  }
#if MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE
  room.recv_pkt_regionless_scope_set = true;
  assert(room.allowPacketForward(&packet));
  room.flood_rules.blocked = true;
  assert(!room.allowPacketForward(&packet));
  room.flood_rules.blocked = false;
#endif
  packet.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT);
  assert(room.allowPacketForward(&packet));
#if defined(ESP32_PLATFORM)
  room._prefs.disable_fwd = 1;
  room._prefs.trace_when_repeat_off = 1;
  assert(!room.allowPacketForward(&packet));
  packet.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TRACE << PH_TYPE_SHIFT);
  assert(room.allowPacketForward(&packet));
  packet.path_len = 1;
  assert(room.allowPacketTransmit(&packet));
  room._prefs.trace_when_repeat_off = 0;
  assert(!room.allowPacketForward(&packet));
  assert(!room.allowPacketTransmit(&packet));
  packet.path_len = 0;
  assert(room.allowPacketTransmit(&packet));

  room._prefs.disable_fwd = 0;
  room.recv_pkt_region = &room;
  packet.radio_profile = 0;
  packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT);
  room._cli.policy.enabled = 2;
  assert(!room.allowPacketForward(&packet));
  assert(!room.deferredFloodCandidate(&packet, 0, false));
  assert(room.deferredFloodCandidate(&packet, 1, true));
  assert(!room.deferredFloodCandidate(&packet, 1, false));
  packet.forwarded = false;
  assert(room.allowPacketTransmit(&packet));
  packet.forwarded = true;
  assert(!room.allowPacketTransmit(&packet));
  packet.radio_profile = 1;
  assert(room.allowPacketTransmit(&packet));
  assert(room.allowPacketForward(&packet));

  room._prefs.flood_max_unscoped = 64;
  room._prefs.flood_channel_data_enabled = 0;
  assert(!room.allowPacketForward(&packet));
  room._prefs.flood_channel_data_max_hops = 1;
  packet.setPathHashSizeAndCount(1, 1);
  assert(room.allowPacketForward(&packet));
  packet.setPathHashSizeAndCount(1, 2);
  assert(!room.allowPacketForward(&packet));
  packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
  assert(room.allowPacketForward(&packet));
#endif
}
'''.replace("@PACKET@", constructor).replace(
            "@ALLOW@", extract_braced(room, "bool MyMesh::allowPacketForward(")).replace(
            "@TRANSMIT@", extract_braced(room, "bool MyMesh::allowPacketTransmit(")).replace(
            "@DEFERRED@", extract_braced(room, "bool MyMesh::deferredFloodCandidate("))
        with tempfile.TemporaryDirectory(prefix="mesh-room-regions-") as directory:
            cpp, exe = Path(directory) / "regions.cpp", Path(directory) / "regions"
            cpp.write_text(source)
            for flags, enabled in (([], 0), (["-DMESH_ENABLE_ROOM_FLOOD_RULE_ENGINE=1"], 1),
                                   (["-DESP32_PLATFORM=1"], 1)):
                with self.subTest(flags=flags):
                    built = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                        *flags, f"-DEXPECT_ROOM_RULES={enabled}", "-I", str(ROOT / "src"),
                        "-I", str(ROOT / "examples/simple_room_server"),
                        str(cpp), "-o", str(exe),
                    ], text=True, capture_output=True, timeout=30)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    subprocess.run([str(exe)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
