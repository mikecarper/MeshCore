// Production generation gates and post-command commit handling, fake CLI only.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#define MESH_MANAGEMENT_PASSWORD_HOST_TEST
#include <helpers/esp32/ManagementPassword.h>
#include <helpers/WebConfigBatch.h>
namespace password = mesh::esp32::managementPassword;
using WCLock = std::lock_guard<std::mutex>;
uint32_t millis() { return 1000; }
struct Callbacks { unsigned starts = 0, ends = 0;
  void onConfigBatchStart() { ++starts; }
  void onConfigBatchEnd() { ++ends; }
};
struct WebConfigServer {
  struct BatchEntry { char reply[160] = {}; } _batch[3];
  enum State { BATCH_IDLE, BATCH_PENDING, BATCH_DONE } _batch_state = BATCH_PENDING;
  unsigned _batch_next = 0, _batch_count = 3, executed = 0, reloads = 0;
  bool _batch_started = false, _batch_all_ok = true, _standalone_wifi_dirty = false;
  bool _batch_reauth_required = false, _batch_reauth_delivered = false, post_completed = false;
  uint32_t _batch_auth_generation = 0, _batch_reauth_until = 0;
  uint32_t _session_generation = 0, _session_last_seen = 5;
  char _batch_owner_token[33] = {}, _session_token[33] = "0123456789abcdef0123456789abcdef";
  std::mutex _mux;
  Callbacks callbacks; Callbacks* _cb = &callbacks;
  WebConfigServer() { password::Snapshot value; assert(password::snapshot(value));
    _session_generation = value.generation; beginBatchAuthorizationLocked(value.generation); }
  void reloadStandaloneWiFi() { ++reloads; }
  void clearSessionLocked();
  void beginBatchAuthorizationLocked(uint32_t);
  void cancelStaleBatchLocked(uint32_t);
  void startBatch(uint32_t);
  void beforeCommand(uint32_t);
  void afterCommand(bool, const char*, const char*, uint32_t);
};
@METHODS@
void reset() {
  auto& state = password::detail::state();
  { password::detail::Lock lock(state); state.current = {}; state.changing = state.uploading = state.rotating = false; }
  assert(password::initialize("original"));
}
void commit(const char* secret) {
  password::ChangeGuard change; assert(change.acquired()); assert(change.publish(secret));
}
int main() {
  reset(); WebConfigServer saved; saved.startBatch(100); assert(saved.callbacks.starts == 1);
  saved.beforeCommand(100); assert(saved.executed == 1);
  commit("next-password"); saved._batch_next = 1;
  saved.afterCommand(true, "next-password", "OK", 100);
  assert(saved.post_completed && saved._batch_reauth_required && saved._batch_reauth_until == 121000);
  assert(!saved._session_token[0] && saved._session_generation == 0 && saved._session_last_seen == 0);
  assert(saved._batch_owner_token[0]); // Only the scoped result receipt survives.
  saved.beforeCommand(200); assert(saved.executed == 2); // Own batch may continue.
  // Failed persistence changes no committed generation or general login.
  reset(); WebConfigServer failed; failed.startBatch(100);
  failed.afterCommand(true, "next-password", "Error: save unconfirmed", 100);
  assert(failed.post_completed && !failed._batch_reauth_required && failed._session_token[0]);
  // Saving the same password is a successful no-op, not a session rotation.
  commit("original"); failed.afterCommand(true, "original", "OK", 100);
  assert(!failed._batch_reauth_required && failed._session_token[0]);
  // A different transport's change before any command prevents callback entry.
  reset(); WebConfigServer stale; commit("external"); stale.startBatch(100);
  assert(stale.callbacks.starts == 0 && stale._batch_state == WebConfigServer::BATCH_DONE);
  assert(stale._batch_next == 3 && !stale._batch_all_ok && !stale._batch_owner_token[0]);
  for (const auto& entry : stale._batch) assert(strstr(entry.reply, "sign in again"));
  // A change between two commands balances the transaction and reloads staged WiFi.
  reset(); WebConfigServer between; between.startBatch(100); between._batch_next = 1;
  between._standalone_wifi_dirty = true; commit("external"); between.beforeCommand(200);
  assert(between.executed == 0 && between.callbacks.ends == 1 && between.reloads == 1);
  assert(!between._batch_started && !between._batch_reauth_required && !between._batch_all_ok);
  // A mismatched successful-looking reply cannot mint a receipt for another save.
  reset(); WebConfigServer mismatch; mismatch.startBatch(100); commit("external");
  mismatch.afterCommand(true, "requested", "OK", 100);
  assert(!mismatch.post_completed && !mismatch._batch_reauth_required && !mismatch._batch_owner_token[0]);
  assert(mismatch.callbacks.ends == 1 && mismatch._batch_state == WebConfigServer::BATCH_DONE);
  puts("WebConfig committed password/batch generation checks passed");
}
