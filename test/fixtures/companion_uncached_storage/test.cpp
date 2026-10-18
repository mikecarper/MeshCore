#include <algorithm>
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <limits>
#include <map>
#include <string>
#include <vector>
#include <Arduino.h>

enum { FILE_O_READ = 0, FILE_O_WRITE = 1, LFS_ERR_NOENT = -2 };
class FakeFilesystem;
class File {
  FakeFilesystem* fs = nullptr;
  std::string path;
  size_t position = 0;
  bool writing = false;
public:
  File() = default;
  explicit File(FakeFilesystem& filesystem) : fs(&filesystem) {}
  File(FakeFilesystem* filesystem, const char* name, bool write)
      : fs(filesystem), path(name), writing(write) {}
  explicit operator bool() const { return fs != nullptr && !path.empty(); }
  bool isDirectory() const { return false; }
  bool open(const char* name, uint8_t mode);
  size_t read(uint8_t* bytes, size_t length);
  size_t write(const uint8_t* bytes, size_t length);
  size_t size() const;
  int available() const { return static_cast<int>(size() - position); }
  bool setBufferSize(size_t size) { return size != 0; }
  void flush() {}
  void close() { fs = nullptr; }
};

class FakeFilesystem {
public:
  using Files = std::map<std::string, std::vector<uint8_t>>;
  Files files;
  size_t max_write = std::numeric_limits<size_t>::max();
  size_t max_read = std::numeric_limits<size_t>::max();
  std::string fail_open;
  unsigned fail_open_remaining = std::numeric_limits<unsigned>::max();
  int stat_error = 0;
  bool fail_remove = false;
  unsigned fail_rename = 0, renames = 0, writes = 0;
  unsigned read_opens = 0;
  std::vector<Files> snapshots;
  bool exists(const char* path) const {
#if defined(ESP32_PLATFORM)
    if (fail_open == path && fail_open_remaining != 0) return false; // ESP32 VFS exists opens the file.
#endif
    return files.count(path) != 0;
  }
  bool mkdir(const char*) { return true; }
  bool remove(const char* path) { return !fail_remove && files.erase(path) != 0; }
  void _lockFS() {}
  void _unlockFS() {}
  FakeFilesystem* _getFS() { return this; }
  File open(const char* path, const char* mode = "r", bool = false) {
    if (*mode == 'r') ++read_opens;
    if (fail_open == path && fail_open_remaining != 0) {
      --fail_open_remaining;
      return File();
    }
    if (*mode != 'r') {
      files[path].clear();
      return File(this, path, true);
    }
    return exists(path) ? File(this, path, false) : File();
  }
  bool rename(const char* from, const char* to) {
    ++renames;
    if (renames == fail_rename || files.count(from) == 0) return false;
#if defined(ESP32_PLATFORM)
    if (files.count(to) != 0) return false;
#endif
    files[to] = files.at(from);
    files.erase(from);
    snapshots.push_back(files);
    return true;
  }
} filesystem;

bool File::open(const char* name, uint8_t mode) {
  if (!fs) return false;
  *this = fs->open(name, mode == FILE_O_WRITE ? "w" : "r");
  return static_cast<bool>(*this);
}
size_t File::read(uint8_t* bytes, size_t length) {
  if (!fs || writing) return 0;
  const auto& data = fs->files.at(path);
  const size_t count = std::min({length, fs->max_read,
      data.size() - std::min(position, data.size())});
  if (count) memcpy(bytes, data.data() + position, count);
  position += count;
  return count;
}
size_t File::write(const uint8_t* bytes, size_t length) {
  if (!fs || !writing) return 0;
  ++fs->writes;
  const size_t count = std::min(length, fs->max_write);
  auto& data = fs->files[path];
  data.resize(position + count);
  if (count) memcpy(data.data() + position, bytes, count);
  position += count;
  fs->snapshots.push_back(fs->files);
  return count;
}
size_t File::size() const { return *this ? fs->files.at(path).size() : 0; }

#if defined(RP2040_PLATFORM)
#include <FS.h>
#define FILESYSTEM fs::FS
#else
#define FILESYSTEM FakeFilesystem
#endif
#include <helpers/ContactInfo.h>
#include <helpers/PersistentStoreFormat.h>
#include <helpers/ContactFileTransaction.h>
#include <helpers/FileRead.h>
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>

