#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <limits>
#include <map>
#include <string>
#include <vector>
#include <Arduino.h>
enum { FILE_O_READ = 0, FILE_O_WRITE = 1 };

class FakeFilesystem;
class File {
  FakeFilesystem* _fs = nullptr;
  std::string _path;
  size_t _position = 0;
  bool _write = false;
public:
  File() = default;
  explicit File(FakeFilesystem& fs) : _fs(&fs) {}
  File(FakeFilesystem* fs, const char* path, bool write);
  bool open(const char* path, uint8_t mode);
  explicit operator bool() const { return _fs != nullptr; }
  size_t read(uint8_t* bytes, size_t length);
  size_t write(const uint8_t* bytes, size_t length);
  size_t size() const;
  bool seek(size_t position);
  void flush() {}
  void close() { _fs = nullptr; }
};

class FakeFilesystem {
  struct Config { uint32_t block_count = 256, block_size = 4096; } _config;
  struct Lfs { const Config* cfg; } _lfs{&_config};
public:
  using Files = std::map<std::string, std::vector<uint8_t>>;
  Files files;
  size_t capacity = 1024 * 1024;
  size_t max_write = std::numeric_limits<size_t>::max();
  std::string fail_read;
  unsigned fail_rename = 0, renames = 0, writes = 0, reads = 0, opens = 0;
  std::vector<Files> rename_snapshots;
  bool exists(const char* path) const { return files.count(path) != 0; }
  bool mkdir(const char*) { return true; }
  bool remove(const char* path) { return files.erase(path) != 0; }
  File open(const char* path, const char* mode = "r", bool = false) {
    ++opens;
    if (*mode != 'r') {
      files[path].clear();
      return File(this, path, true);
    }
    if (fail_read == path || !exists(path)) return File();
    return File(this, path, false);
  }
  bool rename(const char* from, const char* to) {
    ++renames;
    if (renames == fail_rename || !exists(from)) return false;
#if defined(ESP32_PLATFORM)
    if (exists(to)) return false;
#endif
    files[to] = files.at(from);
    files.erase(from);
    rename_snapshots.push_back(files);
    return true;
  }
  size_t totalBytes() const { return capacity; }
  void setBlockCount(uint32_t count) { _config.block_count = count; }
  Lfs* _getFS() { _lfs.cfg = &_config; return &_lfs; }
  size_t usedBytes() const {
    size_t total = 0;
    for (const auto& f : files) total += f.second.size();
    return total;
  }
} SPIFFS;

int _getLfsUsedBlockCount(FakeFilesystem* fs) {
  unsigned used = 2; // directory metadata pair
  for (const auto& file : fs->files) used += (file.second.size() + 4095) / 4096;
  return used;
}

File::File(FakeFilesystem* fs, const char* path, bool write)
    : _fs(fs), _path(path), _write(write) {}
bool File::open(const char* path, uint8_t mode) {
  if (!_fs) return false;
  *this = _fs->open(path, mode == FILE_O_WRITE ? "w" : "r");
  return static_cast<bool>(*this);
}
size_t File::read(uint8_t* bytes, size_t length) {
  if (!_fs || _write || _fs->fail_read == _path) return 0;
  ++_fs->reads;
  auto& data = _fs->files[_path];
  const size_t count = std::min(length, data.size() - std::min(data.size(), _position));
  if (count) memcpy(bytes, data.data() + _position, count);
  _position += count;
  return count;
}
size_t File::write(const uint8_t* bytes, size_t length) {
  if (!_fs || !_write) return 0;
  ++_fs->writes;
  const size_t count = std::min(length, _fs->max_write);
  auto& data = _fs->files[_path];
  data.resize(_position + count);
  memcpy(data.data() + _position, bytes, count);
  _position += count;
  return count;
}
size_t File::size() const { return _fs ? _fs->files.at(_path).size() : 0; }
bool File::seek(size_t pos) {
  if (!_fs || pos > size()) return false;
  _position = pos;
  return true;
}

