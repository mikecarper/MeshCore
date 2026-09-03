#include <gtest/gtest.h>

#include <helpers/PersistentStoreFormat.h>

using namespace mesh::storage;

TEST(PersistentStoreFormat, ContactHeaderRoundTripsAndRejectsDamage) {
  uint8_t raw[CONTACT_PAGE_HEADER_SIZE];
  ContactPageHeader original = {3, 0x00100005UL, 42, 0x12345678UL};
  encodeContactPageHeader(raw, original);

  ContactPageHeader decoded = {};
  ASSERT_TRUE(decodeContactPageHeader(raw, 3, decoded));
  EXPECT_EQ(decoded.page_index, 3);
  EXPECT_EQ(decoded.occupied, original.occupied);
  EXPECT_EQ(decoded.generation, original.generation);
  EXPECT_EQ(decoded.payload_crc, original.payload_crc);

  raw[0] ^= 1;
  EXPECT_FALSE(decodeContactPageHeader(raw, 3, decoded));
  raw[0] ^= 1;
  EXPECT_FALSE(decodeContactPageHeader(raw, 2, decoded));
  writeLE32(&raw[8], 1UL << CONTACTS_PER_PAGE);
  EXPECT_TRUE(decodeContactPageHeader(raw, 3, decoded));
  EXPECT_NE(decoded.occupied & ~contactPageValidSlotMask(), 0u);
}

TEST(PersistentStoreFormat, CRCDetectsPayloadChanges) {
  const uint8_t payload[] = {1, 2, 3, 4, 5};
  uint32_t expected = updateCRC32(0xFFFFFFFFUL, payload, sizeof(payload));
  uint8_t changed[sizeof(payload)] = {1, 2, 3, 4, 4};
  EXPECT_NE(updateCRC32(0xFFFFFFFFUL, changed, sizeof(changed)), expected);

  uint32_t split = updateCRC32(0xFFFFFFFFUL, payload, 2);
  split = updateCRC32(split, payload + 2, sizeof(payload) - 2);
  EXPECT_EQ(split, expected);
}

TEST(PersistentStoreFormat, OccupancyCanBeDerivedFromCrcProtectedRecords) {
  uint8_t empty[CONTACT_RECORD_SIZE] = {};
  EXPECT_FALSE(contactRecordHasData(empty));

  for (uint16_t offset : {uint16_t(0), uint16_t(31),
                          uint16_t(CONTACT_RECORD_SIZE - 1)}) {
    uint8_t populated[CONTACT_RECORD_SIZE] = {};
    populated[offset] = 1;
    EXPECT_TRUE(contactRecordHasData(populated));
  }
}

TEST(PersistentStoreFormat, DirtyPagesStayPendingUntilExplicitlyCleared) {
  DirtyPageSet pages;
  EXPECT_TRUE(pages.empty());
  EXPECT_TRUE(pages.mark(7));
  EXPECT_TRUE(pages.mark(2));
  EXPECT_EQ(pages.first(), 2);
  pages.clear(2);
  EXPECT_EQ(pages.first(), 7);
  EXPECT_FALSE(pages.mark(CONTACT_PAGE_COUNT));
  pages.clear(7);
  EXPECT_TRUE(pages.empty());
}

TEST(PersistentStoreFormat, LegacyCountIgnoresPartialTailAndCapsAtCapacity) {
  EXPECT_EQ(legacyContactCountForSize(0), 0);
  EXPECT_EQ(legacyContactCountForSize(CONTACT_RECORD_SIZE - 1), 0);
  EXPECT_EQ(legacyContactCountForSize(63 * CONTACT_RECORD_SIZE + 17), 63);
  EXPECT_EQ(legacyContactCountForSize(999 * CONTACT_RECORD_SIZE), 350);
}

