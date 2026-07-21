#include <gtest/gtest.h>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string>
#include <unordered_map>
#include <vector>

class FakeFilesystem;

class FakeFile {
  FakeFilesystem* _fs;
  std::string _path;
  size_t _position;
  bool _open;
  bool _write_mode;

public:
  explicit FakeFile(FakeFilesystem& fs);

  bool open(const char* path, uint8_t mode);
  size_t write(const uint8_t* data, size_t len);
  int read(void* data, uint16_t len);
  uint32_t size() const;
  void flush() {}
  void close() { _open = false; }
  explicit operator bool() const { return _open; }
};

class FakeFilesystem {
public:
  std::unordered_map<std::string, std::vector<uint8_t>> files;
  size_t max_write = std::numeric_limits<size_t>::max();
  bool fail_read_open = false;
  bool fail_rename = false;
  unsigned rename_calls = 0;

  bool exists(const char* path) const {
    return files.find(path) != files.end();
  }

  bool remove(const char* path) {
    return files.erase(path) != 0;
  }

  bool rename(const char* from, const char* to) {
    rename_calls++;
    if (fail_rename) return false;
    auto source = files.find(from);
    if (source == files.end()) return false;
    files[to] = source->second;
    files.erase(source);
    return true;
  }
};

enum {
  FILE_O_READ = 0,
  FILE_O_WRITE = 1,
};

FakeFile::FakeFile(FakeFilesystem& fs)
  : _fs(&fs), _position(0), _open(false), _write_mode(false) {}

bool FakeFile::open(const char* path, uint8_t mode) {
  _path = path;
  _write_mode = mode == FILE_O_WRITE;
  if (!_write_mode && (_fs->fail_read_open || !_fs->exists(path))) return false;
  if (_write_mode) {
    auto& contents = _fs->files[_path];
    _position = contents.size();
  } else {
    _position = 0;
  }
  _open = true;
  return true;
}

size_t FakeFile::write(const uint8_t* data, size_t len) {
  if (!_open || !_write_mode) return 0;
  size_t written = std::min(len, _fs->max_write);
  auto& contents = _fs->files[_path];
  contents.insert(contents.end(), data, data + written);
  _position += written;
  return written;
}

int FakeFile::read(void* data, uint16_t len) {
  if (!_open || _write_mode) return -1;
  const auto& contents = _fs->files[_path];
  size_t available = contents.size() - std::min(_position, contents.size());
  size_t count = std::min<size_t>(len, available);
  if (count > 0) memcpy(data, contents.data() + _position, count);
  _position += count;
  return (int)count;
}

uint32_t FakeFile::size() const {
  auto found = _fs->files.find(_path);
  return found == _fs->files.end() ? 0 : (uint32_t)found->second.size();
}

#define NRF52_PLATFORM 1
#define FILESYSTEM FakeFilesystem
using File = FakeFile;
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>

static std::vector<uint8_t> bytes(const char* text) {
  return std::vector<uint8_t>(text, text + strlen(text));
}

TEST(AtomicFileWriter, ReplacesLiveFileOnlyAfterVerifiedCommit) {
  FakeFilesystem fs;
  fs.files["/prefs"] = bytes("old");

  mesh::AtomicFileWriter writer(&fs, "/prefs");
  ASSERT_TRUE(writer);
  const uint8_t replacement[] = {'n', 'e', 'w'};
  EXPECT_EQ(writer.write(replacement, sizeof(replacement)), sizeof(replacement));
  EXPECT_EQ(fs.files["/prefs"], bytes("old"));

  EXPECT_TRUE(writer.commit());
  EXPECT_EQ(fs.files["/prefs"], bytes("new"));
  EXPECT_FALSE(fs.exists("/prefs.tmp"));
  EXPECT_EQ(fs.rename_calls, 1u);
}

TEST(AtomicFileWriter, PartialWritePreservesLiveFile) {
  FakeFilesystem fs;
  fs.files["/prefs"] = bytes("old");
  fs.max_write = 2;

  mesh::AtomicFileWriter writer(&fs, "/prefs");
  const uint8_t replacement[] = {'n', 'e', 'w'};
  EXPECT_EQ(writer.write(replacement, sizeof(replacement)), 2u);
  EXPECT_FALSE(writer.commit());

  EXPECT_EQ(fs.files["/prefs"], bytes("old"));
  EXPECT_FALSE(fs.exists("/prefs.tmp"));
  EXPECT_EQ(fs.rename_calls, 0u);
}

TEST(AtomicFileWriter, ReadbackMismatchPreservesLiveFile) {
  FakeFilesystem fs;
  fs.files["/prefs"] = bytes("old");

  mesh::AtomicFileWriter writer(&fs, "/prefs");
  const uint8_t replacement[] = {'n', 'e', 'w'};
  ASSERT_EQ(writer.write(replacement, sizeof(replacement)), sizeof(replacement));
  fs.files["/prefs.tmp"][1] ^= 0x01;

  EXPECT_FALSE(writer.commit());
  EXPECT_EQ(fs.files["/prefs"], bytes("old"));
  EXPECT_FALSE(fs.exists("/prefs.tmp"));
  EXPECT_EQ(fs.rename_calls, 0u);
}

TEST(AtomicFileWriter, CallerValidationFailurePreservesLiveFile) {
  FakeFilesystem fs;
  fs.files["/prefs"] = bytes("old");

  mesh::AtomicFileWriter writer(&fs, "/prefs");
  const uint8_t replacement[] = {'n', 'e', 'w'};
  ASSERT_EQ(writer.write(replacement, sizeof(replacement)), sizeof(replacement));

  EXPECT_FALSE(writer.commit(false));
  EXPECT_EQ(fs.files["/prefs"], bytes("old"));
  EXPECT_FALSE(fs.exists("/prefs.tmp"));
  EXPECT_EQ(fs.rename_calls, 0u);
}

TEST(AtomicFileWriter, RenameFailurePreservesLiveFileAndCleansTemp) {
  FakeFilesystem fs;
  fs.files["/prefs"] = bytes("old");
  fs.fail_rename = true;

  mesh::AtomicFileWriter writer(&fs, "/prefs");
  const uint8_t replacement[] = {'n', 'e', 'w'};
  ASSERT_EQ(writer.write(replacement, sizeof(replacement)), sizeof(replacement));

  EXPECT_FALSE(writer.commit());
  EXPECT_EQ(fs.files["/prefs"], bytes("old"));
  EXPECT_FALSE(fs.exists("/prefs.tmp"));
  EXPECT_EQ(fs.rename_calls, 1u);
}

TEST(AtomicFileWriter, AbandonedWriteAndStaleTempAreCleaned) {
  FakeFilesystem fs;
  fs.files["/prefs"] = bytes("old");
  fs.files["/prefs.tmp"] = bytes("stale");

  {
    mesh::AtomicFileWriter writer(&fs, "/prefs");
    ASSERT_TRUE(writer);
    EXPECT_TRUE(fs.files["/prefs.tmp"].empty());
    const uint8_t replacement[] = {'n', 'e', 'w'};
    ASSERT_EQ(writer.write(replacement, sizeof(replacement)), sizeof(replacement));
  }

  EXPECT_EQ(fs.files["/prefs"], bytes("old"));
  EXPECT_FALSE(fs.exists("/prefs.tmp"));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
