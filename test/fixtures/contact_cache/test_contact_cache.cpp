#include "FakeFilesystem.h"

#define FILESYSTEM FakeFilesystem
#include <helpers/ContactInfo.h>
#include <helpers/ContactSecretCache.h>
#include <helpers/ContactFileTransaction.h>
#if !defined(NRF52_PLATFORM)
#include <helpers/FileRead.h>
#endif
#if defined(NRF52_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#endif
#include "packet_under_test.h"
#define ADV_TYPE_NONE 0
#if defined(NRF52_PLATFORM)
static bool fail_contact_page_allocation = false;
static void* allocateContactPagePayload(size_t size) {
  return fail_contact_page_allocation ? nullptr : malloc(size);
}
#endif

struct Host {
  unsigned cache_flushes = 0;
  unsigned recovery_resets = 0;
  bool recovery_reset_allowed = true;
  unsigned long dirty_deadline = 0;
  uint8_t dirty_failures = 0;
  size_t capacity = 350;
  std::vector<ContactInfo> contacts;
  bool onContactLoaded(const ContactInfo& c) {
    if (contacts.size() >= capacity) return false;
    contacts.push_back(c);
    return true;
  }
  void onContactCacheFlushed() { ++cache_flushes; }
  bool resetContactsForRecovery() {
    if (!recovery_reset_allowed) return false;
    ++recovery_resets;
    contacts.clear();
    dirty_deadline = 0;
    dirty_failures = 0;
    return true;
  }
  ContactInfo* getContactForStore(uint32_t index) {
    return index < contacts.size() ? &contacts[index] : nullptr;
  }
};
using DataStoreHost = Host;

#if defined(NRF52_PLATFORM)
static const char* CONTACT_MIGRATION_MARKER = "/contacts4.mig";
static bool fail_contact_migration_marker = false;
static bool fail_contact_legacy_stat = false;
static int fail_contact_page_stats = 0;
bool contactPathPresence(FakeFilesystem* fs, const char* path, bool& present) {
  if (fail_contact_page_stats && !strcmp(path, "/contacts4_00")) {
    if (fail_contact_page_stats > 0) --fail_contact_page_stats;
    return false;
  }
  if (fail_contact_legacy_stat && !strcmp(path, "/contacts3")) return false;
  if (fail_contact_migration_marker && !strcmp(path, CONTACT_MIGRATION_MARKER)) return false;
  present = fs->exists(path);
  return true;
}
#else
bool companionPathPresence(FakeFilesystem* fs, const char* path, bool& present) {
  if (fs->metadata_error) return false;
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
#include "contact_recovery_state_under_test.h"
#if defined(ESP32_PLATFORM)
  // Advert queue lifecycle has a dedicated production fixture. The contact
  // reinitialization test needs only this unrelated hardware lifecycle seam.
  struct { void clear() {} } _advert_write;
  File _contact_path_reader;
  bool _channel_load_incomplete = false;
#include "contact_write_state_under_test.h"
  FakeFilesystem* _fs = &SPIFFS;
  FakeFilesystem* _fsExtra = nullptr;
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
  void resetContactPageState(bool clear_incomplete = false);
  bool prepareLegacyContactMigration() { _legacy_migration_ready = true; return true; }
#endif
public:
  explicit DataStore(Host& host) : _cache_host(&host) {
    mesh::contactPathStorage().attach(this);
  }
  ~DataStore();
  FakeFilesystem* _getContactsChannelsFS() const {
#if defined(ESP32_PLATFORM)
    return _fsExtra != nullptr ? _fsExtra : _fs;
#else
    return &SPIFFS;
#endif
  }
  File openRead(FakeFilesystem* fs, const char* path) { return fs->open(path); }
  bool hasIncompleteContactLoad() const;
  void loadContacts(DataStoreHost*);
  void recoverContacts(DataStoreHost*);
#include "contact_recovery_api_under_test.h"
  bool rawRecoveryOnly() const {
    return (_contact_recovery & ContactRecoveryRawOnly) != 0;
  }
  void requireStrongRecovery(bool legacy = false) { queueContactRecovery(legacy); }
  void requireRawReadRecovery(bool legacy = false) { queueContactReadRecovery(legacy); }
  bool saveContacts(DataStoreHost*, bool (*filter)(const ContactInfo&));
  bool flushContactWrites(DataStoreHost*, bool (*filter)(const ContactInfo&));
  bool readStoredPath(uint16_t, uint8_t[64]) override;
  bool flushCachedPaths() override;
#if defined(ESP32_PLATFORM)
  void begin();
  void disableSecondaryFS(bool);
  void useSecondary(FakeFilesystem* fs) { _fsExtra = fs; }
  bool cancelCooperativeWrite() override;
#endif
  uint16_t secretSlot(const uint8_t[32]) const;
  bool readSavedSecret(const uint8_t[32], const uint8_t[32], uint8_t[32]) override;
  bool saveSecret(const uint8_t[32], const uint8_t[32], const uint8_t[32]) override;
#if defined(ESP32_PLATFORM)
  bool markContactDirty(const ContactInfo&);
  bool releaseContact(const ContactInfo&);
  bool hasPendingContactWrites() const;
  bool serviceContactWrites(DataStoreHost*, bool (*filter)(const ContactInfo&));
  bool starting() const {
    return _contact_write != nullptr && !static_cast<bool>(*_contact_write);
  }
  bool activeJob() const { return _contact_write != nullptr; }
  const void* jobAddress() const { return _contact_write; }
  uint32_t jobIndex() const { return _contact_write_index; }
  uint32_t writeRevision() const { return _contact_write_revision; }
  bool writeRequested() const { return _contact_write_requested; }
  bool verifying() const { return _contact_write_verifying; }
  bool readyToPublish() const {
    return _contact_write && _contact_write->readyToPublish();
  }
#endif
#if defined(NRF52_PLATFORM)
  uint32_t pageGeneration(uint8_t page) const { return _contact_page_generations[page]; }
  uint32_t unreadPages() const { return _unread_contact_pages.bits(); }
  uint32_t dirtyPages() const { return _dirty_contact_pages.bits(); }
  void markDirtyPage(uint8_t page) { assert(_dirty_contact_pages.mark(page)); }
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
  unsigned released = 0, queued = 0, queue_attempts = 0;
  bool queue_accepts = true;
  void releasePacket(Packet*) { ++released; }
  bool sendPacket(Packet* packet, uint8_t, uint32_t) {
    ++queue_attempts;
    if (!queue_accepts) { releasePacket(packet); return false; }
    ++queued;
    return true;
  }
  bool sendDirect(Packet*, const uint8_t*, uint8_t, uint32_t = 0);
};
}

