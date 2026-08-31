#include <gtest/gtest.h>

#include <set>
#include <string>
#include <vector>
#include <map>

#include <helpers/ClientACLFileTransaction.h>
#include <helpers/ClientACLFileIntegrity.h>

namespace {

class FakeFilesystem {
public:
  std::set<std::string> files;
  std::set<std::string> valid_files;
  std::map<std::string, std::vector<uint8_t> > images;
  std::string fail_from;
  std::string fail_to;
  std::string fail_remove;

  bool exists(const char* path) const {
    return files.count(path) != 0;
  }

  bool remove(const char* path) {
    if (fail_remove == path) return false;
    images.erase(path);
    valid_files.erase(path);
    return files.erase(path) != 0;
  }

  bool rename(const char* from, const char* to) {
    if (fail_from == from && fail_to == to) return false;
    if (!exists(from) || exists(to)) return false;
    files.erase(from);
    files.insert(to);
    if (valid_files.erase(from) != 0) valid_files.insert(to);
    std::map<std::string, std::vector<uint8_t> >::iterator image =
        images.find(from);
    if (image != images.end()) {
      images[to] = image->second;
      images.erase(image);
    }
    return true;
  }
};

bool isValid(FakeFilesystem* fs, const char* path) {
  return fs->valid_files.count(path) != 0;
}

TEST(ClientACLFileTransaction, FailedVerificationPreservesPrimary) {
  FakeFilesystem fs;
  fs.files = {mesh::CLIENT_ACL_PRIMARY_PATH, mesh::CLIENT_ACL_TEMP_PATH};

  EXPECT_FALSE(mesh::publishVerifiedClientACLTemp(&fs, false));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_TEMP_PATH));
}

TEST(ClientACLFileTransaction, VerifiedTempPublishesThroughBackup) {
  FakeFilesystem fs;
  fs.files = {mesh::CLIENT_ACL_PRIMARY_PATH, mesh::CLIENT_ACL_TEMP_PATH};

  EXPECT_TRUE(mesh::publishVerifiedClientACLTemp(&fs, true));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_TEMP_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
}

TEST(ClientACLFileTransaction, PrimaryRenameFailureLeavesOldImage) {
  FakeFilesystem fs;
  fs.files = {mesh::CLIENT_ACL_PRIMARY_PATH, mesh::CLIENT_ACL_TEMP_PATH};
  fs.fail_from = mesh::CLIENT_ACL_PRIMARY_PATH;
  fs.fail_to = mesh::CLIENT_ACL_BACKUP_PATH;

  EXPECT_FALSE(mesh::publishVerifiedClientACLTemp(&fs, true));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_TEMP_PATH));
}

TEST(ClientACLFileTransaction, PublishFailureRestoresBackup) {
  FakeFilesystem fs;
  fs.files = {mesh::CLIENT_ACL_PRIMARY_PATH, mesh::CLIENT_ACL_TEMP_PATH};
  fs.fail_from = mesh::CLIENT_ACL_TEMP_PATH;
  fs.fail_to = mesh::CLIENT_ACL_PRIMARY_PATH;

  EXPECT_FALSE(mesh::publishVerifiedClientACLTemp(&fs, true));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_TEMP_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
}

TEST(ClientACLFileTransaction, BootRecoveryKeepsPublishedPrimary) {
  FakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_ACL_PRIMARY_PATH,
      mesh::CLIENT_ACL_TEMP_PATH,
      mesh::CLIENT_ACL_BACKUP_PATH,
  };

  EXPECT_TRUE(mesh::recoverClientACLFiles(&fs));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_TEMP_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
}

TEST(ClientACLFileTransaction, BootRecoveryRestoresInterruptedPublish) {
  FakeFilesystem fs;
  fs.files = {mesh::CLIENT_ACL_TEMP_PATH, mesh::CLIENT_ACL_BACKUP_PATH};

  EXPECT_TRUE(mesh::recoverClientACLFiles(&fs));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_TEMP_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
}