#define FILESYSTEM FakeFilesystem
#include <helpers/ContactInfo.h>
#include <helpers/ContactSecretCache.h>
#include <helpers/ContactFileTransaction.h>
#if defined(NRF52_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#endif
#include "packet_under_test.h"
#define ADV_TYPE_NONE 0

struct Host {
  unsigned cache_flushes = 0;
  size_t capacity = 350;
  std::vector<ContactInfo> contacts;
  bool onContactLoaded(const ContactInfo& c) {
    if (contacts.size() >= capacity) return false;
    contacts.push_back(c);
    return true;
  }
  void onContactCacheFlushed() { ++cache_flushes; }
  ContactInfo* getContactForStore(uint32_t index) {
    return index < contacts.size() ? &contacts[index] : nullptr;
  }
};
using DataStoreHost = Host;

#if defined(NRF52_PLATFORM)
static const char* CONTACT_MIGRATION_MARKER = "/contacts4.mig";
bool contactPathPresence(FakeFilesystem* fs, const char* path, bool& present) {
  present = fs->exists(path);
  return true;
}
#else
bool companionPathPresence(FakeFilesystem* fs, const char* path, bool& present) {
  present = fs->exists(path);
  return true;
}
#endif

// The narrow hardware adapter below supplies the same filesystem/host seams.
// Serialization, save/eviction and secret I/O bodies are extracted unchanged
// from DataStore.cpp and compiled below, including their platform branches.
class DataStore : public mesh::ContactPathBackend, public mesh::ContactSecretBackend {
  DataStoreHost* _cache_host;
  bool _cache_load_incomplete = false;
#if defined(ESP32_PLATFORM)
  File _contact_path_reader;
#endif
  uint32_t _secret_retry_at = 0;
#if defined(NRF52_PLATFORM)
  uint32_t _contact_page_generations[14] = {};
  bool _dirty[14] = {};
  bool _contact_load_incomplete = false;
  bool _legacy_contacts_pending_cleanup = false, _legacy_migration_ready = false;
  uint16_t _legacy_contact_count = 0;
  mesh::storage::ContactSlotMap _contact_slots;
  mesh::storage::DirtyPageSet _dirty_contact_pages, _unread_contact_pages;
  void resetContactPageState() { _contact_slots.clear(); }
  bool prepareLegacyContactMigration() { _legacy_migration_ready = true; return true; }
#endif
public:
  explicit DataStore(Host& host) : _cache_host(&host) {
    mesh::contactPathStorage().attach(this);
  }
  FakeFilesystem* _getContactsChannelsFS() const { return &SPIFFS; }
  File openRead(FakeFilesystem* fs, const char* path) { return fs->open(path); }
  bool hasIncompleteContactLoad() const {
    return _cache_load_incomplete
#if defined(NRF52_PLATFORM)
        || _contact_load_incomplete || !_unread_contact_pages.empty()
#endif
        ;
  }
  void loadContacts(DataStoreHost*);
  bool saveContacts(DataStoreHost*, bool (*filter)(const ContactInfo&));
  bool flushContactWrites(DataStoreHost*, bool (*filter)(const ContactInfo&));
  bool readStoredPath(uint16_t, uint8_t[64]) override;
  bool flushCachedPaths() override;
  uint16_t secretSlot(const uint8_t[32]) const;
  bool readSavedSecret(const uint8_t[32], const uint8_t[32], uint8_t[32]) override;
  bool saveSecret(const uint8_t[32], const uint8_t[32], const uint8_t[32]) override;
#if defined(NRF52_PLATFORM)
  bool loadContactPages(DataStoreHost*, uint16_t, uint32_t);
  bool writeContactPage(DataStoreHost*, uint8_t, bool (*filter)(const ContactInfo&));
  bool markContactDirty(const ContactInfo& c) {
    if (hasIncompleteContactLoad() || c.storage_slot >= 350) return false;
    _dirty[c.storage_slot / 25] = true;
    return true;
  }
  bool hasPendingContactWrites() const {
    for (bool dirty : _dirty) if (dirty) return true;
    return false;
  }
  bool serviceContactWrites(DataStoreHost* host, bool (*filter)(const ContactInfo&)) {
    if (hasIncompleteContactLoad()) return false;
    for (uint8_t i = 0; i < 14; ++i) if (_dirty[i]) {
      if (!writeContactPage(host, i, filter)) return false;
      _dirty[i] = false;
      return true;
    }
    return true;
  }
#endif
};
#include "store_under_test.h"

