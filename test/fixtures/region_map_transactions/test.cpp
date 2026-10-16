// Compile the actual RegionMap declarations and selected implementation bodies
// with the real platform transaction helper, not a second serializer.
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <helpers/RegionMap.h>
#include <helpers/RegionNameUtils.h>
#include <helpers/TxtDataHelpers.h>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#else
#include <contact_transaction_under_test.h>
#if defined(ESP32_PLATFORM)
#include <helpers/FilePresence.h>
#endif
#endif

#include "region_under_test.inc"

static void require(bool condition, const char* message) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s\n", message);
    std::exit(1);
  }
}

static void select(MemoryFS& fs) { metadata_filesystem = &fs; }

static bool has_policy(RegionMap& regions, bool candidate) {
  if (regions.getCount() != (candidate ? 4 : 3)) return false;
  const auto* root = regions.findById(11);
  const auto* private_region = regions.findById(27);
  const auto* local = regions.findById(35);
  const auto* added = regions.findById(50);
  if (!root || !private_region || !local) return false;
  if (std::strcmp(root->name, "#Mountain") || root->parent != 0
      || root->flags != (candidate ? 0 : REGION_DENY_FLOOD | REGION_DENY_DIRECT)) return false;
  if (std::strcmp(private_region->name, "$Private") || private_region->parent != 11
      || private_region->flags != REGION_DENY_DIRECT) return false;
  if (std::strcmp(local->name, "Local") || local->parent != 0
      || local->flags != REGION_DENY_FLOOD) return false;
  if (candidate && (!added || std::strcmp(added->name, "Candidate")
      || added->parent != 27 || added->flags != 0)) return false;
  if (!candidate && added) return false;
  if (!regions.getDefaultRegion() || !regions.getHomeRegion()) return false;
  return regions.getWildcard().flags == (candidate ? 0 : REGION_DENY_FLOOD | REGION_DENY_DIRECT)
      && regions.getDefaultRegion()->id == (candidate ? 35 : 11)
      && regions.getHomeRegion()->id == (candidate ? 11 : 27);
}

struct Scenario {
  TransportKeyStore keys;
  MemoryFS fs;
  RegionMap original{keys};
  RegionMap candidate{keys};
  std::vector<uint8_t> baseline;
  std::vector<uint8_t> replacement;
  std::string path;

  explicit Scenario(const char* target = "/regions2") : path(target) {
    select(fs);
    RegionEntry* root = original.putRegion("#Mountain", 0, 11);
    RegionEntry* private_region = original.putRegion("$Private", 11, 27);
    RegionEntry* local = original.putRegion("Local", 0, 35);
    require(root && private_region && local, "create baseline region hierarchy");
    root->flags = REGION_DENY_FLOOD | REGION_DENY_DIRECT;
    private_region->flags = REGION_DENY_DIRECT;
    local->flags = REGION_DENY_FLOOD;
    original.getWildcard().flags = REGION_DENY_FLOOD | REGION_DENY_DIRECT;
    original.setDefaultRegion(root);
    original.setHomeRegion(private_region);
    require(original.save(&fs, path.c_str()), "write baseline regions");
    baseline = fs.files.at(path);
    require(baseline.size() == 10 + 3 * 164, "legacy region image size unchanged");
    require(candidate.load(&fs, path.c_str()), "load candidate from durable baseline");
    candidate.findById(11)->flags = 0;
    candidate.getWildcard().flags = 0;
    candidate.setDefaultRegion(candidate.findById(35));
    candidate.setHomeRegion(candidate.findById(11));
    RegionEntry* added = candidate.putRegion("Candidate", 27, 50);
    require(added != nullptr, "create candidate region");
    added->flags = 0;
    MemoryFS healthy;
    select(healthy);
    require(candidate.save(&healthy, path.c_str()), "write candidate reference");
    replacement = healthy.files.at(path);
    select(fs);
  }

  void reboot(MemoryFS& storage, bool expected_candidate = false) {
    select(storage);
    RegionMap restarted(keys);
    const bool loaded = restarted.load(&storage, path.c_str());
    require(loaded && has_policy(restarted, expected_candidate),
            expected_candidate ? "durable candidate region/default/home/wildcard policy survived"
                : "durable old region/default/home/wildcard policy survived");
    // The next auto-assigned ID is also durable state, not merely recomputed
    // policy. Explicit IDs above leave 36 or 51 as the next allocation.
    const RegionEntry* next = restarted.putRegion("AfterReboot", 0);
    require(next && next->id == (expected_candidate ? 51 : 36),
            "durable next region ID survived");
    require(storage.files.at(path) == (expected_candidate ? replacement : baseline),
            "exact durable public image retained");
  }
};

