#include "FakeFilesystem.h"
#include <cerrno>
#include <sys/stat.h>
#define FILESYSTEM FakeFilesystem
#include <helpers/ContactInfo.h>
#include <helpers/ContactFileTransaction.h>
#include <helpers/FileRead.h>
#include "packet_under_test.h"

static_assert(MESH_CONTACT_CACHE == 0,
              "This fixture must exercise PSRAM/explicit inline-path storage");

// Replace only the SDK metadata syscall; the production presence/error logic
// and transaction implementation are compiled unchanged.
int fixtureStat(FakeFilesystem* fs, const char* vfs_path, struct stat*) {
  ++fs->metadata_probes;
  if (fs->metadata_error || fs->metadata_probes == fs->fail_metadata_probe) {
    errno = EIO; return -1;
  }
  assert(strncmp(vfs_path, "/spiffs", 7) == 0);
  if (!fs->exists(vfs_path + 7)) { errno = ENOENT; return -1; }
  return 0;
}
#include "presence_under_test.h"

struct Host {
  std::vector<ContactInfo> contacts;
  unsigned copies = 0;
  unsigned recovery_resets = 0;
  bool recovery_reset_allowed = true;
  size_t capacity = 350;
  bool onContactLoaded(const ContactInfo& c) {
    if (contacts.size() >= capacity) return false;
    contacts.push_back(c);
    return true;
  }
  bool resetContactsForRecovery() {
    if (!recovery_reset_allowed) return false;
    ++recovery_resets;
    contacts.clear();
    return true;
  }
  bool getContactForSave(uint32_t index, ContactInfo& contact) {
    ++copies;
    if (index >= contacts.size()) return false;
    contact = contacts[index];
    return true;
  }
  ContactInfo* getContactForStore(uint32_t) {
    assert(false && "Uncached ESP must keep getContactForSave copy semantics");
    return nullptr;
  }
};
using DataStoreHost = Host;

// Hardware-only constructor/FS seams are supplied here. The state fields and
// all save/load/dirty/transaction bodies below come from production sources.
class DataStore {
  FakeFilesystem* _fs;
  // Advert queue behavior is covered by its dedicated production fixture;
  // these hardware seams keep this test focused on contacts and disk delete.
  struct { bool synchronous_io = false; } _advert_write;
  void invalidateAdvertWrite(const uint8_t[], int) {}
  void retireAdvertWrite(const uint8_t[], int) {}
  bool _channel_load_incomplete = false;
  bool _uncached_contact_load_incomplete = false;
#include "contact_recovery_state_under_test.h"
#include "contact_write_state_under_test.h"
public:
  explicit DataStore(FakeFilesystem& fs) : _fs(&fs) {}
  ~DataStore();
  FakeFilesystem* _getContactsChannelsFS() const { return _fs; }
  File openRead(FakeFilesystem* fs, const char* path) { return fs->open(path); }
  bool hasIncompleteContactLoad() const;
  void loadContacts(DataStoreHost*);
  void recoverContacts(DataStoreHost*);
#include "contact_recovery_api_under_test.h"
  bool saveContacts(DataStoreHost*, bool (*)(const ContactInfo&));
  bool flushContactWrites(DataStoreHost*, bool (*)(const ContactInfo&));
  bool markContactDirty(const ContactInfo&);
  bool releaseContact(const ContactInfo&);
  bool serviceContactWrites(DataStoreHost*, bool (*)(const ContactInfo&));
  bool hasPendingContactWrites() const;
  bool deleteBlobByKey(const uint8_t[], int);
  bool verifying() const { return _contact_write_verifying; }
  bool starting() const {
    return _contact_write == nullptr || !static_cast<bool>(*_contact_write);
  }
  bool activeJob() const { return _contact_write != nullptr; }
  bool readyToPublish() const {
    return _contact_write && _contact_write->readyToPublish();
  }
};
#include "store_under_test.h"

ContactInfo contact(unsigned number) {
  ContactInfo c;
  for (unsigned i = 0; i < 32; ++i) c.id.pub_key[i] = (number * 13 + i * 17) & 255;
  memcpy(c.id.pub_key, &number, sizeof(number));
  snprintf(c.name, sizeof(c.name), "Fixture %u", number);
  c.type = 1;
  c.flags = number & 31;
  c.tx_radio = number % 5;
  c.sync_since = 1000 + number;
  c.last_advert_timestamp = 2000 + number;
  c.lastmod = 3000 + number;
  c.gps_lat = 4000 + number;
  c.gps_lon = -5000 - number;
  uint8_t path[9];
  for (unsigned i = 0; i < sizeof(path); ++i) path[i] = number + i;
  assert(c.setPath(path, 0x83));
  return c;
}

Host table(unsigned count = 32) {
  Host h;
  for (unsigned i = 0; i < count; ++i) h.contacts.push_back(contact(i));
  return h;
}