TEST(PersistentStoreFormat, LegacyFileSizeValidationRejectsUnsafeSources) {
  const size_t capacity = CONTACT_PAGE_COUNT * CONTACTS_PER_PAGE;
  EXPECT_TRUE(isValidLegacyContactFileSize(0));
  EXPECT_TRUE(isValidLegacyContactFileSize(CONTACT_RECORD_SIZE));
  EXPECT_TRUE(isValidLegacyContactFileSize(capacity * CONTACT_RECORD_SIZE));
  EXPECT_FALSE(isValidLegacyContactFileSize(CONTACT_RECORD_SIZE - 1));
  EXPECT_FALSE(isValidLegacyContactFileSize(
      63 * CONTACT_RECORD_SIZE + 17));
  EXPECT_FALSE(isValidLegacyContactFileSize(
      (capacity + 1) * CONTACT_RECORD_SIZE));
}

TEST(PersistentStoreFormat, LegacyMigrationMovesTailOnePageAtATime) {
  EXPECT_EQ(legacyMigrationPage(63), 2);
  EXPECT_EQ(legacyCountAfterMigratingPage(2), 50);
  EXPECT_FALSE(loadSlotFromMigratedPage(62, 63));
  EXPECT_TRUE(loadSlotFromMigratedPage(63, 63));

  // After the page commit and legacy truncate, all of page two is loaded.
  EXPECT_TRUE(loadSlotFromMigratedPage(50, 50));
  EXPECT_EQ(legacyMigrationPage(25), 0);
  EXPECT_EQ(legacyCountAfterMigratingPage(0), 0);
  EXPECT_EQ(legacyMigrationPage(0), CONTACT_PAGE_COUNT);
}

TEST(PersistentStoreFormat, TailMigrationHasBoundedPeakSpaceAcrossPowerCuts) {
  uint16_t legacy_count = CONTACT_PAGE_COUNT * CONTACTS_PER_PAGE;
  size_t committed_pages = 0;
  size_t peak_bytes = (size_t)legacy_count * CONTACT_RECORD_SIZE;

  while (legacy_count != 0) {
    const uint8_t page = legacyMigrationPage(legacy_count);
    committed_pages++;

    // Worst reset point: the newly committed page and its temporary retry can
    // coexist with the untruncated legacy prefix.
    const size_t retry_peak = (size_t)legacy_count * CONTACT_RECORD_SIZE
        + (committed_pages + 1) * CONTACT_PAGE_FILE_SIZE;
    if (retry_peak > peak_bytes) peak_bytes = retry_peak;

    legacy_count = legacyCountAfterMigratingPage(page);
  }

  EXPECT_EQ(committed_pages, CONTACT_PAGE_COUNT);
  EXPECT_LT(peak_bytes, 64u * 1024u);
}

TEST(PersistentStoreFormat, ContactPageFitsWithinOneFourKiBBlock) {
  EXPECT_LT(CONTACT_PAGE_FILE_SIZE, 4096);
  EXPECT_EQ(CONTACT_PAGE_COUNT * CONTACTS_PER_PAGE, 350);
}

TEST(PersistentStoreFormat, SlotAllocationIsStableAndReusable) {
  ContactSlotMap slots;
  EXPECT_EQ(slots.allocate(), 0);
  EXPECT_EQ(slots.allocate(), 1);
  EXPECT_TRUE(slots.release(0));
  EXPECT_EQ(slots.allocate(), 0);
  EXPECT_FALSE(slots.reserve(1));
  EXPECT_TRUE(slots.reserve(CONTACTS_PER_PAGE));
  EXPECT_EQ(slots.pageMask(1), 1u);
  EXPECT_FALSE(slots.release(CONTACT_PAGE_COUNT * CONTACTS_PER_PAGE));
}

TEST(PersistentStoreFormat, ReleasedSlotCanBeRestoredPastAnEarlierHole) {
  ContactSlotMap slots;
  ASSERT_TRUE(slots.reserve(0));
  ASSERT_TRUE(slots.reserve(CONTACTS_PER_PAGE));
  ASSERT_TRUE(slots.release(CONTACTS_PER_PAGE));

  // Slot 1 is the allocator's earliest hole, but transaction rollback must be
  // able to reclaim the exact page-1 slot which was just released.
  EXPECT_TRUE(slots.reserve(CONTACTS_PER_PAGE));
  EXPECT_FALSE(slots.isUsed(1));
  EXPECT_TRUE(slots.isUsed(CONTACTS_PER_PAGE));
  EXPECT_EQ(slots.pageMask(0), 1u);
  EXPECT_EQ(slots.pageMask(1), 1u);
}

