#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <string>
#include <helpers/PrefsSaveRouting.h>
#include <helpers/PrefsSaveReplyGuard.h>

#define MIN_LOCAL_ADVERT_INTERVAL 60
#define PUB_KEY_SIZE 32
#define PRV_KEY_SIZE 64
namespace mesh {
struct LocalIdentity {
  uint8_t pub_key[PUB_KEY_SIZE]{};
  static bool validatePrivateKey(const uint8_t*) { return true; }
  void readFrom(const uint8_t* key, size_t) { memcpy(pub_key, key, sizeof(pub_key)); }
};
struct Utils {
  // Valid synthetic keys only; cryptography and real file transactions have
  // their own production identity recovery tests.
  static bool fromHex(uint8_t* out, size_t n, const char*) { memset(out, 0x11, n); return true; }
  static void toHex(char* out, const uint8_t*, size_t n) { memset(out, '1', 2*n); out[2*n] = 0; }
};
}
struct Prefs {
  uint8_t advert_interval = 30, flood_advert_interval = 12, multi_acks = 0;
  char password[16] = "old-admin", guest_password[16] = "old-guest";
  bool dirty = true;
  void clearDirty() { dirty = false; }
  void markUnsaved() { dirty = true; }
};
struct StrHelper {
  static void strncpy(char* out, const char* in, size_t n) {
    std::strncpy(out, in, n-1); out[n-1] = 0;
  }
};
struct CommonCLI;
struct Callbacks {
  CommonCLI* cli;
  Prefs persisted;
  bool common_ok = true, observer_ok = true, known = true, identity_ok = false;
  unsigned saves = 0, identity_saves = 0, local_updates = 0, flood_updates = 0;
  void savePrefs(PrefsSaveRouting::Scope);
  void updateAdvertTimer();
  void updateFloodAdvertTimer();
  bool saveIdentity(const mesh::LocalIdentity&) { ++identity_saves; return identity_ok; }
};
struct CommonCLI {
  Prefs prefs;
  Prefs* _prefs = &prefs;
  Callbacks callbacks{this, prefs};
  Callbacks* _callbacks = &callbacks;
  bool _common_save_result_known = false, _common_save_succeeded = false;
  bool _observer_save_result_known = false, _observer_save_succeeded = false;
  uint32_t _prefs_save_failures = 0;
  void savePrefs(PrefsSaveRouting::Scope scope = PrefsSaveRouting::Scope::Common);
  bool trySavePrefs(PrefsSaveRouting::Scope scope = PrefsSaveRouting::Scope::Common);
  bool saveObserverPrefs();
  void set(const char* config, char* reply);
  void password(const char* command, char* reply);
};
void Callbacks::savePrefs(PrefsSaveRouting::Scope scope) {
  ++saves;
  if (!known) return;
  const auto plan = PrefsSaveRouting::planFor(scope);
  if (plan.common) {
    cli->_common_save_result_known = true;
    cli->_common_save_succeeded = common_ok;
    if (common_ok) persisted = cli->prefs;
  }
#ifdef WITH_MQTT_BRIDGE
  if (plan.observer) {
    cli->_observer_save_result_known = true;
    cli->_observer_save_succeeded = observer_ok;
  }
#endif
}
void Callbacks::updateAdvertTimer() {
  assert(persisted.advert_interval == cli->prefs.advert_interval);
  ++local_updates;
}
void Callbacks::updateFloodAdvertTimer() {
  assert(persisted.flood_advert_interval == cli->prefs.flood_advert_interval);
  ++flood_updates;
}
@PARSERS@
@SAVE_METHODS@
void CommonCLI::set(const char* input, char* reply) {
  // Match the firmware's fixed-capacity CLI input buffer.
  char command[160]{};
  snprintf(command, sizeof(command), "%s", input);
  const char* config = command;
  @REPLY_GUARD@
  @SET_BRANCHES@
}
void CommonCLI::password(const char* command, char* reply) {
  @REPLY_GUARD@
  @PASSWORD@
}
bool error(const char* reply) { return strncmp(reply, "Error:", 6) == 0; }
int main() {
  char reply[160]{};
  for (bool guest : {false, true}) {
    CommonCLI cli;
    cli.prefs.advert_interval = 2;
    cli.callbacks.common_ok = false;
    if (guest) cli.set("guest.password new-guest", reply);
    else cli.password("password new-admin", reply);
    assert(error(reply) && strstr(reply, "unchanged"));
    assert(strcmp(cli.prefs.password, "old-admin") == 0);
    assert(strcmp(cli.prefs.guest_password, "old-guest") == 0);
    assert(cli.prefs.advert_interval == 2 && cli.callbacks.local_updates == 0);
    assert(cli.prefs.dirty && cli.callbacks.saves == 1);
    cli.callbacks.common_ok = true;
    if (guest) cli.set("guest.password new-guest", reply);
    else cli.password("password new-admin", reply);
    assert(!error(reply));
    assert(strcmp(guest ? cli.prefs.guest_password : cli.prefs.password,
                  guest ? "new-guest" : "new-admin") == 0);
    assert(cli.prefs.advert_interval == 0 && cli.callbacks.local_updates == 1);
    assert(!cli.prefs.dirty && cli.callbacks.saves == 2);
    assert(strcmp(cli.prefs.password, cli.callbacks.persisted.password) == 0);
    assert(strcmp(cli.prefs.guest_password, cli.callbacks.persisted.guest_password) == 0);
  }
  for (bool flood : {false, true}) {
    const char* key = flood ? "flood.advert.interval " : "advert.interval ";
    CommonCLI cli;
    for (const char* invalid : {"", " ", "nonsense", "-1", "+3", "1x", "3 4",
                                "4294967296", "999999999999999999999999999999", "1", "2"}) {
      cli.set((std::string(key) + invalid).c_str(), reply);
      assert(error(reply));
      assert(cli.prefs.advert_interval == 30 && cli.prefs.flood_advert_interval == 12);
      assert(cli.callbacks.saves == 0 && cli.callbacks.local_updates == 0
             && cli.callbacks.flood_updates == 0);
    }
    cli.set((std::string(key) + (flood ? "169" : "241")).c_str(), reply);
    assert(error(reply) && cli.callbacks.saves == 0);
    cli.callbacks.common_ok = false;
    cli.set((std::string(key) + (flood ? "3" : "120")).c_str(), reply);
    assert(error(reply) && strstr(reply, "unchanged"));
    assert(cli.prefs.advert_interval == 30 && cli.prefs.flood_advert_interval == 12);
    assert(cli.callbacks.local_updates == 0 && cli.callbacks.flood_updates == 0);
    cli.callbacks.common_ok = true;
    for (const char* valid : {"0", flood ? "3" : "60", flood ? "168" : "240"}) {
      cli.set((std::string(key) + valid).c_str(), reply);
      assert(strcmp(reply, "OK") == 0);
      assert(cli.prefs.advert_interval == cli.callbacks.persisted.advert_interval);
      assert(cli.prefs.flood_advert_interval == cli.callbacks.persisted.flood_advert_interval);
    }
    assert(flood ? cli.callbacks.flood_updates == 3 : cli.callbacks.local_updates == 3);
  }
  {
    CommonCLI cli;
    const std::string key = "prv.key " + std::string(128, '1');
    cli.set(key.c_str(), reply);
    assert(error(reply) && cli.callbacks.identity_saves == 1);
    cli.callbacks.identity_ok = true;
    cli.set(key.c_str(), reply);
    assert(strncmp(reply, "OK, reboot", 10) == 0 && cli.callbacks.identity_saves == 2);
  }
  {
    CommonCLI cli;
    cli.prefs.dirty = false;
    cli.callbacks.common_ok = false;
    cli.set("multi.acks 2", reply);
    assert(error(reply) && strstr(reply, "unsaved changes"));
    assert(cli.prefs.multi_acks == 2 && cli.callbacks.persisted.multi_acks == 0);
    assert(cli.prefs.dirty && cli._prefs_save_failures == 1);
    cli.callbacks.common_ok = true;
    cli.set("multi.acks 1", reply);
    assert(strcmp(reply, "OK") == 0 && !cli.prefs.dirty);
    // A callback skipping a save must not inherit the previous success latch.
    cli.callbacks.known = false;
    cli.set("multi.acks 2", reply);
    assert(error(reply) && cli.prefs.dirty && cli._prefs_save_failures == 2);
  }
  {
    CommonCLI cli;
    cli.prefs.advert_interval = 2;
    assert(cli.trySavePrefs(PrefsSaveRouting::Scope::Observer));
    assert(cli.prefs.dirty && cli.prefs.advert_interval == 2 && cli.callbacks.local_updates == 0);
#ifdef WITH_MQTT_BRIDGE
    cli.callbacks.observer_ok = false;
    {
      PrefsSaveReplyGuard guard(cli._prefs_save_failures, reply);
      assert(!cli.saveObserverPrefs());
      strcpy(reply, "OK");
    }
    assert(error(reply));
    cli.callbacks.observer_ok = true;
    {
      PrefsSaveReplyGuard guard(cli._prefs_save_failures, reply);
      assert(cli.saveObserverPrefs());
      strcpy(reply, "OK");
    }
    assert(strcmp(reply, "OK") == 0);
    cli.callbacks.observer_ok = false;
    assert(!cli.trySavePrefs(PrefsSaveRouting::Scope::Both));
    assert(!cli.prefs.dirty && cli.prefs.advert_interval == 0); // common DID commit
    cli.callbacks.observer_ok = true;
    cli.callbacks.common_ok = false;
    cli.prefs.advert_interval = 2; cli.prefs.dirty = true;
    assert(!cli.trySavePrefs(PrefsSaveRouting::Scope::Both));
    assert(cli.prefs.dirty && cli.prefs.advert_interval == 2); // common did NOT commit
#endif
  }
  {
    uint32_t failures = UINT32_MAX;
    {
      PrefsSaveReplyGuard outer(failures, reply);
      { PrefsSaveReplyGuard inner(failures, reply); ++failures; strcpy(reply, "OK"); }
      assert(error(reply));
      strcpy(reply, "OK");
    }
    assert(error(reply));
    { PrefsSaveReplyGuard next(failures, reply); strcpy(reply, "OK"); }
    assert(strcmp(reply, "OK") == 0);
  }
  puts("Common CLI persistence and interval regressions passed");
}