bool keepContact(const ContactInfo& c) { return c.type != 0; }
bool keepEven(const ContactInfo& c) { return (c.id.pub_key[0] & 1) == 0; }
using Filter = bool (*)(const ContactInfo&);

std::vector<uint8_t> image(const Host& h, Filter filter = nullptr) {
  std::vector<uint8_t> bytes;
  for (const auto& c : h.contacts) {
    if (filter && !filter(c)) continue;
    uint8_t record[mesh::storage::CONTACT_RECORD_SIZE];
    assert(serializeContactRecord(c, record));
    bytes.insert(bytes.end(), record, record + sizeof(record));
  }
  return bytes;
}

unsigned drain(DataStore& store, Host& h, Filter filter = nullptr) {
  const bool existed = SPIFFS.exists("/contacts3");
  const auto old = existed ? SPIFFS.files.at("/contacts3") : std::vector<uint8_t>();
  unsigned passes = 0;
  while (store.hasPendingContactWrites()) {
    const unsigned writes = SPIFFS.writes, reads = SPIFFS.reads;
    const unsigned backend_writes = SPIFFS.backend_writes, backend_reads = SPIFFS.backend_reads;
    const bool starting = store.starting();
    const unsigned startup_before = SPIFFS.startupOperations();
    assert(++passes < 5000);
    assert(store.serviceContactWrites(&h, filter));
    const unsigned startup_operations = SPIFFS.startupOperations() - startup_before;
    if (starting) assert(startup_operations <= 1);
    assert(SPIFFS.writes - writes <= 1);
    assert(SPIFFS.reads - reads <= 8);
    assert(SPIFFS.backend_writes - backend_writes <= 1);
    assert(SPIFFS.backend_reads - backend_reads <= 1);
    // Transport service runs here, between every production pass. It must
    // never observe an unpublished replacement or a target-name gap.
    if (store.hasPendingContactWrites()) {
      assert(SPIFFS.exists("/contacts3") == existed);
      if (existed) assert(SPIFFS.files.at("/contacts3") == old);
    }
  }
  assert(SPIFFS.files.at("/contacts3") == image(h, filter));
  assert(!SPIFFS.exists("/contacts3.tmp"));
  assert(SPIFFS.missing_remove_logs == 0);
  return passes;
}

void bootCheck(const Host& expected, Filter filter = nullptr) {
  Host boot;
  DataStore after_reset(SPIFFS);
  after_reset.loadContacts(&boot);
  assert(!after_reset.hasIncompleteContactLoad());
  assert(image(boot) == image(expected, filter));
  assert(!after_reset.hasPendingContactWrites());
}

void boundedFullTableAndFirstSave() {
  SPIFFS = FakeFilesystem();
  SPIFFS.emulate_stdio = true;
  Host h = table(350);
  for (unsigned i = 0; i < h.contacts.size(); i += 7) h.contacts[i].type = 0;
  DataStore store(SPIFFS);
  assert(store.markContactDirty(h.contacts[0]));
  const auto expected = image(h, keepContact);
  const auto passes = drain(store, h, keepContact);
  assert(passes > h.contacts.size()); // serialization and CRC both yield
  assert(SPIFFS.largest_read <= 64);
  assert(SPIFFS.largest_backend_read <= 512); // background selected at Open
  assert(SPIFFS.largest_backend_write <= 502); // background selected at Configure
  assert(SPIFFS.largest_write == mesh::storage::CONTACT_RECORD_SIZE);
  assert(SPIFFS.removes == 0); // first publication has no backup/temp to delete
  assert(h.copies >= h.contacts.size());
  bootCheck(h, keepContact);
}

void backgroundCRCVerificationUsesBoundedBatches() {
  SPIFFS = FakeFilesystem();
  SPIFFS.emulate_stdio = true;
  Host h = table(347);
  DataStore store(SPIFFS);
  assert(store.markContactDirty(h.contacts[0]));
  unsigned crc_read_passes = 0, max_reads = 0, max_backend_reads = 0;
  unsigned passes = 0;
  while (store.hasPendingContactWrites()) {
    const unsigned reads = SPIFFS.reads, backend_reads = SPIFFS.backend_reads;
    assert(++passes < 2000);
    assert(store.serviceContactWrites(&h, nullptr));
    const unsigned read_count = SPIFFS.reads - reads;
    const unsigned backend_count = SPIFFS.backend_reads - backend_reads;
    if (read_count != 0) ++crc_read_passes;
    max_reads = std::max(max_reads, read_count);
    max_backend_reads = std::max(max_backend_reads, backend_count);
    assert(read_count <= 8 && backend_count <= 1);
  }
  // These are actual production DataStore calls, not a direct transaction
  // benchmark. Default one-chunk verification fails this cadence witness.
  assert(SPIFFS.reads == 825);
  assert(crc_read_passes == 104);
  assert(max_reads == 8 && max_backend_reads == 1);
  assert(SPIFFS.largest_read == 64 && SPIFFS.largest_backend_read == 512);
  assert(SPIFFS.backend_reads == 104);
  assert(SPIFFS.backend_writes == 106); // actual347-contact background502 job
  assert(SPIFFS.files.at("/contacts3") == image(h));
  bootCheck(h);
}