// Exercise the real Mesh::sendDirect and all request/login callers with only
// the radio queue and datagram constructor replaced. A flash miss must release
// the packet and report failure to the app, then recover on a successful read.
namespace mesh {
class Mesh {
protected:
  struct Tables { void markSent(Packet*) {} } tables;
  Tables* _tables = &tables;
  void maybeScheduleDirectRetry(Packet*, uint8_t) {}
  uint8_t getTraceDirectPriority(Packet*) { return 0; }
  const char* getLogDateTime() { return "test"; }
public:
  Packet packet;
  unsigned released = 0, queued = 0;
  void releasePacket(Packet*) { ++released; }
  bool sendPacket(Packet*, uint8_t, uint32_t) { ++queued; return true; }
  bool sendDirect(Packet*, const uint8_t*, uint8_t, uint32_t = 0);
};
}

#define MSG_SEND_FAILED 0
#define MSG_SEND_SENT_FLOOD 1
#define MSG_SEND_SENT_DIRECT 2
#define ADV_TYPE_ROOM 3
class BaseChatMesh : public mesh::Mesh {
  uint32_t getTransmitAirtime(const mesh::Packet* packet) const {
    return _radio->getEstAirtimeFor(packet->getRawLength());
  }
  struct Radio { uint32_t getEstAirtimeFor(int) { return 20; } } radio;
  struct Clock { uint32_t getCurrentTimeUnique() { return 100; } } clock;
  struct Rng { void random(uint8_t* data, size_t size) { memset(data, 42, size); } } rng;
  Radio* _radio = &radio;
  mesh::LocalIdentity self_id;
  Clock* getRTCClock() { return &clock; }
  Rng* getRNG() { return &rng; }
  mesh::Packet* createDatagram(uint8_t type, const mesh::Identity&,
      const uint8_t*, const uint8_t*, size_t length) {
    packet = mesh::Packet();
    packet.header = type << PH_TYPE_SHIFT;
    packet.payload_len = length;
    return &packet;
  }
  mesh::Packet* createAnonDatagram(uint8_t type, const mesh::LocalIdentity&,
      const mesh::Identity& peer, const uint8_t* secret, const uint8_t* data, size_t length) {
    return createDatagram(type, peer, secret, data, length);
  }
  bool sendFloodScoped(const ContactInfo&, mesh::Packet* p) { return sendPacket(p, 0, 0); }
  uint32_t calcFloodTimeoutMillisFor(uint32_t t) { return t * 2; }
  uint32_t calcDirectTimeoutMillisFor(uint32_t t, uint8_t n) { return t * (n + 1); }
public:
  int sendLogin(const ContactInfo&, const char*, uint32_t&);
  int sendAnonReq(const ContactInfo&, const uint8_t*, uint8_t, uint32_t&, uint32_t&);
  int sendRequest(const ContactInfo&, const uint8_t*, uint8_t, uint32_t&, uint32_t&);
  int sendRequest(const ContactInfo&, uint8_t, uint32_t&, uint32_t&);
};
#include "send_under_test.h"

using Path = std::array<uint8_t, 64>;
Path route(unsigned index) {
  Path path;
  for (size_t i = 0; i < path.size(); ++i) path[i] = index * 19 + i + (index >> 8);
  return path;
}

