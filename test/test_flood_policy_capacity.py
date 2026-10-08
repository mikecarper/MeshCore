"""Run production flood-policy load/save code across firmware capacity changes."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from test_common_prefs_commit import HARNESS
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
#include <algorithm>
#include <cstring>
#include <cctype>
#include <helpers/FloodFilterPolicy.h>
#define MESH_ENABLE_FLOOD_RULE_ENGINE 1
#define FLOOD_PACKET_FILTER_SCOPE_NAME_LEN 32
#define FLOOD_GROUP_MODERATION_NAME_LEN 32
#define FLOOD_PACKET_FILTER_PATH_PREFIX_BYTES_MAX 9
#define FLOOD_PACKET_FILTER_ANY_TYPE 0xFF
#define FLOOD_PACKET_FILTER_MAX_HOPS 63
#define FLOOD_CHANNEL_HOPS_ALL 0xFF
#define FLOOD_GROUP_MODERATION_RATE_UNLIMITED 0xFFFF
#define FLOOD_CHANNEL_SCOPE_OTHER_ANY 2
#define FLOOD_CHANNEL_SCOPE_SLOTS 15
#define FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS 15
#define FLOOD_PACKET_FILTER_BLACKLIST_MAX 18
#define FLOOD_PACKET_FILTER_PATH_ID_SIZE 3
#define FLOOD_PACKET_FILTER_FILE "/flood_filter"
#define FLOOD_PACKET_FILTER_TEMP_FILE "/flood_filter.tmp"
#define FLOOD_PACKET_FILTER_BACKUP_FILE "/flood_filter.bak"
#define FLOOD_POLICY_SECTION_MAGIC "FPS1"
#define FILE_O_WRITE "w"
namespace mesh {
File openFileRead(FILESYSTEM* fs, const char* path) { return fs->open(path); }
struct Utils {
  static void sha256(void*, size_t, const uint8_t*, size_t) {
    assert(false && "crypto is outside these capacity fixtures");
  }
};
}
struct RegionMap {
  static bool is_name_char(uint8_t c) { return std::isalnum(c) || c=='_' || c=='-'; }
};
struct StrHelper {
  static void strzcpy(char* out, const char* in, size_t size) {
    if (out!=in) std::memmove(out,in,std::min(size,std::strlen(in)+1));
    if (size) out[size-1]=0;
  }
};
'''
CLASS = r'''
class MyMesh {
public:
@ENTRY@;
@SCOPE@;
  FILESYSTEM* _fs;
  std::vector<FloodPacketFilterEntry> storage;
  FloodPacketFilterEntry* flood_packet_filters;
  uint8_t flood_packet_filter_slots;
  bool flood_policy_has_embedded_sections = false;
  bool flood_policy_capacity_limited = false;
  uint8_t flood_channel_data_rule_slot = 0xff;
  uint8_t flood_channel_data_rule_max_hops = FLOOD_CHANNEL_HOPS_ALL;
  FloodChannelScopeEntry flood_channel_scopes[FLOOD_CHANNEL_SCOPE_SLOTS]{};
  char flood_channel_direct_scopes[FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS][32]{};
  uint8_t flood_packet_filter_blacklist_count = 0;
  uint8_t flood_packet_filter_blacklist[18][3]{};
  unsigned seeded = 0;
  MyMesh(MemoryFS& fs, uint8_t slots): _fs(&fs),storage(slots),
      flood_packet_filters(storage.data()),flood_packet_filter_slots(slots) {}
  void seedDefaultFloodPacketFilters() { ++seeded; }
  bool loadFloodPacketFilters();
  bool saveFloodPacketFilters(bool empty_scope_phase=false,bool empty_forward_phase=false);
  bool isFloodChannelDataRule(const FloodPacketFilterEntry&) const;
};
'''
ROOM_CLASS = r'''
class FloodRuleEngine {
public:
  static constexpr uint8_t RULE_SLOTS = 31, NAME_LEN = 32, PATH_PREFIX_BYTES_MAX = 9;
@ENTRY@;
  FILESYSTEM* _fs;
  Entry storage[RULE_SLOTS]{};
  Entry* _entries = storage;
  explicit FloodRuleEngine(MemoryFS& fs): _fs(&fs) {}
  bool save();
};
static const char RULE_FILE[] = "/flood_filter";
static const char RULE_TEMP_FILE[] = "/flood_filter.tmp";
static const char RULE_BACKUP_FILE[] = "/flood_filter.bak";
'''
SCENARIOS = r'''
template<class Entry> void canonicalRule(Entry& row) {
  row.active=true;row.payload_type=PAYLOAD_TYPE_ADVERT;
  row.min_hops=2;row.max_hops=63;row.suspend_on_temp_radio=true;
  std::strcpy(row.scope_name,"#Town");row.match_blacklisted_path=true;
  row.scope_uses_slow_timing=true;row.incoming_scope_kind=FloodFilterPolicy::RULE_IN_ALLOWED;
  row.path_hash_size=3;row.rate_limit_enabled=true;row.rate_per_minute=1234;
  row.priority=7;row.stop_on_match=true;row.retry_on_match=true;
  row.rate_window_started=0xfedcba98;row.rate_window_count=17;row.rate_window_active=true;
}
std::vector<uint8_t> fixture(uint8_t slots, uint8_t highest) {
  MemoryFS fs;MyMesh writer(fs,slots);
  for (uint8_t index : {uint8_t(0),highest}) {
    auto& row=writer.storage[index];row.active=true;row.payload_type=PAYLOAD_TYPE_ADVERT;
    row.min_hops=2;row.max_hops=63;row.drop_on_match=true;
    row.incoming_scope_kind=FloodFilterPolicy::RULE_IN_ANY;
    row.transport_modes=FloodFilterPolicy::RULE_MODE_RADIO;
  }
  assert(writer.saveFloodPacketFilters());
  const auto bytes=fs.get(FLOOD_PACKET_FILTER_FILE);
  MyMesh same(fs,slots);assert(same.loadFloodPacketFilters());
  assert(same.storage[highest].active && same.storage[highest].drop_on_match);
  return bytes;
}
std::map<std::string,std::vector<uint8_t>> snapshot(const MemoryFS& fs) {
  std::map<std::string,std::vector<uint8_t>> result;
  for (const auto& row:fs.files) result[row.first]=*row.second;
  return result;
}
int main() {
  // Canonical FPF7 bytes remain independent of the compact in-memory layout.
  // Exercise every persisted packed flag plus the addressable retry flag.
  {
    MemoryFS fs;MyMesh writer(fs,63);auto& row=writer.storage[0];
    canonicalRule(row);
    std::vector<uint8_t> expected={'F','P','F','7',1,1,PAYLOAD_TYPE_ADVERT,2,63,1};
    const auto zeroes=[&expected](size_t count) {expected.insert(expected.end(),count,0);};
    expected.insert(expected.end(),{'#','T','o','w','n'});zeroes(27);
    expected.insert(expected.end(),{1,1,1,3});zeroes(32);
    expected.push_back(0x80);zeroes(32+32);
    expected.insert(expected.end(),{3,0});zeroes(9);
    expected.insert(expected.end(),{0,1,0xd2,0x04});zeroes(32);
    expected.insert(expected.end(),{7,1,'F','P','S','1',0xff,0xff,0,0,0});
    assert(writer.saveFloodPacketFilters());assert(fs.get(FLOOD_PACKET_FILTER_FILE)==expected);
    MyMesh reader(fs,63);assert(reader.loadFloodPacketFilters());const auto& loaded=reader.storage[0];
    assert(loaded.active && loaded.suspend_on_temp_radio && loaded.match_blacklisted_path);
    assert(loaded.scope_uses_slow_timing && !loaded.drop_on_match && loaded.rate_limit_enabled);
    assert(loaded.stop_on_match && loaded.retry_on_match && loaded.rate_per_minute==1234);
    assert(!loaded.rate_window_active && loaded.rate_window_started==0 && loaded.rate_window_count==0);
    // Execute the real room serializer too. It retains its 31 padded slots,
    // and the same canonical row must load through the repeater path.
    MemoryFS room_fs;FloodRuleEngine room(room_fs);canonicalRule(room.storage[0]);
    auto room_expected=expected;room_expected[4]=31;
    const size_t row_stride=expected.size()-5-9;
    room_expected.insert(room_expected.end()-9,30*row_stride,0);
    assert(room.save());assert(room_fs.get(FLOOD_PACKET_FILTER_FILE)==room_expected);
    MyMesh from_room(room_fs,63);assert(from_room.loadFloodPacketFilters());
    assert(from_room.storage[0].scope_uses_slow_timing && from_room.storage[0].stop_on_match);
    assert(from_room.storage[0].retry_on_match && from_room.storage[0].rate_per_minute==1234);
    // A drop rule exercises the remaining persisted packed flag without an
    // incompatible scope/rate/retry action, while preserving the same format.
    row={};row.active=true;row.payload_type=PAYLOAD_TYPE_ADVERT;row.max_hops=63;row.drop_on_match=true;
    assert(writer.saveFloodPacketFilters());MyMesh drop(fs,63);assert(drop.loadFloodPacketFilters());
    assert(drop.storage[0].active && drop.storage[0].drop_on_match && !drop.storage[0].retry_on_match);
  }
  const auto large=fixture(63,62),small=fixture(4,2);
  // Older writers stored every slot, including inactive trailing entries.
  // Build the exact production wire format and replace the final active row
  // with its serialized empty neighbor; the highest retained rule is slot 1.
  MemoryFS empty_fs;MyMesh empty(empty_fs,63);assert(empty.saveFloodPacketFilters());
  const auto overhead=empty_fs.get(FLOOD_PACKET_FILTER_FILE).size();
  assert((large.size()-overhead)%63==0);
  const size_t stride=(large.size()-overhead)/63;
  auto padded=large;
  std::copy_n(large.begin()+5+stride,stride,padded.begin()+5+62*stride);
  for (uint8_t capacity : {uint8_t(4),uint8_t(47)}) {
    MemoryFS fs;fs.put(FLOOD_PACKET_FILTER_FILE,padded);MyMesh reader(fs,capacity);
    assert(reader.loadFloodPacketFilters());assert(reader.storage[0].active);
    assert(!reader.flood_policy_capacity_limited && reader.seeded==0);
    assert(fs.get(FLOOD_PACKET_FILTER_FILE)==padded);
    assert(reader.saveFloodPacketFilters());
    assert(fs.get(FLOOD_PACKET_FILTER_FILE)[4]==1);
  }
  // FPF6 used a fixed 40-byte legacy row and also stored inactive tails.
  for (bool active_overflow : {false,true}) {
    std::vector<uint8_t> legacy={'F','P','F','6',63};
    for (unsigned index=0;index<63;++index) {
      std::vector<uint8_t> row(40,0);
      if(index==0 || (active_overflow && index==62)) {
        row[0]=1;row[1]=PAYLOAD_TYPE_ADVERT;row[2]=2;row[3]=63;
      }
      legacy.insert(legacy.end(),row.begin(),row.end());
    }
    MemoryFS fs;fs.put(FLOOD_PACKET_FILTER_FILE,legacy);MyMesh reader(fs,4);
    assert(reader.loadFloodPacketFilters()==!active_overflow);
    assert(reader.flood_policy_capacity_limited==active_overflow);
    assert(reader.storage[0].active==!active_overflow);
    assert(fs.get(FLOOD_PACKET_FILTER_FILE)==legacy);
  }
  // An extension may not reference an inactive row beyond the new table.
  // This malformed padded image must not perform an out-of-bounds read.
  {
    auto malformed=padded;
    assert(std::memcmp(malformed.data()+5+63*stride,"FPS1",4)==0);
    malformed[5+63*stride+4]=62;
    MemoryFS fs;fs.put(FLOOD_PACKET_FILTER_FILE,malformed);MyMesh reader(fs,4);
    assert(reader.loadFloodPacketFilters());assert(reader.seeded==1);
    assert(!fs.exists(FLOOD_PACKET_FILTER_FILE));
  }
  // Future counts outside the 64-bit rule mask remain untouched as unsupported.
  {
    MemoryFS fs;const std::vector<uint8_t> future={'F','P','F','7',65};
    fs.put(FLOOD_PACKET_FILTER_FILE,future);MyMesh reader(fs,4);
    assert(!reader.loadFloodPacketFilters() && reader.flood_policy_capacity_limited);
    assert(!reader.saveFloodPacketFilters());assert(fs.get(FLOOD_PACKET_FILTER_FILE)==future);
  }
  // Active overflow must never be mistaken for corrupt storage. Preserve all
  // transaction images and refuse saves, including the empty-table path.
  for (uint8_t capacity : {uint8_t(4),uint8_t(47)}) {
    for (unsigned source=0;source<4;++source) {
      MemoryFS fs;
      if (source==0) {
        fs.put(FLOOD_PACKET_FILTER_FILE,large);
        fs.put(FLOOD_PACKET_FILTER_TEMP_FILE,small);
        fs.put(FLOOD_PACKET_FILTER_BACKUP_FILE,small);
      } else if(source==1) {
        fs.put(FLOOD_PACKET_FILTER_TEMP_FILE,large);
        fs.put(FLOOD_PACKET_FILTER_BACKUP_FILE,small);
      } else if(source==2) {
        fs.put(FLOOD_PACKET_FILTER_BACKUP_FILE,large);
      } else {
        fs.put(FLOOD_PACKET_FILTER_FILE,{0,1,2});
        fs.put(FLOOD_PACKET_FILTER_TEMP_FILE,large);
        fs.put(FLOOD_PACKET_FILTER_BACKUP_FILE,small);
      }
      const auto before=snapshot(fs);MyMesh reader(fs,capacity);
      assert(!reader.loadFloodPacketFilters());
      assert(reader.flood_policy_capacity_limited && reader.seeded==0);
      assert(std::none_of(reader.storage.begin(),reader.storage.end(),[](const auto& e){return e.active;}));
      assert(!reader.saveFloodPacketFilters());
      assert(!reader.saveFloodPacketFilters(true,true));
      assert(snapshot(fs)==before);
      MyMesh compatible(fs,63);assert(compatible.loadFloodPacketFilters());
      assert(compatible.storage[62].active);
    }
  }
  // Normal committed and recoverable tables still use the production path.
  for (const char* path : {FLOOD_PACKET_FILTER_FILE,FLOOD_PACKET_FILTER_TEMP_FILE,FLOOD_PACKET_FILTER_BACKUP_FILE}) {
    MemoryFS fs;fs.put(path,small);MyMesh reader(fs,4);
    assert(reader.loadFloodPacketFilters());assert(reader.storage[2].active);
    assert(!reader.flood_policy_capacity_limited);
    assert(fs.get(FLOOD_PACKET_FILTER_FILE)==small);
  }
  // Malformed recognized images remain corrupt, unlike a capacity mismatch.
  MemoryFS bad;bad.put(FLOOD_PACKET_FILTER_FILE,{0,1,2});MyMesh reader(bad,4);
  assert(reader.loadFloodPacketFilters());assert(reader.seeded==1);
  assert(!reader.flood_policy_capacity_limited);
  assert(!bad.exists(FLOOD_PACKET_FILTER_FILE));
}
'''


class FloodPolicyCapacityTests(unittest.TestCase):
    def test_saved_rules_survive_smaller_firmware_capacity(self):
        source=(ROOT/'examples/simple_repeater/MyMesh.cpp').read_text()
        header=(ROOT/'examples/simple_repeater/MyMesh.h').read_text()
        room_source=(ROOT/'examples/simple_room_server/FloodRuleEngine.cpp').read_text()
        room_header=(ROOT/'examples/simple_room_server/FloodRuleEngine.h').read_text()
        fs=HARNESS[:HARNESS.index('@STORE@')]
        fs=fs.replace('bool valid = false;', 'bool valid = false;\n  size_t cursor=0;')
        fs=fs.replace('size_t write(const uint8_t*, size_t);', '''size_t write(const uint8_t*, size_t);
  size_t read(uint8_t* out,size_t count) {
    if (!valid) return 0;
    count=std::min(count,bytes->size()-cursor);
    std::copy_n(bytes->data()+cursor,count,out);cursor+=count;return count;
  }
  size_t available() const { return valid ? bytes->size()-cursor : 0; }
''')
        fs='#include <algorithm>\n'+fs
        parts=[fs,PRELUDE,CLASS.replace('@ENTRY@',extract_braced(header,'struct FloodPacketFilterEntry')).replace('@SCOPE@',extract_braced(header,'struct FloodChannelScopeEntry'))]
        for marker in ('static File openFloodSettingsRead(', 'static File openFloodSettingsWrite(',
                       'static uint32_t updateFloodSettingsHash(', 'static bool verifyFloodSettingsWrite(',
                       'static bool isValidStoredFloodFilterScopeName(', 'static bool isValidStoredFloodRuleRegionName(',
                       'bool MyMesh::isFloodChannelDataRule(', 'bool MyMesh::loadFloodPacketFilters(',
                       'bool MyMesh::saveFloodPacketFilters('):
            parts.append(extract_braced(source,marker))
        parts.append(ROOM_CLASS.replace('@ENTRY@',extract_braced(room_header,'struct Entry')))
        for marker in ('static File openRead(', 'static File openWrite(',
                       'static uint32_t updateFileHash(', 'static bool verifyWrittenFile(',
                       'bool FloodRuleEngine::save('):
            parts.append(extract_braced(room_source,marker))
        parts.append(SCENARIOS)
        with tempfile.TemporaryDirectory(prefix='flood-policy-capacity-') as temp:
            p=Path(temp);cpp=p/'test.cpp';cpp.write_text('\n'.join(parts))
            for platform in ('NRF52_PLATFORM','ESP32'):
                with self.subTest(platform=platform):
                    exe=p/platform
                    subprocess.run(['g++','-std=c++17','-O1','-g','-fsanitize=address,undefined',
                                    '-fno-omit-frame-pointer','-no-pie','-D'+platform,
                                    '-I'+str(ROOT/'src'),str(cpp),'-o',str(exe)],check=True)
                    subprocess.run([str(exe)],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1'})


if __name__=='__main__':unittest.main()