void deferredBeginCancellationAndCrashRecovery() {
  using Writer = mesh::ContactFileTransaction;
  // Observe/destruct at every startup boundary, including before any recovery
  // and after open but before setBufferSize. A stale temp is never authoritative.
  for (unsigned completed = 0; completed <= 7; ++completed) {
    SPIFFS = FakeFilesystem();
    SPIFFS.emulate_stdio = true;
    const Host h = table(1);
    const auto old = image(h);
    const std::vector<uint8_t> stale = {9, 8, 7};
    SPIFFS.files["/contacts3"] = old;
    SPIFFS.files["/contacts3.tmp"] = stale;
    unsigned before_destructor = 0;
    {
      Writer writer(&SPIFFS, "/contacts3", companionPathPresence, true);
      assert(SPIFFS.startupOperations() == 0 && !writer);
      for (unsigned pass = 0; pass < completed; ++pass) {
        const unsigned before = SPIFFS.startupOperations();
        const auto progress = writer.serviceBegin();
        assert(progress != Writer::BeginProgress::Failed);
        const unsigned startup_operations = SPIFFS.startupOperations() - before;
        assert(startup_operations <= 1);
        assert(SPIFFS.files.at("/contacts3") == old);
        // A physical reset at this boundary sees the prior complete image.
        FakeFilesystem rebooted;
        rebooted.files = SPIFFS.files;
        assert(Writer::recover(&rebooted, "/contacts3", companionPathPresence));
        assert(rebooted.files.at("/contacts3") == old);
      }
      if (completed < 7) {
        const unsigned before = SPIFFS.startupOperations();
        assert(!writer);
        assert(writer.write(old.data(), old.size()) == 0);
        assert(writer.serviceCommit() == Writer::CommitProgress::Failed);
        assert(!writer.commit());
        assert(SPIFFS.startupOperations() == before); // no implicit startup/cleanup
      } else {
        assert(writer);
      }
      before_destructor = SPIFFS.startupOperations();
    }
    if (completed < 6) {
      assert(SPIFFS.startupOperations() == before_destructor); // never owned a temp
    }
    if (completed < 5) assert(SPIFFS.files.at("/contacts3.tmp") == stale);
    else assert(!SPIFFS.exists("/contacts3.tmp"));
    assert(SPIFFS.files.at("/contacts3") == old && SPIFFS.missing_remove_logs == 0);
    bootCheck(h);
  }
  // An interrupted prior publication recovers identically with deferred setup.
  SPIFFS = FakeFilesystem();
  const Host h = table(1);
  const auto old = image(h);
  SPIFFS.files["/contacts3.bak"] = old;
  {
    Writer writer(&SPIFFS, "/contacts3", companionPathPresence, true);
    // Restoring a backup also verifies both names in separate setup passes.
    for (unsigned pass = 0; pass < 9; ++pass) {
      assert(writer.serviceBegin() != Writer::BeginProgress::Failed);
      FakeFilesystem rebooted;
      rebooted.files = SPIFFS.files;
      assert(Writer::recover(&rebooted, "/contacts3", companionPathPresence));
      assert(rebooted.files.at("/contacts3") == old);
    }
    assert(writer && SPIFFS.files.at("/contacts3") == old);
  }
  bootCheck(h);
}

void deferredBeginFaultsAndSynchronousDefaults() {
  using Writer = mesh::ContactFileTransaction;
  for (unsigned fault = 0; fault < 8; ++fault) {
    SPIFFS = FakeFilesystem();
    SPIFFS.emulate_stdio = true;
    const Host h = table(1);
    const auto old = image(h);
    const std::vector<uint8_t> stale = {1, 2, 3};
    SPIFFS.files["/contacts3"] = old;
    SPIFFS.files["/contacts3.tmp"] = stale;
    if (fault == 0) SPIFFS.fail_metadata_probe = 1; // target
    if (fault == 1) SPIFFS.fail_metadata_probe = 2; // backup
    if (fault == 2) {
      SPIFFS.files.erase("/contacts3");
      SPIFFS.files["/contacts3.bak"] = old;
      SPIFFS.fail_rename = 1; // failed interrupted-publication recovery
    }
    if (fault == 3) SPIFFS.fail_metadata_probe = 3; // stale temp
    if (fault == 4) SPIFFS.fail_remove = true;
    if (fault == 5 || fault == 6) SPIFFS.fail_create = true;
    if (fault == 6) SPIFFS.partial_create_failure = true;
    if (fault == 7) SPIFFS.fail_buffer_config = 1;
    {
      Writer writer(&SPIFFS, "/contacts3", companionPathPresence, true);
      auto progress = Writer::BeginProgress::Pending;
      for (unsigned pass = 0; pass < 8 && progress == Writer::BeginProgress::Pending; ++pass)
        progress = writer.serviceBegin();
      assert(progress == Writer::BeginProgress::Failed && !writer);
      assert(writer.write(old.data(), old.size()) == 0 && !writer.commit());
    }
    if (fault <= 4) assert(SPIFFS.files.at("/contacts3.tmp") == stale);
    else assert(!SPIFFS.exists("/contacts3.tmp")); // includes a failed partial create
    if (fault == 2) assert(SPIFFS.files.at("/contacts3.bak") == old);
    else assert(SPIFFS.files.at("/contacts3") == old);
    assert(SPIFFS.missing_remove_logs == 0);
    SPIFFS.fail_metadata_probe = SPIFFS.fail_rename = SPIFFS.fail_buffer_config = 0;
    SPIFFS.fail_create = SPIFFS.fail_remove = false;
    // All pre-existing callers still finish initialization inside construction.
    Writer synchronous(&SPIFFS, "/contacts3", companionPathPresence);
    assert(synchronous && SPIFFS.files.at("/contacts3") == old);
    assert(synchronous.write(old.data(), old.size()) == old.size());
    assert(synchronous.commit());
    assert(SPIFFS.files.at("/contacts3") == old);
    bootCheck(h);
  }
}