static void healthy_save_and_reboot() {
  Scenario test;
  test.reboot(test.fs);
  require(test.original.save(&test.fs), "repeated healthy region save");
  test.reboot(test.fs);
  require(test.candidate.save(&test.fs), "publish changed region policy");
  test.reboot(test.fs, true);
  require(!test.fs.exists("/regions2.tmp") && !test.fs.exists("/regions2.bak"),
          "successful save cleaned transaction siblings");
}

static void invalid_inputs_do_not_mutate() {
  Scenario test;
  const auto before = test.fs.files;
  const unsigned opens = test.fs.open_writes;
  const unsigned removes = test.fs.remove_calls;
  const unsigned renames = test.fs.rename_calls;
  require(!test.candidate.save(nullptr), "null filesystem save refused");
  require(!test.candidate.load(nullptr), "null filesystem load refused");
  require(!test.candidate.save(&test.fs, ""), "empty target save refused");
  require(!test.candidate.load(&test.fs, ""), "empty target load refused");
  require(test.fs.files == before && test.fs.open_writes == opens
              && test.fs.remove_calls == removes && test.fs.rename_calls == renames,
          "invalid input left namespace untouched");
  require(has_policy(test.candidate, true), "invalid input retained caller's RAM policy");
  test.reboot(test.fs);
}

static void refusal_and_short_writes() {
  Scenario test;
  {
    MemoryFS storage = test.fs;
    select(storage);
    storage.fail_write_open = true;
    require(!test.candidate.save(&storage), "replacement open refusal reported");
    storage.fail_write_open = false;
    test.reboot(storage);
  }
  // Exhaust every byte offset. This includes every reserved/header field,
  // every region field/padding boundary, and clean record-boundary prefixes.
  for (size_t boundary = 0; boundary < test.replacement.size(); ++boundary) {
    MemoryFS storage = test.fs;
    select(storage);
    storage.fail_write_after = static_cast<int>(boundary);
    require(!test.candidate.save(&storage), "ordinary short write reported");
    storage.fail_write_after = -1;
    test.reboot(storage);
    require(!storage.exists("/regions2.tmp"), "short write cleaned staging file");
    require(has_policy(test.candidate, true), "save failure retained caller's RAM policy");
  }
  // A full filesystem is independent of the File::write short-write hook:
  // committed bytes consume capacity before each staging write is attempted.
  for (size_t available = 0; available < test.replacement.size(); ++available) {
    MemoryFS storage = test.fs;
    select(storage);
    storage.capacity = storage.used() + available;
    require(!test.candidate.save(&storage), "full-storage write failure reported");
    storage.capacity = std::numeric_limits<size_t>::max();
    test.reboot(storage);
  }
  std::printf("PASS: %zu short-write offsets and %zu full-storage capacities\n",
              test.replacement.size(), test.replacement.size());
}

static void staged_readback_and_publication_failure() {
  Scenario test;
  {
    MemoryFS storage = test.fs;
    select(storage);
    storage.fail_read_open = true;
    require(!test.candidate.save(&storage), "staged readback refusal reported");
    storage.fail_read_open = false;
    test.reboot(storage);
  }
  {
    MemoryFS storage = test.fs;
    select(storage);
    storage.failed_rename_sources.insert("/regions2.tmp");
    require(!test.candidate.save(&storage), "known unchanged publication reported");
    storage.failed_rename_sources.clear();
    test.reboot(storage);
  }
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  {
    MemoryFS storage = test.fs;
    select(storage);
    storage.failed_rename_sources.insert("/regions2");
    require(!test.candidate.save(&storage), "backup move refusal reported");
    storage.failed_rename_sources.clear();
    test.reboot(storage);
  }
  {
    MemoryFS storage = test.fs;
    select(storage);
    storage.failed_rename_sources.insert("/regions2.tmp");
    storage.failed_rename_sources.insert("/regions2.bak");
    require(!test.candidate.save(&storage), "failed publication and rollback reported");
    require(!storage.exists("/regions2") && storage.files.at("/regions2.bak") == test.baseline,
            "rollback failure retained old recovery image");
    storage.failed_rename_sources.clear();
    test.reboot(storage);
  }
  {
    MemoryFS storage = test.fs;
    select(storage);
    require(storage.rename("/regions2", "/regions2.bak"), "simulate reset in backup gap");
    storage.files["/regions2.tmp"] = test.replacement;
    test.reboot(storage);
    require(!storage.exists("/regions2.bak"), "fresh load recovered backup gap");
  }
#endif
}

