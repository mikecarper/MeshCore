#include "hal.h"
#include <helpers/IdentityStore.h>
#include <helpers/ClientACL.h>
#include <helpers/UsbDataLock.h>
#include <helpers/esp32/PrivateStorage.h>

static std::string command(const char* text) {
  char reply[160] = {};
  assert(mesh::handleUsbDataLockCommand(text, reply, sizeof(reply)));
  return reply;
}

int main() {
  mesh::privateStorage::seed();
  IdentityStore store(SPIFFS, "/identity");
  uint8_t secret[64], admin[32];
  for (size_t i = 0; i < sizeof(secret); ++i) secret[i] = 39 + i;
  secret[0] &= 248;
  secret[31] = (secret[31] & 63) | 64;
  for (size_t i = 0; i < sizeof(admin); ++i) admin[i] = 91 + i;
  mesh::LocalIdentity id, recovered;
  id.readFrom(secret, sizeof(secret));
  assert(store.save("_main", id, "ridge"));
  ClientACL acl;
  acl.load(&SPIFFS, id);
  assert(acl.applyPermissions(id, admin, sizeof(admin), PERM_ACL_ADMIN));
  assert(acl.save(&SPIFFS));
  assert(acl.authorizeLoginTimestamp(admin, 100, 0, PERM_ACL_ADMIN));
  const auto plain = SPIFFS.files;
  assert(nvs.count("mc-private") == 0);
  // Leftover bridge copies must be retired after a verified conversion.
  nvs["mesh-pt-migrate"]["identity"] = plain.at("/identity/_main.id");
  nvs["mesh-pt-migrate"]["cfg02"] = plain.at("/s_contacts");
  assert(command("get usb.lock").find("usb.lock off") == 0);

  // Fail between the identity and ACL writes, then reboot with no further command.
  SPIFFS.fail_open_write = "/s_contacts.tmp";
  assert(command("set usb.lock on").find("Error") == 0);
  assert(command("get usb.lock").find("incomplete") != std::string::npos);
  // A persistently full filesystem must still boot and retain working admins.
  mesh::privateStorage::seed();
  assert(store.load("_main", recovered) && recovered.matches(id));
  ClientACL duringFailure;
  duringFailure.load(&SPIFFS, recovered);
  assert(duringFailure.getClient(admin, sizeof(admin))->isAdmin());
  assert(command("get usb.lock").find("incomplete") != std::string::npos);
  SPIFFS.fail_open_write.clear();
  mesh::privateStorage::seed();
  char name[32] = {};
  assert(store.load("_main", recovered, name, sizeof(name)));
  assert(id.matches(recovered) && strcmp(name, "ridge") == 0);
  assert(command("get usb.lock").find("incomplete") == std::string::npos);
  assert(nvs["mesh-pt-migrate"].empty());
  for (const auto& f : SPIFFS.files) {
    assert(std::search(f.second.begin(), f.second.end(), secret, secret + 64) == f.second.end());
    assert(std::search(f.second.begin(), f.second.end(), admin, admin + 32) == f.second.end());
    assert(f.second != plain.at(f.first));
  }
  ClientACL restored;
  restored.load(&SPIFFS, recovered);
  assert(restored.getClient(admin, sizeof(admin))->isAdmin());
  assert(!restored.authorizeLoginTimestamp(admin, 100, 0, PERM_ACL_ADMIN));
  const auto encrypted = SPIFFS.files;
  assert(command("set usb.lock on").find("OK") == 0 && SPIFFS.files == encrypted);

  // Independent writes of the same private key must use different nonces.
  assert(store.save("_main", id, "ridge"));
  const char* path = "/identity/_main.id";
  assert(encrypted.at(path) != SPIFFS.files[path]);
  SPIFFS.files[path][20] ^= 1;
  assert(store.loadResult("_main", recovered) == IdentityLoadResult::Unreadable);
  SPIFFS.files[path] = encrypted.at(path);

  auto wrappingKey = nvs.at("mc-private").at("wrap-key");
  nvs["mc-private"].erase("wrap-key");
  assert(store.loadResult("_main", recovered) == IdentityLoadResult::Unreadable);
  assert(!nvs["mc-private"].count("wrap-key"));
  nvs["mc-private"]["wrap-key"] = wrappingKey;

  SPIFFS.files["/s_contacts"].back() ^= 1;
  ClientACL invalid;
  invalid.load(&SPIFFS, id);
  assert(invalid.getNumClients() == 0);
  SPIFFS.files["/s_contacts.bak"] = encrypted.at("/s_contacts");
  invalid.load(&SPIFFS, id);
  assert(invalid.getClient(admin, sizeof(admin))->isAdmin());

  SPIFFS.fail_open_write = "/s_login_replay.tmp";
  assert(command("set usb.lock off").find("Error") == 0);
  SPIFFS.fail_open_write.clear();
  mesh::privateStorage::seed();
  assert(store.load("_main", recovered));
  assert(SPIFFS.files == plain && id.matches(recovered));
  assert(command("set usb.lock off").find("OK") == 0);
  assert(command("get usb.lock").find("usb.lock off") == 0);

  // An earlier firmware saved 1 for USB locked while keeping ordinary files.
  nvs["mesh-usb"]["locked"] = {1};
  assert(store.load("_main", recovered) && recovered.matches(id));
  assert(SPIFFS.files[path] != plain.at(path));
  assert(SPIFFS.files["/s_contacts"] != plain.at("/s_contacts"));
  assert(command("get usb.lock").find("incomplete") == std::string::npos);
  uint8_t secondAdmin[32];
  for (size_t i = 0; i < sizeof(secondAdmin); ++i) secondAdmin[i] = 181 + i;
  ClientACL updated;
  updated.load(&SPIFFS, id);
  assert(updated.applyPermissions(id, secondAdmin, sizeof(secondAdmin), PERM_ACL_ADMIN));
  assert(updated.save(&SPIFFS));
  assert(updated.authorizeLoginTimestamp(secondAdmin, 500, 0, PERM_ACL_ADMIN));
  for (const auto& f : SPIFFS.files)
    assert(std::search(f.second.begin(), f.second.end(), secondAdmin, secondAdmin + 32) == f.second.end());
  assert(command("set usb.lock off").find("OK") == 0);
  ClientACL finalAcl;
  finalAcl.load(&SPIFFS, id);
  assert(finalAcl.getClient(secondAdmin, sizeof(secondAdmin))->isAdmin());
  assert(!finalAcl.authorizeLoginTimestamp(secondAdmin, 500, 0, PERM_ACL_ADMIN));
}