void mutationDuringEveryBeginStage() {
  for (unsigned completed = 0; completed <= 8; ++completed) {
    SPIFFS = FakeFilesystem();
    SPIFFS.emulate_stdio = true;
    Host h = table();
    const auto old = image(h);
    const std::vector<uint8_t> stale = {4, 5, 6};
    SPIFFS.files["/contacts3"] = old;
    SPIFFS.files["/contacts3.tmp"] = stale;
    DataStore store(SPIFFS);
    assert(store.markContactDirty(h.contacts[0]));
    assert(store.serviceContactWrites(&h, nullptr)); // allocate only
    assert(SPIFFS.startupOperations() == 0);
    for (unsigned pass = 0; pass < completed; ++pass)
      assert(store.serviceContactWrites(&h, nullptr));
    const unsigned opens = SPIFFS.opens, configurations = SPIFFS.buffer_config_calls;
    const unsigned before_cancel = SPIFFS.startupOperations();
    h.contacts[2] = contact(909);
    assert(store.markContactDirty(h.contacts[2]));
    assert(store.serviceContactWrites(&h, nullptr)); // guard precedes every begin step
    assert(store.hasPendingContactWrites());
    assert(SPIFFS.opens == opens && SPIFFS.buffer_config_calls == configurations);
    if (completed < 7) assert(SPIFFS.startupOperations() == before_cancel);
    if (completed < 6) assert(SPIFFS.files.at("/contacts3.tmp") == stale);
    else assert(!SPIFFS.exists("/contacts3.tmp"));
    assert(SPIFFS.files.at("/contacts3") == old);
    drain(store, h);
    bootCheck(h);
  }
}

void mutationRestart(unsigned phase) {
  SPIFFS = FakeFilesystem();
  SPIFFS.emulate_stdio = true;
  Host h = table();
  SPIFFS.files["/contacts3"] = image(h);
  const auto old = SPIFFS.files.at("/contacts3");
  DataStore store(SPIFFS);
  assert(store.markContactDirty(h.contacts[0]));
  unsigned passes = 0;
  while ((phase == 0 && SPIFFS.writes < 5)
      || (phase == 1 && (!store.verifying() || SPIFFS.reads == 0))
      || (phase == 2 && !store.readyToPublish())) {
    assert(++passes < 1000);
    assert(store.serviceContactWrites(&h, nullptr));
  }
  const auto removed = h.contacts[4];
  h.contacts.erase(h.contacts.begin() + 4);
  assert(store.releaseContact(removed));
  std::reverse(h.contacts.begin(), h.contacts.end());
  h.contacts[0] = contact(777);
  assert(store.markContactDirty(h.contacts[0]));
  assert(store.serviceContactWrites(&h, nullptr)); // discard obsolete job
  assert(store.hasPendingContactWrites());
  assert(!SPIFFS.exists("/contacts3.tmp"));
  assert(SPIFFS.files.at("/contacts3") == old);
  drain(store, h);
  bootCheck(h);
}