#define MSG_SEND_FAILED 0
#define MSG_SEND_SENT_FLOOD 1
#define MSG_SEND_SENT_DIRECT 2
#define ADV_TYPE_ROOM 3
class BaseChatMesh : public mesh::Mesh {
  uint32_t getTransmitAirtime(const mesh::Packet* packet) const {
    ++airtime_queries;
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
      const uint8_t* secret, const uint8_t* data, size_t length) {
    ++allocations;
    payload.assign(data, data + length);
    memcpy(shared_secret.data(), secret, shared_secret.size());
    if (!allocation_succeeds) return nullptr;
    packet = mesh::Packet();
    packet.header = type << PH_TYPE_SHIFT;
    packet.payload_len = length;
    assert(length <= sizeof(packet.payload));
    memcpy(packet.payload, data, length);
    return &packet;
  }
  mesh::Packet* createAnonDatagram(uint8_t type, const mesh::LocalIdentity&,
      const mesh::Identity& peer, const uint8_t* secret, const uint8_t* data, size_t length) {
    return createDatagram(type, peer, secret, data, length);
  }
  bool sendFloodScoped(const ContactInfo&, mesh::Packet* p) {
    ++flood_calls;
    return sendPacket(p, 0, 0);
  }
  uint32_t calcFloodTimeoutMillisFor(uint32_t t) { return t * 2; }
  uint32_t calcDirectTimeoutMillisFor(uint32_t t, uint8_t n) { return t * (n + 1); }
  int sendContactPacket(const ContactInfo&, mesh::Packet*, uint32_t&);
public:
  unsigned allocations = 0, tag_checks = 0, flood_calls = 0;
  mutable unsigned airtime_queries = 0;
  bool allocation_succeeds = true, tag_allowed = true;
  std::vector<uint8_t> payload;
  std::array<uint8_t, 32> shared_secret = {};
  int sendLogin(const ContactInfo&, const char*, uint32_t&);
  int sendAnonReq(const ContactInfo&, const uint8_t*, uint8_t, uint32_t&, uint32_t&);
  virtual bool allowRequestTag(uint32_t) { ++tag_checks; return tag_allowed; }
  virtual bool allocateRequestTag(uint32_t&);
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

static void raw_read_recovery_preserves_accepted_ram_state() {
  for (bool paged : {false, true}) {
#if !defined(NRF52_PLATFORM)
    if (paged) continue;
#endif
    Fixture f(30);
    const char* source = "/contacts3";
#if defined(NRF52_PLATFORM)
    if (paged) {
      assert(f.store.writeContactPage(&f.host, 0, cachedContactFilter));
      assert(f.store.writeContactPage(&f.host, 1, cachedContactFilter));
      assert(SPIFFS.remove("/contacts3"));
      source = "/contacts4_00";
    }
#endif
    // These app-accepted changes are newer than disk, including a new peer.
    snprintf(f.host.contacts[0].name, sizeof(f.host.contacts[0].name), "accepted rename");
    f.host.contacts[0].lastmod = 0x12345678;
    assert(f.host.contacts[1].setRawPath(route(2401).data()));
    assert(f.store.markContactDirty(f.host.contacts[0]));
    assert(f.store.markContactDirty(f.host.contacts[1]));
    {
      ContactInfo added = f.host.contacts.back();
      added.id.pub_key[30] = 0xAA;
      added.id.pub_key[31] = 0xBB;
      snprintf(added.name, sizeof(added.name), "accepted new peer");
      assert(added.setRawPath(route(2430).data()));
#if defined(NRF52_PLATFORM)
      added.storage_slot = 30;
#endif
      f.host.contacts.push_back(added);
    }
    assert(f.store.markContactDirty(f.host.contacts.back()));
    f.host.dirty_deadline = 0x76543210;
    f.host.dirty_failures = 3;
#if defined(NRF52_PLATFORM)
    f.store.markDirtyPage(0);
    f.store.markDirtyPage(1);
    const uint32_t dirty_pages = f.store.dirtyPages();
    const uint32_t generation = f.store.pageGeneration(0);
#else
    for (unsigned step = 0; step < 12; ++step)
      assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
    assert(f.store.activeJob() && !f.store.verifying());
    const void* job = f.store.jobAddress();
    const uint32_t job_index = f.store.jobIndex();
    const uint32_t revision = f.store.writeRevision();
    const bool requested = f.store.writeRequested();
#endif
    std::vector<std::array<uint8_t, 152>> accepted(f.host.contacts.size());
    std::vector<uint16_t> handles;
    for (unsigned i = 0; i < f.host.contacts.size(); ++i) {
      assert(serializeContactRecord(f.host.contacts[i], accepted[i].data()));
      handles.push_back(f.host.contacts[i].path_ref.handle());
    }
    // Exhaust the remaining handle budget: the retry must use nonallocating,
    // checksum-checked path reads, not a replacement/reload of ContactInfo.
    std::vector<mesh::ContactPathRef> pressure(359);
    for (auto& path : pressure) assert(path.bind(0, route(0).data()));
    mesh::ContactPathRef no_room;
    assert(!no_room.bind(0, route(0).data()));
    const auto durable = SPIFFS.files;
    const unsigned writes = SPIFFS.writes, removes = SPIFFS.removes;
    const unsigned renames = SPIFFS.renames, flushes = f.host.cache_flushes;
    Path path;
    SPIFFS.fail_read = source;
    assert(!f.host.contacts[20].copyPathTo(path.data()));
    assert(f.store.contactRecoveryPending() && f.store.rawRecoveryOnly());
#if defined(NRF52_PLATFORM)
    assert(f.store.unreadPages() == (paged ? 1u : 0u));
#endif
    SPIFFS.fail_read.clear();
    f.store.recoverContacts(&f.host);
    assert(!f.store.contactRecoveryPending() && !f.store.hasIncompleteContactLoad());
    assert(f.host.recovery_resets == 0);
    assert(!f.store.rawRecoveryOnly() && !f.store.contactsAreVolatile());
    assert(f.host.contacts.size() == accepted.size());
    assert(f.host.dirty_deadline == 0x76543210 && f.host.dirty_failures == 3);
    assert(f.host.cache_flushes == flushes && SPIFFS.files == durable);
    assert(SPIFFS.writes == writes && SPIFFS.removes == removes && SPIFFS.renames == renames);
    for (unsigned i = 0; i < f.host.contacts.size(); ++i) {
      std::array<uint8_t, 152> current;
      assert(serializeContactRecord(f.host.contacts[i], current.data()));
      assert(current == accepted[i] && f.host.contacts[i].path_ref.handle() == handles[i]);
    }
#if defined(NRF52_PLATFORM)
    assert(f.store.unreadPages() == 0 && f.store.dirtyPages() == dirty_pages);
    assert(f.store.pageGeneration(0) == generation);
#else
    assert(f.store.jobAddress() == job && f.store.jobIndex() == job_index);
    assert(f.store.writeRevision() == revision && f.store.writeRequested() == requested);
    assert(f.store.hasPendingContactWrites());
#endif
    pressure.clear();
#if defined(ESP32_PLATFORM)
    unsigned passes = 0;
    while (f.store.hasPendingContactWrites()) {
      assert(++passes < 1000);
      assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
    }
#else
    assert(f.store.saveContacts(&f.host, cachedContactFilter));
    // This fixture's migration lifecycle seam omits legacy truncation. Finish
    // that step only after every actual page write succeeded, then reboot.
    if (!paged) assert(SPIFFS.remove("/contacts3"));
#endif
    f.host.contacts.clear();
    DataStore rebooted(f.host);
    rebooted.loadContacts(&f.host);
    assert(!rebooted.hasIncompleteContactLoad() && f.host.contacts.size() == accepted.size());
    for (unsigned i = 0; i < f.host.contacts.size(); ++i) {
      std::array<uint8_t, 152> current;
      assert(serializeContactRecord(f.host.contacts[i], current.data()));
      assert(current == accepted[i]);
    }
  }
}

static void raw_read_recovery_falls_back_on_persistent_io_or_checksum_failure() {
  for (bool checksum_failure : {false, true}) {
    Fixture f(30);
    const char* failed = "/contacts3";
#if defined(NRF52_PLATFORM)
    assert(f.store.writeContactPage(&f.host, 0, cachedContactFilter));
    assert(f.store.writeContactPage(&f.host, 1, cachedContactFilter));
    assert(SPIFFS.remove("/contacts3"));
    failed = "/contacts4_00";
#endif
    Path path;
    SPIFFS.fail_read = failed;
    assert(!f.host.contacts[20].copyPathTo(path.data()));
    assert(f.store.contactRecoveryPending() && f.store.rawRecoveryOnly());
    if (checksum_failure) {
      SPIFFS.fail_read.clear();
#if defined(NRF52_PLATFORM)
      SPIFFS.files.at("/contacts4_01")[mesh::storage::CONTACT_PAGE_HEADER_SIZE + 4 * 152 + 76] ^= 1;
#else
      SPIFFS.files.at("/contacts3")[29 * 152 + 76] ^= 1;
#endif
    }
    f.host.dirty_deadline = 42;
    f.store.recoverContacts(&f.host);
    assert(!f.store.contactRecoveryPending() && !f.store.hasIncompleteContactLoad());
    assert(f.host.recovery_resets > 0 && f.host.dirty_deadline == 0);
    assert(!f.store.rawRecoveryOnly());
    SPIFFS.fail_read.clear();
  }
}

static void strong_recovery_faults_cannot_be_downgraded_by_raw_reads() {
  for (bool strong_first : {false, true}) {
    Fixture f(30);
    snprintf(f.host.contacts[0].name, sizeof(f.host.contacts[0].name), "unsaved rename");
    if (strong_first) f.store.requireStrongRecovery(true);
    f.store.requireRawReadRecovery(true);
    if (!strong_first) {
      assert(f.store.rawRecoveryOnly());
      f.store.requireStrongRecovery(true);
    }
    assert(f.store.contactRecoveryPending() && !f.store.rawRecoveryOnly());
    f.store.recoverContacts(&f.host);
    assert(!f.store.contactRecoveryPending() && f.host.recovery_resets > 0);
    assert(f.host.contacts.size() == 30 && !strcmp(f.host.contacts[0].name, "Contact 0"));
  }
}

#if defined(NRF52_PLATFORM)
static void raw_read_recheck_preserves_old_and_new_unread_page_scopes() {
  Fixture f(30);
  assert(f.store.writeContactPage(&f.host, 0, cachedContactFilter));
  assert(f.store.writeContactPage(&f.host, 1, cachedContactFilter));
  assert(SPIFFS.remove("/contacts3"));
  const auto durable = SPIFFS.files;
  Path path;
  SPIFFS.fail_read = "/contacts4_00";
  assert(!f.host.contacts[20].copyPathTo(path.data()));
  assert(f.store.unreadPages() == 1 && f.store.rawRecoveryOnly());
  // A different page fails during recheck. A refused boundary must preserve
  // both scopes for the later strong recovery, not just its freshest fault.
  SPIFFS.fail_read = "/contacts4_01";
  f.host.recovery_reset_allowed = false;
  f.store.recoverContacts(&f.host);
  assert(f.store.contactRecoveryPending() && !f.store.rawRecoveryOnly());
  assert(f.store.unreadPages() == 3 && f.host.recovery_resets == 0);
  assert(f.host.contacts.size() == 30 && SPIFFS.files == durable);
  SPIFFS.fail_read.clear();
  f.host.recovery_reset_allowed = true;
  f.store.recoverContacts(&f.host);
  assert(!f.store.contactRecoveryPending() && !f.store.hasIncompleteContactLoad());
  assert(f.host.recovery_resets > 0 && f.host.contacts.size() == 30);
  assert(SPIFFS.files == durable);
}
#endif

static void routes_survive_eviction_and_full_sync() {
  Fixture f;
  assert(sizeof(ContactInfo) <= 92); // no per-contact padding tax across all 350 cached records
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
  assert(f.store.contactRecoveryPending());
  f.store.recoverContacts(&f.host);
  assert(!f.store.contactRecoveryPending() && !f.store.hasIncompleteContactLoad());
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
  uint8_t request[] = {1};
  uint32_t tag, timeout;
  for (bool fail : {true, false}) {
    SPIFFS.fail_read = fail ? "/contacts3" : "";
    if (!fail) {
      assert(f.store.contactRecoveryPending() && f.host.contacts.size() == 30);
      f.store.recoverContacts(&f.host); // Safe boundary: no recipient reference is held.
      assert(!f.store.contactRecoveryPending() && f.host.contacts.size() == 30);
    }
    const auto& recipient = f.host.contacts[29];
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

static void shared_request_tail_keeps_payload_routes_and_timeouts() {
  Fixture f(1);
  mesh::contactSecretCache().attach(&f.store);
  BaseChatMesh mesh;
  ContactInfo recipient = f.host.contacts[0];
  recipient.sync_since = 0x12345678;
  std::vector<uint8_t> request(MAX_PACKET_PAYLOAD, 0x6D);
  std::array<uint8_t, 32> first_secret = {};
  bool have_secret = false;
  unsigned traces = 0;
  for (unsigned kind = 0; kind != 4; ++kind) {
    for (uint8_t path_len : {uint8_t(OUT_PATH_UNKNOWN), uint8_t(0), uint8_t(0x42)}) {
      recipient.out_path_len = path_len;
      for (unsigned outcome = 0; outcome != 4; ++outcome) {
        if (kind == 0 && outcome == 3) continue; // Login does not allocate request tags.
        const unsigned variants = kind == 0 ? 6 : kind == 3 ? 1 : 2;
        for (unsigned variant = 0; variant != variants; ++variant) {
          mesh.allocation_succeeds = outcome != 2;
          mesh.queue_accepts = outcome != 1;
          mesh.tag_allowed = outcome != 3;
          const unsigned allocations = mesh.allocations, attempted = mesh.queue_attempts;
          const unsigned queued = mesh.queued, released = mesh.released;
          const unsigned airtime_queries = mesh.airtime_queries, floods = mesh.flood_calls;
          const unsigned tag_checks = mesh.tag_checks;
          uint32_t timeout = 0xDEADBEEF, tag = 0xCAFEBABE;
          int result;
          std::vector<uint8_t> expected(4);
          const uint32_t timestamp = 100;
          memcpy(expected.data(), &timestamp, sizeof(timestamp));
          if (kind == 0) {
            recipient.type = variant < 3 ? ADV_TYPE_ROOM : 1;
            const size_t lengths[] = {0, 15, 16};
            const std::string password(lengths[variant % 3], 'p');
            if (recipient.type == ADV_TYPE_ROOM) {
              const auto* since = reinterpret_cast<const uint8_t*>(&recipient.sync_since);
              expected.insert(expected.end(), since, since + 4);
            }
            expected.insert(expected.end(), std::min(password.size(), size_t(15)), 'p');
            result = mesh.sendLogin(recipient, password.c_str(), timeout);
          } else if (kind == 1 || kind == 2) {
            // Leave the same timestamp/encryption envelope required by the
            // production datagram builders, not just the caller's scratch.
            const unsigned overhead = kind == 1 ? 1 + PUB_KEY_SIZE : CIPHER_MAC_SIZE;
            const uint8_t length = variant == 0 ? 0
                : MAX_PACKET_PAYLOAD - 4 - overhead - (CIPHER_BLOCK_SIZE - 1);
            expected.insert(expected.end(), request.begin(), request.begin() + length);
            result = kind == 1
                ? mesh.sendAnonReq(recipient, request.data(), length, tag, timeout)
                : mesh.sendRequest(recipient, request.data(), length, tag, timeout);
          } else {
            expected.push_back(0x71);
            expected.insert(expected.end(), 4, 0);
            expected.insert(expected.end(), 4, 42);
            result = mesh.sendRequest(recipient, 0x71, tag, timeout);
          }
          ++traces;
          assert(mesh.tag_checks == tag_checks + unsigned(kind != 0));
          if (outcome == 3) {
            assert(result == MSG_SEND_FAILED && tag == timestamp);
            assert(mesh.allocations == allocations && mesh.queue_attempts == attempted);
            assert(mesh.airtime_queries == airtime_queries && mesh.released == released);
            assert(timeout == 0xDEADBEEF);
            continue;
          }
          assert(mesh.allocations == allocations + 1 && mesh.payload == expected);
          if (mesh.allocation_succeeds)
            assert(mesh.packet.getPayloadType() == (kind < 2 ? PAYLOAD_TYPE_ANON_REQ : PAYLOAD_TYPE_REQ));
          if (kind != 0) assert(tag == timestamp);
          if (!have_secret) { first_secret = mesh.shared_secret; have_secret = true; }
          assert(mesh.shared_secret == first_secret);
          if (outcome == 2) {
            assert(result == MSG_SEND_FAILED && timeout == 0xDEADBEEF);
            assert(mesh.queue_attempts == attempted && mesh.released == released);
            assert(mesh.airtime_queries == airtime_queries && mesh.flood_calls == floods);
            continue;
          }
          assert(mesh.airtime_queries == airtime_queries + 1);
          assert(mesh.queue_attempts == attempted + 1);
          assert(mesh.flood_calls == floods + unsigned(path_len == OUT_PATH_UNKNOWN));
          if (outcome == 1) {
            assert(result == MSG_SEND_FAILED && timeout == 0xDEADBEEF);
            assert(mesh.queued == queued && mesh.released == released + 1);
          } else {
            const bool flood = path_len == OUT_PATH_UNKNOWN;
            assert(result == (flood ? MSG_SEND_SENT_FLOOD : MSG_SEND_SENT_DIRECT));
            assert(timeout == (flood ? 40u : 20u * (path_len + 1u)));
            assert(mesh.queued == queued + 1 && mesh.released == released);
            if (!flood) {
              assert(mesh.packet.getRouteType() == ROUTE_TYPE_DIRECT);
              const unsigned path_bytes = (path_len & 63) * ((path_len >> 6) + 1);
              assert(mesh.packet.path_len == path_len);
              assert(!memcmp(mesh.packet.path, route(0).data(), path_bytes));
            }
          }
        }
      }
    }
  }
  assert(traces == 114);
  printf("PASS: %u shared request/login payload and routing traces\n", traces);
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
  assert(!f.store.contactRecoveryPending()); // Handle pressure is not corrupt data.
  f.store.recoverContacts(&f.host);
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

static void contact_recovery_retries_and_discards_only_unread_contacts() {
  for (unsigned fault = 0; fault < 3; ++fault) {
    const bool persistent_failure = fault != 0;
    const bool removal_failure = fault == 2;
    Fixture f(30);
#if defined(NRF52_PLATFORM)
    assert(f.store.writeContactPage(&f.host, 0, cachedContactFilter));
    assert(f.store.writeContactPage(&f.host, 1, cachedContactFilter));
    SPIFFS.remove("/contacts3");
    const char* failed_path = "/contacts4_00";
#else
    const char* failed_path = "/contacts3";
#endif
    for (const char* name : {"/_main.id", "/new_prefs", "/channels2", "/fleet_channel",
                             "/mesh_mt", "/mesh_eth"}) SPIFFS.files[name] = {7, 3, 9};
    const auto durable = SPIFFS.files;
    f.host.contacts.clear();
    SPIFFS.fail_read = failed_path;
    f.store.loadContacts(&f.host);
    assert(f.store.hasIncompleteContactLoad() && f.store.contactRecoveryPending());
    assert(f.host.recovery_resets == 0); // The load callback never resets its host.
    f.host.recovery_reset_allowed = false;
    f.store.recoverContacts(&f.host);
    assert(f.store.contactRecoveryPending() && SPIFFS.files == durable);
    assert(f.host.recovery_resets == 0); // No discard without the safe host boundary.
    f.host.recovery_reset_allowed = true;
    if (!persistent_failure) SPIFFS.fail_read.clear();
    SPIFFS.fail_remove = removal_failure;
    f.store.recoverContacts(&f.host);
    assert(!f.store.contactRecoveryPending() && !f.store.hasIncompleteContactLoad());
    assert(f.store.contactsAreVolatile() == removal_failure);
    assert(f.host.recovery_resets > 0);
    auto expected = durable;
    if (persistent_failure && !removal_failure) expected.erase(failed_path);
    assert(SPIFFS.files == expected); // No archive or unrelated settings reset.
#if defined(NRF52_PLATFORM)
    assert(f.host.contacts.size() == (persistent_failure ? 5u : 30u));
    for (unsigned i = 0; i < f.host.contacts.size(); ++i)
      f.check(i, route(i + (persistent_failure ? 25u : 0u)));
#else
    assert(f.host.contacts.size() == (persistent_failure ? 0u : 30u));
    for (unsigned i = 0; i < f.host.contacts.size(); ++i) f.check(i, route(i));
#endif
    SPIFFS.fail_read.clear(); SPIFFS.fail_remove = false;
    assert(f.store.saveContacts(&f.host, cachedContactFilter) == !removal_failure);
    if (removal_failure) assert(SPIFFS.files == expected);
  }
}

#if defined(ESP32_PLATFORM)
static void uncertain_publication_is_not_a_raw_read_retry() {
  Fixture f(30);
  assert(f.host.contacts[0].setRawPath(route(2470).data()));
  assert(f.store.markContactDirty(f.host.contacts[0]));
  unsigned passes = 0;
  while (!f.store.readyToPublish()) {
    assert(++passes < 1000);
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
  }
  SPIFFS.after_rename = [](FakeFilesystem& fs, const char* path) {
    if (!strcmp(path, "/contacts3")) fs.metadata_error = true;
  };
  assert(!f.store.serviceContactWrites(&f.host, cachedContactFilter));
  assert(f.store.contactRecoveryPending() && !f.store.rawRecoveryOnly());
  assert(!f.store.activeJob() && f.store.hasIncompleteContactLoad());
  SPIFFS.after_rename = {};
  SPIFFS.metadata_error = false;
  // Ordinary publication uncertainty must still reload even though every
  // current path would read correctly from the now-published file.
  f.store.requireRawReadRecovery(true);
  assert(!f.store.rawRecoveryOnly());
  f.store.recoverContacts(&f.host);
  assert(!f.store.contactRecoveryPending() && !f.store.hasIncompleteContactLoad());
  assert(f.host.recovery_resets > 0 && f.host.contacts.size() == 30);
  f.check(0, route(2470));
}

static unsigned drain_cooperative_save(Fixture& f) {
  unsigned serviced_commands = 0;
  while (f.store.hasPendingContactWrites()) {
    const unsigned before_writes = SPIFFS.writes;
    const unsigned before_reads = SPIFFS.reads;
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
    // Returning from every bounded store pass lets the production mesh loop
    // poll transports. No pass serializes the whole table or verifies it all.
    assert(SPIFFS.writes - before_writes <= 1);
    assert(SPIFFS.reads - before_reads <= 16); // retained path snapshots
    ++serviced_commands;
    assert(serviced_commands < 3000);
    assert(SPIFFS.exists("/contacts3")); // no observable rename gap
    for (unsigned i = 0; i < f.host.contacts.size(); i += 47) {
      Path path;
      assert(f.host.contacts[i].copyPathTo(path.data()));
    }
  }
  return serviced_commands;
}

static void cooperative_save_services_commands_and_preserves_snapshots() {
  Fixture f;
  const ContactInfo snapshot = f.host.contacts[349];
  const auto old = SPIFFS.files.at("/contacts3");
  assert(f.host.contacts[349].setRawPath(route(980).data()));
  assert(f.store.markContactDirty(f.host.contacts[349]));
  assert(f.store.hasPendingContactWrites());
  assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
  assert(f.store.hasPendingContactWrites() && SPIFFS.files.at("/contacts3") == old);
  const unsigned polled = drain_cooperative_save(f);
  assert(polled > 350); // record writes and CRC readback both yield
  assert(SPIFFS.files.at("/contacts3") != old);
  f.check(349, route(980));
  Path path;
  assert(snapshot.copyPathTo(path.data()) && path == route(349));
  for (const auto& files : SPIFFS.rename_snapshots) {
    FakeFilesystem rebooted;
    rebooted.files = files;
    assert(mesh::ContactFileTransaction::recover(&rebooted, "/contacts3"));
    assert(rebooted.files.at("/contacts3") == old
        || rebooted.files.at("/contacts3") == SPIFFS.files.at("/contacts3"));
  }
}

static void background_crc_verification_uses_bounded_batches() {
  Fixture f(347);
  SPIFFS.emulate_stdio = true;
  snprintf(f.host.contacts[0].name, sizeof(f.host.contacts[0].name), "CRC cadence");
  assert(f.store.markContactDirty(f.host.contacts[0]));
  unsigned crc_read_passes = 0, total_crc_reads = 0;
  unsigned max_reads = 0, max_backend_reads = 0, passes = 0;
  while (f.store.hasPendingContactWrites()) {
    const bool verifying = f.store.verifying() && !f.store.readyToPublish();
    const unsigned reads = SPIFFS.reads, backend_reads = SPIFFS.backend_reads;
    assert(++passes < 2000);
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
    if (!verifying) continue; // serialization also reads cold cached paths
    const unsigned read_count = SPIFFS.reads - reads;
    const unsigned backend_count = SPIFFS.backend_reads - backend_reads;
    if (read_count != 0) ++crc_read_passes;
    total_crc_reads += read_count;
    max_reads = std::max(max_reads, read_count);
    max_backend_reads = std::max(max_backend_reads, backend_count);
    assert(read_count <= 8 && backend_count <= 1);
  }
  assert(total_crc_reads == 825);
  assert(crc_read_passes == 104);
  assert(max_reads == 8 && max_backend_reads == 1);
  assert(!SPIFFS.exists("/contacts3.tmp"));
  f.host.contacts.clear();
  f.store.loadContacts(&f.host);
  assert(!f.store.hasIncompleteContactLoad() && f.host.contacts.size() == 347);
  assert(strcmp(f.host.contacts[0].name, "CRC cadence") == 0);
  for (unsigned i = 0; i < 347; ++i) f.check(i, route(i));
}

static void cooperative_mutations_never_publish_obsolete_indices() {
  for (unsigned mutation_step : {4u, 46u, 110u, 0u}) {
    Fixture f(40);
    const auto old = SPIFFS.files.at("/contacts3");
    assert(f.store.markContactDirty(f.host.contacts[0]));
    unsigned step = 0;
    while (mutation_step == 0 ? !f.store.readyToPublish() : step < mutation_step) {
      assert(++step < 1000);
      assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
      assert(SPIFFS.files.at("/contacts3") == old);
    }
    const ContactInfo snapshot = f.host.contacts[3]; // created during save
    assert(f.store.releaseContact(f.host.contacts[3]));
    f.host.contacts.erase(f.host.contacts.begin() + 3); // shifts all later indices
    assert(f.host.contacts[1].setRawPath(route(991).data()));
    snprintf(f.host.contacts[1].name, sizeof(f.host.contacts[1].name), "new revision");
    assert(f.store.markContactDirty(f.host.contacts[1]));
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
    assert(f.store.hasPendingContactWrites()); // abort is not durable success
    assert(SPIFFS.files.at("/contacts3") == old);
    drain_cooperative_save(f);
    const auto& bytes = SPIFFS.files.at("/contacts3");
    assert(bytes.size() == 39 * 152);
    for (unsigned i = 0; i < 39; ++i) {
      assert(memcmp(bytes.data() + i * 152, f.host.contacts[i].id.pub_key, 32) == 0);
      Path path;
      assert(f.host.contacts[i].copyPathTo(path.data()));
      const Path expected = route(i == 1 ? 991 : i >= 3 ? i + 1 : i);
      assert(path == expected);
    }
    Path path;
    assert(snapshot.copyPathTo(path.data()) && path == route(3));
  }
}

static void cooperative_failures_keep_the_old_file_and_pending_mutations() {
  for (unsigned fault = 0; fault < 4; ++fault) {
    Fixture f(30);
    const auto old = SPIFFS.files.at("/contacts3");
    assert(f.host.contacts[0].setRawPath(route(993).data()));
    assert(f.store.markContactDirty(f.host.contacts[0]));
    if (fault == 0) SPIFFS.max_write = 17;
    if (fault == 1) SPIFFS.fail_read = "/contacts3.tmp";
    if (fault == 2) SPIFFS.fail_rename = SPIFFS.renames + 2;
    bool failed = false;
    for (unsigned step = 0; step < 1000 && !failed; ++step) {
      if (fault == 3 && step == 35) SPIFFS.files.at("/contacts3.tmp")[100] ^= 1;
      failed = !f.store.serviceContactWrites(&f.host, cachedContactFilter);
    }
    assert(failed && f.store.hasPendingContactWrites());
    assert(!f.store.activeJob());
    assert(SPIFFS.files.at("/contacts3") == old);
    assert(!SPIFFS.exists("/contacts3.tmp"));
    f.check(0, route(993));
    SPIFFS.max_write = std::numeric_limits<size_t>::max();
    SPIFFS.fail_read.clear(); SPIFFS.fail_rename = 0;
    drain_cooperative_save(f);
    assert(SPIFFS.files.at("/contacts3") != old);
    f.check(0, route(993));
  }
}

static void synchronous_flush_cancels_pending_jobs_and_stays_durable() {
  Fixture f(40);
  const ContactInfo snapshot = f.host.contacts[0];
  assert(f.host.contacts[0].setRawPath(route(994).data()));
  assert(f.store.markContactDirty(f.host.contacts[0]));
  for (unsigned i = 0; i < 8; ++i)
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
  // Update, reorder, then force explicit durability instead of draining a
  // snapshot from an earlier revision. The rename callback sees a full file.
  std::swap(f.host.contacts[0], f.host.contacts[3]);
  assert(f.store.markContactDirty(f.host.contacts[0]));
  assert(f.store.flushContactWrites(&f.host, cachedContactFilter));
  assert(!f.store.hasPendingContactWrites() && !SPIFFS.exists("/contacts3.tmp"));
  assert(memcmp(SPIFFS.files.at("/contacts3").data(), f.host.contacts[0].id.pub_key, 32) == 0);
  f.check(3, route(994));
  Path path;
  assert(snapshot.copyPathTo(path.data()) && path == route(0));
}

static void interleaved_path_pressure_preserves_guarded_eviction() {
  Fixture f(40);
  for (unsigned i = 0; i < 26; ++i) {
    // Keep a real lazy job open while more than all16 resident path slots are
    // replaced by app updates. makeRoom must abandon only that cooperative
    // job before the pre-existing synchronous eviction flush obtains room.
    if (!f.store.hasPendingContactWrites())
      assert(f.store.markContactDirty(f.host.contacts[0]));
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
    assert(f.host.contacts[i].setRawPath(route(1200 + i).data()));
    assert(f.store.markContactDirty(f.host.contacts[i]));
  }
  assert(f.host.cache_flushes > 0); // actual cache exhaustion, no mocked bypass
  drain_cooperative_save(f);
  for (unsigned i = 0; i < 40; ++i)
    f.check(i, route(i < 26 ? 1200 + i : i));
}

static void pending_startup_pressure_preserves_guarded_eviction() {
  for (unsigned completed : {0u, 3u, 6u}) {
    Fixture f(40);
    for (unsigned i = 0; i < 16; ++i) {
      assert(f.host.contacts[i].setRawPath(route(1600 + i).data()));
      assert(f.store.markContactDirty(f.host.contacts[i]));
    }
    assert(f.host.cache_flushes == 0);
    SPIFFS.files["/contacts3.tmp"] = {8, 9};
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter)); // allocate
    for (unsigned pass = 0; pass < completed; ++pass)
      assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
    assert(f.store.starting());
    // The seventeenth resident update must cancel a pending begin, release
    // _committing, then run the existing guarded synchronous eviction flush.
    assert(f.host.contacts[16].setRawPath(route(1616).data()));
    assert(f.host.cache_flushes == 1);
    assert(!f.store.activeJob());
    assert(f.store.markContactDirty(f.host.contacts[16]));
    drain_cooperative_save(f);
    for (unsigned i = 0; i < 40; ++i)
      f.check(i, route(i < 17 ? 1600 + i : i));
  }
}

static void lifecycle_cancels_jobs_and_closes_prior_filesystem_readers() {
  {
    Fixture f(30);
    assert(f.store.markContactDirty(f.host.contacts[0]));
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
    f.check(29, route(29)); // opens the original cold-path reader
    f.store.begin();
    assert(f.store.hasPendingContactWrites());
    assert(!SPIFFS.exists("/contacts3.tmp"));
    const unsigned opens = SPIFFS.opens;
    f.check(28, route(28));
    assert(SPIFFS.opens == opens + 1); // reinitialization reopened its reader
    drain_cooperative_save(f);
  }
  {
    Fixture f(30);
    FakeFilesystem alternate;
    alternate.files = SPIFFS.files;
    f.store.useSecondary(&alternate);
    assert(f.store.markContactDirty(f.host.contacts[0]));
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
    f.check(29, route(29));
    const unsigned opens = SPIFFS.opens;
    f.store.disableSecondaryFS(false);
    assert(f.store.hasPendingContactWrites());
    assert(!alternate.exists("/contacts3.tmp"));
    f.check(28, route(28));
    assert(SPIFFS.opens == opens + 1); // reads follow the primary route
    drain_cooperative_save(f);
    // No lazy job remains tied to alternate's shorter lifetime.
  }
}

static void partial_loads_cannot_replace_the_complete_file() {
  Fixture f(40);
  f.host.contacts.clear();
  const auto durable = SPIFFS.files;
  const unsigned removes = SPIFFS.removes, writes = SPIFFS.writes;
  f.host.capacity = 20;
  f.store.loadContacts(&f.host);
  assert(f.store.hasIncompleteContactLoad());
  assert(!f.store.contactRecoveryPending()); // Host refusal is not a storage fault.
  f.store.recoverContacts(&f.host);
  assert(!f.store.saveContacts(&f.host, cachedContactFilter));
  assert(SPIFFS.files == durable && SPIFFS.removes == removes && SPIFFS.writes == writes);
}

static void valid_backup_routes_survive_failed_promotion() {
  for (bool removal_failure : {false, true}) {
    Fixture f(40);
    const auto valid = SPIFFS.files.at("/contacts3");
    f.host.contacts.clear();
    SPIFFS.files["/contacts3"] = {9};
    SPIFFS.files["/contacts3.bak"] = valid;
    SPIFFS.fail_remove = removal_failure;
    if (!removal_failure) SPIFFS.fail_rename = SPIFFS.renames + 1;
    f.store.loadContacts(&f.host);
    assert(!f.store.hasIncompleteContactLoad() && !f.store.contactRecoveryPending());
    assert(f.host.contacts.size() == 40 && f.host.recovery_resets == 0);
    assert(SPIFFS.files.at("/contacts3.bak") == valid);
    for (unsigned i = 0; i < 40; ++i) f.check(i, route(i));
    if (!removal_failure) SPIFFS.fail_rename = SPIFFS.renames + 1;
    assert(!f.store.saveContacts(&f.host, cachedContactFilter));
    assert(SPIFFS.files.at("/contacts3.bak") == valid);
    assert(!f.store.contactRecoveryPending() && f.host.recovery_resets == 0);
    SPIFFS.fail_remove = false; SPIFFS.fail_rename = 0;
    assert(f.store.saveContacts(&f.host, cachedContactFilter));
    assert(SPIFFS.files.at("/contacts3") == valid);
    for (unsigned i = 0; i < 40; ++i) f.check(i, route(i));
  }
  Fixture f(40);
  const auto valid = SPIFFS.files.at("/contacts3");
  f.host.contacts.clear();
  SPIFFS.files["/contacts3"] = {9};
  SPIFFS.files["/contacts3.bak"] = valid;
  SPIFFS.fail_rename = SPIFFS.renames + 1;
  f.store.loadContacts(&f.host);
  assert(f.host.contacts.size() == 40 && !f.store.hasIncompleteContactLoad());
  SPIFFS.fail_rename = 0;
  SPIFFS.after_rename = [](FakeFilesystem& fs, const char* path) {
    if (!strcmp(path, "/contacts3")) fs.metadata_error = true;
  };
  assert(!f.store.saveContacts(&f.host, cachedContactFilter));
  assert(SPIFFS.files.at("/contacts3") == valid && !SPIFFS.exists("/contacts3.bak"));
  SPIFFS.metadata_error = false; SPIFFS.after_rename = {};
  assert(f.store.saveContacts(&f.host, cachedContactFilter));
  assert(SPIFFS.files.at("/contacts3") == valid);
  for (unsigned i = 0; i < 40; ++i) f.check(i, route(i));
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

static void nrf52_resource_refusals_never_discard_contact_pages() {
  for (bool allocation_failure : {false, true}) {
    Fixture f(30);
    assert(f.store.writeContactPage(&f.host, 0, cachedContactFilter));
    assert(f.store.writeContactPage(&f.host, 1, cachedContactFilter));
    SPIFFS.remove("/contacts3");
    const auto durable = SPIFFS.files;
    const unsigned writes = SPIFFS.writes, removes = SPIFFS.removes;
    f.host.contacts.clear();
    fail_contact_page_allocation = allocation_failure;
    if (!allocation_failure) f.host.capacity = 20;
    f.store.loadContacts(&f.host);
    assert(f.store.hasIncompleteContactLoad() && !f.store.contactRecoveryPending());
    f.store.recoverContacts(&f.host);
    assert(f.host.recovery_resets == 0);
    assert(!f.store.saveContacts(&f.host, cachedContactFilter));
    assert(SPIFFS.files == durable && SPIFFS.writes == writes && SPIFFS.removes == removes);
    fail_contact_page_allocation = false;
    f.host.capacity = 350;
    f.host.contacts.clear();
    DataStore rebooted(f.host);
    rebooted.loadContacts(&f.host);
    assert(!rebooted.hasIncompleteContactLoad() && f.host.contacts.size() == 30);
    for (unsigned i = 0; i < 30; ++i) f.check(i, route(i));
  }
}

static void nrf52_marker_error_keeps_a_usable_contact_view() {
  Fixture f(30);
  const auto durable = SPIFFS.files;
  f.host.contacts.clear();
  fail_contact_migration_marker = true;
  f.store.loadContacts(&f.host);
  fail_contact_migration_marker = false;
  assert(!f.store.hasIncompleteContactLoad() && !f.store.contactRecoveryPending());
  assert(f.store.contactsAreVolatile() && f.host.contacts.size() == 30);
  for (unsigned i = 0; i < 30; ++i) f.check(i, route(i));
  const unsigned writes = SPIFFS.writes, removes = SPIFFS.removes;
  f.store.recoverContacts(&f.host);
  assert(f.host.recovery_resets == 0);
  assert(!f.store.saveContacts(&f.host, cachedContactFilter));
  assert(SPIFFS.files == durable && SPIFFS.writes == writes && SPIFFS.removes == removes);
}

static void nrf52_unknown_legacy_stat_preserves_contact_authority() {
  for (bool legacy_present : {false, true}) {
    for (bool persistent_failure : {false, true}) {
      Fixture f(30);
      assert(f.store.writeContactPage(&f.host, 0, cachedContactFilter));
      assert(f.store.writeContactPage(&f.host, 1, cachedContactFilter));
      if (legacy_present) {
        // A downgrade may leave a shorter authoritative legacy view beside
        // stale, otherwise completely readable pages. No migration marker
        // permits treating those extra records as a committed page tail.
        SPIFFS.files["/contacts3"].resize(5 * mesh::storage::CONTACT_RECORD_SIZE);
      } else {
        SPIFFS.remove("/contacts3");
      }
      const FakeFilesystem::Files unrelated = {
          {"/_main.id", {1, 3}}, {"/new_prefs", {2, 4}}, {"/channels2", {5, 7}},
          {"/.extrafs.mig", {'M', 'C', 'X', 'F', 1, 2}}};
      for (const auto& file : unrelated) SPIFFS.files[file.first] = file.second;
      const auto durable = SPIFFS.files;
      const unsigned writes = SPIFFS.writes, removes = SPIFFS.removes;
      f.host.contacts.clear();
      fail_contact_legacy_stat = true;
      f.store.loadContacts(&f.host);
      assert(f.store.contactRecoveryPending() && f.store.hasIncompleteContactLoad());
      assert(f.host.contacts.empty() && SPIFFS.files == durable);
      if (!persistent_failure) fail_contact_legacy_stat = false;
      f.store.recoverContacts(&f.host);
      assert(!f.store.contactRecoveryPending() && !f.store.hasIncompleteContactLoad());
      assert(f.store.contactsAreVolatile() == persistent_failure);
      const unsigned expected_contacts = legacy_present ? 5 : 30;
      assert(f.host.contacts.size() == (persistent_failure ? 0 : expected_contacts));
      if (persistent_failure) {
        // Unknown source authority is not a corrupt legacy image. Keep both
        // candidate namespaces unchanged and permit usable volatile hardware.
        assert(SPIFFS.files == durable);
        assert(SPIFFS.writes == writes && SPIFFS.removes == removes);
        assert(!f.store.saveContacts(&f.host, cachedContactFilter));
        f.store.recoverContacts(&f.host);
        assert(SPIFFS.files == durable && f.host.contacts.empty());
      }
      for (const auto& file : unrelated) assert(SPIFFS.files.at(file.first) == file.second);
      fail_contact_legacy_stat = false;
      f.host.contacts.clear();
      DataStore rebooted(f.host);
      rebooted.loadContacts(&f.host);
      assert(!rebooted.hasIncompleteContactLoad() && !rebooted.contactRecoveryPending());
      assert(!rebooted.contactsAreVolatile() && f.host.contacts.size() == expected_contacts);
      for (unsigned i = 0; i < expected_contacts; ++i) f.check(i, route(i));
    }
  }
}

static void nrf52_unknown_page_stat_preserves_contact_authority() {
  for (bool legacy_present : {false, true}) {
    for (int failures : {1, 2, -1}) {
      Fixture f(30);
      assert(f.store.writeContactPage(&f.host, 0, cachedContactFilter));
      assert(f.store.writeContactPage(&f.host, 1, cachedContactFilter));
      if (legacy_present) {
        SPIFFS.files["/contacts3"].resize(5 * mesh::storage::CONTACT_RECORD_SIZE);
      } else {
        SPIFFS.remove("/contacts3");
      }
      const FakeFilesystem::Files unrelated = {
          {"/_main.id", {1, 3}}, {"/new_prefs", {2, 4}}, {"/channels2", {5, 7}},
          {"/.extrafs.mig", {'M', 'C', 'X', 'F', 1, 2}}};
      for (const auto& file : unrelated) SPIFFS.files[file.first] = file.second;
      const auto durable = SPIFFS.files;
      const unsigned writes = SPIFFS.writes, removes = SPIFFS.removes;
      f.host.contacts.clear();
      fail_contact_page_stats = failures;
      f.store.loadContacts(&f.host);
      assert(f.store.contactRecoveryPending() && f.store.hasIncompleteContactLoad());
      assert(f.host.contacts.empty() && SPIFFS.files == durable);
      f.store.recoverContacts(&f.host);
      const bool persistent_failure = failures < 0;
      assert(!f.store.contactRecoveryPending() && !f.store.hasIncompleteContactLoad());
      assert(f.store.contactsAreVolatile() == persistent_failure);
      const unsigned expected_contacts = legacy_present ? 5 : 30;
      assert(f.host.contacts.size() == (persistent_failure ? 0 : expected_contacts));
      // In particular, two metadata failures clear just before the cleanup
      // probe. That later successful stat is not permission to erase a page
      // whose complete bytes never failed validation or even a read attempt.
      assert(SPIFFS.files == durable);
      assert(SPIFFS.writes == writes && SPIFFS.removes == removes);
      for (const auto& file : unrelated) assert(SPIFFS.files.at(file.first) == file.second);
      if (persistent_failure) {
        assert(!f.store.saveContacts(&f.host, cachedContactFilter));
        f.store.recoverContacts(&f.host);
        assert(SPIFFS.files == durable && f.host.contacts.empty());
      }
      fail_contact_page_stats = 0;
      f.host.contacts.clear();
      DataStore rebooted(f.host);
      rebooted.loadContacts(&f.host);
      assert(!rebooted.hasIncompleteContactLoad() && !rebooted.contactRecoveryPending());
      assert(!rebooted.contactsAreVolatile() && f.host.contacts.size() == expected_contacts);
      for (unsigned i = 0; i < expected_contacts; ++i) f.check(i, route(i));
    }
  }
}

static void nrf52_unread_legacy_and_unknown_marker_cannot_resurrect_stale_pages() {
  Fixture f(30);
  assert(f.store.writeContactPage(&f.host, 0, cachedContactFilter));
  assert(f.store.writeContactPage(&f.host, 1, cachedContactFilter));
  SPIFFS.files["/contacts3"] = {9};
  SPIFFS.files["/contacts4_00.tmp"] = {8};
  SPIFFS.files["/contacts4.mig"] = {};
  const FakeFilesystem::Files unrelated = {
      {"/_main.id", {1, 3}}, {"/new_prefs", {2, 4}}, {"/channels2", {5, 7}},
      {"/.extrafs.mig", {'M', 'C', 'X', 'F', 1, 2}}};
  for (const auto& file : unrelated) SPIFFS.files[file.first] = file.second;
  f.host.contacts.clear();
  fail_contact_migration_marker = true;
  f.store.loadContacts(&f.host);
  assert(f.store.contactRecoveryPending());
  f.store.recoverContacts(&f.host);
  assert(!f.store.contactRecoveryPending() && !f.store.hasIncompleteContactLoad());
  assert(f.host.contacts.empty());
  assert(!SPIFFS.exists("/contacts3") && !SPIFFS.exists("/contacts4_00")
      && !SPIFFS.exists("/contacts4_01") && !SPIFFS.exists("/contacts4_00.tmp"));
  for (const auto& file : unrelated) assert(SPIFFS.files.at(file.first) == file.second);
  fail_contact_migration_marker = false;
  DataStore rebooted(f.host);
  rebooted.loadContacts(&f.host);
  assert(!rebooted.hasIncompleteContactLoad() && !rebooted.contactRecoveryPending());
  assert(f.host.contacts.empty() && SPIFFS.files == unrelated);
}

static void nrf52_uncertain_page_publication_preserves_snapshots() {
  for (unsigned fault = 0; fault < 3; ++fault) {
    Fixture f;
    for (uint8_t page = 0; page < 14; ++page)
      assert(f.store.writeContactPage(&f.host, page, cachedContactFilter));
    SPIFFS.remove("/contacts3");
    const auto original = SPIFFS.files.at("/contacts4_00");
    ContactInfo snapshot = f.host.contacts[0];
    assert(f.host.contacts[0].setRawPath(route(1100).data()));
    assert(f.store.markContactDirty(f.host.contacts[0]));
    const auto generation = f.store.pageGeneration(0);
    if (fault == 2) SPIFFS.false_success_rename = SPIFFS.renames + 1;
    SPIFFS.after_rename = [fault](FakeFilesystem& fs, const char* path) {
      if (strcmp(path, "/contacts4_00") != 0) return;
      if (fault == 0) fs.late_rename_error = fs.renames;
      else if (fault == 1) fs.read_open_failures[path] = 3;
    };
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter) == (fault == 0));
    assert(f.store.hasPendingContactWrites() == (fault != 0));
    assert(f.store.pageGeneration(0) == generation + (fault == 0));
    assert((SPIFFS.files.at("/contacts4_00") == original) == (fault == 2));
    // Make the retained old path cold if it still has an evictable disk binding.
    for (unsigned i = 1; i < 350; ++i) f.check(i, route(i));
    Path path;
    assert(snapshot.copyPathTo(path.data()) && path == route(0));
    f.check(0, route(1100));
    SPIFFS.after_rename = {};
    SPIFFS.false_success_rename = SPIFFS.late_rename_error = 0;
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
    assert(!f.store.hasPendingContactWrites());
    assert(f.store.pageGeneration(0) == generation + 1);
    for (unsigned i = 1; i < 350; ++i) f.check(i, route(i));
    assert(snapshot.copyPathTo(path.data()) && path == route(0));
    f.check(0, route(1100));
  }
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


#if defined(ESP32_PLATFORM)
static void deferred_cleanup_publishes_actual_cached_handles() {
  Fixture f(40);
  const auto old = SPIFFS.files.at("/contacts3");
  SPIFFS.files["/contacts3.bak"] = {9, 8, 7};
  assert(f.host.contacts[0].setRawPath(route(2100).data()));
  assert(f.store.markContactDirty(f.host.contacts[0]));
  for (unsigned step = 0; step < 4; ++step)
    assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
  assert(SPIFFS.files.at("/contacts3.bak") == std::vector<uint8_t>({9, 8, 7}));
  assert(f.store.serviceContactWrites(&f.host, cachedContactFilter));
  assert(!SPIFFS.exists("/contacts3.bak") && SPIFFS.files.at("/contacts3") == old);
  drain_cooperative_save(f);
  assert(!f.store.hasPendingContactWrites() && SPIFFS.files.at("/contacts3.bak") == old);
  f.check(0, route(2100));
  for (unsigned i = 1; i < 40; ++i) f.check(i, route(i));
}
#endif

int main() {
  raw_read_recovery_preserves_accepted_ram_state();
  raw_read_recovery_falls_back_on_persistent_io_or_checksum_failure();
  strong_recovery_faults_cannot_be_downgraded_by_raw_reads();
  contact_tx_policy_round_trips_without_growing_records();
  shared_request_tail_keeps_payload_routes_and_timeouts();
#if defined(ESP32_PLATFORM)
  uncertain_publication_is_not_a_raw_read_retry();
  deferred_cleanup_publishes_actual_cached_handles();
  cooperative_save_services_commands_and_preserves_snapshots();
  background_crc_verification_uses_bounded_batches();
  cooperative_mutations_never_publish_obsolete_indices();
  cooperative_failures_keep_the_old_file_and_pending_mutations();
  synchronous_flush_cancels_pending_jobs_and_stays_durable();
  interleaved_path_pressure_preserves_guarded_eviction();
  pending_startup_pressure_preserves_guarded_eviction();
  lifecycle_cancels_jobs_and_closes_prior_filesystem_readers();
  routes_survive_eviction_and_full_sync();
  snapshot_rollback_and_dirty_eviction();
  storage_failures_preserve_routes();
  rename_gaps_recover_a_complete_contact_file();
  pinned_snapshots_bound_ram_without_losing_updates();
  encoded_paths_and_copy_assignment();
  failed_path_reads_fail_sends_without_leaking_packets();
  contact_reload_preserves_disk_on_cache_exhaustion();
  contact_recovery_retries_and_discards_only_unread_contacts();
  partial_loads_cannot_replace_the_complete_file();
  valid_backup_routes_survive_failed_promotion();
  secrets_load_from_flash_instead_of_recalculating();
  persisted_secret_matches_the_real_peer_key_exchange();
  secret_corruption_reordering_and_full_storage();
#else
  raw_read_recheck_preserves_old_and_new_unread_page_scopes();
  nrf52_secret_cache_leaves_contact_storage_reserve();
  nrf52_migration_snapshots_and_failed_page_replacement();
  nrf52_resource_refusals_never_discard_contact_pages();
  nrf52_marker_error_keeps_a_usable_contact_view();
  nrf52_unknown_legacy_stat_preserves_contact_authority();
  nrf52_unknown_page_stat_preserves_contact_authority();
  nrf52_unread_legacy_and_unknown_marker_cannot_resurrect_stale_pages();
  nrf52_uncertain_page_publication_preserves_snapshots();
  encoded_paths_and_copy_assignment();
  failed_path_reads_fail_sends_without_leaking_packets();
  contact_reload_preserves_disk_on_cache_exhaustion();
  contact_recovery_retries_and_discards_only_unread_contacts();
  secrets_load_from_flash_instead_of_recalculating();
  persisted_secret_matches_the_real_peer_key_exchange();
#endif
  return 0;
}