struct stat {};
int stat(const char* path, struct stat*) {
  assert(strncmp(path, "/spiffs", 7) == 0);
  errno = filesystem.stat_error;
  if (errno) return -1;
  if (filesystem.files.count(path + 7) == 0) { errno = ENOENT; return -1; }
  return 0;
}
struct lfs_info {};
int lfs_stat(FakeFilesystem* fs, const char* path, lfs_info*) {
  if (fs->stat_error) return -5;
  return fs->files.count(path) != 0 ? 0 : LFS_ERR_NOENT;
}

#if defined(STM32_PLATFORM)
static const char* identity_path = "/_main.id";
#else
static const char* identity_path = "/identity/_main.id";
#endif

struct Host {
  size_t capacity = 100;
  unsigned resets = 0;
  std::vector<ContactInfo> contacts;
  bool resetContactsForRecovery() { ++resets; contacts.clear(); return true; }
  bool getContactForSave(uint32_t index, ContactInfo& c) {
    if (index >= contacts.size()) return false;
    c = contacts[index];
    return true;
  }
  bool onContactLoaded(const ContactInfo& c) {
    if (contacts.size() >= capacity) return false;
    contacts.push_back(c);
    return true;
  }
};
using DataStoreHost = Host;

class DataStore {
  FakeFilesystem* _fs = &filesystem;
  bool _identity_creation_blocked = false;
  bool _channel_load_incomplete = false;
  bool _uncached_contact_load_incomplete = false;
#include "contact_recovery_state_under_test.h"
#if defined(ESP32_PLATFORM)
#include "contact_write_state_under_test.h"
#endif
  struct IdentityAdapter {
    bool recover(const char*) { return true; }
#if defined(ESP32_PLATFORM)
    IdentityLoadResult loadResult(const char* name, mesh::LocalIdentity& identity) {
      // The ESP32 entry point now delegates absence/error discrimination to
      // IdentityStore, not a separate exists() short circuit. This fixture's
      // adapter has no NVS; end-to-end migration cases use the real store.
      if (filesystem.stat_error) return IdentityLoadResult::Unreadable;
      if (!filesystem.files.count(identity_path)) return IdentityLoadResult::Missing;
      return load(name, identity) ? IdentityLoadResult::Loaded : IdentityLoadResult::Unreadable;
    }
#endif
    bool load(const char*, mesh::LocalIdentity&) {
      File file = filesystem.open(identity_path);
      uint8_t data[96];
      return file && file.read(data, sizeof(data)) == sizeof(data);
    }
    bool save(const char*, const mesh::LocalIdentity&) {
      ++filesystem.writes;
      return true;
    }
  } identity_store;
public:
#include "contact_recovery_api_under_test.h"
  void recoverContacts(DataStoreHost*);
#if defined(ESP32_PLATFORM)
  ~DataStore();
  bool markContactDirty(const ContactInfo&);
  bool releaseContact(const ContactInfo&);
  bool serviceContactWrites(DataStoreHost*, bool (*filter)(const ContactInfo&) = nullptr);
  bool flushContactWrites(DataStoreHost*, bool (*filter)(const ContactInfo&) = nullptr);
  bool hasPendingContactWrites() const;
#endif
  FakeFilesystem* _getContactsChannelsFS() { return _fs; }
  File openRead(FakeFilesystem* fs, const char* path) { return fs->open(path); }
  bool loadMainIdentity(mesh::LocalIdentity&);
  bool canCreateMainIdentity() const;
  bool saveMainIdentity(const mesh::LocalIdentity&);
  void loadContacts(DataStoreHost*);
  bool saveContacts(DataStoreHost*, bool (*filter)(const ContactInfo&) = nullptr);
  bool hasIncompleteContactLoad() const;
};
#include "store_under_test.h"

static ContactInfo contact(uint8_t value) {
  ContactInfo c{};
  memset(c.id.pub_key, value, sizeof(c.id.pub_key));
  memset(c.name, 0, sizeof(c.name));
  c.name[0] = 'A' + value;
  c.type = 1;
  c.out_path_len = OUT_PATH_UNKNOWN;
  uint8_t path[64] = {};
  assert(c.setRawPath(path));
  return c;
}

