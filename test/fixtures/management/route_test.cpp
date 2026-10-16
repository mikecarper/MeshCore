#include "IdentityStore.h"
#include <Mesh.h>
#include "ContactFileTransaction.h"
#include "FilePresence.h"
#include "FileRead.h"
#include "PersistentStoreFormat.h"
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>

class RegionMap;
namespace mesh { struct DataRouteState; }

class CommonCLI {
public:
  mesh::DataRouteState* _data_route = nullptr;
  bool adoptLegacyDataTxPath(const uint8_t* path, uint8_t path_len);
};

// Generated from the production source by the host test runner. Includes the
// real route state, save helper, and adoption method rather than copies of their
// behavior. The filesystem and packet boundary use the existing host fixtures.
#include "production_route.inc"

struct Fixture {
  MemoryFS fs;
  mesh::DataRouteState route;
  CommonCLI cli;

  Fixture() {
    route.fs = &fs;
    cli._data_route = &route;
  }
};

static void verifySavedRoute(const Fixture& f) {
  assert(f.route.persisted);
  const auto& data = f.fs.files.at("/data_tx");
  assert(data.size() == 108);
  assert(!memcmp(data.data(), "DTX1", 4));
  assert(data[4] == f.route.path_len && data[5] == f.route.region_mode);
  assert(data[6] == 0 && data[7] == 0 && data[103] == 0);
  assert(!memcmp(data.data() + 8, f.route.path, MAX_PATH_SIZE));
  assert(!memcmp(data.data() + 72, f.route.region, sizeof(f.route.region)));
  assert(mesh::storage::readLE32(data.data() + 104)
      == mesh::storage::updateCRC32(0xffffffff, data.data(), 104));
  assert(!f.fs.exists("/data_tx.tmp") && !f.fs.exists("/data_tx.bak"));
}

static void checkPersistedRouteWins() {
  const uint8_t legacy[] = {0x12, 0x34, 0x56, 0x78};
  for (const uint8_t explicit_len : {uint8_t(0), uint8_t(0x42),
                                    uint8_t(OUT_PATH_UNKNOWN)}) {
    Fixture f;
    f.route.path_len = explicit_len;
    if (explicit_len == 0x42) memcpy(f.route.path, "\xab\xcd\xef\x01", 4);
    f.route.region_mode = mesh::DATA_REGION_NAMED;
    strcpy(f.route.region, "shared-scope");
    assert(mesh::dataRouteSave(f.route));
    verifySavedRoute(f);
    const auto saved_files = f.fs.files;
    const mesh::DataRouteState previous = f.route;

    // A configured shared path, including an explicit none, takes precedence
    // over a legacy path. Legacy none must also leave that shared route intact.
    assert(f.cli.adoptLegacyDataTxPath(legacy, 0x42));
    assert(f.cli.adoptLegacyDataTxPath(nullptr, OUT_PATH_UNKNOWN));
    assert(f.cli.adoptLegacyDataTxPath(nullptr, 0));
    assert(f.route.path_len == previous.path_len);
    assert(!memcmp(f.route.path, previous.path, sizeof(f.route.path)));
    assert(f.route.region_mode == previous.region_mode);
    assert(!memcmp(f.route.region, previous.region, sizeof(f.route.region)));
    assert(f.route.healthy && f.route.persisted);
    assert(f.fs.files == saved_files);
  }
}

static void checkSuccessfulMigration() {
  const uint8_t legacy[] = {0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc};
  for (const uint8_t path_len : {uint8_t(0), uint8_t(2), uint8_t(0x42),
                                uint8_t(0x82)}) {
    Fixture f;
    memset(f.route.path, 0xa5, sizeof(f.route.path));
    assert(f.cli.adoptLegacyDataTxPath(path_len ? legacy : nullptr, path_len));
    assert(f.route.path_len == path_len && f.route.healthy);
    const size_t bytes = (path_len & 63) * ((path_len >> 6) + 1);
    assert(!memcmp(f.route.path, legacy, bytes));
    for (size_t i = bytes; i < sizeof(f.route.path); ++i) assert(f.route.path[i] == 0);
    assert(f.route.region_mode == mesh::DATA_REGION_AUTO && !f.route.region[0]);
    verifySavedRoute(f);
  }
}

