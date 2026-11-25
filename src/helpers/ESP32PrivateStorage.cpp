#if defined(ESP32)
#include "esp32/PrivateStorage.h"
#include <algorithm>
#include <esp_random.h>
#include <nvs.h>
#include <freertos/FreeRTOS.h>
#include <mbedtls/platform_util.h>
#include <new>
#include <string.h>

namespace mesh {
namespace privateStorage {
namespace {
constexpr uint8_t identityMagic[] = {0, 'I', 'D', 1};
constexpr uint8_t fileMagic[] = {0, 'M', 'C', 'A', 'C', 'L', 1, 0};
constexpr size_t nonceSize = 12, tagSize = 16;
constexpr size_t identitySecretSize = 64;
constexpr size_t identityPayload = sizeof(identityMagic) + nonceSize;
constexpr size_t identityTag = identityPayload + identitySecretSize;
static_assert(identityTag + tagSize == IDENTITY_BYTES, "Identity must fit the migration slot");
uint8_t firstKey[32], nextNonce[nonceSize];
portMUX_TYPE nonceLock = portMUX_INITIALIZER_UNLOCKED;
bool seeded = false;

bool start(ChaChaPoly& cipher, uint8_t* nonce, const char* purpose, bool create) {
  if (!seeded) return false;
  nvs_handle_t handle;
  if (nvs_open("mc-private", create ? NVS_READWRITE : NVS_READONLY, &handle) != ESP_OK)
    return false;
  uint8_t key[32];
  size_t size = sizeof(key);
  esp_err_t rc = nvs_get_blob(handle, "wrap-key", key, &size);
  if (create && rc == ESP_ERR_NVS_NOT_FOUND) {
    // All concurrent first writers on this boot use the same candidate.
    rc = nvs_set_blob(handle, "wrap-key", firstKey, sizeof(firstKey));
    if (rc == ESP_OK) rc = nvs_commit(handle);
    size = sizeof(key);
    if (rc == ESP_OK) rc = nvs_get_blob(handle, "wrap-key", key, &size);
  }
  nvs_close(handle);
  const bool ok = rc == ESP_OK && size == sizeof(key) && cipher.setKey(key, sizeof(key));
  mbedtls_platform_zeroize(key, sizeof(key));
  if (!ok) return false;
  if (create) {
    portENTER_CRITICAL(&nonceLock);
    memcpy(nonce, nextNonce, nonceSize);
    for (size_t i = 0; i < nonceSize; ++i) {
      if (++nextNonce[i]) break;
    }
    portEXIT_CRITICAL(&nonceLock);
  }
  if (!cipher.setIV(nonce, nonceSize)) return false;
  cipher.addAuthData(purpose, strlen(purpose) + 1);
  return true;
}
const char* aclPurpose(const char* path) {
  return strncmp(path, "/s_login_replay", 15) == 0 ? "login-replay-v1" : "client-acl-v1";
}
}

void seed() {
  esp_fill_random(firstKey, sizeof(firstKey));
  esp_fill_random(nextNonce, sizeof(nextNonce));
  seeded = true;
}

// The partition migration bridge may have finished restoring a legacy image
// immediately before losing power. Never retire an active recovery source.
bool retireMigrationCopies() {
  nvs_handle_t handle;
  esp_err_t rc = nvs_open("mesh-pt-migrate", NVS_READONLY, &handle);
  if (rc == ESP_ERR_NVS_NOT_FOUND) return true;
  if (rc != ESP_OK) return false;
  bool ok = true;
  for (const char* flag : {"id-pending", "cfg-pending"}) {
    uint8_t value = 0;
    rc = nvs_get_u8(handle, flag, &value);
    if (rc != ESP_ERR_NVS_NOT_FOUND && (rc != ESP_OK || value != 0)) ok = false;
  }
  nvs_close(handle);
  if (!ok || nvs_open("mesh-pt-migrate", NVS_READWRITE, &handle) != ESP_OK) return false;
  for (const char* key : {"identity", "cfg02", "cfg03", "cfg20", "cfg26"}) {
    rc = nvs_erase_key(handle, key);
    if (rc != ESP_OK && rc != ESP_ERR_NVS_NOT_FOUND) ok = false;
  }
  ok = ok && nvs_commit(handle) == ESP_OK;
  nvs_close(handle);
  return ok;
}

bool isSealedIdentity(const uint8_t* record) {
  return memcmp(record, identityMagic, sizeof(identityMagic)) == 0;
}
bool sealIdentity(const char* name, const uint8_t* privateKey, uint8_t* record) {
  ChaChaPoly cipher;
  memcpy(record, identityMagic, sizeof(identityMagic));
  if (!start(cipher, record + sizeof(identityMagic), "identity-v1", true)) return false;
  cipher.addAuthData(name, strlen(name) + 1);
  cipher.encrypt(record + identityPayload, privateKey, identitySecretSize);
  cipher.computeTag(record + identityTag, tagSize);
  return true;
}
bool openIdentity(const char* name, const uint8_t* record, uint8_t* privateKey) {
  if (!isSealedIdentity(record)) return false;
  uint8_t nonce[nonceSize];
  memcpy(nonce, record + sizeof(identityMagic), sizeof(nonce));
  ChaChaPoly cipher;
  if (!start(cipher, nonce, "identity-v1", false)) return false;
  cipher.addAuthData(name, strlen(name) + 1);
  cipher.decrypt(privateKey, record + identityPayload, identitySecretSize);
  if (cipher.checkTag(record + identityTag, tagSize)) return true;
  mbedtls_platform_zeroize(privateKey, identitySecretSize);
  return false;
}

AclFile AclFile::open(File raw, const char* path, bool write, bool* invalid) {
  AclFile result(raw);
  if (invalid) *invalid = false;
  File& handle = result.file;
  if (!handle) return result;
  uint8_t header[sizeof(fileMagic) + nonceSize];
  if (!write) {
    const size_t prefix = std::min(handle.size(), sizeof(fileMagic));
    if (handle.read(header, prefix) != prefix || !handle.seek(0)) return File();
    if (prefix != sizeof(fileMagic) || memcmp(header, fileMagic, sizeof(fileMagic)) != 0)
      return result; // Legacy reader. Migration validates it before publication.
    if (handle.size() < sizeof(header) + tagSize) {
      if (invalid) *invalid = true;
      return File();
    }
    if (handle.read(header, sizeof(header)) != sizeof(header)) return File();
  }
  result.cipher.reset(new(std::nothrow) ChaChaPoly);
  if (!result.cipher) return File();
  const char* purpose = aclPurpose(path);
  uint8_t* nonce = header + sizeof(fileMagic);
  if (!start(*result.cipher, nonce, purpose, write)) return File();
  if (write) {
    memcpy(header, fileMagic, sizeof(fileMagic));
    if (handle.write(header, sizeof(header)) != sizeof(header)) return File();
    result.writing = true;
    return result;
  }
  result.plainSize = handle.size() - sizeof(header) - tagSize;
  result.remaining = result.plainSize;
  uint8_t block[128], tag[tagSize];
  for (size_t left = result.plainSize; left;) {
    const size_t n = std::min(left, sizeof(block));
    if (handle.read(block, n) != n) return File();
    result.cipher->decrypt(block, block, n);
    left -= n;
  }
  mbedtls_platform_zeroize(block, sizeof(block));
  if (handle.read(tag, sizeof(tag)) != sizeof(tag)) return File();
  if (!result.cipher->checkTag(tag, sizeof(tag))) {
    if (invalid) *invalid = true;
    return File();
  }
  if (!handle.seek(sizeof(header)) || !start(*result.cipher, nonce, purpose, false)) return File();
  return result;
}

size_t AclFile::read(uint8_t* data, size_t length) {
  if (!cipher) return file.read(data, length);
  const size_t n = file.read(data, std::min(length, remaining));
  if (n > remaining || n > length) return 0;
  cipher->decrypt(data, data, n);
  remaining -= n;
  return n;
}
size_t AclFile::write(const uint8_t* data, size_t length) {
  if (!cipher) return file.write(data, length);
  if (!writing) return 0;
  uint8_t block[128];
  size_t offset = 0;
  while (offset < length) {
    const size_t n = std::min(length - offset, sizeof(block));
    cipher->encrypt(block, data + offset, n);
    if (file.write(block, n) != n) { file.close(); break; }
    offset += n;
  }
  return offset;
}
void AclFile::close() {
  if (writing && file) {
    uint8_t tag[tagSize];
    cipher->computeTag(tag, sizeof(tag));
    file.write(tag, sizeof(tag)); // Transaction verifies the tag on reopening.
  }
  file.close();
  writing = false;
}
}
}
#endif
