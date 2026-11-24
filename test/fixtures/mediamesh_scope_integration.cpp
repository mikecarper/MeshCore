// Run the unchanged CommonCLI policy callback with real RegionMap, transport
// keys, packet transport codes, and platform atomic region storage.
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <helpers/RegionMap.h>
#include <helpers/RegionNameUtils.h>
#include <helpers/TxtDataHelpers.h>
#if defined(NRF52_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#else
#include <contact_transaction_under_test.h>
#include <helpers/FilePresence.h>
#endif

class RadioProfilesAdapter {
public:
  using Handler = bool (*)(void*, bool, bool);
  Handler handler = nullptr;
  void* context = nullptr;
  unsigned bindings = 0;
  void setMediaMeshScopeHandler(Handler value, void* owner) {
    ++bindings;
    handler = value;
    context = owner;
  }
  bool update(bool enable, bool commit) { return handler(context, enable, commit); }
};

class MyMesh {
public:
  RegionMap& region_map;
  TransportKey default_scope{};
  explicit MyMesh(RegionMap& regions) : region_map(regions) {}
  void onDefaultRegionChanged(const RegionEntry* region);
};

class RegionCallbacks {
  RegionMap& regions_;
  MemoryFS& fs_;
public:
  MyMesh role;
  unsigned save_calls = 0;
  unsigned default_changes = 0;
  uint16_t last_default = 0;
  RegionCallbacks(RegionMap& regions, MemoryFS& fs) : regions_(regions), fs_(fs), role(regions) {}
  bool saveRegions() { ++save_calls; return regions_.save(&fs_); }
  void onDefaultRegionChanged(const RegionEntry* region) {
    ++default_changes;
    last_default = region ? region->id : 0;
    role.onDefaultRegionChanged(region);
  }
};

class CommonCLI {
  RegionMap* _region_map;
  RegionCallbacks* _callbacks;
  FILESYSTEM* _management_fs = nullptr;
  uint32_t _media_regions_retry_ms = 0;
  bool _media_regions_ready = false;
  static bool mediaMeshRegionStoreMissing(FILESYSTEM* fs);
  static bool updateMediaMeshScope(void* context, bool enable, bool commit);
public:
  RadioProfilesAdapter _radio_profiles;
  CommonCLI(RegionMap* regions, RegionCallbacks* callbacks)
      : _region_map(regions), _callbacks(callbacks) {}
  void onRegionsLoaded(FILESYSTEM* fs, bool loaded);
  void recoverMediaMeshRegions();
  #include "unmatched.inc"
};

#include "scope_under_test.inc"

static void require(bool condition, const char* message) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s\n", message);
    std::exit(1);
  }
}

struct Scenario {
  TransportKeyStore keys;
  MemoryFS fs;
  RegionMap regions{keys};
  RegionCallbacks callbacks{regions, fs};
  CommonCLI cli{&regions, &callbacks};

  Scenario() {
    g_mock_millis = 0;
    metadata_filesystem = &fs;
    cli.onRegionsLoaded(&fs, false);
    require(cli._radio_profiles.handler != nullptr
                && cli._radio_profiles.context == &cli,
            "actual onRegionsLoaded binds the callback to this CLI");
  }
  bool update(bool enable, bool commit = true) {
    return cli._radio_profiles.update(enable, commit);
  }
  bool durable_allowed() {
    TransportKeyStore restarted_keys;
    RegionMap restarted(restarted_keys);
    require(restarted.load(&fs), "reboot loads committed region table");
    const auto* region = restarted.findByName("mediamesh");
    require(region != nullptr, "reboot retains the MediaMesh scope entry");
    return (region->flags & REGION_DENY_FLOOD) == 0;
  }
};

static mesh::Packet media_packet() {
  mesh::Packet packet;
  packet.header = ROUTE_TYPE_TRANSPORT_FLOOD | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT);
  const uint8_t payload[] = {0x43, 0x56, 0x03, 0x00, 0xc0, 0xdb, 0xff};
  std::memcpy(packet.payload, payload, sizeof(payload));
  packet.payload_len = sizeof(payload);
  // Independent Python hashlib/hmac vector for SHA256('#mediamesh')[:16]
  // and HMAC-SHA256(key, payload_type || payload)[:2].
  const uint8_t bytes[] = {0x74, 0xb3};
  std::memcpy(&packet.transport_codes[0], bytes, sizeof(bytes));
  packet.transport_codes[1] = 0;
  return packet;
}

