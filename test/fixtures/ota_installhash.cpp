#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace mesh { struct Utils {
  static bool fromHex(uint8_t *out, size_t n, const char *text) {
    for (size_t i=0;i<n;i++) { unsigned value=0; if (sscanf(text+2*i,"%2x",&value)!=1) return false; out[i]=value; }
    return true;
  }
}; }
struct OtaManager { enum FetchState { IDLE, COMPLETE }; FetchState state=COMPLETE; FetchState fetchState() const { return state; } };
struct Store { unsigned size=123; unsigned staged_size() const { return size; } };
struct VerifyResult {
  bool integrity=true, container_hash_ok=true;
  uint8_t container_hash[32] = {};
  bool integrity_ok() const { return integrity; }
};
static VerifyResult verdict;
static unsigned verifies=0, applies=0;
static VerifyResult ota_verify(const Store&, int) { ++verifies; return verdict; }
struct Context {
  bool apply_pending=false, bootloader_apply_pending=false, fetch_to_folder=false;
  OtaManager manager; Store fetch_store; int allow=0;
  bool apply_fetched(char *reply) { ++applies; strcpy(reply,"armed"); return true; }
};
@PARSE_HEX@
@IS_CMD@
bool execute(const char *a, char *reply, Context &c) {
  const char *rest = nullptr;
  @INSTALL_HASH@
  else { strcpy(reply,"unknown command"); }
  return true;
}
int main() {
  const char *zero="0000000000000000000000000000000000000000000000000000000000000000";
  char cmd[128], reply[160]; Context c;
  auto run=[&](const char* input){ memset(reply,0,sizeof(reply)); execute(input,reply,c); };
  run("installhash"); assert(strstr(reply,"ERR usage:")); assert(applies==0 && verifies==0);
  run("installhash 00"); assert(strstr(reply,"ERR usage:")); assert(applies==0 && verifies==0);
  run("installhashXYZ"); assert(!strcmp(reply,"unknown command"));
  snprintf(cmd,sizeof(cmd),"installhash %s",zero);
  const char *ignored=nullptr;
  assert(!is_cmd(cmd,"install|apply|applydelta",&ignored)); // Old firmware must never install on this verb.
  c.apply_pending=true;run(cmd);assert(strstr(reply,"already armed"));assert(applies==0 && verifies==0);c.apply_pending=false;
  c.bootloader_apply_pending=true;run(cmd);assert(strstr(reply,"already armed"));assert(applies==0 && verifies==0);c.bootloader_apply_pending=false;
  c.fetch_to_folder=true;run(cmd);assert(strstr(reply,"no complete update"));assert(applies==0 && verifies==0);c.fetch_to_folder=false;
  c.manager.state=OtaManager::IDLE;run(cmd);assert(strstr(reply,"no complete update"));assert(applies==0 && verifies==0);c.manager.state=OtaManager::COMPLETE;
  c.fetch_store.size=0;run(cmd);assert(strstr(reply,"no complete update"));assert(applies==0 && verifies==0);c.fetch_store.size=123;
  verdict.integrity=false;run(cmd);assert(strstr(reply,"hash mismatch"));assert(applies==0);verdict.integrity=true;
  verdict.container_hash_ok=false;run(cmd);assert(strstr(reply,"hash mismatch"));assert(applies==0);verdict.container_hash_ok=true;
  verdict.container_hash[31]=1;run(cmd);assert(strstr(reply,"hash mismatch"));assert(applies==0);verdict.container_hash[31]=0;
  run(cmd);assert(!strcmp(reply,"OK | armed"));assert(applies==1);
  puts("PASS verified hash is required before one apply");
}
