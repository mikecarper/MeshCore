#pragma once

#if defined(ESP32)
#include <ChaChaPoly.h>
#include <FS.h>
#include <memory>

namespace mesh {
namespace privateStorage {

// Called once while the ESP32's startup entropy source is enabled.
void seed();
bool retireMigrationCopies();
constexpr size_t IDENTITY_BYTES = 96;
// Identity records retain the migration bridge's exact 96-byte slot.
bool isSealedIdentity(const uint8_t* record);
bool sealIdentity(const char* name, const uint8_t* privateKey, uint8_t* record);
bool openIdentity(const char* name, const uint8_t* record, uint8_t* privateKey);

// Sequential authenticated file used by the existing ACL and replay transactions.
// Reads authenticate the complete file before exposing any client permissions.
class AclFile {
  File file;
  std::unique_ptr<ChaChaPoly> cipher;
  size_t remaining = 0;
  size_t plainSize = 0;
  bool writing = false;
public:
  AclFile(File raw) : file(raw) {}
  AclFile(AclFile&&) = default;
  AclFile& operator=(AclFile&&) = default;
  static AclFile open(File raw, const char* path, bool write, bool* invalid = nullptr);
  explicit operator bool() const { return static_cast<bool>(file); }
  bool isDirectory() { return file.isDirectory(); }
  size_t size() const { return cipher ? plainSize : file.size(); }
  bool sealed() const { return cipher != nullptr; }
  size_t read(uint8_t* data, size_t length);
  size_t write(const uint8_t* data, size_t length);
  void close();
};
}
}
#endif