void faultAndRetry(unsigned fault) {
  SPIFFS = FakeFilesystem();
  SPIFFS.emulate_stdio = true;
  Host h = table();
  SPIFFS.files["/contacts3"] = image(h);
  const auto old = SPIFFS.files.at("/contacts3");
  DataStore store(SPIFFS);
  h.contacts[2] = contact(888);
  assert(store.markContactDirty(h.contacts[2]));
  if (fault == 0) SPIFFS.max_write = 50;
  if (fault == 1) SPIFFS.fail_create = true;
  if (fault == 7) SPIFFS.metadata_error = true;
  if (fault == 8 || fault == 9) SPIFFS.fail_buffer_config = fault - 7;
  bool injected = fault == 0 || fault == 1 || fault >= 7;
  bool success = true;
  for (unsigned pass = 0; pass < 1000 && success; ++pass) {
    if (!injected && store.verifying()
        && SPIFFS.files.at("/contacts3.tmp").size() == image(h).size()) {
      if (fault == 2) SPIFFS.fail_read = "/contacts3.tmp";
      if (fault == 3) SPIFFS.files.at("/contacts3.tmp")[20] ^= 1;
      if (fault == 4) SPIFFS.files.at("/contacts3.tmp").pop_back();
      if (fault <= 4) injected = true;
    }
    if (!injected && store.readyToPublish()) {
      SPIFFS.fail_rename = SPIFFS.renames + (fault == 5 ? 1 : 2);
      injected = true;
    }
    success = store.serviceContactWrites(&h, nullptr);
  }
  assert(injected && !success);
  assert(!store.activeJob()); // no Ready/_ok=false job can spin in initialization
  assert(store.hasPendingContactWrites());
  assert(SPIFFS.files.at("/contacts3") == old);
  assert(!SPIFFS.exists("/contacts3.tmp"));
  assert(SPIFFS.missing_remove_logs == 0);
  SPIFFS.max_write = std::numeric_limits<size_t>::max();
  SPIFFS.fail_create = SPIFFS.metadata_error = false;
  SPIFFS.fail_read.clear();
  SPIFFS.fail_rename = 0;
  SPIFFS.fail_buffer_config = 0;
  drain(store, h);
  bootCheck(h);
}

void syncFlushAndFilterChange() {
  SPIFFS = FakeFilesystem();
  SPIFFS.emulate_stdio = true;
  Host h = table();
  SPIFFS.files["/contacts3"] = image(h);
  DataStore store(SPIFFS);
  assert(store.markContactDirty(h.contacts[0]));
  for (unsigned i = 0; i < 10; ++i) assert(store.serviceContactWrites(&h, nullptr));
  h.contacts[0] = contact(901);
  assert(store.markContactDirty(h.contacts[0]));
  assert(store.flushContactWrites(&h, keepEven));
  assert(!store.hasPendingContactWrites());
  assert(SPIFFS.files.at("/contacts3") == image(h, keepEven));
  bootCheck(h, keepEven);
  assert(store.markContactDirty(h.contacts[0]));
  for (unsigned i = 0; i < 10; ++i) assert(store.serviceContactWrites(&h, keepEven));
  assert(store.serviceContactWrites(&h, keepContact)); // changed filter cancels
  assert(store.hasPendingContactWrites() && !SPIFFS.exists("/contacts3.tmp"));
  drain(store, h, keepContact);
  bootCheck(h, keepContact);
  Host replacement = table(17);
  assert(store.markContactDirty(h.contacts[0]));
  for (unsigned i = 0; i < 10; ++i) assert(store.serviceContactWrites(&h, nullptr));
  assert(store.serviceContactWrites(&replacement, nullptr)); // changed host cancels
  assert(store.hasPendingContactWrites() && !SPIFFS.exists("/contacts3.tmp"));
  drain(store, replacement);
  bootCheck(replacement);
}

void realBackupCleanupFailure() {
  SPIFFS = FakeFilesystem();
  SPIFFS.emulate_stdio = true;
  Host h = table();
  const auto old = image(h);
  SPIFFS.files["/contacts3"] = old;
  SPIFFS.files["/contacts3.bak"] = {42};
  DataStore store(SPIFFS);
  h.contacts[1] = contact(987);
  assert(store.markContactDirty(h.contacts[1]));
  SPIFFS.fail_remove = true;
  bool success = true;
  for (unsigned i = 0; i < 1000 && success; ++i) {
    success = store.serviceContactWrites(&h, nullptr);
  }
  assert(!success && store.hasPendingContactWrites());
  assert(SPIFFS.files.at("/contacts3") == old);
  assert(SPIFFS.files.at("/contacts3.bak") == std::vector<uint8_t>({42}));
  // Retirement fails before this transaction owns or opens a new temp.
  assert(!SPIFFS.exists("/contacts3.tmp") && SPIFFS.missing_remove_logs == 0);
  assert(SPIFFS.writes == 0 && SPIFFS.opens == 0 && !store.activeJob());
  SPIFFS.fail_remove = false;
  drain(store, h);
  bootCheck(h);
}