static void custom_path_isolation() {
  Scenario test("/regions-custom");
  test.fs.files["/regions2"] = {0x12, 0x34};
  test.fs.files["/regions2.tmp"] = {0x56};
  test.fs.files["/regions2.bak"] = {0x78};
  const auto unrelated = test.fs.files;
  test.fs.fail_write_after = 9;
  require(!test.candidate.save(&test.fs, test.path.c_str()), "custom path short write reported");
  test.fs.fail_write_after = -1;
  test.reboot(test.fs);
  for (const char* name : {"/regions2", "/regions2.tmp", "/regions2.bak"}) {
    require(test.fs.files.at(name) == unrelated.at(name), "custom path left default artifacts alone");
  }
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  require(test.fs.rename(test.path.c_str(), (test.path + ".bak").c_str()),
          "simulate custom backup gap");
  test.fs.files[test.path + ".tmp"] = test.replacement;
  test.reboot(test.fs);
  for (const char* name : {"/regions2", "/regions2.tmp", "/regions2.bak"}) {
    require(test.fs.files.at(name) == unrelated.at(name), "custom recovery left default artifacts alone");
  }
  // ContactFileTransaction has 48 bytes for each sibling pathname, including
  // its suffix and terminator. Reject overlong paths before any recovery or
  // staging can address a truncated name belonging to another file.
  const std::string limit_path = "/" + std::string(42, 'x');
  Scenario limit(limit_path.c_str());
  require(limit.candidate.save(&limit.fs, limit.path.c_str()), "43-byte custom target fits");
  limit.reboot(limit.fs, true);
  for (size_t length : {size_t(44), size_t(80), size_t(128)}) {
    const std::string path = "/" + std::string(length - 1, 'y');
    MemoryFS storage;
    select(storage);
    storage.files[path] = test.baseline;
    storage.files[path + ".tmp"] = test.replacement;
    storage.files[path + ".bak"] = test.baseline;
    storage.files[(path + ".tmp").substr(0, 47)] = {0x66};
    storage.files[(path + ".bak").substr(0, 47)] = {0x77};
    const auto before = storage.files;
    require(!test.candidate.save(&storage, path.c_str()), "overlong custom save refused");
    RegionMap restarted(test.keys);
    require(!restarted.load(&storage, path.c_str()), "overlong custom load refused");
    require(storage.files == before && storage.open_writes == 0
                && storage.remove_calls == 0 && storage.rename_calls == 0,
            "overlong custom path left all complete and truncated artifacts untouched");
  }
#endif
}

#if defined(ESP32_PLATFORM)
static void esp_metadata_errors_fail_closed() {
  Scenario test;
  for (const char* failed : {"/regions2", "/regions2.bak", "/regions2.tmp"}) {
    MemoryFS storage = test.fs;
    select(storage);
    storage.files["/regions2.tmp"] = {0x61};
    const auto before = storage.files;
    storage.metadata_errors.insert(failed);
    require(!test.candidate.save(&storage), "ESP metadata error refused save");
    require(storage.files == before && storage.open_writes == test.fs.open_writes
                && storage.remove_calls == test.fs.remove_calls
                && storage.rename_calls == test.fs.rename_calls,
            "ESP metadata error caused no namespace mutation");
    storage.metadata_errors.clear();
    test.reboot(storage);
  }
  for (const char* failed : {"/regions2", "/regions2.bak"}) {
    MemoryFS storage = test.fs;
    select(storage);
    require(storage.rename("/regions2", "/regions2.bak"), "create metadata recovery gap");
    storage.files["/regions2.tmp"] = test.replacement;
    storage.metadata_errors.insert(failed);
    const auto before = storage.files;
    RegionMap restarted(test.keys);
    require(!restarted.load(&storage), "ESP metadata error refused recovery load");
    require(storage.files == before, "ESP metadata error retained backup and candidate bytes");
    storage.metadata_errors.clear();
    test.reboot(storage);
  }
}
#endif

int main(int argc, char** argv) {
  if (argc == 2 && std::strcmp(argv[1], "short-write") == 0) {
    refusal_and_short_writes();
    return 0;
  }
  healthy_save_and_reboot();
  invalid_inputs_do_not_mutate();
  refusal_and_short_writes();
  staged_readback_and_publication_failure();
  custom_path_isolation();
#if defined(ESP32_PLATFORM)
  esp_metadata_errors_fail_closed();
#endif
  std::puts("PASS: actual RegionMap durable transaction and reboot policy");
}