struct Fixture {
  Host host;
  DataStore store{host};
  explicit Fixture(size_t count = 350) {
    SPIFFS = FakeFilesystem();
    std::vector<uint8_t> records(count * mesh::storage::CONTACT_RECORD_SIZE, 0);
    host.contacts.reserve(count);
    for (size_t i = 0; i < count; ++i) {
      uint8_t* r = records.data() + i * mesh::storage::CONTACT_RECORD_SIZE;
      memset(r, 0x21, 32); // identical prefixes exercise full peer-key matching
      r[30] = i >> 8;
      r[31] = i;
      snprintf(reinterpret_cast<char*>(r + 32), 32, "Contact %u", unsigned(i));
      r[64] = 1;
      r[71] = 63;
      const Path path = route(i);
      memcpy(r + 76, path.data(), path.size());
    }
    SPIFFS.files["/contacts3"] = records;
    for (size_t i = 0; i < count; ++i) {
      ContactInfo c;
      assert(deserializeContactRecord(records.data() + i * 152, c, i));
#if defined(NRF52_PLATFORM)
      c.storage_slot = i;
#endif
      host.contacts.push_back(c);
    }
  }
  void check(unsigned index, const Path& expected) {
    const uint8_t* actual = host.contacts[index].getPath();
    assert(actual && memcmp(actual, expected.data(), 64) == 0);
  }
};

static void routes_survive_eviction_and_full_sync() {
  Fixture f;
  assert(sizeof(ContactInfo) <= 100); // all 350 records retain compact handles
  const unsigned reads = SPIFFS.reads;
  f.check(0, route(0));
  assert(SPIFFS.reads == reads + 1);
  f.check(0, route(0));
  assert(SPIFFS.reads == reads + 1); // hot route consumes no flash I/O
  for (unsigned i = 1; i < 350; ++i) f.check(i, route(i));
  f.check(0, route(0)); // actual LRU miss after 350 different contacts
  const auto original = SPIFFS.files.at("/contacts3");
  const unsigned opens = SPIFFS.opens;
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
  assert(SPIFFS.opens - opens == 2); // reuse source; open writer and verifier
  assert(SPIFFS.files.at("/contacts3") == original);
  for (unsigned i = 0; i < 350; ++i) f.check(i, route(i));
}

static void snapshot_rollback_and_dirty_eviction() {
  Fixture f;
  ContactInfo pending_response = f.host.contacts[0];
  assert(f.host.contacts[0].setRawPath(route(900).data()));
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
  Path old;
  assert(pending_response.copyPathTo(old.data()) && old == route(0));
  for (unsigned i = 1; i < 80; ++i) {
    assert(f.host.contacts[i].setRawPath(route(900 + i).data()));
  }
  assert(SPIFFS.writes > 350); // full cache forced safe dirty writeback
  for (unsigned i = 0; i < 80; ++i) f.check(i, route(900 + i));
  assert(pending_response.copyPathTo(old.data()) && old == route(0));
  f.host.contacts[0] = pending_response; // rollback after the old file changed
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
  f.check(0, route(0));
  // Deletion compacts records; every remaining handle follows its new offset.
  f.host.contacts.erase(f.host.contacts.begin() + 20);
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
  for (unsigned i = 20; i < 79; ++i) f.check(i, route(901 + i));
  for (unsigned i = 79; i < 349; ++i) f.check(i, route(i + 1));
}