void rebootAbortRecoveryAndDurableRemoval() {
  SPIFFS = FakeFilesystem();
  SPIFFS.emulate_stdio = true;
  Host h = table();
  const auto old = image(h);
  SPIFFS.files["/contacts3"] = old;
  {
    DataStore store(SPIFFS);
    assert(store.markContactDirty(h.contacts[0]));
    for (unsigned i = 0; i < 7; ++i) assert(store.serviceContactWrites(&h, nullptr));
  } // CPU reset/destruction must abandon an incomplete lazy image.
  assert(SPIFFS.files.at("/contacts3") == old);
  assert(!SPIFFS.exists("/contacts3.tmp"));
  bootCheck(h);
  assert(SPIFFS.rename("/contacts3", "/contacts3.bak")); // interrupted publication
  bootCheck(h); // production load recovers the prior complete table
  {
    DataStore store(SPIFFS);
    const auto removed = h.contacts[8];
    h.contacts.erase(h.contacts.begin() + 8);
    assert(store.releaseContact(removed));
    drain(store, h);
  }
  bootCheck(h); // committed removal survives a new DataStore instance
  SPIFFS.fail_read = "/contacts3";
  Host incomplete;
  DataStore failed_boot(SPIFFS);
  failed_boot.loadContacts(&incomplete);
  assert(failed_boot.hasIncompleteContactLoad());
  assert(failed_boot.contactRecoveryPending());
  const auto before = SPIFFS.files;
  assert(!failed_boot.serviceContactWrites(&incomplete, nullptr));
  assert(!failed_boot.flushContactWrites(&incomplete, nullptr));
  assert(SPIFFS.files == before);
  SPIFFS.fail_read.clear();
  failed_boot.recoverContacts(&incomplete);
  assert(!failed_boot.hasIncompleteContactLoad() && !failed_boot.contactRecoveryPending());
  // A fully readable prior backup may have replaced the unread target.
  assert(image(incomplete) == before.at("/contacts3") && SPIFFS.files == before);
}

void contactRecoveryPreservesSettingsAndResourceRefusals() {
  for (unsigned fault = 0; fault < 3; ++fault) {
    SPIFFS = FakeFilesystem();
    Host original = table(32), loaded;
    SPIFFS.files["/contacts3"] = image(original);
    for (const char* name : {"/_main.id", "/new_prefs", "/channels2", "/fleet_channel",
                             "/mesh_mt", "/mesh_eth"}) SPIFFS.files[name] = {7, 3, 9};
    if (fault == 1) {
      SPIFFS.files["/contacts3"] = {8, 9};
      SPIFFS.files["/contacts3.bak"] = {4};
    } else SPIFFS.fail_read = "/contacts3";
    const auto durable = SPIFFS.files;
    DataStore store(SPIFFS);
    store.loadContacts(&loaded);
    assert(store.hasIncompleteContactLoad() && store.contactRecoveryPending());
    assert(loaded.recovery_resets == 0);
    loaded.recovery_reset_allowed = false;
    store.recoverContacts(&loaded);
    assert(store.contactRecoveryPending() && SPIFFS.files == durable);
    loaded.recovery_reset_allowed = true;
    SPIFFS.fail_remove = fault == 2;
    store.recoverContacts(&loaded);
    assert(!store.contactRecoveryPending() && !store.hasIncompleteContactLoad());
    assert(loaded.contacts.empty() && loaded.recovery_resets > 0);
    auto expected = durable;
    if (fault != 2) {
      expected.erase("/contacts3");
      expected.erase("/contacts3.bak");
    }
    assert(SPIFFS.files == expected); // No archive or broad settings reset.
    const unsigned removes = SPIFFS.removes;
    store.recoverContacts(&loaded);
    assert(SPIFFS.removes == removes && SPIFFS.files == expected);
    SPIFFS.fail_read.clear(); SPIFFS.fail_remove = false;
    if (fault == 2) {
      assert(!store.saveContacts(&loaded, nullptr)); // Failed media stays volatile this boot.
      assert(SPIFFS.files == expected);
    } else {
      loaded.contacts.push_back(contact(909));
      assert(store.saveContacts(&loaded, nullptr));
      bootCheck(loaded);
    }
  }
  SPIFFS = FakeFilesystem();
  Host original = table(32), limited;
  SPIFFS.files["/contacts3"] = image(original);
  const auto durable = SPIFFS.files;
  limited.capacity = 20;
  DataStore store(SPIFFS);
  store.loadContacts(&limited);
  assert(store.hasIncompleteContactLoad() && !store.contactRecoveryPending());
  const unsigned writes = SPIFFS.writes, removes = SPIFFS.removes;
  store.recoverContacts(&limited);
  assert(!store.saveContacts(&limited, nullptr));
  assert(SPIFFS.files == durable && SPIFFS.writes == writes && SPIFFS.removes == removes);
  assert(limited.recovery_resets == 0);
}

