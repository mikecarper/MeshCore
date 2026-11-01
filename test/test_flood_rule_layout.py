"""Exercise production flood-entry layouts and independent packed flag states."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <type_traits>
#include <helpers/FloodFilterPolicy.h>
#include <helpers/FloodRuleCLI.h>
#define FLOOD_PACKET_FILTER_SCOPE_NAME_LEN 32
#define FLOOD_GROUP_MODERATION_NAME_LEN 32
#define FLOOD_PACKET_FILTER_PATH_PREFIX_BYTES_MAX 9
static constexpr uint8_t NAME_LEN = 32, PATH_PREFIX_BYTES_MAX = 9;
'''

SCENARIOS = r'''
static_assert(sizeof(RepeaterEntry) == 296 && sizeof(RoomEntry) == 328,
              "one-byte validated payload bounds save four bytes per rule row");
static_assert(sizeof(LegacyEntry) == 40, "retain legacy STM32 rule layout");
static_assert(alignof(RepeaterEntry) >= alignof(uint32_t));
static_assert(alignof(RoomEntry) >= alignof(uint32_t));
#define CHECK_ALIGNED(T) \
  static_assert(offsetof(T, rate_window_started) % alignof(uint32_t) == 0); \
  static_assert(offsetof(T, rate_window_count) % alignof(uint16_t) == 0); \
  static_assert(offsetof(T, rate_per_minute) % alignof(uint16_t) == 0); \
  static_assert(std::is_trivially_copyable<T>::value)
CHECK_ALIGNED(RepeaterEntry);
CHECK_ALIGNED(RoomEntry);

template<class Entry>
auto setTargetByte(Entry& e, bool region, size_t offset, char value, int)
    -> decltype(e.target_name, void()) {
  e.target_is_region = region;
  e.target_name[offset] = value;
}
template<class Entry>
void setTargetByte(Entry& e, bool region, size_t offset, char value, long) {
  (region ? e.target_region_name : e.scope_name)[offset] = value;
}
template<class Entry> void setFlags(Entry& e, unsigned flags) {
  e.active = flags & 1;
  e.suspend_on_temp_radio = flags & 2;
  e.match_blacklisted_path = flags & 4;
  e.scope_uses_slow_timing = flags & 8;
  e.drop_on_match = flags & 16;
  e.rate_limit_enabled = flags & 32;
  e.stop_on_match = flags & 64;
  e.retry_on_match = flags & 128;
  e.rate_window_active = flags & 256;
}
template<class Entry> unsigned flagsOf(const Entry& e) {
  return unsigned(e.active) | unsigned(e.suspend_on_temp_radio) * 2
      | unsigned(e.match_blacklisted_path) * 4
      | unsigned(e.scope_uses_slow_timing) * 8 | unsigned(e.drop_on_match) * 16
      | unsigned(e.rate_limit_enabled) * 32 | unsigned(e.stop_on_match) * 64
      | unsigned(e.retry_on_match) * 128 | unsigned(e.rate_window_active) * 256;
}
template<class Entry> void exerciseFlags() {
  // Exhaustive combinations detect adjacent flag corruption; copy/clear are
  // the same operations used by production rule load, delete, and replace.
  for (unsigned flags = 0; flags < 512; ++flags) {
    Entry e{};
    e.rate_window_started = 0xfedcba98;
    e.rate_window_count = 0x7654;
    e.rate_per_minute = 0x3210;
    setFlags(e, flags);
    assert(flagsOf(e) == flags);
    assert(e.rate_window_started == 0xfedcba98);
    assert(e.rate_window_count == 0x7654 && e.rate_per_minute == 0x3210);
    Entry copied = e;
    assert(flagsOf(copied) == flags);
    std::memcpy(&copied, &e, sizeof(e));
    assert(flagsOf(copied) == flags);
    // These actual decoders require ordinary bool references. Packing must
    // preserve this API and must not alter any unrelated flag.
    assert(FloodFilterPolicy::decodeStoredRuleActive(5, copied.active,
                                                    copied.transport_modes));
    assert(FloodFilterPolicy::decodeStoredRuleChannel(16, copied.channel_key_len,
                                                     copied.retry_on_match));
    assert(flagsOf(copied) == ((flags | 1) & ~128U));
    assert(copied.transport_modes == 4 && copied.channel_key_len == 16);
    std::memset(&copied, 0, sizeof(copied));
    assert(flagsOf(copied) == 0 && copied.rate_window_started == 0);
  }
}
template<class Entry> void exerciseConfigurationEquality() {
  Entry original{};
  original.active = true;
  original.rate_limit_enabled = true;
  original.rate_per_minute = 5;
  Entry live = original;
  live.rate_window_started = 0xfedcba98;
  live.rate_window_count = 3;
  live.rate_window_active = true;
  assert(FloodFilterPolicy::sameRuleConfiguration(original, live));
  // Bytewise struct comparisons are forbidden: neither alignment padding nor
  // live counters identify a rule. Poison trailing padding to enforce this.
  for (size_t offset = offsetof(Entry, extra) + sizeof(live.extra); offset < sizeof(Entry); ++offset)
    reinterpret_cast<unsigned char*>(&live)[offset] = 0xa5;
  assert(FloodFilterPolicy::sameRuleConfiguration(original, live));
  // Every configured field must still distinguish two rows. Arrays include
  // their final byte so accidental prefix-only comparisons cannot pass.
  for (unsigned field = 0; field < 26; ++field) {
    Entry changed = original;
    switch (field) {
      case 0: changed.active = false; break;
      case 1: changed.payload_type = 1; break;
      case 2: changed.min_hops = 1; break;
      case 3: changed.max_hops = 1; break;
      case 4: changed.suspend_on_temp_radio = true; break;
      case 5: setTargetByte(changed, false, 31, 1, 0); break;
      case 6: changed.match_blacklisted_path = true; break;
      case 7: changed.scope_uses_slow_timing = true; break;
      case 8: changed.incoming_scope_kind = 1; break;
      case 9: changed.incoming_scope_name[sizeof(changed.incoming_scope_name)-1] = 1; break;
      case 10: changed.channel_key_len = 1; break;
      case 11: changed.channel_hash = 1; break;
      case 12: changed.channel_secret[sizeof(changed.channel_secret)-1] = 1; break;
      case 13: changed.channel_name[sizeof(changed.channel_name)-1] = 1; break;
      case 14: changed.path_hash_size = 1; break;
      case 15: changed.path_hops = 1; break;
      case 16: changed.path[sizeof(changed.path)-1] = 1; break;
      case 17: setTargetByte(changed, true, 31, 1, 0); break;
      case 18: changed.drop_on_match = true; break;
      case 19: changed.rate_limit_enabled = false; break;
      case 20: changed.rate_per_minute = 6; break;
      case 21: changed.priority = 1; break;
      case 22: changed.stop_on_match = true; break;
      case 23: changed.retry_on_match = true; break;
      case 24: changed.transport_modes = 2; break;
      case 25: setTargetByte(changed, false, 0, '#', 0); break;
    }
    assert(!FloodFilterPolicy::sameRuleConfiguration(original, changed));
    assert(!FloodFilterPolicy::sameRuleConfiguration(changed, original));
  }
  for (unsigned field = 0; field < 11; ++field) {
    Entry changed = original;
    switch (field) {
      case 0: changed.extra.min_payload = 1; break;
      case 1: changed.extra.max_payload = 1; break;
      case 2: changed.extra.payload_set = 1; break;
      case 3: changed.extra.route = FloodRuleExtensions::DirectRoute; break;
      case 4: changed.extra.probability = 30; break;
      case 5: changed.extra.path_position = FloodRuleExtensions::Anywhere; break;
      case 6: changed.extra.disabled = 1; break;
      case 7: changed.extra.path_tail[2] = 1; break;
      case 8: std::strcpy(changed.extra.sender, "Alice"); break;
      case 9: std::strcpy(changed.extra.text, "weather"); break;
      case 10: changed.extra.path_tail[0] = 1; break;
    }
    assert(!FloodFilterPolicy::sameRuleConfiguration(original, changed));
    assert(!FloodFilterPolicy::sameRuleConfiguration(changed, original));
  }
}
template<class Rules, class Entry> void exerciseProductionSlotSelection() {
  Rules rules;
  Entry candidate{};
  candidate.active = true; candidate.payload_type = PAYLOAD_TYPE_ADVERT;
  candidate.max_hops = 63; candidate.rate_limit_enabled = true;
  candidate.rate_per_minute = 5;
  assert(rules.select(candidate) == 0);
  rules.storage[0] = candidate;
  rules.storage[0].rate_window_started = 100;
  rules.storage[0].rate_window_count = 3;
  rules.storage[0].rate_window_active = true;
  assert(rules.select(candidate) == 0); // Reuse despite a live rate window.
  candidate.path_hash_size = 2;
  assert(rules.select(candidate) == 1); // Distinct configuration needs a slot.
  rules.storage[1] = candidate;
  assert(rules.select(candidate) == 1);
  assert(rules.select(candidate, 3) == 3); // Explicit index remains authoritative.
}
int main() {
  // Every old flag combination can coexist with either target kind. Neither
  // the tag nor name views may corrupt independent flags or leak a stale name.
  for (unsigned flags = 0; flags < 512; ++flags) {
    for (unsigned region = 0; region < 2; ++region) {
      RepeaterEntry e{};
      setFlags(e, flags);
      e.target_is_region = region;
      std::strcpy(e.target_name, "1234567890123456789012345678901");
      assert(flagsOf(e) == flags);
      const auto& selected = region ? e.regionName() : e.scopeName();
      const auto& inactive = region ? e.scopeName() : e.regionName();
      assert(!std::strcmp(selected, e.target_name));
      for (char byte : inactive) assert(byte == 0);
      RepeaterEntry copied = e;
      assert(copied.target_is_region == region);
      assert(!std::memcmp(copied.target_name, e.target_name, 32));
      assert(flagsOf(copied) == flags);
    }
  }
  exerciseFlags<RepeaterEntry>();
  exerciseFlags<RoomEntry>();
  exerciseConfigurationEquality<RepeaterEntry>();
  exerciseConfigurationEquality<RoomEntry>();
  exerciseProductionSlotSelection<RepeaterRules, RepeaterEntry>();
  exerciseProductionSlotSelection<RoomRules, RoomEntry>();
}
'''


def production_layouts():
    repeater = extract_braced(
        (ROOT / 'examples/simple_repeater/MyMesh.h').read_text(),
        'struct FloodPacketFilterEntry')
    room = extract_braced(
        (ROOT / 'examples/simple_room_server/FloodRuleEngine.h').read_text(),
        'struct Entry')
    return ('#define MESH_ENABLE_FLOOD_RULE_ENGINE 1\n'
            + repeater.replace('struct FloodPacketFilterEntry', 'struct RepeaterEntry')
            + ';\n' + room.replace('struct Entry', 'struct RoomEntry') + ';\n'
            + '#undef MESH_ENABLE_FLOOD_RULE_ENGINE\n'
            + '#define MESH_ENABLE_FLOOD_RULE_ENGINE 0\n'
            + repeater.replace('struct FloodPacketFilterEntry', 'struct LegacyEntry') + ';\n')


def production_slot_selection():
    bodies = []
    for role, marker, end, members in (
        ('simple_repeater', 'void MyMesh::setFloodPacketFilter(',
         '\n  if (slot < 0) {\n    strcpy(reply, "Err - filter table full");',
         'using FloodPacketFilterEntry = RepeaterEntry;\n'
         'RepeaterEntry storage[63]{};\n'
         'RepeaterEntry* flood_packet_filters = storage;\n'
         'uint8_t flood_packet_filter_slots = 63, flood_channel_data_rule_slot = 0xff;'),
        ('simple_room_server', 'void FloodRuleEngine::set(',
         '\n  if (slot < 0) {\n    copyString(reply, "Err - filter table full", 160);',
         'using Entry = RoomEntry;\n'
         'static constexpr uint8_t RULE_SLOTS = 31;\n'
         'RoomEntry storage[RULE_SLOTS]{};\nEntry* _entries = storage;'),
    ):
        file = 'MyMesh.cpp' if role == 'simple_repeater' else 'FloodRuleEngine.cpp'
        method = extract_braced((ROOT / 'examples' / role / file).read_text(), marker)
        # Compile both real setter slot-selection paths, not a test rewrite.
        body = method[method.index('  int slot = requested_slot;'):method.index(end)]
        name = 'RepeaterRules' if role == 'simple_repeater' else 'RoomRules'
        entry = 'RepeaterEntry' if role == 'simple_repeater' else 'RoomEntry'
        bodies.append('struct ' + name + ' {\n' + members
                      + '\nint select(const ' + entry + '& candidate, int requested_slot=-1) {\n'
                      + body + '\nreturn slot;\n}\n};\n')
    return ''.join(bodies)


class FloodRuleLayoutTests(unittest.TestCase):
    def test_packed_flags_keep_all_states_and_aligned_counters(self):
        with tempfile.TemporaryDirectory(prefix='flood-rule-layout-') as temp:
            path = Path(temp)
            source = path / 'test.cpp'
            source.write_text(PRELUDE + production_layouts() + production_slot_selection() + SCENARIOS)
            exe = path / 'test'
            subprocess.run(['g++', '-std=c++17', '-O1', '-g',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            '-no-pie', '-I' + str(ROOT / 'src'), str(source),
                            '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True,
                           env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1'})

    def test_arm_layouts_keep_the_same_bound_and_natural_alignment(self):
        compilers = sorted((Path.home() / '.platformio/packages').glob(
            'toolchain-gccarmnoneeabi*/bin/arm-none-eabi-g++'))
        if not compilers:
            self.skipTest('ARM compiler is not installed')
        # Syntax/type checks invoke the compiler directly, never PlatformIO.
        for compiler in compilers:
            with self.subTest(compiler=compiler.parent.parent.name):
                subprocess.run([str(compiler), '-std=c++17', '-mcpu=cortex-m4',
                                '-mthumb', '-fsyntax-only', '-x', 'c++',
                                '-I' + str(ROOT / 'src'), '-'], check=True,
                               input=PRELUDE + production_layouts() + production_slot_selection() + SCENARIOS,
                               text=True)


if __name__ == '__main__':
    unittest.main()