static void storage_failures_preserve_routes() {
  Fixture f;
  const auto original = SPIFFS.files.at("/contacts3");
  ContactInfo previous = f.host.contacts[0];
  assert(f.host.contacts[0].setRawPath(route(810).data()));
  SPIFFS.max_write = 13;
  assert(!f.store.saveContacts(&f.host, cachedContactFilter));
  assert(SPIFFS.files.at("/contacts3") == original);
  f.check(0, route(810));
  f.host.contacts[0] = previous;
  f.check(0, route(0));
  SPIFFS.max_write = std::numeric_limits<size_t>::max();
  for (unsigned fail : {1u, 2u}) {
    SPIFFS.renames = 0;
    SPIFFS.fail_rename = fail;
    assert(!f.store.saveContacts(&f.host, cachedContactFilter));
    assert(SPIFFS.files.at("/contacts3") == original);
  }
  SPIFFS.fail_rename = 0;
  SPIFFS.fail_read = "/contacts3";
  assert(!f.host.contacts[100].getPath());
  assert(!f.store.saveContacts(&f.host, cachedContactFilter));
  assert(SPIFFS.files.at("/contacts3") == original);
  SPIFFS.fail_read.clear();
  SPIFFS.files["/contacts3"][100 * 152 + 76] ^= 1;
  assert(!f.host.contacts[100].getPath()); // CRC protects a cold route after boot
  assert(!f.store.saveContacts(&f.host, cachedContactFilter));
  SPIFFS.files["/contacts3"] = original;
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
}

#if defined(ESP32_PLATFORM)
static void rename_gaps_recover_a_complete_contact_file() {
  Fixture f(30);
  auto old = SPIFFS.files.at("/contacts3");
  assert(f.host.contacts[0].setRawPath(route(800).data()));
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
  auto updated = SPIFFS.files.at("/contacts3");
  const auto snapshots = SPIFFS.rename_snapshots;
  assert(snapshots.size() == 2);
  for (const auto& snapshot : snapshots) {
    FakeFilesystem rebooted;
    rebooted.files = snapshot;
    assert(mesh::ContactFileTransaction::recover(&rebooted, "/contacts3"));
    const auto& recovered = rebooted.files.at("/contacts3");
    assert(recovered == old || recovered == updated);
  }
}
#endif

static void pinned_snapshots_bound_ram_without_losing_updates() {
  Fixture f;
  std::vector<ContactInfo> snapshots;
  snapshots.reserve(16);
  for (unsigned i = 0; i < 16; ++i) {
    snapshots.push_back(f.host.contacts[i]);
    assert(snapshots.back().setRawPath(route(1000 + i).data()));
  }
  const ContactInfo previous = f.host.contacts[100];
  assert(!f.host.contacts[100].setRawPath(route(1500).data()));
  Path path;
  assert(f.host.contacts[100].copyPathTo(path.data()) && path == route(100));
  snapshots.clear();
  assert(f.host.contacts[100].setRawPath(route(1500).data()));
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
  assert(previous.copyPathTo(path.data()) && path == route(100));
}

static void encoded_paths_and_copy_assignment() {
  Fixture f(40);
  auto& c = f.host.contacts[0];
  const Path raw = route(700);
  for (uint8_t len : {uint8_t(63), uint8_t(0x60), uint8_t(0x95)}) {
    assert(c.setPath(raw.data(), len));
    assert(c.out_path_len == len);
    const auto* p = c.getPath();
    const unsigned bytes = (len & 63) * ((len >> 6) + 1);
    assert(p && memcmp(p, raw.data(), bytes) == 0);
  }
  const ContactInfo old = c;
  assert(!c.setPath(raw.data(), 0x61)); // 33 two-byte hashes exceed 64 bytes
  assert(!c.setPath(raw.data(), 0xc1)); // reserved hash encoding
  assert(c.setPath(nullptr, 0));
  assert(c.out_path_len == 0);
  assert(c.setPath(nullptr, OUT_PATH_UNKNOWN));
  assert(c.out_path_len == OUT_PATH_UNKNOWN);
  c = old;
  assert(c.out_path_len == old.out_path_len);
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
}

static void failed_path_reads_fail_sends_without_leaking_packets() {
  Fixture f(30);
  mesh::contactSecretCache().attach(&f.store);
  BaseChatMesh mesh;
  const auto& recipient = f.host.contacts[29];
  uint8_t request[] = {1};
  uint32_t tag, timeout;
  for (bool fail : {true, false}) {
    SPIFFS.fail_read = fail ? "/contacts3" : "";
    const int expected = fail ? MSG_SEND_FAILED : MSG_SEND_SENT_DIRECT;
    assert(mesh.sendLogin(recipient, "test", timeout) == expected);
    assert(mesh.sendAnonReq(recipient, request, 1, tag, timeout) == expected);
    assert(mesh.sendRequest(recipient, request, 1, tag, timeout) == expected);
    assert(mesh.sendRequest(recipient, 1, tag, timeout) == expected);
    assert(mesh.released == 4);
    assert(mesh.queued == (fail ? 0u : 4u));
    if (!fail) assert(memcmp(mesh.packet.path, route(29).data(), 63) == 0);
  }
}

