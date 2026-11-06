"""Execute actual repeater trace gates with the production Packet definition."""
from pathlib import Path
from itertools import product
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_morse_tx import portable_radio_header

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <new>
#include <Packet.h>
#include <helpers/RoutingPolicy.h>
#include <helpers/MorseReplyHandoff.h>
#define LOOP_DETECT_OFF 0
#define LOOP_DETECT_MINIMAL 1
#define LOOP_DETECT_MODERATE 2
#define LOOP_DETECT_STRICT 3
#define FLOOD_CHANNEL_HOPS_ALL 255
const uint8_t max_loop_minimal[] = {1}, max_loop_moderate[] = {2}, max_loop_strict[] = {3};
uint32_t millis() { return 0; }
namespace mesh {
@CONSTRUCTOR@
namespace flood_profiles {
struct DeferredFloods {
  template<class Packet, class Policy> void remember(const Packet*, const Policy&, uint32_t) {}
};
template<class Packet, class Policy> bool allowIngress(const Packet*, const Policy&) { return true; }
template<class Packet, class Policy> bool allowEgress(const Packet*, const Policy&) { return true; }
}
// The boundary records delegation to the existing OTA/profile transmit veto.
class Mesh {
public:
  mutable unsigned base_calls = 0;
  bool base_allowed = true;
  virtual ~Mesh() = default;
  virtual bool allowPacketTransmit(const Packet*) const {
    ++base_calls;
    return base_allowed;
  }
};
}
struct SimpleMeshTables {
  struct RecentRepeaterInfo { int8_t snr_x4 = 12; } recent;
  bool known = false;
  const RecentRepeaterInfo* findRecentRepeaterByHash(const uint8_t*, uint8_t) const {
    return known ? &recent : nullptr;
  }
};
struct MyMesh : mesh::Mesh {
  mesh::flood_profiles::DeferredFloods* deferred_floods_ = nullptr;
  struct CLI {
    struct Profiles {} profiles;
    const Profiles& radioProfiles() const { return profiles; }
    mesh::MorseReplyHandoff handoff;
    bool batteryAllowsForward(bool) const { return true; }
    bool batteryAllowsTransmit(const mesh::Packet*) const { return true; }
    mesh::MorseReplyHandoff& morseReply() { return handoff; }
    const mesh::MorseReplyHandoff& morseReply() const { return handoff; }
  } _cli;
  struct Prefs {
    uint8_t disable_fwd = 1, trace_when_repeat_off = 0;
    uint8_t flood_max = 64, flood_max_unscoped = 64, flood_max_advert = 64;
    uint8_t flood_channel_data_enabled = 1, loop_detect = LOOP_DETECT_OFF;
    uint8_t direct_retry_enabled = 1, direct_retry_recent_enabled = 0;
    int8_t direct_retry_snr_margin_x4 = 0;
  } _prefs;
  bool recv_pkt_channel_scope_rejected = false, recv_pkt_regionless_scope_set = false;
  bool recv_pkt_channel_scope_bypass = false, looped = false;
  const void* recv_pkt_region = this;
  unsigned recv_pkt_filter_match_mask = 0;
  bool filter_block = false, moderation_block = false, clock_sync_mesh_edge_enabled = false;
  unsigned commits = 0, clock_samples = 0;
  SimpleMeshTables tables;
  bool floodChannelDataHopApplies(const mesh::Packet*) const { return true; }
  bool isLooped(const mesh::Packet*, const uint8_t*) { return looped; }
  bool shouldBlockFloodPacketForward(const mesh::Packet*, unsigned) { return filter_block; }
  bool shouldBlockFloodGroupTextForward(const mesh::Packet*) { return moderation_block; }
  void commitFloodPacketFilterRates(const mesh::Packet*, unsigned) { ++commits; }
  void recordAcceptedFloodClockSample(const mesh::Packet*) { ++clock_samples; }
  const SimpleMeshTables* getTables() const { return &tables; }
  int8_t getDirectRetryMinSNRX4() const { return 12; }
  bool allowPacketForward(const mesh::Packet*);
  bool allowPacketTransmit(const mesh::Packet*) const override;
  bool allowDirectRetry(const mesh::Packet*, const uint8_t*, uint8_t) const;
};
@METHODS@

