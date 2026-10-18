// Only WiFi endpoints and clock are doubles; the service method is production.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#define MESH_MANAGEMENT_PASSWORD_HOST_TEST
#include <helpers/esp32/ManagementPassword.h>
#include <helpers/WebConfigBatch.h>
namespace password = mesh::esp32::managementPassword;
using WCLock = std::lock_guard<std::mutex>;
struct WiFiFake {
  bool ap_live = true, sta_live = true;
  unsigned disconnects = 0;
  bool softAPdisconnect(bool off) {
    assert(off); ++disconnects; ap_live = false; return true;
  }
} WiFi;
namespace mesh {
struct Debug { void println(const char*) {} };
Debug& usbDebugPort() { static Debug port; return port; }
namespace wifi {
bool ready = true;
unsigned starts = 0;
std::string last_password;
bool startAccessPoint(const char* ssid, bool reuse, const char* secret) {
  assert(strcmp(ssid, "MC-1234") == 0 && reuse);
  ++starts; last_password = secret ? secret : "";
  // Independent SDK model: mode(STA) may fail before touching the existing AP.
  if (!ready) return false;
  WiFi.ap_live = true; return true;
}
}}
struct WebConfigServer {
  enum Mode { MODE_OFF, MODE_LAN, MODE_SETUP } _mode = MODE_SETUP;
  enum BatchState { BATCH_IDLE, BATCH_PENDING, BATCH_DONE } _batch_state = BATCH_IDLE;
  bool _stopping = false, _setup_provisioning_privilege = true;
  bool _batch_reauth_required = false, _batch_reauth_delivered = false;
  uint32_t _ap_auth_generation = 0, _ap_rekey_generation = 0;
  uint32_t _ap_rekey_after = 0, _ap_rekey_deadline = 0, _reboot_at = 0;
  char _ap_ssid[33] = "MC-1234";
  std::mutex _mux;
  unsigned refs = 0;
  unsigned handlerRefCount() const { return refs; }
  WebConfigServer() { password::Snapshot value; assert(password::snapshot(value)); _ap_auth_generation = value.generation; }
  void serviceSetupPassword(uint32_t now);
};
@METHODS@
void reset() {
  auto& state = password::detail::state();
  { password::detail::Lock lock(state); state.current = {}; state.changing = state.uploading = state.rotating = false; }
  assert(password::initialize("original"));
  WiFi = {}; mesh::wifi::ready = true; mesh::wifi::starts = 0; mesh::wifi::last_password.clear();
}
void commit(const char* secret) {
  password::ChangeGuard change; assert(change.acquired()); assert(change.publish(secret));
}
int main() {
  reset(); WebConfigServer portal;
  portal.serviceSetupPassword(100); assert(mesh::wifi::starts == 0);
  commit("next-password");
  portal._batch_state = WebConfigServer::BATCH_PENDING;
  portal.serviceSetupPassword(100); portal.serviceSetupPassword(4000);
  assert(mesh::wifi::starts == 0);
  portal._batch_state = WebConfigServer::BATCH_DONE; portal.refs = 1;
  portal.serviceSetupPassword(4000); assert(mesh::wifi::starts == 0);
  portal.refs = 0; portal._batch_reauth_required = true;
  portal.serviceSetupPassword(4000); assert(mesh::wifi::starts == 0);
  // Delivery gets a bounded grace period, then the live key follows the saved one.
  portal._batch_reauth_delivered = true; portal._ap_rekey_after = 7000;
  portal.serviceSetupPassword(6999); assert(mesh::wifi::starts == 0);
  password::Snapshot current; assert(password::snapshot(current));
  assert(password::beginUpload(current.generation));
  portal.serviceSetupPassword(7000); assert(mesh::wifi::starts == 0);
  password::endUpload(); portal.serviceSetupPassword(7000);
  assert(mesh::wifi::starts == 1 && mesh::wifi::last_password == "next-password");
  assert(portal._ap_auth_generation == current.generation && portal._ap_rekey_generation == 0);
  assert(WiFi.ap_live && WiFi.sta_live && WiFi.disconnects == 0);
  // A client that never receives its receipt cannot postpone rekey indefinitely.
  reset(); WebConfigServer no_receipt; commit("another-pass");
  no_receipt._batch_reauth_required = true;
  no_receipt.serviceSetupPassword(100); no_receipt.serviceSetupPassword(30099);
  assert(mesh::wifi::starts == 0);
  no_receipt.serviceSetupPassword(30100); assert(mesh::wifi::starts == 1);
  // Legacy short/non-ASCII admin credentials retain HTTP access on an open AP.
  for (const char* secret : {"short", "long-\xc3\xa9-pass"}) {
    reset(); WebConfigServer legacy; commit(secret);
    legacy.serviceSetupPassword(100); legacy.serviceSetupPassword(3100);
    assert(mesh::wifi::starts == 1 && mesh::wifi::last_password.empty());
    assert(!legacy._setup_provisioning_privilege && WiFi.ap_live && WiFi.sta_live);
  }
  // A failed restart must not leave an indefinitely usable old-key AP behind.
  reset(); WebConfigServer failed; const auto old_generation = failed._ap_auth_generation;
  commit("next-password"); mesh::wifi::ready = false;
  failed.serviceSetupPassword(100); failed.serviceSetupPassword(3100);
  assert(mesh::wifi::starts == 1 && !WiFi.ap_live && WiFi.sta_live && WiFi.disconnects == 1);
  assert(failed._ap_auth_generation == old_generation && failed._ap_rekey_generation != 0);
  failed.serviceSetupPassword(8099); assert(mesh::wifi::starts == 1);
  mesh::wifi::ready = true; failed.serviceSetupPassword(8100);
  assert(mesh::wifi::starts == 2 && WiFi.ap_live && WiFi.sta_live);
  assert(failed._ap_auth_generation != old_generation && failed._ap_rekey_generation == 0);
  puts("WebConfig password session/rekey checks passed");
}