static void identity_checks() {
  filesystem = FakeFilesystem();
  DataStore fresh;
  mesh::LocalIdentity identity;
  assert(!fresh.loadMainIdentity(identity));
  assert(fresh.canCreateMainIdentity());
  assert(fresh.saveMainIdentity(identity));
  for (unsigned fault = 0; fault < 4; ++fault) {
    filesystem = FakeFilesystem();
    filesystem.files[identity_path] = std::vector<uint8_t>(96, 7);
    if (fault == 0) filesystem.fail_open = identity_path;
    if (fault == 1) filesystem.max_read = 32;
    if (fault == 2) filesystem.files[identity_path].resize(32);
    if (fault == 3) filesystem.stat_error = EIO;
#if defined(RP2040_PLATFORM)
    if (fault == 3) continue; // this backend exposes only bool stat/exists
#endif
    const auto original = filesystem.files;
    DataStore store;
    assert(!store.loadMainIdentity(identity));
    assert(!store.canCreateMainIdentity());
    assert(!store.saveMainIdentity(identity));
    assert(filesystem.writes == 0 && filesystem.files == original);
    filesystem.fail_open.clear();
    filesystem.max_read = std::numeric_limits<size_t>::max();
    filesystem.stat_error = 0;
    filesystem.files[identity_path].resize(96);
    DataStore rebooted;
    assert(rebooted.loadMainIdentity(identity));
    assert(rebooted.canCreateMainIdentity());
  }
}

static std::vector<uint8_t> make_original() {
  filesystem = FakeFilesystem();
  DataStore store;
  Host host;
  host.contacts = {contact(1), contact(2)};
  assert(store.saveContacts(&host));
  return filesystem.files.at("/contacts3");
}

static void contact_checks() {
  const auto original = make_original();
  {
    filesystem = FakeFilesystem();
    filesystem.files["/contacts3"] = original;
    DataStore store;
    Host empty;
    assert(store.saveContacts(&empty));
    assert(filesystem.files.at("/contacts3").empty());
    store.loadContacts(&empty);
    assert(!store.hasIncompleteContactLoad() && empty.contacts.empty());
  }
  Host replacement;
  replacement.contacts = {contact(3), contact(4), contact(5)};
  for (unsigned fault = 0; fault < 5; ++fault) {
    filesystem = FakeFilesystem();
    filesystem.files["/contacts3"] = original;
    if (fault == 0) filesystem.max_write = 2;
    if (fault == 1) filesystem.fail_open = "/contacts3.tmp";
    if (fault == 2) filesystem.max_read = 2;
    if (fault == 3) filesystem.fail_rename = 1;
    if (fault == 4) filesystem.fail_rename = 2;
#if defined(STM32_PLATFORM)
    if (fault == 4) continue; // LittleFS atomically replaces in one rename
#endif
    DataStore store;
    assert(!store.saveContacts(&replacement));
    assert(filesystem.files.at("/contacts3") == original);
  }

  filesystem = FakeFilesystem();
  filesystem.files["/contacts3"] = original;
  DataStore store;
  assert(store.saveContacts(&replacement));
  const auto updated = filesystem.files.at("/contacts3");
  assert(updated.size() == 3 * mesh::storage::CONTACT_RECORD_SIZE);
  // Reboot from every write/rename boundary of the actual transaction.
  const auto snapshots = filesystem.snapshots;
  for (const auto& snapshot : snapshots) {
    filesystem = FakeFilesystem();
    filesystem.files = snapshot;
    DataStore rebooted;
    Host loaded;
    rebooted.loadContacts(&loaded);
    assert(!rebooted.hasIncompleteContactLoad());
    assert(loaded.contacts.size() == 2 || loaded.contacts.size() == 3);
    assert(filesystem.files.at("/contacts3") == original
        || filesystem.files.at("/contacts3") == updated);
  }
  for (unsigned fault = 0; fault < 4; ++fault) {
    filesystem = FakeFilesystem();
    filesystem.files["/contacts3"] = original;
    if (fault == 0) filesystem.fail_open = "/contacts3";
    if (fault == 1) filesystem.max_read = 16;
    // No whole record; whole records before a partial one load instead.
    if (fault == 2) filesystem.files["/contacts3"].resize(mesh::storage::CONTACT_RECORD_SIZE - 1);
    DataStore rebooted;
    Host loaded;
    if (fault == 3) loaded.capacity = 1;
    rebooted.loadContacts(&loaded);
    assert(rebooted.hasIncompleteContactLoad());
    const auto unchanged = filesystem.files;
    assert(!rebooted.saveContacts(&loaded));
    assert(filesystem.files == unchanged);
  }
#if defined(ESP32_PLATFORM)
  filesystem = FakeFilesystem();
  filesystem.files["/contacts3.bak"] = original;
  filesystem.fail_open = "/contacts3.bak";
  DataStore recovering;
  Host unavailable;
  recovering.loadContacts(&unavailable);
  assert(recovering.hasIncompleteContactLoad());
  assert(!recovering.saveContacts(&unavailable));
  assert(filesystem.files.at("/contacts3.bak") == original);
#endif
  filesystem = FakeFilesystem();
  filesystem.files["/contacts3"] = original;
  DataStore filtered;
  assert(filtered.saveContacts(&replacement,
      [](const ContactInfo&) -> bool { return false; }));
  assert(filesystem.files.at("/contacts3").empty());
}