static void contact_reload_preserves_disk_on_cache_exhaustion() {
  Fixture f;
#if defined(NRF52_PLATFORM)
  for (unsigned page = 0; page < 14; ++page)
    assert(f.store.writeContactPage(&f.host, page, cachedContactFilter));
  SPIFFS.remove("/contacts3");
#endif
  f.host.contacts.clear();
  f.store.loadContacts(&f.host);
  assert(!f.store.hasIncompleteContactLoad() && f.host.contacts.size() == 350);
  for (unsigned i = 0; i < 350; ++i) f.check(i, route(i));
  f.host.contacts.clear();
  const auto durable = SPIFFS.files;
  const unsigned writes = SPIFFS.writes;

  // Occupy every handle with independent pending values. A failed load must
  // not turn a valid persisted record into an empty/invalid contact repair.
  std::vector<mesh::ContactPathRef> pending(390);
  for (auto& path : pending) assert(path.bind(0, route(0).data()));
  f.store.loadContacts(&f.host);
  assert(f.store.hasIncompleteContactLoad());
  assert(f.host.contacts.empty());
  assert(!f.store.saveContacts(&f.host, cachedContactFilter));
  assert(SPIFFS.files == durable && SPIFFS.writes == writes);
  pending.clear();
  f.store.loadContacts(&f.host);
  assert(f.host.contacts.empty()); // latched until a fresh boot/store
  DataStore rebooted(f.host);
  rebooted.loadContacts(&f.host);
  assert(!rebooted.hasIncompleteContactLoad() && f.host.contacts.size() == 350);
  for (unsigned i = 0; i < 350; ++i) f.check(i, route(i));
}

#if defined(ESP32_PLATFORM)
static void partial_loads_cannot_replace_the_complete_file() {
  for (bool capacity_failure : {false, true}) {
    Fixture f(40);
    f.host.contacts.clear();
    const auto durable = SPIFFS.files;
    if (capacity_failure) f.host.capacity = 20;
    else SPIFFS.fail_read = "/contacts3";
    f.store.loadContacts(&f.host);
    assert(f.store.hasIncompleteContactLoad());
    assert(!f.store.saveContacts(&f.host, cachedContactFilter));
    assert(SPIFFS.files == durable);
  }
}
#endif

static void secrets_load_from_flash_instead_of_recalculating() {
  Fixture f;
  mesh::LocalIdentity self;
  self.private_key[0] = 42;
  self.pub_key[0] = 23;
  mesh::ContactSecretCache cache;
  cache.attach(&f.store);
  std::array<uint8_t, 32> first;
  memcpy(first.data(), cache.get(self, f.host.contacts[0].id.pub_key), 32);
  assert(self.derivations == 1);
  const unsigned writes = SPIFFS.writes;
  assert(memcmp(first.data(), cache.get(self, f.host.contacts[0].id.pub_key), 32) == 0);
  assert(self.derivations == 1 && SPIFFS.writes == writes);
  for (unsigned i = 1; i <= 20; ++i) cache.get(self, f.host.contacts[i].id.pub_key);
  const unsigned before_miss = self.derivations, before_writes = SPIFFS.writes;
  assert(memcmp(first.data(), cache.get(self, f.host.contacts[0].id.pub_key), 32) == 0);
  assert(self.derivations == before_miss && SPIFFS.writes == before_writes);
  assert(cache.flash_hits == 1);
  // Reboot loses the RAM cache; the persisted identity-bound key is reusable.
  mesh::ContactSecretCache rebooted;
  rebooted.attach(&f.store);
  assert(memcmp(first.data(), rebooted.get(self, f.host.contacts[0].id.pub_key), 32) == 0);
  assert(self.derivations == before_miss);
  self.private_key[1] ^= 2; // also invalidate when public bytes did not change
  assert(memcmp(first.data(), rebooted.get(self, f.host.contacts[0].id.pub_key), 32) != 0);
  assert(self.derivations == before_miss + 1);
  self.pub_key[3] ^= 4;
  rebooted.get(self, f.host.contacts[0].id.pub_key);
  assert(self.derivations == before_miss + 2);
}