static void unknown_forward() {
  Scenario test;
  auto& map = test.regions;
  auto packet = media_packet(); // #mediamesh is initially unknown
  packet.transport_codes[1] = 0x7351;
  const auto original = packet.transport_codes[0];
  auto forward = [&]() { return test.cli.allowsUnmatchedFlood(&packet); };
  require(forward() && map.findMatch(&packet, REGION_DENY_FLOOD) == nullptr, "empty auto passes without claiming a match");
  require(packet.transport_codes[0] == original && packet.transport_codes[1] == 0x7351, "keep both scope codes");
  auto* local = map.putRegion("local", 0);
  require(local && !forward(), "first named region closes auto");
  map.setUnknownScope(RegionMap::UnknownScope::Allow);
  require(forward(), "explicit allow alongside configured regions");
  auto* media = map.putRegion("mediamesh", 0);
  require(media, "add matching named region");
  media->flags = REGION_DENY_FLOOD;
  require(!forward(), "allow still respects a named flood denial");
  require(map.save(&test.fs), "save allow");
  RegionMap restarted(test.keys);
  require(restarted.load(&test.fs) && restarted.getUnknownScope() == RegionMap::UnknownScope::Allow, "reboot retains allow");
  require(map.removeRegion(*media) && map.removeRegion(*local), "remove named regions");
  map.setUnknownScope(RegionMap::UnknownScope::Deny);
  require(!forward() && map.save(&test.fs) && restarted.load(&test.fs)
              && restarted.getUnknownScope() == RegionMap::UnknownScope::Deny, "empty deny survives reboot");
  restarted.resetFrom(map);
  require(restarted.getUnknownScope() == RegionMap::UnknownScope::Deny, "bulk region load preserves mode");
  map.setUnknownScope(RegionMap::UnknownScope::Auto);
  require(forward(), "empty auto reopens");
  for (const uint16_t code : {0, 0xffff}) {
    packet.transport_codes[0] = code;
    require(!forward(), "reserved codes are outside unknown-scope policy");
  }
  packet.transport_codes[0] = original;
  test.fs.files["/regions2"] = {0xff};
  test.cli.onRegionsLoaded(&test.fs, false);
  require(!forward(), "unreadable table does not open forwarding");

  struct Gate {
    using Packet = mesh::Packet;
    bool _cold_boot_repeat_hold = false;
    bool allowPacketForward(const mesh::Packet*) { return true; }
    #include "forward.inc"
  } gate;
  TransportKey key;
  test.keys.getAutoKeyFor(130, "#BlackHole86", key);
  for (uint8_t value : {0x10, 0x20}) {
    packet.payload[0] = value;
    packet.transport_codes[0] = key.calcTransportCode(&packet);
    packet.received_in_sink_scope = packet.hasBlackHoleScope();
    require(packet.received_in_sink_scope, "recognize real sink code for each payload");
    packet.transport_codes[0] ^= 1; // a subsequent rewrite cannot undo the receive-time veto
    require(!gate.canForwardPacket(&packet), "received sink stays blocked after rewrite");
    packet.received_in_sink_scope = packet.hasBlackHoleScope();
    require(!packet.received_in_sink_scope, "other scope is eligible");
    packet.transport_codes[0] = key.calcTransportCode(&packet);
    require(gate.canForwardPacket(&packet), "locally assigned sink can leave once");
  }
  packet.header = (packet.header & ~PH_ROUTE_MASK) | ROUTE_TYPE_TRANSPORT_DIRECT;
  require(!packet.hasBlackHoleScope() && !forward(), "direct traffic outside scoped flood policy");
}