TEST(PersistentStoreFormat, FailedSlotAllocationAndReleaseAreNonMutating) {
  ContactSlotMap slots;
  for (uint16_t slot = 0;
       slot < CONTACT_PAGE_COUNT * CONTACTS_PER_PAGE; slot++) {
    ASSERT_EQ(slots.allocate(), slot);
  }

  uint32_t full_masks[CONTACT_PAGE_COUNT];
  for (uint8_t page = 0; page < CONTACT_PAGE_COUNT; page++) {
    full_masks[page] = slots.pageMask(page);
  }

  EXPECT_EQ(slots.allocate(), CONTACT_SLOT_NONE);
  EXPECT_FALSE(slots.release(CONTACT_SLOT_NONE));
  for (uint8_t page = 0; page < CONTACT_PAGE_COUNT; page++) {
    EXPECT_EQ(slots.pageMask(page), full_masks[page]);
  }
}

TEST(PersistentStoreFormat, LegacyMarkerSelectsResumableMigrationPath) {
  EXPECT_EQ(chooseContactStoreSource(true, true), ContactStoreSource::LEGACY);
  EXPECT_EQ(chooseContactStoreSource(true, false), ContactStoreSource::LEGACY);
  EXPECT_EQ(chooseContactStoreSource(false, true), ContactStoreSource::PAGED);
  EXPECT_EQ(chooseContactStoreSource(false, false), ContactStoreSource::EMPTY);

  EXPECT_FALSE(trustMigratedContactPages(true, false));
  EXPECT_TRUE(trustMigratedContactPages(true, true));
  EXPECT_TRUE(trustMigratedContactPages(false, false));
}

TEST(PersistentStoreFormat, RawPathStatDistinguishesAbsenceFromIoFailure) {
  constexpr int no_entry = -2;
  EXPECT_EQ(classifyContactPathStat(0, no_entry), ContactPathState::PRESENT);
  EXPECT_EQ(classifyContactPathStat(no_entry, no_entry),
            ContactPathState::ABSENT);
  EXPECT_EQ(classifyContactPathStat(-5, no_entry),
            ContactPathState::IO_ERROR);
  EXPECT_EQ(classifyContactPathStat(1, no_entry),
            ContactPathState::IO_ERROR);
}

TEST(PersistentStoreFormat, MigrationPresenceScanFailsClosedOnAnyIoError) {
  constexpr int no_entry = -2;
  const int empty_scan[] = {no_entry, no_entry, no_entry};
  bool empty_has_source = false;
  for (int result : empty_scan) {
    const ContactPathState state = classifyContactPathStat(result, no_entry);
    ASSERT_NE(state, ContactPathState::IO_ERROR);
    empty_has_source = empty_has_source || state == ContactPathState::PRESENT;
  }
  EXPECT_FALSE(empty_has_source);

  const int populated_scan[] = {no_entry, 0, no_entry};
  bool populated_has_source = false;
  for (int result : populated_scan) {
    const ContactPathState state = classifyContactPathStat(result, no_entry);
    ASSERT_NE(state, ContactPathState::IO_ERROR);
    populated_has_source =
        populated_has_source || state == ContactPathState::PRESENT;
  }
  EXPECT_TRUE(populated_has_source);

  const int failed_scan[] = {no_entry, -5, 0};
  bool scan_ok = true;
  bool failed_has_source = false;
  for (int result : failed_scan) {
    const ContactPathState state = classifyContactPathStat(result, no_entry);
    if (state == ContactPathState::IO_ERROR) {
      scan_ok = false;
      break;
    }
    failed_has_source =
        failed_has_source || state == ContactPathState::PRESENT;
  }
  EXPECT_FALSE(scan_ok);
  EXPECT_FALSE(failed_has_source);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