TEST(ClientACLFileTransaction, CorruptPrimaryFallsBackToValidBackup) {
  FakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_ACL_PRIMARY_PATH, mesh::CLIENT_ACL_BACKUP_PATH};
  fs.valid_files = {mesh::CLIENT_ACL_BACKUP_PATH};

  EXPECT_TRUE(mesh::recoverClientACLFilesVerified(&fs, isValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_TRUE(fs.valid_files.count(mesh::CLIENT_ACL_PRIMARY_PATH) != 0);
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
}

TEST(ClientACLFileTransaction, CorruptPublishedPrimaryRestoresOldImage) {
  FakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_ACL_PRIMARY_PATH, mesh::CLIENT_ACL_TEMP_PATH};
  fs.valid_files = {mesh::CLIENT_ACL_PRIMARY_PATH};

  EXPECT_FALSE(mesh::publishVerifiedClientACLTemp(&fs, true, isValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_TRUE(fs.valid_files.count(mesh::CLIENT_ACL_PRIMARY_PATH) != 0);
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
}

TEST(ClientACLFileTransaction, PublishedPrimarySurvivesBackupCleanupFailure) {
  FakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_ACL_PRIMARY_PATH, mesh::CLIENT_ACL_TEMP_PATH};
  fs.valid_files = {
      mesh::CLIENT_ACL_PRIMARY_PATH, mesh::CLIENT_ACL_TEMP_PATH};
  fs.fail_remove = mesh::CLIENT_ACL_BACKUP_PATH;

  EXPECT_TRUE(mesh::publishVerifiedClientACLTemp(&fs, true, isValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_TRUE(fs.valid_files.count(mesh::CLIENT_ACL_PRIMARY_PATH) != 0);
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));

  // A later recovery pass can finish the best-effort cleanup without
  // changing which image is authoritative.
  fs.fail_remove.clear();
  EXPECT_TRUE(mesh::recoverClientACLFilesVerified(&fs, isValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
}

TEST(ClientACLFileTransaction, ValidPrimaryLoadsWhenCleanupCannotFinish) {
  FakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_ACL_PRIMARY_PATH,
      mesh::CLIENT_ACL_TEMP_PATH,
      mesh::CLIENT_ACL_BACKUP_PATH,
  };
  fs.valid_files = {mesh::CLIENT_ACL_PRIMARY_PATH};
  fs.fail_remove = mesh::CLIENT_ACL_BACKUP_PATH;

  EXPECT_TRUE(mesh::recoverClientACLFilesVerified(&fs, isValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_TEMP_PATH));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
}

std::vector<uint8_t> crcImage(size_t records) {
  std::vector<uint8_t> image(records * 201, 0x5A);
  uint32_t crc = mesh::updateClientACLCRC(
      0xFFFFFFFFUL, image.data(), image.size()) ^ 0xFFFFFFFFUL;
  image.insert(image.end(), mesh::CLIENT_ACL_CRC_MAGIC,
               mesh::CLIENT_ACL_CRC_MAGIC + 4);
  const uint8_t* bytes = reinterpret_cast<const uint8_t*>(&crc);
  image.insert(image.end(), bytes, bytes + sizeof(crc));
  return image;
}

bool realImageIsValid(FakeFilesystem* fs, const char* path) {
  std::map<std::string, std::vector<uint8_t> >::const_iterator found =
      fs->images.find(path);
  if (found == fs->images.end()) return false;
  const mesh::ClientACLImageKind kind = mesh::classifyClientACLImage(
      found->second.data(), found->second.size(), 201, 136);
  const bool publication_in_progress = std::string(path)
          == mesh::CLIENT_ACL_PRIMARY_PATH
      && fs->exists(mesh::CLIENT_ACL_BACKUP_PATH);
  return publication_in_progress ? kind == mesh::CLIENT_ACL_IMAGE_CRC
                                 : kind != mesh::CLIENT_ACL_IMAGE_INVALID;
}

TEST(ClientACLFileTransaction, RealCRCClassifierRejectsTornTrailer) {
  std::vector<uint8_t> image = crcImage(2);
  EXPECT_EQ(mesh::classifyClientACLImage(image.data(), image.size(), 201, 136),
            mesh::CLIENT_ACL_IMAGE_CRC);

  image.resize(image.size() - 8); // exactly 201*N: ambiguous standalone legacy
  EXPECT_EQ(mesh::classifyClientACLImage(image.data(), image.size(), 201, 136),
            mesh::CLIENT_ACL_IMAGE_LEGACY);
  // Recovery resolves this ambiguity from transaction state: if a backup is
  // present, only CRC kind is acceptable for the newly published primary.
  EXPECT_TRUE(mesh::classifyClientACLImage(
      image.data(), image.size(), 201, 136) != mesh::CLIENT_ACL_IMAGE_CRC);
}

TEST(ClientACLFileTransaction, RealClassifierCoversRecordBoundaryTruncations) {
  std::vector<uint8_t> image = crcImage(4);
  image.resize(3 * 201);
  EXPECT_EQ(mesh::classifyClientACLImage(image.data(), image.size(), 201, 136),
            mesh::CLIENT_ACL_IMAGE_LEGACY);

  image.resize(2 * 136);
  EXPECT_EQ(mesh::classifyClientACLImage(image.data(), image.size(), 201, 136),
            mesh::CLIENT_ACL_IMAGE_LEGACY);

  image.resize(2 * 136 - 1);
  EXPECT_EQ(mesh::classifyClientACLImage(image.data(), image.size(), 201, 136),
            mesh::CLIENT_ACL_IMAGE_INVALID);
}

TEST(ClientACLFileTransaction, TornTrailerRestoresRealValidatedBackup) {
  FakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_ACL_PRIMARY_PATH, mesh::CLIENT_ACL_BACKUP_PATH};
  fs.images[mesh::CLIENT_ACL_PRIMARY_PATH] = crcImage(2);
  fs.images[mesh::CLIENT_ACL_PRIMARY_PATH].resize(2 * 201);
  fs.images[mesh::CLIENT_ACL_BACKUP_PATH] = std::vector<uint8_t>(136, 0x33);

  EXPECT_TRUE(mesh::recoverClientACLFilesVerified(&fs, realImageIsValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
  EXPECT_EQ(fs.images[mesh::CLIENT_ACL_PRIMARY_PATH].size(), 136u);
}

TEST(ClientACLFileTransaction, TornTrailerRestoresCRCValidatedBackup) {
  FakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_ACL_PRIMARY_PATH, mesh::CLIENT_ACL_BACKUP_PATH};
  fs.images[mesh::CLIENT_ACL_PRIMARY_PATH] = crcImage(2);
  fs.images[mesh::CLIENT_ACL_PRIMARY_PATH].resize(2 * 201);
  fs.images[mesh::CLIENT_ACL_BACKUP_PATH] = crcImage(1);

  EXPECT_TRUE(mesh::recoverClientACLFilesVerified(&fs, realImageIsValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_ACL_PRIMARY_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
  EXPECT_EQ(mesh::classifyClientACLImage(
                fs.images[mesh::CLIENT_ACL_PRIMARY_PATH].data(),
                fs.images[mesh::CLIENT_ACL_PRIMARY_PATH].size(), 201, 136),
            mesh::CLIENT_ACL_IMAGE_CRC);
}

TEST(ClientACLFileTransaction, StandaloneNoCRCLayoutsMigrateOnNextPublish) {
  const size_t standalone_sizes[] = {136, 201};
  for (size_t standalone_size : standalone_sizes) {
    FakeFilesystem fs;
    fs.files = {mesh::CLIENT_ACL_PRIMARY_PATH};
    fs.images[mesh::CLIENT_ACL_PRIMARY_PATH] =
        std::vector<uint8_t>(standalone_size, 0x33);

    EXPECT_TRUE(mesh::recoverClientACLFilesVerified(&fs, realImageIsValid));
    EXPECT_EQ(mesh::classifyClientACLImage(
                  fs.images[mesh::CLIENT_ACL_PRIMARY_PATH].data(),
                  fs.images[mesh::CLIENT_ACL_PRIMARY_PATH].size(), 201, 136),
              mesh::CLIENT_ACL_IMAGE_LEGACY);

    fs.files.insert(mesh::CLIENT_ACL_TEMP_PATH);
    fs.images[mesh::CLIENT_ACL_TEMP_PATH] = crcImage(1);
    EXPECT_TRUE(mesh::publishVerifiedClientACLTemp(
        &fs, true, realImageIsValid));
    EXPECT_FALSE(fs.exists(mesh::CLIENT_ACL_BACKUP_PATH));
    EXPECT_EQ(mesh::classifyClientACLImage(
                  fs.images[mesh::CLIENT_ACL_PRIMARY_PATH].data(),
                  fs.images[mesh::CLIENT_ACL_PRIMARY_PATH].size(), 201, 136),
              mesh::CLIENT_ACL_IMAGE_CRC);
  }
}

} // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