static void scope_filter() {
  Scenario test;
  auto* unrelated = test.regions.putRegion("#local", 0);
  require(unrelated != nullptr, "create independent scope");
  unrelated->flags = REGION_DENY_FLOOD | REGION_DENY_DIRECT;
  test.regions.setDefaultRegion(unrelated);
  test.regions.setHomeRegion(unrelated);
  test.regions.getWildcard().flags = REGION_DENY_FLOOD | REGION_DENY_DIRECT;
  require(test.regions.save(&test.fs), "write independent scope baseline");
  const auto baseline = test.fs.files;
  require(test.update(true, false), "preflight has capacity");
  require(test.regions.getCount() == 1 && test.fs.files == baseline
              && test.callbacks.save_calls == 0, "preflight does not mutate or save");
  mesh::Packet packet = media_packet();
  require(test.regions.findMatch(&packet, REGION_DENY_FLOOD) == nullptr,
          "unknown MediaMesh scope is blocked before selecting the profile");
  require(test.update(true), "preset enables and saves MediaMesh scope");
  auto* media = test.regions.findByName("mediamesh");
  require(media && !std::strcmp(media->name, "mediamesh") && media->parent == 0,
          "new public MediaMesh scope has the exact name and root parent");
  TransportKey key;
  require(test.regions.getTransportKeysFor(*media, &key, 1) == 1,
          "derive actual automatic transport key");
  const uint8_t expected_key[] = {0xb9, 0xec, 0x7b, 0x4d, 0x14, 0xca, 0x1f, 0x9b, 0x4b, 0x4b, 0xc4, 0xa5, 0x26, 0x6e, 0x15, 0x12};
  require(!std::memcmp(key.key, expected_key, sizeof(key.key)),
          "public MediaMesh key matches independent SHA256 vector");
  require(key.calcTransportCode(&packet) == packet.transport_codes[0],
          "actual packet code matches independent HMAC vector");
  require(test.regions.findMatch(&packet, REGION_DENY_FLOOD) == media,
          "MediaMesh packets pass through the actual region flood filter");
  require(test.durable_allowed(), "enabled scope survives reboot");
  require(test.regions.getDefaultRegion()->id == unrelated->id
              && test.regions.getHomeRegion()->id == unrelated->id
              && test.regions.getWildcard().flags == (REGION_DENY_FLOOD | REGION_DENY_DIRECT)
              && unrelated->flags == (REGION_DENY_FLOOD | REGION_DENY_DIRECT),
          "scope enrollment does not change the primary/default/home/wildcard policy");
  require(test.update(false), "radio2 off denies and saves the scope");
  require(test.regions.findMatch(&packet, REGION_DENY_FLOOD) == nullptr,
          "radio2 off stops forwarding MediaMesh-scoped packets");
  require(!test.durable_allowed(), "disabled scope survives reboot");
}

static void existing_policy() {
  Scenario test;
  auto* parent = test.regions.putRegion("#parent", 0);
  auto* media = test.regions.putRegion("#mediamesh", parent->id);
  auto* child = test.regions.putRegion("child", media->id);
  require(parent && media && child, "create pre-existing scope hierarchy");
  const auto parent_id = parent->id, media_id = media->id, child_id = child->id;
  media->flags = REGION_DENY_FLOOD | REGION_DENY_DIRECT | 0x80;
  test.regions.setDefaultRegion(media);
  test.regions.setHomeRegion(child);
  test.regions.getWildcard().flags = REGION_DENY_FLOOD;
  require(test.update(true), "enable existing hashed scope");
  require(test.regions.getCount() == 3 && media->id == media_id
              && media->parent == parent_id && media->flags == (REGION_DENY_DIRECT | 0x80)
              && !std::strcmp(media->name, "#mediamesh")
              && child->id == child_id && child->parent == media_id,
          "existing entry is neither duplicated, renamed, reparented nor stripped");
  require(test.update(false), "disable existing scope");
  require(media->flags == (REGION_DENY_FLOOD | REGION_DENY_DIRECT | 0x80)
              && media->parent == parent_id && test.regions.getCount() == 3
              && test.regions.getDefaultRegion()->id == media_id
              && test.regions.getHomeRegion()->id == child_id
              && test.regions.getWildcard().flags == REGION_DENY_FLOOD,
          "disable preserves direct policy, hierarchy, home/default and wildcard");
}

static void full_table() {
  Scenario test;
  for (int i = 0; i < MAX_REGION_ENTRIES; ++i) {
    char name[31];
    std::snprintf(name, sizeof(name), "region%d", i);
    require(test.regions.putRegion(name, 0) != nullptr, "fill region table");
  }
  require(!test.update(true, false), "full table rejects a new scope at preflight");
  require(!test.update(true), "full table cannot silently enroll a missing scope");
  require(test.update(false, false) && test.update(false),
          "missing scope disable succeeds even on a full table");
  require(test.regions.getCount() == MAX_REGION_ENTRIES && test.fs.files.empty()
              && test.callbacks.save_calls == 0, "failed enrollment has no side effects");
  // Use the real existing entry, including a leading hash, at full capacity.
  auto* entry = test.regions.findByName("region0");
  std::strcpy(entry->name, "#mediamesh");
  require(test.update(true, false) && test.update(true),
          "full table can enable its existing MediaMesh entry");
  require(test.regions.getCount() == MAX_REGION_ENTRIES && test.durable_allowed(),
          "existing full-table scope is saved without an extra allocation");
}

