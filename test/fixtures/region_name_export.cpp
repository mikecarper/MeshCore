// Exercise the actual RegionMap declarations and method bodies, including the
// actual Repeater/Room getLocalScopes wrappers through minimal role adapters.
#include <algorithm>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>
#include <helpers/RegionMap.h>
#include <helpers/RegionNameUtils.h>
#include <helpers/TxtDataHelpers.h>

#include "region_under_test.inc"

// Frozen original exporter from commit 3a3323c4d594dd766dea25caaad06bb5225a8ffa,
// RegionMap.cpp SHA256 0f3dd22e2c228061334bcf95416cf40ecdd01310f99a011785a0837e43334900.
// The adapter snapshots real public region state. The method below is unchanged
// and is used only for >=2-byte legacy output compatibility, never for safety.
namespace legacy {
class RegionMap {
  uint16_t num_regions;
  RegionEntry regions[MAX_REGION_ENTRIES];
  RegionEntry wildcard;
public:
  explicit RegionMap(::RegionMap& source)
      : num_regions(source.getCount()), wildcard(source.getWildcard()) {
    for (int i = 0; i < num_regions; ++i) regions[i] = *source.getByIdx(i);
  }
  int exportNamesTo(char *dest, int max_len, uint8_t mask, bool invert = false);
};

int RegionMap::exportNamesTo(char *dest, int max_len, uint8_t mask, bool invert) {
  char *dp = dest;

  // Check wildcard region
  bool wildcard_matches = invert ? (wildcard.flags & mask) : !(wildcard.flags & mask);
  if (wildcard_matches) {
    *dp++ = '*';
    *dp++ = ',';
  }

  for (int i = 0; i < num_regions; i++) {
    auto region = &regions[i];

    // Check if region matches the filter criteria
    bool region_matches = invert ? (region->flags & mask) : !(region->flags & mask);

    if (region_matches) {
      int len = strlen(skip_hash(region->name));
      if ((dp - dest) + len + 2 < max_len) {   // only append if name will fit
        memcpy(dp, skip_hash(region->name), len);
        dp += len;
        *dp++ = ',';
      }
    }
  }

  if (dp > dest) { dp--; }   // don't include trailing comma

  *dp = 0;  // set null terminator
  return dp - dest;   // return length
}
}  // namespace legacy

static void require(bool condition, const char* message) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s\n", message);
    std::exit(1);
  }
}

static void populate(RegionMap& map, bool dense) {
  if (dense) {
    for (int i = 0; i < MAX_REGION_ENTRIES; ++i) {
      char name[31];
      std::snprintf(name, sizeof(name), "#Place%02d", i);
      RegionEntry* entry = map.putRegion(name, 0);
      require(entry != nullptr, "populate full real RegionMap");
      entry->flags = 0;
    }
    return;
  }
  const char* names[] = {"#Alpine", "$Secret", "B", "#Direct", "Both",
                         "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"};
  const uint8_t flags[] = {0, 0, REGION_DENY_FLOOD, REGION_DENY_DIRECT,
                          REGION_DENY_FLOOD | REGION_DENY_DIRECT, 0};
  for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
    RegionEntry* entry = map.putRegion(names[i], 0);
    require(entry != nullptr, "populate region names and filter policies");
    entry->flags = flags[i];
  }
}

static std::vector<std::string> eligible(RegionMap& map, uint8_t mask, bool invert) {
  std::vector<std::string> names;
  const auto matches = [=](uint8_t flags) {
    return invert ? (flags & mask) != 0 : (flags & mask) == 0;
  };
  if (matches(map.getWildcard().flags)) names.emplace_back("*");
  for (int i = 0; i < map.getCount(); ++i) {
    const RegionEntry* entry = map.getByIdx(i);
    if (matches(entry->flags)) names.emplace_back(RegionNameUtils::canonical(entry->name));
  }
  return names;
}

static std::string joined(const std::vector<std::string>& names) {
  std::string result;
  for (const auto& name : names) {
    if (!result.empty()) result += ',';
    result += name;
  }
  return result;
}

