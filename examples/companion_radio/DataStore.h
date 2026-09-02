#pragma once

#include <helpers/IdentityStore.h>
#include <helpers/ContactInfo.h>
#include <helpers/ChannelDetails.h>
#include <helpers/PersistentStoreFormat.h>
#include "NodePrefs.h"

class DataStoreHost {
public:
  virtual bool onContactLoaded(const ContactInfo& contact) =0;
  virtual bool getContactForSave(uint32_t idx, ContactInfo& contact) =0;
  virtual ContactInfo* getContactForStore(uint32_t idx) =0;
  virtual bool onChannelLoaded(uint8_t channel_idx, const ChannelDetails& ch) =0;
  virtual bool getChannelForSave(uint8_t channel_idx, ChannelDetails& ch) =0;
};

class DataStore {
  FILESYSTEM* _fs;
  FILESYSTEM* _fsExtra;
  // Keep the configured secondary even when normal I/O falls back to the
  // primary filesystem. An explicit local repair/factory reset must still be
  // able to address the inactive on-chip ExtraFS.
  FILESYSTEM* _configuredFsExtra;
  mesh::RTCClock* _clock;
  IdentityStore identity_store;

#if defined(NRF52_PLATFORM)
  mesh::storage::ContactSlotMap _contact_slots;
  mesh::storage::DirtyPageSet _dirty_contact_pages;
  uint32_t _contact_page_generations[mesh::storage::CONTACT_PAGE_COUNT];
  bool _legacy_contacts_pending_cleanup;
  bool _legacy_migration_ready;
  uint16_t _legacy_contact_count;

  bool prepareLegacyContactMigration();
  bool loadContactPages(DataStoreHost* host, uint16_t minimum_slot = 0);
  bool writeContactPage(DataStoreHost* host, uint8_t page,
                        bool (*filter)(const ContactInfo& c));
  bool truncateLegacyContacts(uint16_t remaining_contacts);
  void resetContactPageState();
#if defined(EXTRAFS) && !defined(QSPIFLASH)
  bool reinitializeInternalExtraFS();
#endif
#endif

  void loadPrefsInt(const char *filename, CompanionNodePrefs& prefs, double& node_lat, double& node_lon);
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  void checkAdvBlobFile();
#endif

public:
  DataStore(FILESYSTEM& fs, mesh::RTCClock& clock);
  DataStore(FILESYSTEM& fs, FILESYSTEM& fsExtra, mesh::RTCClock& clock);
  void begin();
  bool formatFileSystem();
  bool repairInternalExtraFS();
  FILESYSTEM* getPrimaryFS() const { return _fs; }
  FILESYSTEM* getSecondaryFS() const { return _fsExtra; }
  void disableSecondaryFS() { _fsExtra = nullptr; }
  bool loadMainIdentity(mesh::LocalIdentity &identity);
  bool saveMainIdentity(const mesh::LocalIdentity &identity);
  void loadPrefs(CompanionNodePrefs& prefs, double& node_lat, double& node_lon);
  bool savePrefs(const CompanionNodePrefs& prefs, double node_lat, double node_lon);
  void loadContacts(DataStoreHost* host);
  bool saveContacts(DataStoreHost* host, bool (*filter)(const ContactInfo& c) = NULL);
  bool markContactDirty(const ContactInfo& contact);
  bool releaseContact(const ContactInfo& contact);
  bool serviceContactWrites(DataStoreHost* host, bool (*filter)(const ContactInfo& c) = NULL);
  bool flushContactWrites(DataStoreHost* host, bool (*filter)(const ContactInfo& c) = NULL);
  bool hasPendingContactWrites() const;
  void loadChannels(DataStoreHost* host);
  bool saveChannels(DataStoreHost* host);
  bool migrateToSecondaryFS();
  uint8_t getBlobByKey(const uint8_t key[], int key_len, uint8_t dest_buf[]);
  bool putBlobByKey(const uint8_t key[], int key_len, const uint8_t src_buf[], uint8_t len);
  bool deleteBlobByKey(const uint8_t key[], int key_len);
  File openRead(const char* filename);
  File openRead(FILESYSTEM* fs, const char* filename);
  bool removeFile(const char* filename);
  bool removeFile(FILESYSTEM* fs, const char* filename);
  uint32_t getStorageUsedKb() const;
  uint32_t getStorageTotalKb() const;

private:
  FILESYSTEM* _getContactsChannelsFS() const { if (_fsExtra) return _fsExtra; return _fs;};
};