void validBackupSurvivesFailedPromotion() {
  for (bool removal_failure : {false, true}) {
    SPIFFS = FakeFilesystem();
    Host original = table(32), loaded;
    const auto valid = image(original);
    SPIFFS.files["/contacts3"] = {9};
    SPIFFS.files["/contacts3.bak"] = valid;
    SPIFFS.fail_remove = removal_failure;
    if (!removal_failure) SPIFFS.fail_rename = SPIFFS.renames + 1;
    DataStore store(SPIFFS);
    store.loadContacts(&loaded);
    assert(!store.hasIncompleteContactLoad() && !store.contactRecoveryPending());
    assert(image(loaded) == valid && loaded.recovery_resets == 0);
    assert(SPIFFS.files.at("/contacts3.bak") == valid);
    if (!removal_failure) SPIFFS.fail_rename = SPIFFS.renames + 1;
    assert(!store.saveContacts(&loaded, nullptr));
    assert(SPIFFS.files.at("/contacts3.bak") == valid);
    SPIFFS.fail_remove = false; SPIFFS.fail_rename = 0;
    assert(store.saveContacts(&loaded, nullptr));
    bootCheck(loaded);
  }
  SPIFFS = FakeFilesystem();
  Host original = table(32), loaded;
  const auto valid = image(original);
  SPIFFS.files["/contacts3"] = {9};
  SPIFFS.files["/contacts3.bak"] = valid;
  SPIFFS.fail_rename = SPIFFS.renames + 1;
  DataStore store(SPIFFS);
  store.loadContacts(&loaded);
  assert(image(loaded) == valid && !store.hasIncompleteContactLoad());
  SPIFFS.fail_rename = 0;
  SPIFFS.after_rename = [](FakeFilesystem& fs, const char* path) {
    if (!strcmp(path, "/contacts3")) fs.metadata_error = true;
  };
  assert(!store.saveContacts(&loaded, nullptr));
  assert(SPIFFS.files.at("/contacts3") == valid && !SPIFFS.exists("/contacts3.bak"));
  SPIFFS.metadata_error = false; SPIFFS.after_rename = {};
  assert(store.saveContacts(&loaded, nullptr));
  bootCheck(loaded);
}

void expectedAbsenceAndRealDeleteFailures() {
  SPIFFS = FakeFilesystem();
  SPIFFS.emulate_stdio = true;
  DataStore store(SPIFFS);
  uint8_t key[32] = {1, 2, 3};
  char path[64];
  makeBlobPath(key, sizeof(key), path, sizeof(path));
  assert(store.deleteBlobByKey(key, sizeof(key)));
  assert(SPIFFS.removes == 0 && SPIFFS.missing_remove_logs == 0);
  SPIFFS.metadata_error = true;
  assert(!store.deleteBlobByKey(key, sizeof(key)));
  assert(SPIFFS.removes == 0);
  SPIFFS.metadata_error = false;
  SPIFFS.files[path] = {7, 8};
  SPIFFS.fail_remove = true;
  assert(!store.deleteBlobByKey(key, sizeof(key)));
  assert(SPIFFS.exists(path));
  SPIFFS.fail_remove = false;
  assert(store.deleteBlobByKey(key, sizeof(key)));
  assert(!SPIFFS.exists(path));
  assert(store.deleteBlobByKey(key, sizeof(key)));
  assert(SPIFFS.removes == 2 && SPIFFS.missing_remove_logs == 0);
  // Failed construction/destruction must not remove a temp that never existed.
  SPIFFS.fail_create = true;
  { mesh::ContactFileTransaction writer(&SPIFFS, "/empty", companionPathPresence);
    assert(!writer); }
  assert(SPIFFS.removes == 2 && SPIFFS.missing_remove_logs == 0);
  SPIFFS.metadata_error = true;
  { mesh::ContactFileTransaction writer(&SPIFFS, "/unreadable", companionPathPresence);
    assert(!writer); }
  assert(SPIFFS.removes == 2 && SPIFFS.missing_remove_logs == 0);
}

// Use the production transaction and the same complete 347-contact image for
// old/new stdio admission measurements. Backend calls model fwrite/fread
// buffering, not physical SPIFFS flash operations or hardware latency.
void bufferedAdmissionBenchmark() {
  SPIFFS = FakeFilesystem();
  SPIFFS.emulate_stdio = true;
  const auto bytes = image(table(347));
  mesh::ContactFileTransaction writer(&SPIFFS, "/contacts3", companionPathPresence);
  assert(writer);
  for (size_t offset = 0; offset < bytes.size(); offset += mesh::storage::CONTACT_RECORD_SIZE) {
    const auto before = SPIFFS.backend_writes;
    assert(writer.write(bytes.data() + offset, mesh::storage::CONTACT_RECORD_SIZE)
           == mesh::storage::CONTACT_RECORD_SIZE);
    assert(SPIFFS.backend_writes - before <= 1);
  }
  auto progress = mesh::ContactFileTransaction::CommitProgress::Pending;
  unsigned passes = 0;
  while (progress == mesh::ContactFileTransaction::CommitProgress::Pending) {
    const auto before_reads = SPIFFS.backend_reads, before_writes = SPIFFS.backend_writes;
    assert(++passes < 1000);
    progress = writer.serviceCommit();
    assert(SPIFFS.backend_reads - before_reads <= 1);
    assert(SPIFFS.backend_writes - before_writes <= 1);
  }
  assert(progress == mesh::ContactFileTransaction::CommitProgress::Succeeded);
  assert(SPIFFS.files.at("/contacts3") == bytes);
  assert(SPIFFS.largest_read == 64);
  assert(SPIFFS.largest_write == mesh::storage::CONTACT_RECORD_SIZE);
  printf("{\"bytes\":%zu,\"logical_writes\":%u,\"logical_reads\":%u,"
         "\"backend_writes\":%u,\"backend_reads\":%u,\"max_backend_write\":%zu,"
         "\"max_backend_read\":%zu,\"commit_passes\":%u,\"transaction_size\":%zu}\n",
         bytes.size(), SPIFFS.writes, SPIFFS.reads, SPIFFS.backend_writes,
         SPIFFS.backend_reads, SPIFFS.largest_backend_write,
         SPIFFS.largest_backend_read, passes, sizeof(writer));
}