static void validate_output(const char* destination, int capacity, int count,
                            const std::vector<std::string>& names) {
  require(count >= 0 && count < capacity, "returned length is inside destination capacity");
  require(destination[count] == 0, "returned length points to null terminator");
  require(std::strlen(destination) == static_cast<size_t>(count),
          "returned length is exactly the produced string length");
  const std::string actual(destination);
  require(actual.empty() || (actual.front() != ',' && actual.back() != ','),
          "no leading or trailing comma");
  size_t allowed = 0;
  for (size_t begin = 0; begin < actual.size();) {
    const size_t end = actual.find(',', begin);
    const std::string token = actual.substr(begin, end == std::string::npos ? end : end - begin);
    require(!token.empty(), "no empty region token");
    while (allowed < names.size() && names[allowed] != token) ++allowed;
    require(allowed < names.size(), "only complete matching names in original order");
    ++allowed;
    if (end == std::string::npos) break;
    begin = end + 1;
  }
  if (joined(names).size() + 3 < static_cast<size_t>(capacity)) {
    require(actual == joined(names), "ample destination includes every matching name");
  }
}

static void guarded_export(RegionMap& map, int capacity, uint8_t mask, bool invert) {
  constexpr size_t guard = 32;
  constexpr unsigned char sentinel = 0xA5;
  const size_t accessible = capacity > 0 ? static_cast<size_t>(capacity) : 0;
  std::vector<unsigned char> storage(guard + accessible + guard, sentinel);
  char* destination = reinterpret_cast<char*>(storage.data() + guard);
  const int count = map.exportNamesTo(destination, capacity, mask, invert);
  require(std::all_of(storage.begin(), storage.begin() + guard,
                      [](unsigned char c) { return c == sentinel; }),
          "export preserved prefix guard");
  require(std::all_of(storage.begin() + guard + accessible, storage.end(),
                      [](unsigned char c) { return c == sentinel; }),
          "export wrote no byte beyond claimed destination capacity");
  if (capacity <= 0) {
    require(count == 0, "nonpositive destination returns zero without writing");
  } else {
    validate_output(destination, capacity, count, eligible(map, mask, invert));
    if (capacity == 1) require(destination[0] == 0, "one-byte destination is empty");
    if (capacity >= 2) {
      legacy::RegionMap original(map);
      std::vector<unsigned char> baseline(accessible, sentinel);
      const int old_count = original.exportNamesTo(
          reinterpret_cast<char*>(baseline.data()), capacity, mask, invert);
      require(count == old_count
                  && std::equal(baseline.begin(), baseline.end(), storage.begin() + guard),
              "all >=2-byte results and destination bytes match frozen original exporter");
    }
  }
}

template<class Role>
static void guarded_wrapper(RegionMap& map, size_t capacity) {
  constexpr size_t guard = 32;
  constexpr unsigned char sentinel = 0xA5;
  std::vector<unsigned char> storage(guard + capacity + guard, sentinel);
  char* destination = reinterpret_cast<char*>(storage.data() + guard);
  Role role(map);
  role.getLocalScopes(destination, capacity);
  require(std::all_of(storage.begin(), storage.begin() + guard,
                      [](unsigned char c) { return c == sentinel; }),
          "role wrapper preserved prefix guard");
  require(std::all_of(storage.begin() + guard + capacity, storage.end(),
                      [](unsigned char c) { return c == sentinel; }),
          "role wrapper wrote no byte beyond claimed destination capacity");
  if (capacity) {
    const void* end = std::memchr(destination, 0, capacity);
    require(end != nullptr, "role wrapper terminates inside supplied buffer");
    validate_output(destination, static_cast<int>(capacity),
                    static_cast<const char*>(end) - destination,
                    eligible(map, REGION_DENY_FLOOD, false));
  }
}

static void healthy() {
  TransportKeyStore store;
  RegionMap map(store);
  populate(map, false);
  for (int capacity : {96, 159, 160}) {
    char destination[160];
    const char* golden = "*,Alpine,$Secret,Direct,ABCDEFGHIJKLMNOPQRSTUVWXYZ1234";
    const int count = map.exportNamesTo(destination, capacity, REGION_DENY_FLOOD);
    require(std::strcmp(destination, golden) == 0 && count == static_cast<int>(std::strlen(golden)),
            "healthy production-size output unchanged");
    guarded_wrapper<repeater::MyMesh>(map, capacity);
    guarded_wrapper<room::MyMesh>(map, capacity);
  }
  RegionMap full(store);
  populate(full, true);
  const char* golden96 = "*,Place00,Place01,Place02,Place03,Place04,Place05,Place06,Place07,Place08,Place09,Place10";
  const char* golden159 = "*,Place00,Place01,Place02,Place03,Place04,Place05,Place06,Place07,Place08,Place09,Place10,Place11,Place12,Place13,Place14,Place15,Place16,Place17,Place18";
  for (int capacity : {96, 159, 160}) {
    char destination[160];
    const char* golden = capacity == 96 ? golden96 : golden159;
    const int count = full.exportNamesTo(destination, capacity, REGION_DENY_FLOOD);
    require(std::strcmp(destination, golden) == 0 && count == static_cast<int>(std::strlen(golden)),
            "full-map production-size truncation unchanged");
  }
}

