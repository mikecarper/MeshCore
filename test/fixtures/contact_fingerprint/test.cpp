#include <helpers/IdentityStore.h>
#include <helpers/PersistentStoreFormat.h>
#include <cassert>
#include <cstdio>

class FingerprintFS : public MemoryFS {
public:
  unsigned renames = 0;
  bool rename(const char* from, const char* to) {
    ++renames;
    return MemoryFS::rename(from, to);
  }
};
#undef FILESYSTEM
#define FILESYSTEM FingerprintFS
#include "transaction_under_test.h"

using Writer = mesh::ContactFileTransaction;
using Progress = Writer::CommitProgress;
using Bytes = std::vector<uint8_t>;
constexpr char Target[] = "/fingerprint";
constexpr char Temp[] = "/fingerprint.tmp";
constexpr char Backup[] = "/fingerprint.bak";

Bytes image(bool fleet, uint8_t value) {
  // Match the FCS1 and DTX1 on-disk layouts, including their raw IEEE CRC tail.
  Bytes bytes(fleet ? 64 : 108, 0);
  memcpy(bytes.data(), fleet ? "FCS1" : "DTX1", 4);
  bytes[4] = 1;
  if (fleet) {
    memset(bytes.data() + 8, 0x11, 16);
    memset(bytes.data() + 24, 0x22, 32);
    mesh::storage::writeLE32(bytes.data() + 56, value);
  } else {
    bytes[8] = value; // One one-byte route hash; automatic region selection.
  }
  const size_t payload = bytes.size() - 4;
  mesh::storage::writeLE32(bytes.data() + payload,
      mesh::storage::updateCRC32(0xffffffff, bytes.data(), payload));
  assert(mesh::storage::updateCRC32(0xffffffff, bytes.data(), bytes.size()) == 0);
  return bytes;
}

void writeImage(Writer& writer, const Bytes& bytes, size_t chunk) {
  for (size_t offset = 0; offset < bytes.size(); offset += chunk) {
    const size_t count = std::min(chunk, bytes.size() - offset);
    assert(writer.write(bytes.data() + offset, count) == count);
  }
}

Progress drain(Writer& writer, FingerprintFS& fs, const Bytes& old,
               unsigned budget, unsigned& passes, bool valid = true) {
  Progress progress = Progress::Pending;
  while (progress == Progress::Pending) {
    assert(++passes < 100);
    const bool publishing = writer.readyToPublish();
    progress = writer.serviceCommit(valid, budget);
    if (progress == Progress::Pending) {
      assert(fs.files.at(Target) == old && fs.renames == 0);
    } else if (progress == Progress::Succeeded) {
      assert(publishing); // No yield between namespace and caller publication.
    }
  }
  return progress;
}

void healthy() {
  for (bool fleet : {false, true}) {
    const auto old = image(fleet, 1), next = image(fleet, 2);
    for (size_t chunk : {size_t(1), size_t(17), next.size()}) {
      for (unsigned budget : {1U, 8U}) {
        FingerprintFS fs;
        fs.files[Target] = old;
        Writer writer(&fs, Target);
        assert(writer);
        writeImage(writer, next, chunk);
        unsigned passes = 0;
        assert(drain(writer, fs, old, budget, passes) == Progress::Succeeded);
#if !defined(ESP32_PLATFORM)
        budget = 1; // RP2040 retains one 64-byte verification chunk per pass.
#endif
        assert(passes == (next.size() + 64 * budget - 1) / (64 * budget) + 4);
        assert(fs.files.at(Target) == next && fs.renames == 2);
        assert(!fs.exists(Temp) && !fs.exists(Backup));
      }
    }
  }
}

void staleStage(bool fleet) {
  const auto old = image(fleet, 1), next = image(fleet, 2);
  assert(old != next && old.size() == next.size());
  for (size_t chunk : {size_t(1), size_t(17), next.size()}) {
    for (unsigned budget : {1U, 8U}) {
      FingerprintFS fs;
      fs.files[Target] = old;
      Writer writer(&fs, Target);
      assert(writer);
      writeImage(writer, next, chunk);
      assert(writer.serviceCommit(true, budget) == Progress::Pending); // Flush.
      fs.files[Temp] = old;
      unsigned passes = 1;
      const auto progress = drain(writer, fs, old, budget, passes);
      assert(progress == Progress::Failed);
      assert(fs.files.at(Target) == old && fs.renames == 0);
      assert(!fs.exists(Temp) && !fs.exists(Backup));
    }
  }
}

void prepublicationFailures() {
  for (bool fleet : {false, true}) {
    const auto old = image(fleet, 1), next = image(fleet, 2);
    for (unsigned fault = 0; fault < 6; ++fault) {
      FingerprintFS fs;
      fs.files[Target] = old;
      Writer writer(&fs, Target);
      assert(writer);
      if (fault == 1) fs.fail_write_after = 1;
      const size_t wrote = writer.write(next.data(), next.size());
      assert(wrote == (fault == 1 ? 1 : next.size()));
      if (fault == 2) fs.fail_read_open = true;
      if (fault == 3) fs.fail_read_after = 1;
      if (fault == 4) fs.files.at(Temp).push_back(0);
      if (fault == 5) fs.files.at(Temp)[8] ^= 1;
      unsigned passes = 0;
      assert(drain(writer, fs, old, 8, passes, fault != 0) == Progress::Failed);
      assert(fs.files.at(Target) == old && fs.renames == 0);
      assert(!fs.exists(Temp) && !fs.exists(Backup));
    }
  }
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string mode = argv[1];
  if (mode == "healthy") healthy();
  else if (mode == "stale_fcs1") staleStage(true);
  else if (mode == "stale_dtx1") staleStage(false);
  else if (mode == "failures") prepublicationFailures();
  else assert(false);
  printf("contact fingerprint %s passed\n", argv[1]);
}