int main() {
  MyMesh node;
  mesh::Packet p;
  // Admission is an exact opt-in, not a general forwarding bypass. Test every
  // route/type and invalid stored values with the actual header accessors.
  for (uint8_t pref : {uint8_t(0), uint8_t(1), uint8_t(2), uint8_t(255)}) {
    node._prefs.trace_when_repeat_off = pref;
    for (uint8_t route = 0; route < 4; ++route) {
      for (uint8_t type = 0; type < 16; ++type) {
        p.header = route | (type << PH_TYPE_SHIFT);
        p.path_len = 0;
        assert(node.allowPacketForward(&p) ==
            (pref == 1 && p.isRouteDirect() && type == PAYLOAD_TYPE_TRACE));
        for (uint16_t path : {uint16_t(0), uint16_t(1), uint16_t(MAX_PATH_SIZE)}) {
          p.path_len = path;
          const bool blocked = pref != 1 && p.isRouteDirect()
              && type == PAYLOAD_TYPE_TRACE && path > 0;
          const unsigned before = node.base_calls;
          assert(node.allowPacketTransmit(&p) == !blocked);
          assert(node.base_calls == before + (blocked ? 0 : 1));
          assert(node.allowDirectRetry(&p, nullptr, 0) == !blocked);
        }
      }
    }
  }
  assert(!node.allowPacketForward(nullptr));
  assert(node.allowPacketTransmit(nullptr));
  assert(node.allowDirectRetry(nullptr, nullptr, 0));

  // repeat on restores the original gate; switching it off retires a relay,
  // but never a locally generated trace whose SNR path starts empty.
  p.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TRACE << PH_TYPE_SHIFT);
  p.path_len = 1;
  node._prefs.trace_when_repeat_off = 0;
  node._prefs.disable_fwd = 0;
  assert(node.allowPacketForward(&p) && node.allowPacketTransmit(&p));
  node._prefs.disable_fwd = 1;
  assert(!node.allowPacketForward(&p) && !node.allowPacketTransmit(&p));
  node._prefs.trace_when_repeat_off = 1;
  assert(node.allowPacketForward(&p) && node.allowPacketTransmit(&p));
  node._prefs.trace_when_repeat_off = 0;
  assert(!node.allowPacketTransmit(&p) && !node.allowDirectRetry(&p, nullptr, 0));
  p.path_len = 0;
  assert(node.allowPacketTransmit(&p) && node.allowDirectRetry(&p, nullptr, 0));

  // An enabled opt-in still delegates the base hard-drain/profile safety veto.
  p.path_len = 1;
  node._prefs.trace_when_repeat_off = 1;
  node.base_allowed = false;
  assert(!node.allowPacketTransmit(&p));
  node.base_allowed = true;
  node._prefs.direct_retry_enabled = 0;
  assert(!node.allowDirectRetry(&p, nullptr, 0));
  node._prefs.direct_retry_enabled = 1;
  node._prefs.direct_retry_recent_enabled = 1;
  const uint8_t hop[] = {0x42};
  assert(node.allowDirectRetry(&p, hop, 1)); // unknown remains eligible
  node.tables.known = true;
  node.tables.recent.snr_x4 = 11;
  assert(!node.allowDirectRetry(&p, hop, 1));
  node.tables.recent.snr_x4 = 12;
  assert(node.allowDirectRetry(&p, hop, 1));
  node._prefs.direct_retry_snr_margin_x4 = 1;
  assert(!node.allowDirectRetry(&p, hop, 1));

  // The opt-in never overrides existing flood-hop/region/scope/loop rules,
  // or spends moderation quota for a packet rejected earlier in the chain.
  node._prefs.disable_fwd = 0;
  p.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
  p.setPathHashSizeAndCount(1, 1);
  assert(node.allowPacketForward(&p));
  const unsigned committed = node.commits;
  node._prefs.flood_max = 0;
  assert(!node.allowPacketForward(&p));
  node._prefs.flood_max = 64;
  node.recv_pkt_channel_scope_rejected = true;
  assert(!node.allowPacketForward(&p));
  node.recv_pkt_channel_scope_rejected = false;
  node.recv_pkt_region = nullptr;
  assert(!node.allowPacketForward(&p));
  node.recv_pkt_region = &node;
  node._prefs.loop_detect = LOOP_DETECT_MINIMAL;
  node.looped = true;
  assert(!node.allowPacketForward(&p));
  node.looped = false;
#if !defined(PORTABLE_MQTT_OBSERVER)
  node.filter_block = true;
  assert(!node.allowPacketForward(&p));
  node.filter_block = false;
  node.moderation_block = true;
  assert(!node.allowPacketForward(&p));
  node.moderation_block = false;
  assert(node.commits == committed);
#else
  (void)committed;