static void invalid() {
  TransportKeyStore store;
  RegionMap map(store);
  populate(map, false);
  for (int capacity : {INT_MIN, -4, -1, 0, 1, 2, 160, INT_MAX}) {
    require(map.exportNamesTo(nullptr, capacity, REGION_DENY_FLOOD) == 0,
            "null destination returns zero without access");
  }
  for (int capacity : {INT_MIN, -4, -1, 0}) {
    guarded_export(map, capacity, REGION_DENY_FLOOD, false);
  }
  repeater::MyMesh repeater(map);
  room::MyMesh room(map);
  for (size_t capacity : {size_t(0), size_t(1), size_t(160)}) {
    repeater.getLocalScopes(nullptr, capacity);
    room.getLocalScopes(nullptr, capacity);
  }
}

static void matrix() {
  TransportKeyStore store;
  size_t cases = 0;
  for (int shape = 0; shape < 3; ++shape) {
    RegionMap map(store);
    if (shape) populate(map, shape == 2);
    for (uint8_t wildcard_flags = 0; wildcard_flags < 4; ++wildcard_flags) {
      map.getWildcard().flags = wildcard_flags;
      for (uint8_t mask = 0; mask < 4; ++mask) {
        for (bool invert : {false, true}) {
          for (int capacity = -1; capacity <= 320; ++capacity) {
            guarded_export(map, capacity, mask, invert);
            ++cases;
          }
        }
      }
    }
  }
  std::printf("PASS: %zu direct capacity/filter combinations\n", cases);
}

static void wrappers() {
  TransportKeyStore store;
  for (int shape = 0; shape < 3; ++shape) {
    RegionMap map(store);
    if (shape) populate(map, shape == 2);
    for (uint8_t wildcard_flags = 0; wildcard_flags < 4; ++wildcard_flags) {
      map.getWildcard().flags = wildcard_flags;
      for (size_t capacity = 0; capacity <= 320; ++capacity) {
        guarded_wrapper<repeater::MyMesh>(map, capacity);
        guarded_wrapper<room::MyMesh>(map, capacity);
      }
    }
  }
}

template<class Role>
static void one_byte() {
  TransportKeyStore store;
  RegionMap map(store);
  Role role(map);
  // An exact one-byte allocation makes an out-of-capacity intermediate comma
  // store observable to ASan even if the eventual terminator is otherwise valid.
  std::unique_ptr<char[]> destination(new char[1]);
  destination[0] = '!';
  role.getLocalScopes(destination.get(), 1);
  require(destination[0] == 0, "one-byte role wrapper produces an empty string");
}

static void direct_one_byte() {
  TransportKeyStore store;
  RegionMap map(store);
  std::unique_ptr<char[]> destination(new char[1]);
  destination[0] = '!';
  require(map.exportNamesTo(destination.get(), 1, REGION_DENY_FLOOD) == 0,
          "one-byte direct exporter returns zero");
  require(destination[0] == 0, "one-byte direct exporter produces an empty string");
}

int main(int argc, char** argv) {
  require(argc == 2, "one scenario argument required");
  const std::string scenario(argv[1]);
  if (scenario == "healthy") healthy();
  else if (scenario == "invalid") invalid();
  else if (scenario == "matrix") matrix();
  else if (scenario == "wrappers") wrappers();
  else if (scenario == "direct-one-byte") direct_one_byte();
  else if (scenario == "repeater-one-byte") one_byte<repeater::MyMesh>();
  else if (scenario == "room-one-byte") one_byte<room::MyMesh>();
  else require(false, "known scenario");
  std::printf("PASS: %s\n", scenario.c_str());
  return 0;
}