void boundedCancellationFlush() {
  // 251 records land exactly on a 251-byte stdio boundary because the record
  // size is 152. Both sides of that boundary must preserve the committed
  // image and flush at most one partial buffer when abandoning the temp.
  for (unsigned records : {1U, 250U, 251U, 252U}) {
    SPIFFS = FakeFilesystem();
    SPIFFS.emulate_stdio = true;
    const auto old = image(table(1));
    const auto bytes = image(table(records));
    SPIFFS.files["/contacts3"] = old;
    unsigned before_cancel = 0;
    {
      mesh::ContactFileTransaction writer(&SPIFFS, "/contacts3", companionPathPresence);
      assert(writer);
      for (size_t offset = 0; offset < bytes.size(); offset += mesh::storage::CONTACT_RECORD_SIZE) {
        const auto before = SPIFFS.backend_writes;
        assert(writer.write(bytes.data() + offset, mesh::storage::CONTACT_RECORD_SIZE)
               == mesh::storage::CONTACT_RECORD_SIZE);
        assert(SPIFFS.backend_writes - before <= 1);
      }
      assert(SPIFFS.backend_writes == bytes.size() / 251);
      assert(SPIFFS.files.at("/contacts3") == old);
      before_cancel = SPIFFS.backend_writes;
    }
    assert(SPIFFS.backend_writes - before_cancel == (bytes.size() % 251 != 0));
    assert(SPIFFS.backend_writes == (bytes.size() + 250) / 251);
    assert(SPIFFS.largest_backend_write <= 251);
    assert(SPIFFS.files.at("/contacts3") == old);
    assert(!SPIFFS.exists("/contacts3.tmp"));
  }
}


void deferredCleanupThroughActualDataStore() {
  SPIFFS = FakeFilesystem();
  SPIFFS.emulate_stdio = true;
  Host h = table(32);
  const auto old = image(h);
  const std::vector<uint8_t> older = {9, 8, 7};
  SPIFFS.files["/contacts3"] = old;
  SPIFFS.files["/contacts3.bak"] = older;
  DataStore store(SPIFFS);
  h.contacts[0] = contact(904);
  assert(store.markContactDirty(h.contacts[0]));
  assert(store.serviceContactWrites(&h, nullptr)); // allocation only
  for (unsigned step = 0; step < 3; ++step)
    assert(store.serviceContactWrites(&h, nullptr)); // Target, Backup, Recover
  assert(SPIFFS.files.at("/contacts3.bak") == older);
  const unsigned before = SPIFFS.startupOperations();
  assert(store.serviceContactWrites(&h, nullptr)); // standalone RetireBackup
  assert(SPIFFS.startupOperations() - before == 1);
  assert(SPIFFS.files.at("/contacts3") == old && !SPIFFS.exists("/contacts3.bak"));
  drain(store, h);
  assert(SPIFFS.exists("/contacts3.bak"));
  assert(!store.hasPendingContactWrites() && SPIFFS.files.at("/contacts3.bak") == old);
  // A mutation after the completed job must retire that backup before replacing
  // the newest authoritative target, with no stale index publication.
  const auto prior = image(h);
  h.contacts.erase(h.contacts.begin() + 4);
  assert(store.releaseContact(contact(4)));
  h.contacts[0] = contact(905);
  assert(store.markContactDirty(h.contacts[0]));
  drain(store, h);
  assert(SPIFFS.files.at("/contacts3.bak") == prior);
  bootCheck(h);
}

int main() {
#if defined(CONTACT_BUFFER_BENCHMARK)
  bufferedAdmissionBenchmark();
#else
  deferredCleanupThroughActualDataStore();
  boundedFullTableAndFirstSave();
  backgroundCRCVerificationUsesBoundedBatches();
  boundedCancellationFlush();
  deferredBeginCancellationAndCrashRecovery();
  deferredBeginFaultsAndSynchronousDefaults();
  mutationDuringEveryBeginStage();
  for (unsigned phase = 0; phase < 3; ++phase) mutationRestart(phase);
  for (unsigned fault = 0; fault < 10; ++fault) faultAndRetry(fault);
  syncFlushAndFilterChange();
  realBackupCleanupFailure();
  rebootAbortRecoveryAndDurableRemoval();
  contactRecoveryPreservesSettingsAndResourceRefusals();
  validBackupSurvivesFailedPromotion();
  expectedAbsenceAndRealDeleteFailures();
#endif
}
