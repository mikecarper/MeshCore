#include "FakeFilesystem.h"
#include <helpers/PersistentStoreFormat.h>
#include <RadioTxPolicy.h>
#define FILESYSTEM FakeFilesystem
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#define MESH_DEBUG_PRINTLN(...) ((void)0)

static const char* SECONDARY_MIGRATION_JOURNAL = "/.extrafs.mig";
static const uint8_t SECONDARY_MIGRATION_PENDING = 1, SECONDARY_MIGRATION_COMMITTED = 2;
bool contactPathPresence(FakeFilesystem* fs, const char* path, bool& present) {
  if (fs->metadata_error) return false;
  present = fs->exists(path);
  return true;
}
struct ChannelDetails {
  struct { uint8_t tx_radio = mesh::RADIO_TX_AUTO, secret[32] = {}; } channel;
  char name[32] = {};
};
struct DataStoreHost {
  bool getChannelForSave(uint8_t, ChannelDetails&) { return false; }
};
class DataStore {
 public:
  FakeFilesystem* _fs;
  FakeFilesystem* _fsExtra;
  bool _contact_load_incomplete = false, _identity_creation_blocked = false;
  bool _prefs_load_incomplete = false;
  bool _channel_load_incomplete = false;
  DataStore(FakeFilesystem& primary, FakeFilesystem& secondary) : _fs(&primary), _fsExtra(&secondary) {}
  File openRead(FakeFilesystem* fs, const char* path) { return fs->open(path); }
  FakeFilesystem* _getContactsChannelsFS() { return _fsExtra ? _fsExtra : _fs; }
  bool migrateToSecondaryFS();
  bool saveChannels(DataStoreHost*);
};
#include "migration_under_test.h"

static std::vector<uint8_t> journal(uint8_t state) {
  return {'M', 'C', 'X', 'F', 1, state};
}
static void seed(FakeFilesystem& primary, FakeFilesystem& secondary) {
  primary.files["/contacts3"] = {1, 2, 3};
  primary.files["/channels2"] = {4, 5, 6};
  secondary.files[SECONDARY_MIGRATION_JOURNAL] = journal(SECONDARY_MIGRATION_PENDING);
}

int main() {
  unsigned scenarios = 0;
  // Final journal faults: late error, exhausted verification, unreadable
  // reconciliation, missing/invalid journal, metadata error, retained Pending,
  // and proven pre-publication failure with an unrelated metadata fault.
  for (unsigned fault = 0; fault < 8; ++fault) {
    FakeFilesystem primary, secondary;
    seed(primary, secondary);
    const auto originals = primary.files;
    DataStore store(primary, secondary);
    if (fault == 6) secondary.false_success_rename = 3;
    secondary.after_rename = [fault](FakeFilesystem& fs, const char* path) {
      if (fault == 7 && strcmp(path, "/channels2") == 0) {
        fs.max_write = 0;
        fs.metadata_error = true;
      }
      if (strcmp(path, SECONDARY_MIGRATION_JOURNAL) != 0) return;
      if (fault == 0) fs.late_rename_error = fs.renames;
      if (fault == 1) fs.read_open_failures[path] = 3;
      if (fault == 2) fs.read_open_failures[path] = 4;
      if (fault == 3) fs.files.erase(path);
      if (fault == 4) fs.files[path][0] ^= 1;
      if (fault == 5) {
        fs.read_open_failures[path] = 3;
        fs.metadata_error = true;
      }
    };
    const bool migrated = store.migrateToSecondaryFS();
    const bool committed = fault < 2;
    const bool primary_retained = fault >= 6;
    assert(migrated == committed);
    assert(store._contact_load_incomplete == (!committed && !primary_retained));
    assert(store._fs == &primary);
    assert(store._fsExtra == (primary_retained ? nullptr : &secondary));
    if (committed) {
      assert(primary.files.empty());
      assert(!secondary.exists(SECONDARY_MIGRATION_JOURNAL));
    } else {
      assert(primary.files == originals);
      // Reproduce the authority leak: a quarantined store must refuse writes.
      if (!primary_retained) {
        DataStoreHost host;
        const auto primary_before = primary.files, secondary_before = secondary.files;
        assert(!store.saveChannels(&host));
        assert(primary.files == primary_before && secondary.files == secondary_before);
      }
    }
    for (const auto& source : originals) assert(secondary.files.at(source.first) == source.second);

    // A subsequent boot honors the journal that actually reached public storage.
    secondary.after_rename = {};
    secondary.metadata_error = false;
    secondary.max_write = std::numeric_limits<size_t>::max();
    secondary.false_success_rename = secondary.late_rename_error = 0;
    secondary.read_open_failures.clear();
    DataStore reboot(primary, secondary);
    if (fault == 4) {
      assert(!reboot.migrateToSecondaryFS() && reboot._contact_load_incomplete);
      assert(primary.files == originals);
    } else {
      if (secondary.exists(SECONDARY_MIGRATION_JOURNAL)
          && secondary.files.at(SECONDARY_MIGRATION_JOURNAL) == journal(SECONDARY_MIGRATION_COMMITTED)) {
        secondary.files["/contacts3"] = {9, 8, 7};
      }
      assert(reboot.migrateToSecondaryFS());
      assert(primary.files.empty() && reboot._fsExtra == &secondary);
      assert(!secondary.exists(SECONDARY_MIGRATION_JOURNAL));
      assert(secondary.files.at("/contacts3") == (fault == 2 || fault == 5
          ? std::vector<uint8_t>{9, 8, 7} : originals.at("/contacts3")));
    }
    ++scenarios;
  }

  // A copy failure cannot publish the final authority switch or retire sources.
  {
    FakeFilesystem primary, secondary;
    seed(primary, secondary);
    const auto originals = primary.files;
    secondary.max_write = 0;
    DataStore store(primary, secondary);
    assert(!store.migrateToSecondaryFS());
    assert(store._fsExtra == nullptr && !store._contact_load_incomplete);
    assert(primary.files == originals);
    assert(secondary.files.at(SECONDARY_MIGRATION_JOURNAL) == journal(SECONDARY_MIGRATION_PENDING));
    ++scenarios;
  }
  // An uncertain initial Pending publication still leaves complete primary authority.
  {
    FakeFilesystem primary, secondary;
    primary.files["/contacts3"] = {1, 2, 3};
    const auto originals = primary.files;
    secondary.after_rename = [](FakeFilesystem& fs, const char* path) {
      if (strcmp(path, SECONDARY_MIGRATION_JOURNAL) == 0) fs.read_open_failures[path] = 3;
    };
    DataStore store(primary, secondary);
    assert(!store.migrateToSecondaryFS());
    assert(store._fsExtra == nullptr && !store._contact_load_incomplete);
    assert(primary.files == originals && !secondary.exists("/contacts3"));
    ++scenarios;
  }
  std::printf("PASS: %u secondary migration publication scenarios\n", scenarios);
}