static void save_retry() {
  Scenario test;
  auto* entry = test.regions.putRegion("mediamesh", 0);
  require(entry && test.regions.save(&test.fs), "save denied scope baseline");
  require(!test.durable_allowed(), "baseline is denied after reboot");
  test.fs.fail_write_open = true;
  require(!test.update(true), "enable reports an actual region save failure");
  require(!(entry->flags & REGION_DENY_FLOOD) && !test.durable_allowed(),
          "failed save keeps old durable state and dirty new RAM flags");
  const unsigned failed_calls = test.callbacks.save_calls;
  test.fs.fail_write_open = false;
  require(test.update(true) && test.callbacks.save_calls == failed_calls + 1
              && test.durable_allowed(), "retry saves even though RAM is already enabled");
  test.fs.fail_write_after = 5;
  require(!test.update(false), "disable reports short-write failure");
  test.fs.fail_write_after = -1;
  require((entry->flags & REGION_DENY_FLOOD) && test.durable_allowed(),
          "short-write retains old enabled durable state");
  const unsigned disable_calls = test.callbacks.save_calls;
  require(test.update(false) && test.callbacks.save_calls == disable_calls + 1
              && !test.durable_allowed(), "retry saves even though RAM is already disabled");
}

static void invalid_context() {
  Scenario test;
  auto callback = test.cli._radio_profiles.handler;
  require(!callback(nullptr, true, false) && !callback(nullptr, true, true),
          "missing callback context fails safely");
  CommonCLI missing_map(nullptr, &test.callbacks);
  missing_map.onRegionsLoaded(&test.fs, false);
  require(!missing_map._radio_profiles.update(true, true), "missing map is rejected");
  CommonCLI missing_callbacks(&test.regions, nullptr);
  missing_callbacks.onRegionsLoaded(&test.fs, false);
  require(!missing_callbacks._radio_profiles.update(true, true), "missing save callback is rejected");
  require(test.regions.getCount() == 0 && test.fs.files.empty(),
          "invalid callback contexts do not alter policy");
}

static void unreadable_boot() {
  Scenario test;
  auto* local = test.regions.putRegion("#local", 0);
  auto* media = test.regions.putRegion("mediamesh", local->id);
  require(local && media, "build saved hierarchy before read failure");
  local->flags = REGION_DENY_DIRECT;
  const uint16_t local_id = local->id;
  test.regions.setDefaultRegion(local);
  test.regions.setHomeRegion(local);
  require(test.regions.save(&test.fs), "save pre-existing hierarchy");
  const auto baseline = test.fs.files;
  const auto writes = test.fs.open_writes;

  TransportKeyStore boot_keys;
  RegionMap boot_regions(boot_keys);
  auto* fallback = boot_regions.putRegion("#bootdefault", 0);
  require(fallback && fallback->id == local_id, "boot fallback reuses the stored default ID");
  TransportKey cached_fallback;
  require(boot_regions.getTransportKeysFor(*fallback, &cached_fallback, 1) == 1,
          "cache fallback public key before the delayed reload");
  RegionCallbacks callbacks(boot_regions, test.fs);
  CommonCLI boot(&boot_regions, &callbacks);
  test.fs.fail_read_open = true;
  const bool loaded = boot_regions.load(&test.fs);
  require(!loaded, "simulate actual unreadable region startup");
  boot.onRegionsLoaded(&test.fs, loaded);
  for (bool enable : {false, true}) {
    require(!boot._radio_profiles.update(enable, false)
                && !boot._radio_profiles.update(enable, true),
            "unknown existing region state rejects checks and commits");
  }
  require(test.fs.files == baseline && test.fs.open_writes == writes
              && callbacks.save_calls == 0 && boot_regions.getCount() == 1,
          "failed startup leaves committed hierarchy byte-exact");
  g_mock_millis = 1000;
  boot.recoverMediaMeshRegions();
  require(test.fs.files == baseline && boot._radio_profiles.bindings == 1,
          "failed background reload does not recreate the table");
  test.fs.fail_read_open = false;
  g_mock_millis = 1999;
  boot.recoverMediaMeshRegions();
  require(boot_regions.getCount() == 1, "background reload is paced");
  g_mock_millis = 2000;
  boot.recoverMediaMeshRegions();
  require(boot_regions.getCount() == 2 && boot._radio_profiles.bindings == 2
              && callbacks.default_changes == 1 && callbacks.last_default == local_id,
          "successful reload restores hierarchy and cached outgoing default scope");
  // Independent hashlib.sha256(b"#local").digest()[:16] vector.
  const TransportKey expected_default{{0xd2, 0xd3, 0x5f, 0xa7, 0x6b, 0xe9, 0x87, 0x5e, 0xd2, 0x54, 0xdb, 0x80, 0x39, 0x74, 0x83, 0xa5}};
  require(std::memcmp(cached_fallback.key, expected_default.key, sizeof(expected_default.key))
              && !std::memcmp(callbacks.role.default_scope.key, expected_default.key,
                              sizeof(expected_default.key)),
          "actual default-region callback derives the reloaded name, not the stale same-ID key");
  require(test.fs.files == baseline && test.fs.open_writes == writes,
          "successful read alone does not rewrite storage");
  require(boot._radio_profiles.update(true, false)
              && boot._radio_profiles.update(true, true),
          "recovered region state accepts normal enrollment without a reboot");
  require(boot_regions.findByName("mediamesh")->parent == local_id
              && boot_regions.getDefaultRegion()->id == local_id
              && boot_regions.getHomeRegion()->id == local_id,
          "late enrollment preserves the recovered hierarchy and routing selections");
  boot.recoverMediaMeshRegions();
  require(boot._radio_profiles.bindings == 2 && callbacks.default_changes == 1,
          "healthy maps do not reload or rebind every loop");
}

