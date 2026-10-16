#pragma once

#include <helpers/IdentityStore.h>
#include <helpers/ContactInfo.h>
#include <helpers/ChannelDetails.h>
#include <helpers/PersistentStoreFormat.h>
#include "NodePrefs.h"
#if MESH_CONTACT_CACHE
#include <helpers/ContactSecretCache.h>
#endif
#include <helpers/CompanionReaderConfig.h>
#if defined(ESP32_PLATFORM)
namespace mesh { class ContactFileTransaction; }
#endif
#if COMPANION_FEATURE_READER
#include <helpers/bible/Reader.h>
#endif

class DataStoreHost {
public:
  virtual bool onContactLoaded(const ContactInfo& contact) =0;
  virtual bool getContactForSave(uint32_t idx, ContactInfo& contact) =0;
  virtual ContactInfo* getContactForStore(uint32_t idx) =0;
  virtual void onContactCacheFlushed() {}
  // Only called by explicit recovery between packet/app callbacks.
  virtual bool resetContactsForRecovery() { return false; }
  virtual bool onChannelLoaded(uint8_t channel_idx, const ChannelDetails& ch) =0;
  virtual bool getChannelForSave(uint8_t channel_idx, ChannelDetails& ch) =0;
};

class DataStore
#if MESH_CONTACT_CACHE
  : public mesh::ContactPathBackend
#if MESH_CONTACT_SECRET_FLASH_CACHE
  , public mesh::ContactSecretBackend