static void recovery_checks() {
  const auto original = make_original();
  for (unsigned fault = 0; fault < 3; ++fault) {
    filesystem = FakeFilesystem();
    filesystem.files["/contacts3"] = original;
    filesystem.files["/contacts3"].resize(mesh::storage::CONTACT_RECORD_SIZE - 1);
    filesystem.files[identity_path] = std::vector<uint8_t>(96, 7);
    filesystem.files["/channels2"] = {3, 4, 5};
    filesystem.files["/prefs"] = {6, 7};
    const auto identity = filesystem.files.at(identity_path);
    const auto channels = filesystem.files.at("/channels2");
    const auto prefs = filesystem.files.at("/prefs");
    if (fault == 1) filesystem.fail_remove = true;
    DataStore store;
    Host host;
    store.loadContacts(&host);
    assert(store.hasIncompleteContactLoad() && store.contactRecoveryPending());
    if (fault == 2) filesystem.files["/contacts3"] = original;
    store.recoverContacts(&host);
    assert(!store.hasIncompleteContactLoad() && !store.contactRecoveryPending());
    assert(host.resets >= 1 && store.contactsAreVolatile() == (fault == 1));
    assert(host.contacts.size() == (fault == 2 ? 2U : 0U));
    assert(filesystem.files.at(identity_path) == identity);
    assert(filesystem.files.at("/channels2") == channels);
    assert(filesystem.files.at("/prefs") == prefs);
    for (const auto& item : filesystem.files) assert(item.first.find(".bad") == std::string::npos);
    host.contacts.push_back(contact(5));
    const auto before = filesystem.files;
    const auto writes = filesystem.writes;
    assert(store.saveContacts(&host) == (fault != 1));
    if (fault == 1) assert(filesystem.files == before && filesystem.writes == writes);
  }
  {
    filesystem = FakeFilesystem();
    filesystem.files["/contacts3"] = original;
    DataStore store;
    Host host;
    host.capacity = 1;
    store.loadContacts(&host);
    assert(store.hasIncompleteContactLoad() && !store.contactRecoveryPending());
    store.recoverContacts(&host);
    assert(host.resets == 0 && host.contacts.size() == 1);
    assert(!store.contactsAreVolatile() && filesystem.files.at("/contacts3") == original);
  }
#if !defined(STM32_PLATFORM)
  for (bool blocked_promotion : {false, true}) {
    filesystem = FakeFilesystem();
    filesystem.files["/contacts3"] = {0xff};
    filesystem.files["/contacts3.bak"] = original;
    if (blocked_promotion) filesystem.fail_rename = 1;
    DataStore store;
    Host host;
    store.loadContacts(&host);
    assert(!store.hasIncompleteContactLoad() && host.contacts.size() == 2);
    if (blocked_promotion) assert(filesystem.files.at("/contacts3.bak") == original);
    else assert(filesystem.files.at("/contacts3") == original);
    host.contacts.push_back(contact(6));
    assert(store.saveContacts(&host));
    assert(!filesystem.files.count("/contacts3.bak"));
    assert(filesystem.files.at("/contacts3").size() == 3 * mesh::storage::CONTACT_RECORD_SIZE);
  }
#endif
}

int main() {
  identity_checks();
  contact_checks();
  recovery_checks();
}
