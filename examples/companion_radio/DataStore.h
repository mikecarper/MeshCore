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
#if COMPANION_FEATURE_READER
#include <helpers/bible/Reader.h>
#endif

class DataStoreHost {
public:
  virtual bool onContactLoaded(const ContactInfo& contact) =0;
  virtual bool getContactForSave(uint32_t idx, ContactInfo& contact) =0;
  virtual ContactInfo* getContactForStore(uint32_t idx) =0;
  virtual void onContactCacheFlushed() {}
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
#if defined(ESP32_PLATFORM)
  const char* _prefs_recovery_source = nullptr;
  const char* _channel_recovery_source = nullptr;
#endif
#if !defined(NRF52_PLATFORM)
  bool _channel_load_incomplete = false;
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
  bool saveContacts(DataStoreHost* host, bool (*filter)(const ContactInfo& c) = NULL);
  bool markContactDirty(const ContactInfo& contact);
  bool releaseContact(const ContactInfo& contact);
  bool restoreContactSlot(const ContactInfo& contact, uint16_t slot);
  bool serviceContactWrites(DataStoreHost* host, bool (*filter)(const ContactInfo& c) = NULL);
  bool flushContactWrites(DataStoreHost* host, bool (*filter)(const ContactInfo& c) = NULL);
  bool hasPendingContactWrites() const;
  bool hasIncompleteContactLoad() const;
  void loadChannels(DataStoreHost* host);
  bool saveChannels(DataStoreHost* host);
  bool migrateToSecondaryFS();
  uint8_t getBlobByKey(const uint8_t key[], int key_len, uint8_t dest_buf[]);
  bool putBlobByKey(const uint8_t key[], int key_len, const uint8_t src_buf[], uint8_t len);
  bool deleteBlobByKey(const uint8_t key[], int key_len);
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