#endif
  assert(node.allowPacketForward(&p));
  node._prefs.disable_fwd = 1;
  assert(!node.allowPacketForward(&p));

  // Execute the real exact-reply handoff, not an always-false fixture stub.
  // The listen acknowledgement and its profile copy must never retry while
  // owned: retransmitting them can delay RX ownership and corrupt capture.
  node._prefs.direct_retry_recent_enabled = 0;
  node._prefs.trace_when_repeat_off = 1;
  mesh::Packet reply, copy, unrelated;
  reply.header = copy.header = unrelated.header =
      ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TRACE << PH_TYPE_SHIFT);
  reply.path_len = copy.path_len = unrelated.path_len = 0;
  const uint8_t actor[32] = {0x42};
  auto& handoff = node._cli.morseReply();
  handoff.beginCommand(actor);
#if MESH_ENABLE_MORSE_RX
  assert(handoff.stage(902100, 25, 60000, 100));
  handoff.prepareReply(&reply);
  handoff.copy(&reply, &copy);
  handoff.armReply(&reply, true);
  handoff.endCommand();
  assert(handoff.pending() && handoff.ownsReply(&reply) && handoff.ownsReply(&copy));
  assert(!node.allowDirectRetry(&reply, nullptr, 0));
  assert(!node.allowDirectRetry(&copy, nullptr, 0));
  assert(node.allowDirectRetry(&unrelated, nullptr, 0)); // identical content is not ownership
  assert(node.allowDirectRetry(nullptr, nullptr, 0));
  handoff.complete(&reply);
  assert(!handoff.ownsReply(&reply) && handoff.ownsReply(&copy));
  assert(node.allowDirectRetry(&reply, nullptr, 0));
  assert(!node.allowDirectRetry(&copy, nullptr, 0));
  handoff.fail(&copy);
  assert(!handoff.ownsReply(&copy));
  assert(node.allowDirectRetry(&copy, nullptr, 0));
  handoff.cancel();
#else
  assert(!handoff.stage(902100, 25, 60000, 100));
  handoff.prepareReply(&reply);
  handoff.copy(&reply, &copy);
  handoff.armReply(&reply, true);
  handoff.endCommand();
  assert(!handoff.pending() && !handoff.ownsReply(&reply) && !handoff.ownsReply(&copy));
  assert(node.allowDirectRetry(&reply, nullptr, 0));
  assert(node.allowDirectRetry(&copy, nullptr, 0));
#endif
}
'''


class RepeaterTraceForwardingTest(unittest.TestCase):
    def test_actual_repeater_methods(self):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('a host C++17 compiler is required')
        source = (ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text(encoding='utf-8')
        methods = '\n'.join(extract_braced(source, signature) for signature in (
            'bool MyMesh::allowPacketForward(',
            'bool MyMesh::allowPacketTransmit(',
            'bool MyMesh::allowDirectRetry('))
        packet_source = (ROOT / 'src/Packet.cpp').read_text(encoding='utf-8')
        code = HARNESS.replace('@METHODS@', methods).replace(
            '@CONSTRUCTOR@', extract_braced(packet_source, 'Packet::Packet()'))
        flags = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                 '-fno-pie', '-no-pie'] if sys.platform.startswith('linux') else []
        with tempfile.TemporaryDirectory(prefix='repeater-trace-forwarding-') as directory:
            work = Path(directory)
            cpp = work / 'test.cpp'
            cpp.write_text(code, encoding='utf-8')
            (work / 'Dispatcher.h').write_text(portable_radio_header(), encoding='utf-8')
            for portable, morse_rx, history in product((False, True), (0, 1), (0, 1)):
                with self.subTest(portable=portable, morse_rx=morse_rx, history=history):
                    binary = work / 'test'
                    built = subprocess.run([
                        compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter',
                        f'-DMESH_ENABLE_MORSE_RX={morse_rx}',
                        f'-DMESH_ENABLE_TELEMETRY_HISTORY={history}',
                        '-DMESH_ENABLE_FLOOD_GROUP_MODERATION=1', '-DMESH_ENABLE_CLOCK_SYNC=1',
                        *(['-DPORTABLE_MQTT_OBSERVER=1'] if portable else []), *flags,
                        '-I' + str(work), '-I' + str(ROOT / 'test/mocks'),
                        '-I' + str(ROOT / 'src'), str(cpp), '-o', str(binary)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True,
                                            text=True, timeout=10)
                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)

    def test_repeater_only_override(self):
        header = (ROOT / 'examples/simple_repeater/MyMesh.h').read_text(encoding='utf-8')
        self.assertIn('bool allowPacketTransmit(const mesh::Packet* packet) const override;', header)
        for role in ('simple_room_server', 'simple_sensor', 'companion_radio'):
            for path in (ROOT / 'examples' / role).glob('MyMesh.*'):
                self.assertNotIn('trace_when_repeat_off', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