static void persisted_secret_matches_the_real_peer_key_exchange() {
  Fixture f(30);
  mesh::LocalIdentity self, peer;
  uint8_t seed[32] = {1};
  ed25519_create_keypair(self.pub_key, self.private_key, seed);
  seed[0] = 2;
  ed25519_create_keypair(peer.pub_key, peer.private_key, seed);
  f.host.contacts[0].id = peer;
  uint8_t reciprocal[32];
  peer.calcSharedSecret(reciprocal, self.pub_key);
  mesh::ContactSecretCache cache;
  cache.attach(&f.store);
  assert(memcmp(reciprocal, cache.get(self, peer.pub_key), 32) == 0);
  for (unsigned i = 1; i < 30; ++i) cache.get(self, f.host.contacts[i].id.pub_key);
  const unsigned derivations = self.derivations;
  assert(memcmp(reciprocal, cache.get(self, peer.pub_key), 32) == 0);
  assert(self.derivations == derivations && cache.flash_hits == 1);
}

static void secret_corruption_reordering_and_full_storage() {
  Fixture f(40);
  mesh::LocalIdentity self;
  mesh::ContactSecretCache cache;
  cache.attach(&f.store);
  std::array<uint8_t, 32> first;
  memcpy(first.data(), cache.get(self, f.host.contacts[0].id.pub_key), 32);
  SPIFFS.files["/csecret000"][36 + 32] ^= 4;
  mesh::ContactSecretCache cold;
  cold.attach(&f.store);
  assert(memcmp(first.data(), cold.get(self, f.host.contacts[0].id.pub_key), 32) == 0);
  assert(self.derivations == 2); // corrupted key was recomputed, never used
  std::swap(f.host.contacts[0], f.host.contacts[1]);
  mesh::ContactSecretCache moved;
  moved.attach(&f.store);
  assert(memcmp(first.data(), moved.get(self, f.host.contacts[0].id.pub_key), 32) != 0);
  assert(self.derivations == 3); // same slot, different full peer key
  SPIFFS.capacity = SPIFFS.usedBytes() + 10000;
  const unsigned writes = SPIFFS.writes;
  moved.get(self, f.host.contacts[30].id.pub_key);
  assert(self.derivations == 4 && SPIFFS.writes == writes);
  moved.get(self, f.host.contacts[30].id.pub_key);
  assert(self.derivations == 4); // still cached in RAM when flash has no reserve
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
}

#if defined(NRF52_PLATFORM)
static void nrf52_secret_cache_leaves_contact_storage_reserve() {
  Fixture f;
  for (unsigned page = 0; page < 14; ++page)
    assert(f.store.writeContactPage(&f.host, page, cachedContactFilter));
  SPIFFS.remove("/contacts3");
  const auto contacts = SPIFFS.files;
  SPIFFS.setBlockCount(25); // 100 KiB ExtraFS
  mesh::LocalIdentity self;
  mesh::ContactSecretCache cache;
  cache.attach(&f.store);
  for (unsigned i = 0; i < 350; i += 56) cache.get(self, f.host.contacts[i].id.pub_key);
  assert(cache.save_failures > 0); // low space remains a best-effort cache
  assert(_getLfsUsedBlockCount(&SPIFFS) <= 21); // keep at least four blocks
  for (const auto& file : contacts) assert(SPIFFS.files.at(file.first) == file.second);
  assert(f.host.contacts[0].setRawPath(route(999).data()));
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
  f.check(0, route(999));
}