#endif
#endif
{
  FILESYSTEM* _fs;
  FILESYSTEM* _fsExtra;
  // Keep the configured secondary even when normal I/O falls back to the
  // primary filesystem. Boot recovery and explicit local repair must still
  // be able to address the inactive on-chip ExtraFS.
  FILESYSTEM* _configuredFsExtra;
  mesh::RTCClock* _clock;
  IdentityStore identity_store;
  bool _identity_creation_blocked = false;
  bool _prefs_load_incomplete = false;
  // Contact-only recovery must not reinterpret a channel/migration error as
  // permission to discard contacts. No recovery files or boot counters needed.
  enum : uint8_t { ContactRecoveryPending = 1, ContactRecoveryLegacy = 2,
                   ContactRecoveryBackup = 4, ContactStorageUnavailable = 8,
                   ContactMigrationDeferred = 16,
#if MESH_CONTACT_CACHE
                   ContactRecoveryRawOnly = 32,
#endif
  };
  uint8_t _contact_recovery = 0;
  void queueContactRecovery(bool legacy = false) {
#if MESH_CONTACT_CACHE
    // Source/publication uncertainty always supersedes a raw-read retry.
    _contact_recovery &= ~ContactRecoveryRawOnly;
#endif
    _contact_recovery |= ContactRecoveryPending
        | (legacy ? ContactRecoveryLegacy : 0);
  }
#if MESH_CONTACT_CACHE
  void queueContactReadRecovery(bool legacy = false) {
    if (!(_contact_recovery & ContactRecoveryPending))
      _contact_recovery |= ContactRecoveryRawOnly;
    _contact_recovery |= ContactRecoveryPending
        | (legacy ? ContactRecoveryLegacy : 0);
  }
#endif
  void clearContactLoadFailure();
#if !defined(NRF52_PLATFORM)
  const char* selectContactFile(bool& present);
  bool prepareContactSave();
#endif
#if defined(ESP32_PLATFORM)
  const char* _prefs_recovery_source = nullptr;
  const char* _channel_recovery_source = nullptr;
  mesh::ContactFileTransaction* _contact_write = nullptr;
  DataStoreHost* _contact_write_host = nullptr;
  bool (*_contact_write_filter)(const ContactInfo&) = nullptr;
  uint32_t _contact_write_revision = 0;
  uint32_t _contact_write_active_revision = 0;
  uint32_t _contact_write_index = 0;
  bool _contact_write_verifying = false;
  bool _contact_write_requested = false;
  bool _contact_write_servicing = false;
  void cancelContactWrite();
  bool serviceContactWrite(DataStoreHost* host,
                          bool (*filter)(const ContactInfo&));
#endif
#if defined(ESP32_PLATFORM)
  // Advert packets are reconstructable cache data. Keep accepted updates in
  // RAM while their filesystem operations run on separate loop passes.
  struct AdvertWriteState {
    static constexpr uint8_t CAPACITY = 4;
    struct Slot {
      uint8_t key[8] = {};
      uint8_t bytes[255] = {};
      uint32_t revision = 0;
      uint8_t key_len = 0;
      uint8_t len = 0;
    } slots[CAPACITY];
    enum class Stage : uint8_t {
      Idle, TargetProbe, BackupProbe, RestoreBackup, RemoveBackup,
      TempProbe, RemoveTemp, OpenWrite, ConfigureWrite, Write, CloseWrite,
      OpenVerify, ConfigureVerify, VerifySize, Verify, CloseVerify,
      PublishTargetProbe, PublishBackupProbe, PublishRemoveBackup,
      PublishBackup, Publish, PublishCleanup, CancelClose
    } stage = Stage::Idle;
    File file;
    uint8_t scratch[64] = {};
    uint8_t active_key[8] = {};
    uint8_t active_key_len = 0;
    char target[24] = {};
    char backup[28] = {};
    uint32_t revision = 0;
    uint32_t active_revision = 0;
    uint32_t retry_at = 0;
    uint8_t active = CAPACITY;
    uint8_t next = 0;
    uint8_t offset = 0;
    uint8_t failures = 0;
    bool target_present = false;
    bool backup_present = false;
    bool temp_present = false;
    bool cancelling = false;
    bool synchronous_io = false;
    ~AdvertWriteState() { file.close(); }
    void pause() {
      file.close();
      stage = Stage::Idle;
      active = CAPACITY;
      retry_at = 0;
    }
    void clear() {
      pause();
      for (auto& slot : slots) slot.len = 0;
      failures = 0;
    }
  } _advert_write;
  void invalidateAdvertWrite(const uint8_t key[], int key_len);
  void retireAdvertWrite(const uint8_t key[], int key_len);
#endif
  bool _channel_load_incomplete = false;
#if !defined(NRF52_PLATFORM)
#if !MESH_CONTACT_CACHE
  bool _uncached_contact_load_incomplete = false;
#endif
#endif
#if MESH_CONTACT_CACHE
  DataStoreHost* _cache_host = nullptr;
  bool _cache_load_incomplete = false;
#if defined(ESP32_PLATFORM)
  File _contact_path_reader;
#endif
  bool readStoredPath(uint16_t source, uint8_t path[64]) override;
  bool flushCachedPaths() override;
#if defined(ESP32_PLATFORM)
  bool cancelCooperativeWrite() override;
#endif
#if MESH_CONTACT_SECRET_FLASH_CACHE
  uint32_t _secret_retry_at = 0;
  bool readSavedSecret(const uint8_t peer[32], const uint8_t identity[32],
                       uint8_t secret[32]) override;
  bool saveSecret(const uint8_t peer[32], const uint8_t identity[32],
                  const uint8_t secret[32]) override;
  uint16_t secretSlot(const uint8_t peer[32]) const;
#endif
#endif

#if defined(NRF52_PLATFORM)
  mesh::storage::ContactSlotMap _contact_slots;
  mesh::storage::DirtyPageSet _dirty_contact_pages;
  mesh::storage::DirtyPageSet _unread_contact_pages;
  bool _contact_load_incomplete = false;
  bool _primary_storage_unavailable = false;
  bool _secondary_authority_unknown = false;
  bool _volatile_primary_fs = false;
  uint32_t _contact_page_generations[mesh::storage::CONTACT_PAGE_COUNT];
  bool _legacy_contacts_pending_cleanup;
  bool _legacy_migration_ready;
  uint16_t _legacy_contact_count;

  bool prepareLegacyContactMigration();
  bool loadContactPages(DataStoreHost* host, uint16_t minimum_slot,
                        uint32_t expected_page_mask);
  bool writeContactPage(DataStoreHost* host, uint8_t page,
                        bool (*filter)(const ContactInfo& c));
  bool truncateLegacyContacts(uint16_t remaining_contacts);
  void resetContactPageState(bool clear_incomplete = false);
#if defined(EXTRAFS) && !defined(QSPIFLASH)
  bool recoverInternalExtraFSOnBoot();
  bool reinitializeInternalExtraFS(bool scan_physical_pages = false);
#endif
#endif

  bool loadPrefsInt(const char *filename, CompanionNodePrefs& prefs,
                    double& node_lat, double& node_lon);
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  void checkAdvBlobFile();
#endif

public:
  DataStore(FILESYSTEM& fs, mesh::RTCClock& clock);
  ~DataStore();
  DataStore(FILESYSTEM& fs, FILESYSTEM& fsExtra, mesh::RTCClock& clock);
  void begin();
  bool formatFileSystem();
  bool repairInternalExtraFS();
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
  bool requestInternalExtraFSBootScan();
  bool formatInternalExtraFSHealth(char* reply, size_t reply_size) const;
#endif
  FILESYSTEM* getPrimaryFS() const { return _fs; }
  FILESYSTEM* getSecondaryFS() const { return _fsExtra; }
#if defined(NRF52_PLATFORM)
  void useVolatilePrimaryFS(FILESYSTEM& fs);
  bool isVolatilePrimaryFS() const { return _volatile_primary_fs; }
  // Meshtext settings belong to the primary store, alongside the identity.
  // An absent record is size zero; unreadable or volatile storage is failure.
  bool loadMeshtextSettings(uint8_t image[40], size_t& size);
  bool saveMeshtextSettings(const uint8_t image[40]);
  bool eraseMeshtextSettings();
#endif
  void markPrimaryFSUnavailable();
  void disableSecondaryFS(bool authority_unknown = true);
  bool loadMainIdentity(mesh::LocalIdentity &identity);
  bool canCreateMainIdentity() const;
  bool saveMainIdentity(const mesh::LocalIdentity &identity);
  bool loadPrefs(CompanionNodePrefs& prefs, double& node_lat,
                 double& node_lon);
  bool savePrefs(const CompanionNodePrefs& prefs, double node_lat, double node_lon);
#if COMPANION_FEATURE_READER
  bool loadReaderBookmark(mesh::bible::Position& pos);
  bool saveReaderBookmark(mesh::bible::Position pos);
#endif
  void loadContacts(DataStoreHost* host);
  bool contactRecoveryPending() const {
    return (_contact_recovery & ContactRecoveryPending) != 0;
  }
  bool contactsAreVolatile() const {
    return (_contact_recovery & (ContactStorageUnavailable | ContactMigrationDeferred)) != 0;
  }
  void recoverContacts(DataStoreHost* host);
  bool saveContacts(DataStoreHost* host, bool (*filter)(const ContactInfo& c) = NULL);
  bool markContactDirty(const ContactInfo& contact);
  bool releaseContact(const ContactInfo& contact);
  bool restoreContactSlot(const ContactInfo& contact, uint16_t slot);
  bool serviceContactWrites(DataStoreHost* host, bool (*filter)(const ContactInfo& c) = NULL);
  bool flushContactWrites(DataStoreHost* host, bool (*filter)(const ContactInfo& c) = NULL);
  bool hasPendingContactWrites() const;
  bool hasIncompleteContactLoad() const;
  bool hasIncompleteChannelLoad() const { return _channel_load_incomplete; }
  void loadChannels(DataStoreHost* host);
  bool saveChannels(DataStoreHost* host);
  bool migrateToSecondaryFS();
  uint8_t getBlobByKey(const uint8_t key[], int key_len, uint8_t dest_buf[]);
  bool putBlobByKey(const uint8_t key[], int key_len, const uint8_t src_buf[], uint8_t len);
  bool deleteBlobByKey(const uint8_t key[], int key_len);
#if defined(ESP32_PLATFORM)
  bool queueAdvertByKey(const uint8_t key[], int key_len,
                        const uint8_t src_buf[], uint8_t len);
  bool serviceAdvertWrites(uint32_t now);
  bool flushAdvertWrites(uint32_t now = 0);
  bool hasPendingAdvertWrites() const;
  bool isAdvertWriteDue(uint32_t now) const;
  bool consumeSynchronousAdvertIO();
#endif
  File openRead(const char* filename);
  File openRead(FILESYSTEM* fs, const char* filename);
  File openDirectory(const char* path);
  File openDirectory(FILESYSTEM* fs, const char* path);
  bool removeFile(const char* filename);
  bool removeFile(FILESYSTEM* fs, const char* filename);
  uint32_t getStorageUsedKb() const;
  uint32_t getStorageTotalKb() const;

private:
  FILESYSTEM* _getContactsChannelsFS() const { if (_fsExtra) return _fsExtra; return _fs;};
};