static void malformed_boot() {
  for (const char* path : {"/regions2", "/regions2.tmp", "/regions2.bak"}) {
    Scenario test;
    test.fs.files[path] = {0xaa, 0xbb, 0xcc};
    const auto before = test.fs.files;
    test.cli.onRegionsLoaded(&test.fs, false);
    require(!test.update(true, false) && !test.update(true)
                && !test.update(false),
            "malformed image or transaction sibling cannot look like a fresh store");
    require(test.fs.files == before && test.fs.open_writes == 0
                && test.callbacks.save_calls == 0 && test.regions.getCount() == 0,
            "automatic enrollment leaves unknown bytes and siblings untouched");
    // Ordinary reads may recover a .bak path, so exercise repeated malformed
    // reads on the public image and orphan staging path where no rename occurs.
    if (std::strcmp(path, "/regions2.bak")) {
      for (unsigned attempt = 1; attempt <= 3; ++attempt) {
        g_mock_millis = attempt * 1000;
        test.cli.recoverMediaMeshRegions();
        require(test.fs.files == before && test.fs.open_writes == 0,
                "repeated malformed reads never overwrite the unknown region image");
      }
    }
  }
}

static void metadata_boot() {
#if defined(ESP32_PLATFORM)
  for (const char* path : {"/regions2", "/regions2.tmp", "/regions2.bak"}) {
    Scenario test;
    test.fs.metadata_errors.insert(path);
    test.cli.onRegionsLoaded(&test.fs, false);
    require(!test.update(true, false) && !test.update(true),
            "metadata error cannot prove a fresh missing store");
    g_mock_millis = 1000;
    test.cli.recoverMediaMeshRegions();
    require(!test.update(true) && test.fs.files.empty() && test.fs.open_writes == 0,
            "metadata errors remain read-only across background retries");
    test.fs.metadata_errors.clear();
    g_mock_millis = 2000;
    test.cli.recoverMediaMeshRegions();
    require(test.update(true, false) && test.update(true) && test.durable_allowed(),
            "conclusively fresh store recovers automatically after metadata heals");
  }
#endif
}

int main(int argc, char** argv) {
  require(argc == 2, "select one test scenario");
  const char* name = argv[1];
  if (!std::strcmp(name, "scope-filter")) scope_filter();
  else if (!std::strcmp(name, "unknown-forward")) unknown_forward();
  else if (!std::strcmp(name, "existing-policy")) existing_policy();
  else if (!std::strcmp(name, "full-table")) full_table();
  else if (!std::strcmp(name, "save-retry")) save_retry();
  else if (!std::strcmp(name, "invalid-context")) invalid_context();
  else if (!std::strcmp(name, "unreadable-boot")) unreadable_boot();
  else if (!std::strcmp(name, "malformed-boot")) malformed_boot();
  else if (!std::strcmp(name, "metadata-boot")) metadata_boot();
  else require(false, "known scenario");
  std::printf("PASS: %s\n", name);
}
