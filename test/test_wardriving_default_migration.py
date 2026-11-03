"""Exercise production wardriving default migration and atomic policy storage."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_flood_policy_capacity import CLASS, HARNESS, PRELUDE
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

SCENARIOS = r'''
using Entry = MyMesh::FloodPacketFilterEntry;
std::map<std::string, std::vector<uint8_t>> snapshot(const MemoryFS& fs) {
  std::map<std::string, std::vector<uint8_t>> result;
  for (const auto& file : fs.files) result[file.first] = *file.second;
  return result;
}
std::map<std::string, std::vector<uint8_t>> policies(const MemoryFS& fs) {
  auto result = snapshot(fs);
  result.erase(FLOOD_WARDRIVING_DEFAULTS_FILE);
  return result;
}
Entry legacyDefault() {
  // Freeze the pre-upgrade factory row independently of the new constructor.
  // These key bytes are the literal "#wardriving" under this harness's
  // documented deterministic SHA stand-in; its one-byte channel hash is zero.
  static const uint8_t old_key[16] = {
    0x23, 0x76, 0x63, 0x71, 0x60, 0x77, 0x6f, 0x71,
    0x61, 0x67, 0x6d, 0x28, 0x7b, 0x6c, 0x7c, 0x6b
  };
  Entry row{};
  row.active = true;
  row.payload_type = 0xff;
  row.min_hops = 5;
  row.max_hops = 63;
  row.channel_key_len = 16;
  std::copy(std::begin(old_key), std::end(old_key), row.channel_secret);
  std::strcpy(row.channel_name, "#wardriving");
  row.drop_on_match = true;
  return row;
}
void populate(MyMesh& node, uint8_t slot = 1, bool extended = false) {
  node.seedDefaultFloodPacketFilters();
  node.storage[1] = {};
  node.storage[slot] = legacyDefault();
  auto& unrelated = node.storage[7];
  unrelated = {};
  unrelated.active = true;
  unrelated.payload_type = PAYLOAD_TYPE_ADVERT;
  unrelated.min_hops = 11;
  unrelated.max_hops = 40;
  unrelated.drop_on_match = true;
  unrelated.priority = 17;
  if (extended) unrelated.extra.probability = 50;
  std::strcpy(node.flood_channel_direct_scopes[0], "#Local");
  node.flood_channel_scopes[0].target_id = 1;
  node.flood_channel_scopes[0].selector =
      FloodFilterPolicy::encodeChannelScopeTargetSelector(0, true);
  auto& data = node.storage[6];
  data = {};
  data.active = true;
  data.payload_type = PAYLOAD_TYPE_GRP_DATA;
  data.min_hops = 4;
  data.max_hops = FLOOD_PACKET_FILTER_MAX_HOPS;
  data.drop_on_match = true;
  node.flood_channel_data_rule_slot = 6;
  node.flood_channel_data_rule_max_hops = 3;
  node.flood_packet_filter_blacklist_count = 1;
  node.flood_packet_filter_blacklist[0][0] = 0x34;
  node.flood_packet_filter_blacklist[0][1] = 0x56;
  node.flood_packet_filter_blacklist[0][2] = 0x78;
}
void checkOthers(const MyMesh& node, const MyMesh& before, uint8_t slot) {
  for (size_t i = 0; i < node.storage.size(); ++i) {
    if (i != slot) assert(FloodFilterPolicy::sameRuleConfiguration(node.storage[i], before.storage[i]));
  }
  assert(!std::memcmp(node.flood_channel_scopes, before.flood_channel_scopes,
                      sizeof(node.flood_channel_scopes)));
  assert(!std::memcmp(node.flood_channel_direct_scopes, before.flood_channel_direct_scopes,
                      sizeof(node.flood_channel_direct_scopes)));
  assert(node.flood_packet_filter_blacklist_count == before.flood_packet_filter_blacklist_count);
  assert(!std::memcmp(node.flood_packet_filter_blacklist, before.flood_packet_filter_blacklist,
                      sizeof(node.flood_packet_filter_blacklist)));
  assert(node.flood_channel_data_rule_slot == before.flood_channel_data_rule_slot);
  assert(node.flood_channel_data_rule_max_hops == before.flood_channel_data_rule_max_hops);
}
void migrateSavedDefaults() {
  for (bool extended : {false, true}) {
    for (uint8_t slot : {uint8_t(1), uint8_t(5)}) {
      for (const char* path : {FLOOD_PACKET_FILTER_FILE,
                               FLOOD_PACKET_FILTER_TEMP_FILE,
                               FLOOD_PACKET_FILTER_BACKUP_FILE}) {
        MemoryFS fs; MyMesh writer(fs, 12); populate(writer, slot, extended);
        assert(writer.saveFloodPacketFilters());
        const auto old = fs.get(FLOOD_PACKET_FILTER_FILE);
        assert(!std::memcmp(old.data(), extended ? "FPF8" : "FPF7", 4));
        if (std::strcmp(path, FLOOD_PACKET_FILTER_FILE)) {
          assert(fs.rename(FLOOD_PACKET_FILTER_FILE, path));
        }
        MyMesh boot(fs, 12); assert(boot.loadFloodPacketFilters());
        assert(boot.flood_policy_has_embedded_sections);
        assert(boot.storage[slot].min_hops == 5);
        boot.storage[slot].rate_window_started = 123;
        boot.storage[slot].rate_window_count = 4;
        boot.storage[slot].rate_window_active = true;
        const auto writes = fs.write_opens;
        assert(boot.migrateDefaultWardrivingFilter());
        assert(boot.storage[slot].min_hops == 3);
        assert(fs.write_opens == writes + 2);  // Policy, then one-time marker.
        assert(fs.exists(FLOOD_WARDRIVING_DEFAULTS_FILE));
        assert(fs.get(FLOOD_WARDRIVING_DEFAULTS_FILE).empty());
        assert(boot.storage[slot].rate_window_started == 123);
        assert(boot.storage[slot].rate_window_count == 4 && boot.storage[slot].rate_window_active);
        checkOthers(boot, writer, slot);
        assert(fs.get(FLOOD_PACKET_FILTER_FILE) != old);
        const auto committed = snapshot(fs);
        MyMesh reboot(fs, 12); assert(reboot.loadFloodPacketFilters());
        assert(reboot.storage[slot].min_hops == 3);
        const auto previous_writes = fs.write_opens;
        assert(reboot.migrateDefaultWardrivingFilter());
        assert(fs.write_opens == previous_writes);
        assert(snapshot(fs) == committed);  // No repeat write on later boots.
        checkOthers(reboot, writer, slot);
      }
    }
  }
}
void preserveCustomizedRules() {
  using Edit = void (*)(Entry&);
  const Edit edits[] = {
    [](Entry& e) { e.active = false; },
    [](Entry& e) { e.payload_type = PAYLOAD_TYPE_GRP_TXT; },
    [](Entry& e) { e.min_hops = 4; },
    [](Entry& e) { e.max_hops = 62; },
    [](Entry& e) { e.suspend_on_temp_radio = true; },
    [](Entry& e) { e.match_blacklisted_path = true; },
    [](Entry& e) { e.incoming_scope_kind = FloodFilterPolicy::RULE_IN_NONE; },
    [](Entry& e) { e.incoming_scope_kind = FloodFilterPolicy::RULE_IN_SCOPE;
                  std::strcpy(e.incoming_scope_name, "#Town"); },
    [](Entry& e) { e.incoming_scope_kind = FloodFilterPolicy::RULE_IN_REGION;
                  std::strcpy(e.incoming_scope_name, "Town"); },
    [](Entry& e) { e.channel_key_len = 32; },
    [](Entry& e) { std::strcpy(e.channel_name, "#Wardriving"); },
    [](Entry& e) { e.path_hash_size = 2; },
    [](Entry& e) { e.path_hash_size = 1; e.path_hops = 1; e.path[0] = 0x42; },
    [](Entry& e) { e.priority = 1; },
    [](Entry& e) { e.stop_on_match = true; },
    [](Entry& e) { e.transport_modes = FloodFilterPolicy::RULE_MODE_BRIDGE; },
    [](Entry& e) { e.drop_on_match = false; e.retry_on_match = true; },
    [](Entry& e) { e.drop_on_match = false; e.rate_limit_enabled = true; e.rate_per_minute = 12; },
    [](Entry& e) { e.drop_on_match = false; std::strcpy(e.target_name, "#Town"); },
    [](Entry& e) { e.drop_on_match = false; e.target_is_region = true;
                  std::strcpy(e.target_name, "Town"); },
    [](Entry& e) { e.drop_on_match = false; std::strcpy(e.target_name, "#Town");
                  e.scope_uses_slow_timing = true; },
    [](Entry& e) { e.extra.min_payload = 12; e.extra.max_payload = 20; e.extra.payload_set = 1; },
    [](Entry& e) { e.extra.route = FloodRuleExtensions::FloodRoute; },
    [](Entry& e) { e.extra.probability = 20; },
    [](Entry& e) { e.extra.path_position = FloodRuleExtensions::Anywhere; },
    [](Entry& e) { e.extra.disabled = 1; },
    [](Entry& e) { std::strcpy(e.extra.sender, "Alice"); },
    [](Entry& e) { std::strcpy(e.extra.text, "sample"); },
  };
  auto preserve = [](const Entry& edited) {
    MemoryFS fs; MyMesh writer(fs, 12); populate(writer);
    writer.storage[1] = edited;
    assert(writer.saveFloodPacketFilters());
    MyMesh boot(fs, 12); assert(boot.loadFloodPacketFilters());
    const auto disk = snapshot(fs);
    const auto writes = fs.write_opens;
    const auto loaded = boot.storage[1];
    // A deliberate deletion is normalized to an empty slot by the loader.
    assert(loaded.active == edited.active);
    assert(boot.migrateDefaultWardrivingFilter());
    assert(FloodFilterPolicy::sameRuleConfiguration(boot.storage[1], loaded));
    assert(policies(fs) == disk);
    assert(fs.exists(FLOOD_WARDRIVING_DEFAULTS_FILE));
    assert(fs.write_opens == writes + 1);  // Marker only; custom policy stays byte-identical.
  };
  for (Edit edit : edits) {
    auto row = legacyDefault(); edit(row); preserve(row);
  }
  // Compare every stored key byte and path byte, including unused tails.
  for (size_t i = 0; i < sizeof(Entry::channel_secret); ++i) {
    auto row = legacyDefault(); row.channel_secret[i] ^= 0x55; preserve(row);
  }
  for (size_t i = 0; i < sizeof(Entry::path); ++i) {
    auto row = legacyDefault(); row.path[i] = 0x42; preserve(row);
  }
  for (size_t i = 0; i < sizeof(FloodRuleExtensions::Fields::path_tail); ++i) {
    auto row = legacyDefault(); row.extra.path_tail[i] = 0x42; preserve(row);
  }
  // Deleting the entire forwarding phase must not recreate the built-in rule.
  MemoryFS empty; MyMesh writer(empty, 12);
  assert(writer.saveFloodPacketFilters()); const auto disk = snapshot(empty);
  MyMesh boot(empty, 12); assert(boot.loadFloodPacketFilters());
  assert(boot.migrateDefaultWardrivingFilter()); assert(policies(empty) == disk);
  assert(std::none_of(boot.storage.begin(), boot.storage.end(), [](const Entry& e) { return e.active; }));
}
void failedWritesStayRecoverable() {
  for (const char* fault : {"open:w:/flood_filter.tmp", "open:r:/flood_filter.tmp",
      "rename:/flood_filter:/flood_filter.bak", "rename:/flood_filter.tmp:/flood_filter"}) {
    for (bool extended : {false, true}) {
      MemoryFS fs; MyMesh writer(fs, 12); populate(writer, 1, extended);
      assert(writer.saveFloodPacketFilters());
      MyMesh boot(fs, 12); assert(boot.loadFloodPacketFilters());
      fs.faults.insert(fault);
      assert(!boot.migrateDefaultWardrivingFilter());
      assert(!fs.exists(FLOOD_WARDRIVING_DEFAULTS_FILE));
      assert(boot.storage[1].min_hops == 5); checkOthers(boot, writer, 1);
      fs.faults.clear();
      MyMesh reboot(fs, 12); assert(reboot.loadFloodPacketFilters());
      assert(reboot.storage[1].min_hops == 5 || reboot.storage[1].min_hops == 3);
      checkOthers(reboot, writer, 1);
      assert(reboot.migrateDefaultWardrivingFilter());
      assert(reboot.storage[1].min_hops == 3);
      MyMesh final(fs, 12); assert(final.loadFloodPacketFilters());
      assert(final.storage[1].min_hops == 3); checkOthers(final, writer, 1);
    }
  }
  MemoryFS fs; MyMesh writer(fs, 12); populate(writer);
  assert(writer.saveFloodPacketFilters()); const auto old = fs.get(FLOOD_PACKET_FILTER_FILE);
  MyMesh boot(fs, 12); assert(boot.loadFloodPacketFilters()); fs.short_write = true;
  assert(!boot.migrateDefaultWardrivingFilter()); assert(boot.storage[1].min_hops == 5);
  assert(!fs.exists(FLOOD_WARDRIVING_DEFAULTS_FILE));
  assert(fs.get(FLOOD_PACKET_FILTER_FILE) == old); fs.short_write = false;
  assert(boot.migrateDefaultWardrivingFilter());
}
void failedMarkerRetriesWithoutRewritingPolicy() {
  for (bool custom : {false, true}) {
    MemoryFS fs; MyMesh writer(fs, 12); populate(writer);
    if (custom) writer.storage[1].min_hops = 4;
    assert(writer.saveFloodPacketFilters());
    MyMesh boot(fs, 12); assert(boot.loadFloodPacketFilters());
    fs.faults.insert("open:w:" FLOOD_WARDRIVING_DEFAULTS_FILE);
    assert(!boot.migrateDefaultWardrivingFilter());
    assert(!fs.exists(FLOOD_WARDRIVING_DEFAULTS_FILE));
    assert(boot.storage[1].min_hops == (custom ? 4 : 3));
    checkOthers(boot, writer, 1);
    const auto committed = policies(fs);
    fs.faults.clear();
    MyMesh reboot(fs, 12); assert(reboot.loadFloodPacketFilters());
    const auto writes = fs.write_opens;
    assert(reboot.migrateDefaultWardrivingFilter());
    assert(reboot.storage[1].min_hops == (custom ? 4 : 3));
    assert(fs.write_opens == writes + 1 && policies(fs) == committed);
    assert(fs.exists(FLOOD_WARDRIVING_DEFAULTS_FILE));
  }
}
void honorLaterOperatorChanges() {
  for (unsigned initial = 0; initial < 4; ++initial) {
    MemoryFS fs; MyMesh writer(fs, 12); populate(writer);
    if (initial == 1) writer.storage[1].min_hops = 4;
    if (initial == 2) writer.storage[1].extra.disabled = 1;
    if (initial == 3) writer.storage[1] = {};
    assert(writer.saveFloodPacketFilters());
    MyMesh upgraded(fs, 12); assert(upgraded.loadFloodPacketFilters());
    assert(upgraded.migrateDefaultWardrivingFilter());
    assert(fs.exists(FLOOD_WARDRIVING_DEFAULTS_FILE));
    // After the upgrade has been applied, an operator can deliberately select
    // the old hop limit, even after initially disabling or deleting the rule.
    upgraded.storage[1] = legacyDefault();
    assert(upgraded.saveFloodPacketFilters());
    const auto disk = snapshot(fs); const auto writes = fs.write_opens;
    MyMesh reboot(fs, 12); assert(reboot.loadFloodPacketFilters());
    assert(reboot.storage[1].min_hops == 5);
    assert(reboot.migrateDefaultWardrivingFilter());
    assert(reboot.storage[1].min_hops == 5);
    assert(snapshot(fs) == disk && fs.write_opens == writes);
  }
}
void legacyAndUnavailableStorage() {
  MemoryFS fs; MyMesh writer(fs, 12); populate(writer);
  assert(writer.saveFloodPacketFilters());
  auto bare = fs.get(FLOOD_PACKET_FILTER_FILE);
  // A bare FPF7 table has no FPS1 sections. Migration must wait until the
  // startup legacy import has committed scopes and blacklist in that image.
  const auto marker = std::search(bare.begin(), bare.end(), "FPS1", "FPS1" + 4);
  assert(marker != bare.end()); bare.erase(marker, bare.end());
  fs.put(FLOOD_PACKET_FILTER_FILE, bare);
  MyMesh boot(fs, 12); assert(boot.loadFloodPacketFilters());
  assert(!boot.flood_policy_has_embedded_sections);
  assert(boot.migrateDefaultWardrivingFilter());
  assert(!fs.exists(FLOOD_WARDRIVING_DEFAULTS_FILE));
  assert(boot.storage[1].min_hops == 5 && fs.get(FLOOD_PACKET_FILTER_FILE) == bare);
  // The importer's real save commits the embedded sections before migration.
  assert(boot.saveFloodPacketFilters()); assert(boot.flood_policy_has_embedded_sections);
  assert(boot.migrateDefaultWardrivingFilter()); assert(boot.storage[1].min_hops == 3);
  MyMesh unmounted(fs, 12); populate(unmounted); unmounted._fs = nullptr;
  unmounted.flood_policy_has_embedded_sections = true;
  const auto disk = snapshot(fs); assert(unmounted.migrateDefaultWardrivingFilter());
  assert(unmounted.storage[1].min_hops == 5 && snapshot(fs) == disk);
  // An unreadable committed policy must never be replaced with defaults.
  fs.faults.insert("open:r:/flood_filter"); MyMesh unreadable(fs, 12);
  assert(!unreadable.loadFloodPacketFilters());
  assert(unreadable.migrateDefaultWardrivingFilter()); assert(snapshot(fs) == disk);
  fs.faults.clear(); MyMesh smaller(fs, 4);
  assert(!smaller.loadFloodPacketFilters() && smaller.flood_policy_capacity_limited);
  assert(smaller.migrateDefaultWardrivingFilter()); assert(snapshot(fs) == disk);
}
int main() {
  MemoryFS fs; MyMesh fresh(fs, 12); fresh.seedDefaultFloodPacketFilters();
  assert(fresh.storage[1].active && fresh.storage[1].min_hops == DEFAULT_WARDRIVING_MAX_HOPS + 1);
  assert(fresh.storage[2].min_hops == 0);
  assert(fresh.storage[2].transport_modes == (FloodFilterPolicy::RULE_MODE_BRIDGE | FloodFilterPolicy::RULE_MODE_CROSS));
#if DEFAULT_WARDRIVING_MAX_HOPS == 2
  migrateSavedDefaults(); preserveCustomizedRules(); failedWritesStayRecoverable(); legacyAndUnavailableStorage();
  failedMarkerRetriesWithoutRewritingPolicy(); honorLaterOperatorChanges();
  // Every matching row migrates in one save even if rules were reordered or
  // the same built-in row was duplicated by a previous management client.
  MyMesh writer(fs, 12); populate(writer); writer.storage[5] = legacyDefault();
  writer.storage[8] = legacyDefault(); assert(writer.saveFloodPacketFilters());
  MyMesh boot(fs, 12); assert(boot.loadFloodPacketFilters());
  const auto writes = fs.write_opens; assert(boot.migrateDefaultWardrivingFilter());
  assert(fs.write_opens == writes + 2);
  for (uint8_t slot : {uint8_t(1), uint8_t(5), uint8_t(8)}) assert(boot.storage[slot].min_hops == 3);
#else
  MyMesh writer(fs, 12); populate(writer); assert(writer.saveFloodPacketFilters());
  const auto disk = snapshot(fs); const auto writes = fs.write_opens;
  MyMesh boot(fs, 12); assert(boot.loadFloodPacketFilters());
  assert(boot.migrateDefaultWardrivingFilter()); assert(boot.storage[1].min_hops == 5);
  assert(snapshot(fs) == disk && fs.write_opens == writes);
#endif
}
'''


class WardrivingDefaultMigrationTests(unittest.TestCase):
    def test_production_default_and_saved_rule_upgrade(self):
        source = (ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text()
        header = (ROOT / 'examples/simple_repeater/MyMesh.h').read_text()
        default = re.search(r'#ifndef DEFAULT_WARDRIVING_MAX_HOPS\s+\n?\s*#define DEFAULT_WARDRIVING_MAX_HOPS\s+(\d+)\s*\n#endif', source)
        self.assertIsNotNone(default)
        self.assertEqual(default.group(1), '2')
        self.assertRegex(source, r'if\s*\(flood_filters_loaded\s*&&\s*importLegacyFloodPolicySections\(\)\)\s*\{\s*migrateDefaultWardrivingFilter\(\);')
        fs = HARNESS[:HARNESS.index('@STORE@')]
        fs = fs.replace('bool short_write = false;', 'bool short_write = false;\n  unsigned write_opens = 0;')
        fs = fs.replace("if (mode[0] == 'w') files[path]", "if (mode[0] == 'w') ++write_opens;\n    if (mode[0] == 'w') files[path]")
        fs = fs.replace('bool valid = false;', 'bool valid = false;\n  size_t cursor=0;')
        fs = fs.replace('size_t write(const uint8_t*, size_t);', '''size_t write(const uint8_t*, size_t);
  size_t read(uint8_t* out,size_t count) {
    if (!valid) return 0;
    count=std::min(count,bytes->size()-cursor);
    std::copy_n(bytes->data()+cursor,count,out);cursor+=count;return count;
  }
  size_t available() const { return valid ? bytes->size()-cursor : 0; }
''')
        # Cryptography has independent tests; use a deterministic derived key
        # here so complete stored channel identities are still compared.
        prelude = PRELUDE.replace('const uint8_t*, size_t key_size', 'const uint8_t* input, size_t key_size')
        prelude = prelude.replace('assert(size == 1 && (key_size == 16 || key_size == 32));',
                                  'assert((size == 1 || size == 16) && key_size);')
        prelude = prelude.replace('std::memset(out,0,size);', '''
    if (size == 1) std::memset(out, 0, size);
    else for (size_t i = 0; i < size; ++i) static_cast<uint8_t*>(out)[i] = input[i % key_size] ^ uint8_t(i);
''')
        owner = CLASS.replace('@ENTRY@', extract_braced(header, 'struct FloodPacketFilterEntry'))
        owner = owner.replace('@SCOPE@', extract_braced(header, 'struct FloodChannelScopeEntry'))
        owner = owner.replace('void seedDefaultFloodPacketFilters() { ++seeded; }',
                              'void seedDefaultFloodPacketFilters();\n  bool migrateDefaultWardrivingFilter();')
        parts = ['#include <algorithm>', fs, prelude,
                 '#define DEFAULT_WARDRIVING_CHANNEL "#wardriving"\n#define FLOOD_WARDRIVING_DEFAULTS_FILE "/flood_wd2"', default.group(), owner]
        parts.append(extract_braced((ROOT / 'src/helpers/TxtDataHelpers.cpp').read_text(), 'void StrHelper::strncpy('))
        # Keep the production shared constructor template and both call sites.
        builder = source.index('seedWardrivingFloodPacketFilter(')
        template = source.rfind('template', 0, builder)
        parts.append(extract_braced(source[template:], 'template'))
        for marker in ('static File openFloodSettingsRead(', 'static File openFloodSettingsWrite(',
                       'static uint32_t updateFloodSettingsHash(', 'static bool verifyFloodSettingsWrite(',
                       'static bool isValidStoredFloodFilterScopeName(', 'static bool isValidStoredFloodRuleRegionName(',
                       'void MyMesh::seedDefaultFloodPacketFilters(', 'bool MyMesh::isFloodChannelDataRule(',
                       'bool MyMesh::loadFloodPacketFilters(', 'bool MyMesh::saveFloodPacketFilters(',
                       'bool MyMesh::migrateDefaultWardrivingFilter('):
            parts.append(extract_braced(source, marker))
        parts.append(SCENARIOS)
        with tempfile.TemporaryDirectory(prefix='wardriving-default-migration-') as temp:
            path = Path(temp)
            cpp = path / 'test.cpp'
            cpp.write_text('\n'.join(parts))
            for platform, override in (('NRF52_PLATFORM', None), ('ESP32', None), ('NRF52_PLATFORM', 4)):
                with self.subTest(platform=platform, override=override):
                    binary = path / (platform + ('-override' if override is not None else ''))
                    subprocess.run(['g++', '-std=c++17', '-O1', '-g', '-fsanitize=address,undefined',
                                    '-fno-omit-frame-pointer', '-no-pie', '-D' + platform,
                                    *(['-DDEFAULT_WARDRIVING_MAX_HOPS=' + str(override)] if override is not None else []),
                                    '-I' + str(ROOT / 'src'), str(cpp), '-o', str(binary)], check=True)
                    subprocess.run([str(binary)], check=True,
                                   env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1'})


if __name__ == '__main__':
    unittest.main()