static void nrf52_migration_snapshots_and_failed_page_replacement() {
  Fixture f;
  ContactInfo snapshot = f.host.contacts[349];
  assert(f.host.contacts[349].setRawPath(route(950).data()));
  // Commit one page at a time from the legacy tail, exactly as migration does.
  for (int page = 13; page >= 0; --page) {
    assert(f.store.writeContactPage(&f.host, page, cachedContactFilter));
    SPIFFS.files["/contacts3"].resize(page * 25 * 152);
    for (unsigned i = page * 25; i < 350; ++i)
      f.check(i, route(i == 349 ? 950 : i));
  }
  SPIFFS.remove("/contacts3");
  Path path;
  assert(snapshot.copyPathTo(path.data()) && path == route(349));
  const auto original = SPIFFS.files.at("/contacts4_00");
  assert(f.host.contacts[0].setRawPath(route(990).data()));
  SPIFFS.max_write = 13;
  assert(!f.store.writeContactPage(&f.host, 0, cachedContactFilter));
  assert(SPIFFS.files.at("/contacts4_00") == original);
  f.check(0, route(990));
  SPIFFS.max_write = std::numeric_limits<size_t>::max();
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
  f.check(0, route(990));
  assert(snapshot.copyPathTo(path.data()) && path == route(349));
  for (unsigned i = 1; i < 350; ++i) f.check(i, route(i == 349 ? 950 : i));
  SPIFFS.files["/contacts4_00"][20 + 3 * 152 + 76] ^= 8;
  assert(!f.host.contacts[3].getPath());
  assert(!f.store.writeContactPage(&f.host, 0, cachedContactFilter));
}
#endif

static void contact_tx_policy_round_trips_without_growing_records() {
  Fixture f(1);
  auto& contact = f.host.contacts[0];
  assert(contact.tx_radio == mesh::RADIO_TX_AUTO);
  for (uint8_t policy = mesh::RADIO_TX_AUTO; policy <= mesh::RADIO_TX_OFF; ++policy) {
    contact.tx_radio = policy;
    contact.shared_secret_valid = true;
    uint8_t record[mesh::storage::CONTACT_RECORD_SIZE];
    assert(serializeContactRecord(contact, record));
    assert(sizeof(record) == 152 && record[66] == mesh::encodeRadioTxPolicy(policy));
    ContactInfo restored;
    assert(deserializeContactRecord(record, restored, 0));
    assert(restored.tx_radio == policy && !restored.shared_secret_valid);
    assert(contact.shared_secret_valid && contact.tx_radio == policy);
  }
}

int main() {
  contact_tx_policy_round_trips_without_growing_records();
#if defined(ESP32_PLATFORM)
  routes_survive_eviction_and_full_sync();
  snapshot_rollback_and_dirty_eviction();
  storage_failures_preserve_routes();
  rename_gaps_recover_a_complete_contact_file();
  pinned_snapshots_bound_ram_without_losing_updates();
  encoded_paths_and_copy_assignment();
  failed_path_reads_fail_sends_without_leaking_packets();
  contact_reload_preserves_disk_on_cache_exhaustion();
  partial_loads_cannot_replace_the_complete_file();
  secrets_load_from_flash_instead_of_recalculating();
  persisted_secret_matches_the_real_peer_key_exchange();
  secret_corruption_reordering_and_full_storage();
#else
  nrf52_secret_cache_leaves_contact_storage_reserve();
  nrf52_migration_snapshots_and_failed_page_replacement();
  encoded_paths_and_copy_assignment();
  failed_path_reads_fail_sends_without_leaking_packets();
  contact_reload_preserves_disk_on_cache_exhaustion();
  secrets_load_from_flash_instead_of_recalculating();
  persisted_secret_matches_the_real_peer_key_exchange();
#endif
  return 0;
}