static void checkIsolatedPublishFailureAndRetry() {
  Fixture f;
  f.route.path_len = 1;
  memset(f.route.path, 0xa5, sizeof(f.route.path));
  f.route.region_mode = mesh::DATA_REGION_NAMED;
  strcpy(f.route.region, "shared-scope");
  f.fs.files["/management"] = std::vector<uint8_t>(120, 0);
  memcpy(f.fs.files["/management"].data(), "MGC1", 4);
  const auto saved_files = f.fs.files;
  const mesh::DataRouteState previous = f.route;
  const uint8_t legacy[] = {0x12, 0x34, 0x56, 0x78};

  f.fs.fail_rename_from = {"/data_tx.tmp"};
  assert(!f.cli.adoptLegacyDataTxPath(legacy, 0x42));
  assert(f.route.path_len == previous.path_len && !f.route.persisted);
  assert(!memcmp(f.route.path, previous.path, sizeof(f.route.path)));
  assert(f.route.region_mode == previous.region_mode);
  assert(!memcmp(f.route.region, previous.region, sizeof(f.route.region)));
  assert(f.route.healthy);
  assert(f.fs.files == saved_files);

  // The failed /data_tx publish is isolated: a later /management transaction
  // can succeed. The production loader must therefore act on adoption failure
  // before allowing a management checkpoint to discard the legacy route.
  uint8_t management[120] = {};
  memcpy(management, "MGC2", 4);
  mesh::ContactFileTransaction writer(&f.fs, "/management");
  assert(writer && writer.write(management, sizeof(management)) == sizeof(management));
  assert(writer.commit());
  assert(f.fs.files.at("/management")
      == std::vector<uint8_t>(management, management + sizeof(management)));
  assert(!f.fs.exists("/data_tx"));
  assert(!f.fs.exists("/management.tmp") && !f.fs.exists("/management.bak"));

  f.fs.fail_rename_from.clear();
  assert(f.cli.adoptLegacyDataTxPath(legacy, 0x42));
  verifySavedRoute(f);
  assert(f.route.path_len == 0x42 && !memcmp(f.route.path, legacy, sizeof(legacy)));
}

static void checkUnavailableUnhealthyAndInvalidRoutes() {
  const uint8_t legacy[MAX_PATH_SIZE] = {};
  CommonCLI unavailable;
  assert(!unavailable.adoptLegacyDataTxPath(legacy, 1));

  for (const bool persisted : {false, true}) {
    Fixture f;
    f.route.healthy = false;
    f.route.persisted = persisted;
    assert(!f.cli.adoptLegacyDataTxPath(legacy, 1));
    assert(f.fs.files.empty() && f.route.path_len == 0);
  }

  for (const uint8_t invalid : {uint8_t(0xc0), uint8_t(0x61), uint8_t(0x96)}) {
    Fixture f;
    assert(!f.cli.adoptLegacyDataTxPath(legacy, invalid));
    assert(f.fs.files.empty() && f.route.path_len == 0 && !f.route.persisted);
  }
  Fixture missing_path;
  assert(!missing_path.cli.adoptLegacyDataTxPath(nullptr, 1));
  assert(missing_path.fs.files.empty() && !missing_path.route.persisted);
}

static void checkPathParsing() {
  struct Case { const char* text; uint8_t encoded; const char* bytes; };
  for (const auto& valid : {Case{"direct", 0, ""}, Case{" none ", OUT_PATH_UNKNOWN, ""},
                           Case{"clear", OUT_PATH_UNKNOWN, ""}, Case{"-", OUT_PATH_UNKNOWN, ""},
                           Case{"1:12ab", 2, "\x12\xab"}, Case{" 12, AB ", 2, "\x12\xab"},
                           Case{"2:12abcd34", 0x42, "\x12\xab\xcd\x34"},
                           Case{"12ab, cd34", 0x42, "\x12\xab\xcd\x34"},
                           Case{"3:123456abcdef", 0x82, "\x12\x34\x56\xab\xcd\xef"}}) {
    std::vector<char> text(valid.text, valid.text + strlen(valid.text) + 1);
    uint8_t path[MAX_PATH_SIZE], encoded = OUT_PATH_UNKNOWN;
    const char* error = nullptr;
    assert(mesh::parseDataPath(text.data(), path, encoded, error));
    assert(encoded == valid.encoded && !error);
    const size_t bytes = encoded == OUT_PATH_UNKNOWN ? 0 : (encoded & 63) * ((encoded >> 6) + 1);
    assert(!memcmp(path, valid.bytes, bytes));
    for (size_t i = bytes; i < sizeof(path); ++i) assert(path[i] == 0);
  }
  for (const char* malformed : {"", " ", ",12", "12,,ab", "12,", "12, ",
                                "12ab,cd34,", "12ab,cd34,  ", "1:", "1:123", "2:12ab34",
                                "3:123456ab", "4:12345678", "12,abcd", "12;ab", "12,xx"}) {
    std::vector<char> text(malformed, malformed + strlen(malformed) + 1);
    uint8_t path[MAX_PATH_SIZE], encoded = OUT_PATH_UNKNOWN;
    const char* error = nullptr;
    if (mesh::parseDataPath(text.data(), path, encoded, error)) {
      fprintf(stderr, "malformed data.tx path accepted: '%s'\n", malformed);
      assert(false);
    }
    assert(error);
  }
}

int main() {
  checkPersistedRouteWins();
  checkSuccessfulMigration();
  checkIsolatedPublishFailureAndRetry();
  checkUnavailableUnhealthyAndInvalidRoutes();
  checkPathParsing();
  puts("management route adoption checks passed");
}
